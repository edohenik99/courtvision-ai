"""Record operator decision for a manual-review pick.

Reads the daily Kelly stakes CSV, matches the requested pick, and
appends or updates data/history/manual_review_history.csv.

Usage
-----
    py -3.13 scripts/record_manual_review_decision.py \\
        --prediction-date 2026-05-07 \\
        --player-name "Ajay Mitchell" \\
        --market-type player_points \\
        --selection under \\
        --decision skip \\
        --reason "same_opponent_under_warning"

Supported decisions: play, skip, reduce_stake, undecided
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

MANUAL_REVIEW_HISTORY_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player_name",
    "team_abbr",
    "opponent",
    "market_type",
    "selection",
    "line",
    "stake_amount",
    "recommended_action",
    "review_status",
    "stake_policy",
    "operator_action",
    "operator_note",
    "manual_review_required",
    "manual_review_reason",
    "same_opponent_under_warning",
    "same_opponent_warning_reason",
    "decision",
    "decision_reason",
    "created_at",
    "updated_at",
)

VALID_DECISIONS: frozenset[str] = frozenset({"play", "skip", "reduce_stake", "undecided"})

# Fields copied verbatim from the Kelly stakes row into the history record.
_KELLY_PASSTHROUGH_COLUMNS: tuple[str, ...] = (
    "team_abbr",
    "opponent",
    "line",
    "stake_amount",
    "recommended_action",
    "review_status",
    "stake_policy",
    "operator_action",
    "operator_note",
    "manual_review_required",
    "manual_review_reason",
    "same_opponent_under_warning",
    "same_opponent_warning_reason",
)

DEFAULT_HISTORY_PATH = _PROJECT_ROOT / "data" / "history" / "manual_review_history.csv"


def _log(msg: str) -> None:
    print(f"[MANUAL_REVIEW] {msg}", flush=True)


def _dedup_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("prediction_date", "")).strip(),
        str(row.get("player_name", "")).strip().lower(),
        str(row.get("market_type", "")).strip().lower(),
        str(row.get("selection", "")).strip().lower(),
    )


def _read_kelly_stakes(kelly_path: Path) -> list[dict[str, str]]:
    if not kelly_path.exists():
        raise SystemExit(
            f"[MANUAL_REVIEW][FATAL] Kelly stakes file not found: {kelly_path}\n"
            f"        Run: py -3.13 scripts/run_kelly_stakes.py --prediction-date <DATE> first."
        )
    with kelly_path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _find_kelly_row(
    kelly_rows: list[dict[str, str]],
    player_name: str,
    market_type: str,
    selection: str,
) -> dict[str, str] | None:
    target = (player_name.strip().lower(), market_type.strip().lower(), selection.strip().lower())
    for row in kelly_rows:
        candidate = (
            str(row.get("player_name", "")).strip().lower(),
            str(row.get("market_type", "")).strip().lower(),
            str(row.get("selection", "")).strip().lower(),
        )
        if candidate == target:
            return row
    return None


def _read_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return [
            {col: str(row.get(col, "")) for col in MANUAL_REVIEW_HISTORY_COLUMNS}
            for row in reader
        ]


def _write_history(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(MANUAL_REVIEW_HISTORY_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _build_history_row(
    *,
    kelly_row: dict[str, str] | None,
    prediction_date: str,
    player_name: str,
    market_type: str,
    selection: str,
    decision: str,
    decision_reason: str,
    now: str,
    created_at: str = "",
) -> dict[str, str]:
    row: dict[str, str] = {col: "" for col in MANUAL_REVIEW_HISTORY_COLUMNS}
    if kelly_row:
        for col in _KELLY_PASSTHROUGH_COLUMNS:
            row[col] = str(kelly_row.get(col, "") or "")
    row["prediction_date"] = prediction_date
    row["player_name"] = player_name
    row["market_type"] = market_type
    row["selection"] = selection
    row["decision"] = decision
    row["decision_reason"] = decision_reason
    row["created_at"] = created_at or now
    row["updated_at"] = now
    return row


def record_decision(
    *,
    prediction_date: str,
    player_name: str,
    market_type: str,
    selection: str,
    decision: str,
    decision_reason: str = "",
    kelly_path: Path | None = None,
    history_path: Path | None = None,
    _now: str | None = None,
) -> dict[str, str]:
    """Core logic — callable from tests without going through CLI.

    _now is an injection point for tests that need deterministic timestamps.
    """
    if decision not in VALID_DECISIONS:
        raise ValueError(f"Invalid decision {decision!r}. Must be one of: {sorted(VALID_DECISIONS)}")

    operator_dir = _PROJECT_ROOT / "outputs" / "runtime" / "operator"
    resolved_kelly = kelly_path or (operator_dir / f"kelly_stakes_{prediction_date}.csv")
    resolved_history = history_path or DEFAULT_HISTORY_PATH

    kelly_rows = _read_kelly_stakes(resolved_kelly)
    kelly_row = _find_kelly_row(kelly_rows, player_name, market_type, selection)

    if kelly_row is None:
        _log(
            f"WARNING: no matching Kelly row for player={player_name!r} "
            f"market={market_type!r} selection={selection!r} — "
            f"recording with provided fields only."
        )

    now = _now if _now is not None else datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing_rows = _read_history(resolved_history)

    new_key = (prediction_date, player_name.lower(), market_type.lower(), selection.lower())
    updated = False
    result_rows: list[dict[str, str]] = []
    created_at = ""

    for existing in existing_rows:
        if _dedup_key(existing) == new_key:
            created_at = str(existing.get("created_at", "")).strip()
            updated = True
            result_rows.append(
                _build_history_row(
                    kelly_row=kelly_row,
                    prediction_date=prediction_date,
                    player_name=player_name,
                    market_type=market_type,
                    selection=selection,
                    decision=decision,
                    decision_reason=decision_reason,
                    now=now,
                    created_at=created_at,
                )
            )
        else:
            result_rows.append(existing)

    if not updated:
        result_rows.append(
            _build_history_row(
                kelly_row=kelly_row,
                prediction_date=prediction_date,
                player_name=player_name,
                market_type=market_type,
                selection=selection,
                decision=decision,
                decision_reason=decision_reason,
                now=now,
            )
        )

    _write_history(resolved_history, result_rows)

    action = "updated" if updated else "recorded"
    new_row = result_rows[-1] if not updated else next(
        r for r in result_rows if _dedup_key(r) == new_key
    )
    _log(
        f"{action} decision={decision!r} player={player_name!r} "
        f"date={prediction_date} market={market_type} selection={selection}"
    )
    _log(f"history_path={resolved_history}")
    print(
        f"[COUNT] manual_review_history_rows={len(result_rows)} "
        f"action={action}",
        flush=True,
    )
    return new_row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record operator decision for a manual-review Kelly pick."
    )
    parser.add_argument("--prediction-date", required=True, help="Slate date YYYY-MM-DD.")
    parser.add_argument("--player-name", required=True, help="Exact player name.")
    parser.add_argument("--market-type", required=True, help="e.g. player_points.")
    parser.add_argument("--selection", required=True, help="over / under.")
    parser.add_argument(
        "--decision",
        required=True,
        choices=sorted(VALID_DECISIONS),
        help="Operator decision for this pick.",
    )
    parser.add_argument("--reason", default="", help="Free-text reason for the decision.")
    parser.add_argument("--kelly-csv", default=None, help="Override Kelly stakes CSV path.")
    parser.add_argument("--history-csv", default=None, help="Override history CSV path.")
    args = parser.parse_args(argv)

    operator_dir = _PROJECT_ROOT / "outputs" / "runtime" / "operator"
    kelly_path = (
        Path(args.kelly_csv)
        if args.kelly_csv
        else operator_dir / f"kelly_stakes_{args.prediction_date}.csv"
    )
    history_path = Path(args.history_csv) if args.history_csv else DEFAULT_HISTORY_PATH

    record_decision(
        prediction_date=args.prediction_date.strip(),
        player_name=args.player_name.strip(),
        market_type=args.market_type.strip(),
        selection=args.selection.strip(),
        decision=args.decision.strip(),
        decision_reason=(args.reason or "").strip(),
        kelly_path=kelly_path,
        history_path=history_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
