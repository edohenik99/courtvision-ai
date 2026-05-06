from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

COMBO_MARKETS: frozenset[str] = frozenset(
    {
        "player_points_assists",
        "player_points_rebounds",
        "player_points_rebounds_assists",
    }
)
REPORT_FILE_PREFIX = "promotion_readiness_report"
OBSERVATION_ONLY_NOTE = "Observation only; no market becomes Kelly eligible from this report."

REPORT_COLUMNS: tuple[str, ...] = (
    "market_type",
    "selection",
    "context_pick_alignment",
    "context_caution_level",
    "total",
    "graded_total",
    "hits",
    "misses",
    "hit_rate",
    "roi",
    "sample_status",
    "calibration_eligible",
    "promotion_status",
    "promotion_reason",
)

GROUP_COLUMNS: tuple[str, ...] = (
    "market_type",
    "selection",
    "context_pick_alignment",
    "context_caution_level",
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
    if not text or text.lower() in {"nan", "none", "null"}:
        return default
    return text


def _norm_series(df: pd.DataFrame, column: str, default: str = "unknown") -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index)
    series = df[column].fillna("").astype(str).str.strip().str.lower()
    return series.mask(series.isin(["", "nan", "none", "null"]), default)


def _truthy_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return df[column].fillna("").astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _sample_status(graded_total: int) -> str:
    if graded_total <= 0:
        return "no_graded_results"
    if graded_total < 20:
        return "insufficient_sample"
    if graded_total < 30:
        return "watch_sample"
    return "usable_sample"


def _is_combo_market(market_type: str) -> bool:
    return _safe_text(market_type).lower() in COMBO_MARKETS


def _promotion_decision(
    *,
    market_type: str,
    selection: str,
    context_pick_alignment: str,
    context_caution_level: str,
    graded_total: int,
    hit_rate: float | None,
) -> tuple[str, str]:
    market_norm = _safe_text(market_type).lower()
    selection_norm = _safe_text(selection).lower()
    alignment_norm = _safe_text(context_pick_alignment).lower()
    caution_norm = _safe_text(context_caution_level).lower()

    if _is_combo_market(market_norm) and selection_norm == "over":
        return "blocked_combo_over_review_required", "combo_over_requires_explicit_review"
    if graded_total < 20:
        return "blocked_insufficient_sample", "graded_total_lt_20"
    if (
        _is_combo_market(market_norm)
        and selection_norm == "under"
        and alignment_norm == "aligned"
        and caution_norm == "low"
    ):
        return "near_candidate", "combo_under_aligned_low_with_minimum_sample"
    if graded_total < 30:
        return "watch_only", "graded_total_20_to_29"
    if hit_rate is not None and hit_rate >= 0.55:
        return "candidate_for_review", "graded_total_gte_30_and_hit_rate_gte_0.55"
    return "blocked_low_hit_rate", "hit_rate_below_0.55"


def _promotion_rank(status: str) -> int:
    return {
        "candidate_for_review": 0,
        "near_candidate": 1,
        "watch_only": 2,
        "blocked_low_hit_rate": 3,
        "blocked_combo_over_review_required": 4,
        "blocked_insufficient_sample": 5,
    }.get(status, 9)


def _normalize_history_df(history_df: pd.DataFrame, through_date: str | None = None) -> pd.DataFrame:
    if not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)
    df = history_df.copy()
    if through_date and "prediction_date" in df.columns:
        df = df[df["prediction_date"].astype(str) <= str(through_date)].copy()
    for column in GROUP_COLUMNS:
        df[column] = _norm_series(df, column)
    result_status = _norm_series(df, "result_status", default="pending")
    hit_flags = result_status.eq("hit") | _truthy_series(df, "hit")
    miss_flags = result_status.eq("miss") | _truthy_series(df, "miss")
    df["_promotion_hit"] = hit_flags
    df["_promotion_miss"] = miss_flags
    if "shadow_roi" in df.columns:
        df["_promotion_roi"] = pd.to_numeric(df["shadow_roi"], errors="coerce")
    else:
        df["_promotion_roi"] = float("nan")
    return df


def build_promotion_readiness_report(
    history_df: pd.DataFrame,
    *,
    through_date: str | None = None,
) -> pd.DataFrame:
    df = _normalize_history_df(history_df, through_date=through_date)
    if df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for group_values, segment in df.groupby(list(GROUP_COLUMNS), sort=True, dropna=False):
        hits = int(segment["_promotion_hit"].sum())
        misses = int(segment["_promotion_miss"].sum())
        graded_total = hits + misses
        hit_rate = float(hits / graded_total) if graded_total else None
        roi_values = segment["_promotion_roi"].dropna()
        roi = float(roi_values.sum() / len(roi_values)) if len(roi_values) else None
        sample_status = _sample_status(graded_total)
        promotion_status, promotion_reason = _promotion_decision(
            market_type=str(group_values[0]),
            selection=str(group_values[1]),
            context_pick_alignment=str(group_values[2]),
            context_caution_level=str(group_values[3]),
            graded_total=graded_total,
            hit_rate=hit_rate,
        )
        calibration_eligible = promotion_status == "candidate_for_review"
        rows.append(
            {
                "market_type": group_values[0],
                "selection": group_values[1],
                "context_pick_alignment": group_values[2],
                "context_caution_level": group_values[3],
                "total": int(len(segment)),
                "graded_total": graded_total,
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hit_rate, 4) if hit_rate is not None else "",
                "roi": round(roi, 4) if roi is not None else "",
                "sample_status": sample_status,
                "calibration_eligible": calibration_eligible,
                "promotion_status": promotion_status,
                "promotion_reason": promotion_reason,
            }
        )

    report = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    report["_promotion_rank"] = report["promotion_status"].map(_promotion_rank)
    report["_hit_rate_sort"] = pd.to_numeric(report["hit_rate"], errors="coerce")
    report = report.sort_values(
        ["_promotion_rank", "graded_total", "_hit_rate_sort", "market_type", "selection"],
        ascending=[True, False, False, True, True],
        na_position="last",
        kind="mergesort",
    ).drop(columns=["_promotion_rank", "_hit_rate_sort"])
    return report.reset_index(drop=True)


def read_market_shadow_history(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    operator_dir = Path(runtime_root) / "operator"
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return operator_dir / f"{stem}.txt", operator_dir / f"{stem}.csv"


def report_row_line(row: pd.Series) -> str:
    market = _safe_text(row.get("market_type"), default="unknown")
    selection = _safe_text(row.get("selection"), default="unknown")
    alignment = _safe_text(row.get("context_pick_alignment"), default="unknown")
    caution = _safe_text(row.get("context_caution_level"), default="unknown")
    hit_rate = _safe_text(row.get("hit_rate"), default="n/a")
    roi = _safe_text(row.get("roi"), default="n/a")
    return (
        f"{market}/{selection}/{alignment}/{caution}: "
        f"graded={int(row.get('graded_total') or 0)}, "
        f"hits={int(row.get('hits') or 0)}, "
        f"misses={int(row.get('misses') or 0)}, "
        f"hit_rate={hit_rate}, roi={roi}, "
        f"status={_safe_text(row.get('promotion_status'), default='unknown')}, "
        f"reason={_safe_text(row.get('promotion_reason'), default='unknown')}"
    )


def render_promotion_readiness_text(
    *,
    prediction_date: str,
    report_df: pd.DataFrame,
    csv_path: Path,
) -> str:
    lines = [
        f"Promotion Readiness Report - {prediction_date}",
        "=" * 72,
        OBSERVATION_ONLY_NOTE,
        f"CSV artifact: {csv_path}",
        "",
        "Grouped Performance",
        "-" * 72,
        f"Rows: {int(len(report_df))}",
    ]
    if report_df.empty:
        lines.append("- None")
    else:
        for _, row in report_df.iterrows():
            lines.append(f"- {report_row_line(row)}")
    return "\n".join(lines) + "\n"


def write_promotion_readiness_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    history_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, pd.DataFrame]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    text_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    source_df = (
        history_df
        if isinstance(history_df, pd.DataFrame)
        else read_market_shadow_history(history_root / "market_shadow_history.csv")
    )
    report_df = build_promotion_readiness_report(source_df, through_date=prediction_date)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(csv_path, index=False)
    text_path.write_text(
        render_promotion_readiness_text(
            prediction_date=prediction_date,
            report_df=report_df,
            csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return text_path, csv_path, report_df


__all__ = [
    "COMBO_MARKETS",
    "OBSERVATION_ONLY_NOTE",
    "REPORT_COLUMNS",
    "build_promotion_readiness_report",
    "read_market_shadow_history",
    "render_promotion_readiness_text",
    "report_paths_for_date",
    "report_row_line",
    "write_promotion_readiness_report",
]
