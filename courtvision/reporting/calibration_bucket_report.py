"""Calibration bucket diagnostics.

Report-only analytics over historical shadow/pick rows. This module does not
change projections, selection logic, Elite gates, Kelly sizing, bankroll, or
final decisions.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.calibration.buckets import abs_edge_bucket, odds_bucket, quality_band
from courtvision.reporting.shadow_artifact_metadata import apply_shadow_report_metadata


REPORT_VERSION = "1.0"
GENERATED_BY = "courtvision.reporting.calibration_bucket_report.write_calibration_bucket_report"
DIAGNOSTIC_ONLY_NOTE = (
    "Calibration Bucket Report is diagnostic only and is not an Elite/Kelly input."
)

BUCKET_DIMENSIONS: tuple[str, ...] = (
    "market_type",
    "selection",
    "confidence_bucket",
    "edge_bucket",
    "abs_edge_bucket",
    "odds_bucket",
    "quality_bucket",
    "clv_grade",
    "close_coverage_status",
    "movement_toward_pick",
    "context_pick_alignment",
    "context_caution_level",
    "same_opponent_under_warning",
    "high_caution_over",
)

REPORT_FIELDS: tuple[str, ...] = (
    "prediction_date",
    "market_type",
    "selection",
    "bucket_dimension",
    "bucket_value",
    "n",
    "hits",
    "misses",
    "pushes",
    "pending",
    "void",
    "unsupported",
    "graded_n",
    "hit_rate",
    "avg_confidence",
    "calibration_gap",
    "brier_score",
    "avg_edge",
    "avg_quality_score",
    "avg_clv_line_points",
    "positive_clv_rate",
    "sample_status",
    "coverage_status",
    "readiness",
)

_HIT_MISS_STATUSES = frozenset({"hit", "miss"})
_KNOWN_STATUSES = frozenset({"hit", "miss", "push", "pending", "void", "unsupported"})


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "<na>"}


def _safe_text(value: Any, default: str = "") -> str:
    if _is_missing(value):
        return default
    return str(value).strip()


def _safe_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _probability(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    if number < 0.0 or number > 1.0:
        return None
    return float(number)


def _bool_bucket(value: Any, *, missing: str = "unknown") -> str:
    if _is_missing(value):
        return missing
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return "true"
    if text in {"false", "0", "no", "n"}:
        return "false"
    return text or missing


def _status(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in _KNOWN_STATUSES:
        return text
    return "pending" if not text else text


def _market_type(row: pd.Series) -> str:
    return (
        _safe_text(row.get("market_type"))
        or _safe_text(row.get("market"))
        or _safe_text(row.get("prop_type"))
        or "unknown"
    ).lower().replace(" ", "_")


def _selection(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side")) or "unknown").lower()


def _edge_bucket(value: Any) -> str:
    edge = _safe_float(value)
    if edge is None:
        return "unknown"
    if edge <= -5.0:
        return "negative_5_plus"
    if edge <= -3.0:
        return "negative_3_to_5"
    if edge <= -1.0:
        return "negative_1_to_3"
    if edge < 1.0:
        return "between_-1_and_1"
    if edge < 3.0:
        return "positive_1_to_3"
    if edge < 5.0:
        return "positive_3_to_5"
    return "positive_5_plus"


def _confidence_bucket(value: Any) -> str:
    confidence = _probability(value)
    if confidence is None:
        return "unknown"
    if confidence < 0.55:
        return "<0.55"
    if confidence < 0.60:
        return "0.55-0.60"
    if confidence < 0.70:
        return "0.60-0.70"
    if confidence < 0.80:
        return "0.70-0.80"
    return "0.80+"


def _clv_grade(value: Any) -> str:
    text = _safe_text(value).lower()
    if text:
        return text
    return "missing"


def _coverage_status(value: Any) -> str:
    text = _safe_text(value).lower()
    if text:
        return text
    return "missing"


def _high_caution_over_bucket(row: pd.Series) -> str:
    for column in (
        "high_caution_over",
        "high_caution_over_flag",
        "context_high_caution_over",
        "is_high_caution_over",
    ):
        if column in row.index and not _is_missing(row.get(column)):
            return _bool_bucket(row.get(column))
    reason_text = " ".join(
        _safe_text(row.get(column)).lower()
        for column in (
            "final_elite_rejection_reason",
            "qualification_reason",
            "rejection_reason",
            "context_warning_flags",
            "warning_flags",
        )
        if column in row.index
    )
    if "high_caution_over" in reason_text or "elite_reject_context_high_caution_over" in reason_text:
        return "true"
    return "unavailable"


def _normalise_history_frame(df: pd.DataFrame | None, *, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for _idx, row in df.iterrows():
        market = _market_type(row)
        selection = _selection(row)
        line_value = row.get("line") if "line" in row.index else row.get("sportsbook_line")
        rows.append(
            {
                "history_source": source,
                "prediction_date": _safe_text(row.get("prediction_date")),
                "player_name": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")),
                "player_id": _safe_text(row.get("player_id")),
                "game_id": _safe_text(row.get("game_id")),
                "market_type": market,
                "selection": selection,
                "line": line_value,
                "result_status": _status(row.get("result_status")),
                "confidence": _probability(row.get("confidence")),
                "edge": _safe_float(row.get("edge")),
                "quality_score": _safe_float(row.get("quality_score")),
                "odds": row.get("odds") if "odds" in row.index else row.get("american_odds"),
                "clv_line_points": _safe_float(row.get("clv_line_points")),
                "clv_grade": _clv_grade(row.get("clv_grade")),
                "close_coverage_status": _coverage_status(row.get("close_coverage_status")),
                "movement_toward_pick": _bool_bucket(row.get("movement_toward_pick"), missing="unknown"),
                "context_pick_alignment": _safe_text(row.get("context_pick_alignment"), "unknown").lower(),
                "context_caution_level": _safe_text(row.get("context_caution_level"), "unknown").lower(),
                "same_opponent_under_warning": _bool_bucket(
                    row.get("same_opponent_under_warning"),
                    missing="unknown",
                ),
                "high_caution_over": _high_caution_over_bucket(row),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["_dedupe_key"] = result.apply(
        lambda row: "|".join(
            [
                _safe_text(row.get("prediction_date")).lower(),
                _safe_text(row.get("player_id")).lower(),
                _safe_text(row.get("player_name")).lower(),
                _safe_text(row.get("game_id")).lower(),
                _safe_text(row.get("market_type")).lower(),
                _safe_text(row.get("selection")).lower(),
                str(_safe_float(row.get("line"))),
            ]
        ),
        axis=1,
    )
    return result


def _combined_history(
    shadow_history_df: pd.DataFrame | None,
    pick_history_df: pd.DataFrame | None,
) -> pd.DataFrame:
    shadow = _normalise_history_frame(shadow_history_df, source="market_shadow_history")
    picks = _normalise_history_frame(pick_history_df, source="pick_history")
    if shadow.empty:
        combined = picks.copy()
    elif picks.empty:
        combined = shadow.copy()
    else:
        shadow_keys = set(shadow["_dedupe_key"].astype(str))
        picks = picks[~picks["_dedupe_key"].astype(str).isin(shadow_keys)].copy()
        combined = pd.concat([shadow, picks], ignore_index=True)
    if combined.empty:
        return combined
    return combined.drop(columns=["_dedupe_key"], errors="ignore")


def _sample_status(n: int) -> str:
    if n < 10:
        return "tiny_sample"
    if n < 30:
        return "small_sample"
    if n < 100:
        return "developing_sample"
    return "mature_sample"


def _mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.mean()), 6)


def _coverage_status_for_bucket(df: pd.DataFrame, bucket_dimension: str, graded_n: int) -> str:
    if df.empty:
        return "no_rows"
    if graded_n <= 0:
        return "no_graded_results"
    if bucket_dimension in {"clv_grade", "close_coverage_status", "movement_toward_pick"}:
        clv_populated = int(pd.to_numeric(df.get("clv_line_points", pd.Series(dtype=float)), errors="coerce").notna().sum())
        if clv_populated <= 0:
            return "missing_clv"
        if clv_populated < len(df):
            return "partial_clv_coverage"
    confidence_populated = int(pd.to_numeric(df.get("confidence", pd.Series(dtype=float)), errors="coerce").notna().sum())
    if confidence_populated <= 0:
        return "missing_confidence"
    if confidence_populated < len(df):
        return "partial_confidence"
    return "covered"


def _readiness(graded_n: int, calibration_gap: float | None) -> str:
    if graded_n < 10:
        return "insufficient_sample"
    if calibration_gap is not None and calibration_gap <= -0.10:
        return "overconfident_watch"
    if calibration_gap is not None and graded_n >= 30 and abs(calibration_gap) <= 0.05:
        return "calibrated_observation"
    return "monitor"


def _bucket_value(df: pd.DataFrame, dimension: str) -> pd.Series:
    if dimension == "market_type":
        return df["market_type"].fillna("unknown").astype(str)
    if dimension == "selection":
        return df["selection"].fillna("unknown").astype(str)
    if dimension == "confidence_bucket":
        return df["confidence"].map(_confidence_bucket)
    if dimension == "edge_bucket":
        return df["edge"].map(_edge_bucket)
    if dimension == "abs_edge_bucket":
        return df["edge"].map(abs_edge_bucket)
    if dimension == "odds_bucket":
        return df["odds"].map(odds_bucket)
    if dimension == "quality_bucket":
        return df["quality_score"].map(quality_band)
    if dimension == "clv_grade":
        return df["clv_grade"].fillna("missing").astype(str)
    if dimension == "close_coverage_status":
        return df["close_coverage_status"].fillna("missing").astype(str)
    if dimension == "movement_toward_pick":
        return df["movement_toward_pick"].fillna("unknown").astype(str)
    if dimension == "context_pick_alignment":
        return df["context_pick_alignment"].fillna("unknown").astype(str)
    if dimension == "context_caution_level":
        return df["context_caution_level"].fillna("unknown").astype(str)
    if dimension == "same_opponent_under_warning":
        return df["same_opponent_under_warning"].fillna("unknown").astype(str)
    if dimension == "high_caution_over":
        return df["high_caution_over"].fillna("unavailable").astype(str)
    return pd.Series(["unknown"] * len(df), index=df.index)


def _metric_row(
    *,
    prediction_date: str,
    market_type: str,
    selection: str,
    bucket_dimension: str,
    bucket_value: str,
    segment: pd.DataFrame,
) -> dict[str, Any]:
    statuses = segment["result_status"].fillna("pending").astype(str).str.lower()
    hits = int((statuses == "hit").sum())
    misses = int((statuses == "miss").sum())
    pushes = int((statuses == "push").sum())
    pending = int((statuses == "pending").sum())
    void = int((statuses == "void").sum())
    unsupported = int((statuses == "unsupported").sum())
    graded = segment[statuses.isin(_HIT_MISS_STATUSES)].copy()
    graded_n = hits + misses
    hit_rate = round(hits / graded_n, 6) if graded_n else None
    avg_confidence = _mean(graded["confidence"]) if graded_n else None
    calibration_gap = (
        round(hit_rate - avg_confidence, 6)
        if hit_rate is not None and avg_confidence is not None
        else None
    )

    brier_score: float | None = None
    if graded_n and "confidence" in graded.columns:
        brier_terms: list[float] = []
        for _idx, row in graded.iterrows():
            probability = _probability(row.get("confidence"))
            if probability is None:
                continue
            outcome = 1.0 if _status(row.get("result_status")) == "hit" else 0.0
            brier_terms.append((probability - outcome) ** 2)
        if brier_terms:
            brier_score = round(sum(brier_terms) / len(brier_terms), 6)

    clv_values = pd.to_numeric(segment.get("clv_line_points", pd.Series(dtype=float)), errors="coerce").dropna()
    positive_clv_rate = (
        round(float((clv_values > 0).sum() / len(clv_values)), 6)
        if len(clv_values) > 0
        else None
    )
    sample_status = _sample_status(int(len(segment)))
    coverage_status = _coverage_status_for_bucket(segment, bucket_dimension, graded_n)
    readiness = _readiness(graded_n, calibration_gap)

    return {
        "prediction_date": prediction_date,
        "market_type": market_type,
        "selection": selection,
        "bucket_dimension": bucket_dimension,
        "bucket_value": bucket_value,
        "n": int(len(segment)),
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "pending": pending,
        "void": void,
        "unsupported": unsupported,
        "graded_n": graded_n,
        "hit_rate": hit_rate,
        "avg_confidence": avg_confidence,
        "calibration_gap": calibration_gap,
        "brier_score": brier_score,
        "avg_edge": _mean(segment["edge"]),
        "avg_quality_score": _mean(segment["quality_score"]),
        "avg_clv_line_points": _mean(segment["clv_line_points"]),
        "positive_clv_rate": positive_clv_rate,
        "sample_status": sample_status,
        "coverage_status": coverage_status,
        "readiness": readiness,
    }


def _build_rows(df: pd.DataFrame, prediction_date: str) -> list[dict[str, Any]]:
    if df.empty:
        return []

    rows: list[dict[str, Any]] = []
    base = df.copy()
    for dimension in BUCKET_DIMENSIONS:
        working = base.copy()
        working["bucket_value"] = _bucket_value(working, dimension)
        group_columns = ["market_type", "selection", "bucket_value"]
        for group_values, segment in working.groupby(group_columns, sort=True, dropna=False):
            market_type, selection, bucket_value = [str(value) for value in group_values]
            rows.append(
                _metric_row(
                    prediction_date=prediction_date,
                    market_type=market_type,
                    selection=selection,
                    bucket_dimension=dimension,
                    bucket_value=bucket_value,
                    segment=segment,
                )
            )
    return sorted(
        rows,
        key=lambda row: (
            str(row["bucket_dimension"]),
            str(row["market_type"]),
            str(row["selection"]),
            str(row["bucket_value"]),
        ),
    )


def _compact_bucket_label(row: dict[str, Any] | None) -> str:
    if not row:
        return "n/a"
    return (
        f"{row.get('bucket_dimension')}={row.get('bucket_value')} "
        f"market={row.get('market_type')} side={row.get('selection')} "
        f"graded_n={row.get('graded_n')} gap={_fmt_num(row.get('calibration_gap'))}"
    )


def _summary(rows: list[dict[str, Any]], source_df: pd.DataFrame) -> dict[str, Any]:
    statuses = source_df["result_status"].fillna("pending").astype(str).str.lower() if not source_df.empty else pd.Series(dtype=str)
    total_graded_rows = int(statuses.isin(_HIT_MISS_STATUSES).sum())
    eligible_rows = [row for row in rows if int(row.get("graded_n") or 0) >= 10 and row.get("calibration_gap") is not None]
    overconfident = sorted(
        eligible_rows,
        key=lambda row: (float(row.get("calibration_gap") or 0), -int(row.get("graded_n") or 0)),
    )
    calibrated = sorted(
        eligible_rows,
        key=lambda row: (abs(float(row.get("calibration_gap") or 0)), -int(row.get("graded_n") or 0)),
    )
    tiny_small = [
        row for row in rows
        if row.get("sample_status") in {"tiny_sample", "small_sample"}
    ]
    return {
        "total_rows": int(len(source_df)),
        "total_graded_rows_used": total_graded_rows,
        "bucket_row_count": len(rows),
        "worst_overconfident_bucket": overconfident[0] if overconfident else None,
        "worst_overconfident_bucket_label": _compact_bucket_label(overconfident[0] if overconfident else None),
        "best_calibrated_bucket": calibrated[0] if calibrated else None,
        "best_calibrated_bucket_label": _compact_bucket_label(calibrated[0] if calibrated else None),
        "tiny_small_sample_warning_count": len(tiny_small),
        "readiness": "review_only",
        "note": DIAGNOSTIC_ONLY_NOTE,
    }


def build_calibration_bucket_report(
    *,
    prediction_date: str,
    shadow_history_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build calibration bucket diagnostics from historical rows."""
    combined = _combined_history(shadow_history_df, pick_history_df)
    rows = _build_rows(combined, prediction_date)
    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "calibration_bucket_report_shadow",
        "notes": [
            "diagnostic_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            "no_final_decision_changed",
            DIAGNOSTIC_ONLY_NOTE,
        ],
        "bucket_dimensions": list(BUCKET_DIMENSIONS),
        "summary": _summary(rows, combined),
        "rows": rows,
    }


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _fmt_num(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.4f}"


def render_calibration_bucket_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(rows, list):
        rows = []

    lines = [
        "Calibration Bucket Report - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        f"total graded rows used: {summary.get('total_graded_rows_used', 0)}",
        f"worst overconfident bucket: {summary.get('worst_overconfident_bucket_label', 'n/a')}",
        f"best calibrated bucket: {summary.get('best_calibrated_bucket_label', 'n/a')}",
        f"tiny/small sample warning count: {summary.get('tiny_small_sample_warning_count', 0)}",
        DIAGNOSTIC_ONLY_NOTE,
        "",
        "Bucket Rows",
        "-" * 72,
    ]
    if not rows:
        lines.append("n/a")
    else:
        lines.append(
            "dimension | bucket | market | side | n | graded | hit_rate | "
            "avg_conf | gap | brier | readiness"
        )
        for row in rows[:100]:
            lines.append(
                " | ".join(
                    [
                        _safe_text(row.get("bucket_dimension"), "unknown"),
                        _safe_text(row.get("bucket_value"), "unknown"),
                        _safe_text(row.get("market_type"), "unknown"),
                        _safe_text(row.get("selection"), "unknown"),
                        str(row.get("n", 0)),
                        str(row.get("graded_n", 0)),
                        _fmt_pct(row.get("hit_rate")),
                        _fmt_pct(row.get("avg_confidence")),
                        _fmt_num(row.get("calibration_gap")),
                        _fmt_num(row.get("brier_score")),
                        _safe_text(row.get("readiness"), "review_only"),
                    ]
                )
            )
        if len(rows) > 100:
            lines.append(f"... {len(rows) - 100} additional rows omitted")
    return "\n".join(lines) + "\n"


def calibration_bucket_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"calibration_bucket_report_{date}.json"


def calibration_bucket_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"calibration_bucket_report_{date}.txt"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def write_calibration_bucket_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    shadow_history_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
    generated_at_utc: str | None = None,
    generated_by: str = GENERATED_BY,
    source_runtime_root: str | Path | None = None,
    source_history_root: str | Path | None = None,
    report_name: str = "calibration_bucket_report",
    orchestrator_run_id: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write JSON and operator TXT diagnostics."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    if shadow_history_df is None:
        shadow_history_df = _read_csv(history_root / "market_shadow_history.csv")
    if pick_history_df is None:
        pick_history_df = _read_csv(history_root / "pick_history.csv")

    payload = build_calibration_bucket_report(
        prediction_date=prediction_date,
        shadow_history_df=shadow_history_df,
        pick_history_df=pick_history_df,
    )
    payload = apply_shadow_report_metadata(
        payload,
        prediction_date=prediction_date,
        generated_at_utc=generated_at_utc,
        generated_by=generated_by,
        source_runtime_root=source_runtime_root or runtime_root,
        source_history_root=source_history_root or history_root,
        report_name=report_name,
        orchestrator_run_id=orchestrator_run_id,
    )
    json_path = calibration_bucket_json_path_for_date(prediction_date, runtime_root)
    txt_path = calibration_bucket_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_calibration_bucket_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "BUCKET_DIMENSIONS",
    "DIAGNOSTIC_ONLY_NOTE",
    "REPORT_FIELDS",
    "REPORT_VERSION",
    "build_calibration_bucket_report",
    "calibration_bucket_json_path_for_date",
    "calibration_bucket_txt_path_for_date",
    "render_calibration_bucket_report",
    "write_calibration_bucket_report",
]
