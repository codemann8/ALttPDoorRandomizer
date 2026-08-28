"""
Read-only analytics queries for the TestSuiteAutomate explorer.

All setting column names interpolated into SQL are validated against
SETTINGS_VALUE_COLUMNS. Time bounds are unix seconds (inclusive).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from test.suite.automate.TestSuiteAutomateDB import (
    RUNS_TABLE,
    SETTINGS_COLUMN_SPECS,
    SETTINGS_TABLE,
    SETTINGS_VALUE_COLUMNS,
    table_columns,
)

UNSET = "__unset__"
ANY_FAIL = "__any_fail__"
UNCLASSIFIED = "(unclassified)"

IGNORED_SETTINGS_COLUMNS = {
    "experimental", "pseudoboots", "mirrorscroll", "standardized_palettes", "mixed_travel", "dungeon_counters", "hints",
}


# ---------------------------------------------------------------------------
# View-only hides (sidecar JSON next to the DB — not generation state)
# A hide means: exclude failures of that error_type with timestamp <= as_of
# from charts/KPIs unless show_hidden is true (so new post-fix regressions stay visible).
# ---------------------------------------------------------------------------

def hides_path(db_path: os.PathLike | str) -> Path:
    p = Path(db_path).resolve()
    return p.parent / f"{p.stem}_explore_hides.json"


def load_hides(db_path: os.PathLike | str) -> List[Dict[str, Any]]:
    path = hides_path(db_path)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("hides") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        et = (it.get("error_type") or "").strip()
        if not et:
            continue
        try:
            as_of = int(it.get("hidden_as_of"))
        except (TypeError, ValueError):
            continue
        out.append({
            "error_type": et,
            "hidden_as_of": as_of,
            "note": (it.get("note") or "") or "",
            "updated_at": int(it.get("updated_at") or as_of),
        })
    out.sort(key=lambda h: h["error_type"].lower())
    return out


def load_hide_map(db_path: os.PathLike | str) -> Dict[str, int]:
    """error_type -> hidden_as_of unix seconds."""
    return {h["error_type"]: int(h["hidden_as_of"]) for h in load_hides(db_path)}


def save_hides(db_path: os.PathLike | str, hides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    path = hides_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # de-dupe by error_type (last wins)
    by_et: Dict[str, Dict[str, Any]] = {}
    for h in hides:
        et = (h.get("error_type") or "").strip()
        if not et:
            continue
        by_et[et] = {
            "error_type": et,
            "hidden_as_of": int(h["hidden_as_of"]),
            "note": (h.get("note") or "") or "",
            "updated_at": int(h.get("updated_at") or time.time()),
        }
    ordered = sorted(by_et.values(), key=lambda x: x["error_type"].lower())
    path.write_text(
        json.dumps({"hides": ordered}, indent=2) + "\n",
        encoding="utf-8",
    )
    return ordered


def set_hide(
    db_path: os.PathLike | str,
    error_type: str,
    hidden_as_of: Optional[int] = None,
    note: str = "",
) -> List[Dict[str, Any]]:
    et = (error_type or "").strip()
    if not et:
        raise ValueError("error_type is required")
    if et == ANY_FAIL:
        raise ValueError("Cannot hide the synthetic 'Any failure' bucket")
    as_of = int(hidden_as_of) if hidden_as_of is not None else int(time.time())
    hides = [h for h in load_hides(db_path) if h["error_type"] != et]
    hides.append({
        "error_type": et,
        "hidden_as_of": as_of,
        "note": note or "",
        "updated_at": int(time.time()),
    })
    return save_hides(db_path, hides)


def clear_hide(db_path: os.PathLike | str, error_type: str) -> List[Dict[str, Any]]:
    et = (error_type or "").strip()
    if not et:
        raise ValueError("error_type is required")
    hides = [h for h in load_hides(db_path) if h["error_type"] != et]
    return save_hides(db_path, hides)


def hide_exclude_clause(
    hide_map: Optional[Dict[str, int]],
    show_hidden: bool = False,
    alias: str = "r",
) -> Tuple[str, List[Any]]:
    """
    SQL that is true for rows that should remain visible.
    Hides only apply to failure rows of a given error_type at/before as_of.
    """
    if show_hidden or not hide_map:
        return "1=1", []
    parts: List[str] = []
    params: List[Any] = []
    for et, as_of in hide_map.items():
        as_of = int(as_of)
        if et == UNCLASSIFIED:
            parts.append(
                f"({alias}.success = 0 "
                f"AND ({alias}.error_type IS NULL OR TRIM({alias}.error_type) = '') "
                f"AND {alias}.timestamp <= ?)"
            )
            params.append(as_of)
        else:
            parts.append(
                f"({alias}.success = 0 "
                f"AND {alias}.error_type = ? "
                f"AND {alias}.timestamp <= ?)"
            )
            params.extend([et, as_of])
    if not parts:
        return "1=1", []
    return f"NOT ({' OR '.join(parts)})", params


def has_suite_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(f"PRAGMA table_info({RUNS_TABLE})").fetchall()
    names = [r["name"] if isinstance(r, sqlite3.Row) else r[1] for r in rows]
    return "suite" in names


def suite_clause(
    suite: Optional[str],
    alias: str = "r",
    *,
    has_col: bool = True,
) -> Tuple[str, List[Any]]:
    if not suite or not has_col:
        return "1=1", []
    # NULL / blank treated as mystery (pre-column historical rows)
    return (
        f"COALESCE(NULLIF(TRIM({alias}.suite), ''), 'mystery') = ?",
        [suite],
    )


def list_suites(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not has_suite_column(conn):
        n = conn.execute(f"SELECT COUNT(*) FROM {RUNS_TABLE}").fetchone()[0]
        return [{"suite": "mystery", "n": int(n or 0)}] if n else []
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(suite), ''), 'mystery') AS suite, COUNT(*) AS n
        FROM {RUNS_TABLE}
        GROUP BY 1
        ORDER BY n DESC, suite ASC
        """
    ).fetchall()
    return [{"suite": row["suite"], "n": row["n"]} for row in rows]


def _combine_where(
    time_where: str,
    time_params: Sequence[Any],
    hide_map: Optional[Dict[str, int]],
    show_hidden: bool,
    suite: Optional[str] = None,
    alias: str = "r",
    *,
    has_suite_col: bool = True,
) -> Tuple[str, List[Any]]:
    hsql, hparams = hide_exclude_clause(hide_map, show_hidden, alias)
    ssql, sparams = suite_clause(suite, alias, has_col=has_suite_col)
    return (
        f"({time_where}) AND ({hsql}) AND ({ssql})",
        list(time_params) + list(hparams) + list(sparams),
    )

BOOL_COLUMNS = {
    name for name, _t, _a, is_bool, _g in SETTINGS_COLUMN_SPECS if is_bool
}

# Dropdown grouping — mirrors the comment blocks in SETTINGS_COLUMN_SPECS.
SETTING_GROUPS: List[Tuple[str, List[str]]] = [
    (
        "Main / item",
        [
            "algorithm", "accessibility", "logic", "mode", "goal", "swords",
        ],
    ),
    (
        "Pool expansion",
        [
            "shopsanity", "dropshuffle", "pottery", "bonk_drops",
        ],
    ),
    (
        "Entrance",
        [
            "shuffle", "shufflelinks", "shuffletavern", "openpyramid",
            "skullwoods", "linked_drops", "overworld_map",
        ],
    ),
    (
        "Dungeon / doors",
        [
            "door_shuffle", "intensity", "door_type_mode", "trap_door_mode",
            "key_logic_algorithm", "decoupledoors", "door_self_loops",
        ],
    ),
    (
        "Keys / dungeon items",
        [
            "mapshuffle", "compassshuffle", "keyshuffle", "bigkeyshuffle",
            "prizeshuffle",
        ],
    ),
    (
        "Overworld",
        [
            "ow_layout", "ow_parallel", "ow_terrain", "ow_whirlpool",
            "ow_crossed", "ow_keepsimilar", "ow_mixed", "ow_fluteshuffle",
            "ow_fog", "shuffle_followers",
        ],
    ),
    (
        "Enemies / bosses",
        [
            "enemy_shuffle", "enemy_health", "enemy_damage", "boss_shuffle",
        ],
    ),
    (
        "Other",
        [
            "crystals_gt", "crystals_ganon",
            "bow_mode", "flute_mode", "take_any", "potshuffle",
            "restrict_boss_items", "difficulty", "item_functionality",
        ],
    ),
]


def connect_ro(db_path: os.PathLike | str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"DB not found: {path}")
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


def _ident(name: str) -> str:
    if name not in SETTINGS_VALUE_COLUMNS:
        raise ValueError(f"Unknown setting column: {name}")
    return name


def time_clause(
    start: Optional[int],
    end: Optional[int],
    alias: str = "r",
) -> Tuple[str, List[Any]]:
    parts: List[str] = []
    params: List[Any] = []
    if start is not None:
        parts.append(f"{alias}.timestamp >= ?")
        params.append(int(start))
    if end is not None:
        parts.append(f"{alias}.timestamp <= ?")
        params.append(int(end))
    return (" AND ".join(parts) if parts else "1=1"), params


def display_value(column: str, raw: Any) -> str:
    if raw is None:
        return "(unset)"
    if column in BOOL_COLUMNS:
        if raw in (1, "1", True):
            return "Yes"
        if raw in (0, "0", False):
            return "No"
    return str(raw)


def _norm_error_sql(alias: str = "r") -> str:
    return (
        f"CASE WHEN {alias}.success = 1 THEN NULL "
        f"WHEN {alias}.error_type IS NULL OR TRIM({alias}.error_type) = '' "
        f"THEN '{UNCLASSIFIED}' "
        f"ELSE {alias}.error_type END"
    )


def _norm_stage_sql(alias: str = "r") -> str:
    return (
        f"CASE WHEN {alias}.success = 1 THEN NULL "
        f"WHEN {alias}.stage IS NULL OR TRIM({alias}.stage) = '' "
        f"THEN '{UNCLASSIFIED}' "
        f"ELSE {alias}.stage END"
    )


def _percentile(sorted_vals: Sequence[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = k - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def _chi2_p(a: int, b: int, c: int, d: int) -> Optional[float]:
    """Two-sided p-value for a 2x2 table (Yates-corrected chi-square, 1 df)."""
    n = a + b + c + d
    if n == 0:
        return None
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    if min(row1, row2, col1, col2) == 0:
        return None
    ad_bc = abs(a * d - b * c)
    # Yates
    chi2 = n * max(ad_bc - n / 2.0, 0.0) ** 2 / (row1 * row2 * col1 * col2)
    if chi2 <= 0:
        return 1.0
    return math.erfc(math.sqrt(chi2 / 2.0))


def meta(
    conn: sqlite3.Connection,
    start: Optional[int],
    end: Optional[int],
    *,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    hides: Optional[List[Dict[str, Any]]] = None,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    totals = conn.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            SUM(success) AS ok,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail,
            MIN(timestamp) AS tmin,
            MAX(timestamp) AS tmax
        FROM {RUNS_TABLE} r
        WHERE {where}
        """,
        params,
    ).fetchone()

    versions = [
        {"app_version": row["app_version"], "n": row["n"]}
        for row in conn.execute(
            f"""
            SELECT app_version, COUNT(*) AS n
            FROM {RUNS_TABLE} r
            WHERE {where}
            GROUP BY app_version
            ORDER BY n DESC
            """,
            params,
        )
    ]

    error_sql = _norm_error_sql()
    errors = [
        {"error_type": row["error_type"], "n": row["n"]}
        for row in conn.execute(
            f"""
            SELECT {error_sql} AS error_type, COUNT(*) AS n
            FROM {RUNS_TABLE} r
            WHERE {where} AND r.success = 0
            GROUP BY 1
            ORDER BY n DESC
            """,
            params,
        )
    ]

    stage_sql = _norm_stage_sql()
    stages = [
        {"stage": row["stage"], "n": row["n"]}
        for row in conn.execute(
            f"""
            SELECT {stage_sql} AS stage, COUNT(*) AS n
            FROM {RUNS_TABLE} r
            WHERE {where} AND r.success = 0
            GROUP BY 1
            ORDER BY n DESC
            """,
            params,
        )
    ]

    grouped = _setting_groups_meta(conn, where, params)

    n = int(totals["n"] or 0)
    ok = int(totals["ok"] or 0)
    fail = int(totals["fail"] or 0)
    return {
        "n": n,
        "ok": ok,
        "fail": fail,
        "success_rate": (ok / n) if n else None,
        "tmin": totals["tmin"],
        "tmax": totals["tmax"],
        "app_versions": versions,
        "error_types": errors,
        "stages": stages,
        "setting_groups": grouped,
        "bool_columns": sorted(BOOL_COLUMNS),
        "hides": hides if hides is not None else [],
        "show_hidden": bool(show_hidden),
        "suite": suite,
        "suites": list_suites(conn),
    }


def _present_setting_columns(conn: sqlite3.Connection) -> List[str]:
    existing = set(table_columns(conn, SETTINGS_TABLE))
    return [c for c in SETTINGS_VALUE_COLUMNS if c in existing]


def _setting_meta_from_tally(col: str, tally: Dict[Any, int]) -> Dict[str, Any]:
    values = [
        {
            "value": UNSET if raw is None else raw,
            "label": display_value(col, raw),
            "n": n,
        }
        for raw, n in sorted(tally.items(), key=lambda kv: (-kv[1], str(kv[0])))
    ]
    distinct = [v for v in values if v["value"] != UNSET]
    return {
        "name": col,
        "is_bool": col in BOOL_COLUMNS,
        "n_values": len(distinct),
        "constant": len(distinct) <= 1,
        "values": values,
    }


def _setting_groups_meta(
    conn: sqlite3.Connection,
    where: str,
    params: Sequence[Any],
) -> List[Dict[str, Any]]:
    """
    One JOIN scan over matching runs, then tally every settings column in Python.

    Beats ~60 separate GROUP BY queries for explorer meta dropdowns.
    """
    cols = _present_setting_columns(conn)
    tallies: Dict[str, Dict[Any, int]] = {c: defaultdict(int) for c in cols}
    if cols:
        select_list = ", ".join(f"s.{c} AS {c}" for c in cols)
        rows = conn.execute(
            f"""
            SELECT {select_list}
            FROM {RUNS_TABLE} r
            JOIN {SETTINGS_TABLE} s USING (settings_code, settings_ver)
            WHERE {where}
            """,
            params,
        ).fetchall()
        for row in rows:
            for c in cols:
                tallies[c][row[c]] += 1

    by_name = {c: _setting_meta_from_tally(c, tallies[c]) for c in cols}
    grouped: List[Dict[str, Any]] = []
    used = set()
    for group_name, group_cols in SETTING_GROUPS:
        items = [by_name[c] for c in group_cols if c in by_name]
        if items:
            grouped.append({"group": group_name, "settings": items})
            used.update(s["name"] for s in items)
    leftover = [by_name[c] for c in cols if c not in used]
    if leftover:
        grouped.append({"group": "Uncategorized", "settings": leftover})
    return grouped


def overview(
    conn: sqlite3.Connection,
    start: Optional[int],
    end: Optional[int],
    *,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    hides: Optional[List[Dict[str, Any]]] = None,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    # Intentionally does NOT call meta() — setting_groups are expensive and
    # only needed for dropdowns on /api/meta.
    totals = conn.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            SUM(success) AS ok,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail,
            MIN(timestamp) AS tmin,
            MAX(timestamp) AS tmax
        FROM {RUNS_TABLE} r
        WHERE {where}
        """,
        params,
    ).fetchone()
    n = int(totals["n"] or 0)
    ok = int(totals["ok"] or 0)
    fail = int(totals["fail"] or 0)

    # Hidden failures in the time window (always computed unfiltered for KPI context)
    time_where, time_params = time_clause(start, end)
    ssql, sparams = suite_clause(suite, has_col=has_suite_column(conn))
    hidden_n = 0
    if hide_map and not show_hidden:
        # Count failures that the hide clause would remove
        inv_parts = []
        inv_params: List[Any] = list(time_params) + list(sparams)
        for et, as_of in hide_map.items():
            if et == UNCLASSIFIED:
                inv_parts.append(
                    "(r.success = 0 AND (r.error_type IS NULL OR TRIM(r.error_type) = '') "
                    "AND r.timestamp <= ?)"
                )
                inv_params.append(int(as_of))
            else:
                inv_parts.append(
                    "(r.success = 0 AND r.error_type = ? AND r.timestamp <= ?)"
                )
                inv_params.extend([et, int(as_of)])
        if inv_parts:
            hidden_n = int(conn.execute(
                f"""
                SELECT COUNT(*) FROM {RUNS_TABLE} r
                WHERE ({time_where}) AND ({ssql}) AND ({' OR '.join(inv_parts)})
                """,
                inv_params,
            ).fetchone()[0] or 0)

    unclassified = conn.execute(
        f"""
        SELECT COUNT(*) FROM {RUNS_TABLE} r
        WHERE {where} AND r.success = 0
          AND (r.error_type IS NULL OR TRIM(r.error_type) = '')
        """,
        params,
    ).fetchone()[0]

    unknown = conn.execute(
        f"""
        SELECT COUNT(*) FROM {RUNS_TABLE} r
        WHERE {where} AND r.success = 0
          AND TRIM(r.stage) = 'Unknown'
        """,
        params,
    ).fetchone()[0]

    # Short windows (≤36h, e.g. 8h / 24h presets): bucket by hour.
    # Longer windows: bucket by calendar day.
    span = None
    if start is not None and end is not None:
        span = max(0, int(end) - int(start))
    elif start is not None:
        span = max(0, int(time.time()) - int(start))
    use_hourly = span is not None and span <= 36 * 3600
    if use_hourly:
        bucket_sql = "strftime('%Y-%m-%d %H:00', r.timestamp, 'unixepoch', 'localtime')"
        grain = "hour"
    else:
        bucket_sql = "date(r.timestamp, 'unixepoch', 'localtime')"
        grain = "day"

    days = [
        {
            "day": row["bucket"],
            "bucket": row["bucket"],
            "n": row["n"],
            "ok": row["ok"],
            "fail": row["fail"],
            "fail_rate": (row["fail"] / row["n"]) if row["n"] else None,
        }
        for row in conn.execute(
            f"""
            SELECT {bucket_sql} AS bucket,
                   COUNT(*) AS n,
                   SUM(r.success) AS ok,
                   SUM(CASE WHEN r.success = 0 THEN 1 ELSE 0 END) AS fail
            FROM {RUNS_TABLE} r
            WHERE {where}
            GROUP BY 1
            ORDER BY 1
            """,
            params,
        )
    ]

    durs = conn.execute(
        f"""
        SELECT r.success, r.duration_ms
        FROM {RUNS_TABLE} r
        WHERE {where} AND r.duration_ms IS NOT NULL
        """,
        params,
    ).fetchall()
    all_ms = sorted(int(r["duration_ms"]) for r in durs)
    ok_ms = sorted(int(r["duration_ms"]) for r in durs if r["success"])
    fail_ms = sorted(int(r["duration_ms"]) for r in durs if not r["success"])

    def dur_block(vals: List[int]) -> Dict[str, Any]:
        return {
            "n": len(vals),
            "p50_ms": _percentile(vals, 0.5),
            "p95_ms": _percentile(vals, 0.95),
            "max_ms": float(vals[-1]) if vals else None,
        }

    error_sql = _norm_error_sql()
    stage_sql = _norm_stage_sql()
    errors = [
        {"name": row["error_type"], "n": row["n"]}
        for row in conn.execute(
            f"""
            SELECT {error_sql} AS error_type, COUNT(*) AS n
            FROM {RUNS_TABLE} r
            WHERE {where} AND r.success = 0
            GROUP BY 1
            ORDER BY n DESC
            """,
            params,
        )
    ]
    stages = [
        {"name": row["stage"], "n": row["n"]}
        for row in conn.execute(
            f"""
            SELECT {stage_sql} AS stage, COUNT(*) AS n
            FROM {RUNS_TABLE} r
            WHERE {where} AND r.success = 0
            GROUP BY 1
            ORDER BY n DESC
            """,
            params,
        )
    ]

    return {
        "n": n,
        "ok": ok,
        "fail": fail,
        "success_rate": (ok / n) if n else None,
        "tmin": totals["tmin"],
        "tmax": totals["tmax"],
        "hides": hides if hides is not None else [],
        "show_hidden": bool(show_hidden),
        "suite": suite,
        "suites": list_suites(conn),
        "unclassified": int(unclassified or 0),
        "unknown": int(unknown or 0),
        "hidden_fail_n": int(hidden_n),
        "time_grain": grain,  # "hour" | "day"
        "days": days,
        "duration": {
            "all": dur_block(all_ms),
            "ok": dur_block(ok_ms),
            "fail": dur_block(fail_ms),
        },
        "errors": errors,
        "stages": stages,
    }


def setting_breakdown(
    conn: sqlite3.Connection,
    setting: str,
    start: Optional[int],
    end: Optional[int],
    *,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    col = _ident(setting)
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    error_sql = _norm_error_sql()
    stage_sql = _norm_stage_sql()

    rows = conn.execute(
        f"""
        SELECT s.{col} AS value,
               r.success,
               {error_sql} AS error_type,
               {stage_sql} AS stage,
               COUNT(*) AS n
        FROM {RUNS_TABLE} r
        JOIN {SETTINGS_TABLE} s USING (settings_code, settings_ver)
        WHERE {where}
        GROUP BY 1, 2, 3, 4
        """,
        params,
    ).fetchall()

    buckets: Dict[Any, Dict[str, Any]] = {}
    error_names = set()
    stage_names = set()
    for row in rows:
        key = UNSET if row["value"] is None else row["value"]
        b = buckets.get(key)
        if b is None:
            b = {
                "value": key,
                "label": display_value(col, row["value"]),
                "n": 0,
                "ok": 0,
                "fail": 0,
                "errors": defaultdict(int),
                "stages": defaultdict(int),
            }
            buckets[key] = b
        n = int(row["n"])
        b["n"] += n
        if row["success"]:
            b["ok"] += n
        else:
            b["fail"] += n
            if row["error_type"]:
                b["errors"][row["error_type"]] += n
                error_names.add(row["error_type"])
            if row["stage"]:
                b["stages"][row["stage"]] += n
                stage_names.add(row["stage"])

    values = []
    for b in buckets.values():
        n = b["n"]
        values.append({
            "value": b["value"],
            "label": b["label"],
            "n": n,
            "ok": b["ok"],
            "fail": b["fail"],
            "fail_rate": (b["fail"] / n) if n else None,
            "errors": dict(b["errors"]),
            "stages": dict(b["stages"]),
        })
    values.sort(key=lambda v: (-(v["fail_rate"] or 0), -v["n"], str(v["label"])))

    return {
        "setting": col,
        "is_bool": col in BOOL_COLUMNS,
        "error_types": sorted(error_names),
        "stages": sorted(stage_names),
        "values": values,
        "n": sum(v["n"] for v in values),
        "fail": sum(v["fail"] for v in values),
    }


def _error_predicate(error_type: Optional[str], alias: str = "r") -> Tuple[str, List[Any]]:
    """SQL snippet matching a failure class. error_type=None or ANY_FAIL → any fail."""
    if not error_type or error_type == ANY_FAIL:
        return f"{alias}.success = 0", []
    if error_type == UNCLASSIFIED:
        return (
            f"{alias}.success = 0 AND "
            f"({alias}.error_type IS NULL OR TRIM({alias}.error_type) = '')",
            [],
        )
    return f"{alias}.success = 0 AND {alias}.error_type = ?", [error_type]


def _stage_predicate(stage: Optional[str], alias: str = "r") -> Tuple[str, List[Any]]:
    if not stage:
        return "1=1", []
    if stage == UNCLASSIFIED:
        return (
            f"{alias}.success = 0 AND "
            f"({alias}.stage IS NULL OR TRIM({alias}.stage) = '')",
            [],
        )
    return f"{alias}.stage = ?", [stage]


def error_lift(
    conn: sqlite3.Connection,
    error_type: Optional[str],
    start: Optional[int],
    end: Optional[int],
    min_n: int = 10,
    stage: Optional[str] = None,
    hide_constant: bool = True,
    *,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    err_sql, err_params = _error_predicate(error_type)
    st_sql, st_params = _stage_predicate(stage)
    filter_sql = f"{where} AND {st_sql}"
    filter_params = list(params) + list(st_params)

    cols = [
        c for c in _present_setting_columns(conn)
        if c not in IGNORED_SETTINGS_COLUMNS
    ]
    # One JOIN scan: tally (n, errors) per setting value in Python.
    per_col: Dict[str, Dict[Any, List[int]]] = {
        c: defaultdict(lambda: [0, 0]) for c in cols
    }
    n_all = 0
    e_all = 0
    if cols:
        select_list = ", ".join(f"s.{c} AS {c}" for c in cols)
        rows = conn.execute(
            f"""
            SELECT {select_list},
                   CASE WHEN {err_sql} THEN 1 ELSE 0 END AS is_err
            FROM {RUNS_TABLE} r
            JOIN {SETTINGS_TABLE} s USING (settings_code, settings_ver)
            WHERE {filter_sql}
            """,
            list(err_params) + filter_params,
        ).fetchall()
        for row in rows:
            n_all += 1
            is_err = int(row["is_err"] or 0)
            e_all += is_err
            for c in cols:
                bucket = per_col[c][row[c]]
                bucket[0] += 1
                bucket[1] += is_err
    else:
        totals = conn.execute(
            f"""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN {err_sql} THEN 1 ELSE 0 END) AS e
            FROM {RUNS_TABLE} r
            WHERE {filter_sql}
            """,
            list(err_params) + filter_params,
        ).fetchone()
        n_all = int(totals["n"] or 0)
        e_all = int(totals["e"] or 0)

    base_rate = (e_all / n_all) if n_all else 0.0
    rows_out: List[Dict[str, Any]] = []
    for col, value_map in per_col.items():
        varied = [v for v in value_map if v is not None]
        if hide_constant and len(varied) <= 1:
            continue
        for raw, (n, e) in value_map.items():
            if n < min_n:
                continue
            rate = (e / n) if n else 0.0
            lift = (rate / base_rate) if base_rate > 0 else None
            excess = e - base_rate * n
            share = (e / e_all) if e_all else 0.0
            a, b = e, n - e
            c_rest, d_rest = e_all - e, (n_all - n) - (e_all - e)
            p = _chi2_p(a, b, c_rest, d_rest)
            rows_out.append({
                "setting": col,
                "value": UNSET if raw is None else raw,
                "label": display_value(col, raw),
                "n": n,
                "errors": e,
                "rate": rate,
                "baseline": base_rate,
                "lift": lift,
                "excess": excess,
                "share": share,
                "p_value": p,
            })

    rows_out.sort(
        key=lambda r: (
            -(r["lift"] if r["lift"] is not None else -1),
            -(r["excess"] or 0),
            -r["errors"],
        )
    )
    return {
        "error_type": error_type or ANY_FAIL,
        "stage": stage,
        "min_n": min_n,
        "n": n_all,
        "errors": e_all,
        "baseline": base_rate,
        "rows": rows_out,
    }


def crosstab(
    conn: sqlite3.Connection,
    row_setting: str,
    col_setting: str,
    start: Optional[int],
    end: Optional[int],
    error_type: Optional[str] = None,
    *,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    rcol = _ident(row_setting)
    ccol = _ident(col_setting)
    if rcol == ccol:
        raise ValueError("Row and column settings must be different")
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    err_sql, err_params = _error_predicate(error_type)

    grouped = conn.execute(
        f"""
        SELECT s.{rcol} AS row_value,
               s.{ccol} AS col_value,
               COUNT(*) AS n,
               SUM(CASE WHEN r.success = 0 THEN 1 ELSE 0 END) AS fail,
               SUM(CASE WHEN {err_sql} THEN 1 ELSE 0 END) AS e
        FROM {RUNS_TABLE} r
        JOIN {SETTINGS_TABLE} s USING (settings_code, settings_ver)
        WHERE {where}
        GROUP BY 1, 2
        """,
        list(err_params) + list(params),
    ).fetchall()

    def key_of(raw: Any) -> Any:
        return UNSET if raw is None else raw

    cells: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
    row_keys: Dict[Any, str] = {}
    col_keys: Dict[Any, str] = {}
    for g in grouped:
        rk, ck = key_of(g["row_value"]), key_of(g["col_value"])
        row_keys[rk] = display_value(rcol, g["row_value"])
        col_keys[ck] = display_value(ccol, g["col_value"])
        n = int(g["n"] or 0)
        fail = int(g["fail"] or 0)
        e = int(g["e"] or 0)
        cells[(rk, ck)] = {
            "n": n,
            "fail": fail,
            "errors": e,
            "fail_rate": (fail / n) if n else None,
            "error_rate": (e / n) if n else None,
        }

    def sort_keys(keys: Iterable[Any], labels: Dict[Any, str], dim: str) -> List[Any]:
        totals = defaultdict(int)
        for (rk, ck), cell in cells.items():
            totals[rk if dim == "row" else ck] += cell["n"]
        return sorted(keys, key=lambda k: (k == UNSET, -totals[k], labels[k]))

    row_order = sort_keys(row_keys.keys(), row_keys, "row")
    col_order = sort_keys(col_keys.keys(), col_keys, "col")

    matrix = []
    for rk in row_order:
        row_cells = []
        for ck in col_order:
            row_cells.append(cells.get((rk, ck), {
                "n": 0, "fail": 0, "errors": 0,
                "fail_rate": None, "error_rate": None,
            }))
        matrix.append(row_cells)

    return {
        "row_setting": rcol,
        "col_setting": ccol,
        "error_type": error_type or ANY_FAIL,
        "rows": [{"value": k, "label": row_keys[k]} for k in row_order],
        "cols": [{"value": k, "label": col_keys[k]} for k in col_order],
        "cells": matrix,
    }


def _value_predicate(setting: str, value: Any, unset: bool) -> Tuple[str, List[Any]]:
    col = _ident(setting)
    if unset or value == UNSET:
        return f"s.{col} IS NULL", []
    return f"s.{col} = ?", [value]


def list_runs(
    conn: sqlite3.Connection,
    *,
    start: Optional[int] = None,
    end: Optional[int] = None,
    setting: Optional[str] = None,
    value: Any = None,
    unset: bool = False,
    setting2: Optional[str] = None,
    value2: Any = None,
    unset2: bool = False,
    error_type: Optional[str] = None,
    stage: Optional[str] = None,
    success: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    hide_map: Optional[Dict[str, int]] = None,
    show_hidden: bool = False,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    where, params = _combine_where(
        *time_clause(start, end), hide_map, show_hidden, suite,
        has_suite_col=has_suite_column(conn),
    )
    clauses = [where]
    if setting:
        sql, p = _value_predicate(setting, value, unset)
        clauses.append(sql)
        params.extend(p)
    if setting2:
        sql, p = _value_predicate(setting2, value2, unset2)
        clauses.append(sql)
        params.extend(p)
    if error_type:
        sql, p = _error_predicate(error_type)
        clauses.append(sql)
        params.extend(p)
    elif success is not None:
        clauses.append("r.success = ?")
        params.append(int(success))
    if stage:
        sql, p = _stage_predicate(stage)
        clauses.append(sql)
        params.extend(p)

    where_sql = " AND ".join(clauses)
    need_join = bool(setting or setting2)
    from_sql = f"{RUNS_TABLE} r"
    if need_join:
        from_sql += f" JOIN {SETTINGS_TABLE} s USING (settings_code, settings_ver)"

    total = conn.execute(
        f"SELECT COUNT(*) FROM {from_sql} WHERE {where_sql}",
        params,
    ).fetchone()[0]

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    error_sql = _norm_error_sql()
    stage_sql = _norm_stage_sql()
    rows = conn.execute(
        f"""
        SELECT r.id, r.seed, r.timestamp, r.success, r.duration_ms,
               r.settings_code, r.settings_ver, r.app_version,
               {error_sql} AS error_type,
               {stage_sql} AS stage
        FROM {from_sql}
        WHERE {where_sql}
        ORDER BY r.timestamp DESC, r.id DESC
        LIMIT ? OFFSET ?
        """,
        list(params) + [limit, offset],
    ).fetchall()

    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "runs": [
            {
                "id": row["id"],
                "seed": row["seed"],
                "timestamp": row["timestamp"],
                "success": bool(row["success"]),
                "duration_ms": row["duration_ms"],
                "settings_code": row["settings_code"],
                "settings_ver": row["settings_ver"],
                "app_version": row["app_version"],
                "error_type": row["error_type"],
                "stage": row["stage"],
            }
            for row in rows
        ],
    }


def run_detail(conn: sqlite3.Connection, run_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        f"""
        SELECT r.*,
               {_norm_error_sql()} AS error_type_norm,
               {_norm_stage_sql()} AS stage_norm
        FROM {RUNS_TABLE} r
        WHERE r.id = ?
        """,
        (int(run_id),),
    ).fetchone()
    if row is None:
        return None

    settings_row = conn.execute(
        f"""
        SELECT * FROM {SETTINGS_TABLE}
        WHERE settings_code = ? AND settings_ver = ?
        """,
        (row["settings_code"], row["settings_ver"]),
    ).fetchone()

    settings: List[Dict[str, Any]] = []
    if settings_row is not None:
        for col in (c for c in SETTINGS_VALUE_COLUMNS if c not in IGNORED_SETTINGS_COLUMNS):
            raw = settings_row[col]
            settings.append({
                "name": col,
                "value": UNSET if raw is None else raw,
                "label": display_value(col, raw),
                "is_bool": col in BOOL_COLUMNS,
            })

    return {
        "id": row["id"],
        "seed": row["seed"],
        "mystery_name": row["mystery_name"],
        "timestamp": row["timestamp"],
        "success": bool(row["success"]),
        "duration_ms": row["duration_ms"],
        "settings_code": row["settings_code"],
        "settings_ver": row["settings_ver"],
        "app_version": row["app_version"],
        "error_type": row["error_type_norm"],
        "stage": row["stage_norm"],
        "error_type_raw": row["error_type"],
        "stage_raw": row["stage"],
        "log": row["log"] or "",
        "settings": settings,
    }
