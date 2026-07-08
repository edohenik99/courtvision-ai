"""Fill blank grading fields in the offline forward evidence ledger."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
import csv
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile

try:
    from scripts.init_evidence_ledger import LEDGER_COLUMNS
except ModuleNotFoundError:  # Support ``python scripts/update_...py``.
    from init_evidence_ledger import LEDGER_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "history" / "evidence_ledger.csv"

MATCH_COLUMNS = (
    "trial_id",
    "prediction_date",
    "market",
    "player",
    "selection",
    "line",
    "odds",
)
INPUT_REQUIRED_COLUMNS = MATCH_COLUMNS + ("result", "profit_1u")
INPUT_OPTIONAL_COLUMNS = ("void_reason", "notes")
INPUT_ALLOWED_COLUMNS = frozenset(INPUT_REQUIRED_COLUMNS + INPUT_OPTIONAL_COLUMNS)
VALID_RESULTS = frozenset({"win", "loss", "push", "void"})


class EvidenceResultUpdateError(RuntimeError):
    """Raised when results cannot be applied without risking the ledger."""


@dataclass(frozen=True)
class EvidenceResultUpdateResult:
    """Summary of one validated evidence-result update batch."""

    ledger_path: Path
    updated_count: int
    skipped_count: int
    unmatched_count: int
    dry_run: bool


def _read_ledger(ledger_path: Path) -> list[dict[str, str]]:
    if not ledger_path.is_file():
        raise EvidenceResultUpdateError(
            f"evidence ledger does not exist: {ledger_path}"
        )

    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_columns = tuple(reader.fieldnames or ())
            if actual_columns != LEDGER_COLUMNS:
                raise EvidenceResultUpdateError(
                    "evidence ledger has the wrong schema; "
                    f"expected {list(LEDGER_COLUMNS)!r}, got {list(actual_columns)!r}"
                )
            rows = list(reader)
    except EvidenceResultUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceResultUpdateError(
            f"could not read evidence ledger: {exc}"
        ) from exc

    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise EvidenceResultUpdateError(
            "evidence ledger contains a row that does not match its schema"
        )
    return rows


def _read_results(results_csv: Path) -> list[dict[str, str]]:
    if not results_csv.is_file():
        raise EvidenceResultUpdateError(f"results CSV does not exist: {results_csv}")

    try:
        with results_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            columns = tuple(reader.fieldnames or ())
            duplicate_columns = sorted(
                name for name, count in Counter(columns).items() if count > 1
            )
            if duplicate_columns:
                raise EvidenceResultUpdateError(
                    "results CSV has duplicate columns: " + ", ".join(duplicate_columns)
                )

            missing = [name for name in INPUT_REQUIRED_COLUMNS if name not in columns]
            if missing:
                raise EvidenceResultUpdateError(
                    "results CSV is missing required columns: " + ", ".join(missing)
                )

            unexpected = [name for name in columns if name not in INPUT_ALLOWED_COLUMNS]
            if unexpected:
                raise EvidenceResultUpdateError(
                    "results CSV has unsupported columns: " + ", ".join(unexpected)
                )
            rows = list(reader)
    except EvidenceResultUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceResultUpdateError(f"could not read results CSV: {exc}") from exc

    cleaned_rows: list[dict[str, str]] = []
    seen_keys: set[tuple[str, ...]] = set()
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise EvidenceResultUpdateError(
                f"results CSV row {row_number} does not match its schema"
            )
        cleaned = {name: value.strip() for name, value in row.items()}
        blank = [name for name in INPUT_REQUIRED_COLUMNS if not cleaned[name]]
        if blank:
            raise EvidenceResultUpdateError(
                f"results CSV row {row_number} has blank required fields: "
                + ", ".join(blank)
            )

        result = cleaned["result"]
        if result not in VALID_RESULTS:
            raise EvidenceResultUpdateError(
                f"results CSV row {row_number} has invalid result {result!r}; "
                "expected win, loss, push, or void"
            )
        if result == "void" and not cleaned.get("void_reason", ""):
            raise EvidenceResultUpdateError(
                f"results CSV row {row_number} is void but has no void_reason"
            )

        key = tuple(cleaned[name] for name in MATCH_COLUMNS)
        if key in seen_keys:
            raise EvidenceResultUpdateError(
                f"results CSV contains a duplicate matching key at row {row_number}"
            )
        seen_keys.add(key)
        cleaned_rows.append(cleaned)
    return cleaned_rows


def _atomic_write_ledger(ledger_path: Path, rows: list[dict[str, str]]) -> None:
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=ledger_path.parent,
            prefix=f".{ledger_path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=LEDGER_COLUMNS, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, ledger_path.stat().st_mode)
        os.replace(temporary_path, ledger_path)
        temporary_path = None
    except (OSError, csv.Error) as exc:
        raise EvidenceResultUpdateError(
            f"could not write evidence ledger atomically: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def update_evidence_results(
    *,
    results_csv: Path,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    allow_unmatched: bool = False,
    allow_existing: bool = False,
    dry_run: bool = False,
) -> EvidenceResultUpdateResult:
    """Validate and fill grading fields without changing prediction evidence."""

    ledger_path = Path(ledger_path).resolve()
    results_csv = Path(results_csv).resolve()
    ledger_rows = _read_ledger(ledger_path)
    result_rows = _read_results(results_csv)

    ledger_indexes: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(ledger_rows):
        key = tuple(row[name] for name in MATCH_COLUMNS)
        ledger_indexes.setdefault(key, []).append(index)

    updated_count = 0
    skipped_count = 0
    unmatched_count = 0
    errors: list[str] = []

    for input_row_number, result_row in enumerate(result_rows, start=2):
        key = tuple(result_row[name] for name in MATCH_COLUMNS)
        matches = ledger_indexes.get(key, [])
        if not matches:
            unmatched_count += 1
            if not allow_unmatched:
                errors.append(
                    f"results CSV row {input_row_number} has no matching ledger row"
                )
            continue
        if len(matches) != 1:
            errors.append(
                f"results CSV row {input_row_number} matches "
                f"{len(matches)} ledger rows; update is ambiguous"
            )
            continue

        ledger_row = ledger_rows[matches[0]]
        if ledger_row["result"].strip() or ledger_row["profit_1u"].strip():
            if allow_existing:
                skipped_count += 1
            else:
                errors.append(
                    f"results CSV row {input_row_number} matches a ledger row "
                    "with an existing result or profit_1u"
                )
            continue

        ledger_row["result"] = result_row["result"]
        ledger_row["profit_1u"] = result_row["profit_1u"]
        if result_row.get("void_reason", ""):
            ledger_row["void_reason"] = result_row["void_reason"]
        if result_row.get("notes", ""):
            ledger_row["notes"] = result_row["notes"]
        updated_count += 1

    if errors:
        raise EvidenceResultUpdateError("; ".join(errors))

    if not dry_run and updated_count:
        _atomic_write_ledger(ledger_path, ledger_rows)

    return EvidenceResultUpdateResult(
        ledger_path=ledger_path,
        updated_count=updated_count,
        skipped_count=skipped_count,
        unmatched_count=unmatched_count,
        dry_run=dry_run,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fill blank grading fields in the offline evidence ledger."
    )
    parser.add_argument("--results-csv", required=True, type=Path)
    parser.add_argument("--allow-unmatched", action="store_true")
    parser.add_argument("--allow-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = update_evidence_results(
            results_csv=args.results_csv,
            ledger_path=ledger_path,
            allow_unmatched=args.allow_unmatched,
            allow_existing=args.allow_existing,
            dry_run=args.dry_run,
        )
    except EvidenceResultUpdateError as exc:
        print(f"Status: failed: {exc}")
        return 1

    print(f"Updated count: {result.updated_count}")
    print(f"Skipped count: {result.skipped_count}")
    print(f"Unmatched count: {result.unmatched_count}")
    print(f"Ledger path: {result.ledger_path}")
    print("Status: dry run; no files written" if result.dry_run else "Status: complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
