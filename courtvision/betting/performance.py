"""Performance analytics layer for CourtVision betting analysis.

Analyzes pick_history.csv to identify profitable patterns and eliminate losing ones.
Uses conservative statistical thresholds to ensure recommendations are production-safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from courtvision.calibration.buckets import abs_edge_bucket as _abs_edge_bucket
from courtvision.config import ROOT_DIR, DEFAULT_BANKROLL


# Minimum sample size for statistical significance
MIN_SAMPLE_SIZE = 20

# Confidence bucket thresholds (confidence stored as 0–1 decimal fraction)
CONFIDENCE_BUCKETS = [
    (0.55, 0.65, "0.55-0.65"),
    (0.65, 0.75, "0.65-0.75"),
    (0.75, 0.85, "0.75-0.85"),
    (0.85, 1.01, "0.85+"),  # 1.01 to include 0.85-1.0
]

# Edge bucket labels — thresholds live in abs_edge_bucket() in courtvision.calibration.buckets.
# pick_history.csv stores `edge` as raw absolute stat units (e.g. 1.5 = 1.5 points).
EDGE_BUCKET_LABELS = ["<1", "1-2", "2-3", "3-5", "5+"]


def normalize_pick_history_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize pick history schema to standard column names.

    Maps various column naming conventions to a standard schema:
    - date: prediction_date, game_date, created_at
    - market_type: market, prop_type, stat_type
    - result: result_status, outcome, grade_result, bet_result
    - stake: stake_fraction, wager_amount, bet_amount
    - profit_loss: pnl, net_profit, profit

    Also normalizes result values:
    - 'hit' -> 'win', 'miss' -> 'loss', 'push' -> 'push', 'pending' -> None

    Returns:
        DataFrame with normalized columns
    """
    if df.empty:
        return df

    df = df.copy()

    # Map date columns
    date_aliases = ["prediction_date", "game_date", "created_at", "timestamp", "date"]
    for alias in date_aliases:
        if alias in df.columns and "date" not in df.columns:
            df["date"] = df[alias]
            break

    # Map player name columns
    player_aliases = ["player_name", "player", "entity_name", "name"]
    for alias in player_aliases:
        if alias in df.columns and "player_name" not in df.columns:
            df["player_name"] = df[alias]
            break

    # Map market type columns
    market_aliases = ["market", "prop_type", "stat_type", "market_type"]
    for alias in market_aliases:
        if alias in df.columns and "market_type" not in df.columns:
            df["market_type"] = df[alias]
            break

    # Map selection columns
    selection_aliases = ["selection", "pick", "side", "over_under"]
    for alias in selection_aliases:
        if alias in df.columns and "selection" not in df.columns:
            df["selection"] = df[alias]
            break

    # Map result columns and normalize values
    result_aliases = ["result_status", "outcome", "grade_result", "bet_result", "result"]
    for alias in result_aliases:
        if alias in df.columns and "result" not in df.columns:
            df["result"] = df[alias]
            break

    # Normalize result values if result column exists
    if "result" in df.columns:
        result_mapping = {
            "hit": "win",
            "miss": "loss",
            "win": "win",
            "loss": "loss",
            "push": "push",
            "pending": None,
            "open": None,
            "": None,
        }
        df["result"] = df["result"].map(result_mapping).fillna(df["result"])
    else:
        df["result"] = None

    # Map stake columns
    stake_aliases = ["stake_fraction", "wager_amount", "bet_amount", "stake", "amount"]
    for alias in stake_aliases:
        if alias in df.columns and "stake" not in df.columns:
            if alias == "stake_fraction":
                df["stake"] = df[alias] * DEFAULT_BANKROLL
            else:
                df["stake"] = df[alias]
            break

    # If no stake, default to 1% of bankroll for analysis
    if "stake" not in df.columns:
        df["stake"] = DEFAULT_BANKROLL * 0.01

    # Map odds columns
    odds_aliases = ["odds", "decimal_odds", "price"]
    for alias in odds_aliases:
        if alias in df.columns and "odds" not in df.columns:
            df["odds"] = df[alias]
            break

    # Default odds to 1.91 (-110) if missing
    if "odds" not in df.columns:
        df["odds"] = 1.91

    # Map confidence columns
    confidence_aliases = ["confidence", "confidence_score", "conf"]
    for alias in confidence_aliases:
        if alias in df.columns and "confidence" not in df.columns:
            df["confidence"] = df[alias]
            break

    # Map edge columns
    edge_aliases = ["edge", "edge_pct", "expected_value", "ev"]
    for alias in edge_aliases:
        if alias in df.columns and "edge" not in df.columns:
            df["edge"] = df[alias]
            break

    # Calculate profit_loss if missing
    if "profit_loss" not in df.columns:
        df["profit_loss"] = df.apply(
            lambda row: _calculate_profit_loss(
                row.get("result"),
                row.get("stake", 0),
                row.get("odds", 1.91),
            ),
            axis=1,
        )

    # Ensure date is datetime
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    return df


def load_pick_history(path: str | Path | None = None) -> pd.DataFrame:
    """Load pick_history.csv safely with validation and schema normalization.

    Args:
        path: Optional path to pick_history.csv. Defaults to data/history/pick_history.csv

    Returns:
        DataFrame with normalized pick history or empty DataFrame if file not found/invalid
    """
    if path is None:
        path = ROOT_DIR / "data" / "history" / "pick_history.csv"
    else:
        path = Path(path)

    if not path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(path)

        if df.empty:
            print("Warning: pick_history.csv is empty")
            return pd.DataFrame()

        # Normalize schema (handles different column naming conventions)
        df = normalize_pick_history_schema(df)

        # Check for essential columns after normalization
        essential_cols = ["player_name"]  # Only truly required column
        missing_essential = [col for col in essential_cols if col not in df.columns]
        if missing_essential:
            print(f"Warning: Missing essential columns after normalization: {missing_essential}")
            print(f"Available columns: {list(df.columns)}")
            return pd.DataFrame()

        return df

    except Exception as e:
        print(f"Error loading pick_history: {e}")
        return pd.DataFrame()


def _calculate_profit_loss(result: str | None, stake: float, odds: float) -> float:
    """Calculate profit/loss for a single pick."""
    if result is None or pd.isna(result):
        return 0.0

    result = str(result).lower().strip()

    if result == "win":
        # Profit = stake * (odds - 1)
        return stake * (odds - 1) if odds > 1 else stake
    elif result == "loss":
        return -stake
    elif result == "push":
        return 0.0
    else:
        return 0.0


def compute_overall_roi(df: pd.DataFrame) -> dict[str, Any]:
    """Compute overall ROI across all picks.

    ROI = total profit_loss / total stake

    Returns:
        Dictionary with roi, profit_loss, total_stake, sample_size
    """
    if df.empty:
        return {"roi": 0.0, "profit_loss": 0.0, "total_stake": 0.0, "sample_size": 0}

    # Only consider graded picks (win/loss/push)
    graded = df[df["result"].isin(["win", "loss", "push"])].copy()

    if graded.empty:
        return {"roi": 0.0, "profit_loss": 0.0, "total_stake": 0.0, "sample_size": 0}

    total_stake = graded["stake"].sum() if "stake" in graded.columns else 0
    total_pl = graded["profit_loss"].sum() if "profit_loss" in graded.columns else 0

    roi = (total_pl / total_stake) if total_stake > 0 else 0.0

    return {
        "roi": round(roi, 4),
        "roi_percent": round(roi * 100, 2),
        "profit_loss": round(total_pl, 2),
        "total_stake": round(total_stake, 2),
        "sample_size": len(graded),
        "wins": int((graded["result"] == "win").sum()),
        "losses": int((graded["result"] == "loss").sum()),
        "pushes": int((graded["result"] == "push").sum()),
        "win_rate": round((graded["result"] == "win").sum() / len(graded), 4) if len(graded) > 0 else 0.0,
    }


def roi_by_market(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Compute ROI grouped by market_type.

    Returns:
        List of market segments with ROI, win_rate, sample_size
    """
    if df.empty or "market_type" not in df.columns:
        return []

    # Only graded picks
    graded = df[df["result"].isin(["win", "loss", "push"])].copy()

    if graded.empty:
        return []

    results = []
    for market_type, group in graded.groupby("market_type"):
        segment = _compute_segment_stats(group, str(market_type))
        results.append(segment)

    # Sort by ROI descending
    return sorted(results, key=lambda x: x["roi"], reverse=True)


def roi_by_prop_type(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Compute ROI by prop type (player_points, rebounds, assists, etc).

    Similar to market_type but with cleaner naming.
    """
    return roi_by_market(df)


def roi_by_confidence_bucket(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Compute ROI grouped by confidence buckets.

    Buckets:
    - 0.55-0.65: Lower confidence
    - 0.65-0.75: Medium confidence
    - 0.75-0.85: Higher confidence
    - 0.85+: Elite confidence

    Returns:
        List of confidence segments with ROI, win_rate, sample_size
    """
    if df.empty or "confidence" not in df.columns:
        return []

    # Only graded picks
    graded = df[df["result"].isin(["win", "loss", "push"])].copy()

    if graded.empty:
        return []

    results = []
    for low, high, label in CONFIDENCE_BUCKETS:
        mask = (graded["confidence"] >= low) & (graded["confidence"] < high)
        bucket_df = graded[mask]

        if len(bucket_df) == 0:
            continue

        segment = _compute_segment_stats(bucket_df, f"confidence_{label}")
        results.append(segment)

    # Sort by ROI descending
    return sorted(results, key=lambda x: x["roi"], reverse=True)


def roi_by_edge_bucket(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Compute ROI grouped by raw-absolute edge buckets.

    Buckets (stat-unit scale, e.g. points):
    - edge_<1:   abs_edge < 1.0
    - edge_1-2:  1.0 <= abs_edge < 2.0
    - edge_2-3:  2.0 <= abs_edge < 3.0
    - edge_3-5:  3.0 <= abs_edge < 5.0
    - edge_5+:   abs_edge >= 5.0

    Returns:
        List of edge segments with ROI, win_rate, sample_size
    """
    if df.empty or "edge" not in df.columns:
        return []

    graded = df[df["result"].isin(["win", "loss", "push"])].copy()
    if graded.empty:
        return []

    graded["_edge_bucket"] = graded["edge"].apply(_abs_edge_bucket)

    results = []
    for bucket_label, bucket_df in graded.groupby("_edge_bucket"):
        segment = _compute_segment_stats(bucket_df, f"edge_{bucket_label}")
        results.append(segment)

    return sorted(results, key=lambda x: x["roi"], reverse=True)


def worst_performing_segments(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return segments with negative ROI and sample_size >= MIN_SAMPLE_SIZE.

    These are candidates for elimination or further investigation.
    """
    all_segments = []

    # Collect all segment types
    all_segments.extend(roi_by_market(df))
    all_segments.extend(roi_by_confidence_bucket(df))
    all_segments.extend(roi_by_edge_bucket(df))

    # Filter for negative ROI and sufficient sample size
    worst = [
        seg for seg in all_segments
        if seg["roi"] < 0 and seg["sample_size"] >= MIN_SAMPLE_SIZE
    ]

    # Sort by ROI ascending (worst first)
    return sorted(worst, key=lambda x: x["roi"])


def best_performing_segments(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Return segments with highest ROI and sample_size >= MIN_SAMPLE_SIZE.

    These are patterns to double-down on.
    """
    all_segments = []

    # Collect all segment types
    all_segments.extend(roi_by_market(df))
    all_segments.extend(roi_by_confidence_bucket(df))
    all_segments.extend(roi_by_edge_bucket(df))

    # Filter for positive ROI and sufficient sample size
    best = [
        seg for seg in all_segments
        if seg["roi"] > 0 and seg["sample_size"] >= MIN_SAMPLE_SIZE
    ]

    # Sort by ROI descending (best first)
    return sorted(best, key=lambda x: x["roi"], reverse=True)


def _compute_segment_stats(group: pd.DataFrame, segment_name: str) -> dict[str, Any]:
    """Compute statistics for a segment (market, bucket, etc).

    Args:
        group: DataFrame subset for this segment
        segment_name: Name identifier for this segment

    Returns:
        Dictionary with segment statistics
    """
    total_stake = group["stake"].sum() if "stake" in group.columns else 0
    total_pl = group["profit_loss"].sum() if "profit_loss" in group.columns else 0

    sample_size = len(group)
    wins = int((group["result"] == "win").sum()) if "result" in group.columns else 0
    losses = int((group["result"] == "loss").sum()) if "result" in group.columns else 0
    pushes = int((group["result"] == "push").sum()) if "result" in group.columns else 0

    # Calculate win rate (excluding pushes)
    graded_count = wins + losses
    win_rate = wins / graded_count if graded_count > 0 else 0.0

    # Calculate ROI
    roi = (total_pl / total_stake) if total_stake > 0 else 0.0

    # Calculate average confidence if available
    avg_confidence = group["confidence"].mean() if "confidence" in group.columns else None

    # Calculate average edge if available
    avg_edge = group["edge"].mean() if "edge" in group.columns else None

    return {
        "segment": segment_name,
        "roi": round(roi, 4),
        "roi_percent": round(roi * 100, 2),
        "profit_loss": round(total_pl, 2),
        "total_stake": round(total_stake, 2),
        "sample_size": sample_size,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(win_rate, 4),
        "avg_confidence": round(avg_confidence, 4) if avg_confidence is not None else None,
        "avg_edge": round(avg_edge, 4) if avg_edge is not None else None,
        "statistically_significant": sample_size >= MIN_SAMPLE_SIZE,
    }


def generate_performance_report(df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Generate a comprehensive performance report.

    Args:
        df: Optional DataFrame. If None, loads from pick_history.csv

    Returns:
        Dictionary containing full performance analysis
    """
    if df is None:
        df = load_pick_history()

    if df.empty:
        return {
            "error": "No pick history data available",
            "overall": {},
            "by_market": [],
            "by_confidence": [],
            "by_edge": [],
            "worst_segments": [],
            "best_segments": [],
        }

    report = {
        "overall": compute_overall_roi(df),
        "by_market": roi_by_market(df),
        "by_prop_type": roi_by_prop_type(df),
        "by_confidence_bucket": roi_by_confidence_bucket(df),
        "by_edge_bucket": roi_by_edge_bucket(df),
        "worst_performing_segments": worst_performing_segments(df),
        "best_performing_segments": best_performing_segments(df),
        "recommendations": _generate_recommendations(df),
    }

    return report


def _generate_recommendations(df: pd.DataFrame) -> list[str]:
    """Generate actionable recommendations based on performance analysis."""
    recommendations = []

    overall = compute_overall_roi(df)
    worst = worst_performing_segments(df)
    best = best_performing_segments(df)

    # Overall performance check
    if overall["roi"] < -0.05:
        recommendations.append("CRITICAL: Overall ROI is significantly negative. Consider paper trading only.")
    elif overall["roi"] < 0:
        recommendations.append("WARNING: Overall ROI is negative. Review position sizing and filters.")
    elif overall["roi"] > 0.05:
        recommendations.append("POSITIVE: Overall ROI exceeds 5%. Current strategy is profitable.")

    # Worst segments elimination
    if worst:
        for seg in worst[:3]:  # Top 3 worst
            recommendations.append(
                f"ELIMINATE: {seg['segment']} has {seg['roi_percent']:.1f}% ROI "
                f"over {seg['sample_size']} picks"
            )

    # Best segments double-down
    if best:
        for seg in best[:3]:  # Top 3 best
            recommendations.append(
                f"DOUBLE-DOWN: {seg['segment']} has +{seg['roi_percent']:.1f}% ROI "
                f"over {seg['sample_size']} picks"
            )

    # Sample size warnings
    if overall["sample_size"] < MIN_SAMPLE_SIZE:
        recommendations.append(
            f"INSUFFICIENT DATA: Only {overall['sample_size']} graded picks. "
            f"Need at least {MIN_SAMPLE_SIZE} for statistical significance."
        )

    return recommendations


def save_performance_report(report: dict[str, Any], output_path: str | Path | None = None) -> Path:
    """Save performance report to JSON file.

    Args:
        report: Performance report dictionary
        output_path: Optional output path. Defaults to outputs/diagnostics/performance_report_<date>.json

    Returns:
        Path to saved file
    """
    if output_path is None:
        date_str = pd.Timestamp.now().strftime("%Y-%m-%d")
        output_dir = ROOT_DIR / "outputs" / "diagnostics"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"performance_report_{date_str}.json"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"Performance report saved to: {output_path}")
    return output_path


if __name__ == "__main__":
    # Quick test
    df = load_pick_history()
    report = generate_performance_report(df)
    save_performance_report(report)
    print(json.dumps(report, indent=2, default=str))
