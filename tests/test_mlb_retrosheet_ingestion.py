from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

import pytest

from courtvision.sports.mlb.data import retrosheet_ingestion
from courtvision.sports.mlb.data.retrosheet_ingestion import (
    RETROSHEET_SOURCE_NAME,
    RetrosheetIngestionError,
    ingest_local_retrosheet_csvs,
    retrosheet_event_row_to_dict,
    retrosheet_event_row_to_json,
    retrosheet_game_row_to_dict,
    retrosheet_game_row_to_json,
)
from courtvision.sports.mlb.data_manifest import validate_source_manifest
from scripts.mlb_ingest_retrosheet import main


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb"
GAMES_FIXTURE = FIXTURE_DIR / "retrosheet_games_sample.csv"
EVENTS_FIXTURE = FIXTURE_DIR / "retrosheet_events_sample.csv"
COLLECTED_AT = datetime(2026, 6, 19, 17, 0, tzinfo=timezone.utc)
FORBIDDEN_FIELDS = {
    "ev",
    "fair_probability",
    "unit_size",
    "staking",
    "kelly",
    "bankroll",
    "elite",
    "strong",
    "recommendation",
}


def _ingest(**kwargs: object):
    return ingest_local_retrosheet_csvs(
        games_csv=GAMES_FIXTURE,
        events_csv=EVENTS_FIXTURE,
        collected_at=COLLECTED_AT,
        **kwargs,
    )


def test_game_and_event_fixtures_parse_with_explicit_labels() -> None:
    result = _ingest()

    assert len(result.games) == 4
    assert len(result.events) == 2
    assert result.games[0].game_number == 1
    assert result.games[0].home_score == 5
    assert result.events[0].is_home_run is True
    assert result.events[0].rbi == 2
    assert result.events[1].is_home_run is False


def test_game_statuses_fail_closed() -> None:
    completed, postponed, suspended, unknown = _ingest().games

    assert completed.game_status == "completed"
    assert completed.is_completed is True
    assert postponed.game_status == "postponed"
    assert postponed.is_completed is False
    assert suspended.game_status == "suspended"
    assert suspended.is_completed is False
    assert unknown.game_status == "unknown"
    assert unknown.is_completed is False


def test_unrecognized_game_status_normalizes_to_unknown(tmp_path: Path) -> None:
    path = tmp_path / "games.csv"
    path.write_text(
        GAMES_FIXTURE.read_text(encoding="utf-8").replace(
            "completed,historical", "final-ish,historical", 1
        ),
        encoding="utf-8",
    )

    result = ingest_local_retrosheet_csvs(
        games_csv=path, collected_at=COLLECTED_AT
    )

    assert result.games[0].game_status == "unknown"
    assert result.games[0].is_completed is False


@pytest.mark.parametrize(
    ("fixture", "kind", "column"),
    [
        (GAMES_FIXTURE, "games", "home_team"),
        (EVENTS_FIXTURE, "events", "batter_id"),
    ],
)
def test_required_columns_are_enforced(
    tmp_path: Path, fixture: Path, kind: str, column: str
) -> None:
    lines = fixture.read_text(encoding="utf-8").splitlines()
    headers = lines[0].split(",")
    remove_index = headers.index(column)
    rewritten = []
    for line in lines:
        values = line.split(",")
        rewritten.append(",".join(values[:remove_index] + values[remove_index + 1 :]))
    path = tmp_path / f"{kind}.csv"
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    kwargs = {f"{kind}_csv": path, "collected_at": COLLECTED_AT}
    with pytest.raises(RetrosheetIngestionError, match="missing required columns"):
        ingest_local_retrosheet_csvs(**kwargs)


@pytest.mark.parametrize(
    ("fixture", "kind"),
    [(GAMES_FIXTURE, "games"), (EVENTS_FIXTURE, "events")],
)
def test_invalid_dates_fail_with_row_context(
    tmp_path: Path, fixture: Path, kind: str
) -> None:
    path = tmp_path / f"invalid-{kind}.csv"
    path.write_text(
        fixture.read_text(encoding="utf-8").replace(
            "2025-04-01", "04/01/2025", 1
        ),
        encoding="utf-8",
    )

    kwargs = {f"{kind}_csv": path, "collected_at": COLLECTED_AT}
    with pytest.raises(RetrosheetIngestionError, match=r"row 2: game_date"):
        ingest_local_retrosheet_csvs(**kwargs)


def test_blank_home_run_label_uses_documented_event_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.csv"
    text = EVENTS_FIXTURE.read_text(encoding="utf-8")
    path.write_text(text.replace(",true,2,historical", ",,2,historical", 1))

    result = ingest_local_retrosheet_csvs(
        events_csv=path, collected_at=COLLECTED_AT
    )

    assert result.events[0].event_type == "home_run"
    assert result.events[0].is_home_run is True


def test_serialization_is_deterministic_and_contains_no_decision_fields() -> None:
    result = _ingest()
    game = result.games[0]
    event = result.events[0]

    assert retrosheet_game_row_to_json(game) == retrosheet_game_row_to_json(game)
    assert retrosheet_event_row_to_json(event) == retrosheet_event_row_to_json(event)
    assert json.loads(retrosheet_game_row_to_json(game)) == (
        retrosheet_game_row_to_dict(game)
    )
    assert json.loads(retrosheet_event_row_to_json(event)) == (
        retrosheet_event_row_to_dict(event)
    )
    assert FORBIDDEN_FIELDS.isdisjoint(retrosheet_game_row_to_dict(game))
    assert FORBIDDEN_FIELDS.isdisjoint(retrosheet_event_row_to_dict(event))


def test_manifest_uses_phase3b_contract_and_local_checksums() -> None:
    result = _ingest()
    manifest = result.manifest

    assert validate_source_manifest(manifest).is_valid
    assert manifest.source_name == RETROSHEET_SOURCE_NAME
    assert manifest.source_type == "historical"
    assert manifest.data_domain == "retrosheet"
    assert manifest.date_range_start == date(2025, 4, 1)
    assert manifest.date_range_end == date(2025, 4, 3)
    assert manifest.as_of_date == date(2025, 4, 3)
    assert manifest.row_count == 6
    assert manifest.file_count == 2
    assert manifest.normalized_path is None
    assert manifest.files[0].checksum == hashlib.sha256(
        GAMES_FIXTURE.read_bytes()
    ).hexdigest()
    assert manifest.files[1].checksum == hashlib.sha256(
        EVENTS_FIXTURE.read_bytes()
    ).hexdigest()


def test_manifest_calls_phase3b_checksum_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    real_helper = retrosheet_ingestion.compute_file_sha256

    def recording_helper(path: str | Path) -> str:
        calls.append(Path(path))
        return real_helper(path)

    monkeypatch.setattr(
        retrosheet_ingestion, "compute_file_sha256", recording_helper
    )

    _ingest()

    assert calls == [GAMES_FIXTURE.resolve(), EVENTS_FIXTURE.resolve()]


def test_as_of_date_cannot_predate_latest_input() -> None:
    with pytest.raises(RetrosheetIngestionError, match="latest game_date"):
        _ingest(as_of_date=date(2025, 4, 2))


def test_dry_run_api_and_cli_create_no_output_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_dir = tmp_path / "dry-run-output"

    result = _ingest(output_dir=output_dir)
    exit_code = main(
        [
            "--games-csv",
            str(GAMES_FIXTURE),
            "--events-csv",
            str(EVENTS_FIXTURE),
            "--out-dir",
            str(output_dir),
            "--dry-run",
        ]
    )

    assert result.normalized_game_output_path is None
    assert result.normalized_event_output_path is None
    assert result.manifest_output_path is None
    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry-run"
    assert not output_dir.exists()


def test_explicit_outputs_are_confined_to_temp_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "explicit-output"

    result = _ingest(
        output_dir=output_dir,
        write_raw=True,
        write_normalized=True,
        write_manifest_file=True,
    )

    paths = (
        result.raw_game_output_path,
        result.raw_event_output_path,
        result.normalized_game_output_path,
        result.normalized_event_output_path,
        result.manifest_output_path,
    )
    assert all(path is not None and path.is_file() for path in paths)
    assert all(
        output_dir.resolve() in path.parents for path in paths if path is not None
    )
    assert result.raw_game_output_path.read_bytes() == GAMES_FIXTURE.read_bytes()
    assert result.raw_event_output_path.read_bytes() == EVENTS_FIXTURE.read_bytes()
    assert len(
        result.normalized_game_output_path.read_text(encoding="utf-8").splitlines()
    ) == 4
    assert len(
        result.normalized_event_output_path.read_text(encoding="utf-8").splitlines()
    ) == 2


def test_local_ingestion_has_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)

    _ingest()

    assert called is False
