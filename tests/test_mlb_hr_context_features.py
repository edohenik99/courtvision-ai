from __future__ import annotations

import ast
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb.training import hr_context_features as context
from courtvision.sports.mlb.data.crosswalk_validation import REQUIRED_CROSSWALK_COLUMNS


AS_OF = "2026-06-05T18:00:00Z"
OPERATING_DATE = "2026-06-05"
GIT_COMMIT = "a" * 40
TARGET_GAME = "765432"
EARLY_DH_GAME = "765431"
BATTER_ID = "600001"
PITCHER_ID = "600009"
OTHER_BATTER_ID = "600002"

CANDIDATE_COLUMNS = (
    "event_id",
    "operating_date",
    "commence_time_utc",
    "home_team",
    "away_team",
    "venue_id",
    "venue_name",
    "team",
    "opponent",
    "player_id",
    "player_name",
    "normalized_player_name",
    "batter_hand",
    "identity_status",
    "identity_mapping_version",
    "candidate_published_or_available_at_utc",
    "candidate_captured_at_utc",
    "candidate_universe_id",
    "candidate_universe_version",
    "candidate_universe_generator",
    "candidate_universe_origin",
    "candidate_universe_policy",
    "candidate_universe_source_digest",
    "candidate_universe_configuration_digest",
    "candidate_universe_cutoff_utc",
)
CROSSWALK_COLUMNS = tuple(sorted(REQUIRED_CROSSWALK_COLUMNS)) + (
    "mlbam_pitcher_id",
    "pitcher_name",
    "pitcher_team",
    "identity_mapping_version",
)
STATCAST_COLUMNS = (
    "game_id",
    "game_date",
    "game_completed_at_utc",
    "completion_evidence_type",
    "completion_witnessed_at_utc",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "plate_appearance_id",
    "pitch_number",
    "batter_id",
    "pitcher_id",
    "batter_hand",
    "pitcher_hand",
    "home_team",
    "away_team",
    "event_type",
    "is_home_run",
    "pitch_type",
    "release_speed",
    "launch_speed",
    "launch_angle",
    "is_barrel",
    "estimated_woba",
    "estimated_slg",
    "batted_ball_type",
    "is_pull",
    "batter_team",
    "pitcher_team",
)


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def _rewrite(path: Path, mutate: object) -> None:
    columns, rows = _read_csv(path)
    assert callable(mutate)
    _write_csv(path, columns, [mutate(dict(row)) for row in rows])


def _append_rows(path: Path, additions: list[dict[str, object]]) -> None:
    columns, rows = _read_csv(path)
    _write_csv(path, columns, [*rows, *additions])


def _stat_row(
    *,
    game_id: str,
    game_date: str,
    completed: str,
    observed: str,
    pa_id: str,
    completion_evidence_type: str = "",
    completion_witnessed_at: str = "",
    batter_id: str,
    pitcher_id: str,
    event_type: str,
    is_home_run: bool,
    pitch_number: int = 1,
    batter_hand: str = "R",
    pitcher_hand: str = "R",
    pitch_type: str = "FF",
    release_speed: object = 95.0,
    launch_speed: object = "",
    launch_angle: object = "",
    is_barrel: object = "",
    estimated_woba: object = "",
    estimated_slg: object = "",
    batted_ball_type: str = "",
    is_pull: object = "",
    home_team: str = "TOR",
    away_team: str = "BOS",
    batter_team: str = "TOR",
    pitcher_team: str = "BOS",
) -> dict[str, object]:
    return {
        "game_id": game_id,
        "game_date": game_date,
        "game_completed_at_utc": completed,
        "completion_evidence_type": completion_evidence_type,
        "completion_witnessed_at_utc": completion_witnessed_at,
        "provider_published_at_utc": observed,
        "first_observed_at_utc": observed,
        "captured_at_utc": observed,
        "plate_appearance_id": pa_id,
        "pitch_number": pitch_number,
        "batter_id": batter_id,
        "pitcher_id": pitcher_id,
        "batter_hand": batter_hand,
        "pitcher_hand": pitcher_hand,
        "home_team": home_team,
        "away_team": away_team,
        "event_type": event_type,
        "is_home_run": is_home_run,
        "pitch_type": pitch_type,
        "release_speed": release_speed,
        "launch_speed": launch_speed,
        "launch_angle": launch_angle,
        "is_barrel": is_barrel,
        "estimated_woba": estimated_woba,
        "estimated_slg": estimated_slg,
        "batted_ball_type": batted_ball_type,
        "is_pull": is_pull,
        "batter_team": batter_team,
        "pitcher_team": pitcher_team,
    }


def _make_source_pack(root: Path) -> Path:
    source = root / "source"
    _write_csv(
        source / context.SOURCE_FILES["candidates"],
        CANDIDATE_COLUMNS,
        [
            {
                "event_id": TARGET_GAME,
                "operating_date": OPERATING_DATE,
                "commence_time_utc": "2026-06-05T23:00:00Z",
                "home_team": "TOR",
                "away_team": "BOS",
                "venue_id": "venue-1",
                "venue_name": "Rogers Centre",
                "team": "TOR",
                "opponent": "BOS",
                "player_id": BATTER_ID,
                "player_name": "Jose Ramirez Jr.",
                "normalized_player_name": "jose ramirez",
                "batter_hand": "R",
                "identity_status": "verified_mlbam",
                "identity_mapping_version": "mlb-id-map-v2",
                "candidate_published_or_available_at_utc": "2026-06-05T16:00:00Z",
                "candidate_captured_at_utc": "2026-06-05T17:00:00Z",
                "candidate_universe_id": "neutral-2026-06-05-1800z",
                "candidate_universe_version": "neutral-universe-v1",
                "candidate_universe_generator": "schedule-roster-enumerator-v1",
                "candidate_universe_origin": "neutral_market_independent",
                "candidate_universe_policy": "all eligible hitters before market attachment",
                "candidate_universe_source_digest": "1" * 64,
                "candidate_universe_configuration_digest": "2" * 64,
                "candidate_universe_cutoff_utc": AS_OF,
            }
        ],
    )
    _write_csv(
        source / context.SOURCE_FILES["identity_crosswalk"],
        CROSSWALK_COLUMNS,
        [
            {
                "game_date": OPERATING_DATE,
                "retrosheet_game_id": "TOR202606050",
                "mlbam_game_id": TARGET_GAME,
                "game_number": "1",
                "retrosheet_batter_id": "ramij001",
                "mlbam_batter_id": BATTER_ID,
                "batter_name": "Jose Ramirez Jr.",
                "retrosheet_home_team_id": "TOR",
                "home_team": "TOR",
                "retrosheet_away_team_id": "BOS",
                "away_team": "BOS",
                "retrosheet_batting_team_id": "TOR",
                "batting_team": "TOR",
                "retrosheet_fielding_team_id": "BOS",
                "fielding_team": "BOS",
                "player_mapping_source": "mlbam_registry",
                "game_mapping_source": "mlbam_schedule",
                "team_mapping_source": "canonical_team_table",
                "verified_at": "2026-06-05T15:00:00Z",
                "mlbam_pitcher_id": PITCHER_ID,
                "pitcher_name": "Chris Sale",
                "pitcher_team": "BOS",
                "identity_mapping_version": "mlb-id-map-v2",
            }
        ],
    )
    statcast_rows = [
        _stat_row(
            game_id="765401",
            game_date="2026-05-01",
            completed="2026-05-01T23:00:00Z",
            observed="2026-05-01T23:05:00Z",
            pa_id="g1-pa1",
            batter_id=BATTER_ID,
            pitcher_id="600008",
            event_type="home_run",
            is_home_run=True,
            launch_speed=100.0,
            launch_angle=25.0,
            is_barrel=True,
            estimated_woba=0.8,
            estimated_slg=1.5,
            batted_ball_type="fly_ball",
            is_pull=True,
        ),
        _stat_row(
            game_id="765402",
            game_date="2026-05-25",
            completed="2026-05-25T23:00:00Z",
            observed="2026-05-25T23:05:00Z",
            pa_id="g2-pa1",
            batter_id=BATTER_ID,
            pitcher_id=PITCHER_ID,
            event_type="strikeout",
            is_home_run=False,
            estimated_woba=0.0,
            estimated_slg=0.0,
        ),
        _stat_row(
            game_id="765404",
            game_date="2026-05-26",
            completed="2026-05-26T23:00:00Z",
            observed="2026-05-26T23:05:00Z",
            pa_id="g4-pa1",
            batter_id=BATTER_ID,
            pitcher_id="600007",
            event_type="field_out",
            is_home_run=False,
            launch_speed=80.0,
            launch_angle=5.0,
            is_barrel=False,
            estimated_woba=0.1,
            estimated_slg=0.2,
            batted_ball_type="ground_ball",
            is_pull=False,
        ),
        _stat_row(
            game_id="765403",
            game_date="2026-06-01",
            completed="2026-06-01T23:00:00Z",
            observed="2026-06-01T23:05:00Z",
            pa_id="g3-pa1",
            batter_id=BATTER_ID,
            pitcher_id="600008",
            event_type="home_run",
            is_home_run=True,
            launch_speed=96.0,
            launch_angle=28.0,
            is_barrel=True,
            estimated_woba=0.7,
            estimated_slg=1.2,
            batted_ball_type="fly_ball",
            is_pull=True,
        ),
        # Earlier game of a doubleheader: completed and visible before the cutoff.
        _stat_row(
            game_id=EARLY_DH_GAME,
            game_date="2026-06-05",
            completed="2026-06-05T16:00:00Z",
            observed="2026-06-05T17:00:00Z",
            pa_id="dh1-pa1",
            batter_id=BATTER_ID,
            pitcher_id="600008",
            event_type="walk",
            is_home_run=False,
            estimated_woba=0.6,
            estimated_slg=0.0,
        ),
        # Opposing probable pitcher's completed history.
        _stat_row(
            game_id="765410",
            game_date="2026-05-20",
            completed="2026-05-20T22:00:00Z",
            observed="2026-05-20T22:05:00Z",
            pa_id="gp1-pa1",
            batter_id=OTHER_BATTER_ID,
            pitcher_id=PITCHER_ID,
            event_type="home_run",
            is_home_run=True,
            launch_speed=101.0,
            launch_angle=27.0,
            is_barrel=True,
            estimated_woba=0.9,
            estimated_slg=1.7,
            batted_ball_type="fly_ball",
            is_pull=True,
        ),
        _stat_row(
            game_id="765411",
            game_date="2026-06-02",
            completed="2026-06-02T22:00:00Z",
            observed="2026-06-02T22:05:00Z",
            pa_id="gp2-pa1",
            batter_id="600003",
            pitcher_id=PITCHER_ID,
            event_type="field_out",
            is_home_run=False,
            launch_speed=90.0,
            launch_angle=0.0,
            is_barrel=False,
            estimated_woba=0.1,
            estimated_slg=0.1,
            batted_ball_type="ground_ball",
            is_pull=False,
        ),
        # Target-game outcome must never enter its own pregame row.
        _stat_row(
            game_id=TARGET_GAME,
            game_date="2026-06-05",
            completed="2026-06-06T02:00:00Z",
            observed="2026-06-06T02:05:00Z",
            pa_id="dh2-pa1",
            batter_id=BATTER_ID,
            pitcher_id=PITCHER_ID,
            event_type="home_run",
            is_home_run=True,
            launch_speed=110.0,
            launch_angle=25.0,
            is_barrel=True,
            estimated_woba=0.99,
            estimated_slg=2.0,
            batted_ball_type="fly_ball",
            is_pull=True,
        ),
        # A different future game is also unavailable at this cutoff.
        _stat_row(
            game_id="765499",
            game_date="2026-06-05",
            completed="2026-06-05T20:00:00Z",
            observed="2026-06-05T20:05:00Z",
            pa_id="future-pa1",
            batter_id=BATTER_ID,
            pitcher_id=PITCHER_ID,
            event_type="home_run",
            is_home_run=True,
            launch_speed=115.0,
            launch_angle=25.0,
            is_barrel=True,
            estimated_woba=0.99,
            estimated_slg=2.0,
            batted_ball_type="fly_ball",
            is_pull=True,
        ),
    ]
    _write_csv(
        source / context.SOURCE_FILES["statcast"], STATCAST_COLUMNS, statcast_rows
    )
    _write_csv(
        source / context.SOURCE_FILES["probable_pitchers"],
        (
            "event_id",
            "team",
            "pitcher_id",
            "pitcher_name",
            "normalized_pitcher_name",
            "pitcher_hand",
            "probable_pitcher_status",
            "identity_status",
            "identity_mapping_version",
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
            "source",
            "source_record_id",
            "source_version",
        ),
        [
            {
                "event_id": TARGET_GAME,
                "team": "BOS",
                "pitcher_id": PITCHER_ID,
                "pitcher_name": "Chris Sale",
                "normalized_pitcher_name": "christopher sale",
                "pitcher_hand": "R",
                "probable_pitcher_status": "confirmed",
                "identity_status": "verified_mlbam",
                "identity_mapping_version": "mlb-id-map-v2",
                "provider_published_at_utc": "2026-06-05T17:20:00Z",
                "first_observed_at_utc": "2026-06-05T17:25:00Z",
                "captured_at_utc": "2026-06-05T17:30:00Z",
                "source": "MLB StatsAPI",
                "source_record_id": "game-765432-bos-probable",
                "source_version": "statsapi-v1",
            }
        ],
    )
    _write_csv(
        source / context.SOURCE_FILES["lineups"],
        (
            "event_id",
            "team",
            "player_id",
            "lineup_status",
            "batting_order_position",
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
            "source",
            "source_record_id",
            "expected_pa",
            "expected_pa_source",
            "expected_pa_version",
        ),
        [
            {
                "event_id": TARGET_GAME,
                "team": "TOR",
                "player_id": BATTER_ID,
                "lineup_status": "confirmed",
                "batting_order_position": 2,
                "provider_published_at_utc": "2026-06-05T17:30:00Z",
                "first_observed_at_utc": "2026-06-05T17:35:00Z",
                "captured_at_utc": "2026-06-05T17:40:00Z",
                "source": "MLB StatsAPI",
                "source_record_id": "game-765432-tor-lineup-600001",
                "expected_pa": "",
                "expected_pa_source": "",
                "expected_pa_version": "",
            }
        ],
    )
    _write_csv(
        source / context.SOURCE_FILES["weather"],
        (
            "event_id",
            "venue_id",
            "venue_name",
            "weather_type",
            "weather_evidence_class",
            "issued_at_utc",
            "valid_for_utc",
            "measured_at_utc",
            "captured_at_utc",
            "temperature",
            "temperature_unit",
            "wind_speed",
            "wind_speed_unit",
            "wind_direction",
            "humidity",
            "roof_status",
            "source",
            "source_record_id",
            "source_version",
        ),
        [
            {
                "event_id": TARGET_GAME,
                "venue_id": "venue-1",
                "venue_name": "Rogers Centre",
                "weather_type": "forecast",
                "weather_evidence_class": "provider_pregame_forecast",
                "issued_at_utc": "2026-06-05T17:30:00Z",
                "valid_for_utc": "2026-06-05T23:00:00Z",
                "measured_at_utc": "",
                "captured_at_utc": "2026-06-05T17:45:00Z",
                "temperature": 79,
                "temperature_unit": "fahrenheit",
                "wind_speed": 8,
                "wind_speed_unit": "mph",
                "wind_direction": "out_to_left",
                "humidity": 61,
                "roof_status": "open",
                "source": "pregame forecast provider",
                "source_record_id": "forecast-765432-1730z",
                "source_version": "forecast-v1",
            }
        ],
    )
    _write_csv(
        source / context.SOURCE_FILES["park_factors"],
        (
            "venue_id",
            "venue_name",
            "park_hr_factor",
            "park_factor_source",
            "park_factor_version",
            "effective_from_date",
            "effective_to_date",
            "published_or_available_at_utc",
            "captured_at_utc",
            "source_record_id",
        ),
        [
            {
                "venue_id": "venue-1",
                "venue_name": "Rogers Centre",
                "park_hr_factor": 1.08,
                "park_factor_source": "local-versioned-table",
                "park_factor_version": "2026-v1",
                "effective_from_date": "2026-01-01",
                "effective_to_date": "2026-12-31",
                "published_or_available_at_utc": "2026-03-01T12:00:00Z",
                "captured_at_utc": "2026-03-01T12:05:00Z",
                "source_record_id": "venue-1-2026-v1-home-run",
            }
        ],
    )
    _write_csv(
        source / context.SOURCE_FILES["market"],
        (
            "event_id",
            "player_id",
            "sportsbook",
            "american_odds",
            "evidence_class",
            "market_configuration_id",
            "quote_at_utc",
            "captured_at_utc",
        ),
        [
            {
                "event_id": TARGET_GAME,
                "player_id": BATTER_ID,
                "sportsbook": "Book A",
                "american_odds": 150,
                "evidence_class": "pregame_snapshot",
                "market_configuration_id": "market-config-v1",
                "quote_at_utc": "2026-06-05T17:40:00Z",
                "captured_at_utc": "2026-06-05T17:50:00Z",
            },
            {
                "event_id": TARGET_GAME,
                "player_id": BATTER_ID,
                "sportsbook": "Book B",
                "american_odds": 160,
                "evidence_class": "pregame_snapshot",
                "market_configuration_id": "market-config-v1",
                "quote_at_utc": "2026-06-05T17:50:00Z",
                "captured_at_utc": "2026-06-05T17:55:00Z",
            },
        ],
    )
    return source


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    return _make_source_pack(tmp_path)


def _build(source_root: Path, **overrides: object) -> context.ContextFeatureBuildResult:
    arguments: dict[str, object] = {
        "operating_date": OPERATING_DATE,
        "as_of_utc": AS_OF,
        "source_root": source_root,
        "git_commit": GIT_COMMIT,
        "dry_run": True,
    }
    arguments.update(overrides)
    return context.build_context_features(**arguments)  # type: ignore[arg-type]


def test_deterministic_ids_manifest_output_and_exact_schema(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _build(source_root)
    second = _build(source_root)
    assert first.rows == second.rows
    assert first.manifest == second.manifest
    assert first.summary == second.summary
    assert first.rows[0]["feature_row_id"] == second.rows[0]["feature_row_id"]
    assert tuple(first.rows[0]) == context.FEATURE_COLUMNS
    assert first.manifest["feature_column_allowlist"] == list(context.FEATURE_COLUMNS)
    assert first.manifest["manifest_digest"] == first.summary["manifest_digest"]
    assert first.rows[0]["configuration_digest"] == first.manifest["configuration_digest"]

    allowed = tmp_path / "research-root"
    allowed.mkdir()
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    built_a = _build(source_root, dry_run=False, output_root=allowed / "build-a")
    built_b = _build(source_root, dry_run=False, output_root=allowed / "build-b")
    assert built_a.features_path is not None and built_b.features_path is not None
    assert built_a.manifest_path is not None and built_b.manifest_path is not None
    assert built_a.summary_path is not None and built_b.summary_path is not None
    assert built_a.features_path.read_bytes() == built_b.features_path.read_bytes()
    assert built_a.manifest_path.read_bytes() == built_b.manifest_path.read_bytes()
    assert built_a.summary_path.read_bytes() == built_b.summary_path.read_bytes()
    header, rows = _read_csv(built_a.features_path)
    assert header == context.FEATURE_COLUMNS
    assert len(rows) == 1


def test_research_only_and_operational_boundaries(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", no_network)
    result = _build(source_root)
    row = result.rows[0]
    assert row["research_only"] is True
    assert result.manifest["model_training_enabled"] is False
    assert result.manifest["predictions_enabled"] is False
    assert result.manifest["promotion_enabled"] is False
    assert result.manifest["eligible_for_betting"] is False
    assert result.summary["provider_network_access_performed"] is False
    assert result.summary["live_prospective_trial_modified"] is False
    assert result.summary["official_pick_or_lifecycle_modified"] is False
    allowed = tmp_path / "research-root"
    allowed.mkdir()
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    with pytest.raises(context.ContextFeatureError, match="dedicated research root"):
        _build(
            source_root,
            dry_run=False,
            output_root=tmp_path / "mlb_hr_prospective_trial",
        )


def test_source_before_and_exactly_at_as_of_are_accepted(source_root: Path) -> None:
    probable = source_root / context.SOURCE_FILES["probable_pitchers"]
    _rewrite(
        probable,
        lambda row: {
            **row,
            "provider_published_at_utc": AS_OF,
            "first_observed_at_utc": AS_OF,
            "captured_at_utc": AS_OF,
        },
    )
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def exact_doubleheader(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == EARLY_DH_GAME:
            row["provider_published_at_utc"] = AS_OF
            row["first_observed_at_utc"] = AS_OF
            row["captured_at_utc"] = AS_OF
        return row

    _rewrite(statcast, exact_doubleheader)
    row = _build(source_root).rows[0]
    assert row["probable_pitcher_available"] is True
    assert row["probable_pitcher_captured_at_utc"] == AS_OF
    assert row["hitter_season_pa"] == 5


def test_forward_observation_clocks_do_not_invent_provider_publication(
    source_root: Path,
) -> None:
    for source_name in ("probable_pitchers", "lineups"):
        path = source_root / context.SOURCE_FILES[source_name]
        _rewrite(path, lambda row: {**row, "provider_published_at_utc": ""})
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _rewrite(statcast, lambda row: {**row, "provider_published_at_utc": ""})

    result = _build(source_root)
    row = result.rows[0]

    assert row["probable_pitcher_available"] is True
    assert row["probable_pitcher_announced_or_published_at_utc"] is None
    assert row["probable_pitcher_provider_published_at_utc"] is None
    assert row["probable_pitcher_first_observed_at_utc"] == "2026-06-05T17:25:00Z"
    assert row["lineup_available"] is True
    assert row["lineup_announced_or_published_at_utc"] is None
    assert row["lineup_provider_published_at_utc"] is None
    assert row["lineup_first_observed_at_utc"] == "2026-06-05T17:35:00Z"
    assert row["hitter_season_pa"] == 5


def test_sources_after_as_of_are_not_accepted(source_root: Path) -> None:
    for source_name in ("probable_pitchers", "lineups", "weather", "park_factors", "market"):
        path = source_root / context.SOURCE_FILES[source_name]
        _rewrite(
            path,
            lambda row: {**row, "captured_at_utc": "2026-06-05T18:01:00Z"},
        )
    row = _build(source_root).rows[0]
    assert row["probable_pitcher_available"] is False
    assert row["lineup_available"] is False
    assert row["weather_available"] is False
    assert row["park_factor_available"] is False
    assert row["market_available"] is False
    assert row["pitcher_stats_available"] is False


def test_future_same_game_and_post_cutoff_rows_cannot_leak_but_doubleheader_can(
    source_root: Path
) -> None:
    row = _build(source_root).rows[0]
    # Five completed/visible PAs include the earlier doubleheader game, while
    # the target-game HR and different future-game HR are excluded.
    assert row["hitter_season_pa"] == 5
    assert row["hitter_season_hr"] == 2
    assert row["hitter_7d_pa"] == 2
    assert row["hitter_7d_hr"] == 1
    assert row["source_max_captured_at_utc_json"].find("2026-06-06") == -1


def test_timezone_equivalent_cutoff_is_semantically_identical(source_root: Path) -> None:
    utc = _build(source_root)
    eastern = _build(source_root, as_of_utc="2026-06-05T14:00:00-04:00")
    assert utc.rows == eastern.rows
    assert utc.manifest == eastern.manifest


def test_hitter_season_and_rolling_arithmetic(source_root: Path) -> None:
    row = _build(source_root).rows[0]
    assert row["hitter_season_pa"] == 5
    assert row["hitter_season_hr"] == 2
    assert row["hitter_season_hr_per_pa"] == 0.4
    assert row["hitter_7d_pa"] == 2
    assert row["hitter_7d_hr_per_pa"] == 0.5
    assert row["hitter_14d_pa"] == 4
    assert row["hitter_14d_hr_per_pa"] == 0.25
    assert row["hitter_30d_pa"] == 4
    assert row["hitter_season_barrel_rate"] == pytest.approx(2 / 3)
    assert row["hitter_season_hard_hit_rate"] == pytest.approx(2 / 3)
    assert row["hitter_season_average_exit_velocity"] == 92.0
    assert row["hitter_season_average_launch_angle"] == pytest.approx(58 / 3)
    assert row["hitter_season_max_exit_velocity"] == 100.0
    assert row["hitter_season_sweet_spot_rate"] == pytest.approx(2 / 3)
    assert row["hitter_season_strikeout_rate"] == 0.2
    assert row["hitter_season_walk_rate"] == 0.2
    assert row["hitter_season_fly_ball_rate"] == pytest.approx(2 / 3)
    assert row["hitter_season_pull_rate"] == pytest.approx(2 / 3)


def test_missing_statcast_is_explicit_and_never_imputed(source_root: Path) -> None:
    (source_root / context.SOURCE_FILES["statcast"]).unlink()
    row = _build(source_root).rows[0]
    assert row["hitter_stats_available"] is False
    assert row["pitcher_stats_available"] is False
    assert row["hitter_season_pa"] is None
    assert row["hitter_season_hr_per_pa"] is None
    assert row["pitcher_season_batters_faced"] is None
    assert row["expected_pa"] is None
    assert row["expected_pa_available"] is False


def test_pitcher_matchup_lineup_park_weather_and_market_context(source_root: Path) -> None:
    row = _build(source_root).rows[0]
    assert row["probable_pitcher_id"] == PITCHER_ID
    assert row["pitcher_hand"] == "R"
    assert row["pitcher_season_batters_faced"] == 3
    assert row["pitcher_season_hr_allowed"] == 1
    assert row["pitcher_season_hr_per_batter_faced"] == pytest.approx(1 / 3)
    assert row["pitcher_season_barrel_rate_allowed"] == 0.5
    assert row["pitcher_season_hard_hit_rate_allowed"] == 0.5
    assert row["pitcher_season_average_exit_velocity_allowed"] == 95.5
    assert row["pitcher_season_average_launch_angle_allowed"] == 13.5
    assert row["pitcher_history_game_count"] == 3
    assert row["pitcher_history_first_game_completed_at_utc"] == "2026-05-20T22:00:00Z"
    assert row["pitcher_history_last_game_completed_at_utc"] == "2026-06-02T22:00:00Z"
    assert row["pitcher_history_max_available_at_utc"] == "2026-06-02T22:05:00Z"
    assert row["pitcher_history_max_first_observed_at_utc"] == "2026-06-02T22:05:00Z"
    assert row["pitcher_history_max_captured_at_utc"] == "2026-06-02T22:05:00Z"
    assert row["pitcher_history_source_digest"] == hashlib.sha256(
        (source_root / context.SOURCE_FILES["statcast"]).read_bytes()
    ).hexdigest()
    assert row["platoon_matchup_category"] == "same_side"
    assert row["bvp_pa_descriptive"] == 1
    assert json.loads(row["pitcher_pitch_mix_json"]) == {"FF": 1.0}
    assert json.loads(row["pitcher_pitch_type_context_json"]) == {
        "FF": {
            "average_exit_velocity_allowed": 95.5,
            "average_launch_angle_allowed": 13.5,
            "average_velocity": 95.0,
            "barrel_rate_allowed": 0.5,
            "contact_count": 2,
            "fly_ball_rate": 0.5,
            "hard_hit_rate_allowed": 0.5,
            "hr_allowed": 1,
            "hr_per_terminal_batter_faced": pytest.approx(1 / 3),
            "pitch_count": 3,
            "terminal_batters_faced": 3,
            "usage_rate": 1.0,
        }
    }
    assert row["lineup_status"] == "confirmed"
    assert row["batting_order_position"] == 2
    assert row["park_hr_factor"] == 1.08
    assert row["park_factor_effective_from_date"] == "2026-01-01"
    assert row["park_factor_effective_to_date"] == "2026-12-31"
    assert row["park_factor_source_digest"] == hashlib.sha256(
        (source_root / context.SOURCE_FILES["park_factors"]).read_bytes()
    ).hexdigest()
    assert row["temperature"] == 79.0
    assert row["temperature_unit"] == "fahrenheit"
    assert row["wind_speed_unit"] == "mph"
    assert row["humidity"] == 61.0
    assert row["weather_issued_at_utc"] == "2026-06-05T17:30:00Z"
    assert row["weather_measured_at_utc"] is None
    assert row["weather_source"] == "pregame forecast provider"
    assert row["market_best_sportsbook"] == "Book B"
    assert row["market_best_american_odds"] == 160
    assert row["market_bookmaker_count"] == 2


def test_expected_pa_requires_source_supplied_admissible_lineup(source_root: Path) -> None:
    lineup = source_root / context.SOURCE_FILES["lineups"]
    columns, rows = _read_csv(lineup)
    rows[0].update(
        {
            "lineup_status": "projected",
            "expected_pa": "4.6",
            "expected_pa_source": "pregame projection provider",
            "expected_pa_version": "expected-pa-v1",
        }
    )
    _write_csv(lineup, columns, rows)

    result = _build(source_root)
    row = result.rows[0]

    assert row["lineup_status"] == "projected"
    assert row["batting_order_position"] == 2
    assert row["expected_pa"] == 4.6
    assert row["expected_pa_available"] is True
    assert row["expected_pa_source"] == "pregame projection provider"
    assert row["expected_pa_version"] == "expected-pa-v1"
    assert result.summary["availability_counts"]["expected_pa_available"] == 1


def test_expected_pa_without_lineup_source_lineage_fails_closed(source_root: Path) -> None:
    lineup = source_root / context.SOURCE_FILES["lineups"]
    _rewrite(lineup, lambda row: {**row, "expected_pa": "4.6"})

    with pytest.raises(context.ContextFeatureError, match="expected_pa_source"):
        _build(source_root)


def test_missing_or_late_probable_pitcher_stays_unavailable(source_root: Path) -> None:
    probable = source_root / context.SOURCE_FILES["probable_pitchers"]
    _rewrite(
        probable,
        lambda row: {**row, "captured_at_utc": "2026-06-05T18:00:01Z"},
    )
    row = _build(source_root).rows[0]
    assert row["probable_pitcher_available"] is False
    assert row["probable_pitcher_id"] is None
    assert row["pitcher_stats_available"] is False
    probable.unlink()
    assert _build(source_root).rows[0]["probable_pitcher_available"] is False


def test_lineup_announced_after_cutoff_is_not_backfilled(source_root: Path) -> None:
    lineup = source_root / context.SOURCE_FILES["lineups"]
    _rewrite(
        lineup,
        lambda row: {**row, "captured_at_utc": "2026-06-05T18:00:01Z"},
    )
    row = _build(source_root).rows[0]
    assert row["lineup_available"] is False
    assert row["lineup_status"] == "unknown"
    assert row["batting_order_position"] is None


def test_weather_and_park_cutoffs_and_missingness(source_root: Path) -> None:
    weather = source_root / context.SOURCE_FILES["weather"]
    _rewrite(
        weather,
        lambda row: {**row, "captured_at_utc": "2026-06-05T18:00:01Z"},
    )
    row = _build(source_root).rows[0]
    assert row["weather_available"] is False
    assert row["temperature"] is None
    weather.unlink()
    (source_root / context.SOURCE_FILES["park_factors"]).unlink()
    row = _build(source_root).rows[0]
    assert row["weather_available"] is False
    assert row["park_factor_available"] is False
    assert row["park_hr_factor"] is None


def test_future_park_version_cannot_leak_into_earlier_cutoff(source_root: Path) -> None:
    park = source_root / context.SOURCE_FILES["park_factors"]
    columns, rows = _read_csv(park)
    _write_csv(
        park,
        columns,
        [
            *rows,
            {
                **rows[0],
                "park_hr_factor": "1.50",
                "park_factor_version": "2026-v2",
                "published_or_available_at_utc": "2026-06-05T18:00:01Z",
                "captured_at_utc": "2026-06-05T18:00:02Z",
                "source_record_id": "venue-1-2026-v2-home-run",
            },
        ],
    )

    row = _build(source_root).rows[0]

    assert row["park_hr_factor"] == 1.08
    assert row["park_factor_version"] == "2026-v1"


def test_identity_and_event_team_mismatches_fail_closed(source_root: Path) -> None:
    candidate = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(
        candidate,
        lambda row: {**row, "normalized_player_name": "wrong person"},
    )
    with pytest.raises(context.ContextFeatureError, match="normalized_player_name"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "second")
    candidate = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidate, lambda row: {**row, "opponent": "CCC"})
    with pytest.raises(context.ContextFeatureError, match="canonical MLB team"):
        _build(source_root)


def test_candidate_and_pitcher_mapping_provenance_fail_closed(source_root: Path) -> None:
    candidate = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(
        candidate,
        lambda row: {**row, "candidate_captured_at_utc": "2026-06-05T18:00:01Z"},
    )
    with pytest.raises(context.ContextFeatureError, match="candidate clocks"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "pitcher-identity")
    probable = source_root / context.SOURCE_FILES["probable_pitchers"]
    _rewrite(probable, lambda row: {**row, "identity_status": "ambiguous"})
    with pytest.raises(context.ContextFeatureError, match="identity_status"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "operating-date")
    candidate = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidate, lambda row: {**row, "operating_date": "2026-06-04"})
    with pytest.raises(context.ContextFeatureError, match="crosswalk game_date mismatch"):
        _build(source_root)


def test_source_digest_mutation_and_immutable_output_are_enforced(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context, "compute_file_sha256", lambda path: "0" * 64)
    with pytest.raises(context.ContextFeatureError, match="changed during materialization"):
        _build(source_root)
    monkeypatch.undo()
    allowed = tmp_path / "research-root"
    allowed.mkdir()
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    output = allowed / "immutable-build"
    _build(source_root, dry_run=False, output_root=output)
    with pytest.raises(context.ContextFeatureError, match="already exists"):
        _build(source_root, dry_run=False, output_root=output)


def test_current_aggregate_files_are_not_used_to_reconstruct_history(
    source_root: Path
) -> None:
    _write_csv(
        source_root / "current_season_totals.csv",
        ("player_id", "season_pa", "season_hr"),
        [{"player_id": BATTER_ID, "season_pa": 999, "season_hr": 999}],
    )
    row = _build(source_root).rows[0]
    assert row["hitter_season_pa"] == 5
    assert row["hitter_season_hr"] == 2
    assert "statcast" in _build(source_root).manifest["temporal_contracts"]


def test_nonfinite_values_and_closing_evidence_fail_closed(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def inject_nan(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == "765401":
            row["launch_speed"] = "NaN"
        return row

    _rewrite(statcast, inject_nan)
    with pytest.raises(context.ContextFeatureError, match="must be finite"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "closing")
    market = source_root / context.SOURCE_FILES["market"]
    _rewrite(market, lambda row: {**row, "evidence_class": "closing_evidence"})
    with pytest.raises(context.ContextFeatureError, match="forbidden non-pregame"):
        _build(source_root)


def test_dry_run_cli_writes_nothing(source_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = {path.name: path.read_bytes() for path in source_root.iterdir()}
    assert (
        context.main(
            [
                "build-context-features",
                "--operating-date",
                OPERATING_DATE,
                "--as-of-utc",
                AS_OF,
                "--source-root",
                str(source_root),
                "--git-commit",
                GIT_COMMIT,
                "--dry-run",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["row_count"] == 1
    assert before == {path.name: path.read_bytes() for path in source_root.iterdir()}


def test_module_has_no_provider_training_or_operational_imports() -> None:
    module_path = Path(context.__file__).resolve()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    prohibited = {"requests", "urllib", "httpx", "socket"}
    assert not (imports & prohibited)
    assert not any(name.startswith("courtvision.providers") for name in from_imports)
    assert not any("hr_prospective_trial" in name for name in from_imports)
    assert not any("official_pick" in name for name in from_imports)
    assert not any("lifecycle" in name for name in from_imports)


def test_aware_datetime_object_is_normalized_and_naive_object_fails(
    source_root: Path
) -> None:
    aware = _build(
        source_root,
        as_of_utc=datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
    )
    assert aware.rows[0]["as_of_utc"] == AS_OF
    with pytest.raises(context.ContextFeatureError, match="timezone-aware"):
        _build(source_root, as_of_utc=datetime(2026, 6, 5, 18, 0))


# Requirement-to-test map for the direct review matrix:
# H1 candidate provenance: tests below containing candidate_universe.
# H2 verified identity: crosswalk/duplicate_name/traded_player/role_collision.
# H3 source grain: multi_pitch/duplicate_pitch/terminal/expected_stats.
# H4 clocks: source_clock/weather/announcement/market_quote tests.
# H5 output allowlist: output_namespace and resolved_symlink tests.
# M1-M4: usable_availability, horizon, market_lineage, and boundary_matrix tests.
# L1: persisted_corruption and final_source_mutation tests.


def test_candidate_universe_identity_is_market_independent(source_root: Path) -> None:
    with_market = _build(source_root)
    (source_root / context.SOURCE_FILES["market"]).unlink()
    without_market = _build(source_root)
    identity = lambda result: tuple(
        (row["event_id"], row["player_id"]) for row in result.rows
    )
    assert identity(with_market) == identity(without_market) == ((TARGET_GAME, BATTER_ID),)
    assert with_market.rows[0]["market_available"] is True
    assert without_market.rows[0]["market_available"] is False


@pytest.mark.parametrize("origin", ["market_selected", "manual_or_unknown"])
def test_non_neutral_candidate_universe_origins_fail_closed(
    source_root: Path, origin: str
) -> None:
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidates, lambda row: {**row, "candidate_universe_origin": origin})
    with pytest.raises(context.ContextFeatureError, match="not allowed for neutral comparison"):
        _build(source_root)


@pytest.mark.parametrize(
    "field_name",
    [
        "candidate_universe_id",
        "candidate_universe_version",
        "candidate_universe_generator",
        "candidate_universe_origin",
        "candidate_universe_policy",
        "candidate_universe_source_digest",
        "candidate_universe_configuration_digest",
        "candidate_universe_cutoff_utc",
    ],
)
def test_missing_candidate_universe_metadata_fails_closed(
    source_root: Path, field_name: str
) -> None:
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidates, lambda row: {**row, field_name: ""})
    with pytest.raises(context.ContextFeatureError):
        _build(source_root)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("candidate_universe_source_digest", "3" * 64),
        ("candidate_universe_configuration_digest", "4" * 64),
    ],
)
def test_candidate_universe_digest_changes_alter_provenance_deterministically(
    source_root: Path, field_name: str, replacement: str
) -> None:
    baseline = _build(source_root)
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidates, lambda row: {**row, field_name: replacement})
    changed = _build(source_root)
    repeated = _build(source_root)
    assert changed.rows == repeated.rows
    assert changed.manifest == repeated.manifest
    assert changed.rows[0][field_name] == replacement
    assert changed.rows[0]["feature_row_id"] != baseline.rows[0]["feature_row_id"]
    assert changed.manifest["candidate_universe_contract"] != baseline.manifest[
        "candidate_universe_contract"
    ]


def test_missing_arbitrary_and_self_asserted_identity_fail_closed(source_root: Path) -> None:
    crosswalk = source_root / context.SOURCE_FILES["identity_crosswalk"]
    crosswalk.unlink()
    with pytest.raises(context.ContextFeatureError, match="required source snapshot does not exist"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "arbitrary-id")
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidates, lambda row: {**row, "player_id": "arbitrary-name-id"})
    with pytest.raises(context.ContextFeatureError, match="canonical MLBAM id"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "caller-resolved")
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(candidates, lambda row: {**row, "identity_status": "resolved"})
    with pytest.raises(context.ContextFeatureError, match="must be verified_mlbam"):
        _build(source_root)


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    [
        ("batter_name", "Different Player", "batter name mismatch"),
        ("identity_mapping_version", "different-version", "mapping version mismatch"),
        ("batting_team", "BOS", "crosswalk validation failed|batting_team mismatch"),
        ("game_date", "2026-06-04", "crosswalk validation failed|game_date mismatch"),
    ],
)
def test_strong_crosswalk_conflicts_fail_closed(
    source_root: Path, field_name: str, replacement: str, message: str
) -> None:
    crosswalk = source_root / context.SOURCE_FILES["identity_crosswalk"]
    _rewrite(crosswalk, lambda row: {**row, field_name: replacement})
    with pytest.raises(context.ContextFeatureError, match=message):
        _build(source_root)


def test_duplicate_display_names_remain_separate_by_canonical_id(source_root: Path) -> None:
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _, candidate_rows = _read_csv(candidates)
    second_candidate = {
        **candidate_rows[0],
        "player_id": "600004",
    }
    _append_rows(candidates, [second_candidate])
    crosswalk = source_root / context.SOURCE_FILES["identity_crosswalk"]
    _, crosswalk_rows = _read_csv(crosswalk)
    second_crosswalk = {
        **crosswalk_rows[0],
        "retrosheet_batter_id": "ramij002",
        "mlbam_batter_id": "600004",
    }
    _append_rows(crosswalk, [second_crosswalk])
    result = _build(source_root)
    assert {row["player_id"] for row in result.rows} == {BATTER_ID, "600004"}
    assert len({row["feature_row_id"] for row in result.rows}) == 2


def test_traded_player_history_does_not_override_event_team_context(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _append_rows(
        statcast,
        [
            _stat_row(
                game_id="765420",
                game_date="2026-04-10",
                completed="2026-04-10T22:00:00Z",
                observed="2026-04-10T22:05:00Z",
                pa_id="traded-pa1",
                batter_id=BATTER_ID,
                pitcher_id="600020",
                event_type="field_out",
                is_home_run=False,
                home_team="ATL",
                away_team="NYM",
                batter_team="ATL",
                pitcher_team="NYM",
            )
        ],
    )
    row = _build(source_root).rows[0]
    assert (row["team"], row["opponent"]) == ("TOR", "BOS")
    assert row["hitter_season_pa"] == 6


def test_batter_pitcher_role_collision_fails_closed(source_root: Path) -> None:
    crosswalk = source_root / context.SOURCE_FILES["identity_crosswalk"]
    _rewrite(crosswalk, lambda row: {**row, "mlbam_pitcher_id": BATTER_ID})
    with pytest.raises(context.ContextFeatureError, match="role collision"):
        _build(source_root)


def test_multi_pitch_pa_uses_every_pitch_but_one_terminal_outcome(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def move_terminal(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == "765411":
            row["pitch_number"] = "3"
        return row

    _rewrite(statcast, move_terminal)
    _append_rows(
        statcast,
        [
            _stat_row(
                game_id="765411",
                game_date="2026-06-02",
                completed="2026-06-02T22:00:00Z",
                observed="2026-06-02T22:05:00Z",
                pa_id="gp2-pa1",
                batter_id="600003",
                pitcher_id=PITCHER_ID,
                event_type="",
                is_home_run=False,
                pitch_number=1,
                pitch_type="SL",
                release_speed=86.0,
            ),
            _stat_row(
                game_id="765411",
                game_date="2026-06-02",
                completed="2026-06-02T22:00:00Z",
                observed="2026-06-02T22:05:00Z",
                pa_id="gp2-pa1",
                batter_id="600003",
                pitcher_id=PITCHER_ID,
                event_type="",
                is_home_run=False,
                pitch_number=2,
                pitch_type="FF",
                release_speed=94.0,
            ),
        ],
    )
    row = _build(source_root).rows[0]
    assert row["pitcher_season_batters_faced"] == 3
    assert json.loads(row["pitcher_pitch_mix_json"]) == {"FF": 0.8, "SL": 0.2}
    assert json.loads(row["pitcher_average_velocity_json"]) == {"FF": 94.75, "SL": 86.0}


def test_duplicate_pitch_ordinal_fails_closed(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _, rows = _read_csv(statcast)
    _append_rows(statcast, [dict(rows[0])])
    with pytest.raises(context.ContextFeatureError, match="duplicate Statcast pitch identity"):
        _build(source_root)


@pytest.mark.parametrize(
    ("event_type", "is_home_run"),
    [("home_run", "false"), ("field_out", "true")],
)
def test_terminal_home_run_flag_must_match_canonical_event(
    source_root: Path, event_type: str, is_home_run: str
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def contradict(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == "765401":
            row["event_type"] = event_type
            row["is_home_run"] = is_home_run
        return row

    _rewrite(statcast, contradict)
    with pytest.raises(context.ContextFeatureError, match="conflicting home-run evidence"):
        _build(source_root)


def test_multiple_or_nonfinal_terminal_rows_fail_closed(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _, rows = _read_csv(statcast)
    extra = {**rows[0], "pitch_number": "2", "event_type": "field_out", "is_home_run": "false"}
    _append_rows(statcast, [extra])
    with pytest.raises(context.ContextFeatureError, match="at most one terminal event"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "nonfinal-terminal")
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _, rows = _read_csv(statcast)
    extra = {**rows[0], "pitch_number": "2", "event_type": "", "is_home_run": "false"}
    _append_rows(statcast, [extra])
    with pytest.raises(context.ContextFeatureError, match="not the final pitch"):
        _build(source_root)


def test_expected_stats_are_explicitly_unavailable_for_contact_k_and_bb_rows(
    source_root: Path,
) -> None:
    result = _build(source_root)
    row = result.rows[0]
    expected_fields = [
        name
        for name in context.FEATURE_COLUMNS
        if "xwoba" in name or "xslg" in name
    ]
    assert expected_fields
    assert all(row[name] is None for name in expected_fields)
    availability = json.loads(row["statcast_metric_availability_json"])
    assert availability["pa_level_xwoba"] is False
    assert availability["pa_level_xslg"] is False
    assert {"pa_level_xwoba", "pa_level_xslg"}.issubset(
        result.manifest["source_capability_gaps"]
    )


@pytest.mark.parametrize(
    ("source_name", "available_field", "capture_field", "message"),
    [
        ("statcast", "first_observed_at_utc", "captured_at_utc", "Statcast clocks"),
        ("probable_pitchers", "first_observed_at_utc", "captured_at_utc", "first_observed"),
        ("lineups", "first_observed_at_utc", "captured_at_utc", "first_observed"),
        ("weather", "issued_at_utc", "captured_at_utc", "after capture"),
        ("park_factors", "published_or_available_at_utc", "captured_at_utc", "after capture"),
        ("market", "quote_at_utc", "captured_at_utc", "after capture"),
    ],
)
def test_source_clock_ordering_is_fail_closed(
    source_root: Path,
    source_name: str,
    available_field: str,
    capture_field: str,
    message: str,
) -> None:
    path = source_root / context.SOURCE_FILES[source_name]
    _rewrite(path, lambda row: {**row, available_field: "2026-06-05T17:59:00Z", capture_field: "2026-06-05T17:58:00Z"})
    with pytest.raises(context.ContextFeatureError, match=message):
        _build(source_root)


def test_weather_valid_for_and_final_evidence_fail_closed(source_root: Path) -> None:
    weather = source_root / context.SOURCE_FILES["weather"]
    _rewrite(weather, lambda row: {**row, "valid_for_utc": "2026-06-06T03:00:00Z"})
    with pytest.raises(context.ContextFeatureError, match="does not cover game start"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "final-weather")
    weather = source_root / context.SOURCE_FILES["weather"]
    _rewrite(weather, lambda row: {**row, "weather_evidence_class": "final_game_weather"})
    with pytest.raises(context.ContextFeatureError, match="forbidden final/postgame weather"):
        _build(source_root)


@pytest.mark.parametrize("source_name", ["probable_pitchers", "lineups"])
def test_announcement_after_cutoff_is_unavailable(
    source_root: Path, source_name: str
) -> None:
    path = source_root / context.SOURCE_FILES[source_name]
    _rewrite(
        path,
        lambda row: {
            **row,
            "first_observed_at_utc": "2026-06-05T18:01:00Z",
            "captured_at_utc": "2026-06-05T18:02:00Z",
        },
    )
    row = _build(source_root).rows[0]
    flag = "probable_pitcher_available" if source_name == "probable_pitchers" else "lineup_available"
    assert row[flag] is False


def test_market_quote_after_cutoff_cannot_hide_behind_earlier_capture(source_root: Path) -> None:
    market = source_root / context.SOURCE_FILES["market"]
    _rewrite(
        market,
        lambda row: {
            **row,
            "quote_at_utc": "2026-06-05T18:01:00Z",
            "captured_at_utc": "2026-06-05T17:59:00Z",
        },
    )
    with pytest.raises(context.ContextFeatureError, match="quote time is after capture"):
        _build(source_root)


@pytest.mark.parametrize(
    "relative_output",
    [
        "prospective_trial/build",
        "prospective_trial/descendant/build",
        "mlb_hr_prospective_trial/build",
        "MLB_HR_PROSPECTIVE_TRIAL/build",
        "arbitrary/build",
    ],
)
def test_output_namespace_rejects_every_location_outside_positive_allowlist(
    source_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_output: str,
) -> None:
    allowed = tmp_path / "approved" / "context_features"
    allowed.mkdir(parents=True)
    outside = tmp_path / relative_output
    outside.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    with pytest.raises(context.ContextFeatureError, match="dedicated research root"):
        _build(source_root, dry_run=False, output_root=outside)


def test_output_namespace_accepts_only_new_child_and_rejects_resolved_symlink_escape(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "approved" / "context_features"
    allowed.mkdir(parents=True)
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    result = _build(source_root, dry_run=False, output_root=allowed / "accepted-build")
    assert result.output_root == (allowed / "accepted-build").resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    link = allowed / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    with pytest.raises(context.ContextFeatureError, match="dedicated research root"):
        _build(source_root, dry_run=False, output_root=link / "escaped-build")


def test_usable_evidence_controls_weather_park_and_statcast_availability(
    source_root: Path,
) -> None:
    weather = source_root / context.SOURCE_FILES["weather"]
    _rewrite(
        weather,
        lambda row: {
            **row,
                "temperature": "",
                "temperature_unit": "",
                "wind_speed": "",
                "wind_speed_unit": "",
                "wind_direction": "",
                "humidity": "",
                "roof_status": "",
        },
    )
    park = source_root / context.SOURCE_FILES["park_factors"]
    _rewrite(park, lambda row: {**row, "park_hr_factor": ""})
    statcast = source_root / context.SOURCE_FILES["statcast"]
    optional_fields = (
        "launch_speed",
        "launch_angle",
        "is_barrel",
        "batted_ball_type",
        "is_pull",
        "pitch_type",
        "release_speed",
    )
    _rewrite(statcast, lambda row: {**row, **{name: "" for name in optional_fields}})
    row = _build(source_root).rows[0]
    assert row["weather_available"] is False and row["temperature"] is None
    assert row["park_factor_available"] is False and row["park_hr_factor"] is None
    availability = json.loads(row["statcast_metric_availability_json"])
    assert all(availability[name] is False for name in optional_fields)
    assert row["hitter_season_barrel_rate"] is None
    assert row["hitter_season_average_exit_velocity"] is None
    assert row["pitcher_pitch_mix_json"] is None


def test_partial_metric_coverage_uses_only_usable_denominators(source_root: Path) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def leave_one_contact_value(row: dict[str, str]) -> dict[str, str]:
        if row["batter_id"] == BATTER_ID:
            row["launch_speed"] = "100" if row["game_id"] == "765401" else ""
            row["is_barrel"] = "true" if row["game_id"] == "765401" else ""
        return row

    _rewrite(statcast, leave_one_contact_value)
    row = _build(source_root).rows[0]
    assert row["hitter_season_average_exit_velocity"] == 100.0
    assert row["hitter_season_hard_hit_rate"] == 1.0
    assert row["hitter_season_barrel_rate"] == 1.0
    assert json.loads(row["statcast_metric_availability_json"])["launch_speed"] is True


def test_postcutoff_statcast_values_do_not_claim_pregame_metric_availability(
    source_root: Path,
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]
    optional_fields = (
        "launch_speed",
        "launch_angle",
        "is_barrel",
        "batted_ball_type",
        "is_pull",
        "pitch_type",
        "release_speed",
    )

    def retain_values_only_after_cutoff(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] not in {TARGET_GAME, "765499"}:
            row.update({name: "" for name in optional_fields})
        return row

    _rewrite(statcast, retain_values_only_after_cutoff)
    availability = json.loads(
        _build(source_root).rows[0]["statcast_metric_availability_json"]
    )
    assert all(availability[name] is False for name in optional_fields)


def test_prior_season_rows_do_not_enter_matchup_horizons_and_coverage_is_bound(
    source_root: Path,
) -> None:
    baseline = _build(source_root)
    baseline_row = baseline.rows[0]
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _append_rows(
        statcast,
        [
            _stat_row(
                game_id="700001",
                game_date="2025-06-01",
                completed="2025-06-01T22:00:00Z",
                observed="2025-06-01T22:05:00Z",
                pa_id="prior-season-pa",
                batter_id=BATTER_ID,
                pitcher_id=PITCHER_ID,
                event_type="home_run",
                is_home_run=True,
                pitch_type="CU",
                release_speed=78,
            )
        ],
    )
    expanded = _build(source_root)
    expanded_row = expanded.rows[0]
    matchup_fields = (
        "hitter_vs_pitcher_hand_pa",
        "hitter_vs_pitcher_hand_hr_per_pa",
        "pitcher_vs_batter_hand_batters_faced",
        "pitcher_vs_batter_hand_hr_per_batter_faced",
        "pitcher_pitch_mix_json",
        "pitcher_average_velocity_json",
        "bvp_pa_descriptive",
        "bvp_hr_descriptive",
    )
    assert {name: expanded_row[name] for name in matchup_fields} == {
        name: baseline_row[name] for name in matchup_fields
    }
    coverage = expanded.manifest["source_coverage"]["statcast"]
    assert coverage["game_date_start"] == "2025-06-01"
    assert expanded.manifest["matchup_horizon_policy"] == context.MATCHUP_HORIZON_POLICY
    assert expanded_row["hitter_season_pa"] == baseline_row["hitter_season_pa"]


def test_truncated_source_pack_changes_bound_coverage_not_horizon_policy(
    source_root: Path,
) -> None:
    baseline = _build(source_root)
    statcast = source_root / context.SOURCE_FILES["statcast"]
    columns, rows = _read_csv(statcast)
    _write_csv(statcast, columns, [row for row in rows if row["game_date"] >= "2026-05-25"])
    truncated = _build(source_root)
    assert truncated.manifest["matchup_horizon_policy"] == baseline.manifest[
        "matchup_horizon_policy"
    ]
    assert truncated.manifest["source_coverage"]["statcast"]["game_date_start"] == "2026-05-25"
    assert truncated.rows[0]["hitter_season_pa"] < baseline.rows[0]["hitter_season_pa"]


def test_staggered_market_lineage_preserves_older_best_quote_timestamp(
    source_root: Path,
) -> None:
    market = source_root / context.SOURCE_FILES["market"]

    def make_older_quote_best(row: dict[str, str]) -> dict[str, str]:
        if row["sportsbook"] == "Book A":
            row["american_odds"] = "200"
        return row

    _rewrite(market, make_older_quote_best)
    row = _build(source_root).rows[0]
    assert row["market_best_sportsbook"] == "Book A"
    assert row["market_best_observed_at_utc"] == "2026-06-05T17:40:00Z"
    assert row["market_observed_at_utc"] == "2026-06-05T17:55:00Z"
    lineage = json.loads(row["market_quote_timestamps_json"])
    assert lineage["Book A"]["quote_at_utc"] == "2026-06-05T17:40:00Z"
    assert lineage["Book B"]["captured_at_utc"] == "2026-06-05T17:55:00Z"


def test_boundary_matrix_same_game_midnight_season_opening_and_sparse_samples(
    source_root: Path,
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def make_target_visible(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == TARGET_GAME:
            row["game_completed_at_utc"] = "2026-06-05T17:00:00Z"
            row["provider_published_at_utc"] = "2026-06-05T17:05:00Z"
            row["first_observed_at_utc"] = "2026-06-05T17:07:00Z"
            row["captured_at_utc"] = "2026-06-05T17:10:00Z"
        return row

    _rewrite(statcast, make_target_visible)
    assert _build(source_root).rows[0]["hitter_season_hr"] == 2

    midnight_pack = _make_source_pack(source_root.parent / "midnight")
    statcast = midnight_pack / context.SOURCE_FILES["statcast"]
    _append_rows(
        statcast,
        [
            _stat_row(
                game_id="765421",
                game_date="2026-05-29",
                completed="2026-05-29T18:00:00Z",
                observed="2026-05-29T18:00:00Z",
                pa_id="exact-7d-boundary",
                batter_id=BATTER_ID,
                pitcher_id="600020",
                event_type="walk",
                is_home_run=False,
            )
        ],
    )
    assert _build(midnight_pack).rows[0]["hitter_7d_pa"] == 3

    season_opening = _make_source_pack(source_root.parent / "season-opening")
    statcast = season_opening / context.SOURCE_FILES["statcast"]
    columns, rows = _read_csv(statcast)
    _write_csv(statcast, columns, [row for row in rows if row["game_id"] == TARGET_GAME])
    opening_row = _build(season_opening).rows[0]
    assert opening_row["hitter_stats_available"] is False
    assert opening_row["hitter_season_pa"] is None

    sparse = _make_source_pack(source_root.parent / "sparse")
    statcast = sparse / context.SOURCE_FILES["statcast"]
    columns, rows = _read_csv(statcast)
    _write_csv(statcast, columns, [row for row in rows if row["game_id"] == "765402"])
    sparse_row = _build(sparse).rows[0]
    assert sparse_row["hitter_season_pa"] == 1
    assert sparse_row["pitcher_season_batters_faced"] == 1
    assert sparse_row["hitter_season_hr_per_pa"] == 0.0


def test_exact_midnight_utc_cutoff_is_inclusive(source_root: Path) -> None:
    midnight = "2026-06-06T00:00:00Z"
    candidates = source_root / context.SOURCE_FILES["candidates"]
    _rewrite(
        candidates,
        lambda row: {
            **row,
            "commence_time_utc": "2026-06-06T01:00:00Z",
            "candidate_universe_cutoff_utc": midnight,
        },
    )
    weather = source_root / context.SOURCE_FILES["weather"]
    _rewrite(weather, lambda row: {**row, "valid_for_utc": "2026-06-06T01:00:00Z"})
    statcast = source_root / context.SOURCE_FILES["statcast"]
    _append_rows(
        statcast,
        [
            _stat_row(
                game_id="765422",
                game_date=OPERATING_DATE,
                completed=midnight,
                observed=midnight,
                pa_id="midnight-inclusive-pa",
                batter_id=BATTER_ID,
                pitcher_id="600020",
                event_type="walk",
                is_home_run=False,
            )
        ],
    )
    row = _build(source_root, as_of_utc=midnight).rows[0]
    assert row["as_of_utc"] == midnight
    assert row["hitter_season_pa"] == 7
    assert json.loads(row["source_max_captured_at_utc_json"])["statcast"] == midnight


def test_pitcher_contact_semantics_invalid_lineup_and_park_linkage(source_root: Path) -> None:
    row = _build(source_root).rows[0]
    assert row["pitcher_season_ground_ball_rate"] == 0.5
    assert row["pitcher_season_fly_ball_rate"] == 0.5
    assert row["pitcher_season_xwoba_allowed"] is None
    assert row["pitcher_season_xslg_allowed"] is None

    lineup = source_root / context.SOURCE_FILES["lineups"]
    _rewrite(lineup, lambda item: {**item, "batting_order_position": "10"})
    with pytest.raises(context.ContextFeatureError, match="1 through 9"):
        _build(source_root)

    source_root = _make_source_pack(source_root.parent / "park-mismatch")
    park = source_root / context.SOURCE_FILES["park_factors"]
    _rewrite(park, lambda item: {**item, "venue_id": "wrong-venue"})
    with pytest.raises(context.ContextFeatureError, match="venue_id mismatch"):
        _build(source_root)


def test_actual_source_digest_and_configuration_digest_are_sensitive(
    source_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _build(source_root)
    candidates = source_root / context.SOURCE_FILES["candidates"]
    expected_digest = hashlib.sha256(candidates.read_bytes()).hexdigest()
    artifacts = {
        artifact["source_name"]: artifact
        for artifact in result.manifest["source_artifacts"]
    }
    assert artifacts["candidates"]["sha256"] == expected_digest

    original_configuration_digest = result.manifest["configuration_digest"]
    monkeypatch.setattr(context, "MATCHUP_HORIZON_POLICY", "test-current-season-policy-change")
    changed = _build(source_root)
    assert changed.manifest["configuration_digest"] != original_configuration_digest
    assert changed.rows[0]["configuration_digest"] == changed.manifest["configuration_digest"]


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity"])
def test_all_nonfinite_spellings_fail_closed(source_root: Path, nonfinite: str) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def inject(row: dict[str, str]) -> dict[str, str]:
        if row["game_id"] == "765401":
            row["launch_speed"] = nonfinite
        return row

    _rewrite(statcast, inject)
    with pytest.raises(context.ContextFeatureError, match="must be finite"):
        _build(source_root)


@pytest.mark.parametrize(
    "corrupt_name",
    [context.MANIFEST_FILENAME, context.BUILD_SUMMARY_FILENAME],
)
def test_manifest_and_summary_corruption_are_detected(
    tmp_path: Path, corrupt_name: str
) -> None:
    expected = {
        tmp_path / context.FEATURES_FILENAME: b"features",
        tmp_path / context.MANIFEST_FILENAME: b"manifest",
        tmp_path / context.BUILD_SUMMARY_FILENAME: b"summary",
    }
    for path, payload in expected.items():
        path.write_bytes(payload)
    (tmp_path / corrupt_name).write_bytes(b"corrupted")
    with pytest.raises(context.ContextFeatureError, match="digest mismatch"):
        context._verify_persisted_artifacts(expected)


@pytest.mark.parametrize(
    "corrupt_name",
    [context.MANIFEST_FILENAME, context.BUILD_SUMMARY_FILENAME],
)
def test_publication_corruption_fails_closed_and_removes_output(
    source_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrupt_name: str,
) -> None:
    allowed = tmp_path / "research-root"
    allowed.mkdir()
    output = allowed / f"corrupt-{corrupt_name}"
    original_verify = context._verify_persisted_artifacts

    def corrupt_then_verify(expected: dict[Path, bytes]) -> None:
        corrupt_path = next(path for path in expected if path.name == corrupt_name)
        corrupt_path.write_bytes(b"corrupted-after-publish")
        original_verify(expected)

    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    monkeypatch.setattr(context, "_verify_persisted_artifacts", corrupt_then_verify)
    with pytest.raises(context.ContextFeatureError, match="digest mismatch"):
        _build(source_root, dry_run=False, output_root=output)
    assert not output.exists()


def test_final_source_mutation_fails_closed_and_removes_published_output(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "research-root"
    allowed.mkdir()
    output = allowed / "mutated-build"
    candidates = source_root / context.SOURCE_FILES["candidates"]
    original_writer = context._write_artifacts_atomically

    def mutate_after_publish(**kwargs: object) -> tuple[Path, Path, Path]:
        paths = original_writer(**kwargs)  # type: ignore[arg-type]
        candidates.write_bytes(candidates.read_bytes() + b"\n")
        return paths

    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    monkeypatch.setattr(context, "_write_artifacts_atomically", mutate_after_publish)
    with pytest.raises(context.ContextFeatureError, match="changed during materialization"):
        _build(source_root, dry_run=False, output_root=output)
    assert not output.exists()


def test_publication_preserves_frozen_sources_and_verifies_all_three_artifacts(
    source_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = {path.name: path.read_bytes() for path in source_root.iterdir()}
    allowed = tmp_path / "research-root"
    allowed.mkdir()
    monkeypatch.setattr(context, "CONTEXT_FEATURE_RESEARCH_ROOT", allowed)
    result = _build(source_root, dry_run=False, output_root=allowed / "frozen-build")
    assert before == {path.name: path.read_bytes() for path in source_root.iterdir()}
    expected = {
        result.features_path: result.features_path.read_bytes(),
        result.manifest_path: result.manifest_path.read_bytes(),
        result.summary_path: result.summary_path.read_bytes(),
    }
    context._verify_persisted_artifacts(expected)  # type: ignore[arg-type]


def test_schedule_final_completion_witness_is_point_in_time_safe(
    source_root: Path,
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def use_schedule_witness(
        row: dict[str, str],
    ) -> dict[str, str]:
        row["game_completed_at_utc"] = ""
        row["completion_evidence_type"] = (
            "schedule_final_observation"
        )
        row["completion_witnessed_at_utc"] = (
            row["first_observed_at_utc"]
        )
        return row

    _rewrite(statcast, use_schedule_witness)

    result = _build(source_root)
    row = result.rows[0]

    assert row["hitter_stats_available"] is True
    assert row["pitcher_stats_available"] is True
    assert row["pitcher_history_game_count"] == 3

    # Schedule Final is completion evidence, but not an exact
    # final-pitch timestamp.
    assert (
        row["pitcher_history_first_game_completed_at_utc"]
        is None
    )
    assert (
        row["pitcher_history_last_game_completed_at_utc"]
        is None
    )

    assert (
        row["pitcher_history_max_available_at_utc"]
        is not None
    )
    assert (
        row["pitcher_history_max_first_observed_at_utc"]
        is not None
    )
    assert (
        row["pitcher_history_max_captured_at_utc"]
        is not None
    )

    coverage = result.manifest["source_coverage"]["statcast"]

    assert coverage["available"] is True
    assert coverage["completed_at_start_utc"] is None
    assert coverage["completed_at_end_utc"] is None



def test_schedule_final_evidence_requires_witness(
    source_root: Path,
) -> None:
    from courtvision.sports.mlb.training.hr_context_features import (
        ContextFeatureError,
    )

    statcast = source_root / context.SOURCE_FILES["statcast"]

    def remove_required_witness(
        row: dict[str, str],
    ) -> dict[str, str]:
        row["game_completed_at_utc"] = ""
        row["completion_evidence_type"] = (
            "schedule_final_observation"
        )
        row["completion_witnessed_at_utc"] = ""
        return row

    _rewrite(statcast, remove_required_witness)

    with pytest.raises(
        ContextFeatureError,
        match="requires completion_witnessed_at_utc",
    ):
        _build(source_root)


def test_schedule_final_evidence_cannot_claim_exact_completion(
    source_root: Path,
) -> None:
    from courtvision.sports.mlb.training.hr_context_features import (
        ContextFeatureError,
    )

    statcast = source_root / context.SOURCE_FILES["statcast"]

    def create_conflict(
        row: dict[str, str],
    ) -> dict[str, str]:
        row["completion_evidence_type"] = (
            "schedule_final_observation"
        )
        row["completion_witnessed_at_utc"] = (
            row["first_observed_at_utc"]
        )
        return row

    _rewrite(statcast, create_conflict)

    with pytest.raises(
        ContextFeatureError,
        match="must not claim an exact completion time",
    ):
        _build(source_root)


def test_explicit_pbp_completion_preserves_exact_provenance(
    source_root: Path,
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    def mark_explicit_pbp(
        row: dict[str, str],
    ) -> dict[str, str]:
        row["completion_evidence_type"] = (
            "play_by_play_last_play_end"
        )
        row["completion_witnessed_at_utc"] = (
            row["captured_at_utc"]
        )
        return row

    _rewrite(statcast, mark_explicit_pbp)

    row = _build(source_root).rows[0]

    assert (
        row["pitcher_history_first_game_completed_at_utc"]
        == "2026-05-20T22:00:00Z"
    )
    assert (
        row["pitcher_history_last_game_completed_at_utc"]
        == "2026-06-02T22:00:00Z"
    )


def test_legacy_statcast_without_new_completion_columns_still_loads(
    source_root: Path,
) -> None:
    statcast = source_root / context.SOURCE_FILES["statcast"]

    columns, rows = _read_csv(statcast)

    legacy_columns = tuple(
        name
        for name in columns
        if name
        not in {
            "completion_evidence_type",
            "completion_witnessed_at_utc",
        }
    )

    legacy_rows = [
        {
            name: row.get(name, "")
            for name in legacy_columns
        }
        for row in rows
    ]

    _write_csv(
        statcast,
        legacy_columns,
        legacy_rows,
    )

    row = _build(source_root).rows[0]

    assert row["hitter_stats_available"] is True
    assert row["pitcher_stats_available"] is True
    assert (
        row["pitcher_history_first_game_completed_at_utc"]
        == "2026-05-20T22:00:00Z"
    )
