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
from courtvision.reporting.high_caution_over_watchlist import (
    OBSERVATION_ONLY_NOTE,
    build_high_caution_over_watchlist,
    watchlist_path_for_date,
    watchlist_row_line,
    write_high_caution_over_watchlist,
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
    return f"- {player}: {market} {side} {line} stake={stake} EV={ev} edge={edge} caution={caution}"


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

    elite_df = _read_csv(operator_dir / f"elite_board_{prediction_date}.csv", warnings)
    kelly_df = _read_csv(operator_dir / f"kelly_stakes_{prediction_date}.csv", warnings)
    full_market_df = _read_csv(operator_dir / f"full_market_board_{prediction_date}.csv", warnings)
    shadow = _read_json(diagnostics_dir / f"market_shadow_grading_{prediction_date}.json", warnings)
    readiness = _read_json(diagnostics_dir / f"market_performance_readiness_{prediction_date}.json", warnings)
    manual_context = _read_json(diagnostics_dir / f"manual_context_{prediction_date}.json", warnings)

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
    counts = _market_counts(full_market_df)
    elite_alignment = _alignment_counts(elite_df)
    elite_caution = _caution_counts(elite_df)
    full_market_alignment = _alignment_counts(full_market_df)
    full_market_context_conflict_causes = _context_conflict_cause_counts(full_market_df)
    elite_context_gate_count = _elite_context_gate_count(full_market_df)
    high_caution_over_watchlist = build_high_caution_over_watchlist(full_market_df)
    high_caution_over_watchlist_path = watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    market_shadow_history_path = history_root / "market_shadow_history.csv"
    market_readiness_summary_path = history_root / "market_readiness_summary.csv"
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
            if _has_context_preview(row):
                lines.extend(_context_preview_lines(row))

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

    lines.extend(["", "Kelly Stakes", "-" * 72])
    if kelly_eligible.empty:
        lines.append("- None")
    else:
        for _, row in _sort_for_display(kelly_eligible).iterrows():
            lines.append(_kelly_line(row))
    lines.append(f"Total exposure: {_format_money(total_exposure)}")
    lines.append(f"Expected EV: {_format_money(expected_ev)}")

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
        "high_caution_over_watchlist_count": int(len(high_caution_over_watchlist)),
        "high_caution_over_watchlist_path": str(high_caution_over_watchlist_path),
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
    full_market_path = runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
    if full_market_path.exists():
        shadow_result = persist_market_shadow_history(
            prediction_date=prediction_date,
            runtime_root=runtime_root,
            history_root=history_root,
        )
    summary, metadata = build_daily_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    watchlist_path, watchlist_df = write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    metadata["high_caution_over_watchlist_path"] = str(watchlist_path)
    metadata["high_caution_over_watchlist_count"] = int(len(watchlist_df))
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
        f"market_shadow_rows={metadata['market_shadow_rows']} "
        f"market_shadow_non_points={metadata['market_shadow_non_points_rows']} "
        f"exposure={metadata['total_exposure']:.2f} "
        f"expected_ev={metadata['expected_ev']:.2f} "
        f"pending_grading={metadata['pending_grading_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
