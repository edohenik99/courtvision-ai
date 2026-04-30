from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

from .buckets import (
    confidence_band,
    injury_confidence_delta_value,
    injury_influence_bucket,
    injury_projection_delta_value,
    market_trust_weight_band,
    minutes_bucket,
    odds_bucket,
    player_profile_bucket,
    player_points_line_band,
    quality_band,
    to_float,
)
from courtvision.runtime_selection import elite_points_risk_guard_reason


CONTEXT_ALIGNMENT_VALUES = {"aligned", "conflicted", "neutral", "insufficient_data"}
CONTEXT_CAUTION_VALUES = {"high", "medium", "low", "insufficient_data"}


def _edge_bucket(edge_abs: float) -> str:
    """Categorize absolute edge into buckets for hit rate analysis.
    
    Buckets:
    - 0.00-1.99: low_edge
    - 2.00-3.99: medium_edge  
    - 4.00-5.99: high_edge
    - 6.00+: very_high_edge
    """
    if edge_abs < 2.0:
        return "0.00-1.99"
    elif edge_abs < 4.0:
        return "2.00-3.99"
    elif edge_abs < 6.0:
        return "4.00-5.99"
    else:
        return "6.00+"


def summarize_graded_props(graded_rows: Sequence[Any]) -> dict[str, Any]:
    normalized_rows = [_normalize_graded_row(item) for item in graded_rows]
    resolved_rows = [row for row in normalized_rows if row["result"] in {"win", "loss", "push"}]

    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {
        "by_prop_type": {},
        "by_side": {},
        "by_quality_band": {},
        "by_confidence_band": {},
        "by_odds_bucket": {},
        "by_minutes_bucket": {},
        "by_player_profile_bucket": {},
        "by_player_points_line_band": {},
        "by_injury_influence_bucket": {},
        "by_final_selection_source_lane": {},
        "by_blocked_by_elite_points_risk_guard": {},
        "by_elite_points_risk_guard_reason": {},
        "by_elite_points_ranking_reason": {},
        "by_player_points_elite_outcome": {},
        "by_player_points_realism_dampener_reason": {},
        "by_market_trust_weight_band": {},
        "by_edge_bucket": {},  # NEW: Hit rate by edge magnitude
        "by_qualification_reason": {},  # NEW: Hit rate by qualification reason
        "by_context_pick_alignment": {},
        "by_context_caution_level": {},
        "by_kelly_eligible": {},
        "by_skip_reason": {},
        "joint_quality_confidence": {},
        "joint_prop_minutes": {},
        "joint_prop_side": {},
        "joint_prop_side_profile": {},
        "joint_line_band_side": {},
        "joint_source_lane_side": {},
        "joint_context_alignment_side": {},
        "joint_context_alignment_market_type": {},
    }

    for row in resolved_rows:
        _append_bucket(buckets["by_prop_type"], row["prop_type"], row)
        _append_bucket(buckets["by_side"], row["selection_side"], row)
        _append_bucket(buckets["by_quality_band"], row["quality_band"], row)
        _append_bucket(buckets["by_confidence_band"], row["confidence_band"], row)
        _append_bucket(buckets["by_odds_bucket"], row["odds_bucket"], row)
        _append_bucket(buckets["by_minutes_bucket"], row["minutes_bucket"], row)
        _append_bucket(buckets["by_player_profile_bucket"], row["player_profile_bucket"], row)
        _append_bucket(buckets["by_player_points_line_band"], row["player_points_line_band"], row)
        _append_bucket(buckets["by_injury_influence_bucket"], row["injury_influence_bucket"], row)
        _append_bucket(buckets["by_final_selection_source_lane"], row["final_selection_source_lane"], row)
        _append_bucket(
            buckets["by_blocked_by_elite_points_risk_guard"],
            row["blocked_by_elite_points_risk_guard"],
            row,
        )
        if row["elite_points_risk_guard_reason"]:
            _append_bucket(
                buckets["by_elite_points_risk_guard_reason"],
                row["elite_points_risk_guard_reason"],
                row,
            )
        if row["elite_points_ranking_reason"]:
            _append_bucket(
                buckets["by_elite_points_ranking_reason"],
                row["elite_points_ranking_reason"],
                row,
            )
        if row["player_points_elite_outcome"]:
            _append_bucket(
                buckets["by_player_points_elite_outcome"],
                row["player_points_elite_outcome"],
                row,
            )
        if row["player_points_realism_dampener_reason"]:
            _append_bucket(
                buckets["by_player_points_realism_dampener_reason"],
                row["player_points_realism_dampener_reason"],
                row,
            )
        _append_bucket(buckets["by_market_trust_weight_band"], row["market_trust_weight_band"], row)
        _append_bucket(
            buckets["joint_quality_confidence"],
            _joint_key(row["quality_band"], row["confidence_band"]),
            row,
        )
        _append_bucket(
            buckets["joint_prop_minutes"],
            _joint_key(row["prop_type"], row["minutes_bucket"]),
            row,
        )
        _append_bucket(
            buckets["joint_prop_side"],
            _joint_key(row["prop_type"], row["selection_side"]),
            row,
        )
        _append_bucket(
            buckets["joint_prop_side_profile"],
            _joint_key(row["prop_type"], row["selection_side"], row["player_profile_bucket"]),
            row,
        )
        _append_bucket(
            buckets["joint_line_band_side"],
            _joint_key(row["player_points_line_band"], row["selection_side"]),
            row,
        )
        _append_bucket(
            buckets["joint_source_lane_side"],
            _joint_key(row["final_selection_source_lane"], row["selection_side"]),
            row,
        )
        _append_bucket(buckets["by_context_pick_alignment"], row["context_pick_alignment"], row)
        _append_bucket(buckets["by_context_caution_level"], row["context_caution_level"], row)
        _append_bucket(buckets["by_kelly_eligible"], row["kelly_eligible"], row)
        if row["skip_reason"]:
            _append_bucket(buckets["by_skip_reason"], row["skip_reason"], row)
        _append_bucket(
            buckets["joint_context_alignment_side"],
            _joint_key(row["context_pick_alignment"], row["selection_side"]),
            row,
        )
        _append_bucket(
            buckets["joint_context_alignment_market_type"],
            _joint_key(row["context_pick_alignment"], row["prop_type"]),
            row,
        )

        # NEW: Edge bucket for hit rate analysis
        edge_abs = float(row.get("edge_abs", abs(row.get("edge", 0.0))))
        _append_bucket(buckets["by_edge_bucket"], _edge_bucket(edge_abs), row)
        
        # NEW: Qualification reason for hit rate analysis
        qual_reason = str(row.get("qualification_reason", "")).strip()
        if qual_reason:
            _append_bucket(buckets["by_qualification_reason"], qual_reason, row)

    summary = {"overall": _metrics(resolved_rows)}
    for name, grouped_rows in buckets.items():
        summary[name] = {str(key): _metrics(rows) for key, rows in grouped_rows.items()}
    return summary


def flatten_grading_summary(summary: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    overall = summary.get("overall", {})
    if isinstance(overall, Mapping):
        rows.append(
            {
                "section": "overall",
                "key": "all",
                "n": int(overall.get("n", 0) or 0),
                "wins": int(overall.get("wins", 0) or 0),
                "losses": int(overall.get("losses", 0) or 0),
                "pushes": int(overall.get("pushes", 0) or 0),
                "win_rate": float(overall.get("win_rate", 0.0) or 0.0),
                "roi": overall.get("roi"),
            }
        )

    for section, payload in summary.items():
        if section == "overall" or not isinstance(payload, Mapping):
            continue
        for key, metrics in payload.items():
            if not isinstance(metrics, Mapping):
                continue
            rows.append(
                {
                    "section": str(section),
                    "key": str(key),
                    "n": int(metrics.get("n", 0) or 0),
                    "wins": int(metrics.get("wins", 0) or 0),
                    "losses": int(metrics.get("losses", 0) or 0),
                    "pushes": int(metrics.get("pushes", 0) or 0),
                    "win_rate": float(metrics.get("win_rate", 0.0) or 0.0),
                    "roi": metrics.get("roi"),
                }
            )

    return pd.DataFrame(rows)


def summarize_elite_filter_replay(
    graded_rows: Sequence[Any],
    replay_elite_rows: Sequence[Any] | None,
) -> dict[str, Any]:
    if replay_elite_rows is None:
        return {
            "counts": {
                "graded_candidates": 0,
                "kept_elite": 0,
                "filtered_out": 0,
                "replay_available": 0,
            },
            "kept_elite": summarize_graded_props([]),
            "filtered_out": summarize_graded_props([]),
        }

    elite_keys = {
        _replay_identity_key(_to_dict(item))
        for item in replay_elite_rows
        if _replay_identity_key(_to_dict(item))
    }

    kept_rows: list[dict[str, Any]] = []
    filtered_rows: list[dict[str, Any]] = []
    for item in graded_rows:
        source = _to_dict(item)
        identity_key = _replay_identity_key(source)
        if not identity_key:
            continue
        normalized = _normalize_graded_row(source)
        normalized["replay_identity_key"] = identity_key
        if identity_key in elite_keys:
            kept_rows.append(normalized)
        else:
            filtered_rows.append(normalized)

    return {
        "counts": {
            "graded_candidates": int(len(kept_rows) + len(filtered_rows)),
            "kept_elite": int(len(kept_rows)),
            "filtered_out": int(len(filtered_rows)),
            "replay_available": 1,
        },
        "kept_elite": summarize_graded_props(kept_rows),
        "filtered_out": summarize_graded_props(filtered_rows),
    }


def summarize_player_points_calibration(
    graded_rows: Sequence[Any],
    replay_elite_rows: Sequence[Any] | None = None,
) -> dict[str, Any]:
    point_rows = [
        _normalize_graded_row(item)
        for item in graded_rows
        if _normalize_market_type(_to_dict(item).get("prop_type") or _to_dict(item).get("market_type") or "") == "player_points"
    ]
    replay_payload = summarize_elite_filter_replay(graded_rows, replay_elite_rows)
    elite_keys = {
        _replay_identity_key(_to_dict(item))
        for item in (replay_elite_rows or [])
        if _replay_identity_key(_to_dict(item))
    }
    kept_points = [
        row for row in _resolved_rows_from_summary_source(point_rows)
        if row["replay_identity_key"] in elite_keys
    ] if replay_elite_rows is not None else []
    filtered_points = [
        row for row in _resolved_rows_from_summary_source(point_rows)
        if row["replay_identity_key"] not in elite_keys
    ] if replay_elite_rows is not None else []

    return {
        "overall_points": summarize_graded_props(point_rows),
        "kept_points": summarize_graded_props(kept_points),
        "filtered_points": summarize_graded_props(filtered_points),
        "replay_counts": replay_payload.get("counts", {}),
    }


def summarize_player_points_uplift_audit(
    graded_rows: Sequence[Any],
    replay_elite_rows: Sequence[Any] | None = None,
) -> dict[str, Any]:
    point_rows = _resolved_rows_from_summary_source(
        [
            _normalize_graded_row(item)
            for item in graded_rows
            if _normalize_market_type(_to_dict(item).get("prop_type") or _to_dict(item).get("market_type") or "") == "player_points"
        ]
    )
    elite_keys = {
        _replay_identity_key(_to_dict(item))
        for item in (replay_elite_rows or [])
        if _replay_identity_key(_to_dict(item))
    }
    kept_points = [row for row in point_rows if row.get("replay_identity_key") in elite_keys] if replay_elite_rows is not None else []
    filtered_points = [row for row in point_rows if row.get("replay_identity_key") not in elite_keys] if replay_elite_rows is not None else []

    return {
        "overall": _points_uplift_bucket_summary(point_rows),
        "kept_points": _points_uplift_bucket_summary(kept_points),
        "filtered_points": _points_uplift_bucket_summary(filtered_points),
    }


def _normalize_graded_row(item: Any) -> dict[str, Any]:
    row = _to_dict(item)
    prop_type = _normalize_market_type(row.get("prop_type") or row.get("market_type") or "unknown")
    confidence_value = row.get("confidence")
    odds_value = row.get("odds", row.get("offered_odds"))
    market_trust_value = row.get("market_trust_weight")
    points_guard_reason = _clean_text(row.get("elite_points_risk_guard_reason")) or _clean_text(elite_points_risk_guard_reason(row))
    kelly_eligible_value = row.get("kelly_eligible")
    if not _clean_text(kelly_eligible_value):
        kelly_eligible_value = row.get("eligible")
    normalized = {
        "prop_type": prop_type or "unknown",
        "selection_side": _clean_text(row.get("selection_side") or row.get("side") or row.get("selection") or "unknown").lower(),
        "quality_band": _clean_text(row.get("quality_band") or quality_band(row.get("quality_score"))),
        "confidence_band": _clean_text(row.get("confidence_band") or confidence_band(confidence_value)),
        "odds_bucket": _clean_text(row.get("odds_bucket") or odds_bucket(odds_value)),
        "minutes_bucket": _clean_text(row.get("minutes_bucket") or minutes_bucket(row)),
        "player_profile_bucket": _clean_text(row.get("player_profile_bucket") or player_profile_bucket(row)),
        "player_points_line_band": _clean_text(row.get("player_points_line_band") or player_points_line_band(row)),
        "injury_influence_bucket": _clean_text(row.get("injury_influence_bucket") or injury_influence_bucket(row)),
        "final_selection_source_lane": _clean_text(
            row.get("final_selection_source_lane")
            or ("live_quality_rescue_pass" if "live_quality_rescue_pass" in str(row.get("notes", "")) else "core_pass")
        ),
        "blocked_by_elite_points_risk_guard": str(
            row.get("blocked_by_elite_points_risk_guard")
            if row.get("blocked_by_elite_points_risk_guard") is not None
            else bool(points_guard_reason)
        ),
        "elite_points_risk_guard_reason": points_guard_reason,
        "elite_points_ranking_reason": _clean_text(row.get("elite_points_ranking_reason")),
        "player_points_elite_outcome": _clean_text(row.get("player_points_elite_outcome")),
        "player_points_realism_dampener_reason": _clean_text(row.get("player_points_realism_dampener_reason")),
        "injury_projection_delta": round(float(injury_projection_delta_value(row)), 4),
        "injury_confidence_delta": round(float(injury_confidence_delta_value(row)), 4),
        "market_trust_weight_band": _clean_text(
            row.get("market_trust_weight_band") or market_trust_weight_band(market_trust_value)
        ),
        "context_pick_alignment": _context_alignment_value(row.get("context_pick_alignment")),
        "context_caution_level": _context_caution_value(row.get("context_caution_level")),
        "kelly_eligible": _kelly_eligible_value(kelly_eligible_value),
        "skip_reason": _clean_text(row.get("skip_reason")).lower(),
        "odds": odds_value,
        "result": _result_value(row),
    }
    normalized["replay_identity_key"] = _replay_identity_key(row)
    return normalized


def _result_value(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("result") or row.get("graded_result") or "").strip().lower()
    if explicit in {"win", "loss", "push"}:
        return explicit

    is_win = to_float(row.get("is_win"))
    is_push = to_float(row.get("is_push"))
    is_loss = to_float(row.get("is_loss"))
    if is_win == 1.0:
        return "win"
    if is_push == 1.0:
        return "push"
    if is_loss == 1.0:
        return "loss"

    hit = to_float(row.get("hit"))
    if hit == 1.0:
        return "win"
    if hit == 0.0:
        return "loss"
    return "unresolved"


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {"n": 0, "wins": 0, "losses": 0, "pushes": 0, "win_rate": 0.0, "roi": None}

    wins = sum(1 for row in rows if row.get("result") == "win")
    pushes = sum(1 for row in rows if row.get("result") == "push")
    losses = sum(1 for row in rows if row.get("result") == "loss")
    return {
        "n": int(total),
        "wins": int(wins),
        "losses": int(losses),
        "pushes": int(pushes),
        "win_rate": round(float(wins / total), 4),
        "roi": _roi(rows),
    }


def _roi(rows: Sequence[Mapping[str, Any]]) -> float | None:
    profit = 0.0
    graded_bets = 0
    for row in rows:
        result = row.get("result")
        if result not in {"win", "loss"}:
            continue
        win_profit = _american_profit_for_unit_stake(row.get("odds"))
        if win_profit is None:
            continue
        graded_bets += 1
        profit += win_profit if result == "win" else -1.0
    if graded_bets == 0:
        return None
    return round(float(profit / graded_bets), 6)


def _american_profit_for_unit_stake(value: Any) -> float | None:
    american = to_float(value)
    if american is None or american == 0:
        return None
    if american > 0:
        return float(american / 100.0)
    return float(100.0 / abs(american))


def _metrics_with_deltas(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = _metrics(rows)
    if not rows:
        metrics["avg_projection_delta"] = 0.0
        metrics["avg_confidence_delta"] = 0.0
        return metrics
    metrics["avg_projection_delta"] = round(
        float(sum(float(row.get("injury_projection_delta", 0.0) or 0.0) for row in rows) / len(rows)),
        4,
    )
    metrics["avg_confidence_delta"] = round(
        float(sum(float(row.get("injury_confidence_delta", 0.0) or 0.0) for row in rows) / len(rows)),
        4,
    )
    return metrics


def _points_uplift_bucket_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {
        "by_side": {},
        "by_player_points_line_band": {},
        "by_injury_influence_bucket": {},
        "joint_side_line_band": {},
        "joint_side_line_band_injury": {},
    }

    for row in rows:
        _append_bucket(buckets["by_side"], str(row.get("selection_side", "")), dict(row))
        _append_bucket(buckets["by_player_points_line_band"], str(row.get("player_points_line_band", "")), dict(row))
        _append_bucket(buckets["by_injury_influence_bucket"], str(row.get("injury_influence_bucket", "")), dict(row))
        _append_bucket(
            buckets["joint_side_line_band"],
            _joint_key(row.get("selection_side", ""), row.get("player_points_line_band", "")),
            dict(row),
        )
        _append_bucket(
            buckets["joint_side_line_band_injury"],
            _joint_key(
                row.get("selection_side", ""),
                row.get("player_points_line_band", ""),
                row.get("injury_influence_bucket", ""),
            ),
            dict(row),
        )

    summary = {"overall": _metrics_with_deltas(rows)}
    for name, grouped_rows in buckets.items():
        summary[name] = {str(key): _metrics_with_deltas(group_rows) for key, group_rows in grouped_rows.items()}
    return summary


def _append_bucket(group: dict[str, list[dict[str, Any]]], key: str, row: dict[str, Any]) -> None:
    group.setdefault(str(key), []).append(row)


def _joint_key(*parts: str) -> str:
    return "|".join(str(part) for part in parts)


def _normalize_market_type(value: Any) -> str:
    market_type = str(value or "unknown").strip().lower()
    aliases = {
        "points": "player_points",
        "rebounds": "player_rebounds",
        "assists": "player_assists",
        "threes": "player_3pt_made",
        "steals": "player_steals",
        "blocks": "player_blocks",
    }
    return aliases.get(market_type, market_type or "unknown")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


def _context_alignment_value(value: Any) -> str:
    text = _clean_text(value).lower()
    return text if text in CONTEXT_ALIGNMENT_VALUES else "insufficient_data"


def _context_caution_value(value: Any) -> str:
    text = _clean_text(value).lower()
    return text if text in CONTEXT_CAUTION_VALUES else "insufficient_data"


def _kelly_eligible_value(value: Any) -> str:
    text = _clean_text(value).lower()
    if text in {"true", "1", "yes", "y"}:
        return "true"
    if text in {"false", "0", "no", "n"}:
        return "false"
    return "unknown"


def _replay_identity_key(row: Mapping[str, Any]) -> str:
    player_name = str(row.get("player_name") or row.get("entity_name") or "").strip().lower()
    team = str(row.get("team_abbreviation") or row.get("team") or "").strip().upper()
    prop_type = _normalize_market_type(row.get("prop_type") or row.get("market_type") or "unknown")
    selection_side = str(row.get("side") or row.get("selection") or row.get("selection_side") or "").strip().lower()
    line_value = to_float(row.get("line_value"))
    if line_value is None:
        line_value = to_float(row.get("sportsbook_line"))
    if not player_name or not team or not prop_type or not selection_side or line_value is None:
        return ""
    return _joint_key(player_name, team, prop_type, selection_side, f"{float(line_value):.4f}")


def _to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {str(key): value for key, value in item.items()}
    if is_dataclass(item):
        return {str(key): value for key, value in asdict(item).items()}
    if hasattr(item, "as_dict") and callable(item.as_dict):
        data = item.as_dict()
        if isinstance(data, Mapping):
            return {str(key): value for key, value in data.items()}
    if hasattr(item, "__dict__"):
        return {str(key): value for key, value in vars(item).items()}
    return {}


def _resolved_rows_from_summary_source(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("result") in {"win", "loss", "push"}]


__all__ = [
    "flatten_grading_summary",
    "summarize_elite_filter_replay",
    "summarize_graded_props",
    "summarize_player_points_calibration",
    "summarize_player_points_uplift_audit",
]
