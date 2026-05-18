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
and one of ``side_edge_pct``/``edge_pct``/``edge``. Missing required columns abort with a
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

from courtvision.artifact_guard import (
    guard_no_existing_artifact,
    guard_prediction_artifact_date,
)
from courtvision.betting.kelly import (  # noqa: E402  (path-bootstrapped above)
    MAX_STAKE_FRACTION,
    MIN_CONFIDENCE_THRESHOLD,
    compute_kelly_fraction,
)
from courtvision.context.game_context import (  # noqa: E402
    IDENTITY_QUARANTINE_ACTION,
    IDENTITY_QUARANTINE_REJECTION_REASON,
    is_identity_quarantined,
)

REQUIRED_COLUMNS: tuple[str, ...] = ("odds", "confidence")
EDGE_COLUMN_PREFERENCE: tuple[str, ...] = ("side_edge_pct", "edge_pct", "edge")

# Default daily exposure cap as a fraction of bankroll. Overridable via
# COURTVISION_MAX_DAILY_EXPOSURE env var or --max-daily-exposure CLI flag.
DEFAULT_MAX_DAILY_EXPOSURE: float = 0.08
MEDIUM_NEUTRAL_OVER_DAMPENER_FACTOR: float = 0.5
MEDIUM_NEUTRAL_OVER_DAMPENER_REASON: str = "medium_neutral_over_dampener"
REVIEW_BEFORE_BET_ACTION: str = "REVIEW_BEFORE_BET"
OK_TO_CONSIDER_ACTION: str = "OK_TO_CONSIDER"

# Phase 12G: Edge Containment HOLD risk control constants
# A row is held when: selection==over, market_type in HOLD_COMBO_MARKETS, edge>=threshold.
# Applies BEFORE normal eligibility gating so it fires even for currently-ineligible
# combo markets (protective for future expansion).
EDGE_CONTAINMENT_HOLD_SKIP_REASON: str = "edge_containment_hold_for_review"
_HOLD_COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})
# Threshold for percentage-form columns (side_edge_pct, edge_pct): 0.04 = 4%
# Threshold for absolute-unit column (edge): 4.0 units
_HOLD_EDGE_THRESHOLD_PCT: float = 0.04
_HOLD_EDGE_THRESHOLD_ABS: float = 4.0


@dataclass
class StakeRow:
    player_name: str
    team_abbr: str
    opponent: str
    market_type: str
    selection: str
    line: float | None
    american_odds: int | None
    decimal_odds: float | None
    edge_pct: float | None
    side_edge_pct: float | None
    confidence: float | None
    stake_fraction: float
    stake_amount: float
    expected_value: float
    eligible: bool
    skip_reason: str
    context_caution_level: str
    context_pick_alignment: str
    stake_dampener_reason: str
    stake_dampener_factor: float
    same_opponent_under_warning: bool
    same_opponent_warning_reason: str
    manual_review_required: bool
    manual_review_reason: str
    recommended_action: str
    review_status: str
    stake_policy: str
    operator_action: str
    operator_note: str
    identity_quarantine_reason: str


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


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


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


def _check_edge_containment_hold(
    row: dict[str, str],
    market_type: str,
    selection: str,
    edge_pct_raw: float | None,
    edge_col: str,
) -> bool:
    """Return True if Phase 12G HOLD control applies to this row.

    Checks metadata flags first (populated when hold control module has run),
    then applies policy criteria directly as a defensive fallback.
    """
    # Metadata-first check (fast path when Phase 12G has already annotated the board)
    stake_policy = str(row.get("edge_containment_stake_policy", "") or "").strip()
    if stake_policy == "HOLD_FOR_REVIEW":
        return True
    rec_action = str(row.get("edge_containment_recommended_action", "") or "").strip()
    if rec_action == "DO_NOT_BET_UNTIL_REVIEWED":
        return True
    # Direct policy criteria (defensive — works even without pre-annotation)
    if market_type not in _HOLD_COMBO_MARKETS or selection != "over" or edge_pct_raw is None:
        return False
    threshold = (
        _HOLD_EDGE_THRESHOLD_PCT
        if edge_col in ("side_edge_pct", "edge_pct")
        else _HOLD_EDGE_THRESHOLD_ABS
    )
    return edge_pct_raw >= threshold


def _medium_neutral_over_dampener(selection: str, context_caution_level: str, context_pick_alignment: str, eligible: bool) -> tuple[str, float]:
    if (
        eligible
        and selection.strip().lower() == "over"
        and context_caution_level.strip().lower() == "medium"
        and context_pick_alignment.strip().lower() == "neutral"
    ):
        return MEDIUM_NEUTRAL_OVER_DAMPENER_REASON, MEDIUM_NEUTRAL_OVER_DAMPENER_FACTOR
    return "", 1.0


def _refresh_expected_value(stake: StakeRow) -> None:
    if stake.eligible and stake.edge_pct is not None:
        stake.expected_value = round(stake.stake_amount * float(stake.edge_pct), 2)
    else:
        stake.expected_value = 0.0


def _apply_stake_dampeners(stakes: list[StakeRow]) -> None:
    for s in stakes:
        if not s.eligible or s.stake_dampener_factor == 1.0:
            continue
        s.stake_amount = round(s.stake_amount * s.stake_dampener_factor, 2)
        s.stake_fraction = round(s.stake_fraction * s.stake_dampener_factor, 6)
        _refresh_expected_value(s)


def _build_stake_row(row: dict[str, str], edge_col: str, bankroll: float) -> StakeRow:
    raw_american = _to_float(row.get("odds"))
    decimal_odds = _american_to_decimal(raw_american) if raw_american is not None else None
    confidence = _to_float(row.get("confidence"))
    edge_pct_raw = _to_float(row.get(edge_col))
    side_edge_pct_raw = _to_float(row.get("side_edge_pct"))
    line = _to_float(row.get("line") or row.get("sportsbook_line"))

    skip_reason = ""
    eligible = True
    raw_market_type = str(row.get("raw_market_type", row.get("market.type", "")) or "").strip().lower()
    selection = str(row.get("selection", "") or "").strip().lower()
    market_type = str(row.get("market_type", "") or "").strip().lower()
    context_caution_level = str(row.get("context_caution_level", "") or "").strip().lower()
    context_pick_alignment = str(row.get("context_pick_alignment", "") or "").strip().lower()
    team_abbr = str(row.get("team_abbr", "") or "").strip()
    opponent = str(row.get("opponent", "") or "").strip()
    same_opponent_under_warning = _is_truthy(row.get("same_opponent_under_warning"))
    manual_review_required = _is_truthy(row.get("manual_review_required"))
    identity_quarantine_reason = is_identity_quarantined(row) or ""
    _manual_review_reason_raw = str(row.get("manual_review_reason", "") or "").strip()
    _same_opponent_warning_reason_raw = str(row.get("same_opponent_warning_reason", "") or "").strip()
    if not same_opponent_under_warning and "same_opponent_under_warning" in _manual_review_reason_raw:
        same_opponent_under_warning = True

    # Phase 12G: Edge Containment HOLD — evaluated BEFORE market_type lock and all
    # other eligibility gates so it fires even for currently-ineligible combo markets.
    _ecr_hold = _check_edge_containment_hold(
        row, market_type, selection, edge_pct_raw, edge_col
    )

    if identity_quarantine_reason:
        skip_reason = IDENTITY_QUARANTINE_REJECTION_REASON
        eligible = False
        manual_review_required = True
        review_status = IDENTITY_QUARANTINE_ACTION
        stake_policy = "DATA_INVALID"
        recommended_action = IDENTITY_QUARANTINE_ACTION
        operator_action = IDENTITY_QUARANTINE_ACTION
        operator_note = f"identity_quarantine_reason={identity_quarantine_reason}"
    elif _ecr_hold:
        # HOLD overrides all other routing
        skip_reason = EDGE_CONTAINMENT_HOLD_SKIP_REASON
        eligible = False
        manual_review_required = True
        review_status = "HOLD"
        stake_policy = "HOLD_FOR_REVIEW"
        recommended_action = "DO_NOT_BET_UNTIL_REVIEWED"
        operator_action = "DO_NOT_BET_UNTIL_REVIEWED"
        operator_note = "edge_containment_hold_for_review"
    elif manual_review_required:
        review_status = "REVIEW_REQUIRED"
        stake_policy = "HOLD"
        recommended_action = REVIEW_BEFORE_BET_ACTION
        operator_action = "DO_NOT_BET_UNTIL_REVIEWED"
        _note_parts = []
        if _manual_review_reason_raw:
            _note_parts.append(f"manual_review_reason={_manual_review_reason_raw}")
        if _same_opponent_warning_reason_raw:
            _note_parts.append(f"same_opponent_warning_reason={_same_opponent_warning_reason_raw}")
        operator_note = "; ".join(_note_parts) if _note_parts else "manual_review_required"
    else:
        review_status = "CLEAR"
        stake_policy = "NORMAL"
        recommended_action = OK_TO_CONSIDER_ACTION
        operator_action = "OK_TO_CONSIDER"
        operator_note = ""

    if not identity_quarantine_reason and not _ecr_hold:
        if market_type and market_type != "player_points":
            skip_reason = "kelly_points_only_market_lock"
            eligible = False
        elif raw_market_type == "milestone" or selection == "milestone":
            skip_reason = "unsupported_milestone_market"
            eligible = False
        elif context_caution_level == "high" and selection == "over":
            skip_reason = "context_high_caution_over"
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

    stake_dampener_reason, stake_dampener_factor = _medium_neutral_over_dampener(
        selection=selection,
        context_caution_level=context_caution_level,
        context_pick_alignment=context_pick_alignment,
        eligible=eligible,
    )

    stake_amount = round(bankroll * stake_fraction, 2)
    # Expected value of one unit stake: edge * stake_amount (treating edge_pct as ROI signal)
    if eligible and edge_pct_raw is not None:
        expected_value = round(stake_amount * float(edge_pct_raw), 2)
    else:
        expected_value = 0.0

    return StakeRow(
        player_name=str(row.get("player_name", "")),
        team_abbr=team_abbr,
        opponent=opponent,
        market_type=str(row.get("market_type", "")),
        selection=str(row.get("selection", "")),
        line=line,
        american_odds=int(raw_american) if raw_american is not None else None,
        decimal_odds=round(decimal_odds, 4) if decimal_odds is not None else None,
        edge_pct=round(edge_pct_raw, 6) if edge_pct_raw is not None else None,
        side_edge_pct=round(side_edge_pct_raw, 6) if side_edge_pct_raw is not None else None,
        confidence=round(confidence, 4) if confidence is not None else None,
        stake_fraction=stake_fraction,
        stake_amount=stake_amount,
        expected_value=expected_value,
        eligible=eligible,
        skip_reason=skip_reason,
        context_caution_level=str(row.get("context_caution_level", "") or ""),
        context_pick_alignment=str(row.get("context_pick_alignment", "") or ""),
        stake_dampener_reason=stake_dampener_reason,
        stake_dampener_factor=stake_dampener_factor,
        same_opponent_under_warning=same_opponent_under_warning,
        same_opponent_warning_reason=str(row.get("same_opponent_warning_reason", "") or ""),
        manual_review_required=manual_review_required,
        manual_review_reason=str(row.get("manual_review_reason", "") or ""),
        recommended_action=recommended_action,
        review_status=review_status,
        stake_policy=stake_policy,
        operator_action=operator_action,
        operator_note=operator_note,
        identity_quarantine_reason=identity_quarantine_reason,
    )


def _write_stakes(
    output_path: Path,
    stakes: list[StakeRow],
    bankroll: float,
    prediction_date: str,
    *,
    force: bool = False,
) -> None:
    caller = "scripts/run_kelly_stakes.py:_write_stakes"
    guard_prediction_artifact_date(
        requested_prediction_date=prediction_date,
        output_path=output_path,
        caller=caller,
    )
    guard_no_existing_artifact(
        output_path=output_path,
        force=force,
        caller=caller,
        artifact_label="kelly_stakes",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "prediction_date",
        "player_name",
        "team_abbr",
        "opponent",
        "market_type",
        "selection",
        "line",
        "american_odds",
        "decimal_odds",
        "edge_pct",
        "side_edge_pct",
        "confidence",
        "stake_fraction",
        "stake_amount",
        "expected_value",
        "bankroll",
        "kelly_eligible",
        "eligible",
        "skip_reason",
        "context_caution_level",
        "context_pick_alignment",
        "stake_dampener_reason",
        "stake_dampener_factor",
        "same_opponent_under_warning",
        "same_opponent_warning_reason",
        "manual_review_required",
        "manual_review_reason",
        "recommended_action",
        "review_status",
        "stake_policy",
        "operator_action",
        "operator_note",
        "identity_quarantine_reason",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for s in stakes:
            writer.writerow({
                "prediction_date": prediction_date,
                "player_name": s.player_name,
                "team_abbr": s.team_abbr,
                "opponent": s.opponent,
                "market_type": s.market_type,
                "selection": s.selection,
                "line": "" if s.line is None else f"{s.line:g}",
                "american_odds": "" if s.american_odds is None else s.american_odds,
                "decimal_odds": "" if s.decimal_odds is None else s.decimal_odds,
                "edge_pct": "" if s.edge_pct is None else s.edge_pct,
                "side_edge_pct": "" if s.side_edge_pct is None else s.side_edge_pct,
                "confidence": "" if s.confidence is None else s.confidence,
                "stake_fraction": s.stake_fraction,
                "stake_amount": s.stake_amount,
                "expected_value": s.expected_value,
                "bankroll": bankroll,
                "kelly_eligible": s.eligible,
                "eligible": s.eligible,
                "skip_reason": s.skip_reason,
                "context_caution_level": s.context_caution_level,
                "context_pick_alignment": s.context_pick_alignment,
                "stake_dampener_reason": s.stake_dampener_reason,
                "stake_dampener_factor": s.stake_dampener_factor,
                "same_opponent_under_warning": s.same_opponent_under_warning,
                "same_opponent_warning_reason": s.same_opponent_warning_reason,
                "manual_review_required": s.manual_review_required,
                "manual_review_reason": s.manual_review_reason,
                "recommended_action": s.recommended_action,
                "review_status": s.review_status,
                "stake_policy": s.stake_policy,
                "operator_action": s.operator_action,
                "operator_note": s.operator_note,
                "identity_quarantine_reason": s.identity_quarantine_reason,
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
        "--force",
        action="store_true",
        help="Allow an existing Kelly stakes CSV to be overwritten intentionally.",
    )
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
    manual_review_required_count = sum(1 for s in stakes if s.manual_review_required)
    review_before_bet_count = sum(1 for s in stakes if s.recommended_action == REVIEW_BEFORE_BET_ACTION)
    review_required_count = sum(1 for s in stakes if s.review_status == "REVIEW_REQUIRED")
    hold_policy_count = sum(1 for s in stakes if s.stake_policy == "HOLD")
    clear_policy_count = sum(1 for s in stakes if s.stake_policy == "NORMAL")
    do_not_bet_until_reviewed_count = sum(1 for s in stakes if s.operator_action == "DO_NOT_BET_UNTIL_REVIEWED")
    # Phase 12G: count rows held by edge containment HOLD control
    edge_containment_hold_count = sum(
        1 for s in stakes if s.skip_reason == EDGE_CONTAINMENT_HOLD_SKIP_REASON
    )

    _log(f"eligible_picks={len(eligible)} skipped_picks={len(skipped)}")
    _log(f"manual_review_required_count={manual_review_required_count}")
    _log(f"review_before_bet_count={review_before_bet_count}")
    _log(f"review_required_count={review_required_count}")
    _log(f"hold_policy_count={hold_policy_count}")
    _log(f"clear_policy_count={clear_policy_count}")
    _log(f"do_not_bet_until_reviewed_count={do_not_bet_until_reviewed_count}")
    eligible_side_counts: dict[str, int] = {}
    for s in eligible:
        side = str(s.selection or "").strip().lower()
        eligible_side_counts[side] = eligible_side_counts.get(side, 0) + 1
    print(f"[COUNT] kelly_over_rows={int(eligible_side_counts.get('over', 0))}", flush=True)
    print(f"[COUNT] kelly_under_rows={int(eligible_side_counts.get('under', 0))}", flush=True)
    print(f"[COUNT] manual_review_required_count={manual_review_required_count}", flush=True)
    print(f"[COUNT] review_before_bet_count={review_before_bet_count}", flush=True)
    print(f"[COUNT] review_required_count={review_required_count}", flush=True)
    print(f"[COUNT] hold_policy_count={hold_policy_count}", flush=True)
    print(f"[COUNT] clear_policy_count={clear_policy_count}", flush=True)
    print(f"[COUNT] do_not_bet_until_reviewed_count={do_not_bet_until_reviewed_count}", flush=True)
    print(f"[COUNT] edge_containment_hold_count={edge_containment_hold_count}", flush=True)
    _log(f"edge_containment_hold_count={edge_containment_hold_count}")

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
            _refresh_expected_value(s)
    else:
        exposure_scale_factor = 1.0

    _apply_stake_dampeners(eligible)

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
            f"frac={s.stake_fraction:.4f} amount=${s.stake_amount:.2f} ev=${s.expected_value:.2f} "
            f"dampener={s.stake_dampener_factor:g} reason={s.stake_dampener_reason or 'none'} "
            f"recommended_action={s.recommended_action} "
            f"manual_review_required={s.manual_review_required} "
            f"review_status={s.review_status} stake_policy={s.stake_policy}"
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

    _write_stakes(output_path, stakes, bankroll, prediction_date, force=args.force)
    _log(f"output={output_path}")
    _log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
