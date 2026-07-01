from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.cli.main import main as cli_main
from courtvision.data_collection.core import CollectionRequest, collect_sources
from courtvision.data_collection.path_guards import ProtectedPathError
from courtvision.sports.mlb.data_collection.hr_odds_archive_collector import (
    HR_ODDS_SCHEMA_VERSION,
    HROddsArchiveCollectionError,
    HROddsArchiveCollector,
    NORMALIZED_HR_ODDS_FILENAME,
    ODDS_VALIDATION_REPORT_FILENAME,
    REQUIRED_ODDS_ARCHIVE_COLUMNS,
)


COLLECTED_AT = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)


def _retrosheet(tmp_path: Path) -> Path:
    path = tmp_path / "retrosheet_games.csv"
    path.write_text(
        "game_id,game_date,home_team,away_team,game_status,source_type\n"
        "20250401TORBOS-1,2025-04-01,TOR,BOS,completed,historical\n"
        "20250402NYYTBR,2025-04-02,NYY,TB,completed,historical\n",
        encoding="utf-8",
    )
    return path


def _row(**changes: str) -> dict[str, str]:
    row = {
        "season": "2025",
        "game_id": "20250401TORBOS-1",
        "game_date": "2025-04-01",
        "player_id": "660271",
        "player_name": "Vladimir Guerrero Jr.",
        "team": "tor",
        "opponent": "bos",
        "sportsbook": "draft kings",
        "market": "home_run",
        "line": "0.5",
        "odds_american": "+350",
        "odds_decimal": "4.5",
        "over_under": "over",
        "collected_at": "2025-04-01T20:00:00Z",
        "event_start_time": "2025-04-01T23:07:00Z",
        "source_name": "licensed_manual_export",
        "source_license": "internal research license",
    }
    row.update(changes)
    return row


def _archive(tmp_path: Path, *rows: dict[str, str]) -> Path:
    path = tmp_path / "approved_hr_odds.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=REQUIRED_ODDS_ARCHIVE_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows or (_row(),))
    return path


def _request(
    tmp_path: Path,
    archive: Path,
    retrosheet: Path,
    *,
    dry_run: bool = False,
    output: Path | None = None,
) -> CollectionRequest:
    return CollectionRequest(
        sport="mlb",
        season=2025,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        output_raw_dir=output or tmp_path / "raw",
        dry_run=dry_run,
        collection_id="v2025-hr-odds-test",
        collection_timestamp=COLLECTED_AT,
        source_options={
            "odds_archive_path": archive,
            "retrosheet_path": retrosheet,
        },
    )


def test_valid_archive_is_normalized_and_summarized(tmp_path: Path) -> None:
    collector = HROddsArchiveCollector.validate(
        _archive(tmp_path),
        _retrosheet(tmp_path),
        requested_season=2025,
    )

    assert len(collector.rows) == 1
    assert collector.rows[0].market == "player_home_runs"
    assert collector.rows[0].sportsbook == "DraftKings"
    assert collector.rows[0].over_under == "OVER"
    assert collector.coverage_summary == {
        "games_with_odds": 1,
        "player_props_count": 1,
        "sportsbooks_count": 1,
        "missing_games_count": 1,
        "coverage_rate": 0.5,
    }


def test_invalid_market_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HROddsArchiveCollectionError, match="approved HR market"):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, _row(market="player_hits")),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


def test_collected_at_after_event_start_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HROddsArchiveCollectionError, match="must be before"):
        HROddsArchiveCollector.validate(
            _archive(
                tmp_path,
                _row(collected_at="2025-04-02T00:00:00Z"),
            ),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


def test_duplicate_rows_are_rejected(tmp_path: Path) -> None:
    row = _row()
    with pytest.raises(HROddsArchiveCollectionError, match="duplicate odds row"):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, row, row.copy()),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


def test_unknown_game_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HROddsArchiveCollectionError, match="unknown game_id"):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, _row(game_id="missing-game")),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"odds_american": "99"}, "odds_american"),
        ({"odds_decimal": "1"}, "odds_decimal"),
        ({"line": "not-numeric"}, "line must be numeric"),
    ],
)
def test_invalid_odds_or_line_is_rejected(
    tmp_path: Path, changes: dict[str, str], message: str
) -> None:
    with pytest.raises(HROddsArchiveCollectionError, match=message):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, _row(**changes)),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


def test_unknown_sportsbook_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HROddsArchiveCollectionError, match="approved allowlist"):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, _row(sportsbook="Mystery Book")),
            _retrosheet(tmp_path),
            requested_season=2025,
        )


def test_cli_dry_run_validates_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _archive(tmp_path)
    retrosheet = _retrosheet(tmp_path)
    output = tmp_path / "raw"

    exit_code = cli_main(
        [
            "collect",
            "mlb",
            "--season",
            "2025",
            "--odds-archive-path",
            str(archive),
            "--retrosheet-path",
            str(retrosheet),
            "--output-raw-dir",
            str(output),
            "--dry-run",
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert "approved_supplied_odds" in summary["planned_sources"]
    assert summary["writes_performed"] is False
    assert not output.exists()


def test_manifest_records_artifact_hashes_counts_and_coverage(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    result = collect_sources(_request(tmp_path, archive, _retrosheet(tmp_path)))
    source_dir = result.collection_dir / "sources" / "approved_supplied_odds"
    normalized = source_dir / NORMALIZED_HR_ODDS_FILENAME
    report_path = source_dir / ODDS_VALIDATION_REPORT_FILENAME

    assert normalized.is_file()
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "valid"
    assert report["schema_version"] == HR_ODDS_SCHEMA_VERSION
    assert report["provenance"]["network_accessed"] is False
    assert report["provenance"]["scraping_performed"] is False

    manifest = json.loads(
        (result.collection_dir / "collection_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["collector_version"] == "1.5.0"
    odds_records = [
        record
        for record in manifest["sources"]
        if record["source_name"] == "approved_supplied_odds"
    ]
    metadata = odds_records[0]["metadata"]
    assert metadata["source_filename"] == archive.name
    assert metadata["source_hash"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert metadata["normalized_file_hash"] == hashlib.sha256(
        normalized.read_bytes()
    ).hexdigest()
    assert metadata["validation_report_hash"] == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    assert metadata["row_counts"] == {"normalized": 1, "rejected": 0, "source": 1}
    assert metadata["coverage_summary"]["coverage_rate"] == 0.5
    assert metadata["schema_version"] == HR_ODDS_SCHEMA_VERSION
    assert metadata["collector_version"] == "1.5.0"


def test_cli_reports_odds_coverage_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli_main(
        [
            "collect",
            "mlb",
            "--season",
            "2025",
            "--odds-archive-path",
            str(_archive(tmp_path)),
            "--retrosheet-path",
            str(_retrosheet(tmp_path)),
            "--output-raw-dir",
            str(tmp_path / "raw"),
            "--collection-id",
            "v2025-cli-coverage",
        ]
    )

    assert exit_code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["odds_coverage_summary"] == {
        "coverage_rate": 0.5,
        "games_with_odds": 1,
        "missing_games_count": 1,
        "player_props_count": 1,
        "sportsbooks_count": 1,
    }


def test_protected_output_path_is_rejected(tmp_path: Path) -> None:
    protected = tmp_path / "outputs"
    with pytest.raises(ProtectedPathError, match="protected path component"):
        collect_sources(
            _request(
                tmp_path,
                _archive(tmp_path),
                _retrosheet(tmp_path),
                output=protected,
            )
        )
    assert not protected.exists()


def test_supplied_crosswalk_enforces_player_resolution(tmp_path: Path) -> None:
    crosswalk = tmp_path / "chadwick.csv"
    crosswalk.write_text(
        "key_person,key_mlbam,key_retro\n"
        "example01,660271,guerv002\n",
        encoding="utf-8",
    )
    valid = HROddsArchiveCollector.validate(
        _archive(tmp_path),
        _retrosheet(tmp_path),
        requested_season=2025,
        crosswalk_path=crosswalk,
    )
    assert valid.resolved_player_count == 1
    assert valid.unresolved_player_count == 0

    with pytest.raises(HROddsArchiveCollectionError, match="does not resolve"):
        HROddsArchiveCollector.validate(
            _archive(tmp_path, _row(player_id="999999")),
            _retrosheet(tmp_path),
            requested_season=2025,
            crosswalk_path=crosswalk,
        )
