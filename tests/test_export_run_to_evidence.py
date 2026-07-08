from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import socket
import urllib.request

import pytest

from scripts.export_run_to_evidence import (
    EvidenceExportError,
    export_run_to_evidence,
)
from scripts.init_evidence_daily_manifest import (
    MANIFEST_COLUMNS,
    initialize_evidence_daily_manifest,
)
from scripts.init_evidence_ledger import LEDGER_COLUMNS, initialize_evidence_ledger


PREDICTION_DATE = "2026-10-20"


def _write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _recommendations() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    elite = [
        {
            "player_name": "Alpha Guard",
            "market_type": "player_points",
            "team": "TOR",
            "opponent": "BOS",
            "game_id": "TOR-BOS",
            "selection": "over",
            "sportsbook_line": "21.5",
            "odds": "-110",
            "implied_probability": "0.5238",
            "model_probability": "0.61",
            "side_edge_pct": "0.0862",
            "confidence": "0.72",
            "provider_used": "fixture_book",
        },
        {
            "player_name": "Beta Wing",
            "market_type": "player_rebounds",
            "team": "BOS",
            "opponent": "TOR",
            "game_id": "TOR-BOS",
            "selection": "under",
            "sportsbook_line": "7.5",
            "odds": "+105",
            "implied_probability": "0.4878",
            "model_probability": "0.57",
            "side_edge_pct": "0.0822",
            "confidence": "0.68",
            "provider_used": "fixture_book",
        },
    ]
    kelly = [
        {
            "player_name": row["player_name"],
            "market_type": row["market_type"],
            "selection": row["selection"],
            "line": row["sportsbook_line"],
            "american_odds": row["odds"],
            "eligible": "True",
            "recommended_units": units,
        }
        for row, units in zip(elite, ("0.50", "0.25"), strict=True)
    ]
    return elite, kelly


@pytest.fixture
def evidence_run(tmp_path: Path) -> dict[str, Path]:
    repo_root = tmp_path
    runtime_root = repo_root / "outputs" / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    logs = runtime_root / "logs"
    operator.mkdir(parents=True)
    diagnostics.mkdir(parents=True)
    logs.mkdir(parents=True)

    board_columns = [
        "player_name",
        "market_type",
        "team",
        "opponent",
        "game_id",
        "selection",
        "sportsbook_line",
        "odds",
        "implied_probability",
        "model_probability",
        "side_edge_pct",
        "confidence",
        "provider_used",
    ]
    kelly_columns = [
        "player_name",
        "market_type",
        "selection",
        "line",
        "american_odds",
        "eligible",
        "recommended_units",
    ]
    _write_csv(
        operator / f"full_market_board_{PREDICTION_DATE}.csv", [], board_columns
    )
    _write_csv(operator / f"elite_board_{PREDICTION_DATE}.csv", [], board_columns)
    _write_csv(operator / f"kelly_stakes_{PREDICTION_DATE}.csv", [], kelly_columns)
    (operator / f"operator_card_{PREDICTION_DATE}.txt").write_text(
        "run_health: NO_BET\nfinal_decision: NO BET\ngames count: 2\n",
        encoding="utf-8",
    )
    (diagnostics / f"completion_state_audit_{PREDICTION_DATE}.json").write_text(
        json.dumps({"report_agreement_status": "COMPLETE"}), encoding="utf-8"
    )
    (diagnostics / f"artifact_manifest_{PREDICTION_DATE}.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8"
    )
    (logs / f"run_today_{PREDICTION_DATE}.log").write_text(
        "run complete\n", encoding="utf-8"
    )
    (logs / f"validation_{PREDICTION_DATE}.log").write_text(
        "validation complete\n", encoding="utf-8"
    )
    (logs / f"grading_{PREDICTION_DATE}.log").write_text(
        "grading complete\n", encoding="utf-8"
    )

    manifest_path = repo_root / "data" / "history" / "evidence_daily_manifest.csv"
    ledger_path = repo_root / "data" / "history" / "evidence_ledger.csv"
    initialize_evidence_daily_manifest(manifest_path)
    initialize_evidence_ledger(ledger_path)
    return {
        "repo_root": repo_root,
        "runtime_root": runtime_root,
        "manifest_path": manifest_path,
        "ledger_path": ledger_path,
    }


def _export(paths: dict[str, Path], **overrides: object):
    arguments: dict[str, object] = {
        "trial_id": "nba-forward-2026-01",
        "prediction_date": PREDICTION_DATE,
        "config_hash": "config-sha256",
        "run_date": "2026-10-20",
        "code_sha": "a" * 40,
        **paths,
    }
    arguments.update(overrides)
    return export_run_to_evidence(**arguments)


def _seed_recommendations(paths: dict[str, Path]) -> None:
    elite, kelly = _recommendations()
    operator = paths["runtime_root"] / "operator"
    _write_csv(
        operator / f"elite_board_{PREDICTION_DATE}.csv",
        elite,
        list(elite[0]),
    )
    _write_csv(
        operator / f"kelly_stakes_{PREDICTION_DATE}.csv",
        kelly,
        list(kelly[0]),
    )


def test_dry_run_writes_nothing(evidence_run: dict[str, Path]) -> None:
    _seed_recommendations(evidence_run)
    manifest_before = evidence_run["manifest_path"].read_bytes()
    ledger_before = evidence_run["ledger_path"].read_bytes()

    result = _export(evidence_run, dry_run=True)

    assert result.dry_run is True
    assert len(result.ledger_rows) == 2
    assert evidence_run["manifest_path"].read_bytes() == manifest_before
    assert evidence_run["ledger_path"].read_bytes() == ledger_before


@pytest.mark.parametrize("missing_name", ["manifest_path", "ledger_path"])
def test_missing_evidence_csv_fails(
    evidence_run: dict[str, Path], missing_name: str
) -> None:
    evidence_run[missing_name].unlink()

    with pytest.raises(EvidenceExportError, match="does not exist"):
        _export(evidence_run)


@pytest.mark.parametrize(
    ("invalid_name", "columns"),
    [("manifest_path", MANIFEST_COLUMNS), ("ledger_path", LEDGER_COLUMNS)],
)
def test_invalid_schema_fails(
    evidence_run: dict[str, Path], invalid_name: str, columns: tuple[str, ...]
) -> None:
    evidence_run[invalid_name].write_text(
        ",".join(columns[:-1]) + "\n", encoding="utf-8"
    )

    with pytest.raises(EvidenceExportError, match="wrong schema"):
        _export(evidence_run)


def test_no_pick_run_appends_only_daily_manifest(
    evidence_run: dict[str, Path],
) -> None:
    result = _export(evidence_run)

    manifest_rows = _read_rows(evidence_run["manifest_path"])
    assert len(manifest_rows) == 1
    assert manifest_rows[0]["run_status"] == "no_picks"
    assert manifest_rows[0]["released_recommendation_count"] == "0"
    assert _read_rows(evidence_run["ledger_path"]) == []
    assert result.ledger_rows == ()


def test_ambiguous_zero_pick_artifacts_do_not_overclaim_success(
    evidence_run: dict[str, Path],
) -> None:
    operator = evidence_run["runtime_root"] / "operator"
    diagnostics = evidence_run["runtime_root"] / "diagnostics"
    logs = evidence_run["runtime_root"] / "logs"
    (operator / f"operator_card_{PREDICTION_DATE}.txt").write_text(
        "artifact retained\n", encoding="utf-8"
    )
    (diagnostics / f"completion_state_audit_{PREDICTION_DATE}.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (diagnostics / f"artifact_manifest_{PREDICTION_DATE}.json").write_text(
        "{}\n", encoding="utf-8"
    )
    for name in ("run_today", "validation", "grading"):
        (logs / f"{name}_{PREDICTION_DATE}.log").write_text(
            "artifact retained\n", encoding="utf-8"
        )

    result = _export(evidence_run)

    assert result.manifest_row["run_status"] == "failed_other"
    assert "unambiguous successful-run signal" in result.manifest_row["failure_reason"]


def test_zero_game_operator_card_infers_no_slate(
    evidence_run: dict[str, Path],
) -> None:
    operator_card = (
        evidence_run["runtime_root"]
        / "operator"
        / f"operator_card_{PREDICTION_DATE}.txt"
    )
    operator_card.write_text(
        "run_health: NO_BET\nfinal_decision: NO BET\ngames count: 0\n",
        encoding="utf-8",
    )

    result = _export(evidence_run)

    assert result.manifest_row["run_status"] == "no_slate"
    assert result.manifest_row["released_recommendation_count"] == "0"


def test_recommendation_run_appends_manifest_and_ledger_rows(
    evidence_run: dict[str, Path],
) -> None:
    _seed_recommendations(evidence_run)

    result = _export(evidence_run)

    manifest = _read_rows(evidence_run["manifest_path"])[0]
    ledger = _read_rows(evidence_run["ledger_path"])
    assert manifest["run_status"] == "complete"
    assert manifest["released_recommendation_count"] == "2"
    assert manifest["provider_used"] == "fixture_book"
    assert len(ledger) == 2
    assert ledger[0]["player"] == "Alpha Guard"
    assert ledger[0]["market"] == "player_points"
    assert ledger[0]["line"] == "21.5"
    assert ledger[0]["odds"] == "-110"
    assert ledger[0]["kelly_eligible"] == "true"
    assert ledger[0]["recommended_units"] == "0.50"
    assert len(result.ledger_rows) == 2


def test_artifact_sha256_values_are_recorded(
    evidence_run: dict[str, Path],
) -> None:
    result = _export(evidence_run)
    source = (
        evidence_run["runtime_root"]
        / "operator"
        / f"full_market_board_{PREDICTION_DATE}.csv"
    )

    assert result.manifest_row["source_board_path"] == source.relative_to(
        evidence_run["repo_root"]
    ).as_posix()
    assert result.manifest_row["source_board_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    for field in (
        "source_board",
        "elite_board",
        "kelly_artifact",
        "operator_card",
        "completion_audit",
        "artifact_manifest",
        "run_log",
        "validation_log",
        "grading_log",
    ):
        assert len(result.manifest_row[f"{field}_sha256"]) == 64


def test_duplicate_daily_manifest_fails(evidence_run: dict[str, Path]) -> None:
    _export(evidence_run)

    with pytest.raises(EvidenceExportError, match="duplicate daily manifest"):
        _export(evidence_run)

    assert len(_read_rows(evidence_run["manifest_path"])) == 1


def test_duplicate_ledger_rows_fail_before_writing_manifest(
    evidence_run: dict[str, Path],
) -> None:
    _seed_recommendations(evidence_run)
    _export(evidence_run)

    with pytest.raises(EvidenceExportError, match="duplicate evidence ledger"):
        _export(evidence_run, allow_duplicate_manifest=True)

    assert len(_read_rows(evidence_run["manifest_path"])) == 1
    assert len(_read_rows(evidence_run["ledger_path"])) == 2


def test_allow_duplicate_flags_work(evidence_run: dict[str, Path]) -> None:
    _seed_recommendations(evidence_run)
    _export(evidence_run)

    _export(
        evidence_run,
        allow_duplicate_manifest=True,
        allow_duplicates=True,
    )

    assert len(_read_rows(evidence_run["manifest_path"])) == 2
    assert len(_read_rows(evidence_run["ledger_path"])) == 4


def test_missing_artifacts_fail_unless_explicitly_allowed(
    evidence_run: dict[str, Path],
) -> None:
    grading_log = (
        evidence_run["runtime_root"]
        / "logs"
        / f"grading_{PREDICTION_DATE}.log"
    )
    grading_log.unlink()

    with pytest.raises(EvidenceExportError, match="grading_log"):
        _export(evidence_run)

    result = _export(evidence_run, allow_missing_artifacts=True)
    assert result.manifest_row["run_status"] == "failed_other"
    assert result.manifest_row["grading_log_path"] == ""
    assert result.manifest_row["grading_log_sha256"] == ""
    assert "grading_log" in result.manifest_row["notes"]


def test_export_does_not_use_live_network(
    evidence_run: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)

    result = _export(evidence_run, dry_run=True)
    assert result.manifest_row["released_recommendation_count"] == "0"
