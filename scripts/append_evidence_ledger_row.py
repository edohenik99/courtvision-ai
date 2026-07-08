"""Append one offline prospective recommendation to the evidence ledger."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import csv
from datetime import date, datetime
from pathlib import Path
import re

try:
    from scripts.init_evidence_ledger import LEDGER_COLUMNS
except ModuleNotFoundError:  # Support ``python scripts/append_...py``.
    from init_evidence_ledger import LEDGER_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "history" / "evidence_ledger.csv"

VALID_RESULTS = frozenset({"win", "loss", "push", "void", "pending"})


class EvidenceLedgerAppendError(RuntimeError):
    """Raised when an evidence ledger row cannot be appended safely."""


def _parse_date(value: str, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceLedgerAppendError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        ) from exc
    if parsed.isoformat() != value:
        raise EvidenceLedgerAppendError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        )
    return parsed.isoformat()


def _require_text(value: str, field_name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise EvidenceLedgerAppendError(f"{field_name} is required")
    return cleaned


def _parse_odds(value: str) -> str:
    cleaned = _require_text(value, "odds")
    if re.fullmatch(r"[+-]?\d+", cleaned) is None:
        raise EvidenceLedgerAppendError("odds must be a signed integer")
    return cleaned


def _normalize_kelly_eligible(value: str | bool) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false"}:
        raise EvidenceLedgerAppendError("kelly_eligible must be true or false")
    return normalized


def _normalize_result(value: str | None) -> str:
    if value is None or value == "":
        return ""
    normalized = str(value).strip()
    if normalized not in VALID_RESULTS:
        raise EvidenceLedgerAppendError(
            "result must be one of: " + ", ".join(sorted(VALID_RESULTS))
        )
    return normalized


def _validate_ledger_schema(ledger_path: Path) -> None:
    if not ledger_path.is_file():
        raise EvidenceLedgerAppendError(
            f"evidence ledger does not exist: {ledger_path}"
        )

    try:
        with ledger_path.open("r", encoding="utf-8", newline="") as handle:
            actual_columns = tuple(next(csv.reader(handle), ()))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceLedgerAppendError(
            f"could not read evidence ledger header: {exc}"
        ) from exc

    if actual_columns != LEDGER_COLUMNS:
        raise EvidenceLedgerAppendError(
            "evidence ledger has the wrong schema; "
            f"expected {list(LEDGER_COLUMNS)!r}, got {list(actual_columns)!r}"
        )


def append_evidence_ledger_row(
    *,
    trial_id: str,
    run_date: str,
    prediction_date: str,
    code_sha: str,
    config_hash: str,
    provider_used: str,
    market: str,
    player: str,
    selection: str,
    line: str,
    odds: str,
    edge: str,
    confidence: str,
    kelly_eligible: str | bool,
    recommended_units: str,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    team: str = "",
    opponent: str = "",
    game_id: str = "",
    implied_probability: str = "",
    model_probability: str = "",
    closing_line: str = "",
    closing_odds: str = "",
    result: str = "",
    profit_1u: str = "",
    void_reason: str = "",
    notes: str = "",
) -> dict[str, str]:
    """Validate and append exactly one evidence-ledger row."""

    ledger_path = Path(ledger_path).resolve()
    _validate_ledger_schema(ledger_path)

    row = {
        "trial_id": _require_text(trial_id, "trial_id"),
        "run_date": _parse_date(run_date, "run_date"),
        "prediction_date": _parse_date(prediction_date, "prediction_date"),
        "code_sha": _require_text(code_sha, "code_sha"),
        "config_hash": _require_text(config_hash, "config_hash"),
        "provider_used": _require_text(provider_used, "provider_used"),
        "market": _require_text(market, "market"),
        "player": _require_text(player, "player"),
        "team": str(team),
        "opponent": str(opponent),
        "game_id": str(game_id),
        "selection": _require_text(selection, "selection"),
        "line": _require_text(line, "line"),
        "odds": _parse_odds(odds),
        "implied_probability": str(implied_probability),
        "model_probability": str(model_probability),
        "edge": _require_text(edge, "edge"),
        "confidence": _require_text(confidence, "confidence"),
        "kelly_eligible": _normalize_kelly_eligible(kelly_eligible),
        "recommended_units": _require_text(
            recommended_units, "recommended_units"
        ),
        "closing_line": str(closing_line),
        "closing_odds": str(closing_odds),
        "result": _normalize_result(result),
        "profit_1u": str(profit_1u),
        "void_reason": str(void_reason),
        "notes": str(notes),
        "created_at": datetime.now().astimezone().isoformat(),
    }

    try:
        with ledger_path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=LEDGER_COLUMNS, lineterminator="\n"
            )
            writer.writerow(row)
    except (OSError, csv.Error) as exc:
        raise EvidenceLedgerAppendError(
            f"could not append evidence ledger row: {exc}"
        ) from exc

    return row


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append one offline recommendation row to the evidence ledger."
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--run-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--prediction-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--provider-used", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--player", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--line", required=True)
    parser.add_argument("--odds", required=True)
    parser.add_argument("--edge", required=True)
    parser.add_argument("--confidence", required=True)
    parser.add_argument("--kelly-eligible", required=True, choices=("true", "false"))
    parser.add_argument("--recommended-units", required=True)
    parser.add_argument("--team", default="")
    parser.add_argument("--opponent", default="")
    parser.add_argument("--game-id", default="")
    parser.add_argument("--implied-probability", default="")
    parser.add_argument("--model-probability", default="")
    parser.add_argument("--closing-line", default="")
    parser.add_argument("--closing-odds", default="")
    parser.add_argument("--result", choices=sorted(VALID_RESULTS), default="")
    parser.add_argument("--profit-1u", default="")
    parser.add_argument("--void-reason", default="")
    parser.add_argument("--notes", default="")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        row = append_evidence_ledger_row(**vars(args), ledger_path=ledger_path)
    except EvidenceLedgerAppendError as exc:
        print(f"Status: failed: {exc}")
        return 1

    print(f"Ledger path: {Path(ledger_path).resolve()}")
    print(f"Created at: {row['created_at']}")
    print("Status: appended one row")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
