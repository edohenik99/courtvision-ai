# CourtVision Phase 6B — Manual Historical MLB CSV Collection Guide

Status: Documentation-only phase.

Required local CSV files:
- statcast.csv
- retrosheet_games.csv
- retrosheet_events.csv
- weather.csv
- ballpark_factors.csv
- hr_odds_snapshot.csv

Recommended folder:
data/manual/mlb/hr/YYYY/

Use `--historical-dry-run` only after the local files actually exist.

Output pack:
- dataset.csv
- metadata.json
- audit.json
- source_manifest.json
- build_summary.txt
- readiness.json
- readiness_summary.txt

Safety:
Odds are market reference only. Do not use outputs for betting recommendations, staking, Kelly sizing, bankroll decisions, production picks, public dashboards, or automated execution.

Next step: Phase 6C — run one small real-data dry run, such as one team or one week.
