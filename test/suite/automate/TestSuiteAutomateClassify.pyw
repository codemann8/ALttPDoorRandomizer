#!/usr/bin/env python3
"""
Periodic classifier for TestSuiteAutomate DB rows.

Scans suite_runs.log for known phrases (first match wins) and fills the
repurposed stage / error_type columns. Run on a schedule after generation.

  python test/suite/automate/TestSuiteAutomateClassify.pyw
  python test/suite/automate/TestSuiteAutomateClassify.pyw --db <path/to/log.db>
  python test/suite/automate/TestSuiteAutomateClassify.pyw --dry-run
  python test/suite/automate/TestSuiteAutomateClassify.pyw --force

Task Scheduler (no console window):

  Preferred: launch pythonw.exe directly
    Program:  <pythonw.exe>
    Arguments: test/suite/automate/TestSuiteAutomateClassify.pyw
    Start in:  <repo root>

  Do NOT use python.exe / py.exe as the task action — that always flashes a
  black console. Summary output is always appended next to the DB as
  _classify.log when not running in an interactive terminal.

Harness (TestSuiteAutomate.pyw) leaves stage/error_type blank on insert; this
script is the intended writer for those columns.

Without --force: classifies blank (unclassified) rows and reclassifies rows
whose stage is still "Unknown" (catch-all / provisional tags).
With --force: reclassifies every failure that has a log, overriding any prior tag.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, NamedTuple, Optional, Pattern, Sequence, TextIO, Tuple, Union

# This file lives at <repo>/test/suite/automate/
_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# Do not chdir — only need imports / DB access

from test.suite.automate.TestSuiteAutomateDB import (  # noqa: E402
    DEFAULT_DB_PATH,
    RUNS_TABLE,
    connect,
)


def _running_under_pythonw() -> bool:
    exe = (sys.executable or "").lower().replace("/", "\\")
    return exe.endswith("pythonw.exe") or exe.endswith("\\pythonw")


def _interactive_console() -> bool:
    try:
        return bool(sys.stdout and sys.stdout.isatty())
    except Exception:
        return False


def _pythonw_path() -> Optional[Path]:
    exe = Path(sys.executable)
    name = exe.name.lower()
    if name == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.is_file():
            return candidate
    if name == "python":
        candidate = exe.with_name("pythonw")
        if candidate.is_file():
            return candidate
    # Common layout next to the active interpreter
    for cand in (exe.parent / "pythonw.exe", exe.parent / "pythonw"):
        if cand.is_file():
            return cand
    return None


def _relaunch_hidden_if_needed(argv: List[str]) -> None:
    """
    If started via python.exe without an interactive console (typical Task
    Scheduler misconfiguration), re-exec under pythonw.exe so no black window
    stays open. Prefer configuring the task to use pythonw.exe / the .vbs
    launcher so there is no flash at all.
    """
    if os.name != "nt":
        return
    if _running_under_pythonw():
        return
    if _interactive_console():
        return
    if "--keep-console" in argv:
        return
    pythonw = _pythonw_path()
    if pythonw is None:
        return
    # os.execv replaces this process (console may flash briefly then vanish)
    os.execv(str(pythonw), [str(pythonw), str(Path(__file__).resolve()), *argv])


class _Tee:
    """Write to console (if any) and an optional log file."""

    def __init__(self, streams: List[TextIO]):
        self.streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass


def _setup_output(db_path: Path) -> Optional[TextIO]:
    """
    Always append a summary log next to the DB when not interactive / under
    pythonw, so scheduled runs leave a trail without needing a console.
    """
    log_path = Path(db_path).expanduser().resolve().parent / "_classify.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return open(log_path, "a", encoding="utf-8")
    except Exception:
        return None


# ===========================================================================
# Classification rules — first match wins (order matters).
#
# Each rule: (match, error_type, stage)
#   match       — one string, or a list/tuple of strings
#                 ALL listed phrases must appear in the log (AND) for the rule
#                 to match. Order of phrases does not matter.
#                 Plain substring by default; each phrase is a regex if regex=True
#   error_type  — short bucket for the failure (e.g. Keylock)
#   stage       — coarse phase / subsystem (e.g. Dungeon)
#
# Examples:
#   Rule("Generation timed out after", "Timeout", "Generate")
#   Rule(["RuntimeError: Keylock detected", "Hyrule Castle"], "Keylock", "Dungeon")
#
# Add new rows at the top of a group when a more specific phrase should beat a
# broader one. Success rows (success=1) are never classified.
# ===========================================================================

MatchSpec = Union[str, Sequence[str]]


class Rule(NamedTuple):
    match: MatchSpec
    error_type: str
    stage: str
    regex: bool = False
    case_insensitive: bool = False


def _match_phrases(match: MatchSpec) -> List[str]:
    """Normalize match to a non-empty list of phrase strings."""
    if isinstance(match, str):
        phrases = [match]
    else:
        phrases = [p for p in match if p]
    if not phrases:
        raise ValueError("Rule.match must contain at least one non-empty string")
    return phrases


# Edit this list as you discover new common log phrases.
RULES: List[Rule] = [
    # --- Timeouts (harness-written log text) ---
    Rule("Generation timed out after", "Timeout", "Generate"),
    Rule("ModuleNotFoundError", "ModuleNotFound", "Generate"),
    Rule("MemoryError", "MemoryError", "Generate"),

    # --- Entrances / overworld ---
    Rule(["do_vanilla_connect", "KeyError: 'Skull Woods First Section Door'"], "VanillaConnect-SWKeyError", "EntrancePlacement"),
    Rule(["do_vanilla_connect", "KeyError"], "VanillaConnect-KeyError", "EntrancePlacement"),
    Rule(["do_old_man_cave_exit", "KeyError"], "OldMan-KeyError", "EntrancePlacement"),
    Rule(["AssertionError", "No available entrances left to place Old Man Cave"], "OldMan-Assert", "EntrancePlacement"),
    Rule(["ValueError", "do_old_man_cave_exit"], "OldMan-IndexError", "EntrancePlacement"),
    Rule("Dark Sanctuary Hint was placed earlier", "DarkSanctuaryHint", "EntrancePlacement"),
    Rule("No available entrances left to place Blacksmith", "Blacksmith-Assert", "EntrancePlacement"),
    Rule("Not enough entrances for restricted cave", "RestrictedCave-Assert", "EntrancePlacement"),
    Rule(["do_limited_shuffle", "pop from empty list"], "LimitedShuffle-EmptyChoice", "EntrancePlacement"),
    Rule(["do_mandatory_connections", "ValueError"], "MandatoryConnect-NotInList", "EntrancePlacement"),
    Rule(["do_mandatory_connections", "IndexError"], "MandatoryConnect-EmptyChoice", "EntrancePlacement"),
    Rule(["do_holes_and_linked_drops", "KeyError", "Pyramid Entrance"], "HolesAndLinkedDrops-PyramidKeyError", "EntrancePlacement"),
    Rule(["do_holes_and_linked_drops", "ValueError"], "HolesAndLinkedDrops-NotInList", "EntrancePlacement"),
    Rule(["do_holes_and_linked_drops", "StopIteration"], "HolesAndLinkedDrops-StopIteration", "EntrancePlacement"),
    # Rule("some OW phrase", "OwFailure", "Overworld"),

    Rule("Infinite loop detected in flute shuffle", "FluteShuffle-InfiniteLoop", "Overworld"),

    # --- Dungeon / key logic ---
    Rule("Complex branch problems", "ComplexBranch", "Dungeon"),
    Rule("Unable to find door permutation", "DoorPermute", "Dungeon"),
    Rule("Exception: Max Counter is none", "MaxCounterNone", "Dungeon"),
    Rule(["handle_split_dungeons", "Unable to resolve in"], "SplitDungeonResolve", "Dungeon"),
    Rule(["Something went terribly wrong I think", "check_for_valid_layout"], "TerriblyWrongLayout", "Dungeon"),
    Rule(["find_valid_trap_combination", "Bad dungeon"], "BadTrap", "Dungeon"),
    Rule(["find_valid_bk_combination", "Bad dungeon"], "BadBKDoor", "Dungeon"),
    Rule(["generate_dungeon_find_proposal", "StopIteration"], "DungeonProposal-StopIteration", "Dungeon"),
    Rule(["check_required_paths", "cannot reach"], "PathReachability", "Dungeon"),
    Rule(["link_doors_prep", "No reachable entrances"], "EntranceReachability", "Dungeon"),
    Rule("need to provide more sophisticated crystal connection", "CrystalReachability", "Dungeon"),
    Rule("Not enough crystal switch sectors for those needed", "CrystalSwitchSectors", "Dungeon"),
    Rule("No crystal switches to assign", "CrystalSwitchAssign", "Dungeon"),
    Rule(["IndexError", "assign_crystal_barrier_sectors"], "CrystalSwitch-IndexError", "Dungeon"),
    Rule(["Could not find a valid seed quickly", "Either free location/crystal assignment"], "Timeout-CrystalNeutrality", "Dungeon"),
    Rule(["Could not find a valid seed quickly", "Simple branch problems"], "Timeout-SimpleBranch", "Dungeon"),
    Rule(["Could not find a valid seed quickly", "Crystal switch issue"], "Timeout-CrystalSwitch", "Dungeon"),
    Rule(["Could not find a valid seed quickly", "Cannot find a candidate for connectedness"], "Timeout-ConnectionCandidate", "Dungeon"),
    Rule(["Could not create world in", "Infinite loop detected"], "Timeout-InfiniteLoop", "Dungeon"),
    #Rule(["Could not create world in", ], "Timeout-InfiniteLoop", "Dungeon"),

    Rule("RuntimeError: Keylock detected", "Keylock", "Dungeon"),
    Rule(["KeyError", "vanilla_key_logic", "adjust_hc_door"], "HCDoor-KeyError", "Dungeon"),
    Rule(["Infinite loop detected", "vanilla_key_logic"], "VanillaKeyLogic-InfiniteLoop", "Dungeon"),
    Rule(["KeyError", "vanilla_key_logic"], "VanillaKeyLogic-KeyError", "Dungeon"),
    Rule(["KeyError", "validate_key_placement", "self_locked_child_door"], "ChildDoor-KeyError", "Dungeon"),

    # Rule(["Keylock detected", "Palace of Darkness"], "Keylock", "PoD"),
    #Rule("Unable to meet minimum requirements", "MinRequirements", "Dungeon"),

    # Enemizer
    Rule(["randomize_underworld_rooms", "IndexError"], "Enemizer-IndexError", "Enemizer"),

    Rule(["find_rules_for_zelda_delivery", "Exception: No path to Throne"], "Standard-ThronePath", "Rules"),

    # --- Fill / item placement ---
    Rule(["IndexError", "generate_itempool", "starting_weapon"], "StartingWeapon-IndexError", "ItemPool"),

    Rule(["FillError", "Unable to place followers"], "Followers-FillError", "Fill"),
    Rule("Exception: Major only: there are only ", "Major-OutOfLocations", "Fill"),
    #Rule("FillError", "FillError", "Fill"),
    #Rule("Could not fill items", "FillError", "Fill"),
    Rule("Unable to place dungeon prizes", "PrizeFill", "Fill"),
    Rule(["KeyError", "ensure_good_items"], "PrizeFill-KeyError", "Fill"),

    Rule(["FillError", "No more spots to place"], "NoMoreSpots", "Fill"),
    Rule(["FillError", "Rare placement for "], "RarePlacement", "Fill"),
    Rule(["StopIteration", "filtered_fill"], "FilteredFill-StopIteration", "Fill"),
    Rule(["RecursionError", "sweep_from_pool"], "SweepFromPool-RecursionError", "Fill"),
    Rule(["AssertionError", "len(placeholder_items)"], "Placeholder-AssertionError", "Fill"),
    Rule(["ValueError", "ensure_good_items"], "EnsureGoodItems-ValueError", "Fill"),
    
    Rule("Infinite loop detected at \"balance_money_progression\"", "InfiniteLoop", "MoneyBalance"),
    Rule("money grind", "MoneyGrindRequired", "MoneyBalance"),
    Rule(["sell_keys", "IndexError"], "SellKeys-IndexError", "Fill"),

    # Spoiler / playthru / beatability
    Rule("Not all progression items reachable", "UnreachableProg", "CanBeatGame"),
    Rule("Not all required items reachable", "UnreachableRequired", "CanBeatGame"),
    Rule("Cannot beat game!", "CannotBeatGame", "CanBeatGame"),

    # --- Catch-alls (keep last) ---
    Rule("RecursionError", "RecursionError", "Unknown"),
    Rule("RuntimeError", "RuntimeError", "Unknown"),
    Rule("IndexError", "IndexError", "Unknown"),
    Rule("ValueError", "ValueError", "Unknown"),
    Rule("AssertionError", "AssertionError", "Unknown"),
    Rule("KeyError", "KeyError", "Unknown"),
    Rule("FillError", "FillError", "Unknown"),
    Rule("StopIteration", "StopIteration", "Unknown"),
    #Rule("Exception:", "Exception", "Unknown"),
    #Rule("Traceback (most recent call last):", "Traceback", "Unknown"),
]


def _compile_rules(rules: List[Rule]) -> List[Tuple[Rule, List[Pattern[str]], List[str]]]:
    """
    For each rule, compile one pattern per phrase.
    A rule matches only when every pattern hits the log (AND).
    """
    compiled = []
    for rule in rules:
        phrases = _match_phrases(rule.match)
        flags = re.IGNORECASE if rule.case_insensitive else 0
        patterns = []
        for phrase in phrases:
            if rule.regex:
                patterns.append(re.compile(phrase, flags))
            else:
                patterns.append(re.compile(re.escape(phrase), flags))
        compiled.append((rule, patterns, phrases))
    return compiled


def classify_log(
    log_text: Optional[str], compiled_rules
) -> Optional[Tuple[str, str, str]]:
    """
    Return (error_type, stage, matched_description) for the first matching rule,
    or None. A multi-phrase rule matches only if every phrase is found.
    """
    if not log_text:
        return None
    for rule, patterns, phrases in compiled_rules:
        if all(pat.search(log_text) for pat in patterns):
            if len(phrases) == 1:
                desc = phrases[0]
            else:
                desc = " AND ".join(repr(p) for p in phrases)
            return rule.error_type, rule.stage, desc
    return None


def cmd_classify(args: argparse.Namespace, emit) -> int:
    db_path = Path(args.db)
    if not db_path.is_file():
        emit(f"DB not found: {db_path}")
        return 2

    compiled = _compile_rules(RULES)
    conn = connect(db_path)
    try:
        # Only failures with a log.
        # --force: every such row (override any existing stage/error_type).
        # default: blanks, or stage already "Unknown" (provisional catch-all).
        base = "success = 0 AND log IS NOT NULL AND TRIM(log) != ''"
        if args.force:
            where = base
        else:
            where = (
                f"{base} AND ("
                "  ("
                "    (error_type IS NULL OR TRIM(error_type) = '') "
                "    AND (stage IS NULL OR TRIM(stage) = '')"
                "  )"
                "  OR stage = 'Unknown'"
                ")"
            )

        rows = conn.execute(
            f"""
            SELECT id, seed, settings_code, error_type, stage, log
            FROM {RUNS_TABLE}
            WHERE {where}
            ORDER BY id
            """
        ).fetchall()

        updated = 0
        unmatched = 0
        for row in rows:
            result = classify_log(row["log"], compiled)
            if result is None:
                unmatched += 1
                if args.verbose:
                    emit(f"id={row['id']} seed={row['seed']}: no rule matched")
                continue

            error_type, stage, matched = result
            if args.verbose or args.dry_run:
                emit(
                    f"id={row['id']} seed={row['seed']}: "
                    f"error_type={error_type!r} stage={stage!r} "
                    f"(matched {matched!r})"
                )

            if not args.dry_run:
                conn.execute(
                    f"""
                    UPDATE {RUNS_TABLE}
                    SET error_type = ?, stage = ?
                    WHERE id = ?
                    """,
                    (error_type, stage, row["id"]),
                )
            updated += 1

        if not args.dry_run:
            conn.commit()

        mode = "DRY-RUN " if args.dry_run else ""
        emit(
            f"{mode}classify done at {datetime.now().isoformat(timespec='seconds')}: "
            f"scanned={len(rows)} updated={updated} unmatched={unmatched}"
        )
    finally:
        conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="TestSuiteAutomateClassify",
        description="Classify suite_runs failures from log text into stage/error_type.",
    )
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show matches without writing to the DB",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Reclassify all failures with a log, overriding any existing "
             "stage/error_type (default: only blanks + stage='Unknown')",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print each classification",
    )
    p.add_argument(
        "--keep-console",
        action="store_true",
        help="Do not re-launch under pythonw when stdout is not a TTY "
             "(default: auto-hide console for non-interactive runs on Windows)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    _relaunch_hidden_if_needed(argv_list)

    args = build_parser().parse_args(argv_list)

    log_fp = _setup_output(Path(args.db))
    streams: List[TextIO] = []
    if _interactive_console():
        streams.append(sys.stdout)  # type: ignore[arg-type]
    if log_fp is not None:
        streams.append(log_fp)
    tee = _Tee(streams) if streams else None

    def emit(msg: str) -> None:
        line = msg if msg.endswith("\n") else msg + "\n"
        if tee is not None:
            tee.write(line)
        elif log_fp is not None:
            try:
                log_fp.write(line)
                log_fp.flush()
            except Exception:
                pass

    try:
        return cmd_classify(args, emit)
    finally:
        if log_fp is not None:
            try:
                log_fp.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
