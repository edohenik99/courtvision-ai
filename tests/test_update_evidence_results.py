from __future__ import annotations

import csv
from pathlib import Path
import socket
import urllib.request

import pytest

from scripts import update_evidence_results as updater
from scripts.init_evidence_ledger import LEDGER_COLUMNS


def _ledger_row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in LEDGER_COLUMNS}
    row.update(
        {
            "trial_id": "nba-forward-2026-01",
            "run_date": "2026-07-07",
            "prediction_date": "2026-07-08",
            "code_sha": "b" * 40,
            "config_hash": "a" * 64,
            "provider_used": "fixture-provider",
            "market": "player_points",
            "player": "Test Player",
            "team": "TOR",
            "opponent": "BOS",
            "game_id": "game-1",
            "selection": "over",
            "line": "24.5",
            "odds": "-110",
            "implied_probability": "0.5238",
            "model_probability": "0.5658",
            "edge": "0.042",
            "confidence": "0.61",
            "kelly_eligible": "true",
            "recommended_units": "0.50",
            "closing_line": "25.5",
            "closing_odds": "-105",
            "created_at": "2026-07-07T12:00:00-04:00",
        }
    )
    row.update(overrides)
    return row


def _result_row(**overrides: str) -> dict[str, str]:
    row = {
        "trial_id": "nba-forward-2026-01",
        "prediction_date": "2026-07-08",
        "market": "player_points",
        "player": "Test Player",
        "selection": "over",
        "line": "24.5",
        "odds": "-110",
        "result": "win",
        "profit_1u": "0.9091",
        "void_reason": "",
        "notes": "graded from official final",
    }
    row.update(overrides)
    return row


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _create_ledger(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    _write_csv(path, LEDGER_COLUMNS, rows or [_ledger_row()])


def _create_input(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    columns = updater.INPUT_REQUIRED_COLUMNS + updater.INPUT_OPTIONAL_COLUMNS
    _write_csv(path, columns, rows or [_result_row()])


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _update(tmp_path: Path, **overrides: object) -> updater.EvidenceResultUpdateResult:
    arguments: dict[str, object] = {
        "ledger_path": tmp_path / "data" / "history" / "evidence_ledger.csv",
        "results_csv": tmp_path / "results.csv",
    }
    arguments.update(overrides)
    return updater.update_evidence_results(**arguments)


def test_valid_result_update_fills_only_grading_fields(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    original = _ledger_row()
    _create_ledger(ledger_path, [original])
    _create_input(
        tmp_path / "results.csv",
        [
            _result_row(
                result="void",
                profit_1u="0",
                void_reason="market cancelled",
            )
        ],
    )

    result = _update(tmp_path)

    updated = _read_rows(ledger_path)[0]
    expected = dict(original)
    expected.update(
        result="void",
        profit_1u="0",
        void_reason="market cancelled",
        notes="graded from official final",
    )
    assert updated == expected
    assert result.updated_count == 1
    assert result.skipped_count == 0
    assert result.unmatched_count == 0


def test_prediction_time_fields_are_unchanged(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    original = _ledger_row()
    _create_ledger(ledger_path, [original])
    _create_input(tmp_path / "results.csv")

    _update(tmp_path)

    updated = _read_rows(ledger_path)[0]
    grading_fields = {"result", "profit_1u", "void_reason", "notes"}
    for column in LEDGER_COLUMNS:
        if column not in grading_fields:
            assert updated[column] == original[column]


def test_closing_line_fields_are_unchanged(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    original = _ledger_row(closing_line="26.0", closing_odds="-120")
    _create_ledger(ledger_path, [original])
    _create_input(tmp_path / "results.csv")

    _update(tmp_path)

    updated = _read_rows(ledger_path)[0]
    assert updated["closing_line"] == "26.0"
    assert updated["closing_odds"] == "-120"


def test_missing_ledger_fails(tmp_path: Path) -> None:
    _create_input(tmp_path / "results.csv")

    with pytest.raises(updater.EvidenceResultUpdateError, match="does not exist"):
        _update(tmp_path)


def test_invalid_ledger_schema_fails_without_writing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("wrong,columns\nsentinel,row\n", encoding="utf-8")
    before = ledger_path.read_bytes()
    _create_input(tmp_path / "results.csv")

    with pytest.raises(updater.EvidenceResultUpdateError, match="wrong schema"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_missing_input_columns_fail(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    input_path = tmp_path / "results.csv"
    _create_ledger(ledger_path)
    columns = tuple(
        column for column in updater.INPUT_REQUIRED_COLUMNS if column != "profit_1u"
    )
    _write_csv(input_path, columns, [])
    before = ledger_path.read_bytes()

    with pytest.raises(updater.EvidenceResultUpdateError, match="profit_1u"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_invalid_result_fails_without_writing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(tmp_path / "results.csv", [_result_row(result="pending")])
    before = ledger_path.read_bytes()

    with pytest.raises(updater.EvidenceResultUpdateError, match="invalid result"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_void_without_void_reason_fails_without_writing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(
        tmp_path / "results.csv",
        [_result_row(result="void", profit_1u="0", void_reason="")],
    )
    before = ledger_path.read_bytes()

    with pytest.raises(updater.EvidenceResultUpdateError, match="no void_reason"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_unmatched_input_fails_by_default_without_writing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(tmp_path / "results.csv", [_result_row(player="Unknown Player")])
    before = ledger_path.read_bytes()

    with pytest.raises(updater.EvidenceResultUpdateError, match="no matching"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_unmatched_input_can_be_allowed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(tmp_path / "results.csv", [_result_row(player="Unknown Player")])
    before = ledger_path.read_bytes()

    result = _update(tmp_path, allow_unmatched=True)

    assert result.updated_count == 0
    assert result.unmatched_count == 1
    assert ledger_path.read_bytes() == before


@pytest.mark.parametrize(
    ("existing_field", "existing_value"),
    [("result", "loss"), ("profit_1u", "-1")],
)
def test_existing_result_or_profit_fails_by_default(
    tmp_path: Path, existing_field: str, existing_value: str
) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path, [_ledger_row(**{existing_field: existing_value})])
    _create_input(tmp_path / "results.csv")
    before = ledger_path.read_bytes()

    with pytest.raises(updater.EvidenceResultUpdateError, match="existing"):
        _update(tmp_path)

    assert ledger_path.read_bytes() == before


def test_existing_result_or_profit_can_be_skipped_with_flag(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(
        ledger_path,
        [_ledger_row(result="loss", profit_1u="-1", notes="original grade")],
    )
    _create_input(tmp_path / "results.csv")
    before = ledger_path.read_bytes()

    result = _update(tmp_path, allow_existing=True)

    assert result.updated_count == 0
    assert result.skipped_count == 1
    assert ledger_path.read_bytes() == before


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(tmp_path / "results.csv")
    before = ledger_path.read_bytes()

    result = _update(tmp_path, dry_run=True)

    assert result.updated_count == 1
    assert result.dry_run is True
    assert ledger_path.read_bytes() == before


def test_script_is_offline_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("evidence result update must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    _create_input(tmp_path / "results.csv")

    result = _update(tmp_path)

    assert result.updated_count == 1


def test_cli_reports_counts_and_ledger_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    input_path = tmp_path / "results.csv"
    _create_ledger(ledger_path)
    _create_input(input_path)

    exit_code = updater.main(
        ["--results-csv", str(input_path), "--dry-run"],
        ledger_path=ledger_path,
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Updated count: 1" in output
    assert "Skipped count: 0" in output
    assert "Unmatched count: 0" in output
    assert f"Ledger path: {ledger_path.resolve()}" in output
