from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from courtvision.context.game_context import is_identity_quarantined

INCUBATOR_FILE_PREFIX = "incubator_board"
INCUBATOR_LANE = "incubator"
INCUBATOR_STATUS_PAPER = "PAPER_ONLY"

MIN_INCUBATOR_EDGE = 5.0
MIN_INCUBATOR_CONFIDENCE = 0.75
MIN_INCUBATOR_QUALITY_SCORE = 60.0

TRUE_STRINGS = {"true", "1", "yes", "y"}
FALSE_STRINGS = {"false", "0", "no", "n"}

INCUBATOR_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player",
    "player_id",
    "team",
    "opponent",
    "market_type",
    "selection",
    "line",
    "odds",
    "edge",
    "confidence",
    "quality_score",
    "context_signal",
    "context_alignment",
    "context_caution_level",
    "source_rejection_reason",
    "source_identity_conflicted",
    "identity_resolution_category",
    "fragility_score",
    "role_stability_bucket",
    "same_opponent_warning",
    "manual_review_flag",
    "incubator_status",
    "incubator_reason",
    "real_money_eligible",
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


def _is_item_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in TRUE_STRINGS


def _is_item_falsey(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    text = _safe_text(value).lower()
    return bool(text) and text in FALSE_STRINGS


def _get_rejection_reason(row: pd.Series | Mapping[str, Any]) -> str:
    for column in (
        "final_elite_rejection_reason",
        "elite_rejection_reason",
        "rejection_reason",
        "selection_rejection_reason",
        "kelly_projected_skip_reason",
    ):
        val = _safe_text(row.get(column))
        if val:
            return val
    return ""


def _is_true_manual_hold(row: pd.Series | Mapping[str, Any]) -> bool:
    # A true manual hold is one that comes from manual action/policy, not automatically from the context gate.
    # Exclude if it has generic HOLD/HOLD_FOR_REVIEW that is NOT caused by the context_high_caution_over.
    skip_reason = _safe_text(row.get("kelly_projected_skip_reason"))
    rejection_reason = _get_rejection_reason(row)
    
    # Check if this row is blocked for other reasons
    stake_policy = _safe_text(row.get("stake_policy")).upper()
    manual_status = _safe_text(row.get("manual_status")).upper()
    
    is_hold = stake_policy in {"HOLD", "HOLD_FOR_REVIEW"} or manual_status == "HOLD"
    is_context_blocked = (
        skip_reason == "context_high_caution_over" or 
        rejection_reason == "elite_reject_context_high_caution_over"
    )
    
    if is_hold and not is_context_blocked:
        return True
    return False


def _is_excluded_security_or_manual_issue(row: pd.Series | Mapping[str, Any]) -> bool:
    # Exclude if it has manual/security/problem holds
    if _is_item_truthy(row.get("manual_review_required")) or _is_item_truthy(row.get("manual_review_flag")):
        return True
    if _is_item_truthy(row.get("review_before_bet")) or _safe_text(row.get("recommended_action")).upper() == "REVIEW_BEFORE_BET":
        return True
    if _is_item_truthy(row.get("same_opponent_under_warning")) or _is_item_truthy(row.get("same_opponent_warning")):
        return True
    if _is_item_truthy(row.get("source_identity_conflicted")) or _is_item_truthy(row.get("identity_team_conflict")):
        return True
    if _safe_text(row.get("identity_resolution_category")) == "true_identity_conflict":
        return True
    if is_identity_quarantined(row) is not None:
        return True
    if _is_true_manual_hold(row):
        return True
    return False


def _is_elite(row: pd.Series | Mapping[str, Any], elite_keys: set[tuple[str, str, str, str, str]]) -> bool:
    shared = (
        _safe_text(row.get("prediction_date")),
        _safe_text(row.get("market_type")).lower(),
        _safe_text(row.get("selection")).lower(),
    )
    for column in ("sportsbook_line", "line"):
        line_val = row.get(column)
        if _safe_text(line_val):
            line_str = _line_token(line_val)
            for col in ("player_id", "player_name", "entity_name"):
                player_val = _safe_text(row.get(col)).lower()
                if player_val and (shared[0], player_val, shared[1], shared[2], line_str) in elite_keys:
                    return True
    return False


def _line_token(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _elite_keys(elite_df: pd.DataFrame) -> set[tuple[str, str, str, str, str]]:
    keys = set()
    if elite_df.empty:
        return keys
    for _, row in elite_df.iterrows():
        pred_date = _safe_text(row.get("prediction_date"))
        market = _safe_text(row.get("market_type")).lower()
        selection = _safe_text(row.get("selection")).lower()
        line_val = ""
        for column in ("sportsbook_line", "line"):
            if _safe_text(row.get(column)):
                line_val = _line_token(row.get(column))
                break
        
        for col in ("player_id", "player_name", "entity_name"):
            player_val = _safe_text(row.get(col)).lower()
            if player_val:
                keys.add((pred_date, player_val, market, selection, line_val))
    return keys


def build_incubator_board(full_market_df: pd.DataFrame, elite_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build a paper-only incubator board from full market and elite candidates."""
    if not isinstance(full_market_df, pd.DataFrame) or full_market_df.empty:
        return pd.DataFrame(columns=INCUBATOR_COLUMNS)

    elite_source = elite_df if isinstance(elite_df, pd.DataFrame) else pd.DataFrame()
    elite_key_set = _elite_keys(elite_source)

    rows: list[dict[str, Any]] = []
    for _, row in full_market_df.iterrows():
        # Exclude manual/security/problem holds
        if _is_excluded_security_or_manual_issue(row):
            continue

        # Exclude if it is in elite
        if _is_elite(row, elite_key_set):
            continue

        # Check required filters
        rejection_reason = _get_rejection_reason(row)
        if rejection_reason != "elite_reject_context_high_caution_over":
            continue

        market_type = _safe_text(row.get("market_type")).lower()
        if market_type != "player_points":
            continue

        selection = _safe_text(row.get("selection")).lower()
        if selection != "over":
            continue

        edge = _safe_float(row.get("edge"))
        if edge is None or edge < MIN_INCUBATOR_EDGE:
            continue

        confidence = _safe_float(row.get("confidence"))
        if confidence is None or confidence < MIN_INCUBATOR_CONFIDENCE:
            continue

        quality_score = _safe_float(row.get("quality_score"))
        if quality_score is None or quality_score < MIN_INCUBATOR_QUALITY_SCORE:
            continue

        context_caution_level = _safe_text(row.get("context_caution_level")).lower()
        if context_caution_level != "high":
            continue

        recommended_action = _safe_text(row.get("recommended_action")).upper()
        if recommended_action == "BET":
            continue

        if _is_item_truthy(row.get("kelly_eligible")):
            continue

        # If it passes all strict filters, construct the incubator row
        player = _safe_text(row.get("player_name")) or _safe_text(row.get("player")) or _safe_text(row.get("entity_name")) or "Unknown"
        team = _safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))
        odds = _safe_float(row.get("odds")) or _safe_float(row.get("american_odds"))
        line = _safe_float(row.get("line")) or _safe_float(row.get("sportsbook_line"))
        
        context_signal = _safe_text(row.get("overall_context_signal")) or _safe_text(row.get("context_signal"))
        context_alignment = _safe_text(row.get("context_pick_alignment")) or _safe_text(row.get("context_alignment"))
        
        reason = (
            f"High-caution player_points over prop blocked by context safety but met strict incubator v1 thresholds: "
            f"edge={edge:.2f}>={MIN_INCUBATOR_EDGE:.1f}, "
            f"confidence={confidence:.3f}>={MIN_INCUBATOR_CONFIDENCE:.2f}, "
            f"quality_score={quality_score:.2f}>={MIN_INCUBATOR_QUALITY_SCORE:.1f}, "
            f"clean player identity"
        )
        
        incubator_row = {
            "prediction_date": _safe_text(row.get("prediction_date")),
            "player": player,
            "player_id": _safe_text(row.get("player_id")),
            "team": team,
            "opponent": _safe_text(row.get("opponent")),
            "market_type": "player_points",
            "selection": "over",
            "line": line,
            "odds": odds,
            "edge": edge,
            "confidence": confidence,
            "quality_score": quality_score,
            "context_signal": context_signal,
            "context_alignment": context_alignment,
            "context_caution_level": "high",
            "source_rejection_reason": rejection_reason,
            "source_identity_conflicted": False,
            "identity_resolution_category": _safe_text(row.get("identity_resolution_category")),
            "fragility_score": _safe_float(row.get("fragility_score")),
            "role_stability_bucket": _safe_text(row.get("role_stability_bucket")) or _safe_text(row.get("player_profile_bucket")),
            "same_opponent_warning": False,
            "manual_review_flag": False,
            "incubator_status": INCUBATOR_STATUS_PAPER,
            "incubator_reason": reason,
            "real_money_eligible": False,
        }
        rows.append(incubator_row)

    incubator_df = pd.DataFrame(rows, columns=INCUBATOR_COLUMNS)
    if not incubator_df.empty:
        # Sort descending by edge, then confidence, then quality
        incubator_df["_sort_edge"] = pd.to_numeric(incubator_df["edge"], errors="coerce")
        incubator_df["_sort_conf"] = pd.to_numeric(incubator_df["confidence"], errors="coerce")
        incubator_df["_sort_qual"] = pd.to_numeric(incubator_df["quality_score"], errors="coerce")
        
        incubator_df = incubator_df.sort_values(
            ["_sort_edge", "_sort_conf", "_sort_qual"],
            ascending=[False, False, False],
            na_position="last",
            kind="mergesort",
        ).drop(columns=["_sort_edge", "_sort_conf", "_sort_qual"])
        
    return incubator_df.reset_index(drop=True)


def read_incubator_board(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=INCUBATOR_COLUMNS)
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame(columns=INCUBATOR_COLUMNS)


def write_incubator_board(
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
        if full_path.exists() and full_path.stat().st_size > 0:
            try:
                full_market_source = pd.read_csv(full_path, keep_default_na=False, low_memory=False)
            except Exception:
                full_market_source = pd.DataFrame()
        else:
            full_market_source = pd.DataFrame()
        
    if isinstance(elite_df, pd.DataFrame):
        elite_source = elite_df
    else:
        elite_path = operator_dir / f"elite_board_{prediction_date}.csv"
        if elite_path.exists() and elite_path.stat().st_size > 0:
            try:
                elite_source = pd.read_csv(elite_path, keep_default_na=False, low_memory=False)
            except Exception:
                elite_source = pd.DataFrame()
        else:
            elite_source = pd.DataFrame()

    incubator = build_incubator_board(full_market_source, elite_source)
    output_path = runtime_root / "operator" / f"{INCUBATOR_FILE_PREFIX}_{prediction_date}.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    incubator.to_csv(output_path, index=False)
    return output_path, incubator
