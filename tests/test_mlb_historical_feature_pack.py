from __future__ import annotations

import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence

import pytest

import courtvision.sports.mlb.data.historical_feature_pack as feature_builder
from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
    audit_historical_backtest_readiness,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    FEATURE_NAMES,
    HISTORICAL_FEATURE_PACK_FILENAME,
    INSUFFICIENT_HISTORICAL_LOOKBACK,
    MARKET_MISSING_SEGMENT,
    HistoricalFeaturePackBuildError,
    build_historical_feature_pack,
    load_historical_feature_pack,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    validate_mlb_hr_label_custody,
)
from courtvision.sports.mlb.data.historical_input_pack import (
    HISTORICAL_INPUT_PACK_MODE,
    HISTORICAL_INPUT_PACK_VERSION,
    PACK_SOURCE_FILES,
    preflight_historical_input_pack,
)
from courtvision.sports.mlb.training.hr_feature_allowlist import (
    validate_mlb_hr_feature_pack,
)
import scripts.mlb_build_hr_feature_pack as feature_cli


STATCAST_FIELDS = (
    "game_date",
    "game_pk",
    "player_name",
    "batter",
    "pitcher",
    "events",
    "description",
    "stand",
    "p_throws",
    "home_team",
    "away_team",
    "inning",
    "inning_topbot",
    "pitch_type",
    "launch_speed",
    "launch_angle",
    "hit_distance_sc",
    "bb_type",
    "estimated_ba_using_speedangle",
    "estimated_woba_using_speedangle",
    "woba_value",
    "barrel",
)
GAME_FIELDS = (
    "game_id",
    "game_date",
    "home_team",
    "away_team",
    "game_number",
    "venue_name",
    "home_score",
    "away_score",
    "game_status",
    "source_type",
)
EVENT_FIELDS = (
    "game_id",
    "game_date",
    "inning",
    "batting_team",
    "fielding_team",
    "batter_id",
    "batter_name",
    "pitcher_id",
    "pitcher_name",
    "event_type",
    "event_text",
    "is_home_run",
    "rbi",
    "source_type",
)
WEATHER_FIELDS = (
    "game_id",
    "game_date",
    "event_start_time",
    "venue_name",
    "latitude",
    "longitude",
    "temperature",
    "wind_speed",
    "wind_direction",
    "wind_out_to_field",
    "humidity",
    "precipitation",
    "roof_status",
    "source_name",
    "source_type",
    "collected_at",
    "as_of_date",
)
BALLPARK_FIELDS = (
    "venue_name",
    "team",
    "park_factor_hr",
    "handedness_factor_lhb",
    "handedness_factor_rhb",
    "altitude",
    "left_field_distance",
    "center_field_distance",
    "right_field_distance",
    "roof_type",
    "source_name",
    "source_type",
    "data_version",
    "collected_at",
    "as_of_date",
)
ODDS_FIELDS = (
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "sportsbook",
    "american_odds",
    "decimal_odds",
    "odds_collected_at",
    "event_start_time",
    "home_team",
    "away_team",
    "provider",
    "source_type",
    "market_label",
    "selection_name",
)


def _write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _csv_observations(path: Path) -> tuple[int, str | None, str | None]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    dates = sorted(row["game_date"] for row in rows if row.get("game_date"))
    return len(rows), dates[0] if dates else None, dates[-1] if dates else None


def _refresh_manifest(pack_dir: Path) -> None:
    manifest_path = pack_dir / "input_pack_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_start = manifest["dataset_date_range_start"]
    pack_end = manifest["dataset_date_range_end"]
    for entry in manifest["sources"]:
        source_path = pack_dir / PACK_SOURCE_FILES[entry["source_name"]]
        source_bytes = source_path.read_bytes()
        count, start, end = _csv_observations(source_path)
        entry["sha256"] = hashlib.sha256(source_bytes).hexdigest()
        entry["byte_size"] = len(source_bytes)
        entry["parsed_row_count"] = count
        entry["date_range_start"] = start or pack_start
        entry["date_range_end"] = end or pack_end
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _build_ready_pack(pack_dir: Path) -> Path:
    pack_dir.mkdir()
    statcast_rows: list[dict[str, object]] = []
    game_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    weather_rows: list[dict[str, object]] = []
    odds_rows: list[dict[str, object]] = []
    first_date = date(2024, 4, 1)

    for game_index in range(100):
        game_date = first_date + timedelta(days=game_index)
        date_text = game_date.isoformat()
        game_id = str(746000 + game_index)
        pitcher_id = str(700000 + game_index % 10)
        pitcher_name = f"Known Pitcher {game_index % 10}"
        start_time = f"{date_text}T23:00:00+00:00"
        game_rows.append(
            {
                "game_id": game_id,
                "game_date": date_text,
                "home_team": "NYY",
                "away_team": "MIA",
                "game_number": "1",
                "venue_name": "Yankee Stadium",
                "home_score": "5",
                "away_score": "2",
                "game_status": "completed",
                "source_type": "historical",
            }
        )
        weather_rows.append(
            {
                "game_id": game_id,
                "game_date": date_text,
                "event_start_time": start_time,
                "venue_name": "Yankee Stadium",
                "latitude": "40.8296",
                "longitude": "-73.9262",
                "temperature": "68.0",
                "wind_speed": "8.0",
                "wind_direction": "out to right",
                "wind_out_to_field": "right",
                "humidity": "52",
                "precipitation": "0.0",
                "roof_status": "open",
                "source_name": "verified_historical_weather_archive",
                "source_type": "historical",
                "collected_at": f"{date_text}T18:00:00+00:00",
                "as_of_date": date_text,
            }
        )
        for player_offset in range(10):
            player_number = (game_index * 10 + player_offset) % 100
            player_id = str(600000 + player_number)
            player_name = f"Real Batter {player_number}"
            hit_hr = player_offset == 0
            event_type = "home_run" if hit_hr else "field_out"
            statcast_rows.append(
                {
                    "game_date": date_text,
                    "game_pk": game_id,
                    "player_name": player_name,
                    "batter": player_id,
                    "pitcher": pitcher_id,
                    "events": event_type,
                    "description": "hit_into_play",
                    "stand": "L" if player_number % 2 else "R",
                    "p_throws": "R",
                    "home_team": "NYY",
                    "away_team": "MIA",
                    "inning": "1",
                    "inning_topbot": "Bot",
                    "pitch_type": "FF",
                    "launch_speed": "101.0" if hit_hr else "91.0",
                    "launch_angle": "28" if hit_hr else "12",
                    "hit_distance_sc": "405" if hit_hr else "220",
                    "bb_type": "fly_ball" if hit_hr else "line_drive",
                    "estimated_ba_using_speedangle": "0.700",
                    "estimated_woba_using_speedangle": "0.800",
                    "woba_value": "2.0" if hit_hr else "0.0",
                    "barrel": "1" if hit_hr else "0",
                }
            )
            event_rows.append(
                {
                    "game_id": game_id,
                    "game_date": date_text,
                    "inning": "1",
                    "batting_team": "NYY",
                    "fielding_team": "MIA",
                    "batter_id": player_id,
                    "batter_name": player_name,
                    "pitcher_id": pitcher_id,
                    "pitcher_name": pitcher_name,
                    "event_type": event_type,
                    "event_text": "Completed plate appearance",
                    "is_home_run": str(hit_hr).lower(),
                    "rbi": "1" if hit_hr else "0",
                    "source_type": "historical",
                }
            )
            odds_rows.append(
                {
                    "game_date": date_text,
                    "game_id": game_id,
                    "player_id": player_id,
                    "player_name": player_name,
                    "team": "NYY",
                    "opponent": "MIA",
                    "market_type": "home_run",
                    "sportsbook": "Historical Book A",
                    "american_odds": "+300",
                    "decimal_odds": "4.0",
                    "odds_collected_at": f"{date_text}T20:00:00+00:00",
                    "event_start_time": start_time,
                    "home_team": "NYY",
                    "away_team": "MIA",
                    "provider": "licensed_historical_export",
                    "source_type": "historical",
                    "market_label": "Player home run",
                    "selection_name": player_name,
                }
            )

    ballpark_rows = [
        {
            "venue_name": "Yankee Stadium",
            "team": "NYY",
            "park_factor_hr": "1.12",
            "handedness_factor_lhb": "1.17",
            "handedness_factor_rhb": "1.07",
            "altitude": "55",
            "left_field_distance": "318",
            "center_field_distance": "408",
            "right_field_distance": "314",
            "roof_type": "open",
            "source_name": "verified_park_factors",
            "source_type": "static",
            "data_version": "2024",
            "collected_at": "2024-03-01T12:00:00+00:00",
            "as_of_date": "2024-03-01",
        }
    ]
    _write_csv(pack_dir / "statcast.csv", STATCAST_FIELDS, statcast_rows)
    _write_csv(pack_dir / "retrosheet_games.csv", GAME_FIELDS, game_rows)
    _write_csv(pack_dir / "retrosheet_events.csv", EVENT_FIELDS, event_rows)
    _write_csv(pack_dir / "weather.csv", WEATHER_FIELDS, weather_rows)
    _write_csv(pack_dir / "ballpark_factors.csv", BALLPARK_FIELDS, ballpark_rows)
    _write_csv(pack_dir / "hr_odds_snapshot.csv", ODDS_FIELDS, odds_rows)

    last_date = (first_date + timedelta(days=99)).isoformat()
    providers = {
        "statcast": "baseball_savant_statcast",
        "retrosheet_games": "retrosheet_game_labels",
        "retrosheet_events": "retrosheet_event_labels",
        "weather": "verified_historical_weather_archive",
        "ballpark_factors": "verified_park_factors",
        "odds_snapshot": "licensed_historical_export",
    }
    manifest = {
        "manifest_version": HISTORICAL_INPUT_PACK_VERSION,
        "mode": HISTORICAL_INPUT_PACK_MODE,
        "created_at": "2026-06-28T16:00:00+00:00",
        "source_classification": "real",
        "dataset_date_range_start": first_date.isoformat(),
        "dataset_date_range_end": last_date,
        "approval_status": "not_approved",
        "eligible_for_betting": False,
        "kelly_eligible": False,
        "sources": [
            {
                "source_name": source_name,
                "provider_label": providers[source_name],
                "source_type": "local_file",
                "source_classification": "real",
                "path": filename,
                "sha256": "pending",
                "byte_size": 0,
                "parsed_row_count": 0,
                "created_at": "2026-06-28T16:00:00+00:00",
                "date_range_start": first_date.isoformat(),
                "date_range_end": last_date,
                "required_or_optional": "required",
                "loaded_successfully": True,
            }
            for source_name, filename in PACK_SOURCE_FILES.items()
        ],
    }
    (pack_dir / "input_pack_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    _refresh_manifest(pack_dir)
    return pack_dir


@pytest.fixture(scope="module")
def ready_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack = _build_ready_pack(tmp_path_factory.mktemp("mlb_ready") / "input_pack")
    preflight = preflight_historical_input_pack(pack)
    assert preflight.is_valid, preflight.errors
    readiness = audit_historical_backtest_readiness(pack)
    assert (
        readiness.verdict
        == HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    ), (readiness.blocking_reasons, readiness.research_review_items)
    return pack


def _copy_pack(source: Path, tmp_path: Path) -> Path:
    destination = tmp_path / "input_pack"
    shutil.copytree(source, destination)
    return destination


def _rewrite_csv(
    pack_dir: Path,
    source_name: str,
    transform: Callable[
        [list[str], list[dict[str, str]]],
        tuple[list[str], list[dict[str, str]]],
    ],
) -> None:
    path = pack_dir / PACK_SOURCE_FILES[source_name]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    fields, rows = transform(fields, rows)
    _write_csv(path, fields, rows)
    _refresh_manifest(pack_dir)


def _snapshot_tree(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    )


def test_valid_ready_pack_creates_feature_pack_via_cli(
    ready_pack: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "feature_staging"

    exit_code = feature_cli.main(
        [
            "--historical-input-pack",
            str(ready_pack),
            "--output-staging-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / HISTORICAL_FEATURE_PACK_FILENAME).is_file()
    assert set(output_dir.iterdir()) == {
        output_dir / HISTORICAL_FEATURE_PACK_FILENAME,
        output_dir / LABEL_CUSTODY_FILENAME,
    }
    output = capsys.readouterr().out
    assert "readiness_verdict: READY_FOR_RESEARCH_BACKTEST" in output
    assert "feature_firewall: valid" in output
    assert "model_training_enabled: false" in output
    payload = json.loads(
        (output_dir / HISTORICAL_FEATURE_PACK_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["feature_names"] == list(FEATURE_NAMES)
    assert payload["rows"]
    assert all("is_home_run" not in row for row in payload["rows"])
    assert all(
        "is_home_run" not in row["feature_values"] for row in payload["rows"]
    )
    assert payload["approval_status"] == "not_approved"
    binding = validate_mlb_hr_label_custody(
        feature_pack_path=output_dir / HISTORICAL_FEATURE_PACK_FILENAME,
        label_custody_path=output_dir / LABEL_CUSTODY_FILENAME,
    )
    assert binding.row_count == len(payload["rows"])


def test_missing_odds_row_is_retained_with_real_population_coverage(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)
    selected: dict[str, str] = {}

    def remove_one_market(fields: list[str], rows: list[dict[str, str]]):
        removed = rows[-1]
        selected.update(
            game_id=removed["game_id"],
            game_date=removed["game_date"],
            player_id=removed["player_id"],
        )
        return fields, rows[:-1]

    _rewrite_csv(pack, "odds_snapshot", remove_one_market)
    readiness = audit_historical_backtest_readiness(pack)
    assert readiness.odds_coverage_rate < 1.0
    assert (
        readiness.verdict
        == HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )

    result = build_historical_feature_pack(
        historical_input_pack=pack,
        output_staging_dir=tmp_path / "feature_staging",
    )
    payload = json.loads(result.feature_pack_path.read_text(encoding="utf-8"))
    matches = [
        row
        for row in payload["rows"]
        if all(row[field] == value for field, value in selected.items())
    ]

    assert len(matches) == 1
    row = matches[0]
    values = row["feature_values"]
    assert row["odds_collected_at"] is None
    assert row["feature_cutoff_at"] < row["event_start_time"]
    assert values["hr_market_available"] is False
    assert values["odds_is_fresh_for_pregame"] is False
    assert all(
        values[field] is None
        for field in (
            "sportsbook",
            "odds_provider",
            "american_odds",
            "decimal_odds",
            "implied_probability",
            "odds_collected_at",
            "odds_as_of",
        )
    )
    assert row["segments"]["market_coverage"] == MARKET_MISSING_SEGMENT
    population = payload["population"]
    assert population["market_missing_batter_game_count"] == 1
    assert population["odds_coverage_rate"] < 1.0
    assert (
        population["market_covered_batter_game_count"]
        + population["market_missing_batter_game_count"]
        == population["eligible_batter_game_count"]
    )
    assert validate_mlb_hr_feature_pack(result.feature_pack).is_valid


def test_insufficient_historical_lookback_exclusion_is_counted(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    result = build_historical_feature_pack(
        historical_input_pack=ready_pack,
        output_staging_dir=tmp_path / "feature_staging",
    )
    population = result.population_accounting
    exclusion_counts = population["exclusion_counts"]

    assert isinstance(exclusion_counts, Mapping)
    assert exclusion_counts[INSUFFICIENT_HISTORICAL_LOOKBACK] > 0
    assert population["excluded_batter_game_count"] == exclusion_counts[
        INSUFFICIENT_HISTORICAL_LOOKBACK
    ]
    assert (
        population["eligible_batter_game_count"]
        + population["excluded_batter_game_count"]
        == population["target_population_count"]
    )


def test_distinct_sportsbooks_fan_out_without_inflating_population(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)
    selected: dict[str, str] = {}

    def add_second_book(fields: list[str], rows: list[dict[str, str]]):
        second = dict(rows[-1])
        second["sportsbook"] = "Historical Book B"
        selected.update(
            game_id=second["game_id"],
            game_date=second["game_date"],
            player_id=second["player_id"],
        )
        return fields, [*rows, second]

    _rewrite_csv(pack, "odds_snapshot", add_second_book)
    result = build_historical_feature_pack(
        historical_input_pack=pack,
        output_staging_dir=tmp_path / "feature_staging",
    )
    payload = json.loads(result.feature_pack_path.read_text(encoding="utf-8"))
    matches = [
        row
        for row in payload["rows"]
        if all(row[field] == value for field, value in selected.items())
    ]

    assert len(matches) == 2
    assert len({row["row_id"] for row in matches}) == 2
    assert {
        row["feature_values"]["sportsbook"] for row in matches
    } == {"Historical Book A", "Historical Book B"}
    assert result.population_accounting["feature_row_count"] == (
        result.population_accounting["eligible_batter_game_count"] + 1
    )
    assert result.population_accounting["market_missing_batter_game_count"] == 0


def test_duplicate_same_sportsbook_snapshot_fails_preflight(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)

    def duplicate_market(fields: list[str], rows: list[dict[str, str]]):
        return fields, [*rows, dict(rows[-1])]

    _rewrite_csv(pack, "odds_snapshot", duplicate_market)
    output_dir = tmp_path / "feature_staging"

    with pytest.raises(
        HistoricalFeaturePackBuildError, match="duplicate odds snapshot identity"
    ):
        build_historical_feature_pack(
            historical_input_pack=pack,
            output_staging_dir=output_dir,
        )

    assert not output_dir.exists()


def test_successful_build_mutates_no_operational_folder(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    operational = [
        tmp_path / name
        for name in ("outputs", "test_outputs", "data", "dashboard", "runtime")
    ]
    for folder in operational:
        folder.mkdir()
        (folder / "sentinel.txt").write_text(
            f"preserve {folder.name}", encoding="utf-8"
        )
    before = {folder: _snapshot_tree(folder) for folder in operational}

    build_historical_feature_pack(
        historical_input_pack=ready_pack,
        output_staging_dir=tmp_path / "feature_staging",
    )

    assert {folder: _snapshot_tree(folder) for folder in operational} == before


def test_not_ready_pack_is_rejected_before_output(
    ready_pack: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)
    output_dir = tmp_path / "feature_staging"
    not_ready = SimpleNamespace(
        verdict=HistoricalBacktestReadinessVerdict.NOT_READY.value,
        blocking_reasons=("research gate intentionally not ready",),
        research_review_items=(),
        possible_leakage_columns=(),
    )
    monkeypatch.setattr(
        feature_builder, "audit_historical_backtest_readiness", lambda _path: not_ready
    )

    with pytest.raises(HistoricalFeaturePackBuildError, match="got NOT_READY"):
        build_historical_feature_pack(
            historical_input_pack=pack,
            output_staging_dir=output_dir,
        )

    assert not output_dir.exists()


def test_leakage_column_is_rejected(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)

    def add_leakage(fields: list[str], rows: list[dict[str, str]]):
        fields.append("future_outcome")
        for row in rows:
            row["future_outcome"] = "known"
        return fields, rows

    _rewrite_csv(pack, "statcast", add_leakage)
    report = audit_historical_backtest_readiness(pack)
    assert report.verdict == HistoricalBacktestReadinessVerdict.NOT_READY.value
    assert report.possible_leakage_columns == ("statcast.future_outcome",)

    with pytest.raises(
        HistoricalFeaturePackBuildError, match="possible leakage columns"
    ):
        build_historical_feature_pack(
            historical_input_pack=pack,
            output_staging_dir=tmp_path / "feature_staging",
        )


def test_late_feature_timestamp_is_rejected(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    pack = _copy_pack(ready_pack, tmp_path)

    def make_weather_late(fields: list[str], rows: list[dict[str, str]]):
        for row in rows:
            row["collected_at"] = f"{row['game_date']}T20:01:00+00:00"
        return fields, rows

    _rewrite_csv(pack, "weather", make_weather_late)
    assert (
        audit_historical_backtest_readiness(pack).verdict
        == HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
    )

    with pytest.raises(
        HistoricalFeaturePackBuildError, match="timestamped after the odds snapshot"
    ):
        build_historical_feature_pack(
            historical_input_pack=pack,
            output_staging_dir=tmp_path / "feature_staging",
        )


def test_rolling_feature_using_same_day_data_is_rejected(
    ready_pack: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        feature_builder,
        "_is_strictly_before",
        lambda source_date, target_date: source_date <= target_date,
    )

    with pytest.raises(
        HistoricalFeaturePackBuildError, match="same-day or future outcomes"
    ):
        build_historical_feature_pack(
            historical_input_pack=ready_pack,
            output_staging_dir=tmp_path / "feature_staging",
        )


def test_builder_cannot_write_to_operational_folders(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    for folder_name in ("manual-data", "outputs", "history", "runtime", "cache"):
        restricted = tmp_path / folder_name
        restricted.mkdir()
        sentinel = restricted / "sentinel.txt"
        sentinel.write_text("preserve", encoding="utf-8")

        with pytest.raises(
            HistoricalFeaturePackBuildError, match="cannot be inside"
        ):
            build_historical_feature_pack(
                historical_input_pack=ready_pack,
                output_staging_dir=restricted,
            )

        assert sentinel.read_text(encoding="utf-8") == "preserve"
        assert list(restricted.iterdir()) == [sentinel]


def test_generated_feature_pack_passes_feature_firewall(
    ready_pack: Path,
    tmp_path: Path,
) -> None:
    result = build_historical_feature_pack(
        historical_input_pack=ready_pack,
        output_staging_dir=tmp_path / "feature_staging",
    )

    loaded = load_historical_feature_pack(result.feature_pack_path)
    firewall = validate_mlb_hr_feature_pack(loaded)

    assert firewall.is_valid, firewall.errors
    assert loaded == result.feature_pack
    assert all(
        item.source_latest_game_date < row.game_date
        for row in loaded.rows
        for item in row.feature_availability
        if item.feature_name in feature_builder.ROLLING_FEATURE_NAMES
    )
    assert all(
        item.available_at <= row.odds_collected_at < row.event_start_time
        for row in loaded.rows
        for item in row.feature_availability
    )
