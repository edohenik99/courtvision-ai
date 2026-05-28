from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.reporting.incubator_board import (
    INCUBATOR_COLUMNS,
    INCUBATOR_STATUS_PAPER,
)

MIN_SAMPLE_SIZE = 20

INCUBATOR_HISTORY_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "game_date",
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
    "context_caution_level",
    "source_rejection_reason",
    "incubator_status",
    "real_money_eligible",
    "result_status",
    "actual_value",
    "closing_line",
    "clv",
    "graded_at",
    "grading_status",
    "grading_reason",
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
    return default if text.lower() in {"nan", "none", "null"} else text


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = -110) -> int:
    try:
        num = _safe_float(value, default=float("nan"))
        if math.isnan(num):
            return default
        return int(num)
    except Exception:
        return default


def _odds_profit_factor(odds: Any) -> float:
    val = _safe_float(odds, default=float("nan"))
    if math.isnan(val) or abs(val) < 1.0:
        return 0.909  # default -110 odds payout
    if val > 0:
        return val / 100.0
    else:
        return 100.0 / abs(val)


def _confidence_bucket(conf: float) -> str:
    if conf >= 0.85:
        return "0.85+"
    if conf >= 0.75:
        return "0.75-0.85"
    if conf >= 0.65:
        return "0.65-0.75"
    return "0.55-0.65"


def _edge_bucket(edge: float) -> str:
    edge_abs = abs(edge)
    if edge_abs >= 5.0:
        return "5+"
    if edge_abs >= 3.0:
        return "3-5"
    if edge_abs >= 2.0:
        return "2-3"
    if edge_abs >= 1.0:
        return "1-2"
    return "<1"


def persist_daily_incubator_board(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    result_status: str = "pending",
    dry_run: bool = False,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    incubator_board_path = runtime_root_path / "operator" / f"incubator_board_{prediction_date}.csv"
    incubator_history_path = history_root_path / "incubator_history.csv"

    if not incubator_board_path.exists():
        return {
            "incubator_board_path": str(incubator_board_path),
            "incubator_history_path": str(incubator_history_path),
            "appended_rows": 0,
            "total_rows": 0,
            "dry_run": bool(dry_run),
            "note": "Incubator board file does not exist for today.",
        }

    try:
        incubator_df = pd.read_csv(incubator_board_path, keep_default_na=False)
    except Exception:
        incubator_df = pd.DataFrame()

    if incubator_df.empty:
        return {
            "incubator_board_path": str(incubator_board_path),
            "incubator_history_path": str(incubator_history_path),
            "appended_rows": 0,
            "total_rows": 0,
            "dry_run": bool(dry_run),
            "note": "Incubator board is empty.",
        }

    normalized_rows: list[dict[str, Any]] = []
    for _, row in incubator_df.iterrows():
        # Ensure we construct the standard history row
        line_val = _safe_float(row.get("line"))
        odds_val = _safe_int(row.get("odds"))
        edge_val = _safe_float(row.get("edge"))
        conf_val = _safe_float(row.get("confidence"))
        qual_val = _safe_float(row.get("quality_score"))
        frag_val = _safe_float(row.get("fragility_score"), default=0.0)

        history_row = {
            "prediction_date": _safe_text(row.get("prediction_date"), default=prediction_date),
            "game_date": _safe_text(row.get("prediction_date"), default=prediction_date),  # Default to pred date
            "player": _safe_text(row.get("player")),
            "player_id": _safe_text(row.get("player_id")),
            "team": _safe_text(row.get("team")),
            "opponent": _safe_text(row.get("opponent")),
            "market_type": _safe_text(row.get("market_type"), default="player_points"),
            "selection": _safe_text(row.get("selection"), default="over"),
            "line": line_val,
            "odds": odds_val,
            "edge": edge_val,
            "confidence": conf_val,
            "quality_score": qual_val,
            "context_caution_level": _safe_text(row.get("context_caution_level"), default="high"),
            "source_rejection_reason": _safe_text(row.get("source_rejection_reason")),
            "incubator_status": INCUBATOR_STATUS_PAPER,
            "real_money_eligible": False,
            "result_status": "pending",
            "actual_value": "",
            "closing_line": "",
            "clv": "",
            "graded_at": "",
            "grading_status": "open_game_pending",
            "grading_reason": "game_not_final",
        }
        normalized_rows.append(history_row)

    if not incubator_history_path.exists():
        incubator_history_path.parent.mkdir(parents=True, exist_ok=True)
        existing = pd.DataFrame(columns=INCUBATOR_HISTORY_COLUMNS)
    else:
        try:
            existing = pd.read_csv(incubator_history_path, keep_default_na=False)
        except Exception:
            existing = pd.DataFrame(columns=INCUBATOR_HISTORY_COLUMNS)

    incoming = pd.DataFrame(normalized_rows, columns=INCUBATOR_HISTORY_COLUMNS)
    
    # Restore graded fields from existing history so pending re-runs don't overwrite graded outcomes
    if not existing.empty:
        # Build lookup key
        existing_lookup = {}
        for _, r in existing.iterrows():
            key = (
                _safe_text(r.get("prediction_date")),
                _safe_text(r.get("player_id")),
                _safe_text(r.get("market_type")),
                _safe_text(r.get("selection")),
                f"{_safe_float(r.get('line')):.3f}".rstrip("0").rstrip("."),
            )
            existing_lookup[key] = r
            
        for idx, r in incoming.iterrows():
            key = (
                _safe_text(r.get("prediction_date")),
                _safe_text(r.get("player_id")),
                _safe_text(r.get("market_type")),
                _safe_text(r.get("selection")),
                f"{_safe_float(r.get('line')):.3f}".rstrip("0").rstrip("."),
            )
            if key in existing_lookup:
                existing_row = existing_lookup[key]
                # Preserve result_status, actual_value, graded_at, grading_status, grading_reason, game_date, closing_line, clv
                for col in ("result_status", "actual_value", "graded_at", "grading_status", "grading_reason", "game_date", "closing_line", "clv"):
                    incoming.at[idx, col] = existing_row.get(col)

    combined = incoming.copy() if existing.empty else pd.concat([existing, incoming], ignore_index=True)
    dedupe_keys = ["prediction_date", "player_id", "market_type", "selection", "line"]
    
    # Standardize line float comparison
    combined["_line_standardized"] = combined["line"].apply(lambda l: f"{_safe_float(l):.3f}".rstrip("0").rstrip("."))
    combined = combined.drop_duplicates(
        subset=["prediction_date", "player_id", "market_type", "selection", "_line_standardized"],
        keep="last",
    ).drop(columns=["_line_standardized"])

    combined = combined[list(INCUBATOR_HISTORY_COLUMNS)].sort_values(["prediction_date", "player", "market_type"]).reset_index(drop=True)
    
    if not dry_run:
        combined.to_csv(incubator_history_path, index=False)

    return {
        "incubator_board_path": str(incubator_board_path),
        "incubator_history_path": str(incubator_history_path),
        "appended_rows": len(incoming),
        "total_rows": len(combined),
        "dry_run": bool(dry_run),
    }


def grade_incubator_picks(
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    prediction_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    incubator_history_path = history_root_path / "incubator_history.csv"

    if not incubator_history_path.exists():
        return {
            "updated_rows": 0,
            "pending_rows": 0,
            "dry_run": bool(dry_run),
            "note": "incubator_history.csv does not exist.",
        }

    try:
        history_df = pd.read_csv(incubator_history_path, keep_default_na=False)
    except Exception:
        history_df = pd.DataFrame(columns=INCUBATOR_HISTORY_COLUMNS)

    if history_df.empty:
        return {
            "updated_rows": 0,
            "pending_rows": 0,
            "dry_run": bool(dry_run),
            "note": "incubator_history.csv is empty.",
        }

    # Ensure all column schemas are standardized
    history_df = history_df.reindex(columns=INCUBATOR_HISTORY_COLUMNS)
    for col in ("actual_value", "grading_reason", "graded_at", "grading_status"):
        history_df[col] = history_df[col].astype("object").fillna("")

    # Identify pending rows
    pending_mask = history_df["result_status"].astype(str).str.lower().isin({"pending", "open_game_pending"})
    if prediction_date:
        pending_mask &= history_df["prediction_date"].astype(str) == str(prediction_date)

    if not pending_mask.any():
        return {
            "updated_rows": 0,
            "pending_rows": int(history_df["result_status"].astype(str).str.lower().isin({"pending", "open_game_pending"}).sum()),
            "dry_run": bool(dry_run),
        }

    from scripts.history_tracking import (
        _load_actual_results_for_date,
        _load_player_stats_for_date,
        _load_games_for_date,
        _grade_pick_row,
    )

    updated = 0
    unique_dates = history_df.loc[pending_mask, "prediction_date"].astype(str).unique()
    for date in unique_dates:
        actual_df = _load_actual_results_for_date(date, runtime_root=runtime_root_path)
        stats_df = _load_player_stats_for_date(date, runtime_root=runtime_root_path)
        games_df = _load_games_for_date(date, runtime_root=runtime_root_path)

        date_mask = (history_df["prediction_date"].astype(str) == date) & pending_mask
        date_rows = history_df[date_mask].copy()

        for idx, row in date_rows.iterrows():
            # Map incubator history columns to scripts/history_tracking expected keys
            temp_row = row.copy()
            temp_row["market"] = row.get("market_type")
            temp_row["player_name"] = row.get("player")
            # We don't have game_id in incubator, but we can search for it in stats/games or leave empty and let it match by team/opponent/date fallback
            
            result_status, actual_value, skip_reason = _grade_pick_row(
                temp_row,
                actual_df=actual_df,
                stats_df=stats_df,
                games_df=games_df,
            )

            if result_status in {"hit", "miss", "push"}:
                updated += 1
                date_rows.at[idx, "result_status"] = result_status
                date_rows.at[idx, "actual_value"] = actual_value if actual_value is not None else ""
                date_rows.at[idx, "graded_at"] = datetime.now(timezone.utc).isoformat()
                date_rows.at[idx, "grading_status"] = "graded"
                date_rows.at[idx, "grading_reason"] = ""
            else:
                date_rows.at[idx, "result_status"] = "pending"
                date_rows.at[idx, "grading_status"] = "open_game_pending"
                date_rows.at[idx, "grading_reason"] = "game_not_final"

        history_df.loc[date_rows.index, "result_status"] = date_rows["result_status"]
        history_df.loc[date_rows.index, "actual_value"] = date_rows["actual_value"]
        history_df.loc[date_rows.index, "graded_at"] = date_rows["graded_at"]
        history_df.loc[date_rows.index, "grading_status"] = date_rows["grading_status"]
        history_df.loc[date_rows.index, "grading_reason"] = date_rows["grading_reason"]

    if not dry_run:
        history_df.to_csv(incubator_history_path, index=False)

    pending_rows = int(history_df["result_status"].astype(str).str.lower().isin({"pending", "open_game_pending"}).sum())
    return {
        "updated_rows": updated,
        "pending_rows": pending_rows,
        "dry_run": bool(dry_run),
    }


def _aggregate_segment_stats(segment_df: pd.DataFrame, label: str) -> dict[str, Any]:
    total_picks = len(segment_df)
    graded_df = segment_df[segment_df["result_status"].astype(str).str.lower().isin({"hit", "miss", "push"})].copy()
    graded_count = len(graded_df)
    pending_count = total_picks - graded_count

    wins = int((graded_df["result_status"].astype(str).str.lower() == "hit").sum())
    losses = int((graded_df["result_status"].astype(str).str.lower() == "miss").sum())
    pushes = int((graded_df["result_status"].astype(str).str.lower() == "push").sum())

    win_rate = float(wins / graded_count) if graded_count > 0 else 0.0

    #ROI using American odds with flat 1 unit stake
    total_stake = 0.0
    total_pl = 0.0
    for _, row in graded_df.iterrows():
        total_stake += 1.0
        res = _safe_text(row.get("result_status")).lower()
        if res == "hit":
            total_pl += _odds_profit_factor(row.get("odds"))
        elif res == "miss":
            total_pl += -1.0

    roi = float(total_pl / total_stake) if total_stake > 0.0 else 0.0

    return {
        "segment": label,
        "total_picks": total_picks,
        "graded_count": graded_count,
        "pending_count": pending_count,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(win_rate, 4),
        "total_stake": round(total_stake, 2),
        "profit_loss": round(total_pl, 4),
        "roi": round(roi, 4),
        "roi_percent": round(roi * 100, 2),
        "statistically_significant": graded_count >= MIN_SAMPLE_SIZE,
    }


def generate_incubator_performance_report(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "overall": {
                "segment": "Overall",
                "total_picks": 0,
                "graded_count": 0,
                "pending_count": 0,
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "win_rate": 0.0,
                "total_stake": 0.0,
                "profit_loss": 0.0,
                "roi": 0.0,
                "roi_percent": 0.0,
                "statistically_significant": False,
            },
            "by_market": [],
            "by_caution": [],
            "by_rejection": [],
            "by_confidence": [],
            "by_edge": [],
        }

    # Overall graded stats
    overall = _aggregate_segment_stats(df, "Overall")

    # By market_type
    by_market = []
    for val, group in df.groupby("market_type"):
        by_market.append(_aggregate_segment_stats(group, str(val)))

    # By context_caution_level
    by_caution = []
    for val, group in df.groupby("context_caution_level"):
        by_caution.append(_aggregate_segment_stats(group, str(val)))

    # By source_rejection_reason
    by_rejection = []
    for val, group in df.groupby("source_rejection_reason"):
        by_rejection.append(_aggregate_segment_stats(group, str(val)))

    # By confidence bucket
    by_confidence = []
    df_copy = df.copy()
    df_copy["_conf_bucket"] = df_copy["confidence"].apply(lambda c: _confidence_bucket(_safe_float(c)))
    for val, group in df_copy.groupby("_conf_bucket"):
        by_confidence.append(_aggregate_segment_stats(group, str(val)))

    # By edge bucket
    by_edge = []
    df_copy["_edge_bucket"] = df_copy["edge"].apply(lambda e: _edge_bucket(_safe_float(e)))
    for val, group in df_copy.groupby("_edge_bucket"):
        by_edge.append(_aggregate_segment_stats(group, str(val)))

    return {
        "overall": overall,
        "by_market": by_market,
        "by_caution": by_caution,
        "by_rejection": by_rejection,
        "by_confidence": by_confidence,
        "by_edge": by_edge,
    }


def write_incubator_performance_report(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    dry_run: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    
    incubator_history_path = history_root_path / "incubator_history.csv"
    if incubator_history_path.exists():
        try:
            history_df = pd.read_csv(incubator_history_path, keep_default_na=False)
        except Exception:
            history_df = pd.DataFrame(columns=INCUBATOR_HISTORY_COLUMNS)
    else:
        history_df = pd.DataFrame(columns=INCUBATOR_HISTORY_COLUMNS)

    # Calculate metrics
    report = generate_incubator_performance_report(history_df)

    # Output paths
    txt_path = runtime_root_path / "operator" / f"incubator_performance_report_{prediction_date}.txt"
    json_path = runtime_root_path / "diagnostics" / f"incubator_performance_report_{prediction_date}.json"
    csv_path = runtime_root_path / "operator" / f"incubator_performance_report_{prediction_date}.csv"

    if dry_run:
        return txt_path, json_path, csv_path, report

    # 1. Write JSON diagnostics
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # 2. Write CSV report (flattened segments)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_rows = []
    
    # Helper to add section rows to CSV
    def add_section_rows(section_list: list[dict[str, Any]], dimension_name: str):
        for item in section_list:
            csv_rows.append({
                "dimension": dimension_name,
                "segment": item["segment"],
                "total_picks": item["total_picks"],
                "graded_count": item["graded_count"],
                "pending_count": item["pending_count"],
                "wins": item["wins"],
                "losses": item["losses"],
                "pushes": item["pushes"],
                "win_rate": item["win_rate"],
                "roi_percent": item["roi_percent"],
                "statistically_significant": item["statistically_significant"],
            })

    # Add overall
    overall_item = report["overall"]
    csv_rows.append({
        "dimension": "overall",
        "segment": "Overall",
        "total_picks": overall_item["total_picks"],
        "graded_count": overall_item["graded_count"],
        "pending_count": overall_item["pending_count"],
        "wins": overall_item["wins"],
        "losses": overall_item["losses"],
        "pushes": overall_item["pushes"],
        "win_rate": overall_item["win_rate"],
        "roi_percent": overall_item["roi_percent"],
        "statistically_significant": overall_item["statistically_significant"],
    })

    # Add other segments
    add_section_rows(report["by_market"], "market_type")
    add_section_rows(report["by_caution"], "context_caution_level")
    add_section_rows(report["by_rejection"], "source_rejection_reason")
    add_section_rows(report["by_confidence"], "confidence_bucket")
    add_section_rows(report["by_edge"], "edge_bucket")

    csv_fields = [
        "dimension", "segment", "total_picks", "graded_count", "pending_count",
        "wins", "losses", "pushes", "win_rate", "roi_percent", "statistically_significant"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(csv_rows)

    # 3. Write TXT report
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("=" * 60)
    lines.append(f"COURTVISION INCUBATOR PERFORMANCE REPORT - {prediction_date}")
    lines.append("=" * 60)
    lines.append("STATUS: paper-only, not staking input")
    lines.append("")

    overall = report["overall"]
    lines.append("Overall Performance")
    lines.append("-" * 40)
    lines.append(f"- total picks count: {overall['total_picks']}")
    lines.append(f"- graded picks count: {overall['graded_count']}")
    lines.append(f"- pending picks count: {overall['pending_count']}")
    lines.append(f"- wins/losses/pushes: {overall['wins']}/{overall['losses']}/{overall['pushes']}")
    lines.append(f"- hit rate: {overall['win_rate']:.2%}")
    lines.append(f"- flat ROI using offered odds: {overall['roi_percent']:.2f}%")
    if not overall["statistically_significant"]:
        lines.append("  (WARNING: Sample size is too small (< 20) for statistical significance.)")
    lines.append("")

    def append_segment_text(section_list: list[dict[str, Any]], title: str):
        lines.append(title)
        lines.append("-" * 40)
        if not section_list:
            lines.append("  - None")
        else:
            for item in section_list:
                sig_warn = " (low sample)" if not item["statistically_significant"] else ""
                lines.append(
                    f"  - {item['segment']}: total={item['total_picks']}, graded={item['graded_count']}, "
                    f"win_rate={item['win_rate']:.2%}, ROI={item['roi_percent']:.2f}%{sig_warn}"
                )
        lines.append("")

    append_segment_text(report["by_market"], "Performance by Market Type")
    append_segment_text(report["by_caution"], "Performance by Context Caution Level")
    append_segment_text(report["by_rejection"], "Performance by Source Rejection Reason")
    append_segment_text(report["by_confidence"], "Performance by Confidence Bucket")
    append_segment_text(report["by_edge"], "Performance by Edge Bucket")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return txt_path, json_path, csv_path, report
