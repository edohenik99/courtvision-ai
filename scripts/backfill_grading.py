from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision_ai import FINAL_GRADING_RESULTS, _to_str_dict, _write_grading_outputs
from courtvision.operations import (
    CourtVisionOperations,
    grade_operation_prediction,
    normalize_operation_stats,
    operations_client,
)


UNRESOLVED_RESULT = "unresolved"
UNSUPPORTED_REASON = "unsupported_grading_market"


@dataclass
class DateBackfillStats:
    date: str
    unresolved: int = 0
    gradeable: int = 0
    updated: int = 0
    unsupported: int = 0
    missing_stats: int = 0


@dataclass
class BackfillStats:
    feedback_rows: int = 0
    unresolved_rows: int = 0
    unresolved_unique_keys: int = 0
    dates: int = 0
    total_updated: int = 0
    total_unsupported: int = 0
    total_missing_stats: int = 0
    affected_dates: list[str] = field(default_factory=list)
    by_date: list[DateBackfillStats] = field(default_factory=list)


def _normalize_result_series(df: pd.DataFrame) -> pd.Series:
    result = (
        df.get("result", pd.Series("", index=df.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    if "graded_result" in df.columns:
        graded = df["graded_result"].fillna("").astype(str).str.strip().str.lower()
        result = result.mask(result.eq(""), graded)
    return result


def _grade_key(row: Mapping[str, Any]) -> str:
    prediction_date = row.get("prediction_date_str") or row.get("prediction_date") or ""
    return (
        f"{prediction_date}|{row.get('market_type', '')}|{row.get('entity_name', '')}|"
        f"{row.get('selection', '')}|{row.get('sportsbook_line', '')}"
    )


def _ensure_grade_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "grade_key" not in out.columns:
        out["grade_key"] = ""
    blank = out["grade_key"].fillna("").astype(str).str.strip().eq("")
    if blank.any():
        out.loc[blank, "grade_key"] = out.loc[blank].apply(_grade_key, axis=1)
    return out


def _ensure_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA
        df[column] = df[column].astype("object")
    return df


def _is_over_under(row: Mapping[str, Any]) -> bool:
    return str(row.get("selection", "") or "").strip().lower() in {"over", "under"}


def _copy_final_values(target: pd.DataFrame, indices: Sequence[int], final_row: Mapping[str, Any]) -> None:
    columns = [
        "actual_value",
        "result",
        "graded_result",
        "is_win",
        "is_push",
        "is_loss",
        "ungraded_reason",
    ]
    _ensure_columns(target, columns)
    for idx in indices:
        for column in columns:
            if column in final_row:
                target.at[idx, column] = final_row.get(column)
        target.at[idx, "result"] = str(final_row.get("result") or final_row.get("graded_result") or "").strip().lower()
        target.at[idx, "graded_result"] = target.at[idx, "result"]
        target.at[idx, "ungraded_reason"] = ""


def _apply_graded_values(target: pd.DataFrame, indices: Sequence[int], graded: Mapping[str, Any]) -> None:
    _ensure_columns(target, list(graded.keys()) + ["ungraded_reason"])
    for idx in indices:
        for column, value in graded.items():
            target.at[idx, column] = value
        target.at[idx, "ungraded_reason"] = ""


def _mark_unsupported(target: pd.DataFrame, indices: Sequence[int]) -> None:
    _ensure_columns(target, ["result", "graded_result", "ungraded_reason"])
    for idx in indices:
        target.at[idx, "result"] = UNRESOLVED_RESULT
        target.at[idx, "graded_result"] = UNRESOLVED_RESULT
        target.at[idx, "ungraded_reason"] = UNSUPPORTED_REASON


def _atomic_write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        df.to_csv(temp_path, index=False)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _artifact_out_dir(runtime_root: Path, out_dir: Path) -> Path:
    expected_runtime = out_dir / "runtime"
    if runtime_root.resolve() == expected_runtime.resolve():
        return out_dir
    if runtime_root.name == "runtime":
        return runtime_root.parent
    return out_dir


def backfill_result_feedback(
    *,
    runtime_root: Path,
    out_dir: Path,
    write: bool,
    ai_factory: Callable[[Path], Any] | None = None,
) -> BackfillStats:
    runtime_root = Path(runtime_root)
    out_dir = Path(out_dir)
    feedback_path = runtime_root / "history" / "result_feedback.csv"
    if not feedback_path.exists():
        raise FileNotFoundError(f"Missing feedback file: {feedback_path}")

    feedback = pd.read_csv(feedback_path, low_memory=False)
    feedback = _ensure_grade_keys(feedback)
    feedback = _ensure_columns(feedback, ["result", "graded_result", "ungraded_reason"])
    normalized = _normalize_result_series(feedback)
    unresolved_mask = normalized.eq(UNRESOLVED_RESULT)

    stats = BackfillStats(
        feedback_rows=int(len(feedback)),
        unresolved_rows=int(unresolved_mask.sum()),
    )
    if stats.unresolved_rows:
        stats.unresolved_unique_keys = int(feedback.loc[unresolved_mask, "grade_key"].astype(str).nunique())
    dates = sorted(feedback.loc[unresolved_mask, "prediction_date"].dropna().astype(str).unique().tolist())
    stats.dates = int(len(dates))

    final_by_key: dict[str, Mapping[str, Any]] = {}
    final_rows = feedback[_normalize_result_series(feedback).isin(FINAL_GRADING_RESULTS)]
    for _, final_row in final_rows.iterrows():
        key = str(final_row.get("grade_key", ""))
        if key and key not in final_by_key:
            final_by_key[key] = _to_str_dict(final_row)

    ai = (
        ai_factory
        or (
            lambda base_out_dir: CourtVisionOperations(
                out_dir=str(base_out_dir)
            )
        )
    )(_artifact_out_dir(runtime_root, out_dir))
    if hasattr(ai, "runtime_dir"):
        ai.runtime_dir = runtime_root
    if hasattr(ai, "runtime_history_dir"):
        ai.runtime_history_dir = runtime_root / "history"
    if hasattr(ai, "feedback_path"):
        ai.feedback_path = feedback_path

    updated_feedback = feedback.copy()
    client = None
    stats_cache: dict[str, pd.DataFrame] = {}
    games_cache: dict[str, list[Mapping[str, Any]]] = {}

    for date in dates:
        date_mask = unresolved_mask & feedback["prediction_date"].astype(str).eq(str(date))
        date_indices = feedback.index[date_mask].tolist()
        date_stats = DateBackfillStats(date=str(date), unresolved=int(len(date_indices)))

        date_keys = feedback.loc[date_indices, "grade_key"].astype(str)
        for key, key_indices_index in date_keys.groupby(date_keys).groups.items():
            indices = list(key_indices_index)
            first_row = _to_str_dict(feedback.loc[indices[0]])
            if not _is_over_under(first_row):
                _mark_unsupported(updated_feedback, indices)
                date_stats.unsupported += int(len(indices))
                continue

            date_stats.gradeable += int(len(indices))
            if key in final_by_key:
                _copy_final_values(updated_feedback, indices, final_by_key[key])
                date_stats.updated += int(len(indices))
                continue

            if client is None:
                client = operations_client(ai)
            if date not in stats_cache:
                raw_stats = client.get_stats(str(date), str(date))
                stats_cache[str(date)] = normalize_operation_stats(
                    ai,
                    raw_stats,
                )
            if date not in games_cache:
                raw_games = client.get_games(str(date))
                games_cache[str(date)] = (
                    [_to_str_dict(row) for row in raw_games.to_dict("records")]
                    if isinstance(raw_games, pd.DataFrame) and not raw_games.empty
                    else []
                )

            row_for_grade = dict(first_row)
            row_for_grade["prediction_date_str"] = str(date)
            row_for_grade["grade_key"] = key
            diagnostics: dict[str, Any] = {}
            graded = grade_operation_prediction(
                ai,
                row_for_grade,
                stats_cache[str(date)],
                games_cache[str(date)],
                diagnostics=diagnostics,
            )
            if graded:
                _apply_graded_values(updated_feedback, indices, graded)
                date_stats.updated += int(len(indices))
            else:
                reason = str(diagnostics.get("ungraded_reason") or "").strip()
                if reason == UNSUPPORTED_REASON:
                    _mark_unsupported(updated_feedback, indices)
                    date_stats.unsupported += int(len(indices))
                else:
                    if reason:
                        _ensure_columns(updated_feedback, ["ungraded_reason"])
                        for idx in indices:
                            updated_feedback.at[idx, "ungraded_reason"] = reason
                    date_stats.missing_stats += int(len(indices))

        stats.total_updated += date_stats.updated
        stats.total_unsupported += date_stats.unsupported
        stats.total_missing_stats += date_stats.missing_stats
        if date_stats.updated or date_stats.unsupported:
            stats.affected_dates.append(date_stats.date)
        stats.by_date.append(date_stats)

    stats.affected_dates = sorted(set(stats.affected_dates))

    if write:
        _atomic_write_csv(feedback_path, updated_feedback)
        artifact_base = _artifact_out_dir(runtime_root, out_dir)
        for date in stats.affected_dates:
            _write_grading_outputs(artifact_base, date, pd.DataFrame())

    return stats


def _print_stats(stats: BackfillStats) -> None:
    print(f"[BACKFILL] feedback_rows={stats.feedback_rows}", flush=True)
    print(f"[BACKFILL] unresolved_rows={stats.unresolved_rows}", flush=True)
    print(f"[BACKFILL] unresolved_unique_keys={stats.unresolved_unique_keys}", flush=True)
    print(f"[BACKFILL] dates={stats.dates}", flush=True)
    for date_stats in stats.by_date:
        print(
            "[BACKFILL] "
            f"date={date_stats.date} unresolved={date_stats.unresolved} "
            f"gradeable={date_stats.gradeable} updated={date_stats.updated} "
            f"unsupported={date_stats.unsupported} missing_stats={date_stats.missing_stats}",
            flush=True,
        )
    print(f"[BACKFILL] total_updated={stats.total_updated}", flush=True)
    print(f"[BACKFILL] total_unsupported={stats.total_unsupported}", flush=True)
    print(f"[BACKFILL] total_missing_stats={stats.total_missing_stats}", flush=True)
    print(f"[BACKFILL] affected_dates={stats.affected_dates}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill unresolved CourtVision result feedback rows.")
    parser.add_argument("--runtime-root", default="outputs/runtime", help="Runtime root containing history/result_feedback.csv")
    parser.add_argument("--out-dir", default="outputs", help="Output root used for regenerated artifacts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print what would change without writing files")
    mode.add_argument("--write", action="store_true", help="Update result_feedback.csv and regenerate grading artifacts")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stats = backfill_result_feedback(
        runtime_root=Path(args.runtime_root),
        out_dir=Path(args.out_dir),
        write=bool(args.write),
    )
    _print_stats(stats)


if __name__ == "__main__":
    main()
