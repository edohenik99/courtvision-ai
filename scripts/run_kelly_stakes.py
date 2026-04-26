"""Kelly stakes runner.

Reads the daily elite board CSV produced by the prediction pipeline,
applies the Kelly-criterion sizing rules from `courtvision.betting.kelly`
to every eligible row, and writes a per-pick stake CSV.

This is intentionally a thin orchestration layer over `kelly.py`. It does
*not* alter prediction logic and does *not* generate fake stakes - if a
required field is missing it logs the reason and emits a zero stake row.

Usage
-----
    python scripts/run_kelly_stakes.py --prediction-date 2026-04-26
    python scripts/run_kelly_stakes.py --prediction-date 2026-04-26 --bankroll 2500

Inputs
------
- outputs/runtime/operator/elite_board_<DATE>.csv
- bankroll (parameter, default 1000.0)

Required columns on the input CSV: ``odds`` (American), ``confidence``,
and one of ``edge_pct``/``edge``. Missing required columns abort with a
clear error.

Output
------
- outputs/runtime/operator/kelly_stakes_<DATE>.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so `courtvision.betting.kelly`
# imports cleanly when this script is invoked directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from courtvision.betting.kelly import (  # noqa: E402  (path-bootstrapped above)
    MAX_STAKE_FRACTION,
    MIN_CONFIDENCE_THRESHOLD,
    compute_kelly_fraction,
)

REQUIRED_COLUMNS: tuple[str, ...] = ("odds", "confidence")
EDGE_COLUMN_PREFERENCE: tuple[str, ...] = ("edge_pct", "edge")

# Default daily exposure cap as a fraction of bankroll. Overridable via
# COURTVISION_MAX_DAILY_EXPOSURE env var or --max-daily-exposure CLI flag.
DEFAULT_MAX_DAILY_EXPOSURE: float = 0.08


@dataclass
class StakeRow:
    player_name: str
    market_type: str
    selection: str
    line: float | None
    american_odds: int | None
    decimal_odds: float | None
    edge_pct: float | None
    confidence: float | None
    stake_fraction: float
    stake_amount: float
    expected_value: float
    eligible: bool
    skip_reason: str


def _log(msg: str) -> None:
    print(f"[KELLY] {msg}", flush=True)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _american_to_decimal(american: float) -> float | None:
    """Convert American odds to decimal odds.

    +150 -> 2.50 ; -110 -> 1.9091 ; 0 -> None (invalid).
    """
    if american is None:
        return None
    try:
        a = float(american)
    except (TypeError, ValueError):
        return None
    if a == 0 or math.isnan(a) or math.isinf(a):
        return None
    if a > 0:
        return 1.0 + (a / 100.0)
    return 1.0 + (100.0 / abs(a))


def _resolve_paths(prediction_date: str, input_override: str | None, output_override: str | None) -> tuple[Path, Path]:
    operator_dir = _PROJECT_ROOT / "outputs" / "runtime" / "operator"
    input_path = Path(input_override) if input_override else operator_dir / f"elite_board_{prediction_date}.csv"
    output_path = Path(output_override) if output_override else operator_dir / f"kelly_stakes_{prediction_date}.csv"
    return input_path, output_path


def _read_board(input_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not input_path.exists():
        raise SystemExit(
            f"[KELLY][FATAL] Elite board not found: {input_path}\n"
            f"        Run the prediction pipeline first to generate the board."
        )
    with input_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return fieldnames, rows


def _validate_columns(fieldnames: list[str]) -> str:
    """Validate required columns; return the edge column to use."""
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise SystemExit(
            f"[KELLY][FATAL] Elite board is missing required column(s): {missing}\n"
            f"        Available columns: {fieldnames}"
        )
    edge_col = next((c for c in EDGE_COLUMN_PREFERENCE if c in fieldnames), None)
    if edge_col is None:
        raise SystemExit(
            f"[KELLY][FATAL] Elite board is missing an edge column. "
            f"Expected one of: {EDGE_COLUMN_PREFERENCE}\n"
            f"        Available columns: {fieldnames}"
        )
    return edge_col


def _build_stake_row(row: dict[str, str], edge_col: str, bankroll: float) -> StakeRow:
    raw_american = _to_float(row.get("odds"))
    decimal_odds = _american_to_decimal(raw_american) if raw_american is not None else None
    confidence = _to_float(row.get("confidence"))
    edge_pct_raw = _to_float(row.get(edge_col))
    line = _to_float(row.get("line") or row.get("sportsbook_line"))

    skip_reason = ""
    eligible = True
    raw_market_type = str(row.get("raw_market_type", row.get("market.type", "")) or "").strip().lower()
    selection = str(row.get("selection", "") or "").strip().lower()
    if raw_market_type == "milestone" or selection == "milestone":
        skip_reason = "unsupported_milestone_market"
        eligible = False
    elif raw_american is None:
        skip_reason = "missing_or_invalid_odds"
        eligible = False
    elif decimal_odds is None or decimal_odds <= 1.0:
        skip_reason = "non_positive_decimal_odds"
        eligible = False
    elif confidence is None:
        skip_reason = "missing_confidence"
        eligible = False
    elif edge_pct_raw is None:
        skip_reason = f"missing_{edge_col}"
        eligible = False
    elif confidence < MIN_CONFIDENCE_THRESHOLD:
        skip_reason = f"confidence_below_min({MIN_CONFIDENCE_THRESHOLD})"
        eligible = False
    elif edge_pct_raw <= 0:
        skip_reason = "non_positive_edge"
        eligible = False

    if eligible:
        stake_fraction = compute_kelly_fraction(
            edge=float(edge_pct_raw),
            odds=float(decimal_odds),
            confidence=float(confidence),
        )
        if stake_fraction <= 0:
            eligible = False
            skip_reason = "kelly_returned_zero"
    else:
        stake_fraction = 0.0

    stake_amount = round(bankroll * stake_fraction, 2)
    # Expected value of one unit stake: edge * stake_amount (treating edge_pct as ROI signal)
    if eligible and edge_pct_raw is not None:
        expected_value = round(stake_amount * float(edge_pct_raw), 2)
    else:
        expected_value = 0.0

    return StakeRow(
        player_name=str(row.get("player_name", "")),
        market_type=str(row.get("market_type", "")),
        selection=str(row.get("selection", "")),
        line=line,
        american_odds=int(raw_american) if raw_american is not None else None,
        decimal_odds=round(decimal_odds, 4) if decimal_odds is not None else None,
        edge_pct=round(edge_pct_raw, 6) if edge_pct_raw is not None else None,
        confidence=round(confidence, 4) if confidence is not None else None,
        stake_fraction=stake_fraction,
        stake_amount=stake_amount,
        expected_value=expected_value,
        eligible=eligible,
        skip_reason=skip_reason,
    )


def _write_stakes(output_path: Path, stakes: list[StakeRow], bankroll: float, prediction_date: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "prediction_date",
        "player_name",
        "market_type",
        "selection",
        "line",
        "american_odds",
        "decimal_odds",
        "edge_pct",
        "confidence",
        "stake_fraction",
        "stake_amount",
        "expected_value",
        "bankroll",
        "eligible",
        "skip_reason",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for s in stakes:
            writer.writerow({
                "prediction_date": prediction_date,
                "player_name": s.player_name,
                "market_type": s.market_type,
                "selection": s.selection,
                "line": "" if s.line is None else f"{s.line:g}",
                "american_odds": "" if s.american_odds is None else s.american_odds,
                "decimal_odds": "" if s.decimal_odds is None else s.decimal_odds,
                "edge_pct": "" if s.edge_pct is None else s.edge_pct,
                "confidence": "" if s.confidence is None else s.confidence,
                "stake_fraction": s.stake_fraction,
                "stake_amount": s.stake_amount,
                "expected_value": s.expected_value,
                "bankroll": bankroll,
                "eligible": s.eligible,
                "skip_reason": s.skip_reason,
            })


def _resolve_max_daily_exposure(cli_value: float | None) -> float:
    """CLI > env var > default."""
    if cli_value is not None:
        return float(cli_value)
    env_value = os.getenv("COURTVISION_MAX_DAILY_EXPOSURE")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            raise SystemExit(
                f"[KELLY][FATAL] COURTVISION_MAX_DAILY_EXPOSURE is not a number: {env_value!r}"
            )
    return DEFAULT_MAX_DAILY_EXPOSURE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute Kelly stakes from the daily elite board.")
    parser.add_argument("--prediction-date", required=True, help="Slate date in YYYY-MM-DD format.")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Bankroll in dollars (default: 1000).")
    parser.add_argument("--input-csv", default=None, help="Override path to the elite board CSV.")
    parser.add_argument("--output-csv", default=None, help="Override path to the stakes output CSV.")
    parser.add_argument(
        "--max-daily-exposure",
        type=float,
        default=None,
        help=(
            "Cap on total daily stake as a fraction of bankroll "
            f"(default: {DEFAULT_MAX_DAILY_EXPOSURE}, or COURTVISION_MAX_DAILY_EXPOSURE env var)."
        ),
    )
    args = parser.parse_args(argv)

    prediction_date = args.prediction_date
    bankroll = float(args.bankroll)
    if bankroll <= 0:
        raise SystemExit(f"[KELLY][FATAL] Bankroll must be > 0, got {bankroll}")

    max_daily_exposure = _resolve_max_daily_exposure(args.max_daily_exposure)
    if max_daily_exposure <= 0 or max_daily_exposure > 1.0:
        raise SystemExit(
            f"[KELLY][FATAL] max_daily_exposure must be in (0, 1], got {max_daily_exposure}"
        )

    input_path, output_path = _resolve_paths(prediction_date, args.input_csv, args.output_csv)

    _log(f"start prediction_date={prediction_date} bankroll={bankroll:.2f} "
         f"max_stake_fraction={MAX_STAKE_FRACTION} min_confidence={MIN_CONFIDENCE_THRESHOLD}")
    _log(f"input={input_path}")

    fieldnames, rows = _read_board(input_path)
    _log(f"rows_loaded={len(rows)} columns={len(fieldnames)}")

    if not rows:
        raise SystemExit(
            f"[KELLY][FATAL] Elite board is empty: {input_path}\n"
            f"        Refusing to emit a stakes file with zero rows."
        )

    edge_col = _validate_columns(fieldnames)
    _log(f"edge_column={edge_col}")

    stakes = [_build_stake_row(row, edge_col, bankroll) for row in rows]

    eligible = [s for s in stakes if s.eligible]
    skipped = [s for s in stakes if not s.eligible]

    _log(f"eligible_picks={len(eligible)} skipped_picks={len(skipped)}")

    # Aggregate skip reasons
    if skipped:
        reason_counts: dict[str, int] = {}
        for s in skipped:
            reason_counts[s.skip_reason] = reason_counts.get(s.skip_reason, 0) + 1
        for reason, n in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
            _log(f"skipped_reason {reason}={n}")

    # ---- Daily exposure cap -------------------------------------------------
    # If the sum of eligible Kelly stakes exceeds bankroll * max_daily_exposure,
    # scale every eligible stake_amount/stake_fraction proportionally. This
    # preserves the relative Kelly weights while bounding total daily risk.
    raw_total_exposure = round(sum(s.stake_amount for s in eligible), 2)
    max_daily_exposure_amount = round(bankroll * max_daily_exposure, 2)
    if raw_total_exposure > max_daily_exposure_amount and raw_total_exposure > 0:
        exposure_scale_factor = max_daily_exposure_amount / raw_total_exposure
        for s in eligible:
            s.stake_amount = round(s.stake_amount * exposure_scale_factor, 2)
            s.stake_fraction = round(s.stake_fraction * exposure_scale_factor, 6)
            if s.edge_pct is not None:
                s.expected_value = round(s.stake_amount * float(s.edge_pct), 2)
    else:
        exposure_scale_factor = 1.0

    _log(f"raw_total_exposure=${raw_total_exposure:.2f}")
    _log(
        f"max_daily_exposure={max_daily_exposure:.4f} "
        f"(${max_daily_exposure_amount:.2f} of ${bankroll:.2f} bankroll)"
    )
    _log(f"exposure_scale_factor={exposure_scale_factor:.4f}")

    # Per-pick stakes (post-scaling)
    for s in eligible:
        _log(
            f"stake player={s.player_name!r} market={s.market_type} sel={s.selection} "
            f"line={s.line} odds={s.american_odds} dec={s.decimal_odds} "
            f"edge_pct={s.edge_pct} conf={s.confidence} "
            f"frac={s.stake_fraction:.4f} amount=${s.stake_amount:.2f} ev=${s.expected_value:.2f}"
        )

    total_exposure = round(sum(s.stake_amount for s in eligible), 2)
    total_ev = round(sum(s.expected_value for s in eligible), 2)
    avg_stake = round(total_exposure / len(eligible), 2) if eligible else 0.0
    exposure_pct = round(100.0 * total_exposure / bankroll, 2) if bankroll > 0 else 0.0
    _log(f"final_total_exposure=${total_exposure:.2f} ({exposure_pct:.2f}% of bankroll)")

    _log(f"bankroll_used=${bankroll:.2f}")
    _log(f"total_exposure=${total_exposure:.2f} ({exposure_pct:.2f}% of bankroll)")
    _log(f"avg_stake=${avg_stake:.2f}")
    _log(f"expected_value_total=${total_ev:.2f}")

    _write_stakes(output_path, stakes, bankroll, prediction_date)
    _log(f"output={output_path}")
    _log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
