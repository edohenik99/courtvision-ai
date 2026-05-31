from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_VERSION = "1.0"
REPORT_FILE_PREFIX = "learning_brain_report"

STATUS_LEARNING_HEALTHY = "LEARNING_HEALTHY"
STATUS_LEARNING_HEALTHY_LOW_SAMPLE = "LEARNING_HEALTHY_LOW_SAMPLE"
STATUS_LEARNING_NEEDS_MORE_DATA = "LEARNING_NEEDS_MORE_DATA"
STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY = "LEARNING_BLOCKED_BY_MISSING_HISTORY"

RECOMMEND_KEEP_BLOCKED = "KEEP_BLOCKED"
RECOMMEND_KEEP_SHADOW = "KEEP_SHADOW"
RECOMMEND_WATCHLIST = "WATCHLIST"
RECOMMEND_MANUAL_REVIEW = "MANUAL_REVIEW_CANDIDATE"
RECOMMEND_PROMOTION_REQUIRES_APPROVAL = "PROMOTION_CANDIDATE_REQUIRES_APPROVAL"
RECOMMEND_DEMOTE_OR_BLOCK = "DEMOTE_OR_BLOCK"

CORE_NO_CHANGE = "NO_CHANGE"
CORE_COLLECT_MORE_DATA = "COLLECT_MORE_DATA"
CORE_SHADOW_RULE_ADJUSTMENT_ONLY = "SHADOW_RULE_ADJUSTMENT_ONLY"
CORE_MANUAL_REVIEW_RULE_PROPOSAL = "MANUAL_REVIEW_RULE_PROPOSAL"

SAFETY_DECLARATIONS: tuple[str, ...] = (
    "This report does not change thresholds.",
    "This report does not promote candidates.",
    "This report does not alter final_decision.",
    "This report does not create bets.",
    "This report does not write pick_history.csv.",
    "This report does not modify any history files.",
    "This report does not regenerate boards.",
    "This report does not train a model into production.",
    "This report does not change Elite/Kelly logic.",
    "Human approval is required before any rule change.",
    "Automatic production rule updates are not allowed.",
)

SUPPORTED_MARKETS: set[str] = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
    "player_steals",
    "player_blocks",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}

COMBO_MARKETS: set[str] = {
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}

MARKET_ALIASES: dict[str, str] = {
    "points": "player_points",
    "pts": "player_points",
    "rebounds": "player_rebounds",
    "reb": "player_rebounds",
    "assists": "player_assists",
    "ast": "player_assists",
    "3pt_made": "player_3pt_made",
    "threes": "player_3pt_made",
    "three_pointers_made": "player_3pt_made",
    "player_threes": "player_3pt_made",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
}

HISTORY_SOURCES: dict[str, dict[str, str | int]] = {
    "real_money_elite_history": {
        "filename": "pick_history.csv",
        "default_lane": "REAL_MONEY_ELITE",
        "role": "primary",
        "priority": 0,
    },
    "shadow_candidate_lane_history": {
        "filename": "shadow_candidate_lane_history.csv",
        "default_lane": "SHADOW_CANDIDATE_RESEARCH",
        "role": "primary",
        "priority": 1,
    },
    "incubator_history": {
        "filename": "incubator_history.csv",
        "default_lane": "INCUBATOR_RESEARCH",
        "role": "primary",
        "priority": 2,
    },
    "full_market_shadow_history": {
        "filename": "market_shadow_history.csv",
        "default_lane": "FULL_MARKET_SHADOW",
        "role": "primary",
        "priority": 3,
    },
    "paper_kelly_history": {
        "filename": "paper_kelly_history.csv",
        "default_lane": "PAPER_KELLY_CORROBORATION",
        "role": "corroborating",
        "priority": 9,
    },
}

BUCKET_DIMENSIONS: tuple[str, ...] = (
    "market_type",
    "selection",
    "context_caution_level",
    "context_pick_alignment",
    "research_lane",
    "edge_bucket",
    "confidence_bucket",
    "quality_bucket",
    "odds_bucket",
    "same_opponent_warning",
    "manual_review_required",
    "identity_resolution_category",
)

NORMALIZED_COLUMNS: tuple[str, ...] = (
    "source",
    "source_role",
    "source_priority",
    "prediction_date",
    "source_artifact_date",
    "player_name",
    "market_type",
    "selection",
    "line",
    "odds",
    "edge",
    "confidence",
    "quality_score",
    "context_caution_level",
    "context_pick_alignment",
    "context_conflict_cause",
    "research_lane",
    "source_rejection_reason",
    "identity_resolution_category",
    "manual_review_required",
    "same_opponent_warning",
    "unsupported_market",
    "identity_conflict",
    "source_date_mismatch",
    "canonical_result",
    "actual_value",
    "flat_profit_loss",
    "grading_skip_reason",
    "reason_not_real_kelly",
    "candidate_key",
    "edge_bucket",
    "confidence_bucket",
    "quality_bucket",
    "odds_bucket",
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return default
    return text


def _safe_lower(value: Any, default: str = "") -> str:
    return _safe_text(value, default=default).lower()


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_lower(value) in {"true", "t", "1", "1.0", "yes", "y"}


def _round(value: Any, digits: int = 6) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number, digits)


def _normalize_market(value: Any) -> str:
    text = _safe_lower(value).replace(" ", "_")
    return MARKET_ALIASES.get(text, text)


def _normalize_selection(value: Any) -> str:
    text = _safe_lower(value)
    if text in {"o", "over"}:
        return "over"
    if text in {"u", "under"}:
        return "under"
    return text or "unknown"


def _line_key(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_lower(value)
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _first_text(row: pd.Series | dict[str, Any], columns: tuple[str, ...], default: str = "") -> str:
    for column in columns:
        if column in row:
            text = _safe_text(row.get(column))
            if text:
                return text
    return default


def _first_float(row: pd.Series | dict[str, Any], columns: tuple[str, ...]) -> float | None:
    for column in columns:
        if column in row:
            number = _safe_float(row.get(column))
            if number is not None:
                return number
    return None


def _american_profit_factor(odds: Any) -> float:
    number = _safe_float(odds)
    if number is None or abs(number) < 1:
        return 100.0 / 110.0
    if number > 0:
        return number / 100.0
    return 100.0 / abs(number)


def _american_break_even(odds: Any) -> float | None:
    number = _safe_float(odds)
    if number is None or abs(number) < 1:
        return None
    if number > 0:
        return 100.0 / (number + 100.0)
    return abs(number) / (abs(number) + 100.0)


def _result_from_row(row: pd.Series | dict[str, Any]) -> str:
    if _truthy(row.get("hit")):
        return "hit"
    if _truthy(row.get("miss")):
        return "miss"
    if _truthy(row.get("push")):
        return "push"

    raw = _safe_lower(
        _first_text(
            row,
            ("result_status", "graded_result", "result", "status", "grading_status"),
        )
    )
    if raw in {"hit", "win", "won", "winner", "true"}:
        return "hit"
    if raw in {"miss", "loss", "lost", "loser", "false"}:
        return "miss"
    if raw in {"push", "tie"}:
        return "push"
    if raw in {"void", "canceled", "cancelled", "no_action"}:
        return "void"
    if raw == "unsupported":
        return "unsupported"
    if raw in {"pending", "open", "open_game_pending", "game_not_final", "ungraded", ""}:
        return "pending"
    if "pending" in raw or "not_final" in raw:
        return "pending"
    if "void" in raw:
        return "void"
    return raw or "pending"


def _profit_for_result(row: pd.Series | dict[str, Any], result: str) -> float | None:
    if result not in {"hit", "miss", "push"}:
        return None
    for column in ("flat_profit_loss", "shadow_roi", "paper_roi"):
        if column in row:
            value = _safe_float(row.get(column))
            if value is not None:
                return value
    if result == "hit":
        return _american_profit_factor(row.get("odds") or row.get("entry_odds"))
    if result == "miss":
        return -1.0
    return 0.0


def _bucket_edge(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    abs_edge = abs(number)
    if abs_edge >= 5.0:
        return "5+"
    if abs_edge >= 3.0:
        return "3-5"
    if abs_edge >= 2.0:
        return "2-3"
    if abs_edge >= 1.0:
        return "1-2"
    return "<1"


def _bucket_confidence(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number >= 0.85:
        return "0.85+"
    if number >= 0.75:
        return "0.75-0.85"
    if number >= 0.65:
        return "0.65-0.75"
    if number >= 0.55:
        return "0.55-0.65"
    return "<0.55"


def _bucket_quality(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number >= 90:
        return "90+"
    if number >= 80:
        return "80-90"
    if number >= 70:
        return "70-80"
    if number >= 60:
        return "60-70"
    return "<60"


def _bucket_odds(value: Any) -> str:
    number = _safe_float(value)
    if number is None or abs(number) < 1:
        return "unknown"
    if number > 0:
        return "plus_money"
    if number <= -150:
        return "heavy_favorite"
    return "standard_negative"


def _candidate_key_from_values(
    prediction_date: Any,
    player_name: Any,
    market_type: Any,
    selection: Any,
    line: Any,
) -> str:
    parts = (
        _safe_lower(prediction_date),
        re.sub(r"\s+", " ", _safe_lower(player_name)),
        _normalize_market(market_type),
        _normalize_selection(selection),
        _line_key(line),
    )
    if not all(parts):
        return ""
    return "|".join(parts)


def _read_csv(path: Path) -> tuple[pd.DataFrame, str | None]:
    if not path.exists():
        return pd.DataFrame(), "missing"
    if path.stat().st_size == 0:
        return pd.DataFrame(), "empty"
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False), None
    except (pd.errors.EmptyDataError, OSError, UnicodeDecodeError, ValueError) as exc:
        return pd.DataFrame(), f"read_error:{type(exc).__name__}"


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "missing"
    if path.stat().st_size == 0:
        return {}, "empty"
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return {}, f"read_error:{type(exc).__name__}"
    return payload if isinstance(payload, dict) else {}, None


def _read_text(path: Path) -> tuple[str, str | None]:
    if not path.exists():
        return "", "missing"
    if path.stat().st_size == 0:
        return "", "empty"
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace"), "decode_replaced"
    except OSError as exc:
        return "", f"read_error:{type(exc).__name__}"


def normalize_history_frame(source: str, df: pd.DataFrame) -> pd.DataFrame:
    descriptor = HISTORY_SOURCES[source]
    if df.empty:
        return pd.DataFrame(columns=NORMALIZED_COLUMNS)

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        prediction_date = _first_text(row, ("prediction_date", "game_date", "date"))
        source_artifact_date = _first_text(row, ("source_artifact_date", "artifact_date"))
        player_name = _first_text(row, ("player_name", "player", "entity_name"), default="Unknown")
        market_type = _normalize_market(_first_text(row, ("market_type", "market"), default="unknown"))
        selection = _normalize_selection(_first_text(row, ("selection", "side"), default="unknown"))
        line = _first_float(row, ("line", "sportsbook_line", "entry_line"))
        odds = _first_float(row, ("odds", "entry_odds"))
        edge = _first_float(row, ("edge", "directional_edge", "abs_edge"))
        confidence = _first_float(row, ("confidence",))
        quality_score = _first_float(row, ("quality_score", "quality"))
        context_caution = _safe_lower(
            _first_text(row, ("context_caution_level", "caution_level"), default="unknown"),
            default="unknown",
        )
        context_alignment = _safe_lower(
            _first_text(row, ("context_pick_alignment", "context_edge_label"), default="unknown"),
            default="unknown",
        )
        context_conflict_cause = _first_text(row, ("context_conflict_cause", "conflict_cause"))
        lane = _first_text(
            row,
            ("research_lane", "lane", "paper_bucket", "promotion_status"),
            default=str(descriptor["default_lane"]),
        )
        if source == "incubator_history":
            lane = "INCUBATOR_RESEARCH"
        elif source == "full_market_shadow_history" and not lane:
            lane = "FULL_MARKET_SHADOW"
        source_rejection = _first_text(
            row,
            (
                "source_rejection_reason",
                "final_elite_rejection_reason",
                "kelly_projected_skip_reason",
                "reason_not_real_kelly",
                "qualification_reason",
                "grading_skip_reason",
            ),
        )
        identity_category = _first_text(
            row,
            (
                "identity_resolution_category",
                "identity_category",
                "source_identity_resolution_category",
                "player_identity_status",
            ),
            default="unknown",
        )
        reason_not_real_kelly = _first_text(row, ("reason_not_real_kelly", "kelly_projected_skip_reason"))
        result = _result_from_row(row)
        same_opponent_warning = (
            _truthy(row.get("same_opponent_warning"))
            or _truthy(row.get("same_opponent_under_warning"))
            or _truthy(row.get("same_opponent_flag"))
        )
        manual_review_required = (
            _truthy(row.get("manual_review_required"))
            or "manual" in _safe_lower(row.get("promotion_status"))
            or "manual" in _safe_lower(row.get("historical_recommendation"))
        )
        identity_conflict = (
            "conflict" in _safe_lower(identity_category)
            or _truthy(row.get("source_identity_conflict"))
            or _truthy(row.get("identity_conflict"))
            or "identity_conflict" in _safe_lower(source_rejection)
        )
        unsupported_market = (
            market_type not in SUPPORTED_MARKETS
            or result == "unsupported"
            or "unsupported" in _safe_lower(source_rejection)
            or "unsupported" in _safe_lower(reason_not_real_kelly)
        )
        source_date_mismatch = bool(
            source_artifact_date
            and prediction_date
            and _safe_text(source_artifact_date) != _safe_text(prediction_date)
        )
        profit = _profit_for_result(row, result)
        candidate_key = _candidate_key_from_values(
            prediction_date,
            player_name,
            market_type,
            selection,
            line,
        )
        normalized = {
            "source": source,
            "source_role": str(descriptor["role"]),
            "source_priority": int(descriptor["priority"]),
            "prediction_date": prediction_date,
            "source_artifact_date": source_artifact_date,
            "player_name": player_name,
            "market_type": market_type,
            "selection": selection,
            "line": line,
            "odds": odds,
            "edge": edge,
            "confidence": confidence,
            "quality_score": quality_score,
            "context_caution_level": context_caution or "unknown",
            "context_pick_alignment": context_alignment or "unknown",
            "context_conflict_cause": context_conflict_cause,
            "research_lane": lane,
            "source_rejection_reason": source_rejection,
            "identity_resolution_category": identity_category or "unknown",
            "manual_review_required": bool(manual_review_required),
            "same_opponent_warning": bool(same_opponent_warning),
            "unsupported_market": bool(unsupported_market),
            "identity_conflict": bool(identity_conflict),
            "source_date_mismatch": bool(source_date_mismatch),
            "canonical_result": result,
            "actual_value": _first_text(row, ("actual_value", "actual")),
            "flat_profit_loss": profit,
            "grading_skip_reason": _first_text(row, ("grading_skip_reason", "grading_reason")),
            "reason_not_real_kelly": reason_not_real_kelly,
            "candidate_key": candidate_key,
            "edge_bucket": _bucket_edge(edge),
            "confidence_bucket": _bucket_confidence(confidence),
            "quality_bucket": _bucket_quality(quality_score),
            "odds_bucket": _bucket_odds(odds),
        }
        rows.append(normalized)
    return pd.DataFrame(rows, columns=NORMALIZED_COLUMNS)


def load_history_frames(history_root: str | Path) -> tuple[dict[str, pd.DataFrame], dict[str, str], list[str]]:
    root = Path(history_root)
    frames: dict[str, pd.DataFrame] = {}
    found: dict[str, str] = {}
    warnings: list[str] = []
    for source, descriptor in HISTORY_SOURCES.items():
        path = root / str(descriptor["filename"])
        raw_df, error = _read_csv(path)
        if error == "missing":
            warnings.append(f"missing history: {path}")
            frames[source] = pd.DataFrame(columns=NORMALIZED_COLUMNS)
            continue
        if error:
            warnings.append(f"history {path} could not be fully read ({error})")
        else:
            found[source] = str(path)
        frames[source] = normalize_history_frame(source, raw_df)
    return frames, found, warnings


def _dedupe_primary_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int, list[dict[str, Any]]]:
    if df.empty:
        return df.copy(), 0, []
    primary = df[df["source_role"] != "corroborating"].copy()
    if primary.empty:
        return primary, 0, []

    keyed = primary[primary["candidate_key"].astype(str) != ""].copy()
    unkeyed = primary[primary["candidate_key"].astype(str) == ""].copy()
    duplicate_items: list[dict[str, Any]] = []
    duplicate_count = 0
    if not keyed.empty:
        grouped = keyed.groupby("candidate_key", dropna=False)
        for candidate_key, group in grouped:
            if len(group) <= 1:
                continue
            duplicate_count += len(group) - 1
            duplicate_items.append(
                {
                    "candidate_key": candidate_key,
                    "count": int(len(group)),
                    "sources": sorted(str(value) for value in group["source"].dropna().unique()),
                    "lanes": sorted(str(value) for value in group["research_lane"].dropna().unique()),
                }
            )
        keyed = keyed.sort_values(["source_priority", "source", "research_lane"]).drop_duplicates(
            subset=["candidate_key"],
            keep="first",
        )
    deduped = pd.concat([keyed, unkeyed], ignore_index=True)
    return deduped.reset_index(drop=True), int(duplicate_count), duplicate_items[:20]


def _wilson_lower_bound(hits: int, attempts: int, z: float = 1.96) -> float | None:
    if attempts <= 0:
        return None
    p_hat = hits / attempts
    denominator = 1 + z * z / attempts
    centre = p_hat + z * z / (2 * attempts)
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * attempts)) / attempts)
    return max(0.0, (centre - margin) / denominator)


def _sample_quality_flag(stats: dict[str, Any], min_sample: int) -> str:
    total = int(stats.get("total_rows", 0))
    graded = int(stats.get("graded_rows", 0))
    pending = int(stats.get("pending_rows", 0))
    dirty = int(stats.get("dirty_data_rows", 0))
    if total <= 0:
        return "NO_DATA"
    if dirty:
        return "DIRTY_DATA"
    if pending and pending / max(total, 1) >= 0.5:
        return "PENDING_HEAVY"
    if graded < min_sample:
        return "LOW_SAMPLE"
    if graded >= max(50, min_sample * 2):
        return "STRONG_SAMPLE"
    return "MODERATE_SAMPLE"


def aggregate_stats(df: pd.DataFrame, *, label: str = "", min_sample: int = 20) -> dict[str, Any]:
    if df.empty:
        stats = {
            "label": label,
            "total_rows": 0,
            "graded_rows": 0,
            "pending_rows": 0,
            "void_rows": 0,
            "unsupported_rows": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "hit_rate": None,
            "roi": None,
            "flat_profit_loss": 0.0,
            "average_edge": None,
            "average_confidence": None,
            "average_quality": None,
            "average_line": None,
            "average_odds": None,
            "break_even_hit_rate": None,
            "wilson_lower_bound": None,
            "sample_quality_flag": "NO_DATA",
            "same_opponent_warning_rows": 0,
            "manual_review_required_rows": 0,
            "identity_conflict_rows": 0,
            "source_date_mismatch_rows": 0,
            "dirty_data_rows": 0,
        }
        return stats

    statuses = df["canonical_result"].fillna("").astype(str).str.lower()
    wins = int((statuses == "hit").sum())
    losses = int((statuses == "miss").sum())
    pushes = int((statuses == "push").sum())
    voids = int((statuses == "void").sum())
    unsupported = int(df["unsupported_market"].fillna(False).astype(bool).sum())
    graded = wins + losses + pushes
    pending = int((statuses == "pending").sum())
    attempts = wins + losses
    profit = float(pd.to_numeric(df["flat_profit_loss"], errors="coerce").fillna(0.0).sum())
    odds_values = pd.to_numeric(df["odds"], errors="coerce").dropna()
    average_odds = float(odds_values.mean()) if not odds_values.empty else None
    break_even = _american_break_even(average_odds) if average_odds is not None else None
    same_opponent = int(df["same_opponent_warning"].fillna(False).astype(bool).sum())
    manual_review = int(df["manual_review_required"].fillna(False).astype(bool).sum())
    identity_conflict = int(df["identity_conflict"].fillna(False).astype(bool).sum())
    source_date_mismatch = int(df["source_date_mismatch"].fillna(False).astype(bool).sum())
    dirty_data_rows = identity_conflict + source_date_mismatch
    actual_missing = int(
        (
            statuses.isin(["hit", "miss", "push"])
            & (df["actual_value"].fillna("").astype(str).str.strip() == "")
        ).sum()
    )
    dirty_data_rows += actual_missing

    def avg(column: str) -> float | None:
        values = pd.to_numeric(df[column], errors="coerce").dropna()
        return round(float(values.mean()), 6) if not values.empty else None

    stats = {
        "label": label,
        "total_rows": int(len(df)),
        "graded_rows": int(graded),
        "pending_rows": int(pending),
        "void_rows": int(voids),
        "unsupported_rows": int(unsupported),
        "wins": int(wins),
        "losses": int(losses),
        "pushes": int(pushes),
        "hit_rate": round(wins / attempts, 6) if attempts else None,
        "roi": round(profit / graded, 6) if graded else None,
        "flat_profit_loss": round(profit, 6),
        "average_edge": avg("edge"),
        "average_confidence": avg("confidence"),
        "average_quality": avg("quality_score"),
        "average_line": avg("line"),
        "average_odds": round(float(average_odds), 6) if average_odds is not None else None,
        "break_even_hit_rate": round(float(break_even), 6) if break_even is not None else None,
        "wilson_lower_bound": (
            round(_wilson_lower_bound(wins, attempts), 6)
            if _wilson_lower_bound(wins, attempts) is not None
            else None
        ),
        "same_opponent_warning_rows": same_opponent,
        "manual_review_required_rows": manual_review,
        "identity_conflict_rows": identity_conflict,
        "source_date_mismatch_rows": source_date_mismatch,
        "missing_actual_value_rows": actual_missing,
        "dirty_data_rows": dirty_data_rows,
    }
    stats["sample_quality_flag"] = _sample_quality_flag(stats, min_sample)
    return stats


def _is_combo_market(value: Any) -> bool:
    return _normalize_market(value) in COMBO_MARKETS


def _bucket_has_truthy_dimension(dimension: str, bucket: Any, expected_dimension: str) -> bool:
    return dimension == expected_dimension and _safe_lower(bucket) == "true"


def recommend_bucket(stats: dict[str, Any], *, dimension: str, bucket: str, min_sample: int = 20) -> str:
    total = int(stats.get("total_rows", 0))
    graded = int(stats.get("graded_rows", 0))
    roi = stats.get("roi")
    hit_rate = stats.get("hit_rate")
    break_even = stats.get("break_even_hit_rate")
    wilson = stats.get("wilson_lower_bound")
    bucket_lower = _safe_lower(bucket)
    dimension_lower = _safe_lower(dimension)
    unsupported = int(stats.get("unsupported_rows", 0)) > 0 or (
        dimension_lower == "market_type" and bucket_lower not in SUPPORTED_MARKETS
    )
    same_warning = int(stats.get("same_opponent_warning_rows", 0)) == total and total > 0
    identity_conflict = int(stats.get("identity_conflict_rows", 0)) > 0 or (
        dimension_lower == "identity_resolution_category" and "conflict" in bucket_lower
    )
    pending_heavy = stats.get("sample_quality_flag") == "PENDING_HEAVY"
    dirty = stats.get("sample_quality_flag") == "DIRTY_DATA"
    manual_review = int(stats.get("manual_review_required_rows", 0)) > 0 or _bucket_has_truthy_dimension(
        dimension,
        bucket,
        "manual_review_required",
    )
    combo_market = dimension_lower == "market_type" and bucket_lower in COMBO_MARKETS
    under_bucket = dimension_lower == "selection" and bucket_lower == "under"
    over_bucket = dimension_lower == "selection" and bucket_lower == "over"
    high_caution = dimension_lower == "context_caution_level" and "high" in bucket_lower
    broad_dimension = dimension_lower in {
        "selection",
        "context_caution_level",
        "context_pick_alignment",
        "edge_bucket",
        "confidence_bucket",
        "quality_bucket",
        "odds_bucket",
    }

    if total <= 0:
        return RECOMMEND_KEEP_SHADOW
    if unsupported or same_warning or identity_conflict:
        return RECOMMEND_KEEP_BLOCKED
    if pending_heavy or dirty:
        return RECOMMEND_KEEP_SHADOW
    if graded < min_sample:
        if roi is not None and roi > 0:
            return RECOMMEND_WATCHLIST
        if roi is not None and roi < 0 and (over_bucket or high_caution):
            return RECOMMEND_KEEP_BLOCKED
        return RECOMMEND_KEEP_SHADOW
    if roi is not None and roi < 0:
        return RECOMMEND_KEEP_BLOCKED if (over_bucket or high_caution) else RECOMMEND_DEMOTE_OR_BLOCK
    if hit_rate is None or break_even is None or hit_rate < break_even:
        return RECOMMEND_KEEP_BLOCKED if (over_bucket or high_caution) else RECOMMEND_KEEP_SHADOW
    if wilson is None or wilson < break_even:
        return RECOMMEND_WATCHLIST if roi is not None and roi > 0 else RECOMMEND_KEEP_SHADOW
    if roi is None or roi < 0.02:
        return RECOMMEND_WATCHLIST
    if broad_dimension:
        return RECOMMEND_WATCHLIST
    if dimension_lower == "research_lane" and bucket_lower == "real_money_elite":
        return RECOMMEND_WATCHLIST
    if combo_market:
        return RECOMMEND_MANUAL_REVIEW
    if manual_review:
        return RECOMMEND_MANUAL_REVIEW
    if under_bucket:
        return RECOMMEND_PROMOTION_REQUIRES_APPROVAL
    return RECOMMEND_PROMOTION_REQUIRES_APPROVAL


def build_bucket_matrix(primary_df: pd.DataFrame, *, min_sample: int = 20) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    if primary_df.empty:
        return matrix
    for dimension in BUCKET_DIMENSIONS:
        if dimension not in primary_df.columns:
            continue
        for bucket, group in primary_df.groupby(dimension, dropna=False):
            bucket_text = _safe_text(bucket, default="unknown")
            stats = aggregate_stats(group, label=bucket_text, min_sample=min_sample)
            item = {
                "dimension": dimension,
                "bucket": bucket_text,
                **stats,
            }
            item["recommendation"] = recommend_bucket(
                stats,
                dimension=dimension,
                bucket=bucket_text,
                min_sample=min_sample,
            )
            matrix.append(item)
    matrix.sort(key=lambda row: (row["dimension"], str(row["bucket"])))
    return matrix


def _current_runtime_artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"
    return {
        "operator_bet_readiness_json": operator_dir / f"bet_readiness_report_{prediction_date}.json",
        "diagnostics_bet_readiness_json": diagnostics_dir / f"bet_readiness_report_{prediction_date}.json",
        "operator_no_bet_funnel_txt": operator_dir / f"no_bet_funnel_report_{prediction_date}.txt",
        "operator_no_bet_funnel_csv": operator_dir / f"no_bet_funnel_report_{prediction_date}.csv",
        "diagnostics_no_bet_funnel_json": diagnostics_dir / f"no_bet_funnel_report_{prediction_date}.json",
        "operator_safe_action_discovery_txt": operator_dir / f"safe_action_discovery_report_{prediction_date}.txt",
        "operator_safe_action_discovery_csv": operator_dir / f"safe_action_discovery_report_{prediction_date}.csv",
        "diagnostics_safe_action_discovery_json": diagnostics_dir / f"safe_action_discovery_report_{prediction_date}.json",
        "operator_under_visibility_txt": operator_dir / f"under_visibility_audit_{prediction_date}.txt",
        "operator_under_visibility_csv": operator_dir / f"under_visibility_audit_{prediction_date}.csv",
        "diagnostics_under_visibility_json": diagnostics_dir / f"under_visibility_audit_{prediction_date}.json",
        "operator_shadow_candidate_performance_txt": operator_dir
        / f"shadow_candidate_lane_performance_{prediction_date}.txt",
        "operator_shadow_candidate_performance_csv": operator_dir
        / f"shadow_candidate_lane_performance_{prediction_date}.csv",
        "diagnostics_shadow_candidate_performance_json": diagnostics_dir
        / f"shadow_candidate_lane_performance_{prediction_date}.json",
        "operator_incubator_performance_txt": operator_dir / f"incubator_performance_report_{prediction_date}.txt",
        "operator_incubator_performance_csv": operator_dir / f"incubator_performance_report_{prediction_date}.csv",
        "diagnostics_incubator_performance_json": diagnostics_dir / f"incubator_performance_report_{prediction_date}.json",
        "operator_full_market_board_csv": operator_dir / f"full_market_board_{prediction_date}.csv",
        "operator_elite_board_csv": operator_dir / f"elite_board_{prediction_date}.csv",
        "operator_near_elite_review_csv": operator_dir / f"near_elite_review_{prediction_date}.csv",
        "operator_operator_card_txt": operator_dir / f"operator_card_{prediction_date}.txt",
    }


def _artifact_inventory(runtime_root: Path, prediction_date: str) -> tuple[dict[str, str], list[str]]:
    paths = _current_runtime_artifact_paths(runtime_root, prediction_date)
    found = {name: str(path) for name, path in paths.items() if path.exists()}
    missing = [name for name, path in paths.items() if not path.exists()]
    return found, missing


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


def collect_no_bet_blockers(runtime_root: str | Path, prediction_date: str) -> tuple[list[str], dict[str, str], list[str]]:
    root = Path(runtime_root)
    found_artifacts, missing_artifacts = _artifact_inventory(root, prediction_date)
    blockers: set[str] = set()
    warnings: list[str] = []

    paths = _current_runtime_artifact_paths(root, prediction_date)
    full_market_df, full_error = _read_csv(paths["operator_full_market_board_csv"])
    elite_df, elite_error = _read_csv(paths["operator_elite_board_csv"])
    if full_error not in {None, "missing"}:
        warnings.append(f"full_market_board read warning: {full_error}")
    if elite_error not in {None, "missing"}:
        warnings.append(f"elite_board read warning: {elite_error}")
    if elite_error is None and elite_df.empty:
        blockers.add("no Elite rows")
        blockers.add("no Kelly eligible rows")
    if full_error is None and not full_market_df.empty and (elite_error != "missing" and elite_df.empty):
        blockers.add("no stakeable candidates")
    if full_error is None and not full_market_df.empty:
        if "context_caution_level" in full_market_df.columns and "selection" in full_market_df.columns:
            caution = full_market_df["context_caution_level"].fillna("").astype(str).str.lower()
            selection = full_market_df["selection"].fillna("").astype(str).str.lower()
            if ((caution.str.contains("high")) & (selection == "over")).any():
                blockers.add("high-caution OVER gate")
        for column in ("same_opponent_warning", "same_opponent_under_warning"):
            if column in full_market_df.columns and full_market_df[column].apply(_truthy).any():
                blockers.add("same-opponent warnings")
        if "context_pick_alignment" in full_market_df.columns:
            if full_market_df["context_pick_alignment"].fillna("").astype(str).str.lower().str.contains("conflict").any():
                blockers.add("context-conflicted candidates")

    readiness_found = False
    for name in ("operator_bet_readiness_json", "diagnostics_bet_readiness_json"):
        payload, error = _read_json(paths[name])
        if error == "missing":
            continue
        readiness_found = True
        serialized = json.dumps(payload, sort_keys=True).lower()
        if _contains_any(serialized, ("research_only", "research only")):
            blockers.add("research-only status")
        if "no elite" in serialized or "elite rows" in serialized:
            blockers.add("no Elite rows")
        if "kelly" in serialized and ("no" in serialized or "0" in serialized):
            blockers.add("no Kelly eligible rows")
        if "shadow" in serialized:
            blockers.add("shadow-only restriction")
        if "unsupported" in serialized:
            blockers.add("unsupported market")
        if "identity" in serialized:
            blockers.add("identity conflicts")
        raw_blockers = payload.get("blockers")
        if isinstance(raw_blockers, list):
            blockers.update(_safe_text(item) for item in raw_blockers if _safe_text(item))
    if not readiness_found:
        warnings.append("missing runtime artifact: bet_readiness_report JSON")

    no_bet_payload, no_bet_error = _read_json(paths["diagnostics_no_bet_funnel_json"])
    if no_bet_error == "missing":
        warnings.append("missing runtime artifact: no_bet_funnel_report JSON")
    elif no_bet_payload:
        aggregate = no_bet_payload.get("aggregate") if isinstance(no_bet_payload.get("aggregate"), dict) else {}
        if int(aggregate.get("total_elite_rows", 0) or 0) <= 0:
            blockers.add("no Elite rows")
        if int(aggregate.get("total_kelly_eligible_rows", 0) or 0) <= 0:
            blockers.add("no Kelly eligible rows")
        if int(aggregate.get("total_high_caution_over_blocks", 0) or 0) > 0:
            blockers.add("high-caution OVER gate")
        if int(aggregate.get("total_same_opponent_warning_rows", 0) or 0) > 0:
            blockers.add("same-opponent warnings")
        if int(aggregate.get("total_unsupported_active_market_rows", 0) or 0) > 0:
            blockers.add("unsupported market")

    card_text, card_error = _read_text(paths["operator_operator_card_txt"])
    if card_error == "missing":
        warnings.append("missing runtime artifact: operator_card TXT")
    elif card_text:
        lower = card_text.lower()
        if "no bet" in lower:
            blockers.add("research-only status")
        if "no elite" in lower or "elite picks count: 0" in lower:
            blockers.add("no Elite rows")
        if "kelly eligible count: 0" in lower or "kelly rows count: 0" in lower:
            blockers.add("no Kelly eligible rows")
        if "under research" in lower or "under_aligned_research" in lower:
            blockers.add("UNDER research not promoted")
            blockers.add("shadow-only restriction")
        if "combo" in lower and "kelly" in lower:
            blockers.add("combo market not Kelly eligible")

    if any("under_visibility" in key for key in found_artifacts):
        blockers.add("UNDER research not promoted")
        blockers.add("shadow-only restriction")
    if any("shadow_candidate" in key for key in found_artifacts):
        blockers.add("shadow-only restriction")
    if any("safe_action_discovery" in key for key in found_artifacts):
        blockers.add("research-only status")

    if not blockers:
        blockers.add("insufficient current slate artifacts to explain betting status")
    return sorted(blockers), found_artifacts, warnings


def _source_summaries(frames: dict[str, pd.DataFrame], *, min_sample: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for source, frame in frames.items():
        stats = aggregate_stats(frame, label=source, min_sample=min_sample)
        summaries.append(
            {
                "source": source,
                "source_role": str(HISTORY_SOURCES[source]["role"]),
                **stats,
            }
        )
    return summaries


def _learning_summary(label: str, df: pd.DataFrame, *, min_sample: int, source_role: str = "primary") -> dict[str, Any]:
    stats = aggregate_stats(df, label=label, min_sample=min_sample)
    recommendation = recommend_bucket(stats, dimension="research_lane", bucket=label, min_sample=min_sample)
    if source_role == "corroborating" and recommendation == RECOMMEND_PROMOTION_REQUIRES_APPROVAL:
        recommendation = RECOMMEND_WATCHLIST
    return {
        "bucket": label,
        "source_role": source_role,
        **stats,
        "recommendation": recommendation,
    }


def _build_what_learned(
    primary_df: pd.DataFrame,
    all_df: pd.DataFrame,
    *,
    min_sample: int,
) -> list[dict[str, Any]]:
    paper_df = all_df[all_df["source_role"] == "corroborating"] if not all_df.empty else all_df
    summaries: list[dict[str, Any]] = [
        _learning_summary(
            "UNDER_ALIGNED_RESEARCH",
            primary_df[primary_df["research_lane"].astype(str).str.upper() == "UNDER_ALIGNED_RESEARCH"],
            min_sample=min_sample,
        ),
        _learning_summary(
            "COMBO_OVER_WEAK_POSITIVE_RESEARCH",
            primary_df[primary_df["research_lane"].astype(str).str.upper() == "COMBO_OVER_WEAK_POSITIVE_RESEARCH"],
            min_sample=min_sample,
        ),
        _learning_summary(
            "INCUBATOR_RESEARCH",
            primary_df[
                (primary_df["research_lane"].astype(str).str.upper() == "INCUBATOR_RESEARCH")
                | (primary_df["source"] == "incubator_history")
            ],
            min_sample=min_sample,
        ),
        _learning_summary(
            "HIGH_CAUTION_OVER_DO_NOT_PROMOTE",
            primary_df[
                (primary_df["selection"] == "over")
                & (primary_df["context_caution_level"].astype(str).str.lower().str.contains("high"))
            ],
            min_sample=min_sample,
        ),
        _learning_summary(
            "NEAR_ELITE_RESEARCH",
            primary_df[primary_df["research_lane"].astype(str).str.upper() == "NEAR_ELITE_RESEARCH"],
            min_sample=min_sample,
        ),
        _learning_summary(
            "full-market OVERs",
            primary_df[(primary_df["source"] == "full_market_shadow_history") & (primary_df["selection"] == "over")],
            min_sample=min_sample,
        ),
        _learning_summary(
            "full-market UNDERs",
            primary_df[(primary_df["source"] == "full_market_shadow_history") & (primary_df["selection"] == "under")],
            min_sample=min_sample,
        ),
        _learning_summary(
            "context-conflicted candidates",
            primary_df[
                primary_df["context_pick_alignment"].astype(str).str.lower().str.contains("conflict")
                | (primary_df["context_conflict_cause"].astype(str).str.strip() != "")
            ],
            min_sample=min_sample,
        ),
        _learning_summary(
            "same-opponent warnings",
            primary_df[primary_df["same_opponent_warning"].fillna(False).astype(bool)],
            min_sample=min_sample,
        ),
        _learning_summary(
            "paper Kelly corroboration",
            paper_df,
            min_sample=min_sample,
            source_role="corroborating",
        ),
    ]
    return summaries


def _explain_protection(stats: dict[str, Any]) -> str:
    if int(stats.get("total_rows", 0)) <= 0:
        return "No evidence available."
    roi = stats.get("roi")
    hit_rate = stats.get("hit_rate")
    break_even = stats.get("break_even_hit_rate")
    if stats.get("sample_quality_flag") in {"LOW_SAMPLE", "PENDING_HEAVY", "DIRTY_DATA"}:
        return "Block is protective because the evidence is incomplete, pending-heavy, or dirty."
    if roi is not None and roi < 0:
        return "Block is protective because graded ROI is negative."
    if hit_rate is not None and break_even is not None and hit_rate < break_even:
        return "Block is protective because hit rate trails break-even at the observed odds."
    return "Evidence may be improving, but any loosening would require manual review."


def _targeted_item(
    label: str,
    df: pd.DataFrame,
    *,
    min_sample: int,
    forced_recommendation: str | None = None,
) -> dict[str, Any]:
    stats = aggregate_stats(df, label=label, min_sample=min_sample)
    recommendation = forced_recommendation or recommend_bucket(
        stats,
        dimension="targeted_bucket",
        bucket=label,
        min_sample=min_sample,
    )
    return {
        "bucket": label,
        **stats,
        "protective_or_overblocking": _explain_protection(stats),
        "recommendation": recommendation,
    }


def build_keep_blocked_buckets(primary_df: pd.DataFrame, *, min_sample: int) -> list[dict[str, Any]]:
    if primary_df.empty:
        return []
    low_confidence = pd.to_numeric(primary_df["confidence"], errors="coerce") < 0.65
    low_quality = pd.to_numeric(primary_df["quality_score"], errors="coerce") < 70
    items = [
        _targeted_item(
            "broad OVERs",
            primary_df[primary_df["selection"] == "over"],
            min_sample=min_sample,
        ),
        _targeted_item(
            "high-caution OVERs",
            primary_df[
                (primary_df["selection"] == "over")
                & primary_df["context_caution_level"].astype(str).str.lower().str.contains("high")
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "conflicted-context OVERs",
            primary_df[
                (primary_df["selection"] == "over")
                & (
                    primary_df["context_pick_alignment"].astype(str).str.lower().str.contains("conflict")
                    | (primary_df["context_conflict_cause"].astype(str).str.strip() != "")
                )
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "same-opponent warning rows",
            primary_df[primary_df["same_opponent_warning"].fillna(False).astype(bool)],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "low-confidence rows",
            primary_df[low_confidence.fillna(False)],
            min_sample=min_sample,
        ),
        _targeted_item(
            "low-quality rows",
            primary_df[low_quality.fillna(False)],
            min_sample=min_sample,
        ),
        _targeted_item(
            "identity conflict rows",
            primary_df[primary_df["identity_conflict"].fillna(False).astype(bool)],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "unsupported markets",
            primary_df[primary_df["unsupported_market"].fillna(False).astype(bool)],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "unsupported combo props",
            primary_df[
                primary_df["market_type"].apply(_is_combo_market)
                & primary_df["reason_not_real_kelly"].astype(str).str.lower().str.contains("unsupported|not_real_kelly")
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
        _targeted_item(
            "incubator rows",
            primary_df[
                (primary_df["source"] == "incubator_history")
                | (primary_df["research_lane"].astype(str).str.upper() == "INCUBATOR_RESEARCH")
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_SHADOW,
        ),
        _targeted_item(
            "combo markets for real Kelly",
            primary_df[primary_df["market_type"].apply(_is_combo_market)],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_BLOCKED,
        ),
    ]
    return [item for item in items if int(item.get("total_rows", 0)) > 0]


def build_shadow_tracking_candidates(primary_df: pd.DataFrame, *, min_sample: int) -> list[dict[str, Any]]:
    if primary_df.empty:
        return []
    low_caution = primary_df["context_caution_level"].astype(str).str.lower().str.contains("low")
    aligned = primary_df["context_pick_alignment"].astype(str).str.lower().str.contains("aligned")
    combo = primary_df["market_type"].apply(_is_combo_market)
    items = [
        _targeted_item(
            "UNDER_ALIGNED_RESEARCH buckets",
            primary_df[primary_df["research_lane"].astype(str).str.upper() == "UNDER_ALIGNED_RESEARCH"],
            min_sample=min_sample,
        ),
        _targeted_item(
            "low-caution UNDER buckets",
            primary_df[(primary_df["selection"] == "under") & low_caution],
            min_sample=min_sample,
        ),
        _targeted_item(
            "context-aligned UNDER buckets",
            primary_df[(primary_df["selection"] == "under") & aligned],
            min_sample=min_sample,
        ),
        _targeted_item(
            "combo OVER weak positive buckets",
            primary_df[
                (primary_df["research_lane"].astype(str).str.upper() == "COMBO_OVER_WEAK_POSITIVE_RESEARCH")
                | ((primary_df["selection"] == "over") & combo)
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_SHADOW,
        ),
        _targeted_item(
            "near-elite buckets",
            primary_df[primary_df["research_lane"].astype(str).str.upper() == "NEAR_ELITE_RESEARCH"],
            min_sample=min_sample,
        ),
        _targeted_item(
            "selected high-caution combo OVER sub-buckets",
            primary_df[
                (primary_df["selection"] == "over")
                & combo
                & primary_df["context_caution_level"].astype(str).str.lower().str.contains("high")
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_SHADOW,
        ),
        _targeted_item(
            "incubator buckets",
            primary_df[
                (primary_df["source"] == "incubator_history")
                | (primary_df["research_lane"].astype(str).str.upper() == "INCUBATOR_RESEARCH")
            ],
            min_sample=min_sample,
            forced_recommendation=RECOMMEND_KEEP_SHADOW,
        ),
    ]

    normalized: list[dict[str, Any]] = []
    for item in items:
        if int(item.get("total_rows", 0)) <= 0:
            continue
        if item["recommendation"] == RECOMMEND_PROMOTION_REQUIRES_APPROVAL:
            item = {**item, "recommendation": RECOMMEND_MANUAL_REVIEW}
        elif item["recommendation"] not in {RECOMMEND_KEEP_SHADOW, RECOMMEND_WATCHLIST, RECOMMEND_MANUAL_REVIEW}:
            item = {**item, "recommendation": RECOMMEND_KEEP_SHADOW}
        normalized.append(item)
    return normalized


def _build_data_quality_warnings(
    *,
    history_warnings: list[str],
    runtime_warnings: list[str],
    source_summaries: list[dict[str, Any]],
    primary_df: pd.DataFrame,
    duplicate_count: int,
    duplicate_items: list[dict[str, Any]],
    prediction_date: str,
    bucket_matrix: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    warnings.extend(history_warnings)
    warnings.extend(runtime_warnings)
    if duplicate_count:
        warnings.append(
            f"duplicate rows detected across histories: {duplicate_count} duplicate row(s); examples={duplicate_items[:3]}"
        )
    for summary in source_summaries:
        if summary["sample_quality_flag"] == "LOW_SAMPLE" and int(summary["total_rows"]) > 0:
            warnings.append(f"low sample size in {summary['source']}: graded={summary['graded_rows']}")
        if summary["sample_quality_flag"] == "PENDING_HEAVY":
            warnings.append(f"pending-heavy source: {summary['source']}")
        if int(summary.get("void_rows", 0)) / max(int(summary.get("total_rows", 0)), 1) >= 0.3 and int(summary.get("total_rows", 0)) > 0:
            warnings.append(f"void-heavy source: {summary['source']}")
    if not primary_df.empty:
        stale_pending = primary_df[
            (primary_df["canonical_result"] == "pending")
            & (primary_df["prediction_date"].astype(str) < prediction_date)
        ]
        if not stale_pending.empty:
            warnings.append(f"stale pending rows: {len(stale_pending)}")
        if primary_df["source_date_mismatch"].fillna(False).astype(bool).any():
            warnings.append("source_artifact_date mismatches detected")
        unknown_players = primary_df["player_name"].fillna("").astype(str).str.lower().isin({"", "unknown"})
        if unknown_players.any():
            warnings.append(f"unknown player names: {int(unknown_players.sum())}")
        graded = primary_df["canonical_result"].isin(["hit", "miss", "push"])
        missing_actual = graded & (primary_df["actual_value"].fillna("").astype(str).str.strip() == "")
        if missing_actual.any():
            warnings.append(f"missing actual values on graded rows: {int(missing_actual.sum())}")
        if primary_df["unsupported_market"].fillna(False).astype(bool).any():
            warnings.append(f"unsupported markets detected: {int(primary_df['unsupported_market'].sum())}")
        clv_columns_visible = any(column in primary_df.columns for column in ("clv", "closing_line", "closing_line_observed"))
        if not clv_columns_visible:
            warnings.append("CLV/line-quality gaps: no normalized CLV coverage available")
    if any(item.get("sample_quality_flag") == "LOW_SAMPLE" for item in bucket_matrix):
        warnings.append("overfitting risk: many bucket cuts are below the minimum sample threshold")
    if any(item.get("sample_quality_flag") == "PENDING_HEAVY" for item in bucket_matrix):
        warnings.append("pending-heavy bucket detected")
    return sorted(dict.fromkeys(warnings))


def _total_by_source(frames: dict[str, pd.DataFrame], result: str | None = None) -> dict[str, int]:
    totals: dict[str, int] = {}
    for source, frame in frames.items():
        if frame.empty:
            totals[source] = 0
        elif result is None:
            totals[source] = int(len(frame))
        elif result == "graded":
            totals[source] = int(frame["canonical_result"].isin(["hit", "miss", "push"]).sum())
        elif result == "void":
            totals[source] = int((frame["canonical_result"] == "void").sum())
        else:
            totals[source] = int((frame["canonical_result"] == result).sum())
    return totals


def _status_for(primary_df: pd.DataFrame, history_files_found: dict[str, str], *, min_sample: int) -> str:
    if not history_files_found:
        return STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY
    if primary_df.empty:
        return STATUS_LEARNING_NEEDS_MORE_DATA
    graded = int(primary_df["canonical_result"].isin(["hit", "miss", "push"]).sum())
    if graded <= 0:
        return STATUS_LEARNING_NEEDS_MORE_DATA
    if graded < min_sample:
        return STATUS_LEARNING_HEALTHY_LOW_SAMPLE
    return STATUS_LEARNING_HEALTHY


def _top_buckets(bucket_matrix: list[dict[str, Any]], *, profitable: bool) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in bucket_matrix:
        roi = item.get("roi")
        if roi is None:
            continue
        if profitable and roi <= 0:
            continue
        if not profitable and roi >= 0:
            continue
        filtered.append(item)
    if profitable:
        filtered.sort(key=lambda row: (row.get("roi") or 0, row.get("graded_rows") or 0), reverse=True)
    else:
        filtered.sort(key=lambda row: (row.get("roi") or 0, -(row.get("graded_rows") or 0)))
    return filtered[:12]


def _recommended_core_changes(
    *,
    status: str,
    promotion_candidates: list[dict[str, Any]],
    shadow_tracking_candidates: list[dict[str, Any]],
) -> list[str]:
    if status in {STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY, STATUS_LEARNING_NEEDS_MORE_DATA}:
        return [CORE_COLLECT_MORE_DATA]
    if promotion_candidates:
        return [CORE_MANUAL_REVIEW_RULE_PROPOSAL, CORE_COLLECT_MORE_DATA]
    if any(item["recommendation"] in {RECOMMEND_WATCHLIST, RECOMMEND_MANUAL_REVIEW} for item in shadow_tracking_candidates):
        return [CORE_NO_CHANGE, CORE_COLLECT_MORE_DATA, CORE_SHADOW_RULE_ADJUSTMENT_ONLY]
    return [CORE_NO_CHANGE, CORE_COLLECT_MORE_DATA]


def build_learning_brain_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    min_sample: int = 20,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    frames, history_files_found, history_warnings = load_history_frames(history_root)
    all_frames = [frame for frame in frames.values() if not frame.empty]
    all_df = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame(columns=NORMALIZED_COLUMNS)
    primary_df, duplicate_count, duplicate_items = _dedupe_primary_rows(all_df)

    no_bet_blockers, runtime_artifacts_found, runtime_warnings = collect_no_bet_blockers(runtime_root, prediction_date)
    source_summaries = _source_summaries(frames, min_sample=min_sample)
    what_learned = _build_what_learned(primary_df, all_df, min_sample=min_sample)
    bucket_matrix = build_bucket_matrix(primary_df, min_sample=min_sample)
    keep_blocked_buckets = build_keep_blocked_buckets(primary_df, min_sample=min_sample)
    shadow_tracking_candidates = build_shadow_tracking_candidates(primary_df, min_sample=min_sample)
    promotion_candidates = [
        item
        for item in bucket_matrix
        if item.get("recommendation") == RECOMMEND_PROMOTION_REQUIRES_APPROVAL
    ]
    status = _status_for(primary_df, history_files_found, min_sample=min_sample)
    top_profitable = _top_buckets(bucket_matrix, profitable=True)
    top_losing = _top_buckets(bucket_matrix, profitable=False)
    data_quality_warnings = _build_data_quality_warnings(
        history_warnings=history_warnings,
        runtime_warnings=runtime_warnings,
        source_summaries=source_summaries,
        primary_df=primary_df,
        duplicate_count=duplicate_count,
        duplicate_items=duplicate_items,
        prediction_date=prediction_date,
        bucket_matrix=bucket_matrix,
    )
    recommended_core_changes = _recommended_core_changes(
        status=status,
        promotion_candidates=promotion_candidates,
        shadow_tracking_candidates=shadow_tracking_candidates,
    )
    decision_brain_posture = (
        "protecting_bankroll"
        if not promotion_candidates
        else "manual_review_needed_before_any_gate_change"
    )
    executive_explanation = (
        "The learning layer sees candidates, but the current evidence does not justify automatic staking. "
        "The decision brain is refusing to bet because Elite/Kelly evidence is absent or blocked by safety gates."
    )
    if status == STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY:
        executive_explanation = "No usable history files were found, so the learning layer cannot draw conclusions yet."
    elif status == STATUS_LEARNING_HEALTHY_LOW_SAMPLE:
        executive_explanation = (
            "History is present and readable, but graded samples are still below the requested minimum. "
            "The safest action is continued reporting and shadow collection."
        )

    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "status": status,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(Path(runtime_root)),
        "history_root": str(Path(history_root)),
        "min_sample": int(min_sample),
        "history_files_found": history_files_found,
        "runtime_artifacts_found": runtime_artifacts_found,
        "total_samples_by_source": _total_by_source(frames),
        "graded_samples_by_source": _total_by_source(frames, result="graded"),
        "pending_samples_by_source": _total_by_source(frames, result="pending"),
        "void_samples_by_source": _total_by_source(frames, result="void"),
        "primary_combined_sample_count": int(len(primary_df)),
        "primary_combined_graded_sample_count": int(primary_df["canonical_result"].isin(["hit", "miss", "push"]).sum())
        if not primary_df.empty
        else 0,
        "duplicate_rows_detected": int(duplicate_count),
        "duplicate_row_examples": duplicate_items,
        "no_bet_blockers": no_bet_blockers,
        "executive_verdict": {
            "status": status,
            "explanation": executive_explanation,
            "decision_brain_posture": decision_brain_posture,
            "production_change_recommended": False,
        },
        "source_summaries": source_summaries,
        "what_the_system_has_learned": what_learned,
        "bucket_performance_matrix": bucket_matrix,
        "top_profitable_buckets": top_profitable,
        "top_losing_buckets": top_losing,
        "promotion_candidates": promotion_candidates,
        "keep_blocked_buckets": keep_blocked_buckets,
        "shadow_tracking_candidates": shadow_tracking_candidates,
        "recommended_core_changes": recommended_core_changes,
        "data_quality_warnings": data_quality_warnings,
        "safety_declarations": list(SAFETY_DECLARATIONS),
        "recommended_next_step": [
            "Do not change core gates yet.",
            "Continue Learning Brain reporting.",
            "Expand shadow tracking for UNDER_ALIGNED_RESEARCH, NEAR_ELITE_RESEARCH, and selected combo research lanes.",
            "Do not promote anything automatically.",
            "Run a backtest before any live rule change proposal.",
        ],
        "applied_changes": False,
        "pick_history_modified": False,
        "live_rules_modified": False,
        "generated_real_money_recommendations": False,
    }
    return _json_ready(payload)


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.3f}"


def _render_stats_line(item: dict[str, Any]) -> str:
    return (
        f"- {item.get('bucket') or item.get('source') or item.get('label')}: "
        f"total={item.get('total_rows', 0)}, graded={item.get('graded_rows', 0)}, "
        f"pending={item.get('pending_rows', 0)}, void={item.get('void_rows', 0)}, "
        f"W/L/P={item.get('wins', 0)}/{item.get('losses', 0)}/{item.get('pushes', 0)}, "
        f"hit={_format_pct(item.get('hit_rate'))}, ROI={_format_pct(item.get('roi'))}, "
        f"edge={_format_num(item.get('average_edge'))}, conf={_format_num(item.get('average_confidence'))}, "
        f"quality={_format_num(item.get('average_quality'))}, sample={item.get('sample_quality_flag', 'NO_DATA')}, "
        f"rec={item.get('recommendation', 'n/a')}"
    )


def render_learning_brain_text(payload: dict[str, Any]) -> str:
    verdict = payload["executive_verdict"]
    promotion_candidates = payload["promotion_candidates"]
    lines: list[str] = [
        f"CourtVision Learning Brain Report - {payload['prediction_date']}",
        "=" * 76,
        "Reporting-only diagnostic. No Elite, Kelly, final_decision, bankroll, staking, thresholds, histories, or boards changed.",
        "",
        "1. Executive Verdict",
        "-" * 76,
        f"Status: {payload['status']}",
        verdict["explanation"],
        f"Decision brain posture: {verdict['decision_brain_posture']}.",
        "Production change recommended: no. Any future rule change requires human approval.",
        "",
        "2. Why No Bets?",
        "-" * 76,
    ]
    for blocker in payload["no_bet_blockers"]:
        lines.append(f"- {blocker}")
    lines.extend(
        [
            "",
            "3. What the System Has Learned",
            "-" * 76,
        ]
    )
    for item in payload["source_summaries"]:
        lines.append(_render_stats_line({**item, "bucket": item["source"]}))
    lines.append("")
    lines.append("Tracked learning buckets:")
    for item in payload["what_the_system_has_learned"]:
        lines.append(_render_stats_line(item))

    lines.extend(["", "4. Bucket Performance Matrix", "-" * 76])
    if payload["bucket_performance_matrix"]:
        for item in payload["bucket_performance_matrix"][:60]:
            lines.append(
                f"- {item['dimension']}={item['bucket']}: n={item['total_rows']}, graded={item['graded_rows']}, "
                f"pending={item['pending_rows']}, hit={_format_pct(item['hit_rate'])}, ROI={_format_pct(item['roi'])}, "
                f"WL={_format_pct(item['wilson_lower_bound'])}, BE={_format_pct(item['break_even_hit_rate'])}, "
                f"sample={item['sample_quality_flag']}, rec={item['recommendation']}"
            )
    else:
        lines.append("- none available")

    lines.extend(["", "5. What Should Stay Blocked", "-" * 76])
    if payload["keep_blocked_buckets"]:
        for item in payload["keep_blocked_buckets"]:
            lines.append(_render_stats_line(item))
            lines.append(f"  evidence: {item['protective_or_overblocking']}")
    else:
        lines.append("- none with available evidence")

    lines.extend(["", "6. What Might Deserve More Shadow Tracking", "-" * 76])
    if payload["shadow_tracking_candidates"]:
        for item in payload["shadow_tracking_candidates"]:
            lines.append(_render_stats_line(item))
            lines.append("  note: More shadow tracking does not mean production promotion.")
    else:
        lines.append("- none with available evidence")

    lines.extend(["", "7. What Might Deserve Promotion Later", "-" * 76])
    if promotion_candidates:
        for item in promotion_candidates:
            lines.append(
                f"- {item['dimension']}={item['bucket']}: {RECOMMEND_PROMOTION_REQUIRES_APPROVAL}; "
                f"graded={item['graded_rows']}, hit={_format_pct(item['hit_rate'])}, "
                f"ROI={_format_pct(item['roi'])}, BE={_format_pct(item['break_even_hit_rate'])}, "
                f"WL={_format_pct(item['wilson_lower_bound'])}"
            )
        lines.append("No automatic production promotion is allowed.")
    else:
        lines.append("Production promotion candidates: none.")

    lines.extend(["", "8. Core Brain Change Recommendations", "-" * 76])
    for item in payload["recommended_core_changes"]:
        lines.append(f"- {item}")
    lines.append("This report proposes possible future work only. It does not apply rule changes.")

    lines.extend(["", "9. Learning Safety Guardrails", "-" * 76])
    for declaration in payload["safety_declarations"]:
        lines.append(f"- {declaration}")

    lines.extend(["", "10. Data Quality Warnings", "-" * 76])
    if payload["data_quality_warnings"]:
        for warning in payload["data_quality_warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    lines.extend(["", "11. Recommended Next Step", "-" * 76])
    for item in payload["recommended_next_step"]:
        lines.append(f"- {item}")

    return "\n".join(lines) + "\n"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    runtime_root_path = Path(runtime_root)
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return (
        runtime_root_path / "operator" / f"{stem}.txt",
        runtime_root_path / "diagnostics" / f"{stem}.json",
    )


def write_learning_brain_report_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    min_sample: int = 20,
) -> tuple[Path, Path, dict[str, Any]]:
    text_path, json_path = report_paths_for_date(prediction_date=prediction_date, runtime_root=runtime_root)
    payload = build_learning_brain_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        min_sample=min_sample,
    )
    text = render_learning_brain_text(payload)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return text_path, json_path, payload


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.DataFrame):
        return [_json_ready(item) for item in value.to_dict("records")]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


__all__ = [
    "REPORT_FILE_PREFIX",
    "RECOMMEND_KEEP_BLOCKED",
    "RECOMMEND_KEEP_SHADOW",
    "RECOMMEND_MANUAL_REVIEW",
    "RECOMMEND_PROMOTION_REQUIRES_APPROVAL",
    "RECOMMEND_WATCHLIST",
    "STATUS_LEARNING_BLOCKED_BY_MISSING_HISTORY",
    "STATUS_LEARNING_HEALTHY",
    "STATUS_LEARNING_HEALTHY_LOW_SAMPLE",
    "STATUS_LEARNING_NEEDS_MORE_DATA",
    "aggregate_stats",
    "build_bucket_matrix",
    "build_learning_brain_report",
    "collect_no_bet_blockers",
    "normalize_history_frame",
    "recommend_bucket",
    "render_learning_brain_text",
    "report_paths_for_date",
    "write_learning_brain_report_outputs",
]
