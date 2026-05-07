from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.kelly_performance import build_kelly_decision_performance
from courtvision.reporting.combo_under_watchlist import (
    OBSERVATION_ONLY_NOTE as COMBO_UNDER_OBSERVATION_ONLY_NOTE,
    build_combo_under_watchlist,
    watchlist_path_for_date as combo_under_watchlist_path_for_date,
    watchlist_row_line as combo_under_watchlist_row_line,
    write_combo_under_watchlist,
)
from courtvision.reporting.high_caution_over_watchlist import (
    OBSERVATION_ONLY_NOTE,
    build_high_caution_over_watchlist,
    watchlist_path_for_date,
    watchlist_row_line,
    write_high_caution_over_watchlist,
)
from courtvision.reporting.promotion_readiness import (
    OBSERVATION_ONLY_NOTE as PROMOTION_READINESS_OBSERVATION_ONLY_NOTE,
    build_promotion_readiness_report,
    read_market_shadow_history,
    report_paths_for_date as promotion_readiness_report_paths_for_date,
    report_row_line as promotion_readiness_report_row_line,
    write_promotion_readiness_report,
)
from courtvision.reporting.paper_kelly_simulation import (
    SIMULATION_WARNING as PAPER_KELLY_SIMULATION_WARNING,
    build_paper_kelly_simulation,
    report_paths_for_date as paper_kelly_report_paths_for_date,
    write_paper_kelly_simulation,
)
from courtvision.reporting.paper_kelly_performance import (
    REPORT_TITLE as PAPER_KELLY_PERFORMANCE_TITLE,
    build_paper_kelly_performance_report,
    history_path as paper_kelly_history_path,
    read_paper_kelly_history,
    report_paths_for_date as paper_kelly_performance_report_paths_for_date,
    report_row_line as paper_kelly_performance_row_line,
    summarize_paper_kelly_history,
    write_paper_kelly_performance_report,
)
from courtvision.reporting.same_opponent_rematch import annotate_operator_board_files, manual_review_summary
from courtvision.reporting.correlation_exposure import (
    REPORT_TITLE as CORRELATION_EXPOSURE_TITLE,
    build_correlation_exposure_report,
    report_paths_for_date as correlation_exposure_report_paths_for_date,
    report_row_line as correlation_exposure_row_line,
    write_correlation_exposure_report,
)
from courtvision.reporting.team_distribution import (
    OBSERVATION_ONLY_NOTE as TEAM_DISTRIBUTION_OBSERVATION_ONLY_NOTE,
    REPORT_TITLE as TEAM_DISTRIBUTION_TITLE,
    build_team_distribution_report,
    report_paths_for_date as team_distribution_report_paths_for_date,
    report_row_line as team_distribution_report_row_line,
    write_team_distribution_report,
)
from scripts.history_tracking import PLAYER_POINTS_MARKET, persist_market_shadow_history

RUN_HEALTH_RECOMMENDATIONS: dict[str, str] = {
    "HEALTHY": "Run is bet-ready within configured staking limits.",
    "HEALTHY_LOW_VOLUME": "Run is valid but low-volume; avoid forcing extra action.",
    "HEALTHY_CONTEXT_GATED": (
        "Run is bet-ready; context safety blocked risky candidates and final Elite is clean."
    ),
    "DEGRADED_LOW_COVERAGE": "Provider/candidate coverage is thin; treat picks cautiously.",
    "DEGRADED_CONTEXT_BLOCKED": (
        "Model found edges but context safety rejected too many; no threshold loosening recommended."
    ),
    "NO_BET": "No stakeable picks. Do not force action.",
    "ERROR_OR_INCOMPLETE": "Run artifacts are missing or incomplete; review validation before using picks.",
}
RUN_HEALTH_CONTEXT_BLOCKED_RATIO = 0.5
RUN_HEALTH_LOW_VOLUME_KELLY_ELIGIBLE = 2
CONTEXT_CONFLICT_CAUSE_BUCKETS: tuple[str, ...] = (
    "playoff_only",
    "defense_driven",
    "pace_driven",
    "pace_defense_combined",
    "playoff_defense_combined",
    "stale_team_not_in_game",
)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _read_csv(path: Path, warnings: list[str]) -> pd.DataFrame:
    if not path.exists():
        warnings.append(f"Missing file: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        warnings.append(f"Could not read CSV {path}: {exc}")
        return pd.DataFrame()


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"Missing file: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read JSON {path}: {exc}")
        return {}


def _format_money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _format_num(value: Any, digits: int = 3) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number:.{digits}f}"


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _market_counts(df: pd.DataFrame) -> Counter[str]:
    if df.empty or "market_type" not in df.columns:
        return Counter()
    return Counter(_safe_text(value) or "unknown" for value in df["market_type"])


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _elite_artifact_has_no_rows(path: Path, elite_df: pd.DataFrame) -> bool:
    return path.exists() and (not isinstance(elite_df, pd.DataFrame) or elite_df.empty)


def _kelly_df_for_reporting(
    *,
    elite_path: Path,
    elite_df: pd.DataFrame,
    kelly_df: pd.DataFrame,
    warnings: list[str],
) -> pd.DataFrame:
    if _elite_artifact_has_no_rows(elite_path, elite_df):
        if isinstance(kelly_df, pd.DataFrame) and not kelly_df.empty:
            warnings.append(
                "Ignoring Kelly stakes artifact because elite board has 0 rows; "
                "treating Kelly exposure as zero for reporting."
            )
        return pd.DataFrame(columns=list(kelly_df.columns) if isinstance(kelly_df, pd.DataFrame) else [])
    return kelly_df


def _empty_kelly_decision_performance(reason: str) -> dict[str, Any]:
    empty = {
        "count": 0,
        "graded_count": 0,
        "pending_count": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "hit_rate": None,
        "roi": None,
        "status": "insufficient_sample",
    }
    return {
        "status": "insufficient_sample",
        "reason": reason,
        "overall": empty,
        "by_kelly_eligible": {"true": empty, "false": empty},
        "by_skip_reason": {},
    }


def _sort_for_display(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("quality_score", "expected_value", "edge", "edge_pct"):
        if col in df.columns:
            working = df.copy()
            working[col] = pd.to_numeric(working[col], errors="coerce")
            return working.sort_values(col, ascending=False, na_position="last")
    return df


def _pick_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    side = _safe_text(row.get("selection")) or "n/a"
    line = _format_num(row.get("sportsbook_line") if "sportsbook_line" in row.index else row.get("line"), 1)
    odds = _safe_text(row.get("odds") if "odds" in row.index else row.get("american_odds")) or "n/a"
    edge = _format_num(row.get("edge"), 3)
    confidence = _format_num(row.get("confidence"), 3)
    quality = _format_num(row.get("quality_score"), 2)
    return (
        f"- {player}: {market} {side} {line} "
        f"(odds={odds}, edge={edge}, conf={confidence}, quality={quality})"
    )


def _has_manual_context(row: pd.Series) -> bool:
    for col in (
        "manual_status",
        "manual_minutes_limit",
        "manual_projection_adjustment",
        "manual_confidence_adjustment",
        "manual_context_reason",
    ):
        if col in row.index and _safe_text(row.get(col)):
            return True
    return False


def _manual_context_lines(row: pd.Series) -> list[str]:
    return [
        f"  manual_status: {_safe_text(row.get('manual_status')) or 'n/a'}",
        f"  manual_minutes_limit: {_safe_text(row.get('manual_minutes_limit')) or 'n/a'}",
        f"  manual_projection_adjustment: {_safe_text(row.get('manual_projection_adjustment')) or 'n/a'}",
        f"  manual_confidence_adjustment: {_safe_text(row.get('manual_confidence_adjustment')) or 'n/a'}",
        f"  manual_context_reason: {_safe_text(row.get('manual_context_reason')) or 'n/a'}",
        f"  manual_context_applied: {_safe_text(row.get('manual_context_applied')) or 'False'}",
    ]


def _has_manual_review(row: pd.Series) -> bool:
    return _is_truthy(row.get("manual_review_required")) or _is_truthy(row.get("same_opponent_under_warning"))


def _manual_review_lines(row: pd.Series) -> list[str]:
    return [
        f"  manual_review_required: {_safe_text(row.get('manual_review_required')) or 'False'}",
        f"  manual_review_reason: {_safe_text(row.get('manual_review_reason')) or 'n/a'}",
        f"  same_opponent_recent_games: {_safe_text(row.get('same_opponent_recent_games')) or '0'}",
        f"  same_opponent_last_actual_points: {_safe_text(row.get('same_opponent_last_actual_points')) or 'n/a'}",
        f"  same_opponent_last_line: {_safe_text(row.get('same_opponent_last_line')) or 'n/a'}",
        f"  same_opponent_last_selection: {_safe_text(row.get('same_opponent_last_selection')) or 'n/a'}",
        f"  same_opponent_last_result_status: {_safe_text(row.get('same_opponent_last_result_status')) or 'n/a'}",
        f"  same_opponent_under_warning: {_safe_text(row.get('same_opponent_under_warning')) or 'False'}",
        f"  same_opponent_warning_reason: {_safe_text(row.get('same_opponent_warning_reason')) or 'n/a'}",
    ]


def _has_context_preview(row: pd.Series) -> bool:
    return any(
        col in row.index
        for col in (
            "pace_context_signal",
            "defense_context_signal",
            "rest_context_signal",
            "playoff_context_signal",
            "overall_context_signal",
            "context_pick_alignment",
            "context_caution_level",
            "context_preview_applied",
        )
    )


def _context_preview_lines(row: pd.Series) -> list[str]:
    return [
        f"  pace_context_signal: {_safe_text(row.get('pace_context_signal')) or 'insufficient_data'}",
        f"  defense_context_signal: {_safe_text(row.get('defense_context_signal')) or 'insufficient_data'}",
        f"  rest_context_signal: {_safe_text(row.get('rest_context_signal')) or 'insufficient_data'}",
        f"  playoff_context_signal: {_safe_text(row.get('playoff_context_signal')) or 'insufficient_data'}",
        f"  overall_context_signal: {_safe_text(row.get('overall_context_signal')) or 'insufficient_data'}",
        f"  context_pick_alignment: {_safe_text(row.get('context_pick_alignment')) or 'insufficient_data'}",
        f"  context_caution_level: {_safe_text(row.get('context_caution_level')) or 'insufficient_data'}",
        f"  context_preview_applied: {_safe_text(row.get('context_preview_applied')) or 'False'}",
    ]


def _alignment_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {"aligned": 0, "conflicted": 0, "neutral": 0, "insufficient_data": 0}
    if df.empty or "context_pick_alignment" not in df.columns:
        return counts
    series = df["context_pick_alignment"].fillna("insufficient_data").astype(str).str.strip().str.lower()
    raw_counts = series.value_counts().to_dict()
    for key in counts:
        counts[key] = int(raw_counts.get(key, 0) or 0)
    return counts


def _caution_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "insufficient_data": 0}
    if df.empty or "context_caution_level" not in df.columns:
        return counts
    series = df["context_caution_level"].fillna("insufficient_data").astype(str).str.strip().str.lower()
    raw_counts = series.value_counts().to_dict()
    for key in counts:
        counts[key] = int(raw_counts.get(key, 0) or 0)
    return counts


def _elite_context_gate_count(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    for column in ("final_elite_rejection_reason", "elite_rejection_reason"):
        if column in df.columns:
            return int(
                df[column]
                .fillna("")
                .astype(str)
                .str.strip()
                .eq("elite_reject_context_high_caution_over")
                .sum()
            )
    return 0


def _high_caution_conflicted_over_count(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    selection = df.get("selection", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    caution = df.get("context_caution_level", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    alignment = df.get("context_pick_alignment", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
    return int((selection.eq("over") & caution.eq("high") & alignment.eq("conflicted")).sum())


def _context_conflict_cause(row: pd.Series) -> str:
    if _safe_text(row.get("candidate_team_not_in_game")).lower() == "true":
        return "stale_team_not_in_game"
    if _safe_text(row.get("game_context_suppression_reason")).lower() == "team_not_in_game_context":
        return "stale_team_not_in_game"
    explicit = _safe_text(row.get("context_conflict_cause")).lower()
    if explicit:
        return explicit
    if _safe_text(row.get("selection")).lower() != "over":
        return ""
    if _safe_text(row.get("context_caution_level")).lower() != "high":
        return ""
    if _safe_text(row.get("context_pick_alignment")).lower() != "conflicted":
        return ""

    pace_under = _safe_text(row.get("pace_context_signal")).lower() == "supports_under"
    defense_under = _safe_text(row.get("defense_context_signal")).lower() == "supports_under"
    rest_under = _safe_text(row.get("rest_context_signal")).lower() == "supports_under"
    playoff_under = _safe_text(row.get("playoff_context_signal")).lower() == "supports_under"
    if pace_under and defense_under:
        return "pace_defense_combined"
    if playoff_under and defense_under:
        return "playoff_defense_combined"
    if playoff_under and not any((pace_under, defense_under, rest_under)):
        return "playoff_only"
    if defense_under:
        return "defense_driven"
    if pace_under:
        return "pace_driven"
    return ""


def _context_conflict_cause_counts(df: pd.DataFrame) -> dict[str, int]:
    counts = {bucket: 0 for bucket in CONTEXT_CONFLICT_CAUSE_BUCKETS}
    if df.empty:
        return counts
    for _, row in df.iterrows():
        cause = _context_conflict_cause(row)
        if cause:
            counts[cause] = int(counts.get(cause, 0) + 1)
    return counts


def _kelly_high_caution_over_skip_count(df: pd.DataFrame) -> int:
    if df.empty or "skip_reason" not in df.columns:
        return 0
    return int(
        df["skip_reason"]
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("context_high_caution_over")
        .sum()
    )


def _missing_or_unreadable_required_artifact(warnings: list[str], *, elite_count: int) -> bool:
    for warning in warnings:
        text = warning.lower()
        if "missing file:" not in text and "could not read csv" not in text:
            continue
        if "elite_board" in text or "full_market_board" in text:
            return True
        if elite_count > 0 and "kelly_stakes" in text:
            return True
    return False


def _daily_run_health_summary(
    *,
    elite_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
    kelly_df: pd.DataFrame,
    kelly_eligible_count: int,
    warnings: list[str],
) -> dict[str, Any]:
    flags: list[str] = []
    elite_count = int(len(elite_df))
    full_market_count = int(len(full_market_df))
    kelly_rows = int(len(kelly_df))
    context_gate_rejections = _elite_context_gate_count(full_market_df)
    elite_context_violations = _high_caution_conflicted_over_count(elite_df)
    high_caution_over_skips = _kelly_high_caution_over_skip_count(kelly_df)
    final_elite_context_clean = elite_count > 0 and elite_context_violations == 0
    valid_context_safety_blocking = (
        context_gate_rejections > 0
        and final_elite_context_clean
        and high_caution_over_skips == 0
    )

    if _missing_or_unreadable_required_artifact(warnings, elite_count=elite_count):
        flags.append("required_artifact_missing_or_unreadable")
    if elite_count == 0:
        flags.append("elite_board_empty")
    if kelly_rows > 0 and kelly_eligible_count == 0:
        flags.append("kelly_rows_exist_no_eligible")
    if kelly_rows > 0 and (high_caution_over_skips / kelly_rows) >= RUN_HEALTH_CONTEXT_BLOCKED_RATIO:
        flags.append("kelly_context_high_caution_over_skip_rate_high")
    if (
        full_market_count > 0
        and (context_gate_rejections / full_market_count) >= RUN_HEALTH_CONTEXT_BLOCKED_RATIO
        and not valid_context_safety_blocking
    ):
        flags.append("elite_context_gate_rejection_rate_high")
    elif valid_context_safety_blocking:
        flags.append("valid_context_safety_blocking")
    if elite_context_violations > 0:
        flags.append("elite_contains_context_blocked_over")

    if "required_artifact_missing_or_unreadable" in flags:
        status = "ERROR_OR_INCOMPLETE"
        reason = "Required artifact/read validation failed."
    elif "elite_board_empty" in flags or "kelly_rows_exist_no_eligible" in flags:
        status = "NO_BET"
        reason = "No stakeable picks are available."
    elif any(
        flag in flags
        for flag in (
            "kelly_context_high_caution_over_skip_rate_high",
            "elite_context_gate_rejection_rate_high",
            "elite_contains_context_blocked_over",
        )
    ):
        status = "DEGRADED_CONTEXT_BLOCKED"
        reason = (
            f"Context safety blocked {context_gate_rejections} candidate(s) and Kelly skipped "
            f"{high_caution_over_skips}/{kelly_rows} row(s) for high-caution OVER context."
        )
    elif "valid_context_safety_blocking" in flags:
        status = "HEALTHY_CONTEXT_GATED"
        reason = (
            f"Context safety blocked {context_gate_rejections} high-caution OVER candidate(s); "
            f"final Elite is clean with {kelly_eligible_count} Kelly-eligible pick(s)."
        )
    elif 0 < kelly_eligible_count < RUN_HEALTH_LOW_VOLUME_KELLY_ELIGIBLE:
        status = "HEALTHY_LOW_VOLUME"
        flags.append("kelly_eligible_low_volume")
        reason = f"Run is valid with {kelly_eligible_count} Kelly-eligible pick(s)."
    else:
        status = "HEALTHY"
        reason = f"Run passed health checks with {kelly_eligible_count} Kelly-eligible pick(s)."

    return {
        "status": status,
        "reason": reason,
        "flags": sorted(dict.fromkeys(flags)),
        "recommendation": RUN_HEALTH_RECOMMENDATIONS[status],
    }


def _alignment_performance_line(label: str, item: Any) -> str:
    payload = item if isinstance(item, dict) else {}
    graded = payload.get("graded_picks", payload.get("graded_count", 0))
    pending = payload.get("pending_picks", payload.get("pending_count", 0))
    return (
        f"- {label}: "
        f"graded={int(graded or 0)}, "
        f"pending={int(pending or 0)}, "
        f"hit_rate={_format_pct(payload.get('hit_rate'))}, "
        f"roi={_format_pct(payload.get('roi'))}, "
        f"status={_safe_text(payload.get('status')) or 'insufficient_sample'}"
    )


def _kelly_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    side = _safe_text(row.get("selection")) or "n/a"
    line = _format_num(row.get("line"), 1)
    stake = _format_money(_safe_float(row.get("stake_amount")))
    ev = _format_money(_safe_float(row.get("expected_value")))
    edge = _format_pct(row.get("edge_pct"))
    caution = _safe_text(row.get("context_caution_level")) or "insufficient_data"
    action = _safe_text(row.get("recommended_action")) or "OK_TO_CONSIDER"
    reason = _safe_text(row.get("manual_review_reason"))
    reason_text = f" manual_review_reason={reason}" if reason else ""
    return (
        f"- {player}: {market} {side} {line} stake={stake} EV={ev} "
        f"edge={edge} caution={caution} recommended_action={action}{reason_text}"
    )


def build_daily_summary(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[str, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"
    warnings: list[str] = []

    elite_path = operator_dir / f"elite_board_{prediction_date}.csv"
    kelly_path = operator_dir / f"kelly_stakes_{prediction_date}.csv"
    elite_df = _read_csv(elite_path, warnings)
    kelly_df = (
        _read_csv(kelly_path, [])
        if _elite_artifact_has_no_rows(elite_path, elite_df)
        else _read_csv(kelly_path, warnings)
    )
    full_market_df = _read_csv(operator_dir / f"full_market_board_{prediction_date}.csv", warnings)
    kelly_df = _kelly_df_for_reporting(
        elite_path=elite_path,
        elite_df=elite_df,
        kelly_df=kelly_df,
        warnings=warnings,
    )
    shadow = _read_json(diagnostics_dir / f"market_shadow_grading_{prediction_date}.json", warnings)
    readiness = _read_json(diagnostics_dir / f"market_performance_readiness_{prediction_date}.json", warnings)
    manual_context = _read_json(diagnostics_dir / f"manual_context_{prediction_date}.json", warnings)
    manual_review_history_df = _read_csv(history_root / "manual_review_history.csv", [])

    kelly_eligible = (
        kelly_df[kelly_df["eligible"].map(_is_truthy)].copy()
        if not kelly_df.empty and "eligible" in kelly_df.columns
        else kelly_df.copy()
    )
    total_exposure = (
        float(pd.to_numeric(kelly_eligible.get("stake_amount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not kelly_eligible.empty
        else 0.0
    )
    expected_ev = (
        float(pd.to_numeric(kelly_eligible.get("expected_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not kelly_eligible.empty
        else 0.0
    )
    kelly_manual_review_required_count = (
        int(kelly_df["manual_review_required"].map(_is_truthy).sum())
        if not kelly_df.empty and "manual_review_required" in kelly_df.columns
        else 0
    )
    kelly_review_before_bet_count = (
        int(
            kelly_df["recommended_action"]
            .fillna("")
            .astype(str)
            .str.strip()
            .eq("REVIEW_BEFORE_BET")
            .sum()
        )
        if not kelly_df.empty and "recommended_action" in kelly_df.columns
        else 0
    )
    kelly_review_policy_hold_count = (
        int(
            kelly_df["stake_policy"]
            .fillna("")
            .astype(str)
            .str.strip()
            .eq("HOLD")
            .sum()
        )
        if not kelly_df.empty and "stake_policy" in kelly_df.columns
        else 0
    )
    _mr_date_df = (
        manual_review_history_df[
            manual_review_history_df["prediction_date"].astype(str).str.strip().eq(str(prediction_date))
        ].copy()
        if not manual_review_history_df.empty and "prediction_date" in manual_review_history_df.columns
        else pd.DataFrame()
    )
    _mr_decisions = (
        _mr_date_df["decision"].fillna("").astype(str).str.strip()
        if not _mr_date_df.empty and "decision" in _mr_date_df.columns
        else pd.Series(dtype=str)
    )
    mr_total = int(len(_mr_date_df))
    mr_pending = int(_mr_decisions.eq("undecided").sum())
    mr_skipped = int(_mr_decisions.eq("skip").sum())
    mr_played = int(_mr_decisions.eq("play").sum())
    mr_reduced = int(_mr_decisions.eq("reduce_stake").sum())
    mr_decisions_recorded = mr_total - mr_pending

    counts = _market_counts(full_market_df)
    elite_alignment = _alignment_counts(elite_df)
    elite_caution = _caution_counts(elite_df)
    full_market_alignment = _alignment_counts(full_market_df)
    full_market_context_conflict_causes = _context_conflict_cause_counts(full_market_df)
    elite_manual_review = manual_review_summary(elite_df)
    full_market_manual_review = manual_review_summary(full_market_df)
    elite_context_gate_count = _elite_context_gate_count(full_market_df)
    high_caution_over_watchlist = build_high_caution_over_watchlist(full_market_df)
    high_caution_over_watchlist_path = watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    combo_under_watchlist = build_combo_under_watchlist(full_market_df)
    combo_under_watchlist_path = combo_under_watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    market_shadow_history_path = history_root / "market_shadow_history.csv"
    market_readiness_summary_path = history_root / "market_readiness_summary.csv"
    promotion_readiness_text_path, promotion_readiness_csv_path = promotion_readiness_report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    promotion_readiness_report = build_promotion_readiness_report(
        read_market_shadow_history(market_shadow_history_path),
        through_date=prediction_date,
    )
    paper_kelly_text_path, paper_kelly_csv_path = paper_kelly_report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    paper_kelly_simulation = build_paper_kelly_simulation(
        prediction_date=prediction_date,
        combo_under_watchlist=combo_under_watchlist,
        high_caution_over_watchlist=high_caution_over_watchlist,
    )
    paper_kelly_exposure = (
        float(pd.to_numeric(paper_kelly_simulation.get("simulated_stake", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not paper_kelly_simulation.empty
        else 0.0
    )
    paper_kelly_pre_cap_exposure = (
        float(pd.to_numeric(paper_kelly_simulation.get("pre_cap_simulated_stake", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not paper_kelly_simulation.empty
        else 0.0
    )
    paper_kelly_cap_reduced = paper_kelly_pre_cap_exposure - paper_kelly_exposure
    cap_reason_counts: dict[str, int] = {}
    if not paper_kelly_simulation.empty and "cap_adjustment_reason" in paper_kelly_simulation.columns:
        reason_series = (
            paper_kelly_simulation["cap_adjustment_reason"]
            .fillna("none")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        cap_reason_counts = {
            str(reason): int(count)
            for reason, count in reason_series.value_counts().head(5).items()
        }
    paper_kelly_history_csv_path = paper_kelly_history_path(history_root)
    paper_kelly_performance_text_path, paper_kelly_performance_csv_path = paper_kelly_performance_report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    paper_kelly_history = read_paper_kelly_history(paper_kelly_history_csv_path)
    paper_kelly_performance_report = build_paper_kelly_performance_report(
        paper_kelly_history,
        through_date=prediction_date,
    )
    paper_kelly_performance_summary = summarize_paper_kelly_history(
        paper_kelly_history,
        through_date=prediction_date,
    )
    correlation_exposure_text_path, correlation_exposure_csv_path = correlation_exposure_report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    correlation_exposure_report, correlation_exposure_summary = build_correlation_exposure_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    team_distribution_text_path, team_distribution_csv_path = team_distribution_report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    team_distribution_report_df, team_distribution_summary = build_team_distribution_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    market_shadow_rows = int(len(full_market_df))
    market_shadow_non_points_rows = (
        int(
            full_market_df["market_type"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .ne(PLAYER_POINTS_MARKET)
            .sum()
        )
        if not full_market_df.empty and "market_type" in full_market_df.columns
        else 0
    )
    market_shadow_counts = (
        Counter(
            _safe_text(value) or "unknown"
            for value in full_market_df["market_type"]
        )
        if not full_market_df.empty and "market_type" in full_market_df.columns
        else Counter()
    )
    run_health = _daily_run_health_summary(
        elite_df=elite_df,
        full_market_df=full_market_df,
        kelly_df=kelly_df,
        kelly_eligible_count=int(len(kelly_eligible)),
        warnings=warnings,
    )
    shadow_totals = shadow.get("totals", {}) if isinstance(shadow, dict) else {}
    context_alignment_performance = (
        shadow.get("context_alignment_performance", {})
        if isinstance(shadow, dict)
        else {}
    )
    kelly_decision_performance = (
        shadow.get("kelly_decision_performance", {})
        if isinstance(shadow, dict)
        else {}
    )
    if not kelly_decision_performance:
        if _elite_artifact_has_no_rows(elite_path, elite_df):
            kelly_decision_performance = _empty_kelly_decision_performance("elite_board_empty")
        else:
            kelly_decision_performance = build_kelly_decision_performance(
                prediction_date=prediction_date,
                runtime_root=runtime_root,
                out_dir=runtime_root.parent if runtime_root.name == "runtime" else runtime_root,
            )
    pending_grading = int(shadow_totals.get("pending_picks") or 0)

    readiness_markets = readiness.get("markets", []) if isinstance(readiness, dict) else []
    rejection_counts = readiness.get("rejection_count_by_market_type_reason", {}) if isinstance(readiness, dict) else {}

    lines = [
        f"Daily Summary - {prediction_date}",
        "=" * 72,
        "Scope: elite board and Kelly remain locked to player_points only.",
        "",
        "Run Health",
        "-" * 72,
        f"- status: {run_health['status']}",
        f"- reason: {run_health['reason']}",
        "- flags:",
    ]
    if run_health["flags"]:
        for flag in run_health["flags"]:
            lines.append(f"  - {flag}")
    else:
        lines.append("  - none")
    lines.extend(
        [
            f"- recommendation: {run_health['recommendation']}",
            "",
            "Elite Picks",
            "-" * 72,
        ]
    )
    if elite_df.empty:
        lines.append("- None")
    else:
        for _, row in _sort_for_display(elite_df).iterrows():
            lines.append(_pick_line(row))
            if _has_manual_context(row):
                lines.extend(_manual_context_lines(row))
            if _has_manual_review(row):
                lines.extend(_manual_review_lines(row))
            if _has_context_preview(row):
                lines.extend(_context_preview_lines(row))

    lines.extend(["", "Manual Review Warnings", "-" * 72])
    lines.append(
        "- elite: "
        f"same_opponent_under_warning_count={elite_manual_review['same_opponent_under_warning_count']}, "
        f"manual_review_required_count={elite_manual_review['manual_review_required_count']}"
    )
    lines.append(
        "- full_market: "
        f"same_opponent_under_warning_count={full_market_manual_review['same_opponent_under_warning_count']}, "
        f"manual_review_required_count={full_market_manual_review['manual_review_required_count']}"
    )
    lines.append("- mode: passive_diagnostic_only")

    lines.extend(["", "Context-Pick Alignment", "-" * 72])
    lines.append(
        "- elite: "
        f"aligned={elite_alignment['aligned']}, "
        f"conflicted={elite_alignment['conflicted']}, "
        f"neutral={elite_alignment['neutral']}, "
        f"insufficient_data={elite_alignment['insufficient_data']}"
    )
    lines.append(
        "- full_market: "
        f"aligned={full_market_alignment['aligned']}, "
        f"conflicted={full_market_alignment['conflicted']}, "
        f"neutral={full_market_alignment['neutral']}, "
        f"insufficient_data={full_market_alignment['insufficient_data']}"
    )
    lines.append(
        "- elite caution: "
        f"high={elite_caution['high']}, "
        f"medium={elite_caution['medium']}, "
        f"low={elite_caution['low']}, "
        f"insufficient_data={elite_caution['insufficient_data']}"
    )
    lines.append("- full_market context conflict causes:")
    for cause, count in sorted(full_market_context_conflict_causes.items()):
        lines.append(f"  - {cause}: {count}")

    lines.extend(["", "High-Caution OVER Watchlist — Observation Only / No Stake", "-" * 72])
    lines.append(f"- watchlist row count: {int(len(high_caution_over_watchlist))}")
    lines.append(f"- artifact: {high_caution_over_watchlist_path}")
    lines.append(f"- note: {OBSERVATION_ONLY_NOTE}")
    lines.append("- top 5 by edge:")
    if high_caution_over_watchlist.empty:
        lines.append("  - None")
    else:
        for _, row in high_caution_over_watchlist.head(5).iterrows():
            lines.append(f"  - {watchlist_row_line(row)}")

    lines.extend(["", "Market Expansion Shadow Tracking", "-" * 72])
    lines.append(f"- total shadow rows: {market_shadow_rows}")
    lines.append(f"- non-points rows: {market_shadow_non_points_rows}")
    lines.append(f"- shadow history artifact: {market_shadow_history_path}")
    lines.append(f"- readiness artifact: {market_readiness_summary_path}")
    lines.append("- top markets by shadow rows:")
    if market_shadow_counts:
        for market, count in market_shadow_counts.most_common(5):
            lines.append(f"  - {market}: {count}")
    else:
        lines.append("  - None")
    lines.append("- warning: Observation only; not Kelly eligible.")

    lines.extend(["", "Combo UNDER Promotion Watchlist — Observation Only / No Kelly", "-" * 72])
    lines.append(f"- watchlist row count: {int(len(combo_under_watchlist))}")
    lines.append(f"- artifact: {combo_under_watchlist_path}")
    lines.append(f"- note: {COMBO_UNDER_OBSERVATION_ONLY_NOTE}")
    lines.append("- top 5 by absolute edge:")
    if combo_under_watchlist.empty:
        lines.append("  - None")
    else:
        for _, row in combo_under_watchlist.head(5).iterrows():
            lines.append(f"  - {combo_under_watchlist_row_line(row)}")

    lines.extend(["", "Promotion Readiness — Observation Only", "-" * 72])
    lines.append(f"- report row count: {int(len(promotion_readiness_report))}")
    lines.append(f"- txt artifact: {promotion_readiness_text_path}")
    lines.append(f"- csv artifact: {promotion_readiness_csv_path}")
    lines.append(f"- note: {PROMOTION_READINESS_OBSERVATION_ONLY_NOTE}")
    lines.append("- top 5 by promotion status/sample:")
    if promotion_readiness_report.empty:
        lines.append("  - None")
    else:
        for _, row in promotion_readiness_report.head(5).iterrows():
            lines.append(f"  - {promotion_readiness_report_row_line(row)}")

    lines.extend(["", "Paper Kelly Simulation — Observation Only / No Real Stake", "-" * 72])
    lines.append(f"- total paper rows: {int(len(paper_kelly_simulation))}")
    lines.append(f"- pre-cap exposure: {paper_kelly_pre_cap_exposure:.6f}")
    lines.append(f"- post-cap exposure: {paper_kelly_exposure:.6f}")
    lines.append(f"- exposure reduced by caps: {paper_kelly_cap_reduced:.6f}")
    lines.append(f"- txt artifact: {paper_kelly_text_path}")
    lines.append(f"- csv artifact: {paper_kelly_csv_path}")
    lines.append(f"- warning: {PAPER_KELLY_SIMULATION_WARNING}")
    lines.append("- exposure by bucket:")
    if paper_kelly_simulation.empty:
        lines.append("  - none")
    else:
        bucket_exposure = paper_kelly_simulation.groupby("paper_bucket", sort=True)["simulated_stake"].sum()
        for bucket, value in bucket_exposure.items():
            lines.append(f"  - {bucket}: {float(value):.6f}")
    lines.append("- top cap reasons:")
    if cap_reason_counts:
        for reason, count in sorted(cap_reason_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - {reason}: {count}")
    else:
        lines.append("  - none")
    lines.append("- top 10 simulated EV rows:")
    if paper_kelly_simulation.empty:
        lines.append("  - None")
    else:
        top_rows = paper_kelly_simulation.sort_values("simulated_ev", ascending=False, kind="mergesort").head(10)
        for _, row in top_rows.iterrows():
            lines.append(
                "  - "
                f"{_safe_text(row.get('player_name')) or 'Unknown'}: "
                f"{_safe_text(row.get('market_type')) or 'unknown'} "
                f"{_safe_text(row.get('selection')) or 'unknown'} "
                f"{_safe_text(row.get('line')) or 'n/a'} "
                f"bucket={_safe_text(row.get('paper_bucket')) or 'unknown'} "
                f"dir_edge={_format_num(row.get('directional_edge'), 3)} "
                f"stake={_format_num(row.get('simulated_stake'), 6)} "
                f"ev={_format_num(row.get('simulated_ev'), 6)}"
            )

    lines.extend(["", PAPER_KELLY_PERFORMANCE_TITLE, "-" * 72])
    lines.append("- scope: reporting/history only; no real Kelly promotion.")
    lines.append(f"- history artifact: {paper_kelly_history_csv_path}")
    lines.append(f"- txt artifact: {paper_kelly_performance_text_path}")
    lines.append(f"- csv artifact: {paper_kelly_performance_csv_path}")
    lines.append(
        "- summary: "
        f"total={int(paper_kelly_performance_summary.get('total') or 0)}, "
        f"graded_total={int(paper_kelly_performance_summary.get('graded_total') or 0)}, "
        f"hits={int(paper_kelly_performance_summary.get('hits') or 0)}, "
        f"misses={int(paper_kelly_performance_summary.get('misses') or 0)}, "
        f"pushes={int(paper_kelly_performance_summary.get('pushes') or 0)}, "
        f"pending={int(paper_kelly_performance_summary.get('pending') or 0)}, "
        f"hit_rate={_format_pct(paper_kelly_performance_summary.get('hit_rate'))}, "
        f"paper_roi={_format_pct(paper_kelly_performance_summary.get('paper_roi'))}, "
        f"sample_status={_safe_text(paper_kelly_performance_summary.get('sample_status')) or 'no_graded_results'}"
    )
    lines.append("- grouped performance:")
    if paper_kelly_performance_report.empty:
        lines.append("  - None")
    else:
        for _, row in paper_kelly_performance_report.head(10).iterrows():
            lines.append(f"  - {paper_kelly_performance_row_line(row)}")

    lines.extend(["", CORRELATION_EXPOSURE_TITLE, "-" * 72])
    lines.append("- scope: observation only; no eligibility, scoring, gate, or stake changes.")
    lines.append(f"- txt artifact: {correlation_exposure_text_path}")
    lines.append(f"- csv artifact: {correlation_exposure_csv_path}")
    lines.append(
        "- summary: "
        f"risk={_safe_text(correlation_exposure_summary.get('risk_label')) or 'low'}, "
        f"total_rows={int(correlation_exposure_summary.get('total_rows') or 0)}, "
        f"repeated_player_count={int(correlation_exposure_summary.get('repeated_player_count') or 0)}, "
        f"max_rows_per_player={int(correlation_exposure_summary.get('max_rows_per_player') or 0)}, "
        f"max_rows_per_game={int(correlation_exposure_summary.get('max_rows_per_game') or 0)}, "
        f"max_rows_per_team={int(correlation_exposure_summary.get('max_rows_per_team') or 0)}, "
        f"dominant_side={_safe_text(correlation_exposure_summary.get('dominant_side')) or 'none'}, "
        f"dominant_side_share={_format_pct(correlation_exposure_summary.get('dominant_side_share'))}, "
        "multi_bucket_players="
        f"{int(correlation_exposure_summary.get('players_in_multiple_buckets') or 0)}, "
        "multi_market_same_direction_players="
        f"{int(correlation_exposure_summary.get('players_multiple_markets_same_direction') or 0)}"
    )
    lines.append("- top risk groups:")
    if correlation_exposure_report.empty:
        lines.append("  - None")
    else:
        for _, row in correlation_exposure_report.head(10).iterrows():
            lines.append(f"  - {correlation_exposure_row_line(row)}")
    for warning in correlation_exposure_summary.get("warnings", []) or []:
        lines.append(f"- warning: {warning}")

    lines.extend(["", TEAM_DISTRIBUTION_TITLE, "-" * 72])
    lines.append(f"- scope: {TEAM_DISTRIBUTION_OBSERVATION_ONLY_NOTE}")
    lines.append(f"- txt artifact: {team_distribution_text_path}")
    lines.append(f"- csv artifact: {team_distribution_csv_path}")
    lines.append(
        "- summary: "
        f"total_teams={int(team_distribution_summary.get('total_teams') or 0)}, "
        f"teams_with_elite={int(team_distribution_summary.get('teams_with_elite') or 0)}, "
        f"teams_with_paper={int(team_distribution_summary.get('teams_with_paper') or 0)}, "
        f"teams_cap_limited={int(team_distribution_summary.get('teams_cap_limited') or 0)}, "
        f"teams_context_rejected={int(team_distribution_summary.get('teams_context_rejected') or 0)}, "
        f"most_represented={_safe_text(team_distribution_summary.get('most_represented_team')) or 'none'} "
        f"({int(team_distribution_summary.get('most_represented_count') or 0)} rows)"
    )
    lines.append("- top 10 teams:")
    if team_distribution_report_df.empty:
        lines.append("  - None")
    else:
        for _, row in team_distribution_report_df.head(10).iterrows():
            lines.append(f"  - {team_distribution_report_row_line(row)}")
    for warning in team_distribution_summary.get("warnings", []) or []:
        lines.append(f"- warning: {warning}")

    lines.extend(["", "Kelly Stakes", "-" * 72])
    if kelly_eligible.empty:
        lines.append("- None")
    else:
        for _, row in _sort_for_display(kelly_eligible).iterrows():
            lines.append(_kelly_line(row))
    lines.append(f"Total exposure: {_format_money(total_exposure)}")
    lines.append(f"Expected EV: {_format_money(expected_ev)}")
    lines.append(f"manual_review_required_count: {kelly_manual_review_required_count}")
    lines.append(f"review_before_bet_count: {kelly_review_before_bet_count}")
    lines.append(f"hold_policy_count: {kelly_review_policy_hold_count}")
    lines.append(f"clear_policy_count: {len(kelly_df) - kelly_review_policy_hold_count if not kelly_df.empty else 0}")
    lines.append(f"do_not_bet_until_reviewed_count: {kelly_review_policy_hold_count}")

    lines.extend(["", "Manual Review Decisions", "-" * 72])
    lines.append(f"- manual_review_picks: {mr_total}")
    lines.append(f"- decisions_recorded: {mr_decisions_recorded}")
    lines.append(f"- pending: {mr_pending}")
    lines.append(f"- skipped: {mr_skipped}")
    lines.append(f"- played: {mr_played}")
    lines.append(f"- reduced_stake: {mr_reduced}")
    if mr_total == 0:
        lines.append("- no manual review decisions recorded for this date")

    lines.extend(["", "Kelly Decision Performance", "-" * 72])
    by_eligible = kelly_decision_performance.get("by_kelly_eligible", {}) if isinstance(kelly_decision_performance, dict) else {}
    lines.append(_alignment_performance_line("kelly_eligible=true", by_eligible.get("true", {}) if isinstance(by_eligible, dict) else {}))
    lines.append(_alignment_performance_line("kelly_eligible=false", by_eligible.get("false", {}) if isinstance(by_eligible, dict) else {}))
    by_skip = kelly_decision_performance.get("by_skip_reason", {}) if isinstance(kelly_decision_performance, dict) else {}
    if isinstance(by_skip, dict) and by_skip:
        lines.append("By skip reason:")
        for reason, item in sorted(by_skip.items()):
            lines.append(_alignment_performance_line(reason, item))
    else:
        lines.append("- insufficient_sample")

    lines.extend(["", "Full-Market Market Counts", "-" * 72])
    if counts:
        for market, count in sorted(counts.items()):
            lines.append(f"- {market}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "Shadow Grading Totals", "-" * 72])
    lines.append(f"- total picks: {int(shadow_totals.get('total_picks') or 0)}")
    lines.append(f"- graded picks: {int(shadow_totals.get('graded_picks') or 0)}")
    lines.append(f"- pending picks: {pending_grading}")
    lines.append(f"- hit rate: {_format_pct(shadow_totals.get('hit_rate'))}")
    lines.append(f"Pending grading count: {pending_grading}")

    lines.extend(["", "Context Alignment Performance", "-" * 72])
    if not context_alignment_performance:
        lines.append("- insufficient sample: no context alignment performance payload yet")
    else:
        by_alignment = context_alignment_performance.get("by_alignment", {})
        for alignment_key in ("aligned", "conflicted", "neutral"):
            item = by_alignment.get(alignment_key, {}) if isinstance(by_alignment, dict) else {}
            lines.append(_alignment_performance_line(alignment_key, item))
        if context_alignment_performance.get("status") == "insufficient_sample":
            lines.append("- note: no resolved graded hit/miss sample yet; performance is pending.")

        by_side = context_alignment_performance.get("by_alignment_and_selection_side", {})
        side_lines: list[str] = []
        if isinstance(by_side, dict):
            for alignment_key in ("aligned", "conflicted", "neutral"):
                side_payload = by_side.get(alignment_key, {})
                if not isinstance(side_payload, dict):
                    continue
                for side in ("over", "under"):
                    item = side_payload.get(side, {})
                    if isinstance(item, dict) and int(item.get("total_picks", 0) or 0) > 0:
                        side_lines.append(_alignment_performance_line(f"{alignment_key}/{side}", item))
        if side_lines:
            lines.append("By selection side:")
            lines.extend(side_lines)

        by_market = context_alignment_performance.get("by_alignment_and_market_type", {})
        market_lines: list[str] = []
        if isinstance(by_market, dict):
            for alignment_key in ("aligned", "conflicted", "neutral"):
                market_payload = by_market.get(alignment_key, {})
                if not isinstance(market_payload, dict):
                    continue
                for market_type, item in sorted(market_payload.items()):
                    if isinstance(item, dict) and int(item.get("total_picks", 0) or 0) > 0:
                        market_lines.append(_alignment_performance_line(f"{alignment_key}/{market_type}", item))
        if market_lines:
            lines.append("By market type:")
            lines.extend(market_lines)

    lines.extend(["", "Manual Context", "-" * 72])
    if manual_context:
        file_found = _is_truthy(manual_context.get("file_found"))
        rows = int(manual_context.get("rows") or 0)
        matches = int(manual_context.get("candidate_matches") or 0)
        lines.append(f"- file found: {str(file_found).lower()}")
        lines.append(f"- rows: {rows}")
        lines.append(f"- candidate matches: {matches}")
        lines.append("- passive mode: true")
        if manual_context.get("warnings"):
            for warning in manual_context.get("warnings") or []:
                lines.append(f"- warning: {warning}")
    else:
        lines.append("- file found: false")
        lines.append("- candidate matches: 0")
        lines.append("- passive mode: true")

    lines.extend(["", "Warnings / Readiness Notes", "-" * 72])
    lines.append("- Elite board locked to player_points only.")
    lines.append("- Kelly stakes locked to player_points only.")
    lines.append("- Context preview signals do not alter projections.")
    lines.append("- High-caution conflicted OVER context gates final elite admission and Kelly staking.")
    lines.append("- Same-opponent rematch manual-review flags are diagnostic only; no pick is removed automatically.")
    if elite_context_gate_count:
        lines.append(
            f"- Elite context safety gate excluded {elite_context_gate_count} "
            "high-caution conflicted OVER candidate(s) from final elite."
        )
    if elite_df.empty and elite_context_gate_count:
        lines.append("- No-bet condition: elite board is empty after the context safety gate.")
    if manual_context:
        matches = int(manual_context.get("candidate_matches") or 0)
        lines.append(f"- Manual player context is diagnostic only; matched candidates: {matches}.")
    non_points = sum(count for market, count in counts.items() if market != "player_points")
    if non_points:
        lines.append(f"- {non_points} non-points full-market picks are diagnostic/readiness only.")
    if readiness_markets:
        for row in readiness_markets:
            market = _safe_text(row.get("market_type")) or "unknown"
            count = int(row.get("full_market_count") or row.get("count") or 0)
            avg_confidence = _format_num(row.get("avg_confidence"), 3)
            avg_quality = _format_num(row.get("avg_quality_score"), 2)
            lines.append(f"- readiness {market}: count={count}, avg_conf={avg_confidence}, avg_quality={avg_quality}")
    if rejection_counts:
        total_rejections = 0
        for reasons in rejection_counts.values():
            if isinstance(reasons, dict):
                total_rejections += sum(int(value or 0) for value in reasons.values())
        if total_rejections:
            lines.append(f"- full-market gate rejections tracked: {total_rejections}")
    for warning in warnings:
        lines.append(f"- WARNING: {warning}")

    metadata = {
        "elite_count": int(len(elite_df)),
        "kelly_eligible_count": int(len(kelly_eligible)),
        "total_exposure": round(total_exposure, 2),
        "expected_ev": round(expected_ev, 2),
        "full_market_counts": dict(sorted(counts.items())),
        "elite_context_alignment": elite_alignment,
        "elite_context_caution": elite_caution,
        "elite_high_caution_count": elite_caution["high"],
        "elite_medium_caution_count": elite_caution["medium"],
        "elite_low_caution_count": elite_caution["low"],
        "elite_context_safety_gate_rejected_count": elite_context_gate_count,
        "same_opponent_under_warning_count": full_market_manual_review["same_opponent_under_warning_count"],
        "manual_review_required_count": full_market_manual_review["manual_review_required_count"],
        "elite_same_opponent_under_warning_count": elite_manual_review["same_opponent_under_warning_count"],
        "elite_manual_review_required_count": elite_manual_review["manual_review_required_count"],
        "kelly_manual_review_required_count": kelly_manual_review_required_count,
        "kelly_review_before_bet_count": kelly_review_before_bet_count,
        "high_caution_over_watchlist_count": int(len(high_caution_over_watchlist)),
        "high_caution_over_watchlist_path": str(high_caution_over_watchlist_path),
        "combo_under_watchlist_count": int(len(combo_under_watchlist)),
        "combo_under_watchlist_path": str(combo_under_watchlist_path),
        "promotion_readiness_report_count": int(len(promotion_readiness_report)),
        "promotion_readiness_report_path": str(promotion_readiness_text_path),
        "promotion_readiness_report_csv_path": str(promotion_readiness_csv_path),
        "paper_kelly_simulation_count": int(len(paper_kelly_simulation)),
        "paper_kelly_simulation_exposure": round(paper_kelly_exposure, 6),
        "paper_kelly_pre_cap_exposure": round(paper_kelly_pre_cap_exposure, 6),
        "paper_kelly_cap_reduced": round(paper_kelly_cap_reduced, 6),
        "paper_kelly_cap_reason_counts": cap_reason_counts,
        "paper_kelly_simulation_path": str(paper_kelly_text_path),
        "paper_kelly_simulation_csv_path": str(paper_kelly_csv_path),
        "paper_kelly_history_path": str(paper_kelly_history_csv_path),
        "paper_kelly_performance_report_count": int(len(paper_kelly_performance_report)),
        "paper_kelly_performance_report_path": str(paper_kelly_performance_text_path),
        "paper_kelly_performance_report_csv_path": str(paper_kelly_performance_csv_path),
        "paper_kelly_performance_summary": paper_kelly_performance_summary,
        "correlation_exposure_report_count": int(len(correlation_exposure_report)),
        "correlation_exposure_report_path": str(correlation_exposure_text_path),
        "correlation_exposure_report_csv_path": str(correlation_exposure_csv_path),
        "correlation_exposure_summary": correlation_exposure_summary,
        "team_distribution_report_count": int(len(team_distribution_report_df)),
        "team_distribution_report_path": str(team_distribution_text_path),
        "team_distribution_report_csv_path": str(team_distribution_csv_path),
        "team_distribution_summary": team_distribution_summary,
        "market_shadow_history_path": str(market_shadow_history_path),
        "market_readiness_summary_path": str(market_readiness_summary_path),
        "market_shadow_rows": market_shadow_rows,
        "market_shadow_non_points_rows": market_shadow_non_points_rows,
        "market_shadow_top_markets": dict(market_shadow_counts.most_common(5)),
        "full_market_context_conflict_cause_counts": full_market_context_conflict_causes,
        "stale_team_not_in_game_count": int(full_market_context_conflict_causes.get("stale_team_not_in_game", 0)),
        "run_health": run_health,
        "run_health_status": run_health["status"],
        "run_health_reason": run_health["reason"],
        "run_health_flags": run_health["flags"],
        "run_health_recommendation": run_health["recommendation"],
        "full_market_context_alignment": full_market_alignment,
        "shadow_totals": shadow_totals,
        "context_alignment_performance": context_alignment_performance,
        "kelly_decision_performance": kelly_decision_performance,
        "pending_grading_count": pending_grading,
        "manual_context": manual_context,
        "warnings": warnings,
    }
    return "\n".join(lines) + "\n", metadata


def write_daily_summary_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    shadow_result: dict[str, Any] | None = None
    same_opponent_board_result = annotate_operator_board_files(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    if full_market_path.exists():
        shadow_result = persist_market_shadow_history(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
        )
    watchlist_path, watchlist_df = write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    combo_under_path, combo_under_df = write_combo_under_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    promotion_text_path, promotion_csv_path, promotion_df = write_promotion_readiness_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    paper_text_path, paper_csv_path, paper_df = write_paper_kelly_simulation(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        combo_under_watchlist=combo_under_df,
        high_caution_over_watchlist=watchlist_df,
    )
    paper_performance_text_path, paper_performance_csv_path, paper_performance_df, paper_persist_result = (
        write_paper_kelly_performance_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
        )
    )
    correlation_text_path, correlation_csv_path, correlation_df, correlation_summary = (
        write_correlation_exposure_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
        )
    )
    team_dist_text_path, team_dist_csv_path, team_dist_df, team_dist_summary = (
        write_team_distribution_report(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
        )
    )
    summary, metadata = build_daily_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    metadata["high_caution_over_watchlist_path"] = str(watchlist_path)
    metadata["high_caution_over_watchlist_count"] = int(len(watchlist_df))
    metadata["combo_under_watchlist_path"] = str(combo_under_path)
    metadata["combo_under_watchlist_count"] = int(len(combo_under_df))
    metadata["promotion_readiness_report_path"] = str(promotion_text_path)
    metadata["promotion_readiness_report_csv_path"] = str(promotion_csv_path)
    metadata["promotion_readiness_report_count"] = int(len(promotion_df))
    metadata["paper_kelly_simulation_path"] = str(paper_text_path)
    metadata["paper_kelly_simulation_csv_path"] = str(paper_csv_path)
    metadata["paper_kelly_simulation_count"] = int(len(paper_df))
    metadata["paper_kelly_simulation_exposure"] = round(
        float(pd.to_numeric(paper_df.get("simulated_stake", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not paper_df.empty
        else 0.0,
        6,
    )
    metadata["paper_kelly_history_path"] = str(paper_persist_result["paper_kelly_history_path"])
    metadata["paper_kelly_performance_report_path"] = str(paper_performance_text_path)
    metadata["paper_kelly_performance_report_csv_path"] = str(paper_performance_csv_path)
    metadata["paper_kelly_performance_report_count"] = int(len(paper_performance_df))
    metadata["paper_kelly_performance_current_date_rows"] = int(paper_persist_result["current_date_rows"])
    metadata["paper_kelly_performance_pending_rows"] = int(paper_persist_result["pending_rows"])
    metadata["correlation_exposure_report_path"] = str(correlation_text_path)
    metadata["correlation_exposure_report_csv_path"] = str(correlation_csv_path)
    metadata["correlation_exposure_report_count"] = int(len(correlation_df))
    metadata["correlation_exposure_summary"] = correlation_summary
    metadata["team_distribution_report_path"] = str(team_dist_text_path)
    metadata["team_distribution_report_csv_path"] = str(team_dist_csv_path)
    metadata["team_distribution_report_count"] = int(len(team_dist_df))
    metadata["team_distribution_summary"] = team_dist_summary
    metadata["same_opponent_board_annotation"] = same_opponent_board_result
    if shadow_result:
        metadata["market_shadow_rows"] = int(shadow_result["current_date_rows"])
        metadata["market_shadow_non_points_rows"] = int(shadow_result["current_date_non_points_rows"])
        metadata["market_shadow_history_path"] = str(shadow_result["market_shadow_history_path"])
        metadata["market_readiness_summary_path"] = str(shadow_result["market_readiness_summary_path"])
    output_path = runtime_root / "operator" / f"daily_summary_{prediction_date}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    return output_path, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the operator daily summary report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)
    output_path, metadata = write_daily_summary_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
    )
    print(f"daily_summary_txt={output_path}")
    print(
        "daily_summary_totals "
        f"elite={metadata['elite_count']} "
        f"kelly_eligible={metadata['kelly_eligible_count']} "
        f"run_health={metadata['run_health_status']} "
        f"high_caution_over_watchlist={metadata['high_caution_over_watchlist_count']} "
        f"combo_under_watchlist={metadata['combo_under_watchlist_count']} "
        f"same_opponent_under_warnings={metadata['same_opponent_under_warning_count']} "
        f"manual_review_required={metadata['manual_review_required_count']} "
        f"kelly_manual_review_required={metadata['kelly_manual_review_required_count']} "
        f"review_before_bet={metadata['kelly_review_before_bet_count']} "
        f"promotion_readiness={metadata['promotion_readiness_report_count']} "
        f"paper_kelly_simulation={metadata['paper_kelly_simulation_count']} "
        f"market_shadow_rows={metadata['market_shadow_rows']} "
        f"market_shadow_non_points={metadata['market_shadow_non_points_rows']} "
        f"exposure={metadata['total_exposure']:.2f} "
        f"expected_ev={metadata['expected_ev']:.2f} "
        f"pending_grading={metadata['pending_grading_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
