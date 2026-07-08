from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import socket
import urllib.request

import pytest

from scripts import append_evidence_ledger_row as appender
from scripts.init_evidence_ledger import LEDGER_COLUMNS


def _create_ledger(path: Path, rows: list[dict[str, str]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows or [])


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _append(ledger_path: Path, **overrides: object) -> dict[str, str]:
    kwargs: dict[str, object] = {
        "trial_id": "nba-forward-2026-01",
        "run_date": "2026-07-07",
        "prediction_date": "2026-07-08",
        "code_sha": "b" * 40,
        "config_hash": "a" * 64,
        "provider_used": "fixture-provider",
        "market": "player_points",
        "player": "Test Player",
        "selection": "over",
        "line": "24.5",
        "odds": "-110",
        "edge": "0.042",
        "confidence": "0.61",
        "kelly_eligible": "true",
        "recommended_units": "0.50",
        "ledger_path": ledger_path,
    }
    kwargs.update(overrides)
    return appender.append_evidence_ledger_row(**kwargs)


def test_valid_row_appends_with_exact_schema(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"
    _create_ledger(ledger_path)

    appended = _append(
        ledger_path,
        team="TOR",
        opponent="BOS",
        game_id="game-1",
        implied_probability="0.5238",
        model_probability="0.5658",
        notes="prospective row",
    )

    header, rows = _read_rows(ledger_path)
    assert header == list(LEDGER_COLUMNS)
    assert rows == [appended]
    assert rows[0]["line"] == "24.5"
    assert rows[0]["closing_line"] == ""
    assert rows[0]["result"] == ""


def test_existing_rows_are_preserved(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    existing = {column: "" for column in LEDGER_COLUMNS}
    existing.update(
        {
            "trial_id": "existing-trial",
            "run_date": "2026-07-06",
            "prediction_date": "2026-07-06",
            "created_at": "2026-07-06T12:00:00-04:00",
        }
    )
    _create_ledger(ledger_path, [existing])
    before = ledger_path.read_bytes()

    _append(ledger_path)

    assert ledger_path.read_bytes().startswith(before)
    _, rows = _read_rows(ledger_path)
    assert len(rows) == 2
    assert rows[0] == existing
    assert rows[1]["trial_id"] == "nba-forward-2026-01"


def test_missing_ledger_fails(tmp_path: Path) -> None:
    ledger_path = tmp_path / "missing.csv"

    with pytest.raises(appender.EvidenceLedgerAppendError, match="does not exist"):
        _append(ledger_path)

    assert not ledger_path.exists()


def test_invalid_ledger_schema_fails_without_modifying_file(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    ledger_path.write_text("wrong,columns\nsentinel,row\n", encoding="utf-8")
    before = ledger_path.read_bytes()

    exit_code = appender.main(
        [
            "--trial-id",
            "nba-forward-2026-01",
            "--run-date",
            "2026-07-07",
            "--prediction-date",
            "2026-07-08",
            "--code-sha",
            "b" * 40,
            "--config-hash",
            "a" * 64,
            "--provider-used",
            "fixture-provider",
            "--market",
            "player_points",
            "--player",
            "Test Player",
            "--selection",
            "over",
            "--line",
            "24.5",
            "--odds=-110",
            "--edge",
            "0.042",
            "--confidence",
            "0.61",
            "--kelly-eligible",
            "true",
            "--recommended-units",
            "0.50",
        ],
        ledger_path=ledger_path,
    )

    assert exit_code != 0
    assert ledger_path.read_bytes() == before


@pytest.mark.parametrize("field_name", ["run_date", "prediction_date"])
def test_invalid_date_fails_without_appending(
    tmp_path: Path, field_name: str
) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    before = ledger_path.read_bytes()

    with pytest.raises(appender.EvidenceLedgerAppendError, match=field_name):
        _append(ledger_path, **{field_name: "2026-02-30"})

    assert ledger_path.read_bytes() == before


def test_invalid_odds_fails_without_appending(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    before = ledger_path.read_bytes()

    with pytest.raises(appender.EvidenceLedgerAppendError, match="odds"):
        _append(ledger_path, odds="-110.5")

    assert ledger_path.read_bytes() == before


def test_invalid_kelly_eligible_fails_without_appending(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    before = ledger_path.read_bytes()

    with pytest.raises(appender.EvidenceLedgerAppendError, match="kelly_eligible"):
        _append(ledger_path, kelly_eligible="yes")

    assert ledger_path.read_bytes() == before


def test_invalid_result_fails_without_appending(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)
    before = ledger_path.read_bytes()

    with pytest.raises(appender.EvidenceLedgerAppendError, match="result"):
        _append(ledger_path, result="cancelled")

    assert ledger_path.read_bytes() == before


def test_created_at_includes_timezone(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)

    row = _append(ledger_path)

    assert datetime.fromisoformat(row["created_at"]).tzinfo is not None


def test_controlled_fields_are_allowed_when_supplied(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)

    row = _append(
        ledger_path,
        closing_line="25.5",
        closing_odds="-105",
        result="win",
        profit_1u="0.9091",
    )

    assert row["closing_line"] == "25.5"
    assert row["closing_odds"] == "-105"
    assert row["result"] == "win"
    assert row["profit_1u"] == "0.9091"


def test_script_is_offline_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("evidence ledger append must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    ledger_path = tmp_path / "evidence_ledger.csv"
    _create_ledger(ledger_path)

    _append(ledger_path)

    assert len(_read_rows(ledger_path)[1]) == 1
