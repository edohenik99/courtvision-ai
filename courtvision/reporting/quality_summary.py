from __future__ import annotations

import csv
import json
import math
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from courtvision.reporting.high_caution_over_watchlist import (
    OBSERVATION_ONLY_NOTE,
    build_high_caution_over_watchlist,
    watchlist_path_for_date,
    write_high_caution_over_watchlist,
)

ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER = "elite_reject_context_high_caution_over"
CONTEXT_CONFLICT_CAUSE_BUCKETS: tuple[str, ...] = (
    "playoff_only",
    "defense_driven",
    "pace_driven",
    "pace_defense_combined",
    "playoff_defense_combined",
    "stale_team_not_in_game",
)

PREDICTION_ARTIFACT_NAMES: tuple[str, ...] = (
    "elite_board",
    "full_market_board",
    "high_caution_over_watchlist",
    "sgp_board",
    "elite_pipeline_audit",
    "elite_pipeline_audit_summary",
    "player_predictions",
    "game_predictions",
    "player_edges",
    "game_edges",
    "model_metrics",
    "market_coverage",
    "board_diagnostics",
    "player_points_elite_admission",
    "market_availability_audit",
    "market_performance_readiness",
    "manual_context",
    "game_context",
    "injury_context_diagnostics",
    "injury_context_report",
    "top_plays_report",
    "elite_decision_report",
    "top_player_edges",
    "top_game_edges",
    "stat_only_board",
    "strike_board",
    "predictive_lines_board",
    "team_board",
    "near_miss_board",
)

QUALITY_HISTORY_CSV_NAME = "quality_history.csv"
QUALITY_HISTORY_JSONL_NAME = "quality_history.jsonl"
QUALITY_HISTORY_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "generated_at",
    "commit_hash",
    "data_mode",
    "games_count",
    "players_count",
    "player_predictions_rows",
    "baseline_rows",
    "unique_baseline_players",
    "active_team_baseline_players",
    "odds_unique_players",
    "odds_baseline_matched_players",
    "full_market_unique_players",
    "elite_unique_players",
    "kelly_unique_players",
    "raw_odds_rows",
    "normalized_odds_rows",
    "live_odds_count",
    "synthetic_fallback_odds_count",
    "raw_candidates_count",
    "rejected_candidates_count",
    "full_market_board_count",
    "elite_board_count",
    "sgp_board_count",
    "kelly_rows_count",
    "kelly_eligible_count",
    "kelly_skipped_count",
    "high_caution_over_skip_count",
    "medium_neutral_over_dampened_count",
    "total_stake",
    "total_expected_value",
    "max_player_exposure",
    "max_team_exposure",
    "max_game_exposure",
    "markets_seen",
    "points_normalized_odds_rows",
    "points_full_market_board_count",
    "points_elite_board_count",
    "points_kelly_eligible_count",
    "non_points_rejected_count",
    "low_coverage_warning_count",
    "run_health_status",
    "run_health_reason",
    "run_health_flags",
    "run_health_recommendation",
    "date_isolation_status",
    "warnings_count",
)

MARKET_COLUMN_CANDIDATES: tuple[str, ...] = ("market", "market_type", "prop_type")
POINTS_MARKET_NAMES: set[str] = {"points", "player_points"}
DEFAULT_MIN_LIVE_CANDIDATES = 10
DEFAULT_MIN_KELLY_ELIGIBLE = 2
DEFAULT_MIN_ODDS_PLAYERS_PER_GAME_RATIO = 3
DEFAULT_MIN_FULL_MARKET_PLAYERS_PER_GAME_RATIO = 3
DEFAULT_MIN_ACTIVE_TEAM_BASELINE_PLAYERS_PER_GAME_RATIO = 5
RUN_HEALTH_CONTEXT_BLOCKED_RATIO = 0.5
RUN_HEALTH_LOW_VOLUME_KELLY_ELIGIBLE = 2
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
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return None if number is None else int(number)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _read_csv(
    path: Path,
    warnings: list[str],
    *,
    optional: bool = True,
    missing_warning: str | None = None,
    empty_warning: str | None = None,
) -> pd.DataFrame:
    if not path.exists():
        warnings.append(missing_warning or (f"Missing optional artifact: {path}" if optional else f"Missing artifact: {path}"))
        return pd.DataFrame()
    if path.stat().st_size == 0:
        warnings.append(empty_warning or f"Empty CSV artifact: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        warnings.append(empty_warning or f"Empty CSV artifact: {path}")
        return pd.DataFrame()
    except Exception as exc:
        warnings.append(f"Could not read CSV {path}: {exc}")
        return pd.DataFrame()


def _read_json(path: Path, warnings: list[str], *, optional: bool = True) -> dict[str, Any]:
    if not path.exists():
        if optional:
            warnings.append(f"Missing optional artifact: {path}")
        else:
            warnings.append(f"Missing artifact: {path}")
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read JSON {path}: {exc}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _git_commit_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except Exception:
        return "unknown"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "unknown"


def _count_rows(df: pd.DataFrame) -> int:
    return 0 if not isinstance(df, pd.DataFrame) or df.empty else int(len(df))


def _nonempty_text_series(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().replace("", pd.NA).dropna()


def _unique_player_count(df: pd.DataFrame) -> int:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return 0
    for column in ("player_id", "player_name", "entity_name"):
        if column in df.columns:
            values = _nonempty_text_series(df[column])
            if not values.empty:
                return int(values.nunique())
    return 0


def _player_identity_values(df: pd.DataFrame, baseline_df: pd.DataFrame | None = None) -> tuple[set[str], str]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return set(), ""
    columns = ("player_id", "player_name", "entity_name")
    if isinstance(baseline_df, pd.DataFrame) and not baseline_df.empty:
        for column in columns:
            if column in df.columns and column in baseline_df.columns:
                return set(_nonempty_text_series(df[column]).astype(str)), column
    for column in columns:
        if column in df.columns:
            return set(_nonempty_text_series(df[column]).astype(str)), column
    return set(), ""


def _active_team_values(*frames: pd.DataFrame) -> set[str]:
    teams: set[str] = set()
    for frame in frames:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        for column in ("team_abbr", "team"):
            if column in frame.columns:
                teams.update(_nonempty_text_series(frame[column]).astype(str).str.upper().tolist())
    return teams


def _numeric_sum(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum())


def _value_counts(df: pd.DataFrame, column: str) -> dict[str, int]:
    if df.empty or column not in df.columns:
        return {}
    series = df[column].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    return {str(k): int(v) for k, v in series.value_counts().sort_index().items()}


def _first_present(mapping: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _infer_run_data_mode(summary: dict[str, Any], frames: Sequence[pd.DataFrame]) -> str:
    pipeline_mode = _safe_text(summary.get("pipeline_mode")).lower()
    quality_status = _safe_text(summary.get("market_quality_status")).lower()
    if "fixture" in pipeline_mode:
        return "fixture"
    for frame in frames:
        if frame.empty:
            continue
        for column in ("line_source", "source_lane", "bookmaker", "vendor"):
            if column in frame.columns:
                values = " ".join(frame[column].fillna("").astype(str).str.lower().tolist())
                if "fixture" in values:
                    return "fixture"
        if "is_live_market" in frame.columns and frame["is_live_market"].map(_is_truthy).any():
            return "live"
    if quality_status == "live":
        return "live"
    return "unknown"


def _normalized_date_tokens(path: Path) -> list[str]:
    import re

    return re.findall(r"\d{4}-\d{2}-\d{2}", str(path))


def _prediction_artifact_paths(runtime_root: Path, prediction_date: str) -> list[Path]:
    lanes = [
        runtime_root / "operator",
        runtime_root / "diagnostics",
        runtime_root / "research",
        runtime_root / "optional",
    ]
    paths: list[Path] = []
    for lane in lanes:
        for name in PREDICTION_ARTIFACT_NAMES:
            for suffix in ("csv", "json", "txt"):
                path = lane / f"{name}_{prediction_date}.{suffix}"
                if path.exists():
                    paths.append(path)
    return sorted(set(paths), key=lambda p: str(p))


def _date_isolation_summary(
    *,
    prediction_date: str,
    artifact_paths: Iterable[Path],
) -> dict[str, Any]:
    artifacts: list[str] = []
    mismatches: list[dict[str, str]] = []
    for raw_path in artifact_paths:
        path = Path(raw_path)
        artifacts.append(str(path))
        dates = _normalized_date_tokens(path)
        mismatched = sorted({date for date in dates if date != prediction_date})
        for artifact_date in mismatched:
            mismatches.append(
                {
                    "artifact_date": artifact_date,
                    "path": str(path),
                }
            )
    return {
        "requested_prediction_date": prediction_date,
        "prediction_artifacts": sorted(artifacts),
        "mismatched_artifacts": mismatches,
        "status": "warning" if mismatches else "ok",
    }


def _rejection_reasons(
    *,
    audit_summary: dict[str, Any],
    market_availability: dict[str, Any],
    fallback_rejected_count: int,
) -> tuple[list[dict[str, Any]], int]:
    counts: Counter[str] = Counter()
    totals = audit_summary.get("totals", {}) if isinstance(audit_summary, dict) else {}
    rejected_total = int(totals.get("total_rejections") or 0)

    for row in audit_summary.get("rows", []) if isinstance(audit_summary, dict) else []:
        if not isinstance(row, dict):
            continue
        reason = _safe_text(row.get("rejection_reason"))
        if not reason or reason in {"total_candidates", "passed_to_elite"}:
            continue
        counts[reason] += int(row.get("count") or 0)

    if not counts:
        rejected_total = 0
        by_market = market_availability.get("rejection_counts_by_market", {})
        if isinstance(by_market, dict):
            for reasons in by_market.values():
                if not isinstance(reasons, dict):
                    continue
                for reason, count in reasons.items():
                    count_int = int(count or 0)
                    counts[_safe_text(reason) or "unknown"] += count_int
                    rejected_total += count_int

    if not rejected_total:
        rejected_total = fallback_rejected_count or int(sum(counts.values()))

    rows: list[dict[str, Any]] = []
    for reason, count in counts.most_common(10):
        pct = round((count / rejected_total) * 100.0, 2) if rejected_total else 0.0
        rows.append({"reason": reason, "count": int(count), "percentage": pct})
    return rows, int(rejected_total)


def _kelly_safety_summary(kelly_df: pd.DataFrame) -> dict[str, Any]:
    if kelly_df.empty:
        return {
            "total_rows": 0,
            "kelly_eligible_count": 0,
            "skipped_count": 0,
            "total_stake": 0.0,
            "total_expected_value": 0.0,
            "context_high_caution_over_skip_count": 0,
            "medium_neutral_over_dampened_count": 0,
            "total_stake_reduction_from_dampeners": 0.0,
        }

    eligible_col = "kelly_eligible" if "kelly_eligible" in kelly_df.columns else "eligible"
    eligible_mask = (
        kelly_df[eligible_col].map(_is_truthy)
        if eligible_col in kelly_df.columns
        else pd.Series(False, index=kelly_df.index)
    )
    stake = pd.to_numeric(kelly_df.get("stake_amount", pd.Series(0, index=kelly_df.index)), errors="coerce").fillna(0.0)
    expected_value = pd.to_numeric(kelly_df.get("expected_value", pd.Series(0, index=kelly_df.index)), errors="coerce").fillna(0.0)
    factors = pd.to_numeric(
        kelly_df.get("stake_dampener_factor", pd.Series(1.0, index=kelly_df.index)),
        errors="coerce",
    ).fillna(1.0)
    dampened_mask = (
        kelly_df.get("stake_dampener_reason", pd.Series("", index=kelly_df.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("medium_neutral_over_dampener")
    )
    stake_reduction = 0.0
    for amount, factor, dampened in zip(stake, factors, dampened_mask):
        if dampened and factor > 0:
            stake_reduction += (float(amount) / float(factor)) - float(amount)

    skip_reason = kelly_df.get("skip_reason", pd.Series("", index=kelly_df.index)).fillna("").astype(str).str.strip()
    return {
        "total_rows": int(len(kelly_df)),
        "kelly_eligible_count": int(eligible_mask.sum()),
        "skipped_count": int((~eligible_mask).sum()),
        "total_stake": round(float(stake.sum()), 2),
        "total_expected_value": round(float(expected_value.sum()), 2),
        "context_high_caution_over_skip_count": int(skip_reason.eq("context_high_caution_over").sum()),
        "medium_neutral_over_dampened_count": int(dampened_mask.sum()),
        "total_stake_reduction_from_dampeners": round(float(stake_reduction), 2),
    }


def _exposure_summary(kelly_df: pd.DataFrame, elite_df: pd.DataFrame) -> dict[str, Any]:
    if kelly_df.empty:
        return {
            "by_player": {},
            "by_team": {},
            "by_game": {},
            "max_player_exposure": 0.0,
            "max_team_exposure": 0.0,
            "max_game_exposure": 0.0,
            "warnings": ["Kelly stakes artifact is missing or empty."],
        }

    working = kelly_df.copy()
    if "stake_amount" not in working.columns:
        working["stake_amount"] = 0.0
    working["stake_amount"] = pd.to_numeric(working["stake_amount"], errors="coerce").fillna(0.0)

    if not elite_df.empty and "player_name" in elite_df.columns:
        lookup_columns = ["player_name"]
        for column in ("team", "team_abbr", "game_id"):
            if column in elite_df.columns and column not in working.columns:
                lookup_columns.append(column)
        if len(lookup_columns) > 1:
            working = working.merge(
                elite_df[lookup_columns].drop_duplicates(subset=["player_name"]),
                on="player_name",
                how="left",
            )

    def grouped(column: str) -> dict[str, float]:
        if column not in working.columns:
            return {}
        labels = working[column].fillna("unknown").astype(str).str.strip().replace("", "unknown")
        values = working.assign(_label=labels).groupby("_label")["stake_amount"].sum()
        return {str(key): round(float(value), 2) for key, value in values.sort_index().items()}

    by_player = grouped("player_name")
    team_column = "team" if "team" in working.columns else "team_abbr"
    by_team = grouped(team_column) if team_column in working.columns else {}
    game_column = "game_id" if "game_id" in working.columns else "game_key"
    by_game = grouped(game_column) if game_column in working.columns else {}
    warnings: list[str] = []
    if not by_team:
        warnings.append("Team exposure unavailable because no team column was found.")
    if not by_game:
        warnings.append("Game exposure unavailable because no game key column was found.")
    return {
        "by_player": by_player,
        "by_team": by_team,
        "by_game": by_game,
        "max_player_exposure": round(max(by_player.values()) if by_player else 0.0, 2),
        "max_team_exposure": round(max(by_team.values()) if by_team else 0.0, 2),
        "max_game_exposure": round(max(by_game.values()) if by_game else 0.0, 2),
        "warnings": warnings,
    }


def _board_movement_summary(
    *,
    previous_payload: dict[str, Any],
    current_elite_count: int,
    current_kelly_count: int,
) -> dict[str, Any]:
    previous_funnel = previous_payload.get("candidate_funnel", {}) if isinstance(previous_payload, dict) else {}
    previous_elite = previous_funnel.get("elite_board_count")
    previous_kelly = previous_funnel.get("kelly_rows_count")
    if previous_elite is None and previous_kelly is None:
        return {
            "comparison_available": False,
            "note": "No previous same-date quality summary was available.",
        }
    previous_elite_int = _safe_int(previous_elite)
    previous_kelly_int = _safe_int(previous_kelly)
    return {
        "comparison_available": True,
        "previous_elite_row_count": previous_elite_int,
        "current_elite_row_count": current_elite_count,
        "elite_row_count_diff": None if previous_elite_int is None else current_elite_count - previous_elite_int,
        "previous_kelly_row_count": previous_kelly_int,
        "current_kelly_row_count": current_kelly_count,
        "kelly_row_count_diff": None if previous_kelly_int is None else current_kelly_count - previous_kelly_int,
    }


def _market_column(df: pd.DataFrame) -> str | None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    for column in MARKET_COLUMN_CANDIDATES:
        if column in df.columns:
            return column
    return None


def _market_series(df: pd.DataFrame) -> pd.Series:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.Series(dtype=str)
    column = _market_column(df)
    if column is None:
        return pd.Series("unknown", index=df.index, dtype=str)
    return df[column].fillna("unknown").astype(str).str.strip().replace("", "unknown")


def _count_by_market(df: pd.DataFrame) -> dict[str, int]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    markets = _market_series(df)
    return {str(market): int(count) for market, count in markets.value_counts().sort_index().items()}


def _market_availability_counts(market_availability: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = market_availability.get("counts", []) if isinstance(market_availability, dict) else []
    counts: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return counts
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        market = _safe_text(_first_present(raw_row, MARKET_COLUMN_CANDIDATES)) or "unknown"
        row = counts.setdefault(
            market,
            {
                "normalized_odds_rows": 0,
                "raw_candidates_count": None,
            },
        )
        row["normalized_odds_rows"] += int(raw_row.get("count_after_normalization") or 0)
        if "count_after_candidate_generation" in raw_row:
            current = row.get("raw_candidates_count")
            row["raw_candidates_count"] = int(current or 0) + int(raw_row.get("count_after_candidate_generation") or 0)
    return counts


def _rejection_reasons_by_market(
    *,
    audit_summary: dict[str, Any],
    market_availability: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: dict[str, Counter[str]] = {}

    for raw_row in audit_summary.get("rows", []) if isinstance(audit_summary, dict) else []:
        if not isinstance(raw_row, dict):
            continue
        reason = _safe_text(raw_row.get("rejection_reason"))
        if not reason or reason in {"total_candidates", "passed_to_elite"}:
            continue
        market = _safe_text(_first_present(raw_row, MARKET_COLUMN_CANDIDATES)) or "unknown"
        counts.setdefault(market, Counter())[reason] += int(raw_row.get("count") or 0)

    if not counts:
        by_market = market_availability.get("rejection_counts_by_market", {}) if isinstance(market_availability, dict) else {}
        if isinstance(by_market, dict):
            for market, reasons in by_market.items():
                market_name = _safe_text(market) or "unknown"
                if not isinstance(reasons, dict):
                    continue
                for reason, count in reasons.items():
                    reason_name = _safe_text(reason) or "unknown"
                    counts.setdefault(market_name, Counter())[reason_name] += int(count or 0)

    rows: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    for market in sorted(counts):
        total = int(sum(counts[market].values()))
        totals[market] = total
        for reason, count in sorted(counts[market].items(), key=lambda item: (-item[1], item[0])):
            pct = round((count / total) * 100.0, 2) if total else 0.0
            rows.append(
                {
                    "market": market,
                    "rejection_reason": reason,
                    "count": int(count),
                    "percentage": pct,
                }
            )
    return rows, totals


def _kelly_counts_by_market(kelly_df: pd.DataFrame) -> dict[str, dict[str, int]]:
    if not isinstance(kelly_df, pd.DataFrame) or kelly_df.empty:
        return {}
    markets = _market_series(kelly_df)
    eligible_col = "kelly_eligible" if "kelly_eligible" in kelly_df.columns else "eligible"
    eligible_mask = (
        kelly_df[eligible_col].map(_is_truthy)
        if eligible_col in kelly_df.columns
        else pd.Series(False, index=kelly_df.index)
    )
    skip_reason = kelly_df.get("skip_reason", pd.Series("", index=kelly_df.index)).fillna("").astype(str).str.strip()
    dampened_mask = (
        kelly_df.get("stake_dampener_reason", pd.Series("", index=kelly_df.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("medium_neutral_over_dampener")
    )
    counts: dict[str, dict[str, int]] = {}
    for market in sorted(markets.unique()):
        mask = markets.eq(market)
        total = int(mask.sum())
        eligible = int(eligible_mask[mask].sum())
        counts[str(market)] = {
            "kelly_rows_count": total,
            "kelly_eligible_count": eligible,
            "skipped_count": int(total - eligible),
            "high_caution_over_skip_count": int((skip_reason.eq("context_high_caution_over") & mask).sum()),
            "medium_neutral_over_dampened_count": int((dampened_mask & mask).sum()),
        }
    return counts


def _market_coverage_summary(
    *,
    market_availability: dict[str, Any],
    full_market_df: pd.DataFrame,
    elite_df: pd.DataFrame,
    kelly_df: pd.DataFrame,
    rejection_counts_by_market: dict[str, int],
) -> list[dict[str, Any]]:
    availability = _market_availability_counts(market_availability)
    full_counts = _count_by_market(full_market_df)
    elite_counts = _count_by_market(elite_df)
    kelly_counts = _kelly_counts_by_market(kelly_df)
    markets = set(availability) | set(full_counts) | set(elite_counts) | set(kelly_counts) | set(rejection_counts_by_market)
    rows: list[dict[str, Any]] = []
    for market in sorted(markets):
        availability_row = availability.get(market, {})
        normalized = int(availability_row.get("normalized_odds_rows") or 0)
        raw_candidates = availability_row.get("raw_candidates_count")
        if normalized == 0 and not any(
            (
                full_counts.get(market, 0),
                elite_counts.get(market, 0),
                kelly_counts.get(market, {}).get("kelly_rows_count", 0),
                rejection_counts_by_market.get(market, 0),
            )
        ):
            continue
        kelly_row = kelly_counts.get(market, {})
        rows.append(
            {
                "market": market,
                "normalized_odds_rows": normalized,
                "raw_candidates_count": raw_candidates,
                "full_market_board_count": int(full_counts.get(market, 0)),
                "elite_board_count": int(elite_counts.get(market, 0)),
                "rejected_count": int(rejection_counts_by_market.get(market, 0)),
                "kelly_rows_count": int(kelly_row.get("kelly_rows_count", 0)),
                "kelly_eligible_count": int(kelly_row.get("kelly_eligible_count", 0)),
                "skipped_count": int(kelly_row.get("skipped_count", 0)),
                "high_caution_over_skip_count": int(kelly_row.get("high_caution_over_skip_count", 0)),
                "medium_neutral_over_dampened_count": int(kelly_row.get("medium_neutral_over_dampened_count", 0)),
            }
        )
    return rows


def _player_baseline_coverage_summary(
    *,
    player_predictions_df: pd.DataFrame,
    player_baselines_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
    elite_df: pd.DataFrame,
    kelly_df: pd.DataFrame,
) -> dict[str, Any]:
    player_predictions_rows = _count_rows(player_predictions_df)
    baseline_rows = _count_rows(player_baselines_df)
    unique_baseline_players = _unique_player_count(player_baselines_df)
    full_market_unique_players = _unique_player_count(full_market_df)
    elite_unique_players = _unique_player_count(elite_df)
    kelly_unique_players = _unique_player_count(kelly_df)

    active_teams = _active_team_values(full_market_df, elite_df)
    active_team_baseline_players = None
    if active_teams and not player_baselines_df.empty:
        team_column = "team_abbr" if "team_abbr" in player_baselines_df.columns else "team"
        if team_column in player_baselines_df.columns:
            team_series = player_baselines_df[team_column].fillna("").astype(str).str.strip().str.upper()
            active_team_baseline_players = _unique_player_count(player_baselines_df[team_series.isin(active_teams)])

    odds_values, identity_column = _player_identity_values(full_market_df, player_baselines_df)
    baseline_values: set[str] = set()
    if identity_column and identity_column in player_baselines_df.columns:
        baseline_values = set(_nonempty_text_series(player_baselines_df[identity_column]).astype(str))
    odds_baseline_matched_players = len(odds_values & baseline_values) if baseline_values else 0
    active_board_match_rate = (
        round(odds_baseline_matched_players / len(odds_values), 4)
        if odds_values
        else None
    )

    return {
        "player_predictions_rows": player_predictions_rows,
        "baseline_rows": baseline_rows,
        "unique_baseline_players": unique_baseline_players,
        "baseline_universe_player_count": unique_baseline_players,
        "active_team_baseline_players": active_team_baseline_players,
        "active_team_source": "full_market_board" if active_teams else "unavailable",
        "active_teams": sorted(active_teams),
        "odds_unique_players": len(odds_values),
        "odds_unique_players_source": "full_market_board",
        "odds_baseline_matched_players": int(odds_baseline_matched_players),
        "odds_baseline_match_key": identity_column or "unavailable",
        "active_board_match_rate": active_board_match_rate,
        "full_market_unique_players": full_market_unique_players,
        "elite_unique_players": elite_unique_players,
        "kelly_unique_players": kelly_unique_players,
        "players_or_baselines_count": player_predictions_rows,
        "players_or_baselines_count_legacy_definition": (
            "Legacy alias for player_predictions_rows; not the model baseline universe."
        ),
    }


def _coverage_warnings(
    *,
    slate_provider_counts: dict[str, Any],
    player_baseline_coverage: dict[str, Any],
    kelly_safety: dict[str, Any],
    market_rows: Sequence[dict[str, Any]],
    min_live_candidates: int = DEFAULT_MIN_LIVE_CANDIDATES,
    min_kelly_eligible: int = DEFAULT_MIN_KELLY_ELIGIBLE,
    min_odds_players_per_game_ratio: int = DEFAULT_MIN_ODDS_PLAYERS_PER_GAME_RATIO,
    min_full_market_players_per_game_ratio: int = DEFAULT_MIN_FULL_MARKET_PLAYERS_PER_GAME_RATIO,
    min_active_team_baseline_players_per_game_ratio: int = DEFAULT_MIN_ACTIVE_TEAM_BASELINE_PLAYERS_PER_GAME_RATIO,
) -> list[str]:
    warnings: list[str] = []
    live_candidates = _history_int(slate_provider_counts.get("live_odds_count"))
    if live_candidates < min_live_candidates:
        warnings.append(
            f"Low live candidate coverage: live_candidates={live_candidates} below floor={min_live_candidates}."
        )

    kelly_eligible = _history_int(kelly_safety.get("kelly_eligible_count"))
    if kelly_eligible < min_kelly_eligible:
        warnings.append(
            f"Low Kelly eligible coverage: kelly_eligible={kelly_eligible} below floor={min_kelly_eligible}."
        )

    games_count = _history_int(slate_provider_counts.get("games_count"))
    active_team_baseline_players = _safe_int(player_baseline_coverage.get("active_team_baseline_players"))
    if active_team_baseline_players is not None and games_count > 0:
        ratio = round(active_team_baseline_players / games_count, 2)
        if ratio < min_active_team_baseline_players_per_game_ratio:
            warnings.append(
                "Low active-team baseline coverage: "
                f"active_team_baseline_players={active_team_baseline_players}, "
                f"games_count={games_count}, ratio={ratio}, "
                f"floor={min_active_team_baseline_players_per_game_ratio}."
            )

    odds_unique_players = _history_int(player_baseline_coverage.get("odds_unique_players"))
    odds_baseline_matched_players = _history_int(player_baseline_coverage.get("odds_baseline_matched_players"))
    if odds_unique_players > 0 and (odds_baseline_matched_players / odds_unique_players) < 0.5:
        ratio = round(odds_baseline_matched_players / odds_unique_players, 4)
        warnings.append(
            "Low active-board baseline match: "
            f"odds_baseline_matched_players={odds_baseline_matched_players}, "
            f"odds_unique_players={odds_unique_players}, active_board_match_rate={ratio}."
        )
    if games_count > 0 and odds_unique_players / games_count < min_odds_players_per_game_ratio:
        ratio = round(odds_unique_players / games_count, 2)
        warnings.append(
            "Low provider player coverage: "
            f"odds_unique_players={odds_unique_players}, games_count={games_count}, "
            f"ratio={ratio}, floor={min_odds_players_per_game_ratio}."
        )

    full_market_unique_players = _history_int(player_baseline_coverage.get("full_market_unique_players"))
    if games_count > 0 and full_market_unique_players / games_count < min_full_market_players_per_game_ratio:
        ratio = round(full_market_unique_players / games_count, 2)
        warnings.append(
            "Low full-market player coverage: "
            f"full_market_unique_players={full_market_unique_players}, games_count={games_count}, "
            f"ratio={ratio}, floor={min_full_market_players_per_game_ratio}."
        )

    for row in market_rows:
        normalized = _history_int(row.get("normalized_odds_rows"))
        raw_candidates = row.get("raw_candidates_count")
        raw_candidates_known = raw_candidates is not None
        no_raw_candidates = raw_candidates_known and _history_int(raw_candidates) == 0
        no_board_rows = (
            _history_int(row.get("full_market_board_count")) == 0
            and _history_int(row.get("elite_board_count")) == 0
            and _history_int(row.get("kelly_rows_count")) == 0
        )
        if normalized > 0 and no_board_rows and (no_raw_candidates or not raw_candidates_known):
            warnings.append(
                "Market coverage loss: "
                f"market={row.get('market')} normalized_odds_rows={normalized} "
                "but zero candidates/board rows survived."
            )
    return warnings


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
    if not isinstance(df, pd.DataFrame) or df.empty:
        return counts
    for _, row in df.iterrows():
        cause = _context_conflict_cause(row)
        if cause:
            counts[cause] = int(counts.get(cause, 0) + 1)
    return counts


def _elite_context_safety_summary(full_market_df: pd.DataFrame, elite_df: pd.DataFrame) -> dict[str, Any]:
    def _reason_count(df: pd.DataFrame) -> int:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return 0
        count = 0
        for column in ("final_elite_rejection_reason", "elite_rejection_reason"):
            if column in df.columns:
                count += int(
                    df[column]
                    .fillna("")
                    .astype(str)
                    .str.strip()
                    .eq(ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER)
                    .sum()
                )
        return count

    def _context_mask_count(df: pd.DataFrame) -> int:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return 0
        selection = df.get("selection", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
        caution = df.get("context_caution_level", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
        alignment = df.get("context_pick_alignment", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().str.lower()
        return int((selection.eq("over") & caution.eq("high") & alignment.eq("conflicted")).sum())

    rejected = _reason_count(full_market_df)
    elite_remaining = _context_mask_count(elite_df)
    full_market_cause_counts = _context_conflict_cause_counts(full_market_df)
    elite_cause_counts = _context_conflict_cause_counts(elite_df)
    return {
        "rejection_reason": ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER,
        "full_market_high_caution_conflicted_over_count": _context_mask_count(full_market_df),
        "rejected_from_final_elite_count": rejected,
        "elite_high_caution_conflicted_over_count": elite_remaining,
        "final_elite_context_clean": elite_remaining == 0,
        "valid_context_safety_blocking": bool(rejected and elite_remaining == 0),
        "full_market_context_conflict_cause_counts": full_market_cause_counts,
        "elite_context_conflict_cause_counts": elite_cause_counts,
        "stale_team_not_in_game_count": int(full_market_cause_counts.get("stale_team_not_in_game", 0)),
        "status": "warning" if elite_remaining else "ok",
    }


def _artifact_counts(full_market_df: pd.DataFrame, elite_df: pd.DataFrame) -> dict[str, Any]:
    frame = full_market_df if not full_market_df.empty else elite_df
    if frame.empty:
        return {
            "live_odds_count": 0,
            "synthetic_or_fallback_odds_count": 0,
            "provider_breakdown": {},
        }
    live_mask = pd.Series(False, index=frame.index)
    if "is_live_market" in frame.columns:
        live_mask = live_mask | frame["is_live_market"].map(_is_truthy)
    for column in ("line_source", "source_lane"):
        if column in frame.columns:
            text = frame[column].fillna("").astype(str).str.lower()
            live_mask = live_mask | text.str.contains("live", regex=False)
    fallback_mask = pd.Series(False, index=frame.index)
    if "synthetic_line" in frame.columns:
        fallback_mask = fallback_mask | frame["synthetic_line"].map(_is_truthy)
    for column in ("line_source", "source_lane"):
        if column in frame.columns:
            text = frame[column].fillna("").astype(str).str.lower()
            fallback_mask = fallback_mask | text.str.contains("synthetic|fallback", regex=True)
    breakdown: dict[str, dict[str, int]] = {}
    for column in ("bookmaker", "vendor", "line_source", "source_lane"):
        counts = _value_counts(frame, column)
        if counts:
            breakdown[column] = counts
    return {
        "live_odds_count": int(live_mask.sum()),
        "synthetic_or_fallback_odds_count": int(fallback_mask.sum()),
        "provider_breakdown": breakdown,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _history_int(value: Any) -> int:
    number = _safe_int(value)
    return 0 if number is None else int(number)


def _history_float(value: Any) -> float:
    number = _safe_float(value)
    return 0.0 if number is None else round(float(number), 2)


def _history_warning_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, tuple):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1 if _safe_text(value) else 0


def _warning_contains(warnings: Sequence[Any], *needles: str) -> bool:
    lowered_needles = tuple(needle.lower() for needle in needles if needle)
    for warning in warnings:
        text = _safe_text(warning).lower()
        if text and any(needle in text for needle in lowered_needles):
            return True
    return False


def _run_health_summary(
    *,
    candidate_funnel: dict[str, Any],
    kelly_safety: dict[str, Any],
    market_coverage: dict[str, Any],
    elite_context_safety: dict[str, Any],
    date_isolation: dict[str, Any],
    warnings: Sequence[Any],
) -> dict[str, Any]:
    flags: list[str] = []
    coverage_warnings = (
        market_coverage.get("coverage_warnings", [])
        if isinstance(market_coverage.get("coverage_warnings"), list)
        else []
    )

    elite_count = _history_int(candidate_funnel.get("elite_board_count"))
    full_market_count = _history_int(candidate_funnel.get("full_market_board_count"))
    kelly_rows = _history_int(_first_present(candidate_funnel, ("kelly_rows_count", "total_rows")))
    kelly_eligible = _history_int(kelly_safety.get("kelly_eligible_count"))
    high_caution_over_skips = _history_int(
        _first_present(kelly_safety, ("context_high_caution_over_skip_count", "high_caution_over_skip_count"))
    )
    context_gate_rejections = _history_int(elite_context_safety.get("rejected_from_final_elite_count"))
    elite_context_violations = _history_int(elite_context_safety.get("elite_high_caution_conflicted_over_count"))
    final_elite_context_clean = elite_count > 0 and elite_context_violations == 0
    valid_context_safety_blocking = (
        context_gate_rejections > 0
        and final_elite_context_clean
        and high_caution_over_skips == 0
    )

    fatal_artifact_issue = _warning_contains(warnings, "missing artifact:", "prediction artifact date mismatch")
    fatal_artifact_issue = fatal_artifact_issue or (
        _warning_contains(warnings, "could not read csv")
        and _warning_contains(warnings, "elite_board", "full_market_board", "kelly_stakes")
    )
    fatal_artifact_issue = fatal_artifact_issue or (
        _warning_contains(warnings, "missing optional artifact")
        and _warning_contains(warnings, "full_market_board")
    )
    fatal_artifact_issue = fatal_artifact_issue or (
        elite_count > 0
        and _warning_contains(warnings, "missing optional artifact")
        and _warning_contains(warnings, "kelly_stakes")
    )
    if _safe_text(date_isolation.get("status")).lower() not in {"", "ok"}:
        flags.append("date_isolation_warning")
    if fatal_artifact_issue:
        flags.append("required_artifact_missing_or_unreadable")

    if elite_count == 0:
        flags.append("elite_board_empty")
    if kelly_rows > 0 and kelly_eligible == 0:
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

    if _warning_contains(
        coverage_warnings,
        "low live candidate coverage",
        "low active-board baseline match",
        "low provider player coverage",
        "low full-market player coverage",
    ):
        flags.append("provider_or_candidate_coverage_low")

    if flags and any(flag in flags for flag in ("required_artifact_missing_or_unreadable", "date_isolation_warning")):
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
    elif "provider_or_candidate_coverage_low" in flags:
        status = "DEGRADED_LOW_COVERAGE"
        reason = "Provider/candidate coverage warnings are present."
    elif "valid_context_safety_blocking" in flags:
        status = "HEALTHY_CONTEXT_GATED"
        reason = (
            f"Context safety blocked {context_gate_rejections} high-caution OVER candidate(s); "
            f"final Elite is clean with {kelly_eligible} Kelly-eligible pick(s)."
        )
    elif kelly_eligible > 0 and kelly_eligible < RUN_HEALTH_LOW_VOLUME_KELLY_ELIGIBLE:
        status = "HEALTHY_LOW_VOLUME"
        flags.append("kelly_eligible_low_volume")
        reason = f"Run is valid with {kelly_eligible} Kelly-eligible pick(s)."
    else:
        status = "HEALTHY"
        reason = f"Run passed health checks with {kelly_eligible} Kelly-eligible pick(s)."

    return {
        "status": status,
        "reason": reason,
        "flags": sorted(dict.fromkeys(flags)),
        "recommendation": RUN_HEALTH_RECOMMENDATIONS[status],
    }


def _quality_history_row(payload: dict[str, Any], *, fallback_prediction_date: str) -> dict[str, Any]:
    run = payload.get("run_identity", {}) if isinstance(payload.get("run_identity"), dict) else {}
    slate = payload.get("slate_provider_counts", {}) if isinstance(payload.get("slate_provider_counts"), dict) else {}
    funnel = payload.get("candidate_funnel", {}) if isinstance(payload.get("candidate_funnel"), dict) else {}
    kelly = payload.get("kelly_safety_summary", {}) if isinstance(payload.get("kelly_safety_summary"), dict) else {}
    exposure = payload.get("risk_exposure_summary", {}) if isinstance(payload.get("risk_exposure_summary"), dict) else {}
    isolation = payload.get("date_isolation_check", {}) if isinstance(payload.get("date_isolation_check"), dict) else {}
    player_coverage = (
        payload.get("player_baseline_coverage", {})
        if isinstance(payload.get("player_baseline_coverage"), dict)
        else {}
    )
    market_coverage = payload.get("market_coverage", {}) if isinstance(payload.get("market_coverage"), dict) else {}
    run_health = payload.get("run_health", {}) if isinstance(payload.get("run_health"), dict) else {}
    market_rows = market_coverage.get("rows", []) if isinstance(market_coverage.get("rows"), list) else []
    points_rows = [
        row
        for row in market_rows
        if isinstance(row, dict) and _safe_text(row.get("market")).lower() in POINTS_MARKET_NAMES
    ]
    non_points_rejected = sum(
        _history_int(row.get("rejected_count"))
        for row in market_rows
        if isinstance(row, dict) and _safe_text(row.get("market")).lower() not in POINTS_MARKET_NAMES
    )
    coverage_warnings = (
        market_coverage.get("coverage_warnings", [])
        if isinstance(market_coverage.get("coverage_warnings"), list)
        else []
    )
    raw_health_flags = (
        run_health.get("flags")
        if isinstance(run_health.get("flags"), list)
        else payload.get("run_health_flags", [])
    )
    health_flags = raw_health_flags if isinstance(raw_health_flags, list) else [raw_health_flags]

    prediction_date = _safe_text(run.get("prediction_date")) or fallback_prediction_date
    row = {
        "prediction_date": prediction_date,
        "generated_at": _safe_text(run.get("generated_at")),
        "commit_hash": _safe_text(run.get("commit_hash")),
        "data_mode": _safe_text(_first_present(run, ("run_data_mode", "data_mode"))),
        "games_count": _history_int(slate.get("games_count")),
        "players_count": _history_int(
            _first_present(player_coverage, ("player_predictions_rows", "players_or_baselines_count"))
            or _first_present(slate, ("players_or_baselines_count", "players_count"))
        ),
        "player_predictions_rows": _history_int(player_coverage.get("player_predictions_rows")),
        "baseline_rows": _history_int(player_coverage.get("baseline_rows")),
        "unique_baseline_players": _history_int(player_coverage.get("unique_baseline_players")),
        "active_team_baseline_players": _history_int(player_coverage.get("active_team_baseline_players")),
        "odds_unique_players": _history_int(player_coverage.get("odds_unique_players")),
        "odds_baseline_matched_players": _history_int(player_coverage.get("odds_baseline_matched_players")),
        "full_market_unique_players": _history_int(player_coverage.get("full_market_unique_players")),
        "elite_unique_players": _history_int(player_coverage.get("elite_unique_players")),
        "kelly_unique_players": _history_int(player_coverage.get("kelly_unique_players")),
        "raw_odds_rows": _history_int(_first_present(slate, ("raw_odds_rows_count", "raw_odds_rows"))),
        "normalized_odds_rows": _history_int(
            _first_present(slate, ("normalized_odds_rows_count", "normalized_odds_rows"))
        ),
        "live_odds_count": _history_int(slate.get("live_odds_count")),
        "synthetic_fallback_odds_count": _history_int(
            _first_present(slate, ("synthetic_or_fallback_odds_count", "synthetic_fallback_odds_count"))
        ),
        "raw_candidates_count": _history_int(funnel.get("raw_candidates_count")),
        "rejected_candidates_count": _history_int(funnel.get("rejected_candidates_count")),
        "full_market_board_count": _history_int(funnel.get("full_market_board_count")),
        "elite_board_count": _history_int(funnel.get("elite_board_count")),
        "sgp_board_count": _history_int(funnel.get("sgp_board_count")),
        "kelly_rows_count": _history_int(_first_present(funnel, ("kelly_rows_count", "total_rows"))),
        "kelly_eligible_count": _history_int(kelly.get("kelly_eligible_count")),
        "kelly_skipped_count": _history_int(_first_present(kelly, ("skipped_count", "kelly_skipped_count"))),
        "high_caution_over_skip_count": _history_int(
            _first_present(kelly, ("context_high_caution_over_skip_count", "high_caution_over_skip_count"))
        ),
        "medium_neutral_over_dampened_count": _history_int(kelly.get("medium_neutral_over_dampened_count")),
        "total_stake": _history_float(kelly.get("total_stake")),
        "total_expected_value": _history_float(kelly.get("total_expected_value")),
        "max_player_exposure": _history_float(exposure.get("max_player_exposure")),
        "max_team_exposure": _history_float(exposure.get("max_team_exposure")),
        "max_game_exposure": _history_float(exposure.get("max_game_exposure")),
        "markets_seen": len([row for row in market_rows if isinstance(row, dict)]),
        "points_normalized_odds_rows": sum(_history_int(row.get("normalized_odds_rows")) for row in points_rows),
        "points_full_market_board_count": sum(_history_int(row.get("full_market_board_count")) for row in points_rows),
        "points_elite_board_count": sum(_history_int(row.get("elite_board_count")) for row in points_rows),
        "points_kelly_eligible_count": sum(_history_int(row.get("kelly_eligible_count")) for row in points_rows),
        "non_points_rejected_count": int(non_points_rejected),
        "low_coverage_warning_count": len(coverage_warnings),
        "run_health_status": _safe_text(
            _first_present(run_health, ("status", "run_health_status")) or payload.get("run_health_status")
        ),
        "run_health_reason": _safe_text(
            _first_present(run_health, ("reason", "run_health_reason")) or payload.get("run_health_reason")
        ),
        "run_health_flags": ";".join(_safe_text(flag) for flag in health_flags if _safe_text(flag)),
        "run_health_recommendation": _safe_text(
            _first_present(run_health, ("recommendation", "run_health_recommendation"))
            or payload.get("run_health_recommendation")
        ),
        "date_isolation_status": _safe_text(isolation.get("status")),
        "warnings_count": _history_warning_count(payload.get("warnings")),
    }
    return {column: row.get(column, "") for column in QUALITY_HISTORY_COLUMNS}


def _read_quality_history_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [
                {column: row.get(column, "") for column in QUALITY_HISTORY_COLUMNS}
                for row in reader
                if _safe_text(row.get("prediction_date"))
            ]
    except Exception:
        return []


def _write_quality_history_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUALITY_HISTORY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in QUALITY_HISTORY_COLUMNS})


def _write_quality_history_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(_json_safe(row), sort_keys=True) for row in rows)
    path.write_text((text + "\n") if text else "", encoding="utf-8")


def update_quality_history_from_summary(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    write_jsonl: bool = True,
) -> tuple[Path, Path | None, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"
    summary_path = operator_dir / f"quality_summary_{prediction_date}.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Quality summary JSON must contain an object: {summary_path}")

    row = _quality_history_row(payload, fallback_prediction_date=prediction_date)
    history_csv_path = operator_dir / QUALITY_HISTORY_CSV_NAME
    history_jsonl_path = operator_dir / QUALITY_HISTORY_JSONL_NAME if write_jsonl else None

    existing_rows = _read_quality_history_rows(history_csv_path)
    row_date = _safe_text(row.get("prediction_date")) or prediction_date
    updated_rows = [
        existing_row
        for existing_row in existing_rows
        if _safe_text(existing_row.get("prediction_date")) != row_date
    ]
    updated_rows.append(row)
    updated_rows.sort(key=lambda item: _safe_text(item.get("prediction_date")))

    _write_quality_history_csv(history_csv_path, updated_rows)
    if history_jsonl_path is not None:
        _write_quality_history_jsonl(history_jsonl_path, updated_rows)
    return history_csv_path, history_jsonl_path, row


def build_quality_summary(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    out_dir: str | Path | None = None,
    generated_at: str | None = None,
    extra_prediction_artifact_paths: Sequence[str | Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    out_dir_path = Path(out_dir) if out_dir is not None else runtime_root.parent
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"
    research_dir = runtime_root / "research"
    model_dir = runtime_root.parent / "model"
    warnings: list[str] = []

    quality_json_path = operator_dir / f"quality_summary_{prediction_date}.json"
    previous_payload = _read_json(quality_json_path, [], optional=True) if quality_json_path.exists() else {}

    elite_df = _read_csv(operator_dir / f"elite_board_{prediction_date}.csv", warnings, optional=False)
    full_market_df = _read_csv(operator_dir / f"full_market_board_{prediction_date}.csv", warnings)
    sgp_path = operator_dir / f"sgp_board_{prediction_date}.csv"
    sgp_df = _read_csv(
        sgp_path,
        warnings,
        empty_warning=f"SGP board artifact is empty; expected columns unavailable: {sgp_path}",
    )
    kelly_df = _read_csv(operator_dir / f"kelly_stakes_{prediction_date}.csv", warnings)
    player_predictions_df = _read_csv(research_dir / f"player_predictions_{prediction_date}.csv", warnings)
    player_baselines_df = _read_csv(
        model_dir / "player_baselines.csv",
        warnings,
        missing_warning=f"Missing optional baseline artifact: {model_dir / 'player_baselines.csv'}",
        empty_warning=f"Model player baselines artifact is empty: {model_dir / 'player_baselines.csv'}",
    )
    model_metrics = _read_json(research_dir / f"model_metrics_{prediction_date}.json", warnings)
    board_diagnostics = _read_json(diagnostics_dir / f"board_diagnostics_{prediction_date}.json", warnings)
    audit_summary = _read_json(operator_dir / f"elite_pipeline_audit_summary_{prediction_date}.json", warnings)
    market_availability = _read_json(diagnostics_dir / f"market_availability_audit_{prediction_date}.json", warnings)

    prediction_summary = model_metrics.get("prediction_summary", {}) if isinstance(model_metrics, dict) else {}
    counts = _artifact_counts(full_market_df, elite_df)
    run_data_mode = _infer_run_data_mode(prediction_summary, [elite_df, full_market_df])

    games_count = _safe_int(prediction_summary.get("games_count"))
    raw_odds_rows = _safe_int(prediction_summary.get("odds_count"))
    normalized_odds_rows = None
    market_counts = market_availability.get("counts", []) if isinstance(market_availability, dict) else []
    if isinstance(market_counts, list) and market_counts:
        raw_sum = 0
        normalized_sum = 0
        for row in market_counts:
            if not isinstance(row, dict):
                continue
            raw_sum += int(row.get("count_before_normalization") or 0)
            normalized_sum += int(row.get("count_after_normalization") or 0)
        raw_odds_rows = raw_odds_rows if raw_odds_rows is not None else raw_sum
        normalized_odds_rows = normalized_sum
    if normalized_odds_rows is None:
        normalized_odds_rows = raw_odds_rows

    board_counts = board_diagnostics.get("board_counts", {}) if isinstance(board_diagnostics, dict) else {}
    raw_candidates = _safe_int(prediction_summary.get("candidate_count")) or _count_rows(full_market_df)
    full_market_count = _safe_int(prediction_summary.get("full_market_count"))
    if full_market_count is None:
        full_market_count = _safe_int(board_counts.get("full_market")) or _count_rows(full_market_df)
    elite_count = _safe_int(prediction_summary.get("elite_count"))
    if elite_count is None:
        elite_count = _safe_int(board_counts.get("elite")) or _count_rows(elite_df)
    kelly_count = _count_rows(kelly_df)
    fallback_rejected = max(raw_candidates - elite_count, 0)
    rejection_reasons, rejected_count = _rejection_reasons(
        audit_summary=audit_summary,
        market_availability=market_availability,
        fallback_rejected_count=fallback_rejected,
    )

    artifact_paths = _prediction_artifact_paths(runtime_root, prediction_date)
    if extra_prediction_artifact_paths:
        artifact_paths.extend(Path(path) for path in extra_prediction_artifact_paths)
    date_isolation = _date_isolation_summary(
        prediction_date=prediction_date,
        artifact_paths=artifact_paths,
    )
    for mismatch in date_isolation["mismatched_artifacts"]:
        warnings.append(
            "Prediction artifact date mismatch: "
            f"requested={prediction_date} artifact={mismatch['artifact_date']} path={mismatch['path']}"
        )

    slate_provider_counts = {
        "games_count": games_count,
        "raw_odds_rows_count": raw_odds_rows,
        "normalized_odds_rows_count": normalized_odds_rows,
        "live_odds_count": counts["live_odds_count"],
        "synthetic_or_fallback_odds_count": counts["synthetic_or_fallback_odds_count"],
        "provider_breakdown": counts["provider_breakdown"],
    }
    player_baseline_coverage = _player_baseline_coverage_summary(
        player_predictions_df=player_predictions_df,
        player_baselines_df=player_baselines_df,
        full_market_df=full_market_df,
        elite_df=elite_df,
        kelly_df=kelly_df,
    )
    slate_provider_counts["players_or_baselines_count"] = player_baseline_coverage["players_or_baselines_count"]
    slate_provider_counts["players_or_baselines_count_legacy_definition"] = player_baseline_coverage[
        "players_or_baselines_count_legacy_definition"
    ]
    candidate_funnel = {
        "raw_candidates_count": raw_candidates,
        "rejected_candidates_count": rejected_count,
        "full_market_board_count": full_market_count,
        "elite_board_count": elite_count,
        "sgp_board_count": _count_rows(sgp_df),
        "kelly_rows_count": kelly_count,
    }
    kelly_safety = _kelly_safety_summary(kelly_df)
    risk_exposure = _exposure_summary(kelly_df, elite_df)
    elite_context_safety = _elite_context_safety_summary(full_market_df, elite_df)
    high_caution_over_watchlist = build_high_caution_over_watchlist(full_market_df)
    high_caution_over_watchlist_path = watchlist_path_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    rejection_reasons_market_rows, rejection_counts_by_market = _rejection_reasons_by_market(
        audit_summary=audit_summary,
        market_availability=market_availability,
    )
    market_rows = _market_coverage_summary(
        market_availability=market_availability,
        full_market_df=full_market_df,
        elite_df=elite_df,
        kelly_df=kelly_df,
        rejection_counts_by_market=rejection_counts_by_market,
    )
    coverage_warnings = _coverage_warnings(
        slate_provider_counts=slate_provider_counts,
        player_baseline_coverage=player_baseline_coverage,
        kelly_safety=kelly_safety,
        market_rows=market_rows,
    )
    if elite_context_safety["rejected_from_final_elite_count"]:
        coverage_warnings.append(
            "Elite context safety gate excluded "
            f"{elite_context_safety['rejected_from_final_elite_count']} "
            "high-caution conflicted OVER candidate(s) from final elite."
        )
    if elite_count == 0 and elite_context_safety["rejected_from_final_elite_count"]:
        coverage_warnings.append(
            "Elite board is empty after the context safety gate; no-bet is preferred "
            "over filling elite with Kelly-blocked high-caution OVERs."
        )
    board_movement = _board_movement_summary(
        previous_payload=previous_payload,
        current_elite_count=elite_count,
        current_kelly_count=kelly_count,
    )
    final_warnings = warnings + coverage_warnings + risk_exposure.get("warnings", [])

    run_identity = {
        "prediction_date": prediction_date,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "output_directory": str(out_dir_path),
        "commit_hash": _git_commit_hash(),
        "run_data_mode": run_data_mode,
    }
    run_health = _run_health_summary(
        candidate_funnel=candidate_funnel,
        kelly_safety=kelly_safety,
        market_coverage={"coverage_warnings": coverage_warnings},
        elite_context_safety=elite_context_safety,
        date_isolation=date_isolation,
        warnings=final_warnings,
    )
    payload = {
        "run_identity": run_identity,
        "run_health": run_health,
        "run_health_status": run_health["status"],
        "run_health_reason": run_health["reason"],
        "run_health_flags": run_health["flags"],
        "run_health_recommendation": run_health["recommendation"],
        "slate_provider_counts": slate_provider_counts,
        "player_baseline_coverage": player_baseline_coverage,
        "candidate_funnel": candidate_funnel,
        "top_rejection_reasons": rejection_reasons,
        "market_coverage": {
            "config": {
                "min_live_candidates": DEFAULT_MIN_LIVE_CANDIDATES,
                "min_kelly_eligible": DEFAULT_MIN_KELLY_ELIGIBLE,
                "min_odds_players_per_game_ratio": DEFAULT_MIN_ODDS_PLAYERS_PER_GAME_RATIO,
                "min_full_market_players_per_game_ratio": DEFAULT_MIN_FULL_MARKET_PLAYERS_PER_GAME_RATIO,
                "min_active_team_baseline_players_per_game_ratio": DEFAULT_MIN_ACTIVE_TEAM_BASELINE_PLAYERS_PER_GAME_RATIO,
            },
            "rows": market_rows,
            "rejection_reasons_by_market": rejection_reasons_market_rows,
            "coverage_warnings": coverage_warnings,
        },
        "kelly_safety_summary": kelly_safety,
        "elite_context_safety_gate": elite_context_safety,
        "high_caution_over_watchlist": {
            "path": str(high_caution_over_watchlist_path),
            "row_count": int(len(high_caution_over_watchlist)),
            "observation_only": True,
            "note": OBSERVATION_ONLY_NOTE,
            "source": str(operator_dir / f"full_market_board_{prediction_date}.csv"),
        },
        "risk_exposure_summary": risk_exposure,
        "board_movement_summary": board_movement,
        "date_isolation_check": date_isolation,
        "warnings": final_warnings,
    }
    payload = _json_safe(payload)
    return _format_quality_summary_text(payload), payload


def _fmt_value(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _format_mapping(mapping: dict[str, Any], *, indent: str = "- ") -> list[str]:
    if not mapping:
        return [f"{indent}none"]
    return [f"{indent}{key}: {value}" for key, value in sorted(mapping.items())]


def _format_quality_summary_text(payload: dict[str, Any]) -> str:
    run = payload["run_identity"]
    run_health = payload.get("run_health", {}) if isinstance(payload.get("run_health"), dict) else {}
    slate = payload["slate_provider_counts"]
    player_coverage = payload.get("player_baseline_coverage", {})
    funnel = payload["candidate_funnel"]
    market_coverage = payload.get("market_coverage", {})
    kelly = payload["kelly_safety_summary"]
    elite_context_safety = payload.get("elite_context_safety_gate", {})
    high_caution_over_watchlist = payload.get("high_caution_over_watchlist", {})
    exposure = payload["risk_exposure_summary"]
    movement = payload["board_movement_summary"]
    isolation = payload["date_isolation_check"]
    warnings = payload.get("warnings", [])

    lines = [
        f"Quality Summary - {run['prediction_date']}",
        "=" * 72,
        "",
        "Run Identity",
        "-" * 72,
        f"- prediction_date: {run['prediction_date']}",
        f"- generated_at: {run['generated_at']}",
        f"- output_directory: {run['output_directory']}",
        f"- commit_hash: {run['commit_hash']}",
        f"- run_data_mode: {run['run_data_mode']}",
        "",
        "Run Health",
        "-" * 72,
        f"- status: {run_health.get('status') or payload.get('run_health_status', 'unknown')}",
        f"- reason: {run_health.get('reason') or payload.get('run_health_reason', 'unknown')}",
        "- flags:",
    ]
    health_flags = (
        run_health.get("flags")
        if isinstance(run_health.get("flags"), list)
        else payload.get("run_health_flags", [])
    )
    if health_flags:
        for flag in health_flags:
            lines.append(f"  - {flag}")
    else:
        lines.append("  - none")
    lines.extend(
        [
            f"- recommendation: {run_health.get('recommendation') or payload.get('run_health_recommendation', 'unknown')}",
            "",
            "Slate / Provider Counts",
            "-" * 72,
            f"- games_count: {_fmt_value(slate.get('games_count'))}",
            f"- raw_odds_rows_count: {_fmt_value(slate.get('raw_odds_rows_count'))}",
            f"- normalized_odds_rows_count: {_fmt_value(slate.get('normalized_odds_rows_count'))}",
            f"- live_odds_count: {_fmt_value(slate.get('live_odds_count'))}",
            f"- synthetic_or_fallback_odds_count: {_fmt_value(slate.get('synthetic_or_fallback_odds_count'))}",
            "- provider_breakdown:",
        ]
    )
    provider_breakdown = slate.get("provider_breakdown", {})
    if provider_breakdown:
        for column, counts in provider_breakdown.items():
            lines.append(f"  - {column}:")
            for key, count in sorted(counts.items()):
                lines.append(f"    - {key}: {count}")
    else:
        lines.append("  - none")

    lines.extend(
        [
            "",
            "Player / Baseline Coverage",
            "-" * 72,
            f"- player_predictions_rows: {_fmt_value(player_coverage.get('player_predictions_rows'))}",
            f"- baseline_rows: {_fmt_value(player_coverage.get('baseline_rows'))}",
            f"- unique_baseline_players: {_fmt_value(player_coverage.get('unique_baseline_players'))}",
            f"- baseline_universe_player_count: {_fmt_value(player_coverage.get('baseline_universe_player_count'))}",
            f"- active_team_baseline_players: {_fmt_value(player_coverage.get('active_team_baseline_players'))}",
            f"- odds_unique_players: {_fmt_value(player_coverage.get('odds_unique_players'))}",
            f"- odds_baseline_matched_players: {_fmt_value(player_coverage.get('odds_baseline_matched_players'))}",
            f"- active_board_match_rate: {_fmt_value(player_coverage.get('active_board_match_rate'))}",
            f"- full_market_unique_players: {_fmt_value(player_coverage.get('full_market_unique_players'))}",
            f"- elite_unique_players: {_fmt_value(player_coverage.get('elite_unique_players'))}",
            f"- kelly_unique_players: {_fmt_value(player_coverage.get('kelly_unique_players'))}",
            f"- players_or_baselines_count_legacy_alias: {_fmt_value(player_coverage.get('players_or_baselines_count'))}",
            f"- legacy_alias_definition: {_fmt_value(player_coverage.get('players_or_baselines_count_legacy_definition'))}",
        ]
    )

    lines.extend(
        [
            "",
            "Candidate Funnel",
            "-" * 72,
            f"- raw_candidates_count: {funnel['raw_candidates_count']}",
            f"- rejected_candidates_count: {funnel['rejected_candidates_count']}",
            f"- full_market_board_count: {funnel['full_market_board_count']}",
            f"- elite_board_count: {funnel['elite_board_count']}",
            f"- sgp_board_count: {funnel['sgp_board_count']}",
            f"- kelly_rows_count: {funnel['kelly_rows_count']}",
            "",
            "Top Rejection Reasons",
            "-" * 72,
        ]
    )
    if payload["top_rejection_reasons"]:
        for row in payload["top_rejection_reasons"]:
            lines.append(f"- {row['reason']}: {row['count']} ({row['percentage']:.2f}%)")
    else:
        lines.append("- none available")

    lines.extend(
        [
            "",
            "Elite Context Safety Gate",
            "-" * 72,
            f"- rejection reason: {elite_context_safety.get('rejection_reason', ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER)}",
            "- full-market high-caution conflicted OVER count: "
            f"{elite_context_safety.get('full_market_high_caution_conflicted_over_count', 0)}",
            "- rejected from final elite count: "
            f"{elite_context_safety.get('rejected_from_final_elite_count', 0)}",
            "- elite high-caution conflicted OVER count: "
            f"{elite_context_safety.get('elite_high_caution_conflicted_over_count', 0)}",
            "- final Elite context clean: "
            f"{elite_context_safety.get('final_elite_context_clean', False)}",
            "- full-market context conflict causes:",
            *_format_mapping(
                elite_context_safety.get("full_market_context_conflict_cause_counts", {}),
                indent="  - ",
            ),
            f"- status: {elite_context_safety.get('status', 'unknown')}",
        ]
    )

    lines.extend(
        [
            "",
            "High-Caution OVER Watchlist",
            "-" * 72,
            f"- path: {high_caution_over_watchlist.get('path', 'unavailable')}",
            f"- row count: {high_caution_over_watchlist.get('row_count', 0)}",
            f"- observation_only: {high_caution_over_watchlist.get('observation_only', True)}",
            f"- note: {high_caution_over_watchlist.get('note', OBSERVATION_ONLY_NOTE)}",
        ]
    )

    lines.extend(
        [
            "",
            "Market Coverage",
            "-" * 72,
            "- market | normalized | raw_candidates | full_market | elite | rejected | kelly | eligible | skipped | high_over_skip | dampened",
        ]
    )
    market_rows = market_coverage.get("rows", []) if isinstance(market_coverage, dict) else []
    if market_rows:
        for row in market_rows:
            raw_candidates = row.get("raw_candidates_count")
            lines.append(
                "- "
                f"{row['market']} | "
                f"{row['normalized_odds_rows']} | "
                f"{_fmt_value(raw_candidates)} | "
                f"{row['full_market_board_count']} | "
                f"{row['elite_board_count']} | "
                f"{row['rejected_count']} | "
                f"{row['kelly_rows_count']} | "
                f"{row['kelly_eligible_count']} | "
                f"{row['skipped_count']} | "
                f"{row['high_caution_over_skip_count']} | "
                f"{row['medium_neutral_over_dampened_count']}"
            )
    else:
        lines.append("- none available")

    lines.extend(["", "Rejection Reasons By Market", "-" * 72])
    rejection_rows = (
        market_coverage.get("rejection_reasons_by_market", [])
        if isinstance(market_coverage, dict)
        else []
    )
    if rejection_rows:
        for row in rejection_rows:
            lines.append(
                f"- {row['market']} / {row['rejection_reason']}: "
                f"{row['count']} ({row['percentage']:.2f}%)"
            )
    else:
        lines.append("- none available")

    lines.extend(["", "Coverage Warnings", "-" * 72])
    coverage_warnings = market_coverage.get("coverage_warnings", []) if isinstance(market_coverage, dict) else []
    if coverage_warnings:
        for warning in coverage_warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Kelly Safety Summary",
            "-" * 72,
            f"- total Kelly rows: {kelly['total_rows']}",
            f"- kelly_eligible count: {kelly['kelly_eligible_count']}",
            f"- skipped count: {kelly['skipped_count']}",
            f"- total stake: ${kelly['total_stake']:.2f}",
            f"- total expected value: ${kelly['total_expected_value']:.2f}",
            f"- context_high_caution_over skip count: {kelly['context_high_caution_over_skip_count']}",
            f"- medium_neutral_over_dampener count: {kelly['medium_neutral_over_dampened_count']}",
            f"- total stake reduction from dampeners: ${kelly['total_stake_reduction_from_dampeners']:.2f}",
            "",
            "Risk / Exposure Summary",
            "-" * 72,
            f"- max player exposure: ${exposure['max_player_exposure']:.2f}",
            f"- max team exposure: ${exposure['max_team_exposure']:.2f}",
            f"- max game exposure: ${exposure['max_game_exposure']:.2f}",
            "- exposure by player:",
        ]
    )
    lines.extend(f"  {line}" for line in _format_mapping(exposure.get("by_player", {})))
    lines.append("- exposure by team:")
    lines.extend(f"  {line}" for line in _format_mapping(exposure.get("by_team", {})))
    lines.append("- exposure by game:")
    lines.extend(f"  {line}" for line in _format_mapping(exposure.get("by_game", {})))
    if exposure.get("warnings"):
        lines.append("- exposure warnings:")
        for warning in exposure["warnings"]:
            lines.append(f"  - {warning}")

    lines.extend(["", "Board Movement Summary", "-" * 72])
    if movement.get("comparison_available"):
        lines.append(f"- previous elite row count: {_fmt_value(movement.get('previous_elite_row_count'))}")
        lines.append(f"- current elite row count: {movement.get('current_elite_row_count')}")
        lines.append(f"- elite row count difference: {_fmt_value(movement.get('elite_row_count_diff'))}")
        lines.append(f"- previous Kelly row count: {_fmt_value(movement.get('previous_kelly_row_count'))}")
        lines.append(f"- current Kelly row count: {movement.get('current_kelly_row_count')}")
        lines.append(f"- Kelly row count difference: {_fmt_value(movement.get('kelly_row_count_diff'))}")
    else:
        lines.append(f"- {movement.get('note', 'Comparison unavailable.')}")

    lines.extend(
        [
            "",
            "Date Isolation Check",
            "-" * 72,
            f"- requested prediction date: {isolation['requested_prediction_date']}",
            f"- status: {isolation['status']}",
            "- prediction artifacts written for this date:",
        ]
    )
    for path in isolation.get("prediction_artifacts", []):
        lines.append(f"  - {path}")
    if not isolation.get("prediction_artifacts"):
        lines.append("  - none found")
    if isolation.get("mismatched_artifacts"):
        lines.append("- mismatched artifacts:")
        for row in isolation["mismatched_artifacts"]:
            lines.append(f"  - artifact_date={row['artifact_date']} path={row['path']}")

    lines.extend(["", "Warnings", "-" * 72])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def write_quality_summary_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    out_dir: str | Path | None = None,
    generated_at: str | None = None,
    extra_prediction_artifact_paths: Sequence[str | Path] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    write_high_caution_over_watchlist(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    text, payload = build_quality_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=out_dir,
        generated_at=generated_at,
        extra_prediction_artifact_paths=extra_prediction_artifact_paths,
    )
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)
    text_path = operator_dir / f"quality_summary_{prediction_date}.txt"
    json_path = operator_dir / f"quality_summary_{prediction_date}.json"
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    update_quality_history_from_summary(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    return text_path, json_path, payload


__all__ = [
    "QUALITY_HISTORY_COLUMNS",
    "QUALITY_HISTORY_CSV_NAME",
    "QUALITY_HISTORY_JSONL_NAME",
    "build_quality_summary",
    "update_quality_history_from_summary",
    "write_quality_summary_outputs",
]
