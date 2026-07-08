"""Fill blank closing-line fields in the offline forward evidence ledger."""

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
INPUT_REQUIRED_COLUMNS = MATCH_COLUMNS + ("closing_line", "closing_odds")
INPUT_OPTIONAL_COLUMNS = ("notes",)
INPUT_ALLOWED_COLUMNS = frozenset(INPUT_REQUIRED_COLUMNS + INPUT_OPTIONAL_COLUMNS)


class EvidenceClosingLineUpdateError(RuntimeError):
    """Raised when closing lines cannot be applied without risking the ledger."""


@dataclass(frozen=True)
class ClosingLineUpdateResult:
    """Summary of one validated closing-line update batch."""

    ledger_path: Path
    updated_count: int
    skipped_count: int
    unmatched_count: int
    dry_run: bool


def _read_ledger(ledger_path: Path) -> list[dict[str, str]]:
    if not ledger_path.is_file():
        raise EvidenceClosingLineUpdateError(
            f"evidence ledger does not exist: {ledger_path}"
        )

    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            actual_columns = tuple(reader.fieldnames or ())
            if actual_columns != LEDGER_COLUMNS:
                raise EvidenceClosingLineUpdateError(
                    "evidence ledger has the wrong schema; "
                    f"expected {list(LEDGER_COLUMNS)!r}, got {list(actual_columns)!r}"
                )
            rows = list(reader)
    except EvidenceClosingLineUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceClosingLineUpdateError(
            f"could not read evidence ledger: {exc}"
        ) from exc

    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise EvidenceClosingLineUpdateError(
            "evidence ledger contains a row that does not match its schema"
        )
    return rows


def _read_closing_lines(closing_lines_csv: Path) -> list[dict[str, str]]:
    if not closing_lines_csv.is_file():
        raise EvidenceClosingLineUpdateError(
            f"closing-lines CSV does not exist: {closing_lines_csv}"
        )

    try:
        with closing_lines_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            columns = tuple(reader.fieldnames or ())
            duplicate_columns = sorted(
                name for name, count in Counter(columns).items() if count > 1
            )
            if duplicate_columns:
                raise EvidenceClosingLineUpdateError(
                    "closing-lines CSV has duplicate columns: "
                    + ", ".join(duplicate_columns)
                )

            missing = [name for name in INPUT_REQUIRED_COLUMNS if name not in columns]
            if missing:
                raise EvidenceClosingLineUpdateError(
                    "closing-lines CSV is missing required columns: "
                    + ", ".join(missing)
                )

            unexpected = [name for name in columns if name not in INPUT_ALLOWED_COLUMNS]
            if unexpected:
                raise EvidenceClosingLineUpdateError(
                    "closing-lines CSV has unsupported columns: "
                    + ", ".join(unexpected)
                )
            rows = list(reader)
    except EvidenceClosingLineUpdateError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceClosingLineUpdateError(
            f"could not read closing-lines CSV: {exc}"
        ) from exc

    cleaned_rows: list[dict[str, str]] = []
    seen_keys: set[tuple[str, ...]] = set()
    for row_number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise EvidenceClosingLineUpdateError(
                f"closing-lines CSV row {row_number} does not match its schema"
            )
        cleaned = {name: value.strip() for name, value in row.items()}
        blank = [name for name in INPUT_REQUIRED_COLUMNS if not cleaned[name]]
        if blank:
            raise EvidenceClosingLineUpdateError(
                f"closing-lines CSV row {row_number} has blank required fields: "
                + ", ".join(blank)
            )
        key = tuple(cleaned[name] for name in MATCH_COLUMNS)
        if key in seen_keys:
            raise EvidenceClosingLineUpdateError(
                f"closing-lines CSV contains a duplicate matching key at row {row_number}"
            )
        seen_keys.add(key)
        cleaned_rows.append(cleaned)
    return cleaned_rows


def _atomic_write_ledger(
    ledger_path: Path, rows: list[dict[str, str]]
) -> None:
    temporary_path: Path | None = None
    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=ledger_path.parent,
            prefix=f".{ledger_path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(
            file_descriptor, "w", encoding="utf-8", newline=""
        ) as handle:
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
        raise EvidenceClosingLineUpdateError(
            f"could not write evidence ledger atomically: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def update_evidence_closing_lines(
    *,
    closing_lines_csv: Path,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    allow_unmatched: bool = False,
    allow_existing: bool = False,
    dry_run: bool = False,
) -> ClosingLineUpdateResult:
    """Validate and fill only blank ``closing_line``/``closing_odds`` cells."""

    ledger_path = Path(ledger_path).resolve()
    closing_lines_csv = Path(closing_lines_csv).resolve()
    ledger_rows = _read_ledger(ledger_path)
    closing_rows = _read_closing_lines(closing_lines_csv)

    ledger_indexes: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(ledger_rows):
        key = tuple(row[name] for name in MATCH_COLUMNS)
        ledger_indexes.setdefault(key, []).append(index)

    updated_count = 0
    skipped_count = 0
    unmatched_count = 0
    errors: list[str] = []

    for input_row_number, closing_row in enumerate(closing_rows, start=2):
        key = tuple(closing_row[name] for name in MATCH_COLUMNS)
        matches = ledger_indexes.get(key, [])
        if not matches:
            unmatched_count += 1
            if not allow_unmatched:
                errors.append(
                    f"closing-lines CSV row {input_row_number} has no matching ledger row"
                )
            continue
        if len(matches) != 1:
            errors.append(
                f"closing-lines CSV row {input_row_number} matches "
                f"{len(matches)} ledger rows; update is ambiguous"
            )
            continue

        ledger_row = ledger_rows[matches[0]]
        if ledger_row["closing_line"].strip() or ledger_row["closing_odds"].strip():
            if allow_existing:
                skipped_count += 1
            else:
                errors.append(
                    f"closing-lines CSV row {input_row_number} matches a ledger row "
                    "with an existing closing_line or closing_odds"
                )
            continue

        ledger_row["closing_line"] = closing_row["closing_line"]
        ledger_row["closing_odds"] = closing_row["closing_odds"]
        updated_count += 1

    if errors:
        raise EvidenceClosingLineUpdateError("; ".join(errors))

    if not dry_run and updated_count:
        _atomic_write_ledger(ledger_path, ledger_rows)

    return ClosingLineUpdateResult(
        ledger_path=ledger_path,
        updated_count=updated_count,
        skipped_count=skipped_count,
        unmatched_count=unmatched_count,
        dry_run=dry_run,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fill blank closing-line fields in the offline evidence ledger."
    )
    parser.add_argument("--closing-lines-csv", required=True, type=Path)
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
        result = update_evidence_closing_lines(
            closing_lines_csv=args.closing_lines_csv,
            ledger_path=ledger_path,
            allow_unmatched=args.allow_unmatched,
            allow_existing=args.allow_existing,
            dry_run=args.dry_run,
        )
    except EvidenceClosingLineUpdateError as exc:
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
