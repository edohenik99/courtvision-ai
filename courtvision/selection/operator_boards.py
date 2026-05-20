"""Board construction and lane assignment for operator mode.

This module extracts board construction logic from courtvision_ai.py into
package-owned, testable components. Board names correspond to operator-facing
lanes: elite (strong conviction), full_market (live market), stat_only (projection).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

import pandas as pd

from courtvision.context.game_context import (
    IDENTITY_QUARANTINE_REJECTION_REASON,
    identity_quarantine_reason_counts,
    mark_identity_quarantine_fields,
)
from courtvision.reason_codes import (
    DUPLICATE_BETTING_IDENTITY_REASON,
    SELECTION_NOT_SELECTED_BY_BOARD_SELECTOR_REASON,
    UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
    UNSUPPORTED_MILESTONE_MARKET_REASON,
)
from courtvision.runtime_gates import (
    is_identity_quarantined,
    is_unsupported_milestone_market,
    operator_live_source_gate_status,
)

ACTIVE_OPERATOR_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _coerce_market_count_mapping(value: Any) -> dict[str, int]:
    if isinstance(value, Mapping):
        counts: dict[str, int] = {}
        for key, count in value.items():
            market = str(key).strip().lower()
            if not market:
                continue
            safe_count = _safe_int(count)
            if safe_count > 0:
                counts[market] = safe_count
        return dict(sorted(counts.items()))
    if isinstance(value, list):
        counts = {}
        for item in value:
            if not isinstance(item, Mapping):
                continue
            market = str(
                item.get("market_type")
                or item.get("market")
                or item.get("key")
                or ""
            ).strip().lower()
            if not market:
                continue
            safe_count = _safe_int(item.get("count"))
            if safe_count > 0:
                counts[market] = counts.get(market, 0) + safe_count
        return dict(sorted(counts.items()))
    return {}


def _safe_text(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _truthy(value: Any) -> bool:
    text = _safe_text(value).lower()
    return text in {"1", "true", "yes", "y", "on"}


def _normalize_identity_value(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if not pd.isna(number) and number.is_integer():
        return str(int(number))
    return text


def _normalize_name(value: Any) -> str:
    return " ".join(_safe_text(value).lower().split())


def _normalize_line_value(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text.lower()
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _player_identity_key(row: pd.Series) -> str:
    player_id = _normalize_identity_value(row.get("player_id"))
    if player_id and player_id not in {"0", "0.0"}:
        return f"id:{player_id}"
    name = _normalize_name(row.get("player_name") or row.get("entity_name") or row.get("player"))
    return f"name:{name}" if name else ""


def _betting_identity_key(row: pd.Series) -> tuple[str, str, str, str, str] | None:
    player_key = _player_identity_key(row)
    market = _safe_text(row.get("market_type") or row.get("prop_type")).lower()
    selection = _safe_text(row.get("selection") or row.get("selection_side") or row.get("side")).lower()
    line = _normalize_line_value(row.get("line") if _safe_text(row.get("line")) else row.get("sportsbook_line"))
    game_id = _normalize_identity_value(row.get("game_id"))
    if not player_key or not market or not selection or not line:
        return None
    return (player_key, game_id, market, selection, line)


def _is_team_not_in_game_context(row: pd.Series) -> bool:
    suppression_reason = _safe_text(row.get("game_context_suppression_reason")).lower()
    return (
        _truthy(row.get("candidate_team_not_in_game"))
        or suppression_reason == "team_not_in_game_context"
        or _safe_text(row.get("context_conflict_cause")).lower() == "stale_team_not_in_game"
    )


def _context_validity_score(row: pd.Series) -> int:
    context_columns = {
        "candidate_team_not_in_game",
        "game_context_suppressed",
        "game_context_suppression_reason",
        "context_conflict_cause",
        "opponent",
        "home_away",
        "game_status",
        "game_date",
        "game_datetime",
    }
    if not any(column in row.index for column in context_columns):
        return 0
    if _is_team_not_in_game_context(row) or _truthy(row.get("game_context_suppressed")):
        return 0
    if _safe_text(row.get("opponent")) or _safe_text(row.get("home_away")):
        return 2
    if _safe_text(row.get("game_status")) or _safe_text(row.get("game_date")) or _safe_text(row.get("game_datetime")):
        return 1
    return 0


def _context_warning_count(row: pd.Series) -> int:
    count = 0
    if _truthy(row.get("candidate_team_not_in_game")):
        count += 1
    if _truthy(row.get("game_context_suppressed")):
        count += 1
    if _safe_text(row.get("game_context_suppression_reason")):
        count += 1
    if _safe_text(row.get("context_conflict_cause")):
        count += 1
    if _safe_text(row.get("context_caution_level")).lower() in {"high", "insufficient_data"}:
        count += 1
    if _safe_text(row.get("context_pick_alignment")).lower() in {"conflicted", "insufficient_data"}:
        count += 1
    return count


def _live_source_score(row: pd.Series) -> int:
    score = 0
    if _truthy(row.get("is_live_market")) and not _truthy(row.get("synthetic_line")):
        score += 2
    if "live_market" in _safe_text(row.get("line_source")).lower():
        score += 1
    if "live" in _safe_text(row.get("source_lane")).lower():
        score += 1
    qualification_reason = _safe_text(row.get("qualification_reason")).lower()
    if qualification_reason and "stat_only" not in qualification_reason:
        score += 1
    return score


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe_rank(row: pd.Series, position: int) -> tuple[int, int, int, int, float, float, int]:
    return (
        _context_validity_score(row),
        0 if _is_team_not_in_game_context(row) else 1,
        -_context_warning_count(row),
        _live_source_score(row),
        _safe_float(row.get("selection_score")),
        _safe_float(row.get("quality_score")),
        -position,
    )


def _row_summary(row: pd.Series, index: Any) -> dict[str, Any]:
    return {
        "row_index": str(index),
        "player_name": _safe_text(row.get("player_name") or row.get("entity_name")),
        "player_id": _safe_text(row.get("player_id")),
        "team": _safe_text(row.get("team") or row.get("team_abbr")),
        "market_type": _safe_text(row.get("market_type")),
        "selection": _safe_text(row.get("selection") or row.get("selection_side") or row.get("side")),
        "line": _safe_text(row.get("line") if _safe_text(row.get("line")) else row.get("sportsbook_line")),
        "game_id": _safe_text(row.get("game_id")),
        "selection_score": _safe_text(row.get("selection_score")),
        "quality_score": _safe_text(row.get("quality_score")),
        "context_validity_score": _context_validity_score(row),
        "context_warning_count": _context_warning_count(row),
        "team_not_in_game_context": _is_team_not_in_game_context(row),
    }


def _dedupe_betting_identities(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    empty_summary = {
        "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
        "total_rows_dropped": 0,
        "counts_by_market_type": {},
        "groups": [],
        "dropped_indices": [],
    }
    if df.empty:
        return df.copy(), empty_summary

    groups: dict[tuple[str, str, str, str, str], list[tuple[int, Any, pd.Series]]] = {}
    for position, (idx, row) in enumerate(df.iterrows()):
        key = _betting_identity_key(row)
        if key is None:
            continue
        groups.setdefault(key, []).append((position, idx, row))

    keep_indices: set[Any] = set(df.index)
    dropped_indices: list[Any] = []
    drop_groups: list[dict[str, Any]] = []
    drop_counts_by_market_type: dict[str, int] = {}

    for key, members in groups.items():
        if len(members) <= 1:
            continue
        ranked = sorted(
            members,
            key=lambda item: _dedupe_rank(item[2], item[0]),
            reverse=True,
        )
        kept_position, kept_index, kept_row = ranked[0]
        dropped_members = ranked[1:]
        for _, dropped_index, dropped_row in dropped_members:
            keep_indices.discard(dropped_index)
            dropped_indices.append(dropped_index)
            market = _safe_text(dropped_row.get("market_type")).lower()
            if market:
                drop_counts_by_market_type[market] = drop_counts_by_market_type.get(market, 0) + 1

        player_key, game_id, market_type, selection, line = key
        drop_groups.append(
            {
                "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
                "identity": "|".join(key),
                "player_key": player_key,
                "game_id": game_id,
                "market_type": market_type,
                "selection": selection,
                "line": line,
                "row_count": len(members),
                "drop_count": len(dropped_members),
                "kept": _row_summary(kept_row, kept_index),
                "dropped": [_row_summary(row, idx) for _, idx, row in dropped_members],
                "tie_breaker": (
                    "context_validity,not_team_not_in_game_context,"
                    "fewest_context_warnings,live_source,selection_score,quality_score,input_order"
                ),
            }
        )

    if not dropped_indices:
        return df.copy(), empty_summary

    deduped = df.loc[[idx for idx in df.index if idx in keep_indices]].copy()
    summary = {
        "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
        "total_rows_dropped": int(len(dropped_indices)),
        "counts_by_market_type": dict(sorted(drop_counts_by_market_type.items())),
        "groups": drop_groups,
        "dropped_indices": dropped_indices,
    }
    return deduped, summary


def duplicate_betting_identity_drop_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize duplicate betting-identity drop diagnostics from a trace payload."""
    empty = {
        "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
        "total_rows_dropped": 0,
        "counts_by_market_type": {},
        "groups": [],
    }
    if not isinstance(payload, Mapping):
        return empty

    existing = payload.get("duplicate_betting_identity")
    if isinstance(existing, Mapping):
        counts = _coerce_market_count_mapping(
            existing.get("counts_by_market_type")
            or existing.get("duplicate_betting_identity_drop_counts_by_market_type")
        )
        total = _safe_int(
            existing.get("total_rows_dropped")
            or existing.get("duplicate_betting_identity_drop_count")
            or existing.get("count")
            or existing.get("total")
        )
        groups = existing.get("groups") or existing.get("duplicate_betting_identity_drop_groups") or []
        if not isinstance(groups, list):
            groups = []
        if total <= 0:
            total = sum(counts.values())
        return {
            "rejection_reason": str(existing.get("rejection_reason") or DUPLICATE_BETTING_IDENTITY_REASON),
            "total_rows_dropped": int(total),
            "counts_by_market_type": counts,
            "groups": groups,
        }

    for nested_key in ("selection_trace", "final_board_construction", "candidate_funnel"):
        nested = payload.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        nested_summary = duplicate_betting_identity_drop_summary(nested)
        if nested_summary["total_rows_dropped"] > 0 or nested_summary["counts_by_market_type"]:
            return nested_summary

    for scope in ("full_market", "elite"):
        scope_payload = payload.get(scope)
        if not isinstance(scope_payload, Mapping):
            continue
        counts = _coerce_market_count_mapping(
            scope_payload.get("duplicate_betting_identity_drop_counts_by_market_type")
        )
        total = _safe_int(scope_payload.get("duplicate_betting_identity_drop_count"))
        groups = scope_payload.get("duplicate_betting_identity_drop_groups") or []
        if not isinstance(groups, list):
            groups = []
        if total <= 0:
            total = sum(counts.values())
        if total > 0 or counts:
            return {
                "rejection_reason": str(
                    scope_payload.get("duplicate_betting_identity_rejection_reason")
                    or DUPLICATE_BETTING_IDENTITY_REASON
                ),
                "total_rows_dropped": int(total),
                "counts_by_market_type": counts,
                "groups": groups,
            }

    counts = _coerce_market_count_mapping(
        payload.get("duplicate_betting_identity_drop_counts_by_market_type")
        or payload.get("counts_by_market_type")
    )
    total = _safe_int(
        payload.get("duplicate_betting_identity_drop_count")
        or payload.get("total_rows_dropped")
        or payload.get("count")
        or payload.get("total")
    )
    groups = payload.get("duplicate_betting_identity_drop_groups") or payload.get("groups") or []
    if not isinstance(groups, list):
        groups = []
    if total <= 0:
        total = sum(counts.values())
    return {
        "rejection_reason": DUPLICATE_BETTING_IDENTITY_REASON,
        "total_rows_dropped": int(total),
        "counts_by_market_type": counts,
        "groups": groups,
    }


def unsupported_active_operator_market_drop_summary(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize unsupported active-market drop diagnostics from a trace payload."""
    empty = {
        "rejection_reason": UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
        "total_rows_dropped": 0,
        "counts_by_market_type": {},
    }
    if not isinstance(payload, Mapping):
        return empty

    existing = payload.get("unsupported_active_operator_markets")
    if isinstance(existing, Mapping):
        counts = _coerce_market_count_mapping(
            existing.get("counts_by_market_type")
            or existing.get("unsupported_active_operator_market_counts")
        )
        total = _safe_int(
            existing.get("total_rows_dropped")
            or existing.get("unsupported_active_operator_market_count")
            or existing.get("count")
            or existing.get("total")
        )
        if total <= 0:
            total = sum(counts.values())
        return {
            "rejection_reason": str(
                existing.get("rejection_reason")
                or UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON
            ),
            "total_rows_dropped": int(total),
            "counts_by_market_type": counts,
        }

    for nested_key in ("selection_trace", "final_board_construction", "candidate_funnel"):
        nested = payload.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        nested_summary = unsupported_active_operator_market_drop_summary(nested)
        if nested_summary["total_rows_dropped"] > 0 or nested_summary["counts_by_market_type"]:
            return nested_summary

    for scope in ("full_market", "elite"):
        scope_payload = payload.get(scope)
        if not isinstance(scope_payload, Mapping):
            continue
        counts = _coerce_market_count_mapping(
            scope_payload.get("unsupported_active_operator_market_counts")
            or scope_payload.get("unsupported_active_operator_market_drop_counts_by_market_type")
        )
        total = _safe_int(
            scope_payload.get("unsupported_active_operator_market_count")
            or scope_payload.get("unsupported_active_operator_market_drop_count")
        )
        if total <= 0:
            total = sum(counts.values())
        if total > 0 or counts:
            return {
                "rejection_reason": UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
                "total_rows_dropped": int(total),
                "counts_by_market_type": counts,
            }

    counts = _coerce_market_count_mapping(
        payload.get("unsupported_active_operator_market_counts")
        or payload.get("unsupported_active_operator_market_drop_counts_by_market_type")
        or payload.get("counts_by_market_type")
    )
    total = _safe_int(
        payload.get("unsupported_active_operator_market_count")
        or payload.get("unsupported_active_operator_market_drop_count")
        or payload.get("total_rows_dropped")
        or payload.get("count")
        or payload.get("total")
    )
    if total <= 0:
        total = sum(counts.values())

    if total <= 0:
        rejection_rows = payload.get("selection_rejection_reasons")
        if isinstance(rejection_rows, list):
            for row in rejection_rows:
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("reason", "")).strip() == UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON:
                    total = _safe_int(row.get("count"))
                    break

    return {
        "rejection_reason": UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON,
        "total_rows_dropped": int(total),
        "counts_by_market_type": counts,
    }


def format_unsupported_active_operator_market_drop_line(payload: Mapping[str, Any] | None) -> str:
    summary = unsupported_active_operator_market_drop_summary(payload)
    total = int(summary.get("total_rows_dropped", 0) or 0)
    if total <= 0:
        return ""
    counts = summary.get("counts_by_market_type", {})
    if isinstance(counts, Mapping) and counts:
        count_text = ", ".join(f"{market}={count}" for market, count in sorted(counts.items()))
        return f"unsupported active markets dropped: {total} ({count_text})"
    return f"unsupported active markets dropped: {total}"


def _display_name(row: pd.Series) -> str:
    return str(
        row.get("entity_name")
        or row.get("player_name")
        or row.get("player")
        or ""
    ).strip()


def _selection_identity_frame(df: pd.DataFrame) -> set[tuple[Any, ...]]:
    if df.empty:
        return set()
    identities: set[tuple[Any, ...]] = set()
    for idx, row in df.iterrows():
        identities.add(
            (
                idx,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
        )
    return identities


def _non_milestone_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    return ~df.apply(is_unsupported_milestone_market, axis=1)


def _active_operator_market_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    if "market_type" not in df.columns:
        return pd.Series(False, index=df.index)
    markets = df["market_type"].fillna("").astype(str).str.strip().str.lower()
    return markets.isin(ACTIVE_OPERATOR_MARKETS)


def _market_counts(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "market_type" not in df.columns:
        return {}
    markets = df["market_type"].fillna("").astype(str).str.strip().str.lower()
    return {str(key): int(value) for key, value in markets.value_counts().sort_index().items()}


def compute_board_diversity_metrics(board_df: pd.DataFrame) -> dict[str, Any]:
    """Compute diversity metrics for a board.

    Tracks market type distribution, player clustering, and game exposure
    to identify potential bias issues.

    Returns dict with:
    - market_type_counts: Dict of market type frequencies
    - player_counts: Dict of player appearance frequencies
    - game_counts: Dict of game appearance frequencies
    - points_bias_ratio: Ratio of points markets to total
    - max_player_clustering: Max picks for any single player
    - max_game_exposure: Max picks for any single game
    """
    if board_df.empty:
        return {
            "market_type_counts": {},
            "player_counts": {},
            "game_counts": {},
            "points_bias_ratio": 0.0,
            "max_player_clustering": 0,
            "max_game_exposure": 0,
        }

    market_type_counts = board_df["market_type"].value_counts().to_dict() if "market_type" in board_df.columns else {}
    player_counts = board_df["player_name"].value_counts().to_dict() if "player_name" in board_df.columns else {}
    game_counts = board_df["game_id"].value_counts().to_dict() if "game_id" in board_df.columns else {}

    total_picks = len(board_df)
    points_picks = market_type_counts.get("player_points", 0)
    points_bias_ratio = points_picks / total_picks if total_picks > 0 else 0.0

    max_player_clustering = max(player_counts.values()) if player_counts else 0
    max_game_exposure = max(game_counts.values()) if game_counts else 0

    return {
        "market_type_counts": market_type_counts,
        "player_counts": player_counts,
        "game_counts": game_counts,
        "points_bias_ratio": round(points_bias_ratio, 3),
        "max_player_clustering": max_player_clustering,
        "max_game_exposure": max_game_exposure,
    }


def apply_diversity_penalty(
    row: pd.Series,
    board_metrics: dict[str, Any],
    max_points_ratio: float = 0.5,
    max_per_player: int = 2,
    max_per_game: int = 3,
) -> tuple[float, list[str]]:
    """Apply diversity-based confidence penalty to a candidate.

    Penalizes picks that would exacerbate existing board biases:
    - Points market bias (too many points picks)
    - Same-player clustering (too many picks for one player)
    - Same-game overexposure (too many picks from one game)

    Returns:
    - penalty: Confidence reduction (0.0 to 1.0)
    - reasons: List of penalty reasons for diagnostics
    """
    penalty = 0.0
    reasons: list[str] = []

    market_type = row.get("market_type", "")
    player_name = row.get("player_name", "")
    game_id = row.get("game_id", "")

    # Points market bias penalty
    if market_type == "player_points":
        current_ratio = board_metrics.get("points_bias_ratio", 0.0)
        if current_ratio > max_points_ratio:
            penalty += 0.03
            reasons.append(f"points_bias_ratio_{current_ratio:.2f}")

    # Same-player clustering penalty
    player_counts = board_metrics.get("player_counts", {})
    if player_name in player_counts:
        current_count = player_counts[player_name]
        if current_count >= max_per_player:
            penalty += 0.04
            reasons.append(f"player_clustering_{current_count}")

    # Same-game overexposure penalty
    game_counts = board_metrics.get("game_counts", {})
    if game_id in game_counts:
        current_count = game_counts[game_id]
        if current_count >= max_per_game:
            penalty += 0.02
            reasons.append(f"game_exposure_{current_count}")

    return min(penalty, 0.15), reasons  # Cap total penalty at 15%


def build_operator_boards(
    prepared_df: pd.DataFrame,
    *,
    per_market_limit: int = 20,
    select_elite_board: Callable[[pd.DataFrame], pd.DataFrame],
    select_top_per_market: Callable[[pd.DataFrame, int], pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build elite and full_market boards from prepared candidates.

    Elite board: strong conviction, high confidence, live market qualified.
    Full market board: top N per market type, live market qualified.

    Args:
        prepared_df: DataFrame with enriched candidate rows (live and stat-only)
        per_market_limit: Max candidates per market type in full_market board
        select_elite_board: Callable to filter elite candidates with trace
        select_top_per_market: Callable to select top N per market with trace

    Returns:
        (elite_df, full_market_df, final_board_construction_traces)
    """
    if prepared_df.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {
                "elite": {"input_count": 0, "selected_count": 0},
                "full_market": {"input_count": 0, "selected_count": 0},
                "required_selector_columns": [],
                "selection_rejection_reasons": [],
                "qualified_but_not_selected_rows": [],
                "identity_quarantine": {
                    "rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
                    "total_rows_dropped": 0,
                    "counts_by_reason": {},
                },
            },
        )

    prepared_df = mark_identity_quarantine_fields(prepared_df)
    if "selection_rejection_reason" not in prepared_df.columns:
        prepared_df["selection_rejection_reason"] = ""
    identity_quarantine_counts = identity_quarantine_reason_counts(prepared_df)
    identity_quarantine_count = int(sum(identity_quarantine_counts.values()))
    identity_quarantine_mask = prepared_df.apply(
        lambda row: is_identity_quarantined(row) is not None,
        axis=1,
    )

    required_selector_columns = [
        "selection_score",
        "confidence",
        "edge",
        "quality_score",
        "market_type",
        "qualification_reason",
        "is_live_market",
        "synthetic_line",
        "source_lane",
    ]
    required_selector_diagnostics = [
        {
            "column": column,
            "exists": column in prepared_df.columns,
            "non_null_count": int(prepared_df[column].notna().sum()) if column in prepared_df.columns else 0,
        }
        for column in required_selector_columns
    ]

    # Unified live gate logic: single mask for both eligibility marking AND filtering
    qualification_reason_series = prepared_df.get("qualification_reason", pd.Series("", index=prepared_df.index))
    live_gate_statuses = [
        operator_live_source_gate_status(row)
        for _, row in prepared_df.iterrows()
    ]
    diagnostic_live_mask = pd.Series(
        [bool(status["diagnostic_live"]) for status in live_gate_statuses],
        index=prepared_df.index,
    )
    unified_live_mask = pd.Series(
        [bool(status["eligible"]) for status in live_gate_statuses],
        index=prepared_df.index,
    )
    live_gate_rejection_reason = pd.Series(
        [str(status["rejection_reason"]) for status in live_gate_statuses],
        index=prepared_df.index,
    )

    # Rejection reasons - unified logic ensures no row marked valid is later dropped
    prepared_df.loc[
        prepared_df["selection_rejection_reason"].eq("")
        & live_gate_rejection_reason.ne(""),
        "selection_rejection_reason",
    ] = live_gate_rejection_reason

    # Filter using the SAME unified mask used for eligibility marking
    milestone_mask = ~_non_milestone_mask(prepared_df)
    if milestone_mask.any():
        prepared_df.loc[
            prepared_df["selection_rejection_reason"].eq("") & milestone_mask,
            "selection_rejection_reason",
        ] = UNSUPPORTED_MILESTONE_MARKET_REASON

    active_market_mask = _active_operator_market_mask(prepared_df)
    unsupported_active_market_mask = unified_live_mask & ~milestone_mask & ~active_market_mask
    if unsupported_active_market_mask.any():
        prepared_df.loc[
            prepared_df["selection_rejection_reason"].eq("") & unsupported_active_market_mask,
            "selection_rejection_reason",
        ] = UNSUPPORTED_ACTIVE_OPERATOR_MARKET_REASON

    live_candidates_before_dedupe_df = prepared_df[
        unified_live_mask & ~milestone_mask & active_market_mask & ~identity_quarantine_mask
    ].copy()
    live_candidates_df, duplicate_betting_identity_summary = _dedupe_betting_identities(
        live_candidates_before_dedupe_df
    )
    duplicate_betting_identity_drop_count = int(
        duplicate_betting_identity_summary.get("total_rows_dropped", 0) or 0
    )
    duplicate_betting_identity_drop_groups = (
        duplicate_betting_identity_summary.get("groups", [])
        if isinstance(duplicate_betting_identity_summary.get("groups", []), list)
        else []
    )
    duplicate_betting_identity_drop_counts_by_market_type = dict(
        duplicate_betting_identity_summary.get("counts_by_market_type", {}) or {}
    )
    if duplicate_betting_identity_drop_count > 0:
        dropped_indices = duplicate_betting_identity_summary.get("dropped_indices", [])
        if not isinstance(dropped_indices, list):
            dropped_indices = []
        for dropped_index in dropped_indices:
            if dropped_index in prepared_df.index and not str(prepared_df.at[dropped_index, "selection_rejection_reason"]).strip():
                prepared_df.at[dropped_index, "selection_rejection_reason"] = DUPLICATE_BETTING_IDENTITY_REASON
    unsupported_milestone_count = int(milestone_mask.sum())
    unsupported_active_market_count = int(unsupported_active_market_mask.sum())
    unsupported_active_market_counts = _market_counts(prepared_df[unsupported_active_market_mask].copy())

    final_board_construction: dict[str, Any] = {"elite": {}, "full_market": {}}
    final_board_construction["required_selector_columns"] = required_selector_diagnostics

    final_board_construction["elite"]["input_count"] = len(prepared_df)
    final_board_construction["elite"]["post_live_market_gate_count"] = len(live_candidates_before_dedupe_df)
    final_board_construction["elite"]["post_duplicate_betting_identity_dedupe_count"] = len(live_candidates_df)
    final_board_construction["elite"]["unsupported_milestone_count"] = unsupported_milestone_count
    final_board_construction["elite"]["unsupported_active_operator_market_count"] = unsupported_active_market_count
    final_board_construction["elite"]["unsupported_active_operator_market_counts"] = unsupported_active_market_counts
    final_board_construction["elite"]["identity_quarantine_count"] = identity_quarantine_count
    final_board_construction["elite"]["identity_quarantine_reason_counts"] = identity_quarantine_counts
    final_board_construction["elite"]["identity_quarantine_rejection_reason"] = (
        IDENTITY_QUARANTINE_REJECTION_REASON
    )
    final_board_construction["elite"]["duplicate_betting_identity_drop_count"] = duplicate_betting_identity_drop_count
    final_board_construction["elite"]["duplicate_betting_identity_drop_groups"] = duplicate_betting_identity_drop_groups
    final_board_construction["elite"]["duplicate_betting_identity_drop_counts_by_market_type"] = (
        duplicate_betting_identity_drop_counts_by_market_type
    )
    final_board_construction["elite"]["duplicate_betting_identity_rejection_reason"] = (
        DUPLICATE_BETTING_IDENTITY_REASON
    )
    final_board_construction["elite"]["diagnostic_live_flag_count"] = int(diagnostic_live_mask.sum())
    final_board_construction["elite"]["qualification_reason_missing_count"] = int(
        qualification_reason_series.fillna("").astype(str).str.strip().eq("").sum()
    )

    final_board_construction["full_market"]["input_count"] = len(prepared_df)
    final_board_construction["full_market"]["post_live_market_gate_count"] = len(live_candidates_before_dedupe_df)
    final_board_construction["full_market"]["post_duplicate_betting_identity_dedupe_count"] = len(live_candidates_df)
    final_board_construction["full_market"]["unsupported_milestone_count"] = unsupported_milestone_count
    final_board_construction["full_market"]["unsupported_active_operator_market_count"] = unsupported_active_market_count
    final_board_construction["full_market"]["unsupported_active_operator_market_counts"] = unsupported_active_market_counts
    final_board_construction["full_market"]["identity_quarantine_count"] = identity_quarantine_count
    final_board_construction["full_market"]["identity_quarantine_reason_counts"] = identity_quarantine_counts
    final_board_construction["full_market"]["identity_quarantine_rejection_reason"] = (
        IDENTITY_QUARANTINE_REJECTION_REASON
    )
    final_board_construction["full_market"]["duplicate_betting_identity_drop_count"] = (
        duplicate_betting_identity_drop_count
    )
    final_board_construction["full_market"]["duplicate_betting_identity_drop_groups"] = (
        duplicate_betting_identity_drop_groups
    )
    final_board_construction["full_market"]["duplicate_betting_identity_drop_counts_by_market_type"] = (
        duplicate_betting_identity_drop_counts_by_market_type
    )
    final_board_construction["full_market"]["duplicate_betting_identity_rejection_reason"] = (
        DUPLICATE_BETTING_IDENTITY_REASON
    )
    final_board_construction["full_market"]["diagnostic_live_flag_count"] = int(diagnostic_live_mask.sum())

    elite_df = select_elite_board(live_candidates_df)
    elite_count = len(elite_df)
    final_board_construction["elite"]["selected_count"] = elite_count

    full_market_df = select_top_per_market(live_candidates_df, per_market_limit)
    full_market_count = len(full_market_df)
    final_board_construction["full_market"]["selected_count"] = full_market_count

    selected_identities = _selection_identity_frame(elite_df) | _selection_identity_frame(full_market_df)
    if not live_candidates_df.empty:
        for idx, row in live_candidates_df.iterrows():
            identity = (
                idx,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
            if identity not in selected_identities and not str(prepared_df.at[idx, "selection_rejection_reason"]).strip():
                prepared_df.at[idx, "selection_rejection_reason"] = SELECTION_NOT_SELECTED_BY_BOARD_SELECTOR_REASON

    not_selected_df = prepared_df[prepared_df["selection_rejection_reason"].astype(str).str.strip().ne("")].copy()
    final_board_construction["selection_rejection_reasons"] = (
        not_selected_df["selection_rejection_reason"].value_counts().rename_axis("reason").reset_index(name="count").to_dict("records")
        if not not_selected_df.empty
        else []
    )
    sample_cols = [
        "player_name",
        "entity_name",
        "market_type",
        "team",
        "team_abbr",
        "selection_score",
        "confidence",
        "edge",
        "quality_score",
        "qualification_reason",
        "source_lane",
        "selection_rejection_reason",
        "recommended_action",
        "identity_quarantine_reason",
        "identity_team_conflict_reason",
        "player_identity_status",
        "player_identity_conflict_reason",
        "canonical_player_id",
        "canonical_player_name",
        "canonical_team_abbr",
        "provider_team_abbr",
        "odds_team_abbr",
        "baseline_team_abbr",
        "resolved_team_abbr",
        "identity_source_team_abbr",
    ]
    available_sample_cols = [col for col in sample_cols if col in prepared_df.columns]
    final_board_construction["qualified_but_not_selected_rows"] = (
        not_selected_df[available_sample_cols].head(10).to_dict("records")
        if available_sample_cols and not not_selected_df.empty
        else []
    )
    final_board_construction["identity_quarantine"] = {
        "rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
        "total_rows_dropped": identity_quarantine_count,
        "counts_by_reason": identity_quarantine_counts,
    }

    return elite_df, full_market_df, final_board_construction


def assign_candidate_lanes(
    qualified_df: pd.DataFrame,
    elite_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
) -> dict[str, Any]:
    """Assign candidates to lanes and track which were selected.

    This produces a lane assignment summary showing why candidates ended up
    in different boards (elite vs full_market vs none).

    Args:
        qualified_df: All qualified candidates before board selection
        elite_df: Candidates in elite board
        full_market_df: Candidates in full_market board

    Returns:
        Lane assignment summary with counts and reasons
    """
    elite_set = _selection_identity_frame(elite_df)
    full_market_set = _selection_identity_frame(full_market_df)

    in_elite = 0
    in_full_market = 0
    in_neither = 0

    if not qualified_df.empty:
        for _, row in qualified_df.iterrows():
            key = (
                row.name,
                str(row.get("market_type", "")),
                _display_name(row),
                str(row.get("team", row.get("team_abbr", "")) or "").strip().upper(),
            )
            if key in elite_set:
                in_elite += 1
            elif key in full_market_set:
                in_full_market += 1
            else:
                in_neither += 1

    return {
        "total_qualified": len(qualified_df),
        "assigned_to_elite": in_elite,
        "assigned_to_full_market": in_full_market,
        "qualified_but_not_selected": in_neither,
        "elite_board_size": len(elite_df),
        "full_market_board_size": len(full_market_df),
    }
