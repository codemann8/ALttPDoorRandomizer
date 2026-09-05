# TestSuiteAutomate — automation DB triage

Agent-oriented notes for investigating generation failures produced by the scheduled seed harness. Prefer **read-only** SQL against the SQLite DB; do not invent a second analytics stack unless needed.

## Related files

| Path | Role |
|---|---|
| `test/suite/automate/TestSuiteAutomate.pyw` | Generate seeds (default); `init` / `migrate` for schema |
| `test/suite/automate/TestSuiteAutomateClassify.pyw` | Fill `stage` / `error_type` from `log` phrases |
| `test/suite/automate/TestSuiteAutomateExplore.pyw` | Local explorer UI |
| `test/suite/automate/TestSuiteAutomateDB.py` | Schema, path defaults, settings column list |
| `test/suite/automate/automate_customizer.yaml` | Customizer wrapper (`meta.suite` labels the run segment) |
| `test/suite/automate/automate_mystery.yaml` | Weighted option coverage for the `mystery` suite |
| `test/suite/automate/explore/` | Explorer HTTP server, queries, static UI |

### Local paths (not hardcoded here)

Machine-specific defaults live in `test/suite/automate/TestSuiteAutomateDB.py`:

| Constant | Meaning |
|---|---|
| `DEFAULT_DB_PATH` | SQLite DB file (override harness/classify with `--db`) |
| `DEFAULT_AUTOMATE_DIR` | Output root for daily folders / temp fixed customizers |
| `DEFAULT_CUSTOMIZER` | Default customizer yaml (override with `--customizer`) |
| `AUTOMATE_DIR` / `REPO_ROOT` | Resolved from that module’s location on disk |

Under `DEFAULT_AUTOMATE_DIR` (or `--outputpath`), the harness typically writes:

```text
<outputpath>/YYMMDD/
  _harness_*.log
  OR_<seed>_Spoiler.txt    # when --spoiler is not none
```

Classify appends `_classify.log` next to the DB file (same directory as `DEFAULT_DB_PATH` / `--db`).

Use `suite_runs` + `suite_settings` only.

---

## Schema overview

```text
suite_settings  (1 row per unique settings_code + settings_ver)
       ^
       |  FK (settings_code, settings_ver)
       |
suite_runs      (1 row per generation attempt)
```

### `suite_runs` — one attempt

| Column | Meaning |
|---|---|
| `id` | Surrogate PK |
| `seed` | RNG seed used for that attempt |
| `mystery_name` | Optional label (often null) |
| `suite` | Weights profile label from customizer `meta.suite` (current default: `mystery`). Explorer isolates all charts to one suite |
| `settings_code` | Compact base64 settings token (`Settings.make_code`) — join key + repro aid |
| `settings_ver` | Codec version byte from `BaseClasses.settings_version` — **always join with code** |
| `app_version` | DR/OR version string at generation time |
| `timestamp` | Unix seconds (UTC-ish wall clock of the attempt) |
| `success` | `1` = generation returned 0; `0` = failure / timeout / harness error |
| `duration_ms` | Wall time for freeze + generate (includes timeout wait) |
| `stage` | **Classifier-filled** coarse subsystem (e.g. `Dungeon`, `EntrancePlacement`). Blank until classify runs |
| `error_type` | **Classifier-filled** short failure bucket (e.g. `Keylock`, `Timeout`). Blank until classify |
| `log` | Captured stdout/stderr (and harness timeout text) on failure; usually null on success unless `--store-success-log` |

**Harness does not set `stage` / `error_type`.** They stay `NULL` until `TestSuiteAutomateClassify.pyw` matches phrases in `log`. Rules live in that script (`RULES`); first match wins; multi-phrase rules require **all** phrases (AND).

Default classify eligibility:

- failures with non-empty `log`, and
- both tags blank, **or** `stage = 'Unknown'` (provisional catch-alls can be refined later).

`--force` reclassifies every failure with a log.

Common provisional / catch-all values:

| `stage` / `error_type` | Notes |
|---|---|
| `NULL` | Not classified yet — run classify or inspect `log` |
| `Unknown` | Catch-all rule (or legacy); safe to re-run classify without `--force` |
| `Generate` + `Timeout` | Log contains harness text `Generation timed out after` (default cap 30 min) |
| `Generate` + `MemoryLimit` | Log contains harness text `Generation exceeded memory limit` (default cap 4096MB) |

### Timeout / memory-limit logs

On timeout or memory cap the harness kills the process tree and stores a log starting roughly like:

```text
Generation timed out after 1800s (seed=...). Process tree killed.
Settings were frozen before generation; see suite_settings via this run's settings_code.
A partial spoiler may exist under <day_dir>.
```

```text
Generation exceeded memory limit (4096MB, peak~....MB, seed=...). Process tree killed.
```

Settings are frozen **before** generation, so timeout/OOM rows still have a full `suite_settings` join. A partial spoiler may exist under the daily folder even though generation did not finish.

**Task Scheduler / Parallel runs:** Overlapping harness instances (e.g. every 5 minutes while some seeds take longer) are supported. Each generation child is placed in a Windows Job Object with kill-on-close, so:

- the harness `--timeout` (default 30 min) terminates the generator reliably
- if the harness process itself exits or is stopped, the child cannot survive as an orphan

Keep the task **ExecutionTimeLimit** comfortably above `--timeout` (e.g. 1h when timeout is 30 min) so Task Scheduler does not stop the harness before it can record the timeout row. Optional `--memory-limit-mb` (default 4096) is a second backstop for algorithmic memory runaways.

### `suite_settings` — decoded settings dimension

PK: `(settings_code, settings_ver)`. Booleans are `INTEGER` `0`/`1` (use `AVG(col)` for rates).

High-value columns for slicing:

| Column | Notes |
|---|---|
| `door_shuffle` | `vanilla` / `basic` / `partitioned` / `crossed` / … |
| `intensity` | Door intensity (`1`–`3` or `random`) |
| `shuffle` | Entrance shuffle (ER); `vanilla` = off |
| `mode` | `open` / `standard` / `inverted` |
| `logic` | e.g. `noglitches`, `owglitches` |
| `goal` | e.g. `ganon`, `dungeons`, `triforcehunt` |
| `ow_layout` / `ow_crossed` / `ow_mixed` / `ow_parallel` | Overworld shuffle surface |
| `keyshuffle` / `bigkeyshuffle` / `mapshuffle` / `compassshuffle` | Dungeon item shuffle |
| `algorithm` | Fill algorithm (global, not per-player) |
| `key_logic_algorithm` | Key logic mode |
| `door_type_mode` / `trap_door_mode` / `decoupledoors` | Door-generation knobs |

Full column list: `SETTINGS_COLUMN_SPECS` in `test/suite/automate/TestSuiteAutomateDB.py`.

---

## Opening the DB

Resolve the path from `DEFAULT_DB_PATH` in `test/suite/automate/TestSuiteAutomateDB.py` (or whatever `--db` the harness uses).

```bash
sqlite3 "<DEFAULT_DB_PATH>"
```

```python
import sqlite3
from test.suite.automate.TestSuiteAutomateDB import DEFAULT_DB_PATH

conn = sqlite3.connect(str(DEFAULT_DB_PATH))
conn.row_factory = sqlite3.Row
```

Timestamps are Unix seconds:

```sql
datetime(timestamp, 'unixepoch', 'localtime')
```

---

## Triage queries

### 1. Overall health (last 7 days)

```sql
SELECT
  COUNT(*) AS attempts,
  SUM(success) AS ok,
  COUNT(*) - SUM(success) AS fail,
  ROUND(100.0 * SUM(success) / COUNT(*), 2) AS success_pct
FROM suite_runs
WHERE timestamp >= strftime('%s', 'now', '-7 days');
```

### 2. Failure mix by stage / error_type

```sql
SELECT
  COALESCE(stage, '(unclassified)') AS stage,
  COALESCE(error_type, '(unclassified)') AS error_type,
  COUNT(*) AS n,
  ROUND(AVG(duration_ms) / 1000.0, 1) AS avg_sec
FROM suite_runs
WHERE success = 0
  AND timestamp >= strftime('%s', 'now', '-7 days')
GROUP BY 1, 2
ORDER BY n DESC;
```

### 3. Unclassified failures (need classify or new rules)

```sql
SELECT id, seed, settings_code, duration_ms,
       datetime(timestamp, 'unixepoch', 'localtime') AS when_local,
       substr(log, 1, 200) AS log_head
FROM suite_runs
WHERE success = 0
  AND log IS NOT NULL AND TRIM(log) != ''
  AND (
    (error_type IS NULL OR TRIM(error_type) = '')
    AND (stage IS NULL OR TRIM(stage) = '')
  )
ORDER BY id DESC
LIMIT 50;
```

### 4. Timeouts

```sql
SELECT id, seed, settings_code, duration_ms,
       datetime(timestamp, 'unixepoch', 'localtime') AS when_local
FROM suite_runs
WHERE success = 0
  AND (
    error_type = 'Timeout'
    OR log LIKE 'Generation timed out after%'
  )
ORDER BY id DESC
LIMIT 50;
```

### 5. Settings correlation for one error bucket

Replace `'Keylock'` (or any `error_type`) as needed:

```sql
SELECT
  s.door_shuffle,
  s.intensity,
  s.shuffle AS er_shuffle,
  s.mode,
  s.keyshuffle,
  COUNT(*) AS n
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
WHERE r.success = 0
  AND r.error_type = 'Keylock'
GROUP BY 1, 2, 3, 4, 5
ORDER BY n DESC
LIMIT 30;
```

### 6. Door / intensity failure rates (min sample)

```sql
SELECT
  s.door_shuffle,
  s.intensity,
  COUNT(*) AS attempts,
  SUM(r.success) AS ok,
  ROUND(100.0 * SUM(r.success) / COUNT(*), 2) AS success_pct
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
GROUP BY 1, 2
HAVING COUNT(*) >= 20
ORDER BY success_pct ASC, attempts DESC;
```

### 7. ER × OWR slice (common stress surface)

```sql
SELECT
  s.shuffle AS er,
  s.ow_layout,
  s.ow_crossed,
  s.ow_mixed,
  COUNT(*) AS attempts,
  ROUND(100.0 * SUM(r.success) / COUNT(*), 2) AS success_pct
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
GROUP BY 1, 2, 3, 4
HAVING COUNT(*) >= 15
ORDER BY success_pct ASC, attempts DESC;
```

### 8. Recent failures with settings snapshot

```sql
SELECT
  r.id,
  r.seed,
  r.error_type,
  r.stage,
  r.duration_ms,
  s.mode,
  s.door_shuffle,
  s.intensity,
  s.shuffle AS er,
  s.goal,
  s.algorithm,
  r.settings_code,
  datetime(r.timestamp, 'unixepoch', 'localtime') AS when_local
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
WHERE r.success = 0
ORDER BY r.id DESC
LIMIT 40;
```

### 9. Full log + settings for one seed or run id

```sql
-- by seed (may be multiple attempts; substitute a real seed)
SELECT r.*, s.*
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
WHERE r.seed = <SEED>;

-- by run id
SELECT r.*, s.*
FROM suite_runs r
JOIN suite_settings s
  ON s.settings_code = r.settings_code
 AND s.settings_ver  = r.settings_ver
WHERE r.id = <RUN_ID>;
```

### 10. Repeat offenders (same settings_code failing more than once)

```sql
SELECT
  r.settings_code,
  r.settings_ver,
  COUNT(*) AS fail_n,
  GROUP_CONCAT(DISTINCT r.error_type) AS error_types,
  MIN(r.seed) AS example_seed
FROM suite_runs r
WHERE r.success = 0
GROUP BY 1, 2
HAVING COUNT(*) >= 2
ORDER BY fail_n DESC
LIMIT 30;
```

---

## Suggested triage flow

1. **Query 2** — what buckets dominate lately?
2. For a hot `error_type`, **query 5** — which settings co-occur?
3. **Query 8 / 9** — pull a concrete seed, read `log`, open daily spoiler if present.
4. If many blanks / `Unknown`, run classify (or add a rule in `TestSuiteAutomateClassify.pyw` `RULES`, then classify again).
5. Repro: use `seed` + settings from `suite_settings` / `settings_code` with Customizer or CLI as appropriate.

### Classify commands

```bat
python test/suite/automate/TestSuiteAutomateClassify.pyw --dry-run -v
python test/suite/automate/TestSuiteAutomateClassify.pyw
python test/suite/automate/TestSuiteAutomateClassify.pyw --force
```

Silent scheduled classify: use `pythonw.exe` on this script (see its docstring). Start in the **repo root**.

### Adding a settings column later

1. Encode in `BaseClasses.Settings` (`make_code` / `adjust_args_from_code`).
2. Append to `SETTINGS_COLUMN_SPECS` in `TestSuiteAutomateDB.py`.
3. `python test/suite/automate/TestSuiteAutomate.pyw migrate`
4. Optionally weight in `automate_mystery.yaml`.

---

## Notes for agents

- Join **`settings_code` and `settings_ver` together**; never join on code alone across codec versions.
- Treat `success = 0` + empty `log` as incomplete capture (rare harness/path issues).
- `duration_ms` near the timeout (e.g. ~1800000) strongly suggests a hang, even before classify tags `Timeout`.
- Do not drop or rewrite `suite_settings` rows casually — many runs share one settings dimension row (`INSERT OR IGNORE` on write).
- Prefer aggregated SQL for “what’s broken?”; use single-row log dumps for “why this seed?”.
