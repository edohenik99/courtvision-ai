"""Inspect parsed local MLB fixtures for historical research."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Final


PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
FIXTURE_DIR: Final = PROJECT_ROOT / "tests" / "fixtures" / "mlb"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.ballpark_factors import (
    ingest_local_ballpark_factors_csv,
)
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    ingest_local_retrosheet_csvs,
)
from courtvision.sports.mlb.data.statcast_ingestion import ingest_local_statcast_csv
from courtvision.sports.mlb.data.weather_ingestion import ingest_local_weather_csv
from courtvision.sports.mlb.training.hr_dataset_builder import (
    build_fixture_hr_batter_game_dataset,
)
from courtvision.sports.mlb.training.hr_leakage_audit import (
    audit_hr_batter_game_rows,
)


INSPECTION_FIELDS: Final = (
    "player_name",
    "player_id",
    "game_id",
    "game_date",
    "team",
    "opponent",
    "venue_name",
    "hit_hr_today",
    "home_run_count",
    "weather_temperature",
    "weather_wind_speed",
    "park_factor_hr",
    "missing_required_fields",
    "warnings",
    "eligible_for_training",
    "approval_status",
)


def _display_value(value: object) -> object:
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else value


def main() -> int:
    """Parse the repository's MLB fixtures and print an in-memory summary."""

    statcast = ingest_local_statcast_csv(FIXTURE_DIR / "statcast_sample.csv")
    retrosheet = ingest_local_retrosheet_csvs(
        games_csv=FIXTURE_DIR / "retrosheet_games_sample.csv",
        events_csv=FIXTURE_DIR / "retrosheet_events_sample.csv",
    )
    weather = ingest_local_weather_csv(FIXTURE_DIR / "weather_sample.csv")
    ballpark = ingest_local_ballpark_factors_csv(
        FIXTURE_DIR / "ballpark_factors_sample.csv"
    )
    dataset = build_fixture_hr_batter_game_dataset(FIXTURE_DIR)
    audit = audit_hr_batter_game_rows(dataset.rows)

    print("MLB fixture statistics inspection (historical research only)")
    print(f"Statcast row count: {len(statcast.rows)}")
    print(f"Retrosheet game count: {len(retrosheet.games)}")
    print(f"Retrosheet event count: {len(retrosheet.events)}")
    print(f"Weather row count: {len(weather.rows)}")
    print(f"Ballpark row count: {len(ballpark.rows)}")
    print(f"HR batter-game dataset row count: {dataset.row_count}")
    print(
        "Leakage audit summary: "
        f"rows={audit.row_count}, errors={audit.error_count}, "
        f"warnings={audit.warning_count}, passed={str(audit.passed).lower()}"
    )
    print("First 5 batter-game rows:")
    for row in dataset.rows[:5]:
        displayed = {
            field_name: _display_value(getattr(row, field_name))
            for field_name in INSPECTION_FIELDS
        }
        print(json.dumps(displayed, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
