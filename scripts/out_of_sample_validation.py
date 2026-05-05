"""Out-of-sample validation for player_points recalibration.

Splits historical graded picks by date:
- Train/tune period: earlier dates
- Test period: later dates

Reports:
- Hit rate
- ROI (if odds available)
- Selected volume
- Average edge
- Calibration error
- RMSE
- Over/under split
- Edge bucket split
- Player repeat-offender split
"""
from __future__ import annotations

import csv
import glob
import json
import math
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.projection.recalibration import (
    DEFAULT_CONFIG as DEFAULT_RECALIBRATION_CONFIG,
    RecalibrationMode,
    recalibrate_player_points,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRAIN_CUTOFF_DATE = "2026-04-27"  # Grades <= this date are training data
TEST_START_DATE = "2026-04-28"  # Grades >= this date are test data

OUTPUT_PATH = "outputs/runtime/diagnostics/out_of_sample_validation_report.json"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_graded_picks() -> list[dict[str, Any]]:
    """Load all graded picks from history files."""
    rows: list[dict[str, Any]] = []
    for path in glob.glob("outputs/runtime/history/graded_picks_*.csv"):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("market_type") == "player_points":
                    rows.append(row)
    return rows


def load_grading_results() -> list[dict[str, Any]]:
    """Load all grading results and join with graded picks."""
    results_by_id: dict[str, dict[str, Any]] = {}

    for path in glob.glob("outputs/runtime/research/grading_results_*.csv"):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("market_type") == "player_points":
                    pick_id = row.get("pick_id") or f"{row.get('player_name')}_{row.get('market_type')}_{row.get('sportsbook_line')}"
                    results_by_id[pick_id] = row

    return list(results_by_id.values())


def parse_result(row: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Parse result fields into booleans."""
    is_win = str(row.get("is_win", "")).strip() in {"True", "1", "true"}
    is_loss = str(row.get("is_loss", "")).strip() in {"True", "1", "true"}
    is_push = str(row.get("is_push", "")).strip() in {"True", "1", "true"}
    return is_win, is_loss, is_push


def get_game_date(row: dict[str, Any]) -> str:
    """Extract game date from row."""
    date = row.get("game_date") or row.get("prediction_date") or row.get("date") or ""
    return str(date).strip()[:10]  # YYYY-MM-DD


# ---------------------------------------------------------------------------
# Recalibration
# ---------------------------------------------------------------------------

def recalibrate(row: dict[str, Any]) -> dict[str, Any]:
    """Apply recalibration to a historical pick."""
    return recalibrate_player_points(
        row,
        config=DEFAULT_RECALIBRATION_CONFIG,
        requested_mode=RecalibrationMode.OFF,
    )


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute metrics for a set of graded picks."""
    wins = losses = pushes = 0
    total_roi = count_roi = 0.0
    proj_margins = []
    actual_margins = []

    for r in rows:
        is_win, is_loss, is_push = parse_result(r)
        if is_win:
            wins += 1
        elif is_loss:
            losses += 1
        elif is_push:
            pushes += 1

        # ROI if odds available
        odds = float(r.get("odds") or r.get("price") or 0)
        if odds > 0:
            count_roi += 1
            if is_win:
                total_roi += (odds - 100) / 100  # Assuming American odds
            elif is_loss:
                total_roi += -1.0

        # Margins for calibration
        line = float(r.get("sportsbook_line") or r.get("line") or 1)
        proj = float(r.get("model_projection") or r.get("projection") or 0)
        actual = float(r.get("actual_value") or 1)

        if r.get("selection") == "over":
            proj_margins.append(proj - line)
            actual_margins.append(actual - line)
        else:
            proj_margins.append(line - proj)
            actual_margins.append(line - actual)

    total = wins + losses + pushes
    if total == 0:
        return {"error": "no_data"}

    # Edge buckets
    edge_buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0})
    for r in rows:
        edge = abs(float(r.get("edge") or r.get("edge_pct") or 0))
        if edge >= 6.0:
            bucket = "6+"
        elif edge >= 3.0:
            bucket = "3_to_6"
        elif edge >= 1.0:
            bucket = "1_to_3"
        elif edge >= 0.0:
            bucket = "0_to_1"
        else:
            bucket = "negative"

        edge_buckets[bucket]["count"] += 1
        is_win, is_loss, _ = parse_result(r)
        if is_win:
            edge_buckets[bucket]["wins"] += 1
        elif is_loss:
            edge_buckets[bucket]["losses"] += 1

    for b in edge_buckets:
        c = edge_buckets[b]
        c["hit_rate"] = c["wins"] / (c["wins"] + c["losses"]) if (c["wins"] + c["losses"]) > 0 else 0.0

    # Calibration error
    cal_errors = [abs(p - a) for p, a in zip(proj_margins, actual_margins)]
    mean_cal_error = sum(cal_errors) / len(cal_errors) if cal_errors else 0.0
    rmse = math.sqrt(sum(e * e for e in cal_errors) / len(cal_errors)) if cal_errors else 0.0

    # Over/under split
    overs = [r for r in rows if str(r.get("selection", "")).strip().lower() == "over"]
    unders = [r for r in rows if str(r.get("selection", "")).strip().lower() == "under"]

    def side_metrics(side_rows: list[dict[str, Any]]) -> dict[str, Any]:
        s_wins = sum(1 for r in side_rows if parse_result(r)[0])
        s_losses = sum(1 for r in side_rows if parse_result(r)[1])
        s_pushes = sum(1 for r in side_rows if parse_result(r)[2])
        s_total = s_wins + s_losses + s_pushes
        return {
            "count": len(side_rows),
            "hit_rate": s_wins / (s_wins + s_losses) if (s_wins + s_losses) > 0 else 0.0,
        }

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": wins / (wins + losses) if (wins + losses) > 0 else 0.0,
        "avg_roi": total_roi / count_roi if count_roi > 0 else 0.0,
        "selected_volume": total,
        "avg_edge": sum(abs(float(r.get("edge") or r.get("edge_pct") or 0)) for r in rows) / len(rows) if rows else 0.0,
        "mean_calibration_error": mean_cal_error,
        "rmse": rmse,
        "edge_buckets": dict(edge_buckets),
        "overs": side_metrics(overs),
        "unders": side_metrics(unders),
    }


# ---------------------------------------------------------------------------
# Player repeat-offender analysis
# ---------------------------------------------------------------------------

def player_repeat_offender_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze which players consistently underperform."""
    by_player: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0, "pushes": 0})

    for r in rows:
        name = str(r.get("player_name", "")).strip()
        if not name:
            continue
        is_win, is_loss, is_push = parse_result(r)
        if is_win:
            by_player[name]["wins"] += 1
        elif is_loss:
            by_player[name]["losses"] += 1
        elif is_push:
            by_player[name]["pushes"] += 1

    # Find repeat offenders (>2 picks, <40% hit rate)
    repeat_offenders = []
    for name, counts in by_player.items():
        total = counts["wins"] + counts["losses"] + counts["pushes"]
        if total >= 2:
            hit_rate = counts["wins"] / (counts["wins"] + counts["losses"]) if (counts["wins"] + counts["losses"]) > 0 else 0.0
            if hit_rate < 0.40:
                repeat_offenders.append({
                    "player": name,
                    "total_picks": total,
                    "hit_rate": round(hit_rate, 3),
                    "wins": counts["wins"],
                    "losses": counts["losses"],
                })

    return {
        "total_players": len(by_player),
        "repeat_offenders": sorted(repeat_offenders, key=lambda x: x["hit_rate"])[:20],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading graded picks...")
    rows = load_grading_results()
    print(f"Loaded {len(rows)} graded player_points picks.")

    if not rows:
        print("No data found.")
        return

    # Split by date
    train_rows = [r for r in rows if get_game_date(r) < TRAIN_CUTOFF_DATE]
    test_rows = [r for r in rows if get_game_date(r) >= TEST_START_DATE]

    print(f"Train period (<{TRAIN_CUTOFF_DATE}): {len(train_rows)} picks")
    print(f"Test period (>={TEST_START_DATE}): {len(test_rows)} picks")

    if not test_rows:
        print("No test data available.")
        return

    # Original metrics (test period only)
    original_metrics = compute_metrics(test_rows)

    # Recalibrated metrics (test period)
    recalibrated_test = []
    for r in test_rows:
        recal = recalibrate(r)
        new_row = dict(r)
        new_row["model_projection"] = recal["recalibrated_projection"]
        new_row["edge"] = recal["recalibrated_edge"]
        if recal["recalibration_selected"]:
            recalibrated_test.append(new_row)

    recal_metrics = compute_metrics(recalibrated_test)

    # Player analysis on test period
    player_analysis = player_repeat_offender_analysis(test_rows)

    report = {
        "train_period": {"cutoff": TRAIN_CUTOFF_DATE, "count": len(train_rows)},
        "test_period": {"start": TEST_START_DATE, "count": len(test_rows)},
        "original": original_metrics,
        "recalibrated": recal_metrics,
        "recalibrated_volume_vs_original": len(recalibrated_test) / len(test_rows) if test_rows else 0.0,
        "player_analysis": player_analysis,
        "parameters": asdict(DEFAULT_RECALIBRATION_CONFIG),
    }

    Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {OUTPUT_PATH}")
    print(f"\nOriginal hit rate: {original_metrics.get('hit_rate', 0):.1%}")
    print(f"Recalibrated hit rate: {recal_metrics.get('hit_rate', 0):.1%}")
    print(f"Volume retained: {report['recalibrated_volume_vs_original']:.1%}")


if __name__ == "__main__":
    main()
