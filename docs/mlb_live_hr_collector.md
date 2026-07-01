# MLB Live Home Run Odds Collector

`tools/theoddsapi_live_hr_collector.py` is a guarded research collector for
current MLB 1+ home-run prices from The Odds API. It is not part of the
bankroll-facing runtime and must not be used to change selection, sizing,
grading, or ROI logic.

## Free-plan collection strategy

The collector requests current MLB events and then requests the
`batter_home_runs_alternate` market for a limited number of eligible events.
It retains only `Over 0.5` outcomes, which represent a player to hit one or
more home runs. The default run is capped at 12 events and skips games less
than 30 minutes from their scheduled start.

Run it at most once per UTC day. The purpose is to build a small daily
research archive while conserving free-plan request credits. Do not use
historical or other paid API endpoints, and do not add extra collection runs
merely to increase sample size.

## Output files

The default output directory is
`data/theoddsapi/live_hr_snapshots/`. A successful collection can write:

- `live_hr_props_YYYYMMDD_HHMMSSZ.csv`: an immutable timestamped snapshot.
- `live_hr_props_master.csv`: the accumulated, deduplicated research dataset.
- `run_log.csv`: the UTC run date, status, row counts, request-credit metadata,
  and output paths.

These runtime files, backups, and JSON diagnostics under `data/theoddsapi/`
are local artifacts and are ignored by Git. API keys belong only in `.env`,
which is also ignored.

## Daily run guard

Before making a request, the collector checks `run_log.csv` for a successful
run on the current UTC date. If one exists, it exits without calling the API.
`--force` bypasses this protection and can consume additional credits, so it
should not be used in routine collection.

The normal live command is intentionally not documented here as an everyday
development command. Tests and repository validation must mock network access
and must not depend on a real API key.

## Dedupe-only maintenance

To clean accidental same-day duplicates without calling The Odds API:

```powershell
python .\tools\theoddsapi_live_hr_collector.py --dedupe-only
```

This keeps the latest row for each UTC snapshot date, event, bookmaker,
market, player, side, and point combination. It operates on the configured
master CSV only.

## Future grading plan

Live-result grading should remain separate from collection. After explicit
approval for grading changes, reusable MLB grading logic should live under
`courtvision/sports/mlb/`, with a thin
`scripts/grade_live_hr_results.py` command and fixture-driven tests. The
grader should consume immutable snapshots, write a separate idempotent graded
artifact, and avoid changing existing bankroll, ROI, or cross-sport grading
history until that integration is separately reviewed.
