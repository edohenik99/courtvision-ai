from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.artifact_guard import guard_no_existing_artifact  # noqa: E402
from courtvision.context.game_context import (  # noqa: E402
    format_identity_quarantine_line,
    identity_quarantine_summary,
)
from courtvision.context.player_identity import (  # noqa: E402
    annotate_source_identity_conflicts,
    source_identity_conflict_diagnostic_count,
    source_identity_conflict_exposure_summary,
)
from courtvision.reporting.clv_market_movement import (  # noqa: E402
    DIAGNOSTIC_ONLY_NOTE as CLV_DIAGNOSTIC_ONLY_NOTE,
)
from courtvision.reporting.calibration_bucket_report import (  # noqa: E402
    DIAGNOSTIC_ONLY_NOTE as CALIBRATION_BUCKET_DIAGNOSTIC_ONLY_NOTE,
)
from courtvision.reporting.player_role_stability import (  # noqa: E402
    DIAGNOSTIC_ONLY_NOTE as PLAYER_ROLE_STABILITY_DIAGNOSTIC_ONLY_NOTE,
)
from courtvision.reporting.meta_label_promotion import (  # noqa: E402
    DIAGNOSTIC_ONLY_NOTE as META_LABEL_DIAGNOSTIC_ONLY_NOTE,
)
from courtvision.reporting.completion_state_audit import history_pending_grading_count  # noqa: E402
from courtvision.reporting.near_elite_review import (  # noqa: E402
    REVIEW_ONLY_NOTE as NEAR_ELITE_REVIEW_ONLY_NOTE,
    near_elite_row_line,
)
from courtvision.selection import (  # noqa: E402
    format_unsupported_active_operator_market_drop_line,
    unsupported_active_operator_market_drop_summary,
)


TRUE_STRINGS = {"true", "1", "yes", "y"}
GRADED_STATUSES = {"hit", "miss"}
REQUIRED_ARTIFACT_KEYS = (
    "elite_board",
    "full_market_board",
    "quality_summary_json",
    "board_diagnostics",
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


def _safe_int(value: Any, default: int = 0) -> int:
    number = _safe_float(value)
    return default if number is None else int(number)


def _source_identity_example_lines(source_identity: dict[str, Any], *, limit: int = 3) -> list[str]:
    raw_examples = source_identity.get("source_identity_conflict_examples", [])
    if not isinstance(raw_examples, list) or not raw_examples:
        return []
    lines = ["- source identity examples (non-blocking):"]
    for raw_example in raw_examples[:limit]:
        if not isinstance(raw_example, dict):
            continue
        player_name = _safe_text(raw_example.get("player_name")) or "Unknown"
        player_id = _safe_text(raw_example.get("player_id"))
        identity = f"{player_name} ({player_id})" if player_id else player_name
        lane = _safe_text(raw_example.get("lane")) or "unknown"
        artifact = _safe_text(raw_example.get("artifact")) or lane
        market = _safe_text(raw_example.get("market_type")) or "unknown"
        policy = _safe_text(raw_example.get("policy")) or "unknown"
        reason = _safe_text(raw_example.get("conflict_reason")) or "unknown"
        lines.append(
            f"  - {identity} | lane={lane} | artifact={artifact} | "
            f"market={market} | policy={policy} | reason={reason}"
        )
    return lines if len(lines) > 1 else []


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in TRUE_STRINGS


def _format_num(value: Any, digits: int = 2, *, trim: bool = False) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    text = f"{number:.{digits}f}"
    return text.rstrip("0").rstrip(".") if trim else text


def _format_rate(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _read_csv(path: Path, warnings: list[str], *, required: bool = False) -> pd.DataFrame:
    if not path.exists():
        if required:
            warnings.append(f"Missing required CSV: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        warnings.append(f"Could not read CSV {path}: {exc}")
        return pd.DataFrame()


def _read_json(path: Path, warnings: list[str], *, required: bool = False) -> dict[str, Any]:
    if not path.exists():
        if required:
            warnings.append(f"Missing required JSON: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read JSON {path}: {exc}")
        return {}


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "near_elite_review": operator / f"near_elite_review_{prediction_date}.csv",
        "sgp_board": operator / f"sgp_board_{prediction_date}.csv",
        "kelly_stakes": operator / f"kelly_stakes_{prediction_date}.csv",
        "daily_summary": operator / f"daily_summary_{prediction_date}.txt",
        "quality_summary": operator / f"quality_summary_{prediction_date}.txt",
        "quality_summary_json": operator / f"quality_summary_{prediction_date}.json",
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "elite_pipeline_audit_summary": operator / f"elite_pipeline_audit_summary_{prediction_date}.json",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
        "completion_state_audit_json": diagnostics / f"completion_state_audit_{prediction_date}.json",
        "completion_state_audit_text": operator / f"completion_state_audit_{prediction_date}.txt",
        "artifact_manifest_json": diagnostics / f"artifact_manifest_{prediction_date}.json",
        "artifact_manifest_text": operator / f"artifact_manifest_{prediction_date}.txt",
        "market_shadow_report": operator / f"market_shadow_report_{prediction_date}.txt",
        "market_shadow_grading": diagnostics / f"market_shadow_grading_{prediction_date}.json",
        "clv_market_movement_report": operator / f"clv_market_movement_{prediction_date}.txt",
        "clv_market_movement_diagnostics": diagnostics / f"clv_market_movement_{prediction_date}.json",
        "calibration_bucket_report": operator / f"calibration_bucket_report_{prediction_date}.txt",
        "calibration_bucket_report_diagnostics": diagnostics / f"calibration_bucket_report_{prediction_date}.json",
        "player_role_stability_report": operator / f"player_role_stability_{prediction_date}.txt",
        "player_role_stability_report_diagnostics": diagnostics / f"player_role_stability_{prediction_date}.json",
        "meta_label_promotion_shadow_report": operator / f"meta_label_promotion_shadow_{prediction_date}.txt",
        "meta_label_promotion_shadow_diagnostics": diagnostics / f"meta_label_promotion_shadow_{prediction_date}.json",
        "meta_label_promotion_shadow_csv": operator / f"meta_label_promotion_shadow_{prediction_date}.csv",
        "high_caution_over_watchlist": operator / f"high_caution_over_watchlist_{prediction_date}.csv",
        "combo_under_watchlist": operator / f"combo_under_watchlist_{prediction_date}.csv",
        "paper_kelly_simulation": operator / f"paper_kelly_simulation_{prediction_date}.csv",
        "same_opponent_under_warnings": operator / f"same_opponent_under_warnings_{prediction_date}.csv",
    }


def _count_bool_column(df: pd.DataFrame, *columns: str) -> int:
    if df.empty:
        return 0
    for column in columns:
        if column in df.columns:
            return int(df[column].map(_is_truthy).sum())
    return 0


def _quality_count(payload: dict[str, Any], key_path: tuple[str, ...], default: int) -> int:
    node: Any = payload
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return _safe_int(node, default)


def _market_counts(df: pd.DataFrame) -> Counter[str]:
    if df.empty or "market_type" not in df.columns:
        return Counter()
    return Counter(_safe_text(value) or "unknown" for value in df["market_type"])


def _line_value(row: pd.Series) -> Any:
    for column in ("sportsbook_line", "line"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def _edge_value(row: pd.Series) -> Any:
    for column in ("directional_edge", "edge"):
        if column in row.index and _safe_text(row.get(column)):
            return row.get(column)
    return None


def _row_has_any(row: pd.Series, *columns: str) -> bool:
    return any(column in row.index and _is_truthy(row.get(column)) for column in columns)


def _action_value(row: pd.Series) -> str:
    if "recommended_action" in row.index:
        value = _safe_text(row.get("recommended_action"))
        if value and value.lower() not in {"n/a", "na"}:
            return value

    if _row_has_any(row, "review_before_bet"):
        return "REVIEW_BEFORE_BET"
    if _row_has_any(row, "manual_review_required"):
        return "MANUAL_REVIEW_REQUIRED"
    if _row_has_any(row, "same_opponent_under_warning"):
        return "REVIEW_SAME_OPPONENT_WARNING"
    if _row_has_any(
        row,
        "kelly_manual_review_required",
        "kelly_review_required",
        "review_policy_hold",
    ):
        return "KELLY_REVIEW_REQUIRED"

    operator_action = _safe_text(row.get("operator_action") if "operator_action" in row.index else "")
    review_status = _safe_text(row.get("review_status") if "review_status" in row.index else "")
    stake_policy = _safe_text(row.get("stake_policy") if "stake_policy" in row.index else "")
    if (
        operator_action == "DO_NOT_BET_UNTIL_REVIEWED"
        or review_status == "REVIEW_REQUIRED"
        or stake_policy == "HOLD"
    ):
        return "KELLY_REVIEW_REQUIRED"

    return "CLEAR"


def _row_text_contains(row: pd.Series, needles: tuple[str, ...], columns: tuple[str, ...]) -> bool:
    haystack = " ".join(
        _safe_text(row.get(column)).lower()
        for column in columns
        if column in row.index
    )
    return any(needle in haystack for needle in needles)


def _preview_action_value(row: pd.Series, *, final_decision: str, elite_count: int) -> str:
    """Return an operator-safe display action for full-market preview rows.

    Full-market preview rows are diagnostic only. They must not inherit CLEAR in a
    NO_BET card because CLEAR reads like betting permission.
    """
    if _row_has_any(row, "review_before_bet"):
        return "REVIEW_REQUIRED"
    if _row_has_any(row, "manual_review_required"):
        return "MANUAL_REVIEW_REQUIRED"
    if _row_has_any(row, "same_opponent_under_warning"):
        return "REVIEW_SAME_OPPONENT_WARNING"
    if _row_has_any(
        row,
        "kelly_manual_review_required",
        "kelly_review_required",
        "review_policy_hold",
    ):
        return "KELLY_REVIEW_REQUIRED"

    reason_columns = (
        "qualification_reason",
        "rejection_reason",
        "elite_rejection_reason",
        "recommended_action_reason",
        "reason",
        "context_warning_flags",
        "warning_flags",
    )
    if _row_text_contains(
        row,
        (
            "elite_reject_context_high_caution_over",
            "context_high_caution_over",
            "high_caution_over",
        ),
        reason_columns,
    ):
        return "WATCHLIST_ONLY"

    if _safe_text(final_decision).upper() == "NO BET" or elite_count <= 0:
        return "SHADOW_ONLY"

    return _action_value(row)


def _bucket_value(row: pd.Series) -> str:
    for column in ("bucket", "watchlist_bucket", "context_caution_level", "fragility_bucket", "quality_band"):
        if column in row.index:
            value = _safe_text(row.get(column))
            if value:
                return value
    return "n/a"


def _sort_candidates(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    if df.empty:
        return df
    working = df.copy()
    sort_columns: list[str] = []
    for column in ("quality_score", "confidence"):
        if column in working.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
            sort_columns.append(column)
    if "edge" in working.columns:
        working["_abs_edge_for_display"] = pd.to_numeric(working["edge"], errors="coerce").abs()
        sort_columns.append("_abs_edge_for_display")
    if sort_columns:
        working = working.sort_values(sort_columns, ascending=[False] * len(sort_columns), na_position="last")
    return working.head(limit)


def _clip(text: Any, width: int) -> str:
    value = _safe_text(text) or "n/a"
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def _render_table(rows: list[dict[str, str]], columns: list[tuple[str, int]]) -> list[str]:
    if not rows:
        return []
    headers = {key: key for key, _width in columns}
    widths = {
        key: min(max(width, len(headers[key]), *(len(_safe_text(row.get(key))) for row in rows)), width)
        for key, width in columns
    }
    header = " ".join(headers[key].ljust(widths[key]) for key, _width in columns)
    separator = " ".join("-" * widths[key] for key, _width in columns)
    lines = [header, separator]
    for row in rows:
        lines.append(" ".join(_clip(row.get(key), widths[key]).ljust(widths[key]) for key, _width in columns))
    return lines


def _pick_rows(df: pd.DataFrame, *, limit: int, include_bucket: bool) -> list[dict[str, str]]:
    display = _sort_candidates(df, limit)
    rows: list[dict[str, str]] = []
    for _idx, row in display.iterrows():
        item = {
            "player": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown",
            "market": _safe_text(row.get("market_type")) or "unknown",
            "sel": _safe_text(row.get("selection")) or "n/a",
            "line": _format_num(_line_value(row), 1, trim=True),
            "odds": _safe_text(row.get("odds") if "odds" in row.index else row.get("american_odds")) or "n/a",
            "edge": _format_num(_edge_value(row), 3),
            "conf": _format_num(row.get("confidence"), 3),
            "qual": _format_num(row.get("quality_score"), 2),
            "action": _action_value(row),
        }
        if include_bucket:
            item["bucket"] = _bucket_value(row)
        rows.append(item)
    return rows


def _render_pick_table(df: pd.DataFrame, *, limit: int, include_bucket: bool = False) -> list[str]:
    columns = [
        ("player", 22),
        ("market", 32),
        ("sel", 5),
        ("line", 6),
        ("odds", 6),
        ("edge", 8),
        ("conf", 6),
        ("qual", 7),
    ]
    if include_bucket:
        columns.append(("bucket", 12))
    columns.append(("action", 28))
    return _render_table(_pick_rows(df, limit=limit, include_bucket=include_bucket), columns)


def _review_match_key(row: pd.Series) -> tuple[str, str, str, str, str]:
    return (
        _safe_text(row.get("prediction_date")),
        _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")),
        _safe_text(row.get("market_type")),
        _safe_text(row.get("selection")).lower(),
        _format_num(_line_value(row), 3, trim=True),
    )


def _preview_watchlist_key(row: pd.Series) -> tuple[str, str, str, str]:
    return (
        (_safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))).lower(),
        _safe_text(row.get("market_type")).lower(),
        _safe_text(row.get("selection")).lower(),
        _format_num(_line_value(row), 3, trim=True),
    )


def _watchlist_action_lookup(
    *,
    high_caution_df: pd.DataFrame,
    combo_under_df: pd.DataFrame,
    same_opponent_df: pd.DataFrame,
) -> dict[tuple[str, str, str, str], str]:
    lookup: dict[tuple[str, str, str, str], str] = {}

    # Lowest priority: generic combo-under watchlist.
    if not combo_under_df.empty:
        for _idx, row in combo_under_df.iterrows():
            key = _preview_watchlist_key(row)
            if key[0] and key[1]:
                lookup[key] = "COMBO_WATCHLIST_ONLY"

    # Higher priority: high-caution OVER rejection.
    if not high_caution_df.empty:
        for _idx, row in high_caution_df.iterrows():
            key = _preview_watchlist_key(row)
            if key[0] and key[1]:
                lookup[key] = "WATCHLIST_ONLY"

    # Highest priority: same-opponent warning requires review semantics.
    if not same_opponent_df.empty:
        for _idx, row in same_opponent_df.iterrows():
            key = _preview_watchlist_key(row)
            if key[0] and key[1]:
                lookup[key] = "REVIEW_SAME_OPPONENT_WARNING"

    return lookup


def _with_preview_actions(
    preview_df: pd.DataFrame,
    *,
    final_decision: str,
    elite_count: int,
    high_caution_df: pd.DataFrame,
    combo_under_df: pd.DataFrame,
    same_opponent_df: pd.DataFrame,
) -> pd.DataFrame:
    if preview_df.empty:
        return preview_df

    watchlist_actions = _watchlist_action_lookup(
        high_caution_df=high_caution_df,
        combo_under_df=combo_under_df,
        same_opponent_df=same_opponent_df,
    )

    rows: list[dict[str, Any]] = []
    for _idx, row in preview_df.iterrows():
        item = row.to_dict()
        watchlist_action = watchlist_actions.get(_preview_watchlist_key(row))
        item["recommended_action"] = watchlist_action or _preview_action_value(
            row,
            final_decision=final_decision,
            elite_count=elite_count,
        )
        rows.append(item)

    return pd.DataFrame(rows, columns=list(dict.fromkeys([*preview_df.columns, "recommended_action"])))


def _with_kelly_review_fields(board_df: pd.DataFrame, kelly_df: pd.DataFrame) -> pd.DataFrame:
    if board_df.empty or kelly_df.empty:
        return board_df

    review_columns = (
        "recommended_action",
        "review_before_bet",
        "manual_review_required",
        "same_opponent_under_warning",
        "kelly_manual_review_required",
        "review_policy_hold",
        "operator_action",
        "operator_note",
        "review_status",
        "stake_policy",
    )
    lookup: dict[tuple[str, str, str, str, str], pd.Series] = {}
    for _idx, row in kelly_df.iterrows():
        lookup[_review_match_key(row)] = row

    rows: list[dict[str, Any]] = []
    for _idx, row in board_df.iterrows():
        item = row.to_dict()
        source = lookup.get(_review_match_key(row))
        if source is not None:
            for column in review_columns:
                if column not in source.index:
                    continue
                source_value = source.get(column)
                if column in item and _safe_text(item.get(column)):
                    if _is_truthy(item.get(column)) or not _is_truthy(source_value):
                        continue
                if _safe_text(source_value):
                    item[column] = source_value
        rows.append(item)
    return pd.DataFrame(rows, columns=list(dict.fromkeys([*board_df.columns, *review_columns])))


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return singular
    return plural or f"{singular}s"


def _count_or_none(values: Any) -> str:
    if isinstance(values, list):
        return "none" if not values else str(len(values))
    return "none"


def _pending_summary(payload: dict[str, Any], prefix: str) -> str:
    pending = _safe_int(payload.get(f"{prefix}_pending_count"), 0)
    open_game = _safe_int(payload.get(f"{prefix}_open_game_pending_count"), 0)
    stale = _safe_int(payload.get(f"{prefix}_stale_pending_count"), 0)
    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    taxonomy = _safe_text(details.get(f"{prefix}_pending_taxonomy_source")) or "n/a"
    return f"pending={pending}, open_game_pending={open_game}, stale_pending={stale}, taxonomy_source={taxonomy}"


def _has_items(value: Any) -> bool:
    return isinstance(value, list) and len(value) > 0


def _count_label(count: int) -> str:
    return "none" if count <= 0 else str(count)


def _completion_warning_counts(payload: dict[str, Any]) -> tuple[int, int]:
    """Return blocking and optional warning counts for completion audit display."""
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        return 0, 0

    blocking = 0
    optional = 0
    for warning in warnings:
        warning_text = _safe_text(warning).lower()
        if "missing optional pending repair audit" in warning_text:
            optional += 1
        else:
            blocking += 1

    return blocking, optional


def _completion_recommended_action(payload: dict[str, Any], prediction_date: str) -> str:
    if not payload:
        return f"run scripts/write_completion_state_audit.py --prediction-date {prediction_date}"

    real_pending = _safe_int(payload.get("real_pick_pending_count"), 0)
    if real_pending > 0:
        return "inspect grading before trusting results"
    if _has_items(payload.get("agreement_issues")) or _has_items(payload.get("warnings")):
        return "inspect completion audit before trusting results"

    status = _safe_text(payload.get("report_agreement_status")).upper()
    if status == "COMPLETE":
        return "slate closed / no action required"
    if status == "COMPLETE_WITH_SHADOW_OPEN_NOISE" and real_pending == 0:
        return "real picks closed / ignore shadow-paper open-game noise"
    return "inspect completion audit before trusting results"


def _audit_issue_counts(payload: dict[str, Any]) -> tuple[int, int]:
    """Return blocking/failure and warning issue counts from an audit payload."""
    if not isinstance(payload, dict) or not payload:
        return 0, 0

    issues = payload.get("issues", [])
    blocking_count = _safe_int(payload.get("failure_count"), 0)
    warning_count = _safe_int(payload.get("warning_count"), 0)

    if isinstance(issues, list):
        issue_blocking = 0
        issue_warnings = 0
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            severity = _safe_text(issue.get("severity")).lower()
            if severity in {"failure", "error", "blocking"}:
                issue_blocking += 1
            elif severity in {"warning", "warn"}:
                issue_warnings += 1
        blocking_count = max(blocking_count, issue_blocking)
        warning_count = max(warning_count, issue_warnings)

    return blocking_count, warning_count


def _audit_status_classification(payload: dict[str, Any]) -> tuple[str, int, int, str]:
    """Classify audit status as blocking/non-blocking/clean/review for card display."""
    if not isinstance(payload, dict) or not payload:
        return "missing", 0, 0, "missing"

    status = _safe_text(payload.get("status")) or "UNKNOWN"
    normalized_status = status.upper()
    blocking_count, warning_count = _audit_issue_counts(payload)

    if normalized_status.startswith("FAIL") or blocking_count > 0:
        classification = "blocking"
    elif normalized_status == "PASS_WITH_WARNINGS":
        classification = "non-blocking"
    elif normalized_status.startswith("PASS"):
        classification = "clean"
    else:
        classification = "review"

    return status, blocking_count, warning_count, classification


def _audit_summary_line(label: str, payload: dict[str, Any]) -> str:
    status, blocking_count, warning_count, classification = _audit_status_classification(payload)
    return (
        f"- {label}: {status}, {classification} "
        f"(blocking={_count_label(blocking_count)}, warnings={_count_label(warning_count)})"
    )


def _audit_warning_summary_lines(
    *,
    full_market_sanity_payload: dict[str, Any],
    candidate_quality_drift_payload: dict[str, Any],
) -> list[str]:
    lines = [
        _audit_summary_line("full-market sanity audit", full_market_sanity_payload),
        _audit_summary_line("candidate quality drift audit", candidate_quality_drift_payload),
    ]

    classifications = [
        _audit_status_classification(full_market_sanity_payload),
        _audit_status_classification(candidate_quality_drift_payload),
    ]
    total_blocking = sum(item[1] for item in classifications)
    has_blocking_status = any(item[3] == "blocking" for item in classifications)

    lines.append(f"- blocking audit warnings: {_count_label(total_blocking)}")
    if has_blocking_status or total_blocking > 0:
        lines.append("- operator action: inspect audit before trusting results.")
    else:
        lines.append("- operator action: continue only if final_decision rules remain clean.")

    return lines


def _unsupported_active_market_drop_count(
    quality_payload: dict[str, Any],
    board_diagnostics: dict[str, Any],
) -> int:
    payloads = (quality_payload, board_diagnostics)
    section_names = (
        "unsupported_active_operator_markets",
        "unsupported_active_operator_market",
        "candidate_funnel",
    )
    count_keys = (
        "total_rows_dropped",
        "unsupported_active_operator_market_drop_count",
        "unsupported_active_operator_market_count",
    )

    for payload in payloads:
        if not isinstance(payload, dict):
            continue

        for key in count_keys:
            count = _safe_int(payload.get(key), 0)
            if count > 0:
                return count

        for section_name in section_names:
            section = payload.get(section_name, {})
            if not isinstance(section, dict):
                continue
            for key in count_keys:
                count = _safe_int(section.get(key), 0)
                if count > 0:
                    return count

    return 0


def _top_rejection_reason_line(board_diagnostics: dict[str, Any]) -> str:
    candidates: list[tuple[int, str]] = []

    top_reasons = board_diagnostics.get("top_rejection_reasons", {})
    if isinstance(top_reasons, list):
        for item in top_reasons:
            if not isinstance(item, dict):
                continue
            reason = _safe_text(
                item.get("reason")
                or item.get("rejection_reason")
                or item.get("key")
            )
            count = _safe_int(item.get("count"), 0)
            if reason and count > 0:
                candidates.append((count, reason))

    elite_context = board_diagnostics.get("elite_context_safety_gate", {})
    if isinstance(elite_context, dict):
        reason_counts = elite_context.get("candidate_rejection_reason_counts", {})
        if isinstance(reason_counts, dict):
            for reason, count_value in reason_counts.items():
                reason_text = _safe_text(reason)
                count = _safe_int(count_value, 0)
                if reason_text and count > 0:
                    candidates.append((count, reason_text))

    if not candidates:
        return ""

    count, reason = max(candidates, key=lambda item: item[0])
    return f"- top rejection reason: {reason} ({count})"


def _elite_rejection_summary_lines(
    *,
    quality_payload: dict[str, Any],
    board_diagnostics: dict[str, Any],
    high_caution_count: int,
    combo_under_count: int,
    same_opponent_warning_count: int,
) -> list[str]:
    lines: list[str] = []

    if high_caution_count > 0:
        lines.append(f"- high-caution OVER context gate: {high_caution_count}")
    if combo_under_count > 0:
        lines.append(f"- combo UNDER watchlist: {combo_under_count}")

    unsupported_count = _unsupported_active_market_drop_count(quality_payload, board_diagnostics)
    if unsupported_count > 0:
        lines.append(f"- unsupported active markets dropped: {unsupported_count}")

    lines.append(f"- same-opponent UNDER warnings: {same_opponent_warning_count}")

    top_reason_line = _top_rejection_reason_line(board_diagnostics)
    if top_reason_line:
        lines.append(top_reason_line)

    if not lines:
        lines.append("- No elite rejection details available.")

    return lines


def _completion_open_noise_is_clean(payload: dict[str, Any]) -> bool:
    if not payload:
        return False

    status = _safe_text(payload.get("report_agreement_status")).upper()
    if status != "COMPLETE_WITH_SHADOW_OPEN_NOISE":
        return False

    if _safe_int(payload.get("real_pick_pending_count"), 0) != 0:
        return False
    if _has_items(payload.get("agreement_issues")):
        return False

    shadow_pending = _safe_int(payload.get("shadow_pending_count"), 0)
    shadow_open = _safe_int(payload.get("shadow_open_game_pending_count"), 0)
    shadow_stale = _safe_int(payload.get("shadow_stale_pending_count"), 0)

    paper_pending = _safe_int(payload.get("paper_pending_count"), 0)
    paper_open = _safe_int(payload.get("paper_open_game_pending_count"), 0)
    paper_stale = _safe_int(payload.get("paper_stale_pending_count"), 0)

    shadow_clean = shadow_pending <= 0 or (shadow_pending == shadow_open and shadow_stale == 0)
    paper_clean = paper_pending <= 0 or (paper_pending == paper_open and paper_stale == 0)
    return shadow_clean and paper_clean


def _no_bet_reason_lines(
    *,
    elite_count: int,
    high_caution_count: int,
    combo_under_count: int,
    completion_state_payload: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []

    if elite_count <= 0:
        reasons.append("- No elite picks survived safety/context gates.")
    if high_caution_count > 0:
        reasons.append(f"- {high_caution_count} high-caution OVER candidates were watchlist-only.")
    if combo_under_count > 0:
        reasons.append(f"- {combo_under_count} combo UNDER candidates were watchlist-only.")
    if _completion_open_noise_is_clean(completion_state_payload):
        reasons.append("- Completion audit is clean; shadow/paper pending rows are open-game only.")

    if not reasons:
        reasons.append("- No stakeable picks are available under the current operator gates.")

    return reasons


def _completion_state_lines(payload: dict[str, Any], path: Path, prediction_date: str) -> list[str]:
    lines = ["Completion State Audit", "-" * 40]
    if not payload:
        state = "unreadable" if path.exists() else "missing"
        lines.append(f"- Completion audit: {state}")
        lines.append(f"- recommended action: {_completion_recommended_action(payload, prediction_date)}")
        return lines

    lines.append(f"- report_agreement_status: {_safe_text(payload.get('report_agreement_status')) or 'UNKNOWN'}")
    lines.append(f"- real_pick_pending_count: {_safe_int(payload.get('real_pick_pending_count'), 0)}")
    lines.append(f"- market_shadow_history: {_pending_summary(payload, 'shadow')}")
    lines.append(f"- paper_kelly_history: {_pending_summary(payload, 'paper')}")
    lines.append(f"- agreement issue count: {_count_or_none(payload.get('agreement_issues'))}")
    warning_blocking_count, warning_optional_count = _completion_warning_counts(payload)
    lines.append(f"- warning count: {_count_or_none(payload.get('warnings'))}")
    lines.append(f"- blocking warning count: {_count_label(warning_blocking_count)}")
    lines.append(f"- optional warning count: {_count_label(warning_optional_count)}")
    lines.append(f"- recommended action: {_completion_audit_recommended_action(payload)}")
    return lines


def _example_lines(df: pd.DataFrame, *, limit: int = 3) -> list[str]:
    if df.empty:
        return []
    rows = _sort_candidates(df, limit)
    lines: list[str] = []
    for _idx, row in rows.iterrows():
        player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
        market = _safe_text(row.get("market_type")) or "unknown"
        selection = _safe_text(row.get("selection")) or "n/a"
        line = _format_num(_line_value(row), 1, trim=True)
        edge = _format_num(_edge_value(row), 3)
        confidence = _format_num(row.get("confidence"), 3)
        reason = ""
        for column in (
            "manual_review_reason",
            "same_opponent_warning_reason",
            "final_elite_rejection_reason",
            "kelly_projected_skip_reason",
            "skip_reason",
            "operator_note",
            "review_reason",
        ):
            if column in row.index and _safe_text(row.get(column)):
                reason = _safe_text(row.get(column))
                break
        suffix = f", reason={reason}" if reason else ""
        lines.append(f"  - {player}: {market} {selection} {line} (edge={edge}, conf={confidence}{suffix})")
    return lines


def _filter_truthy(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return pd.DataFrame(columns=list(df.columns))
    return df[df[column].map(_is_truthy)].copy()


def _matchups(df: pd.DataFrame, limit: int = 8) -> list[str]:
    if df.empty:
        return []
    labels: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for _idx, row in df.iterrows():
        team = _safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))
        opponent = _safe_text(row.get("opponent"))
        if not team or not opponent:
            continue
        game_id = _safe_text(row.get("game_id"))
        home_away = _safe_text(row.get("home_away")).lower()
        if home_away == "away":
            label = f"{team} @ {opponent}"
        elif home_away == "home":
            label = f"{opponent} @ {team}"
        else:
            label = f"{team} vs {opponent}"
        key = (game_id, *sorted((team, opponent)))
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _provider_status(quality_payload: dict[str, Any], full_market_df: pd.DataFrame) -> tuple[str, bool]:
    slate = quality_payload.get("slate_provider_counts", {})
    if not isinstance(slate, dict):
        slate = {}
    games = _safe_int(slate.get("games_count"), 0)
    normalized = _safe_int(slate.get("normalized_odds_rows_count"), len(full_market_df))
    live = _safe_int(slate.get("live_odds_count"), _count_bool_column(full_market_df, "is_live_market"))
    synthetic = _safe_int(slate.get("synthetic_or_fallback_odds_count"), 0)

    if normalized <= 0 and games > 0:
        return "unsafe_no_odds", True
    if live > 0:
        return f"live ({live} live odds, {synthetic} fallback/synthetic)", False
    if normalized > 0:
        return f"available_non_live ({normalized} normalized odds, {synthetic} fallback/synthetic)", False
    return "unknown", False


def _context_status(path: Path, label: str, payload: dict[str, Any]) -> str:
    if not path.exists():
        return f"{label}: missing"
    if label == "injury":
        normalized = _safe_int(payload.get("normalized_rows"), 0)
        matched = _safe_int(payload.get("candidate_player_matches"), 0)
        return f"injury: available ({normalized} rows, {matched} candidate matches)"
    if label == "game":
        rows = _safe_int(payload.get("rows"), _safe_int(payload.get("total_candidates"), 0))
        suppressed = _safe_int(payload.get("game_context_suppressed_count"), 0)
        stale = _safe_int(payload.get("stale_team_not_in_game_count"), 0)
        return f"game: available ({rows} rows, suppressed={suppressed}, stale_team={stale})"
    return f"{label}: available"


def _history_hit_rates(path: Path, prediction_date: str) -> dict[str, Any]:
    if not path.exists():
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}
    try:
        df = pd.read_csv(path, keep_default_na=False)
    except Exception:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}
    if df.empty or "result_status" not in df.columns:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}

    statuses = df["result_status"].map(lambda value: _safe_text(value).lower())
    graded = df[statuses.isin(GRADED_STATUSES)].copy()
    if graded.empty:
        return {"all_time": None, "last_7": None, "graded_count": 0, "source": str(path)}

    def hit_rate(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        hits = int((frame["result_status"].map(lambda value: _safe_text(value).lower()) == "hit").sum())
        losses = int((frame["result_status"].map(lambda value: _safe_text(value).lower()) == "miss").sum())
        total = hits + losses
        return None if total <= 0 else hits / total

    all_time = hit_rate(graded)
    last_7 = None
    if "prediction_date" in graded.columns:
        dated = graded.copy()
        dated["_date_sort"] = pd.to_datetime(dated["prediction_date"], errors="coerce")
        current_date = pd.to_datetime(prediction_date, errors="coerce")
        if not pd.isna(current_date):
            dated = dated[(dated["_date_sort"].isna()) | (dated["_date_sort"] <= current_date)]
        dates = [value for value in sorted(dated["_date_sort"].dropna().unique())]
        if dates:
            last_dates = set(dates[-7:])
            last_7 = hit_rate(dated[dated["_date_sort"].isin(last_dates)])
    return {
        "all_time": all_time,
        "last_7": last_7,
        "graded_count": int(len(graded)),
        "source": str(path),
    }


def _completion_audit_recommended_action(payload: dict[str, Any]) -> str:
    status = _safe_text(payload.get("report_agreement_status")).upper()
    agreement_issues = payload.get("agreement_issues", [])
    real_pick_pending_count = _safe_int(payload.get("real_pick_pending_count"), 0)

    shadow_pending_count = _safe_int(payload.get("shadow_pending_count"), 0)
    shadow_open_game_pending_count = _safe_int(payload.get("shadow_open_game_pending_count"), 0)
    shadow_stale_pending_count = _safe_int(payload.get("shadow_stale_pending_count"), 0)

    paper_pending_count = _safe_int(payload.get("paper_pending_count"), 0)
    paper_open_game_pending_count = _safe_int(payload.get("paper_open_game_pending_count"), 0)
    paper_stale_pending_count = _safe_int(payload.get("paper_stale_pending_count"), 0)

    has_agreement_issues = _has_items(agreement_issues)
    shadow_is_open_game_only = (
        shadow_pending_count > 0
        and shadow_pending_count == shadow_open_game_pending_count
        and shadow_stale_pending_count == 0
    )
    paper_is_open_game_only = (
        paper_pending_count <= 0
        or (
            paper_pending_count == paper_open_game_pending_count
            and paper_stale_pending_count == 0
        )
    )

    if status == "COMPLETE" and real_pick_pending_count == 0 and not has_agreement_issues:
        return "slate closed / no action required"

    if (
        status == "COMPLETE_WITH_SHADOW_OPEN_NOISE"
        and real_pick_pending_count == 0
        and not has_agreement_issues
        and shadow_is_open_game_only
        and paper_is_open_game_only
    ):
        return "real picks closed / ignore shadow-paper open-game noise"

    if real_pick_pending_count > 0:
        return "inspect grading before trusting results"

    return _safe_text(payload.get("recommended_action")) or "inspect completion audit before trusting results"


def _final_decision(
    *,
    elite_count: int,
    manual_review_count: int,
    review_before_bet_count: int,
    kelly_hold_count: int,
    source_identity_blocking_count: int,
    missing_required: list[str],
    provider_unsafe: bool,
    quality_payload: dict[str, Any],
) -> str:
    run_health = _safe_text(quality_payload.get("run_health_status")).upper()
    date_check = quality_payload.get("date_isolation_check", {})
    date_check_status = _safe_text(date_check.get("status") if isinstance(date_check, dict) else "").lower()
    output_validation_failed = bool(date_check_status and date_check_status != "ok")

    if (
        missing_required
        or provider_unsafe
        or output_validation_failed
        or run_health == "ERROR_OR_INCOMPLETE"
        or run_health.startswith("DEGRADED")
    ):
        return "DEGRADED"
    if source_identity_blocking_count > 0:
        return "REVIEW REQUIRED"
    if elite_count <= 0:
        return "NO BET"
    if manual_review_count > 0 or review_before_bet_count > 0 or kelly_hold_count > 0:
        return "REVIEW REQUIRED"
    return "BETTABLE"


def _files_written_lines(paths: dict[str, Path]) -> list[str]:
    keys = (
        "elite_board",
        "full_market_board",
        "near_elite_review",
        "daily_summary",
        "quality_summary",
        "operator_card",
        "board_diagnostics",
        "market_shadow_report",
        "clv_market_movement_report",
        "clv_market_movement_diagnostics",
        "calibration_bucket_report",
        "calibration_bucket_report_diagnostics",
        "player_role_stability_report",
        "player_role_stability_report_diagnostics",
        "meta_label_promotion_shadow_report",
        "meta_label_promotion_shadow_diagnostics",
        "meta_label_promotion_shadow_csv",
    )
    lines: list[str] = []
    for key in keys:
        path = paths[key]
        status = "ok" if key == "operator_card" or path.exists() else "missing"
        lines.append(f"- {key}: {path} [{status}]")
    return lines


def _bool_label(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = _safe_text(value).lower()
    if text in TRUE_STRINGS:
        return "true"
    if text in {"false", "0", "no", "n"}:
        return "false"
    return text or "n/a"


def _runtime_safety_summary(
    *,
    prediction_date: str,
    paths: dict[str, Path],
    warnings: list[str],
    runtime_mode: str | None,
    force_past_date: bool | str | None,
    force_outputs: bool | str | None,
    kelly_bankroll: str | int | float | None,
) -> dict[str, Any]:
    manifest_path = paths["artifact_manifest_json"]
    summary: dict[str, Any] = {
        "prediction_date": prediction_date,
        "COURTVISION_MODE": _safe_text(runtime_mode) or _safe_text(os.environ.get("COURTVISION_MODE")) or "betting",
        "ForcePastDate": _bool_label(force_past_date),
        "ForceOutputs": _bool_label(force_outputs),
        "KellyBankroll": _safe_text(kelly_bankroll) or "n/a",
        "artifact_manifest_status": "written_after_operator_card",
        "fatal_missing": "n/a",
        "artifact_manifest_path": str(manifest_path),
    }

    if manifest_path.exists():
        manifest_payload = _read_json(manifest_path, warnings)
        missing_by_severity = (
            manifest_payload.get("missing_by_severity", {})
            if isinstance(manifest_payload.get("missing_by_severity"), dict)
            else {}
        )
        summary["artifact_manifest_status"] = _safe_text(manifest_payload.get("status")) or "unknown"
        summary["fatal_missing"] = _safe_int(missing_by_severity.get("fatal"), 0)
    return summary


def build_operator_card(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    runtime_mode: str | None = None,
    force_past_date: bool | str | None = None,
    force_outputs: bool | str | None = None,
    kelly_bankroll: str | int | float | None = None,
) -> tuple[str, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    warnings: list[str] = []

    elite_df = _read_csv(paths["elite_board"], warnings, required=True)
    full_market_df = _read_csv(paths["full_market_board"], warnings, required=True)
    near_elite_df = _read_csv(paths["near_elite_review"], warnings)
    sgp_df = _read_csv(paths["sgp_board"], warnings)
    kelly_df = _read_csv(paths["kelly_stakes"], warnings)
    quality_payload = _read_json(paths["quality_summary_json"], warnings, required=True)
    audit_summary_payload = _read_json(paths["elite_pipeline_audit_summary"], warnings)
    board_diagnostics = _read_json(paths["board_diagnostics"], warnings, required=True)
    full_market_sanity_payload = _read_json(
        runtime_root / "diagnostics" / f"full_market_sanity_audit_{prediction_date}.json",
        warnings,
    )
    candidate_quality_drift_payload = _read_json(
        runtime_root / "diagnostics" / f"candidate_quality_drift_audit_{prediction_date}.json",
        warnings,
    )
    completion_state_payload = _read_json(paths["completion_state_audit_json"], warnings)
    market_shadow_payload = _read_json(paths["market_shadow_grading"], warnings)
    clv_market_payload = _read_json(paths["clv_market_movement_diagnostics"], warnings)
    calibration_bucket_payload = _read_json(paths["calibration_bucket_report_diagnostics"], warnings)
    player_role_stability_payload = _read_json(paths["player_role_stability_report_diagnostics"], warnings)
    meta_label_promotion_payload = _read_json(paths["meta_label_promotion_shadow_diagnostics"], warnings)
    injury_payload = _read_json(runtime_root / "diagnostics" / f"injury_context_diagnostics_{prediction_date}.json", warnings)
    game_payload = _read_json(runtime_root / "diagnostics" / f"game_context_{prediction_date}.json", warnings)
    high_caution_df = _read_csv(paths["high_caution_over_watchlist"], warnings)
    combo_under_df = _read_csv(paths["combo_under_watchlist"], warnings)
    paper_kelly_df = _read_csv(paths["paper_kelly_simulation"], warnings)
    same_opponent_file_df = _read_csv(paths["same_opponent_under_warnings"], warnings)

    missing_required = [
        str(paths[key])
        for key in REQUIRED_ARTIFACT_KEYS
        if not paths[key].exists()
    ]

    funnel = quality_payload.get("candidate_funnel", {})
    if not isinstance(funnel, dict):
        funnel = {}
    kelly_summary = quality_payload.get("kelly_safety_summary", {})
    if not isinstance(kelly_summary, dict):
        kelly_summary = {}

    elite_count = _quality_count(quality_payload, ("candidate_funnel", "elite_board_count"), len(elite_df))
    full_market_count = _quality_count(quality_payload, ("candidate_funnel", "full_market_board_count"), len(full_market_df))
    near_elite_count = _quality_count(
        quality_payload,
        ("near_elite_review", "row_count"),
        _quality_count(quality_payload, ("candidate_funnel", "near_elite_review_count"), len(near_elite_df)),
    )
    sgp_count = _quality_count(quality_payload, ("candidate_funnel", "sgp_board_count"), len(sgp_df))
    if elite_count <= 0:
        kelly_rows_count = _quality_count(quality_payload, ("kelly_safety_summary", "total_rows"), 0)
    else:
        kelly_rows_count = _quality_count(quality_payload, ("kelly_safety_summary", "total_rows"), len(kelly_df))
    kelly_eligible_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "kelly_eligible_count"),
        _count_bool_column(kelly_df, "kelly_eligible", "eligible") if elite_count > 0 else 0,
    )
    manual_review_count = _quality_count(
        quality_payload,
        ("manual_review_required_count",),
        _count_bool_column(full_market_df, "manual_review_required") + _count_bool_column(elite_df, "manual_review_required"),
    )
    review_before_bet_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "review_before_bet_count"),
        _count_bool_column(kelly_df, "review_before_bet"),
    )
    high_caution_count = _quality_count(
        quality_payload,
        ("high_caution_over_watchlist", "row_count"),
        len(high_caution_df),
    )
    combo_under_count = len(combo_under_df)
    same_opponent_warning_count = _quality_count(
        quality_payload,
        ("same_opponent_under_warning_count",),
        _count_bool_column(full_market_df, "same_opponent_under_warning"),
    )
    kelly_hold_count = _quality_count(
        quality_payload,
        ("kelly_safety_summary", "review_policy_hold_count"),
        0,
    )
    if not kelly_df.empty and "operator_action" in kelly_df.columns:
        kelly_hold_count = max(
            kelly_hold_count,
            int(kelly_df["operator_action"].map(lambda value: _safe_text(value) == "DO_NOT_BET_UNTIL_REVIEWED").sum()),
        )

    quality_source_identity = quality_payload.get("source_identity_conflict", {})
    if not isinstance(quality_source_identity, dict):
        quality_source_identity = {}
    source_identity_payload: dict[str, Any] = (
        audit_summary_payload
        if source_identity_conflict_diagnostic_count(audit_summary_payload) > 0
        else quality_payload
    )
    if source_identity_conflict_diagnostic_count(source_identity_payload) > 0:
        full_market_df = annotate_source_identity_conflicts(full_market_df, source_identity_payload)
        elite_df = annotate_source_identity_conflicts(elite_df, source_identity_payload)
        kelly_df = annotate_source_identity_conflicts(kelly_df, source_identity_payload)
        high_caution_df = annotate_source_identity_conflicts(high_caution_df, source_identity_payload)
        combo_under_df = annotate_source_identity_conflicts(combo_under_df, source_identity_payload)
        paper_kelly_df = annotate_source_identity_conflicts(paper_kelly_df, source_identity_payload)
    source_identity_summary = source_identity_conflict_exposure_summary(
        source_identity_payload=source_identity_payload,
        full_market_df=full_market_df,
        elite_df=elite_df,
        kelly_df=kelly_df,
        high_caution_watchlist_df=high_caution_df,
        combo_under_watchlist_df=combo_under_df,
        paper_kelly_df=paper_kelly_df,
    )
    for key, value in quality_source_identity.items():
        if (
            key in {"source_identity_conflict_safety_state", "source_identity_conflict_policy"}
            or key not in source_identity_summary
            or not source_identity_summary.get(key)
        ):
            source_identity_summary[key] = value
    source_identity_blocking_count = _safe_int(
        source_identity_summary.get("source_identity_conflict_blocking_rows"),
        _safe_int(source_identity_summary.get("source_identity_conflicted_elite_rows"), 0)
        + _safe_int(source_identity_summary.get("source_identity_conflicted_kelly_rows"), 0),
    )

    elite_display_df = _with_kelly_review_fields(elite_df, kelly_df)
    full_market_display_df = _with_kelly_review_fields(full_market_df, kelly_df)
    elite_manual_review_count = _count_bool_column(elite_display_df, "manual_review_required")

    provider_status, provider_unsafe = _provider_status(quality_payload, full_market_df)
    final_decision = _final_decision(
        elite_count=elite_count,
        manual_review_count=manual_review_count,
        review_before_bet_count=review_before_bet_count,
        kelly_hold_count=kelly_hold_count,
        source_identity_blocking_count=source_identity_blocking_count,
        missing_required=missing_required,
        provider_unsafe=provider_unsafe,
        quality_payload=quality_payload,
    )

    slate = quality_payload.get("slate_provider_counts", {})
    if not isinstance(slate, dict):
        slate = {}
    games_count = _safe_int(slate.get("games_count"), 0)
    if games_count <= 0 and "game_id" in full_market_df.columns:
        games_count = int(full_market_df["game_id"].map(_safe_text).replace("", pd.NA).dropna().nunique())
    odds_count = _safe_int(slate.get("normalized_odds_rows_count"), len(full_market_df))
    stale_odds_count = slate.get("stale_odds_count", "n/a")
    run_health_status = _safe_text(quality_payload.get("run_health_status")) or "UNKNOWN"
    run_health_reason = _safe_text(quality_payload.get("run_health_reason")) or "n/a"
    provider_breakdown = slate.get("provider_breakdown", {})
    line_sources = provider_breakdown.get("line_source", {}) if isinstance(provider_breakdown, dict) else {}
    line_source_text = ", ".join(f"{key}={value}" for key, value in sorted(line_sources.items())) if line_sources else "n/a"
    matchup_list = _matchups(full_market_df)

    market_shadow_totals = market_shadow_payload.get("totals", {}) if isinstance(market_shadow_payload, dict) else {}
    if not isinstance(market_shadow_totals, dict):
        market_shadow_totals = {}
    history_rates = _history_hit_rates(history_root / "pick_history.csv", prediction_date)
    shadow_history_rates = _history_hit_rates(history_root / "market_shadow_history.csv", prediction_date)
    all_time_rate = history_rates["all_time"] if history_rates["all_time"] is not None else shadow_history_rates["all_time"]
    last_7_rate = history_rates["last_7"] if history_rates["last_7"] is not None else shadow_history_rates["last_7"]
    graded_count = _safe_int(market_shadow_totals.get("graded_picks"), 0)
    history_pending_count = history_pending_grading_count(
        history_root / "market_shadow_history.csv",
        prediction_date,
    )
    pending_count = (
        history_pending_count
        if history_pending_count is not None
        else _safe_int(market_shadow_totals.get("pending_picks"), 0)
    )
    market_shadow_rows = _safe_int(market_shadow_totals.get("total_picks"), len(full_market_df))
    kelly_performance = market_shadow_payload.get("kelly_decision_performance", {}) if isinstance(market_shadow_payload, dict) else {}
    kelly_performance_status = (
        _safe_text(kelly_performance.get("status")) if isinstance(kelly_performance, dict) else ""
    ) or "n/a"
    clv_market_summary = clv_market_payload.get("summary", {}) if isinstance(clv_market_payload, dict) else {}
    if not isinstance(clv_market_summary, dict):
        clv_market_summary = {}
    clv_total_rows = _safe_int(clv_market_summary.get("total_rows"), 0)
    clv_close_coverage_count = _safe_int(clv_market_summary.get("close_coverage_count"), 0)
    clv_positive_count = _safe_int(clv_market_summary.get("positive_clv_count"), 0)
    clv_positive_rate = _safe_float(clv_market_summary.get("positive_clv_rate"))
    clv_movement_toward_count = _safe_int(clv_market_summary.get("movement_toward_pick_count"), 0)
    clv_movement_away_count = _safe_int(clv_market_summary.get("movement_away_from_pick_count"), 0)
    clv_missing_close_count = _safe_int(clv_market_summary.get("missing_close_line_count"), 0)
    calibration_bucket_summary = (
        calibration_bucket_payload.get("summary", {})
        if isinstance(calibration_bucket_payload, dict)
        else {}
    )
    player_role_stability_summary = (
        player_role_stability_payload.get("summary", {})
        if isinstance(player_role_stability_payload, dict)
        else {}
    )
    if not isinstance(player_role_stability_summary, dict):
        player_role_stability_summary = {}
    stability_total_evaluated = _safe_int(player_role_stability_summary.get("total_rows_evaluated"), 0)
    stability_stable_count = _safe_int(player_role_stability_summary.get("stable_count"), 0)
    stability_mostly_stable_count = _safe_int(player_role_stability_summary.get("mostly_stable_count"), 0)
    stability_mixed_count = _safe_int(player_role_stability_summary.get("mixed_count"), 0)
    stability_volatile_count = _safe_int(player_role_stability_summary.get("volatile_count"), 0)
    stability_highly_volatile_count = _safe_int(player_role_stability_summary.get("highly_volatile_count"), 0)
    stability_unknown_count = _safe_int(player_role_stability_summary.get("unknown_count"), 0)
    stability_top_examples = player_role_stability_summary.get("top_volatile_examples") or []
    meta_label_promotion_summary = (
        meta_label_promotion_payload.get("summary", {})
        if isinstance(meta_label_promotion_payload, dict)
        else {}
    )
    if not isinstance(meta_label_promotion_summary, dict):
        meta_label_promotion_summary = {}
    meta_label_total_evaluated = _safe_int(meta_label_promotion_summary.get("total_rows_evaluated"), 0)
    meta_label_strong_count = _safe_int(meta_label_promotion_summary.get("shadow_strong_review_candidate_count"), 0)
    meta_label_watch_count = _safe_int(meta_label_promotion_summary.get("shadow_watch_candidate_count"), 0)
    meta_label_neutral_count = _safe_int(meta_label_promotion_summary.get("shadow_neutral_count"), 0)
    meta_label_weak_count = _safe_int(meta_label_promotion_summary.get("shadow_weak_count"), 0)
    meta_label_avoid_count = _safe_int(meta_label_promotion_summary.get("shadow_avoid_review_count"), 0)
    meta_label_top_candidates = meta_label_promotion_summary.get("top_strong_candidates") or []
    if not isinstance(calibration_bucket_summary, dict):
        calibration_bucket_summary = {}
    calibration_graded_rows_used = _safe_int(calibration_bucket_summary.get("total_graded_rows_used"), 0)
    calibration_worst_overconfident = (
        _safe_text(calibration_bucket_summary.get("worst_overconfident_bucket_label"))
        or "n/a"
    )
    calibration_best_calibrated = (
        _safe_text(calibration_bucket_summary.get("best_calibrated_bucket_label"))
        or "n/a"
    )
    calibration_tiny_small_count = _safe_int(
        calibration_bucket_summary.get("tiny_small_sample_warning_count"),
        0,
    )

    board_counts = board_diagnostics.get("board_counts", {}) if isinstance(board_diagnostics, dict) else {}
    board_count_note = ""
    if isinstance(board_counts, dict) and board_counts:
        board_count_note = (
            f"diagnostics qualified_pool={_safe_int(board_counts.get('qualified_pool'), 0)}, "
            f"rejected={_safe_int(board_counts.get('rejected'), 0)}"
        )
    unsupported_active_summary = unsupported_active_operator_market_drop_summary(quality_payload)
    if int(unsupported_active_summary.get("total_rows_dropped", 0) or 0) <= 0:
        unsupported_active_summary = unsupported_active_operator_market_drop_summary(board_diagnostics)
    unsupported_active_line = format_unsupported_active_operator_market_drop_line(unsupported_active_summary)
    identity_summary = identity_quarantine_summary(quality_payload)
    if int(identity_summary.get("total_rows_dropped", 0) or 0) <= 0:
        identity_summary = identity_quarantine_summary(board_diagnostics)
    if int(identity_summary.get("total_rows_dropped", 0) or 0) <= 0:
        identity_summary = identity_quarantine_summary(game_payload)
    identity_quarantine_line = format_identity_quarantine_line(identity_summary)
    source_identity_total = _safe_int(source_identity_summary.get("source_identity_conflict_count"), 0)
    source_identity_player_total = _safe_int(
        source_identity_summary.get("source_identity_conflicted_player_count"),
        source_identity_total,
    )
    source_identity_operator_rows = _safe_int(
        source_identity_summary.get("source_identity_conflicted_operator_rows"),
        0,
    )
    source_identity_elite_rows = _safe_int(
        source_identity_summary.get("source_identity_conflicted_elite_rows"),
        0,
    )
    source_identity_kelly_rows = _safe_int(
        source_identity_summary.get("source_identity_conflicted_kelly_rows"),
        0,
    )
    source_identity_watchlist_rows = _safe_int(
        source_identity_summary.get("source_identity_conflicted_watchlist_rows"),
        0,
    )
    source_identity_paper_rows = _safe_int(
        source_identity_summary.get("source_identity_conflicted_paper_rows"),
        0,
    )
    source_identity_full_market_players = _safe_int(
        source_identity_summary.get("source_identity_conflicted_full_market_players"),
        0,
    )
    source_identity_elite_players = _safe_int(
        source_identity_summary.get("source_identity_conflicted_elite_players"),
        0,
    )
    source_identity_kelly_players = _safe_int(
        source_identity_summary.get("source_identity_conflicted_kelly_players"),
        0,
    )
    source_identity_watchlist_players = _safe_int(
        source_identity_summary.get("source_identity_conflicted_watchlist_players"),
        0,
    )
    source_identity_paper_players = _safe_int(
        source_identity_summary.get("source_identity_conflicted_paper_players"),
        0,
    )
    source_identity_safety = _safe_text(
        source_identity_summary.get("source_identity_conflict_safety_state")
    ) or "clear"

    full_manual_df = _filter_truthy(full_market_display_df, "manual_review_required")
    full_same_opponent_df = _filter_truthy(full_market_display_df, "same_opponent_under_warning")
    review_before_bet_df = _filter_truthy(kelly_df, "review_before_bet")
    if same_opponent_file_df.empty:
        same_opponent_examples_df = full_same_opponent_df
    else:
        same_opponent_examples_df = same_opponent_file_df
    runtime_safety = _runtime_safety_summary(
        prediction_date=prediction_date,
        paths=paths,
        warnings=warnings,
        runtime_mode=runtime_mode,
        force_past_date=force_past_date,
        force_outputs=force_outputs,
        kelly_bankroll=kelly_bankroll,
    )

    lines: list[str] = []
    lines.append("=" * 40)
    lines.append(f"COURTVISION DAILY CARD - {prediction_date}")
    lines.append("=" * 40)
    lines.append(f"prediction_date: {prediction_date}")
    lines.append(f"run_health: {run_health_status} - {run_health_reason}")
    lines.append(f"final_decision: {final_decision}")
    if warnings:
        lines.append(f"report_warnings: {len(warnings)}")
    lines.append("")

    lines.append("Runtime Safety")
    lines.append("-" * 40)
    lines.append(f"- prediction_date: {runtime_safety['prediction_date']}")
    lines.append(f"- COURTVISION_MODE: {runtime_safety['COURTVISION_MODE']}")
    lines.append(f"- ForcePastDate: {runtime_safety['ForcePastDate']}")
    lines.append(f"- ForceOutputs: {runtime_safety['ForceOutputs']}")
    lines.append(f"- Kelly bankroll: {runtime_safety['KellyBankroll']}")
    lines.append(f"- artifact_manifest_status: {runtime_safety['artifact_manifest_status']}")
    fatal_missing = runtime_safety["fatal_missing"]
    if isinstance(fatal_missing, int) and fatal_missing > 0:
        lines.append(f"- fatal_missing: {fatal_missing} (DANGER: core operator artifacts missing)")
    else:
        lines.append(f"- fatal_missing: {fatal_missing}")
    lines.append("")

    lines.append("Slate Summary")
    lines.append("-" * 40)
    lines.append(f"- games count: {games_count}")
    lines.append(f"- matchups: {', '.join(matchup_list) if matchup_list else 'n/a'}")
    lines.append(f"- provider/live status: {provider_status}")
    lines.append(f"- provider line sources: {line_source_text}")
    lines.append(f"- odds count: {odds_count}")
    lines.append(f"- stale odds count: {_safe_text(stale_odds_count) or 'n/a'}")
    lines.append(f"- {_context_status(runtime_root / 'diagnostics' / f'injury_context_diagnostics_{prediction_date}.json', 'injury', injury_payload)}")
    lines.append(f"- {_context_status(runtime_root / 'diagnostics' / f'game_context_{prediction_date}.json', 'game', game_payload)}")
    lines.append("")

    lines.append("Board Summary")
    lines.append("-" * 40)
    lines.append(f"- elite picks count: {elite_count}")
    lines.append(f"- full market candidates count: {full_market_count}")
    lines.append(f"- near-elite review count: {near_elite_count}")
    lines.append(f"- near-elite policy: {NEAR_ELITE_REVIEW_ONLY_NOTE}")
    if unsupported_active_line:
        lines.append(f"- {unsupported_active_line}")
    if identity_quarantine_line:
        lines.append(f"- {identity_quarantine_line}")
    if (
        source_identity_total > 0
        or source_identity_operator_rows
        or source_identity_elite_rows
        or source_identity_kelly_rows
        or source_identity_watchlist_rows
        or source_identity_paper_rows
    ):
        lines.append(
            "- source identity diagnostic conflicts: "
            f"rows={source_identity_total}, unique_players={source_identity_player_total}"
        )
        lines.append(
            "- source identity operator row exposure: "
            f"full_market={source_identity_operator_rows}, "
            f"elite={source_identity_elite_rows}, "
            f"kelly={source_identity_kelly_rows}, "
            f"watchlist={source_identity_watchlist_rows}, "
            f"paper={source_identity_paper_rows}"
        )
        lines.append(
            "- source identity unique-player exposure: "
            f"full_market={source_identity_full_market_players}, "
            f"elite={source_identity_elite_players}, "
            f"kelly={source_identity_kelly_players}, "
            f"watchlist={source_identity_watchlist_players}, "
            f"paper={source_identity_paper_players}"
        )
        if source_identity_safety == "blocking_manual_review_required":
            lines.append(
                "- source identity safety: BLOCKING manual review required "
                "(source-conflicted row reached Elite/Kelly)"
            )
        elif source_identity_operator_rows or source_identity_watchlist_rows or source_identity_paper_rows:
            lines.append(
                "- source identity safety: non-blocking diagnostic warning "
                "(no Elite/Kelly source-conflict exposure)"
            )
            lines.extend(_source_identity_example_lines(source_identity_summary, limit=3))
        else:
            lines.append("- source identity safety: clear")
    lines.append(f"- SGP candidates count: {sgp_count}")
    lines.append(f"- Kelly rows count: {kelly_rows_count}")
    lines.append(f"- Kelly eligible count: {kelly_eligible_count}")
    lines.append(f"- manual review count: {manual_review_count}")
    lines.append(f"- review_before_bet count: {review_before_bet_count}")
    lines.append(f"- high caution OVER count: {high_caution_count}")
    lines.append(f"- combo UNDER watchlist count: {combo_under_count}")
    lines.append(f"- same-opponent warning count: {same_opponent_warning_count}")
    if board_count_note:
        lines.append(f"- {board_count_note}")
    lines.append("")

    lines.append("Market Breakdown")
    lines.append("-" * 40)
    market_counts = _market_counts(full_market_df)
    if market_counts:
        for market, count in sorted(market_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"{market}: {count}")
    else:
        lines.append("n/a")
    lines.append("")

    lines.append("Elite Picks")
    lines.append("-" * 40)
    if elite_count > 0 and not elite_display_df.empty:
        lines.extend(_render_pick_table(elite_display_df, limit=25))
    else:
        lines.append("NO ELITE PICKS - all candidates were filtered by safety, quality, context, or exposure gates.")
    lines.append("")

    if elite_count <= 0:
        lines.append("Top Candidate Preview")
        lines.append("-" * 40)
        if full_market_df.empty:
            lines.append("n/a")
        else:
            lines.append(
                "Preview rows are diagnostic only and are not betting recommendations unless they appear in Elite Picks/Kelly."
            )
            preview_df = _with_preview_actions(
                full_market_display_df.copy(),
                final_decision=final_decision,
                elite_count=elite_count,
                high_caution_df=high_caution_df,
                combo_under_df=combo_under_df,
                same_opponent_df=same_opponent_file_df,
            )
            lines.extend(_render_pick_table(preview_df, limit=10, include_bucket=True))
        lines.append("")

    lines.append("Watchlists")
    lines.append("-" * 40)
    lines.append(f"- high caution OVER: {high_caution_count}")
    lines.extend(_example_lines(high_caution_df, limit=3))
    lines.append(f"- combo UNDER watchlist: {combo_under_count}")
    lines.extend(_example_lines(combo_under_df, limit=3))
    lines.append(f"- same-opponent UNDER warnings: {same_opponent_warning_count}")
    lines.extend(_example_lines(same_opponent_examples_df, limit=3))
    lines.append(f"- manual review required: {manual_review_count}")
    lines.extend(_example_lines(full_manual_df, limit=3))
    lines.append(f"- review_before_bet: {review_before_bet_count}")
    lines.extend(_example_lines(review_before_bet_df, limit=3))
    lines.append("")

    lines.append("Near-Elite Review")
    lines.append("-" * 40)
    lines.append(f"- near-elite review count: {near_elite_count}")
    lines.append(f"- {NEAR_ELITE_REVIEW_ONLY_NOTE}")
    lines.append("- top 5 near-elite candidates:")
    if near_elite_df.empty:
        lines.append("  - None")
    else:
        for _, row in _sort_candidates(near_elite_df, 5).iterrows():
            lines.append(f"  - {near_elite_row_line(row)}")
    lines.append("")

    if final_decision == "REVIEW REQUIRED":
        lines.append("Why Review Required?")
        lines.append("-" * 40)
        review_lines: list[str] = []
        if elite_manual_review_count > 0:
            review_lines.append(
                f"- {elite_manual_review_count} elite {_plural(elite_manual_review_count, 'candidate')} "
                f"{'requires' if elite_manual_review_count == 1 else 'require'} manual review."
            )
        elif manual_review_count > 0:
            review_lines.append(
                f"- {manual_review_count} {_plural(manual_review_count, 'candidate')} "
                f"{'requires' if manual_review_count == 1 else 'require'} manual review."
            )
        if review_before_bet_count > 0:
            review_lines.append(
                f"- {review_before_bet_count} {_plural(review_before_bet_count, 'candidate')} "
                f"{'is' if review_before_bet_count == 1 else 'are'} marked review_before_bet."
            )
        if same_opponent_warning_count > 0:
            review_lines.append(
                f"- {same_opponent_warning_count} same-opponent UNDER "
                f"{_plural(same_opponent_warning_count, 'warning')} "
                f"{'is' if same_opponent_warning_count == 1 else 'are'} present."
            )
        if kelly_rows_count > 0 and (
            manual_review_count > 0 or review_before_bet_count > 0 or kelly_hold_count > 0
        ):
            review_lines.append(
                "- Kelly exists, but stake should not be treated as clean until review is complete."
            )
        if source_identity_blocking_count > 0:
            review_lines.append(
                "- Source-conflicted identity reached Elite/Kelly; manual review is required before betting."
            )
        if not review_lines:
            review_lines.append("- Review flags are present on the board or Kelly artifact.")
        lines.extend(review_lines)
        lines.append("")

    lines.append("Grading Snapshot")
    lines.append("-" * 40)
    lines.append(f"- market shadow rows: {market_shadow_rows}")
    lines.append(f"- graded rows: {graded_count}")
    lines.append(f"- pending grading: {pending_count}")
    lines.append(f"- market shadow hit rate: {_format_rate(market_shadow_totals.get('hit_rate'))}")
    lines.append(f"- all-time hit rate: {_format_rate(all_time_rate)}")
    lines.append(f"- last 7 slates hit rate: {_format_rate(last_7_rate)}")
    lines.append(f"- Kelly performance status: {kelly_performance_status}")
    lines.append("")

    lines.append("CLV / Market Movement - Shadow Only")
    lines.append("-" * 40)
    lines.append(f"- close coverage count: {clv_close_coverage_count} / {clv_total_rows}")
    lines.append(f"- positive CLV count/rate: {clv_positive_count} / {_format_rate(clv_positive_rate)}")
    lines.append(f"- movement toward pick count: {clv_movement_toward_count}")
    lines.append(f"- movement away from pick count: {clv_movement_away_count}")
    lines.append(f"- missing close-line count: {clv_missing_close_count}")
    lines.append(f"- {CLV_DIAGNOSTIC_ONLY_NOTE}")
    lines.append("")

    lines.append("Calibration Health - Shadow Only")
    lines.append("-" * 40)
    lines.append(f"- total graded rows used: {calibration_graded_rows_used}")
    lines.append(f"- worst overconfident bucket: {calibration_worst_overconfident}")
    lines.append(f"- best calibrated bucket: {calibration_best_calibrated}")
    lines.append(f"- tiny/small sample warning count: {calibration_tiny_small_count}")
    lines.append(f"- {CALIBRATION_BUCKET_DIAGNOSTIC_ONLY_NOTE}")
    lines.append("")

    lines.append("Player Role Stability - Shadow Only")
    lines.append("-" * 40)
    lines.append(f"- total rows evaluated: {stability_total_evaluated}")
    lines.append(f"- stable count: {stability_stable_count}")
    lines.append(f"- mostly stable count: {stability_mostly_stable_count}")
    lines.append(f"- mixed count: {stability_mixed_count}")
    lines.append(f"- volatile count: {stability_volatile_count}")
    lines.append(f"- highly volatile count: {stability_highly_volatile_count}")
    lines.append(f"- unknown count: {stability_unknown_count}")
    lines.append("- top volatile examples:")
    if not stability_top_examples:
        lines.append("  - none")
    else:
        for ex in stability_top_examples:
            reasons = "; ".join(ex.get("role_stability_reasons", []))
            lines.append(
                f"  - {ex.get('player_name')} ({ex.get('team')}): score={ex.get('role_stability_score')} "
                f"bucket={ex.get('role_stability_bucket')} reasons=[{reasons}]"
            )
    lines.append(f"- {PLAYER_ROLE_STABILITY_DIAGNOSTIC_ONLY_NOTE}")
    lines.append("")

    lines.append("Meta-Label Promotion - Shadow Only")
    lines.append("-" * 40)
    lines.append(f"- total rows evaluated: {meta_label_total_evaluated}")
    lines.append(f"- shadow strong review candidate count: {meta_label_strong_count}")
    lines.append(f"- shadow watch candidate count: {meta_label_watch_count}")
    lines.append(f"- shadow neutral count: {meta_label_neutral_count}")
    lines.append(f"- shadow weak count: {meta_label_weak_count}")
    lines.append(f"- shadow avoid review count: {meta_label_avoid_count}")
    lines.append("- top strong candidates:")
    if not meta_label_top_candidates:
        lines.append("  - none")
    else:
        for ex in meta_label_top_candidates:
            reasons = "; ".join(ex.get("reason_codes", []))
            lines.append(
                f"  - {ex.get('player_name')} ({ex.get('team')}): score={ex.get('meta_label_rules_score')} "
                f"bucket={ex.get('meta_label_bucket')} reasons=[{reasons}]"
            )
    lines.append(f"- {META_LABEL_DIAGNOSTIC_ONLY_NOTE}")
    lines.append("")

    lines.extend(_completion_state_lines(completion_state_payload, paths["completion_state_audit_json"], prediction_date))
    lines.append("")

    lines.append("Audit Warning Summary")
    lines.append("-" * 40)
    lines.extend(
        _audit_warning_summary_lines(
            full_market_sanity_payload=full_market_sanity_payload,
            candidate_quality_drift_payload=candidate_quality_drift_payload,
        )
    )
    lines.append("")

    if final_decision == "NO BET":
        lines.append("NO BET Reason Summary")
        lines.append("-" * 40)
        lines.extend(
            _no_bet_reason_lines(
                elite_count=elite_count,
                high_caution_count=high_caution_count,
                combo_under_count=combo_under_count,
                completion_state_payload=completion_state_payload,
            )
        )
        lines.append("")

        lines.append("Elite Rejection Summary")
        lines.append("-" * 40)
        lines.extend(
            _elite_rejection_summary_lines(
                quality_payload=quality_payload,
                board_diagnostics=board_diagnostics,
                high_caution_count=high_caution_count,
                combo_under_count=combo_under_count,
                same_opponent_warning_count=same_opponent_warning_count,
            )
        )
        lines.append("")

    lines.append("Final Decision")
    lines.append("-" * 40)
    if final_decision == "REVIEW REQUIRED" and source_identity_blocking_count > 0:
        lines.append("REVIEW REQUIRED - source identity conflict reached Elite/Kelly.")
    elif final_decision == "REVIEW REQUIRED":
        lines.append("REVIEW REQUIRED — elite candidates exist, but review flags are present.")
    else:
        lines.append(final_decision)
    lines.append("")

    lines.append("Files Written")
    lines.append("-" * 40)
    lines.extend(_files_written_lines(paths))
    if missing_required:
        lines.append("")
        lines.append("Missing Required Artifacts")
        lines.append("-" * 40)
        lines.extend(f"- {path}" for path in missing_required)

    card_text = "\n".join(lines)
    payload = {
        "prediction_date": prediction_date,
        "final_decision": final_decision,
        "elite_count": elite_count,
        "full_market_count": full_market_count,
        "near_elite_review_count": near_elite_count,
        "sgp_count": sgp_count,
        "kelly_rows_count": kelly_rows_count,
        "kelly_eligible_count": kelly_eligible_count,
        "identity_quarantine": identity_summary,
        "source_identity_conflict": source_identity_summary,
        "manual_review_count": manual_review_count,
        "review_before_bet_count": review_before_bet_count,
        "high_caution_over_count": high_caution_count,
        "combo_under_watchlist_count": combo_under_count,
        "same_opponent_warning_count": same_opponent_warning_count,
        "unsupported_active_operator_markets": unsupported_active_summary,
        "provider_status": provider_status,
        "runtime_safety": runtime_safety,
        "clv_market_movement": clv_market_summary,
        "calibration_bucket_report": calibration_bucket_summary,
        "player_role_stability_report": player_role_stability_summary,
        "meta_label_promotion_report": meta_label_promotion_summary,
        "missing_required": missing_required,
        "completion_state_audit_status": _safe_text(completion_state_payload.get("report_agreement_status")) if completion_state_payload else "missing",
        "warnings": warnings,
    }
    return card_text, payload


def write_operator_card_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    runtime_mode: str | None = None,
    force_past_date: bool | str | None = None,
    force_outputs: bool | str | None = None,
    kelly_bankroll: str | int | float | None = None,
    force: bool = False,
) -> tuple[Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    guard_no_existing_artifact(
        output_path=paths["operator_card"],
        force=force,
        caller="write_operator_card_outputs",
        artifact_label="operator_card",
    )
    paths["operator_card"].parent.mkdir(parents=True, exist_ok=True)
    text, payload = build_operator_card(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        runtime_mode=runtime_mode,
        force_past_date=force_past_date,
        force_outputs=force_outputs,
        kelly_bankroll=kelly_bankroll,
    )
    paths["operator_card"].write_text(text + "\n", encoding="utf-8")
    return paths["operator_card"], payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the CourtVision daily operator card.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--runtime-mode", help="Runtime mode visible in the operator safety summary.")
    parser.add_argument("--force-past-date", choices=("true", "false"), help="Whether -ForcePastDate was active.")
    parser.add_argument("--force-outputs", choices=("true", "false"), help="Whether output overwrite forcing was active.")
    parser.add_argument("--kelly-bankroll", help="Bankroll value passed to Kelly staking.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow an existing operator_card_DATE.txt artifact to be overwritten intentionally.",
    )
    args = parser.parse_args(argv)

    output_path, payload = write_operator_card_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        runtime_mode=args.runtime_mode,
        force_past_date=args.force_past_date,
        force_outputs=args.force_outputs,
        kelly_bankroll=args.kelly_bankroll,
        force=args.force,
    )
    print(f"operator_card_txt={output_path}")
    print(f"operator_card_decision={payload['final_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
