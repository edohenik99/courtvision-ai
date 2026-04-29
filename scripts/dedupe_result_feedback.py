from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision_ai import FINAL_GRADING_RESULTS


UNRESOLVED_RESULT = "unresolved"
UNKNOWN_RESULT = "unknown"


@dataclass
class Conflict:
    grade_key: str
    rows: int
    final_results: list[str]


@dataclass
class DedupeAudit:
    total_rows: int = 0
    unique_grade_keys: int = 0
    duplicate_grade_keys_count: int = 0
    max_duplicates_single_key: int = 0
    conflicting_grade_keys_count: int = 0
    conflict_rows: int = 0
    corrected_conflict_rows: int = 0
    would_keep_rows: int = 0
    would_remove_rows: int = 0
    unresolved_rows: int = 0
    final_rows: int = 0
    blank_grade_key_rows: int = 0
    conflicts: list[Conflict] = field(default_factory=list)


@dataclass(frozen=True)
class ConflictResolution:
    grade_key: str
    actual_value: float
    correct_result: str
    source: str


def _normalized_result_series(df: pd.DataFrame) -> pd.Series:
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
    return result.replace("", UNKNOWN_RESULT)


def _grade_key_from_row(row: Mapping[str, Any]) -> str:
    prediction_date = row.get("prediction_date_str") or row.get("prediction_date") or ""
    return (
        f"{prediction_date}|{row.get('market_type', '')}|{row.get('entity_name', '')}|"
        f"{row.get('selection', '')}|{row.get('sportsbook_line', '')}"
    )


def _ensure_grade_keys(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "grade_key" not in out.columns:
        out["grade_key"] = ""
    out["grade_key"] = out["grade_key"].fillna("").astype(str)
    blank = out["grade_key"].str.strip().eq("")
    if blank.any():
        out.loc[blank, "grade_key"] = out.loc[blank].apply(_grade_key_from_row, axis=1)
    return out


def _row_score(row: pd.Series) -> tuple[int, int, int, int, int]:
    result = str(row.get("_normalized_result", "") or "").strip().lower()
    is_final = int(result in FINAL_GRADING_RESULTS)
    is_known = int(result != UNKNOWN_RESULT)
    has_actual = int(str(row.get("actual_value", "") or "").strip() not in {"", "nan", "None"})
    has_reason = int(str(row.get("ungraded_reason", "") or "").strip() not in {"", "nan", "None"})
    return (is_final, is_known, has_actual, -has_reason, int(row.get("_source_order", 0) or 0))


def _canonical_index(group: pd.DataFrame) -> int:
    scored = [(_row_score(row), int(idx)) for idx, row in group.iterrows()]
    return max(scored, key=lambda item: item[0])[1]


def _result_flags(result: str) -> dict[str, int]:
    normalized = str(result or "").strip().lower()
    return {
        "is_win": 1 if normalized == "win" else 0,
        "is_push": 1 if normalized == "push" else 0,
        "is_loss": 1 if normalized == "loss" else 0,
    }


def _apply_resolution_to_row(row: pd.Series, resolution: ConflictResolution) -> pd.Series:
    out = row.copy()
    out["actual_value"] = resolution.actual_value
    out["result"] = resolution.correct_result
    out["graded_result"] = resolution.correct_result
    out["ungraded_reason"] = ""
    for column, value in _result_flags(resolution.correct_result).items():
        out[column] = value
    return out


def parse_conflict_resolution_report(path: Path) -> dict[str, ConflictResolution]:
    text = Path(path).read_text(encoding="utf-8")
    resolutions: dict[str, ConflictResolution] = {}
    current: dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        grade_key = current.get("grade_key", "").strip()
        result = current.get("correct_result", "").strip().lower()
        if not grade_key:
            return
        if result not in FINAL_GRADING_RESULTS:
            raise ValueError(f"Invalid correct_result for {grade_key!r}: {result!r}")
        try:
            actual_value = float(current.get("actual_value", ""))
        except ValueError as exc:
            raise ValueError(f"Invalid actual_value for {grade_key!r}: {current.get('actual_value')!r}") from exc
        resolutions[grade_key] = ConflictResolution(
            grade_key=grade_key,
            actual_value=actual_value,
            correct_result=result,
            source=current.get("source_used", current.get("source", "")).strip(),
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            current = {}
            continue
        if line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = value.strip()
    flush()
    return resolutions


def build_dedupe_plan(
    df: pd.DataFrame,
    conflict_resolutions: Mapping[str, ConflictResolution] | None = None,
) -> tuple[pd.DataFrame, DedupeAudit]:
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame()
    work = _ensure_grade_keys(df)
    work["_source_order"] = range(len(work))
    work["_normalized_result"] = _normalized_result_series(work)

    key_counts = work["grade_key"].value_counts(dropna=False)
    duplicate_counts = key_counts[key_counts > 1]
    audit = DedupeAudit(
        total_rows=int(len(work)),
        unique_grade_keys=int(work["grade_key"].nunique(dropna=False)),
        duplicate_grade_keys_count=int(len(duplicate_counts)),
        max_duplicates_single_key=int(duplicate_counts.max()) if not duplicate_counts.empty else 0,
        unresolved_rows=int(work["_normalized_result"].eq(UNRESOLVED_RESULT).sum()),
        final_rows=int(work["_normalized_result"].isin(FINAL_GRADING_RESULTS).sum()),
        blank_grade_key_rows=int(df.get("grade_key", pd.Series("", index=df.index)).fillna("").astype(str).str.strip().eq("").sum())
        if not df.empty
        else 0,
    )

    conflict_resolutions = conflict_resolutions or {}
    keep_rows: list[pd.Series] = []
    conflict_rows = 0
    for grade_key, group in work.groupby("grade_key", sort=True, dropna=False):
        final_results = sorted(set(group.loc[group["_normalized_result"].isin(FINAL_GRADING_RESULTS), "_normalized_result"].astype(str)))
        if len(final_results) > 1:
            resolution = conflict_resolutions.get(str(grade_key))
            if resolution is None:
                audit.conflicts.append(Conflict(str(grade_key), int(len(group)), final_results))
                keep_rows.extend([row.copy() for _, row in group.iterrows()])
                conflict_rows += int(len(group))
            else:
                preferred = group[group["_normalized_result"].astype(str).eq(resolution.correct_result)]
                source_group = preferred if not preferred.empty else group
                canonical = source_group.loc[_canonical_index(source_group)]
                keep_rows.append(_apply_resolution_to_row(canonical, resolution))
                audit.corrected_conflict_rows += int(len(group))
            continue
        keep_rows.append(work.loc[_canonical_index(group)].copy())

    audit.conflicting_grade_keys_count = int(len(audit.conflicts))
    audit.conflict_rows = int(conflict_rows)
    audit.would_keep_rows = int(len(keep_rows))
    audit.would_remove_rows = int(len(work) - len(keep_rows))

    deduped = pd.DataFrame(keep_rows) if keep_rows else work.iloc[0:0].copy()
    deduped = deduped.drop(columns=["_source_order", "_normalized_result"], errors="ignore").reset_index(drop=True)
    return deduped, audit


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


def _print_audit(audit: DedupeAudit, *, show_conflicts: int) -> None:
    print(f"[DEDUPE] total_rows={audit.total_rows}", flush=True)
    print(f"[DEDUPE] unique_grade_keys={audit.unique_grade_keys}", flush=True)
    print(f"[DEDUPE] duplicate_grade_keys_count={audit.duplicate_grade_keys_count}", flush=True)
    print(f"[DEDUPE] max_duplicates_single_key={audit.max_duplicates_single_key}", flush=True)
    print(f"[DEDUPE] final_rows={audit.final_rows}", flush=True)
    print(f"[DEDUPE] unresolved_rows={audit.unresolved_rows}", flush=True)
    print(f"[DEDUPE] blank_grade_key_rows={audit.blank_grade_key_rows}", flush=True)
    print(f"[DEDUPE] conflicting_grade_keys_count={audit.conflicting_grade_keys_count}", flush=True)
    print(f"[DEDUPE] conflict_rows={audit.conflict_rows}", flush=True)
    print(f"[DEDUPE] corrected_conflict_rows={audit.corrected_conflict_rows}", flush=True)
    print(f"[DEDUPE] would_keep_rows={audit.would_keep_rows}", flush=True)
    print(f"[DEDUPE] would_remove_rows={audit.would_remove_rows}", flush=True)
    if audit.conflicts:
        print("[DEDUPE] conflicts:", flush=True)
        for conflict in audit.conflicts[:show_conflicts]:
            print(
                "[DEDUPE] "
                f"conflict grade_key={conflict.grade_key!r} rows={conflict.rows} "
                f"final_results={conflict.final_results}",
                flush=True,
            )
        if len(audit.conflicts) > show_conflicts:
            print(f"[DEDUPE] conflicts_truncated={len(audit.conflicts) - show_conflicts}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and safely dedupe CourtVision result feedback rows.")
    parser.add_argument("--runtime-root", default="outputs/runtime", help="Runtime root containing history/result_feedback.csv")
    parser.add_argument("--show-conflicts", type=int, default=50, help="Maximum conflict keys to print")
    parser.add_argument(
        "--resolve-conflicts-from",
        type=Path,
        help="Reviewed conflict resolution report with grade_key, actual_value, and correct_result fields",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Audit and print the dedupe plan without writing")
    mode.add_argument("--write", action="store_true", help="Write the deduped result_feedback.csv when no conflicts exist")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    feedback_path = Path(args.runtime_root) / "history" / "result_feedback.csv"
    if not feedback_path.exists():
        raise SystemExit(f"Missing feedback file: {feedback_path}")

    feedback = pd.read_csv(feedback_path, low_memory=False)
    resolutions = parse_conflict_resolution_report(args.resolve_conflicts_from) if args.resolve_conflicts_from else {}
    if resolutions:
        print(f"[DEDUPE] conflict_resolutions_loaded={len(resolutions)}", flush=True)
    deduped, audit = build_dedupe_plan(feedback, conflict_resolutions=resolutions)
    _print_audit(audit, show_conflicts=max(0, int(args.show_conflicts)))

    if args.write:
        if audit.conflicts:
            raise SystemExit("[DEDUPE] write_blocked=true reason=conflicting_final_results")
        _atomic_write_csv(feedback_path, deduped)
        print(f"[DEDUPE] wrote={feedback_path}", flush=True)
    else:
        print("[DEDUPE] dry_run=true files_written=0", flush=True)


if __name__ == "__main__":
    main()
