from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.game_context import is_identity_quarantined
from courtvision.context.player_identity import SOURCE_IDENTITY_CONFLICT_COLUMNS


REVIEW_FILE_PREFIX = "near_elite_review"
REVIEW_LANE = "near_elite"
REVIEW_ONLY_ACTION = "REVIEW_ONLY"
NO_AUTO_STAKE_POLICY = "NO_AUTO_STAKE"
REVIEW_ONLY_NOTE = (
    "Near-Elite Review rows are manual-review candidates only. "
    "They are not Elite picks and are not Kelly/staking inputs."
)
REVIEW_REASON_PREFIX = "near_elite_player_points_over_met_review_thresholds_not_elite"

MIN_EDGE = 3.0
MIN_CONFIDENCE = 0.70
MIN_QUALITY_SCORE = 48.0

TRUE_STRINGS = {"true", "1", "yes", "y"}
FALSE_STRINGS = {"false", "0", "no", "n"}
HOLD_STAKE_POLICIES = {"HOLD", "HOLD_FOR_REVIEW"}
DO_NOT_BET_ACTION = "DO_NOT_BET_UNTIL_REVIEWED"

NEAR_ELITE_REVIEW_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player_id",
    "player_name",
    "team_abbr",
    "opponent",
    "game_id",
    "market_type",
    "selection",
    "line",
    "sportsbook_line",
    "model_projection",
    "edge",
    "confidence",
    "quality_score",
    "selection_score",
    "odds",
    "context_pick_alignment",
    "context_caution_level",
    "final_elite_rejection_reason",
    "elite_rejection_reason",
    "selection_rejection_reason",
    "kelly_projected_skip_reason",
    "identity_resolution_category",
    *SOURCE_IDENTITY_CONFLICT_COLUMNS,
    "operator_action",
    "stake_policy",
    "kelly_eligible",
    "review_lane",
    "review_reason",
)


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


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


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in TRUE_STRINGS


def _is_falsey(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    text = _safe_text(value).lower()
    return bool(text) and text in FALSE_STRINGS


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df.get(column, pd.Series(dtype=float, index=df.index)), errors="coerce")


def _text_series(df: pd.DataFrame, column: str) -> pd.Series:
    return df.get(column, pd.Series("", index=df.index)).fillna("").astype(str).str.strip()


def _line_value(row: pd.Series) -> Any:
    for column in ("sportsbook_line", "line"):
        value = row.get(column)
        if _safe_text(value):
            return value
    return ""


def _line_token(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _identity_tokens(row: pd.Series) -> list[str]:
    tokens: list[str] = []
    for column in ("player_id", "player_name", "entity_name"):
        token = _safe_text(row.get(column)).lower()
        if token and token not in tokens:
            tokens.append(token)
    return tokens or [""]


def _row_keys(row: pd.Series) -> set[tuple[str, str, str, str, str]]:
    shared = (
        _safe_text(row.get("prediction_date")),
        _safe_text(row.get("market_type")).lower(),
        _safe_text(row.get("selection")).lower(),
        _line_token(_line_value(row)),
    )
    return {
        (shared[0], identity, shared[1], shared[2], shared[3])
        for identity in _identity_tokens(row)
    }


def _elite_key_set(elite_df: pd.DataFrame) -> set[tuple[str, str, str, str, str]]:
    if not isinstance(elite_df, pd.DataFrame) or elite_df.empty:
        return set()
    keys: set[tuple[str, str, str, str, str]] = set()
    for _idx, row in elite_df.iterrows():
        keys.update(_row_keys(row))
    return keys


def _not_in_elite_mask(full_market_df: pd.DataFrame, elite_df: pd.DataFrame) -> pd.Series:
    elite_keys = _elite_key_set(elite_df)
    if not elite_keys:
        return pd.Series(True, index=full_market_df.index)
    return full_market_df.apply(lambda row: _row_keys(row).isdisjoint(elite_keys), axis=1)


def _blocked_mask(df: pd.DataFrame) -> pd.Series:
    mask = pd.Series(False, index=df.index)
    for column in ("identity_quarantined", "row_identity_quarantined"):
        if column in df.columns:
            mask = mask | df[column].map(_is_truthy)
    if "player_identity_valid" in df.columns:
        mask = mask | df["player_identity_valid"].map(_is_falsey)
    for column in ("review_before_bet", "manual_review_required", "same_opponent_under_warning"):
        if column in df.columns:
            mask = mask | df[column].map(_is_truthy)
    for column in ("operator_action", "recommended_action", "edge_containment_recommended_action"):
        if column in df.columns:
            actions = df[column].fillna("").astype(str).str.strip().str.upper()
            mask = mask | actions.eq(DO_NOT_BET_ACTION)
    for column in ("stake_policy", "edge_containment_stake_policy"):
        if column in df.columns:
            policies = df[column].fillna("").astype(str).str.strip().str.upper()
            mask = mask | policies.isin(HOLD_STAKE_POLICIES)

    if not df.empty:
        identity_gate = df.apply(lambda row: is_identity_quarantined(row) is not None, axis=1)
        mask = mask | identity_gate
    return mask


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    fallback_columns = {
        "team_abbr": "team",
        "line": "sportsbook_line",
        "model_projection": "projection",
        "odds": "american_odds",
    }
    for column, fallback in fallback_columns.items():
        if column not in prepared.columns and fallback in prepared.columns:
            prepared[column] = prepared[fallback]
    for column in NEAR_ELITE_REVIEW_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    return prepared


def _source_rejection_reason(row: pd.Series) -> str:
    for column in (
        "final_elite_rejection_reason",
        "elite_rejection_reason",
        "selection_rejection_reason",
        "kelly_projected_skip_reason",
        "rejection_reason",
    ):
        value = _safe_text(row.get(column))
        if value:
            return value
    return "not_selected_for_elite"


def _review_reason(row: pd.Series) -> str:
    return (
        f"{REVIEW_REASON_PREFIX}: edge={_safe_text(row.get('edge')) or 'n/a'}>={MIN_EDGE:.1f}, "
        f"confidence={_safe_text(row.get('confidence')) or 'n/a'}>={MIN_CONFIDENCE:.2f}, "
        f"quality_score={_safe_text(row.get('quality_score')) or 'n/a'}>={MIN_QUALITY_SCORE:.0f}; "
        f"source_rejection_reason={_source_rejection_reason(row)}"
    )


def _ordered_columns(df: pd.DataFrame) -> list[str]:
    return list(dict.fromkeys([*NEAR_ELITE_REVIEW_COLUMNS, *df.columns]))


def build_near_elite_review(full_market_df: pd.DataFrame, elite_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build a review-only lane from full-market rows that did not reach Elite.

    This function is intentionally reporting-only. It never mutates source frames,
    never promotes a row into Elite, and always forces the emitted lane to
    non-staking metadata.
    """

    if not isinstance(full_market_df, pd.DataFrame) or full_market_df.empty:
        return pd.DataFrame(columns=NEAR_ELITE_REVIEW_COLUMNS)

    elite_source = elite_df if isinstance(elite_df, pd.DataFrame) else pd.DataFrame()
    prepared = _prepare_frame(full_market_df)
    market = _text_series(prepared, "market_type").str.lower()
    selection = _text_series(prepared, "selection").str.lower()
    eligible_mask = (
        market.eq("player_points")
        & selection.eq("over")
        & _numeric_series(prepared, "edge").ge(MIN_EDGE)
        & _numeric_series(prepared, "confidence").ge(MIN_CONFIDENCE)
        & _numeric_series(prepared, "quality_score").ge(MIN_QUALITY_SCORE)
        & _not_in_elite_mask(prepared, elite_source)
        & ~_blocked_mask(prepared)
    )
    review = prepared.loc[eligible_mask].copy()
    if review.empty:
        return pd.DataFrame(columns=_ordered_columns(prepared))

    review["operator_action"] = REVIEW_ONLY_ACTION
    review["stake_policy"] = NO_AUTO_STAKE_POLICY
    review["kelly_eligible"] = False
    review["review_lane"] = REVIEW_LANE
    review["review_reason"] = review.apply(_review_reason, axis=1)

    review["_near_elite_sort_edge"] = pd.to_numeric(review["edge"], errors="coerce")
    review["_near_elite_sort_confidence"] = pd.to_numeric(review["confidence"], errors="coerce")
    review["_near_elite_sort_quality"] = pd.to_numeric(review["quality_score"], errors="coerce")
    review = review.sort_values(
        ["_near_elite_sort_edge", "_near_elite_sort_confidence", "_near_elite_sort_quality"],
        ascending=[False, False, False],
        na_position="last",
        kind="mergesort",
    ).drop(columns=["_near_elite_sort_edge", "_near_elite_sort_confidence", "_near_elite_sort_quality"])
    return review.loc[:, _ordered_columns(review)].reset_index(drop=True)


def _read_csv(path: Path, *, columns: tuple[str, ...] = NEAR_ELITE_REVIEW_COLUMNS) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)


def read_near_elite_review(path: Path) -> pd.DataFrame:
    return _read_csv(path)


def review_path_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"{REVIEW_FILE_PREFIX}_{prediction_date}.csv"


def write_near_elite_review(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    full_market_df: pd.DataFrame | None = None,
    elite_df: pd.DataFrame | None = None,
) -> tuple[Path, pd.DataFrame]:
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"
    if isinstance(full_market_df, pd.DataFrame):
        full_market_source = full_market_df
    else:
        full_path = operator_dir / f"full_market_board_{prediction_date}.csv"
        full_market_source = _read_csv(full_path) if full_path.exists() else pd.DataFrame()
    if isinstance(elite_df, pd.DataFrame):
        elite_source = elite_df
    else:
        elite_path = operator_dir / f"elite_board_{prediction_date}.csv"
        elite_source = _read_csv(elite_path) if elite_path.exists() else pd.DataFrame()

    review = build_near_elite_review(full_market_source, elite_source)
    output_path = review_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(output_path, index=False)
    return output_path, review


def near_elite_row_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    selection = _safe_text(row.get("selection")) or "n/a"
    line = _safe_text(row.get("line")) or _safe_text(row.get("sportsbook_line")) or "n/a"
    edge = _safe_text(row.get("edge")) or "n/a"
    confidence = _safe_text(row.get("confidence")) or "n/a"
    quality = _safe_text(row.get("quality_score")) or "n/a"
    reason = _safe_text(row.get("review_reason")) or "n/a"
    return (
        f"{player}: {market} {selection} {line} "
        f"(edge={edge}, confidence={confidence}, quality={quality}, reason={reason})"
    )


__all__ = [
    "MIN_CONFIDENCE",
    "MIN_EDGE",
    "MIN_QUALITY_SCORE",
    "NEAR_ELITE_REVIEW_COLUMNS",
    "NO_AUTO_STAKE_POLICY",
    "REVIEW_LANE",
    "REVIEW_ONLY_ACTION",
    "REVIEW_ONLY_NOTE",
    "build_near_elite_review",
    "near_elite_row_line",
    "read_near_elite_review",
    "review_path_for_date",
    "write_near_elite_review",
]
