"""Phase 15F low-line OVER minutes guard policy simulation.

Simulation-only diagnostics that estimate historical counterfactual outcomes
for low-line player_points OVER minutes-basis guard policies. This module never
changes prediction, grading, Kelly, suppression, or history state.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from courtvision.reporting.low_line_over_minutes_guard_outcome import (
    DEFAULT_RUNTIME_ROOT,
    HIT_RATE_STATUSES,
    TERMINAL_STATUSES,
    build_low_line_over_minutes_guard_outcome,
    low_line_over_minutes_guard_outcome_csv_path_for_date,
    _bucket_for_minutes_basis,
    _normalize_result,
    _read_csv,
    _row_roi,
    _safe_text,
    _to_float,
)


MIN_SUPPRESSED_GRADED_SAMPLE = 30
MEANINGFUL_HIT_RATE_DELTA = 0.05
MEANINGFUL_ROI_DELTA = 0.05
HIGH_MISSED_WINNER_RATE = 0.35

POLICY_NAMES = (
    "review_only_current_policy",
    "suppress_weak_minutes_basis",
    "suppress_weak_and_missing_minutes_basis",
    "reduce_stake_weak_minutes_basis",
)

CSV_COLUMNS = [
    "policy_name",
    "total_candidates",
    "suppressed_rows",
    "kept_rows",
    "stake_reduced_rows",
    "suppressed_graded_rows",
    "kept_graded_rows",
    "suppressed_hits",
    "suppressed_misses",
    "suppressed_pushes",
    "suppressed_voids",
    "saved_losers",
    "missed_winners",
    "net_saved_result_count",
    "kept_hit_rate",
    "suppressed_hit_rate",
    "baseline_hit_rate",
    "hit_rate_delta",
    "baseline_roi",
    "simulated_roi",
    "roi_delta",
    "volume_reduction_pct",
    "stake_volume_reduction_pct",
    "risk_verdict",
]


def low_line_over_minutes_guard_policy_simulation_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"low_line_over_minutes_guard_policy_simulation_{date}.json"


def low_line_over_minutes_guard_policy_simulation_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_policy_simulation_{date}.txt"


def low_line_over_minutes_guard_policy_simulation_csv_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"low_line_over_minutes_guard_policy_simulation_{date}.csv"


def _resolve_history_path(runtime_root: Path, filename: str, explicit: Any) -> Any:
    if explicit is not None:
        return explicit
    if runtime_root.as_posix().replace("\\", "/").endswith("outputs/runtime"):
        return Path("data/history") / filename
    return runtime_root.parent / "history" / filename


def _normalize_outcome_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    working = frame.copy(deep=True)
    for column in (
        "prediction_date",
        "player_name",
        "player_id",
        "market_type",
        "selection",
        "line",
        "minutes_guard_review_bucket",
        "minutes_basis",
        "result_status",
        "row_roi",
    ):
        if column not in working.columns:
            working[column] = pd.NA

    working["market_type"] = working["market_type"].map(lambda value: _safe_text(value).lower())
    working["selection"] = working["selection"].map(lambda value: _safe_text(value).lower())
    working["line"] = working["line"].map(_to_float)
    working["minutes_basis"] = working["minutes_basis"].map(_to_float)
    working["minutes_guard_review_bucket"] = [
        bucket if bucket in {"weak_minutes_basis", "borderline_minutes_basis", "stable_minutes_basis", "missing_minutes_basis"}
        else _bucket_for_minutes_basis(minutes_basis)
        for bucket, minutes_basis in zip(
            working["minutes_guard_review_bucket"].map(_safe_text),
            working["minutes_basis"],
        )
    ]
    working["result_status"] = working["result_status"].map(_normalize_result)
    working["terminal_result"] = working["result_status"].isin(TERMINAL_STATUSES)
    working["hit_rate_eligible"] = working["result_status"].isin(HIT_RATE_STATUSES)
    if "row_roi" not in frame.columns or pd.to_numeric(working["row_roi"], errors="coerce").notna().sum() == 0:
        working["row_roi"] = working.apply(_row_roi, axis=1)
    else:
        working["row_roi"] = working["row_roi"].map(_to_float)

    low_line_mask = (
        working["market_type"].eq("player_points")
        & working["selection"].eq("over")
        & working["line"].map(lambda value: value is not None and value < 15.0)
    )
    return working[low_line_mask].reset_index(drop=True)


def _load_outcome_rows(
    prediction_date: str,
    *,
    runtime_root: Path,
    market_shadow_history: str | Path | pd.DataFrame | None,
    paper_kelly_history: str | Path | pd.DataFrame | None,
    outcome_csv: str | Path | pd.DataFrame | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source_files: list[dict[str, Any]] = []
    sources: list[str | Path | pd.DataFrame] = []
    if outcome_csv is not None:
        sources.append(outcome_csv)
    else:
        sources.append(low_line_over_minutes_guard_outcome_csv_path_for_date(prediction_date, runtime_root))

    for source in sources:
        frame = _read_csv(source)
        if frame.empty:
            continue
        normalized = _normalize_outcome_rows(frame)
        source_files.append(
            {
                "source_type": "low_line_over_minutes_guard_outcome",
                "source_file": "<dataframe>" if isinstance(source, pd.DataFrame) else str(source),
                "rows": int(len(frame)),
            }
        )
        return normalized, source_files

    outcome_payload = build_low_line_over_minutes_guard_outcome(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        paper_kelly_history=paper_kelly_history,
        guard_review_csv=pd.DataFrame(),
    )
    outcome_df = outcome_payload.get("outcome_df")
    source_files.extend(outcome_payload.get("source_files_scanned", []))
    return _normalize_outcome_rows(outcome_df) if isinstance(outcome_df, pd.DataFrame) else pd.DataFrame(), source_files


def _status_counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or "result_status" not in frame.columns:
        return {"graded_rows": 0, "hits": 0, "misses": 0, "pushes": 0, "voids": 0, "pending_rows": 0}
    status = frame["result_status"].map(_normalize_result)
    hits = int(status.eq("hit").sum())
    misses = int(status.eq("miss").sum())
    pushes = int(status.eq("push").sum())
    voids = int(status.eq("void").sum())
    graded = hits + misses + pushes + voids
    return {
        "graded_rows": graded,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "voids": voids,
        "pending_rows": int(len(frame) - graded),
    }


def _hit_rate(frame: pd.DataFrame) -> float | None:
    if frame.empty or "result_status" not in frame.columns:
        return None
    status = frame["result_status"].map(_normalize_result)
    denom = int(status.isin(HIT_RATE_STATUSES).sum())
    if denom <= 0:
        return None
    return round(int(status.eq("hit").sum()) / denom, 4)


def _roi(frame: pd.DataFrame, weights: pd.Series | None = None) -> float | None:
    if frame.empty or "row_roi" not in frame.columns:
        return None
    status = frame["result_status"].map(_normalize_result)
    eligible = status.isin({"hit", "miss", "push"})
    values = pd.to_numeric(frame.loc[eligible, "row_roi"], errors="coerce")
    values = values.dropna()
    if values.empty:
        return None
    if weights is None:
        return round(float(values.mean()), 4)
    aligned_weights = pd.to_numeric(weights.loc[values.index], errors="coerce").fillna(0.0)
    total_weight = float(aligned_weights.sum())
    if total_weight <= 0:
        return None
    return round(float((values * aligned_weights).sum() / total_weight), 4)


def _policy_suppression_mask(frame: pd.DataFrame, policy_name: str) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)
    bucket = frame["minutes_guard_review_bucket"].map(_safe_text)
    if policy_name == "suppress_weak_minutes_basis":
        return bucket.eq("weak_minutes_basis")
    if policy_name == "suppress_weak_and_missing_minutes_basis":
        return bucket.isin({"weak_minutes_basis", "missing_minutes_basis"})
    return pd.Series(False, index=frame.index)


def _policy_stake_multiplier(frame: pd.DataFrame, policy_name: str) -> pd.Series:
    multiplier = pd.Series(1.0, index=frame.index, dtype="float")
    if policy_name == "reduce_stake_weak_minutes_basis" and not frame.empty:
        weak_mask = frame["minutes_guard_review_bucket"].map(_safe_text).eq("weak_minutes_basis")
        multiplier.loc[weak_mask] = 0.5
    if policy_name.startswith("suppress_"):
        multiplier.loc[_policy_suppression_mask(frame, policy_name)] = 0.0
    return multiplier


def _sample_rows(frame: pd.DataFrame) -> dict[str, Any]:
    counts = _status_counts(frame)
    return {
        "rows": int(len(frame)),
        **counts,
        "hit_rate": _hit_rate(frame),
        "roi": _roi(frame),
    }


def select_policy_risk_verdict(
    *,
    policy_name: str,
    suppressed_graded_rows: int,
    suppressed_hits: int,
    suppressed_misses: int,
    hit_rate_delta: float | None,
    roi_delta: float | None,
) -> str:
    if policy_name == "review_only_current_policy":
        return "BASELINE_NO_SUPPRESSION"
    if suppressed_graded_rows < MIN_SUPPRESSED_GRADED_SAMPLE:
        return "INSUFFICIENT_SAMPLE"

    improves_hit_rate = hit_rate_delta is not None and hit_rate_delta >= MEANINGFUL_HIT_RATE_DELTA
    improves_roi = roi_delta is not None and roi_delta >= MEANINGFUL_ROI_DELTA
    harms_hit_rate = hit_rate_delta is not None and hit_rate_delta < 0
    harms_roi = roi_delta is not None and roi_delta < 0
    missed_winner_rate = suppressed_hits / max(suppressed_hits + suppressed_misses, 1)

    if harms_hit_rate or harms_roi or suppressed_hits >= suppressed_misses:
        return "POLICY_SIM_NOT_READY"
    if improves_hit_rate or improves_roi:
        if missed_winner_rate >= HIGH_MISSED_WINNER_RATE:
            return "POLICY_SIM_MIXED"
        return "POLICY_SIM_REVIEW_READY"
    return "POLICY_SIM_NOT_READY"


def _simulate_policy(frame: pd.DataFrame, policy_name: str, baseline: Mapping[str, Any]) -> dict[str, Any]:
    suppressed_mask = _policy_suppression_mask(frame, policy_name)
    if policy_name == "reduce_stake_weak_minutes_basis":
        suppressed_mask = pd.Series(False, index=frame.index)
    suppressed = frame[suppressed_mask].copy()
    kept = frame[~suppressed_mask].copy()
    stake_multiplier = _policy_stake_multiplier(frame, policy_name)
    stake_reduced_rows = int((stake_multiplier.gt(0) & stake_multiplier.lt(1)).sum())

    suppressed_counts = _status_counts(suppressed)
    kept_counts = _status_counts(kept)
    suppressed_hit_rate = _hit_rate(suppressed)
    kept_hit_rate = _hit_rate(kept)
    baseline_hit_rate = baseline.get("hit_rate")
    hit_rate_delta = (
        round(float(kept_hit_rate) - float(baseline_hit_rate), 4)
        if kept_hit_rate is not None and baseline_hit_rate is not None
        else None
    )
    baseline_roi = baseline.get("roi")
    simulated_roi = _roi(kept) if policy_name != "reduce_stake_weak_minutes_basis" else _roi(frame, stake_multiplier)
    roi_delta = (
        round(float(simulated_roi) - float(baseline_roi), 4)
        if simulated_roi is not None and baseline_roi is not None
        else None
    )
    saved_losers = int(suppressed_counts["misses"])
    missed_winners = int(suppressed_counts["hits"])
    risk_verdict = select_policy_risk_verdict(
        policy_name=policy_name,
        suppressed_graded_rows=int(suppressed_counts["graded_rows"]),
        suppressed_hits=missed_winners,
        suppressed_misses=saved_losers,
        hit_rate_delta=hit_rate_delta,
        roi_delta=roi_delta,
    )

    return {
        "policy_name": policy_name,
        "total_candidates": int(len(frame)),
        "suppressed_rows": int(len(suppressed)),
        "kept_rows": int(len(kept)),
        "stake_reduced_rows": stake_reduced_rows,
        "suppressed_graded_rows": int(suppressed_counts["graded_rows"]),
        "kept_graded_rows": int(kept_counts["graded_rows"]),
        "suppressed_hits": missed_winners,
        "suppressed_misses": saved_losers,
        "suppressed_pushes": int(suppressed_counts["pushes"]),
        "suppressed_voids": int(suppressed_counts["voids"]),
        "saved_losers": saved_losers,
        "missed_winners": missed_winners,
        "net_saved_result_count": saved_losers - missed_winners,
        "kept_hit_rate": kept_hit_rate,
        "suppressed_hit_rate": suppressed_hit_rate,
        "baseline_hit_rate": baseline_hit_rate,
        "hit_rate_delta": hit_rate_delta,
        "baseline_roi": baseline_roi,
        "simulated_roi": simulated_roi,
        "roi_delta": roi_delta,
        "volume_reduction_pct": round(len(suppressed) / len(frame), 4) if len(frame) else 0.0,
        "stake_volume_reduction_pct": round(float((1.0 - stake_multiplier).sum() / len(frame)), 4) if len(frame) else 0.0,
        "risk_verdict": risk_verdict,
    }


def _best_policy(policy_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in policy_rows if row.get("policy_name") != "review_only_current_policy"]
    if not candidates:
        return {}
    verdict_rank = {
        "POLICY_SIM_REVIEW_READY": 4,
        "POLICY_SIM_MIXED": 3,
        "POLICY_SIM_NOT_READY": 2,
        "INSUFFICIENT_SAMPLE": 1,
    }

    def score(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
        return (
            float(verdict_rank.get(_safe_text(row.get("risk_verdict")), 0)),
            float(_to_float(row.get("hit_rate_delta")) or 0.0),
            float(_to_float(row.get("roi_delta")) or 0.0),
            float(_to_float(row.get("net_saved_result_count")) or 0.0),
        )

    return sorted(candidates, key=score, reverse=True)[0]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def build_low_line_over_minutes_guard_policy_simulation(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    paper_kelly_history: str | Path | pd.DataFrame | None = None,
    outcome_csv: str | Path | pd.DataFrame | None = None,
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    market_shadow_history = _resolve_history_path(runtime_root, "market_shadow_history.csv", market_shadow_history)
    paper_kelly_history = _resolve_history_path(runtime_root, "paper_kelly_history.csv", paper_kelly_history)
    outcome_df, source_files = _load_outcome_rows(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        paper_kelly_history=paper_kelly_history,
        outcome_csv=outcome_csv,
    )
    baseline = _sample_rows(outcome_df)
    baseline["hit_rate"] = _hit_rate(outcome_df)
    baseline["roi"] = _roi(outcome_df)

    policy_rows = [_simulate_policy(outcome_df, policy_name, baseline) for policy_name in POLICY_NAMES]
    policy_results = {row["policy_name"]: row for row in policy_rows}
    best_policy = _best_policy(policy_rows)
    summary_df = pd.DataFrame(policy_rows).reindex(columns=CSV_COLUMNS)
    payload = {
        "prediction_date": prediction_date,
        "note": "simulation_only_no_prediction_grading_kelly_history_or_suppression_change",
        "policy_names": list(POLICY_NAMES),
        "baseline": baseline,
        "policy_results": policy_results,
        "best_policy": best_policy,
        "best_policy_name": best_policy.get("policy_name"),
        "readiness_verdict": best_policy.get("risk_verdict", "INSUFFICIENT_SAMPLE"),
        "total_candidates": int(len(outcome_df)),
        "history_mutated": False,
        "live_picks_suppressed": False,
        "simulation_df": summary_df,
        "source_files_scanned": source_files,
    }
    serializable = _json_safe({key: value for key, value in payload.items() if key != "simulation_df"})
    serializable["simulation_df"] = summary_df
    return serializable


def _format_pct(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _to_float(value)
    return "n/a" if number is None else f"{number:.4f}"


def _format_txt(payload: Mapping[str, Any], prediction_date: str) -> str:
    sep = "=" * 78
    sep2 = "-" * 78
    policies = payload.get("policy_results", {}) if isinstance(payload.get("policy_results"), Mapping) else {}
    best = payload.get("best_policy", {}) if isinstance(payload.get("best_policy"), Mapping) else {}
    baseline = payload.get("baseline", {}) if isinstance(payload.get("baseline"), Mapping) else {}
    lines = [
        f"{sep}\n",
        "LOW-LINE OVER MINUTES GUARD POLICY SIMULATION (Phase 15F -- SIMULATION ONLY)\n",
        f"date: {prediction_date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",
        "OVERVIEW\n",
        f"{sep2}\n",
        f"  total_candidates      : {payload.get('total_candidates', 0)}\n",
        f"  baseline_hit_rate     : {_format_pct(baseline.get('hit_rate'))}\n",
        f"  baseline_roi          : {_format_pct(baseline.get('roi'))}\n",
        f"  best_policy_name      : {best.get('policy_name')}\n",
        f"  readiness_verdict     : {payload.get('readiness_verdict')}\n\n",
        "POLICIES\n",
        f"{sep2}\n",
    ]
    for policy_name in POLICY_NAMES:
        row = policies.get(policy_name, {}) if isinstance(policies.get(policy_name, {}), Mapping) else {}
        lines.append(
            f"  {policy_name}: suppressed={row.get('suppressed_rows', 0)} "
            f"kept={row.get('kept_rows', 0)} saved_losers={row.get('saved_losers', 0)} "
            f"missed_winners={row.get('missed_winners', 0)} "
            f"net_saved={row.get('net_saved_result_count', 0)} "
            f"kept_hit={_format_pct(row.get('kept_hit_rate'))} "
            f"delta={_format_num(row.get('hit_rate_delta'))} "
            f"verdict={row.get('risk_verdict')}\n"
        )
    lines.extend(
        [
            "\nNOTE: SIMULATION ONLY; no prediction/grading/Kelly/history changes and no picks suppressed.\n",
            f"{sep}\n",
        ]
    )
    return "".join(lines)


def write_low_line_over_minutes_guard_policy_simulation(
    prediction_date: str,
    *,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    market_shadow_history: str | Path | pd.DataFrame | None = None,
    paper_kelly_history: str | Path | pd.DataFrame | None = None,
    outcome_csv: str | Path | pd.DataFrame | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    payload = build_low_line_over_minutes_guard_policy_simulation(
        prediction_date,
        runtime_root=runtime_root,
        market_shadow_history=market_shadow_history,
        paper_kelly_history=paper_kelly_history,
        outcome_csv=outcome_csv,
    )
    json_path = low_line_over_minutes_guard_policy_simulation_json_path_for_date(prediction_date, runtime_root)
    txt_path = low_line_over_minutes_guard_policy_simulation_txt_path_for_date(prediction_date, runtime_root)
    csv_path = low_line_over_minutes_guard_policy_simulation_csv_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    simulation_df = payload.get("simulation_df")
    csv_df = simulation_df if isinstance(simulation_df, pd.DataFrame) else pd.DataFrame(columns=CSV_COLUMNS)
    csv_df.reindex(columns=CSV_COLUMNS).to_csv(csv_path, index=False)

    serializable = {key: value for key, value in payload.items() if key != "simulation_df"}
    json_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(serializable, prediction_date), encoding="utf-8")
    return json_path, txt_path, csv_path, serializable


__all__ = [
    "MIN_SUPPRESSED_GRADED_SAMPLE",
    "POLICY_NAMES",
    "build_low_line_over_minutes_guard_policy_simulation",
    "low_line_over_minutes_guard_policy_simulation_csv_path_for_date",
    "low_line_over_minutes_guard_policy_simulation_json_path_for_date",
    "low_line_over_minutes_guard_policy_simulation_txt_path_for_date",
    "select_policy_risk_verdict",
    "write_low_line_over_minutes_guard_policy_simulation",
]
