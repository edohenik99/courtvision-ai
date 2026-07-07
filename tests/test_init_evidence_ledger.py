from __future__ import annotations

import csv
from pathlib import Path
import socket
import urllib.request

from scripts import init_evidence_ledger as initializer


EXPECTED_COLUMNS = [
    "trial_id",
    "run_date",
    "prediction_date",
    "code_sha",
    "config_hash",
    "provider_used",
    "market",
    "player",
    "team",
    "opponent",
    "game_id",
    "selection",
    "line",
    "odds",
    "implied_probability",
    "model_probability",
    "edge",
    "confidence",
    "kelly_eligible",
    "recommended_units",
    "closing_line",
    "closing_odds",
    "result",
    "profit_1u",
    "void_reason",
    "notes",
    "created_at",
]


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def test_ledger_is_created_with_exact_expected_columns(tmp_path: Path) -> None:
    ledger_path = tmp_path / "data" / "history" / "evidence_ledger.csv"

    assert initializer.initialize_evidence_ledger(ledger_path) is True

    assert _read_header(ledger_path) == EXPECTED_COLUMNS
    assert ledger_path.read_text(encoding="utf-8").count("\n") == 1


def test_parent_directory_is_created(tmp_path: Path) -> None:
    ledger_path = tmp_path / "missing" / "nested" / "evidence_ledger.csv"
    assert not ledger_path.parent.exists()

    initializer.initialize_evidence_ledger(ledger_path)

    assert ledger_path.parent.is_dir()
    assert ledger_path.is_file()


def test_existing_valid_ledger_is_not_overwritten(tmp_path: Path) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    original = ",".join(EXPECTED_COLUMNS) + "\ntrial-1,existing-row\n"
    ledger_path.write_text(original, encoding="utf-8")
    before = ledger_path.read_bytes()

    assert initializer.initialize_evidence_ledger(ledger_path) is False

    assert ledger_path.read_bytes() == before


def test_existing_invalid_schema_fails_safely(tmp_path: Path, capsys) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    ledger_path.write_text("wrong,columns\nsentinel,data\n", encoding="utf-8")
    before = ledger_path.read_bytes()

    assert initializer.main(ledger_path) != 0

    assert ledger_path.read_bytes() == before
    output = capsys.readouterr().out
    assert str(ledger_path.resolve()) in output
    assert "invalid schema" in output


def test_script_is_offline_safe(tmp_path: Path, monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("evidence ledger initialization must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    ledger_path = tmp_path / "evidence_ledger.csv"
    assert initializer.main(ledger_path) == 0
    assert ledger_path.is_file()


def test_script_exits_successfully_when_ledger_already_exists(
    tmp_path: Path, capsys
) -> None:
    ledger_path = tmp_path / "evidence_ledger.csv"
    assert initializer.main(ledger_path) == 0
    first_bytes = ledger_path.read_bytes()
    capsys.readouterr()

    assert initializer.main(ledger_path) == 0

    assert ledger_path.read_bytes() == first_bytes
    output = capsys.readouterr().out
    assert str(ledger_path.resolve()) in output
    assert "already existed (schema valid)" in output
