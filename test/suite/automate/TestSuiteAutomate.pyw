#!/usr/bin/env python3
"""
Automated seed generation harness (silent-friendly for Task Scheduler).

One entry point for DB lifecycle and generation:

  # First-time / create DB + tables (safe to re-run)
  python test/suite/automate/TestSuiteAutomate.pyw init
  pythonw test/suite/automate/TestSuiteAutomate.pyw init --db <path/to/my.db>

  # After adding columns to SETTINGS_COLUMN_SPECS in TestSuiteAutomateDB.py
  python test/suite/automate/TestSuiteAutomate.pyw migrate
  # python test/suite/automate/TestSuiteAutomate.pyw migrate --add-column some_new_option:TEXT

  # Generate N mystery seeds via Customizer and log success/failure
  python test/suite/automate/TestSuiteAutomate.pyw --count 10
  pythonw test/suite/automate/TestSuiteAutomate.pyw --outputpath <output dir>
  python test/suite/automate/TestSuiteAutomate.pyw --customizer test/suite/automate/automate_customizer.yaml
  python test/suite/automate/TestSuiteAutomate.pyw --outputpath <output dir> --count 1

  python test/suite/automate/TestSuiteAutomateClassify.pyw
  python test/suite/automate/TestSuiteAutomateExplore.pyw

Scheduled Task example (no UI):
  Program:  pythonw.exe
  Arguments: test/suite/automate/TestSuiteAutomate.pyw --outputpath <output dir>
  Start in:  <repo root>
  (--outputpath also places log.db there unless --db is set)

When adding a new setting later:
  1. Encode it in BaseClasses.Settings (make_code / adjust_args_from_code)
  2. Append to SETTINGS_COLUMN_SPECS in TestSuiteAutomateDB.py
  3. python test/suite/automate/TestSuiteAutomate.pyw migrate
  4. Optionally weight it in automate_mystery.yaml
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# This file lives at <repo>/test/suite/automate/ — generation must run from repo root.
_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from test.suite.automate.TestSuiteAutomateDB import (  # noqa: E402
    DEFAULT_AUTOMATE_DIR,
    DEFAULT_CUSTOMIZER,
    DEFAULT_DB_PATH,
    RUNS_TABLE,
    SETTINGS_TABLE,
    add_settings_column,
    connect,
    ensure_indexes,
    ensure_runs_columns,
    ensure_settings_columns,
    get_app_version,
    init_schema,
    make_temp_fixed_customizer,
    record_attempt,
    settings_row_from_args,
)

# ---------------------------------------------------------------------------
# Windows: hide child console windows when running under Task Scheduler
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000


def _hidden_subprocess_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return kwargs


def _log(msg: str, log_file: Path | None = None) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ===========================================================================
# Subcommand: init
# ===========================================================================
def cmd_init(args: argparse.Namespace) -> int:
    """Create the database file and schema if missing."""
    db_path = Path(args.db)
    _log(f"Initializing DB at {db_path}")
    conn = connect(db_path)
    try:
        init_schema(conn)
        msgs = (
            ensure_settings_columns(conn)
            + ensure_runs_columns(conn)
            + ensure_indexes(conn)
        )
        for m in msgs:
            _log(m)
        _log(f"Schema ready ({SETTINGS_TABLE} + {RUNS_TABLE}).")
        #
        # --- When you add a NEW setting later ---------------------------------
        # 1. Add encoding to BaseClasses.Settings (make_code / adjust_args_from_code)
        # 2. Append a row to SETTINGS_COLUMN_SPECS in test/suite/automate/TestSuiteAutomateDB.py:
        #        ("my_new_option", "TEXT", "my_new_option", False, False),
        #    or for a bool flag:
        #        ("my_new_flag", "INTEGER", "my_new_flag", True, False),
        # 3. Run one of:
        #        python test/suite/automate/TestSuiteAutomate.pyw migrate
        #        python test/suite/automate/TestSuiteAutomate.pyw migrate --add-column my_new_option:TEXT
        # 4. Optionally weight it in test/suite/automate/automate_mystery.yml
        # --------------------------------------------------------------------
    finally:
        conn.close()
    return 0


# ===========================================================================
# Subcommand: migrate
# ===========================================================================
def cmd_migrate(args: argparse.Namespace) -> int:
    """
    Apply schema updates for new settings columns.

    Prefer updating SETTINGS_COLUMN_SPECS then running plain `migrate`.
    Use --add-column for a one-off ALTER without editing specs first.
    """
    db_path = Path(args.db)
    if not db_path.exists():
        _log(f"DB not found at {db_path}; running init first.")
        return cmd_init(args)

    conn = connect(db_path)
    try:
        init_schema(conn)  # no-op if present
        if args.add_column:
            # format: NAME or NAME:TYPE  (TYPE defaults to TEXT)
            raw = args.add_column
            if ":" in raw:
                name, sql_type = raw.split(":", 1)
            elif args.column_type:
                name, sql_type = raw, args.column_type
            else:
                name, sql_type = raw, "TEXT"
            msg = add_settings_column(conn, name.strip(), sql_type.strip(), dry_run=args.dry_run)
            _log(msg)
        else:
            msgs = (
                ensure_settings_columns(conn)
                + ensure_runs_columns(conn)
                + ensure_indexes(conn)
            )
            if not msgs:
                _log(
                    f"Schema already up to date "
                    f"({SETTINGS_TABLE} columns + indexes)."
                )
            else:
                for m in msgs:
                    _log(m)
    finally:
        conn.close()
    return 0


# ===========================================================================
# Generate seeds
# ===========================================================================
def _customizer_player_entry(customizer_path: Path):
    """Return (doc, settings.1 entry) from a customizer yaml."""
    from source.classes.CustomSettings import load_yaml

    doc = load_yaml(str(customizer_path))
    settings = doc.get("settings") or {}
    entry = settings.get(1, settings.get("1"))
    return doc, entry


def _mystery_path_from_customizer(customizer_path: Path) -> Optional[Path]:
    """
    If settings.1 is a weights-file path, resolve it.
    Fixed/inline settings maps return None (mystery is optional).
    """
    _doc, entry = _customizer_player_entry(customizer_path)
    if not isinstance(entry, str):
        return None
    candidate = (customizer_path.parent / entry).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"Mystery weights not found: {candidate}")
    return candidate


def _sanitize_suite(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Keep labels short and filename-safe for Task Scheduler / explorer
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)
    cleaned = cleaned.strip("-_.")[:64]
    return cleaned or None


def _suite_from_yaml(customizer_path: Path, mystery_path: Optional[Path] = None) -> Optional[str]:
    """Read suite label from customizer meta.suite, then weights-file suite."""
    from source.classes.CustomSettings import load_yaml

    doc = load_yaml(str(customizer_path))
    meta = doc.get("meta") or {}
    label = _sanitize_suite(meta.get("suite") or doc.get("suite"))
    if label:
        return label
    if mystery_path and mystery_path.is_file():
        weights = load_yaml(str(mystery_path))
        if isinstance(weights, dict):
            return _sanitize_suite(weights.get("suite"))
    return None


def _player_settings_from_rolled(rolled) -> dict:
    """
    Map MysteryUtils.roll_settings Namespace -> fixed customizer player settings dict.

    Uses the same yaml keys CustomSettings.adjust_args understands.
    """
    # (yaml_key, attribute on rolled namespace)
    fields = [
        ("ow_layout", "ow_layout"),
        ("ow_parallel", "ow_parallel"),
        ("ow_terrain", "ow_terrain"),
        ("ow_crossed", "ow_crossed"),
        ("ow_keepsimilar", "ow_keepsimilar"),
        ("ow_mixed", "ow_mixed"),
        ("ow_whirlpool", "ow_whirlpool"),
        ("ow_fluteshuffle", "ow_fluteshuffle"),
        ("ow_fog", "ow_fog"),
        ("shuffle_followers", "shuffle_followers"),
        ("bonk_drops", "bonk_drops"),
        ("shuffle", "shuffle"),
        ("door_shuffle", "door_shuffle"),
        ("logic", "logic"),
        ("mode", "mode"),
        ("swords", "swords"),
        ("flute_mode", "flute_mode"),
        ("bow_mode", "bow_mode"),
        ("item_functionality", "item_functionality"),
        ("goal", "goal"),
        ("difficulty", "difficulty"),
        ("accessibility", "accessibility"),
        ("retro", "retro"),
        ("take_any", "take_any"),
        ("hints", "hints"),
        ("shopsanity", "shopsanity"),
        ("dropshuffle", "dropshuffle"),
        ("pottery", "pottery"),
        ("mixed_travel", "mixed_travel"),
        ("standardize_palettes", "standardize_palettes"),
        ("intensity", "intensity"),
        ("door_type_mode", "door_type_mode"),
        ("trap_door_mode", "trap_door_mode"),
        ("key_logic_algorithm", "key_logic_algorithm"),
        ("decoupledoors", "decoupledoors"),
        ("door_self_loops", "door_self_loops"),
        ("dungeon_counters", "dungeon_counters"),
        ("crystals_gt", "crystals_gt"),
        ("crystals_ganon", "crystals_ganon"),
        ("experimental", "experimental"),
        ("collection_rate", "collection_rate"),
        ("openpyramid", "openpyramid"),
        ("prizeshuffle", "prizeshuffle"),
        ("bigkeyshuffle", "bigkeyshuffle"),
        ("keyshuffle", "keyshuffle"),
        ("mapshuffle", "mapshuffle"),
        ("compassshuffle", "compassshuffle"),
        ("boss_shuffle", "shufflebosses"),
        ("enemy_shuffle", "shuffleenemies"),
        ("enemy_health", "enemy_health"),
        ("enemy_damage", "enemy_damage"),
        ("any_enemy_logic", "any_enemy_logic"),
        ("shufflepots", "shufflepots"),
        ("bombbag", "bombbag"),
        ("shufflelinks", "shufflelinks"),
        ("shuffletavern", "shuffletavern"),
        ("shuffleganon", "shuffleganon"),
        ("skullwoods", "skullwoods"),
        ("linked_drops", "linked_drops"),
        ("restrict_boss_items", "restrict_boss_items"),
        ("overworld_map", "overworld_map"),
        ("pseudoboots", "pseudoboots"),
        ("mirrorscroll", "mirrorscroll"),
        ("triforce_goal", "triforce_goal"),
        ("triforce_pool", "triforce_pool"),
        ("triforce_goal_min", "triforce_goal_min"),
        ("triforce_goal_max", "triforce_goal_max"),
        ("triforce_pool_min", "triforce_pool_min"),
        ("triforce_pool_max", "triforce_pool_max"),
        ("triforce_min_difference", "triforce_min_difference"),
        ("triforce_max_difference", "triforce_max_difference"),
    ]
    out = {}
    for yaml_key, attr in fields:
        if not hasattr(rolled, attr):
            continue
        val = getattr(rolled, attr)
        if val is not None:
            out[yaml_key] = val
    return out


def _generation_python() -> str:
    """
    Interpreter used for DungeonRandomizer subprocesses.
    Prefer python.exe over pythonw.exe so child processes behave consistently.
    """
    exe = sys.executable
    lowered = exe.lower()
    if lowered.endswith("pythonw.exe"):
        candidate = exe[:-len("pythonw.exe")] + "python.exe"
        if Path(candidate).is_file():
            return candidate
    if lowered.endswith("pythonw"):
        candidate = exe[:-len("pythonw")] + "python"
        if Path(candidate).is_file():
            return candidate
    return exe


def _preflight_dependencies(log_file: Path | None = None) -> list[str]:
    """
    Return human-readable errors for missing runtime deps.
    Generation imports Rom, which requires the bps package (python-bps-continued).
    """
    errors = []
    try:
        import bps.apply  # noqa: F401
        import bps.io  # noqa: F401
    except ImportError:
        errors.append(
            f"Missing package 'bps' (PyPI: python-bps-continued) for interpreter:\n"
            f"  {sys.executable}\n"
            f"Install with:\n"
            f"  \"{sys.executable}\" -m pip install python-bps-continued\n"
            f"Note: this is per-Python-install. If `py` defaults to 3.9 but your\n"
            f"working DR install is 3.8, either install bps on 3.9 or launch the\n"
            f"harness with the 3.8 interpreter explicitly."
        )
    return errors


def _roll_frozen_settings(
    customizer_path: Path,
    seed: int | None,
    temp_dir: Path | str | None = None,
):
    """
    Build a fixed customizer + settings row for one attempt.

    settings.1 may be:
      - a mystery weights path (string) → roll once, then freeze
      - a fixed settings map (dict) → use as-is (no mystery required)

    Generation always runs against a temp fixed yaml so logging and generation
    share the same resolved settings. Avoids importing Main/Rom here.
    """
    import RaceRandom as random
    from CLI import parse_cli
    from source.classes.CustomSettings import CustomSettings
    from source.tools.MysteryUtils import get_weights, roll_settings

    customizer_path = customizer_path.resolve()
    doc, entry = _customizer_player_entry(customizer_path)
    meta = doc.get("meta") or {}
    algorithm = meta.get("algorithm")

    if seed is None:
        random.seed(None)
        seed = random.randint(0, 999999999)
    seed = int(seed)
    random.seed(seed)

    if isinstance(entry, str):
        mystery_path = (customizer_path.parent / entry).resolve()
        if not mystery_path.is_file():
            raise FileNotFoundError(f"Mystery weights not found: {mystery_path}")
        weights = get_weights(str(mystery_path))
        rolled = roll_settings(weights)
        player_settings = _player_settings_from_rolled(rolled)
        rolled_algo = getattr(rolled, "algorithm", None)
        if isinstance(rolled_algo, str):
            algorithm = rolled_algo
    elif isinstance(entry, dict):
        player_settings = dict(entry)
    else:
        raise ValueError(
            f"{customizer_path}: settings.1 must be a mystery weights path "
            f"or a fixed settings map"
        )

    if temp_dir is None and DEFAULT_AUTOMATE_DIR.exists():
        temp_dir = DEFAULT_AUTOMATE_DIR
    fixed_path = make_temp_fixed_customizer(
        player_settings,
        seed=seed,
        algorithm=algorithm if isinstance(algorithm, str) else None,
        directory=str(temp_dir) if temp_dir is not None else None,
    )

    # Apply fixed customizer onto CLI args without Main.init_world (no Rom/bps).
    fixed_args = parse_cli([
        "--customizer", fixed_path,
        "--suppress_rom",
        "--spoiler", "none",
        "--seed", str(seed),
        "--loglevel", "error",
    ])
    fixed_args.suppress_rom = True
    fixed_args.spoiler = "none"
    if isinstance(algorithm, str):
        fixed_args.algorithm = algorithm

    custom = CustomSettings()
    custom.load_yaml(fixed_path)
    custom.adjust_args(fixed_args, resolve_weighted=False)

    settings_row = settings_row_from_args(fixed_args, player=1)
    return seed, settings_row, fixed_path, algorithm


def _kill_process_tree(pid: int) -> None:
    """Kill a process and any children (needed on Windows after a timeout)."""
    if pid is None or pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                **_hidden_subprocess_kwargs(),
            )
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


class _WindowsKillJob:
    """
    Windows Job Object that kills its members when the handle is closed and can
    be terminated explicitly on timeout.

    Enables Parallel Task Scheduler runs: each harness owns a job for its
    generator child, so a 30-minute timeout (or harness exit / TS killing the
    parent) cannot leave an orphaned multi-GB python.exe behind.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._handle = None
        self._assigned = False

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            return
        self._handle = handle

    @property
    def active(self) -> bool:
        return bool(self._handle)

    def assign(self, pid: int) -> bool:
        if not self._handle or pid is None or pid <= 0:
            return False
        ctypes = self._ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
        if not handle:
            return False
        try:
            if not kernel32.AssignProcessToJobObject(self._handle, handle):
                return False
            self._assigned = True
            return True
        finally:
            kernel32.CloseHandle(handle)

    def terminate(self) -> None:
        if not self._handle:
            return
        try:
            self._ctypes.windll.kernel32.TerminateJobObject(self._handle, 1)
        except Exception:
            pass

    def close(self) -> None:
        """Close the job handle; with KILL_ON_JOB_CLOSE this kills survivors."""
        if not self._handle:
            return
        try:
            self._ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None


def _cap_log(log_text: str, limit: int = 50000) -> str:
    if len(log_text) <= limit:
        return log_text
    half = limit // 2
    return log_text[:half] + "\n...\n" + log_text[-half:]


def _process_private_bytes(pid: int) -> int | None:
    """Best-effort private memory bytes for a PID (Windows WorkingSet fallback)."""
    if pid is None or pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, int(pid)
            )
            if not handle:
                return None
            try:
                class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                        ("PrivateUsage", ctypes.c_size_t),
                    ]

                counters = PROCESS_MEMORY_COUNTERS_EX()
                counters.cb = ctypes.sizeof(counters)
                ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                )
                if not ok:
                    return None
                return int(counters.PrivateUsage or counters.PagefileUsage or 0)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            # Linux: Private_Dirty-ish via status VmRSS as a lower bound
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return int(parts[1]) * 1024
    except Exception:
        return None
    return None


def _read_text_file(path: Path | None, limit: int = 200000) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def _run_one_generation(
    fixed_customizer: str,
    seed: int,
    outputpath: Path,
    spoiler: str = "full",
    timeout_sec: int = 1800,
    memory_limit_mb: int = 0,
) -> tuple[bool, str]:
    """
    Spawn DungeonRandomizer with a fixed customizer (no mystery re-roll).
    Writes spoiler (and any other outputs) under outputpath (the daily folder).

    If the process exceeds timeout_sec (default 30 minutes), it is killed and
    the attempt is reported as a failure. Settings for the attempt are captured
    by the harness *before* this call (freeze path), so a timeout still records
    the full settings row / code in the DB. A partial spoiler may already exist
    in outputpath from early Main.

    If memory_limit_mb > 0, the child is also killed when its private memory
    exceeds that many MiB (guards against algorithmic memory runaways that would
    otherwise thrash the host until the wall-clock timeout).

    On Windows the child is placed in a Job Object with KILL_ON_JOB_CLOSE so
    Parallel Task Scheduler instances can overlap safely: when this harness
    exits (timeout handler, crash, or TS stopping the task), the generator
    cannot survive as an orphan.

    stage / error_type are left blank for TestSuiteAutomateClassify.pyw to fill
    later by scanning the log text.

    Returns (success, log_text).
    """
    cmd = [
        _generation_python(),
        str(REPO_ROOT / "DungeonRandomizer.py"),
        "--customizer", fixed_customizer,
        "--seed", str(seed),
        "--suppress_rom",
        "--spoiler", spoiler,
        "--loglevel", "error",
        "--outputpath", str(outputpath),
    ]

    # Capture child output to temp files (not PIPEs) so a thrashing generator
    # cannot stall the harness timeout path on a full pipe buffer.
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    stdout_f = None
    stderr_f = None
    proc = None
    job = None
    try:
        out_fd, out_name = tempfile.mkstemp(
            prefix=f"gen_{seed}_", suffix="_stdout.txt", dir=str(outputpath)
        )
        err_fd, err_name = tempfile.mkstemp(
            prefix=f"gen_{seed}_", suffix="_stderr.txt", dir=str(outputpath)
        )
        os.close(out_fd)
        os.close(err_fd)
        stdout_path = Path(out_name)
        stderr_path = Path(err_name)
        stdout_f = open(stdout_path, "wb")
        stderr_f = open(stderr_path, "wb")

        popen_kwargs = dict(
            stdout=stdout_f,
            stderr=stderr_f,
            cwd=str(REPO_ROOT),
            **_hidden_subprocess_kwargs(),
        )
        # On POSIX, start a new session so we can kill the whole group on timeout.
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kwargs)
        if os.name == "nt":
            job = _WindowsKillJob()
            if job.active:
                job.assign(proc.pid)

        deadline = (
            time.monotonic() + float(timeout_sec)
            if timeout_sec and timeout_sec > 0
            else None
        )
        mem_limit_bytes = (
            int(memory_limit_mb) * 1024 * 1024
            if memory_limit_mb and memory_limit_mb > 0
            else 0
        )
        poll_interval = 2.0 if mem_limit_bytes else 5.0
        timed_out = False
        mem_killed = False
        peak_mb = 0
        while proc.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            if mem_limit_bytes:
                usage = _process_private_bytes(proc.pid)
                if usage:
                    peak_mb = max(peak_mb, usage // (1024 * 1024))
                    if usage >= mem_limit_bytes:
                        mem_killed = True
                        break
            time.sleep(poll_interval)

        if timed_out or mem_killed:
            if job is not None and job.active:
                job.terminate()
            _kill_process_tree(proc.pid)
            # Brief wait for process to disappear; do not block on pipes.
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
            try:
                stdout_f.flush()
                stderr_f.flush()
            except Exception:
                pass
            stdout = _read_text_file(stdout_path)
            stderr = _read_text_file(stderr_path)
            if timed_out:
                reason = (
                    f"Generation timed out after {timeout_sec}s "
                    f"(seed={seed}). Process tree killed.\n"
                )
            else:
                reason = (
                    f"Generation exceeded memory limit "
                    f"({memory_limit_mb}MB, peak~{peak_mb}MB, seed={seed}). "
                    f"Process tree killed.\n"
                )
            log_text = (
                reason
                + "Settings were frozen before generation; see suite_settings via "
                + f"this run's settings_code. A partial spoiler may exist under "
                + f"{outputpath}.\n"
            )
            if stdout:
                log_text += "\n--- stdout ---\n" + stdout
            if stderr:
                log_text += "\n--- stderr ---\n" + stderr
            return False, _cap_log(log_text)

        stdout = _read_text_file(stdout_path)
        stderr = _read_text_file(stderr_path)
    except Exception:
        if proc is not None and proc.poll() is None:
            if job is not None and job.active:
                job.terminate()
            _kill_process_tree(proc.pid)
        return False, traceback.format_exc()
    finally:
        for fh in (stdout_f, stderr_f):
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
        if job is not None:
            # Closing the job handle kills any survivors (KILL_ON_JOB_CLOSE).
            job.close()
        for path in (stdout_path, stderr_path):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass

    log_text = ""
    if stdout:
        log_text += stdout
    if stderr:
        if log_text:
            log_text += "\n"
        log_text += stderr
    log_text = _cap_log(log_text)

    return proc.returncode == 0, log_text


def cmd_run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    customizer = Path(args.customizer)
    output_root = Path(args.outputpath)

    # Daily folder holds spoilers + harness log (same layout as the old Automate tree)
    day_dir = output_root / datetime.now().strftime("%y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    log_file = day_dir / f"_harness_{datetime.now().strftime('%y%m%d')}.log"

    if not customizer.is_file():
        _log(f"Customizer not found: {customizer}", log_file)
        return 2

    dep_errors = _preflight_dependencies(log_file)
    if dep_errors:
        for err in dep_errors:
            _log(err, log_file)
        return 3

    # Auto-init schema so first scheduled run still works
    conn = connect(db_path)
    try:
        init_schema(conn)
        ensure_settings_columns(conn)
        ensure_runs_columns(conn)
        ensure_indexes(conn)
    finally:
        conn.close()

    count = max(int(args.count), 1)
    app_version = get_app_version()
    spoiler = getattr(args, "spoiler", "full") or "full"
    # 0 disables the cap; default 1800s = 30 minutes
    timeout_sec = int(getattr(args, "timeout", 1800) or 0)
    # 0 disables; default 4096MB guards against algorithmic memory runaways
    memory_limit_mb = int(getattr(args, "memory_limit_mb", 4096) or 0)
    mystery_path = _mystery_path_from_customizer(customizer)
    suite = _sanitize_suite(getattr(args, "suite", None)) or _suite_from_yaml(
        customizer, mystery_path
    ) or "default"
    alive = 0

    _log(
        f"Starting run: count={count} suite={suite} spoiler={spoiler} "
        f"timeout={timeout_sec}s memory_limit_mb={memory_limit_mb} "
        f"customizer={customizer} db={db_path} out={day_dir}",
        log_file,
    )

    for i in range(1, count + 1):
        fixed_path = None
        t0 = time.perf_counter()
        ts = int(time.time())
        seed = None
        settings_row = None
        success = False
        log_text = ""

        try:
            # Freeze settings first so timeout/crash during generate still logs them.
            seed, settings_row, fixed_path, _algo = _roll_frozen_settings(
                customizer, seed=None, temp_dir=day_dir
            )
            success, log_text = _run_one_generation(
                fixed_path,
                seed,
                day_dir,
                spoiler=spoiler,
                timeout_sec=timeout_sec,
                memory_limit_mb=memory_limit_mb,
            )
        except Exception:
            success = False
            log_text = traceback.format_exc()
            # settings_row may still be None if roll failed early
        finally:
            if fixed_path:
                try:
                    os.unlink(fixed_path)
                except OSError:
                    pass

        duration_ms = int((time.perf_counter() - t0) * 1000)

        if settings_row is None:
            # Cannot satisfy FK without a settings row; store a placeholder code row
            from BaseClasses import settings_version
            settings_row = {
                "settings_code": f"UNKNOWN-{ts}-{i}",
                "settings_ver": int(settings_version),
            }

        conn = connect(db_path)
        try:
            # stage / error_type left blank — TestSuiteAutomateClassify.pyw fills them
            # later by scanning log for known phrases.
            record_attempt(
                conn,
                settings_row,
                seed=seed,
                mystery_name=None,
                suite=suite,
                app_version=app_version,
                timestamp=ts,
                success=success,
                duration_ms=duration_ms,
                stage=None,
                error_type=None,
                log=log_text if not success else (log_text if args.store_success_log else None),
            )
        finally:
            conn.close()

        if success:
            alive += 1
        status = "OK" if success else "FAIL"
        _log(
            f"[{i}/{count}] seed={seed} {status} "
            f"code={settings_row.get('settings_code')} {duration_ms}ms",
            log_file,
        )

    rate = (alive / count) * 100.0
    _log(f"Done. Success {alive}/{count} ({rate:.2f}%)", log_file)
    return 0 if alive == count else 1


# ===========================================================================
# CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="TestSuiteAutomate",
        description="Automated seed generation + SQLite logging (init / migrate).",
    )

    # Generation flags are top-level (default action is generate):
    #   TestSuiteAutomate.pyw --outputpath <dir>
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite database path (default: <outputpath>/log.db)",
    )
    parser.add_argument(
        "--outputpath",
        default=None,
        help="Root for daily spoiler/harness folders (default: DEFAULT_AUTOMATE_DIR)",
    )
    parser.add_argument(
        "--count",
        type=lambda v: max(int(v), 1),
        default=1,
        help="Number of seeds to generate (default: 1)",
    )
    parser.add_argument(
        "--customizer",
        default=None,
        help="Customizer yaml (default: DEFAULT_CUSTOMIZER)",
    )
    parser.add_argument(
        "--suite",
        default=None,
        help="Suite label stored on each run (default: meta.suite in the customizer yaml)",
    )
    parser.add_argument(
        "--spoiler",
        default="full",
        choices=["none", "settings", "semi", "full", "debug"],
        help="Spoiler level written into the daily output folder (default: full)",
    )
    parser.add_argument(
        "--timeout",
        type=lambda v: max(int(v), 0),
        default=1800,
        help="Max seconds per seed generation before kill + DB failure row "
             "(default: 1800 = 30 minutes; 0 = no limit)",
    )
    parser.add_argument(
        "--memory-limit-mb",
        type=lambda v: max(int(v), 0),
        default=4096,
        help="Kill generation if private memory exceeds this many MiB "
             "(default: 4096; 0 = no limit). Survives Task Scheduler orphaning "
             "better than wall-clock timeout alone.",
    )
    parser.add_argument(
        "--store-success-log",
        action="store_true",
        help="Store stdout/stderr on successes too (default: failures only)",
    )
    parser.add_argument(
        "--cpu_threads",
        type=int,
        default=multiprocessing.cpu_count(),
        help=argparse.SUPPRESS,
    )

    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create DB file and tables if missing")
    p_init.set_defaults(func=cmd_init)

    p_mig = sub.add_parser(
        "migrate",
        help="Add any missing settings columns from SETTINGS_COLUMN_SPECS "
             "(or --add-column for a one-off)",
    )
    p_mig.add_argument(
        "--add-column",
        metavar="NAME[:TYPE]",
        help="Manually ALTER TABLE settings ADD COLUMN. "
             "Example: --add-column my_option TEXT",
    )
    p_mig.add_argument(
        "--column-type",
        default="TEXT",
        help="SQL type when --add-column is NAME only (default: TEXT)",
    )
    p_mig.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ALTER without applying it",
    )
    p_mig.set_defaults(func=cmd_migrate)

    return parser


def apply_path_defaults(args: argparse.Namespace) -> None:
    """outputpath defaults to DEFAULT_AUTOMATE_DIR; db defaults to <outputpath>/log.db."""
    args.outputpath = args.outputpath or str(DEFAULT_AUTOMATE_DIR)
    if not args.db:
        args.db = str(Path(args.outputpath) / "log.db")
    if not getattr(args, "customizer", None):
        args.customizer = str(DEFAULT_CUSTOMIZER)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        args.func = cmd_run

    apply_path_defaults(args)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
