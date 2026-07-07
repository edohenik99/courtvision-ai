"""Initialize the offline NBA forward paper-trial evidence ledger."""

from __future__ import annotations

import csv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "history" / "evidence_ledger.csv"

LEDGER_COLUMNS = (
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
)


class LedgerSchemaError(ValueError):
    """Raised when an existing evidence ledger does not match the contract."""


def _read_header(ledger_path: Path) -> tuple[str, ...]:
    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            return tuple(next(csv.reader(handle), ()))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise LedgerSchemaError(f"could not read existing ledger header: {exc}") from exc


def _validate_existing_ledger(ledger_path: Path) -> None:
    actual_columns = _read_header(ledger_path)
    if actual_columns != LEDGER_COLUMNS:
        raise LedgerSchemaError(
            "existing ledger has the wrong schema; "
            f"expected {list(LEDGER_COLUMNS)!r}, got {list(actual_columns)!r}"
        )


def initialize_evidence_ledger(ledger_path: Path = DEFAULT_LEDGER_PATH) -> bool:
    """Create the ledger exclusively, or validate it if it already exists.

    Returns ``True`` only when this call created the file. No existing file is
    ever opened for writing.
    """

    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with ledger_path.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(LEDGER_COLUMNS)
    except FileExistsError:
        _validate_existing_ledger(ledger_path)
        return False

    return True


def main(ledger_path: Path = DEFAULT_LEDGER_PATH) -> int:
    ledger_path = Path(ledger_path).resolve()
    print(f"Ledger path: {ledger_path}")

    try:
        created = initialize_evidence_ledger(ledger_path)
    except LedgerSchemaError as exc:
        print(f"Status: already existed (invalid schema): {exc}")
        return 1
    except OSError as exc:
        print(f"Status: initialization failed: {exc}")
        return 1

    if created:
        print("Status: created")
    else:
        print("Status: already existed (schema valid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
