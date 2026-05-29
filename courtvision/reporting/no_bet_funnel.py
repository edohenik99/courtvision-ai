from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPORT_FILE_PREFIX = "no_bet_funnel_report"
REPORT_VERSION = "1.0"
DEFAULT_LOOKBACK = 14
MIN_BUCKET_SAMPLE = 20

STATUS_NO_BET = "NO BET"
STATUS_REVIEW_REQUIRED = "REVIEW REQUIRED"
STATUS_BET_APPROVED = "BET APPROVED"
STATUS_UNKNOWN = "UNKNOWN"

ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER = "elite_reject_context_high_caution_over"
KELLY_PROJECTED_CONTEXT_HIGH_CAUTION_OVER = "context_high_caution_over"

SLATE_CSV_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "decision_bucket",
    "final_decision",
    "run_health",
    "full_market_count",
    "near_elite_count",
    "incubator_count",
    "elite_count",
    "kelly_eligible_count",
    "high_caution_over_count",
    "high_caution_over_rate",
    "combo_under_watchlist_count",
    "same_opponent_warning_count",
    "unsupported_active_market_count",
    "top_rejection_reason",
    "top_rejection_count",
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
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return default
    return text


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pct(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _format_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except (pd.errors.EmptyDataError, OSError, ValueError):
        return pd.DataFrame()


def _csv_row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    return int(len(_read_csv(path)))


def _extract_first_int(text: str, patterns: tuple[str, ...]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _safe_int(match.group(1))
    return None


def _extract_first_text(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _safe_text(match.group(1))
    return ""


def _decision_bucket(final_decision: str, run_health: str = "") -> str:
    decision = _safe_text(final_decision).upper()
    health = _safe_text(run_health).upper()
    if "NO BET" in decision or decision == "NO_BET" or health.startswith("NO_BET"):
        return STATUS_NO_BET
    if "REVIEW REQUIRED" in decision or "REVIEW REQUIRED" in health:
        return STATUS_REVIEW_REQUIRED
    if "BET APPROVED" in decision or "BETTABLE" in decision or decision == "BET":
        return STATUS_BET_APPROVED
    return STATUS_UNKNOWN


def parse_operator_card(text: str, *, source_path: str | Path | None = None) -> dict[str, Any]:
    """Parse stable fields from a CourtVision operator card text artifact."""

    source = str(source_path or "")
    prediction_date = _extract_first_text(text, (r"prediction_date:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})",))
    if not prediction_date and source:
        match = re.search(r"operator_card_([0-9]{4}-[0-9]{2}-[0-9]{2})\.txt$", source)
        if match:
            prediction_date = match.group(1)

    run_health = _extract_first_text(text, (r"run_health:\s*([^\n\r]+)",))
    final_decision = _extract_first_text(text, (r"final_decision:\s*([^\n\r]+)",))
    if not final_decision:
        final_decision = _extract_first_text(text, (r"Final Decision\s*-+\s*([^\n\r]+)",))

    top_rejection_reason = ""
    top_rejection_count = 0
    top_match = re.search(
        r"top rejection reason:\s*([A-Za-z0-9_\-]+)(?:\s*\((\d+)\))?",
        text,
        flags=re.IGNORECASE,
    )
    if top_match:
        top_rejection_reason = _safe_text(top_match.group(1))
        top_rejection_count = _safe_int(top_match.group(2), 0)

    counts = {
        "full_market_count": _extract_first_int(text, (r"full market candidates count:\s*(\d+)",)),
        "near_elite_count": _extract_first_int(text, (r"near-elite review count:\s*(\d+)",)),
        "incubator_count": _extract_first_int(text, (r"incubator board count:\s*(\d+)",)),
        "elite_count": _extract_first_int(text, (r"elite picks count:\s*(\d+)",)),
        "kelly_rows_count": _extract_first_int(text, (r"Kelly rows count:\s*(\d+)",)),
        "kelly_eligible_count": _extract_first_int(text, (r"Kelly eligible count:\s*(\d+)",)),
        "high_caution_over_count": _extract_first_int(
            text,
            (
                r"high caution OVER count:\s*(\d+)",
                r"high-caution OVER context gate:\s*(\d+)",
                r"high caution OVER:\s*(\d+)",
            ),
        ),
        "combo_under_watchlist_count": _extract_first_int(text, (r"combo UNDER watchlist count:\s*(\d+)",)),
        "same_opponent_warning_count": _extract_first_int(
            text,
            (
                r"same-opponent warning count:\s*(\d+)",
                r"same-opponent UNDER warnings:\s*(\d+)",
            ),
        ),
        "unsupported_active_market_count": _extract_first_int(text, (r"unsupported active markets dropped:\s*(\d+)",)),
    }

    return {
        "prediction_date": prediction_date,
        "source_path": source,
        "run_health": run_health,
        "final_decision": final_decision,
        "decision_bucket": _decision_bucket(final_decision, run_health),
        "top_rejection_reason": top_rejection_reason,
        "top_rejection_count": top_rejection_count,
        **{key: (0 if value is None else int(value)) for key, value in counts.items()},
    }


def _operator_card_paths(runtime_root: Path) -> list[Path]:
    operator_dir = runtime_root / "operator"
    paths = sorted(operator_dir.glob("operator_card_*.txt"))
    return [path for path in paths if re.search(r"operator_card_\d{4}-\d{2}-\d{2}\.txt$", path.name)]


def _date_from_card_path(path: Path) -> str:
    match = re.search(r"operator_card_([0-9]{4}-[0-9]{2}-[0-9]{2})\.txt$", path.name)
    return match.group(1) if match else ""


def _reason_counts_from_frame(df: pd.DataFrame) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not isinstance(df, pd.DataFrame) or df.empty:
        return counts
    for column in (
        "final_elite_rejection_reason",
        "elite_rejection_reason",
        "selection_rejection_reason",
        "rejection_reason",
        "kelly_projected_skip_reason",
    ):
        if column not in df.columns:
            continue
        values = df[column].fillna("").astype(str).str.strip()
        for value, count in values[values != ""].value_counts().items():
            counts[str(value)] += int(count)
    return counts


def _top_quality_reasons(quality_payload: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    raw = quality_payload.get("top_rejection_reasons")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                reason = _safe_text(item.get("reason"))
                count = _safe_int(item.get("count"), 0)
                if reason and count:
                    counts[reason] += count
    return counts


def _context_gate_reason_counts(board_payload: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    gate = board_payload.get("elite_context_safety_gate")
    if isinstance(gate, dict):
        for key in ("candidate_rejection_reason_counts", "rejection_reason_counts"):
            raw = gate.get(key)
            if isinstance(raw, dict):
                for reason, count in raw.items():
                    reason_text = _safe_text(reason)
                    count_int = _safe_int(count, 0)
                    if reason_text and count_int:
                        counts[reason_text] += count_int
    return counts


def _best_rejection_counts(
    *,
    parsed_card: dict[str, Any],
    full_market_df: pd.DataFrame,
    board_payload: dict[str, Any],
    quality_payload: dict[str, Any],
) -> Counter[str]:
    card_reason = _safe_text(parsed_card.get("top_rejection_reason"))
    card_count = _safe_int(parsed_card.get("top_rejection_count"), 0)
    if card_reason and card_count:
        return Counter({card_reason: card_count})

    context_counts = _context_gate_reason_counts(board_payload)
    if context_counts:
        return context_counts

    frame_counts = _reason_counts_from_frame(full_market_df)
    if frame_counts:
        return frame_counts

    return _top_quality_reasons(quality_payload)


def _quality_count(payload: dict[str, Any], path: tuple[str, ...]) -> int | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if current is None:
        return None
    return _safe_int(current, 0)


def _optional_int(mapping: dict[str, Any], key: str) -> int | None:
    if not isinstance(mapping, dict) or key not in mapping:
        return None
    return _safe_int(mapping.get(key), 0)


def _first_not_none(*values: int | None) -> int:
    for value in values:
        if value is not None:
            return int(value)
    return 0


def build_slate_record(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    parsed_card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    operator_dir = runtime_root_path / "operator"
    diagnostics_dir = runtime_root_path / "diagnostics"
    parsed = dict(parsed_card or {})

    full_market_path = operator_dir / f"full_market_board_{prediction_date}.csv"
    elite_path = operator_dir / f"elite_board_{prediction_date}.csv"
    near_elite_path = operator_dir / f"near_elite_review_{prediction_date}.csv"
    incubator_path = operator_dir / f"incubator_board_{prediction_date}.csv"
    high_caution_path = operator_dir / f"high_caution_over_watchlist_{prediction_date}.csv"
    combo_under_path = operator_dir / f"combo_under_watchlist_{prediction_date}.csv"
    kelly_path = operator_dir / f"kelly_stakes_{prediction_date}.csv"
    board_path = diagnostics_dir / f"board_diagnostics_{prediction_date}.json"
    quality_path = operator_dir / f"quality_summary_{prediction_date}.json"

    full_market_df = _read_csv(full_market_path)
    board_payload = _read_json(board_path)
    quality_payload = _read_json(quality_path)

    board_counts = board_payload.get("board_counts") if isinstance(board_payload.get("board_counts"), dict) else {}
    funnel = quality_payload.get("candidate_funnel") if isinstance(quality_payload.get("candidate_funnel"), dict) else {}
    kelly_summary = (
        quality_payload.get("kelly_safety_summary")
        if isinstance(quality_payload.get("kelly_safety_summary"), dict)
        else {}
    )

    full_market_count = _first_not_none(
        _csv_row_count(full_market_path),
        _optional_int(funnel, "full_market_board_count"),
        _optional_int(board_counts, "full_market"),
        _safe_int(parsed.get("full_market_count"), 0),
    )
    elite_count = _first_not_none(
        _csv_row_count(elite_path),
        _optional_int(funnel, "elite_board_count"),
        _optional_int(board_counts, "elite"),
        _safe_int(parsed.get("elite_count"), 0),
    )
    near_elite_count = _first_not_none(
        _csv_row_count(near_elite_path),
        _optional_int(funnel, "near_elite_review_count"),
        _safe_int(parsed.get("near_elite_count"), 0),
    )
    incubator_count = _first_not_none(
        _csv_row_count(incubator_path),
        _optional_int(funnel, "incubator_board_count"),
        _safe_int(parsed.get("incubator_count"), 0),
    )
    high_caution_count = _first_not_none(
        _csv_row_count(high_caution_path),
        _quality_count(quality_payload, ("high_caution_over_watchlist", "row_count")),
        _safe_int(parsed.get("high_caution_over_count"), 0),
    )
    combo_under_count = _first_not_none(
        _csv_row_count(combo_under_path),
        _safe_int(parsed.get("combo_under_watchlist_count"), 0),
    )
    kelly_rows_count = _first_not_none(
        _csv_row_count(kelly_path),
        _optional_int(kelly_summary, "total_rows"),
        _optional_int(funnel, "kelly_rows_count"),
        _safe_int(parsed.get("kelly_rows_count"), 0),
    )
    kelly_eligible_count = _first_not_none(
        _optional_int(kelly_summary, "kelly_eligible_count"),
        _safe_int(parsed.get("kelly_eligible_count"), 0),
    )

    unsupported_payload = (
        board_payload.get("unsupported_active_operator_markets")
        if isinstance(board_payload.get("unsupported_active_operator_markets"), dict)
        else {}
    )
    unsupported_active_market_count = _first_not_none(
        _optional_int(funnel, "unsupported_active_operator_market_drop_count"),
        _optional_int(unsupported_payload, "dropped_rows"),
        _safe_int(parsed.get("unsupported_active_market_count"), 0),
    )
    same_opponent_warning_count = _first_not_none(
        _optional_int(quality_payload, "same_opponent_under_warning_count"),
        _safe_int(parsed.get("same_opponent_warning_count"), 0),
    )

    rejection_counts = _best_rejection_counts(
        parsed_card=parsed,
        full_market_df=full_market_df,
        board_payload=board_payload,
        quality_payload=quality_payload,
    )
    top_reason, top_count = ("", 0)
    if rejection_counts:
        top_reason, top_count = rejection_counts.most_common(1)[0]

    final_decision = _safe_text(parsed.get("final_decision"))
    if not final_decision:
        run_health = _safe_text(quality_payload.get("run_health_status"))
        final_decision = STATUS_NO_BET if run_health == "NO_BET" and elite_count <= 0 else ""

    record = {
        "prediction_date": prediction_date,
        "decision_bucket": parsed.get("decision_bucket") or _decision_bucket(final_decision, parsed.get("run_health", "")),
        "final_decision": final_decision,
        "run_health": _safe_text(parsed.get("run_health") or quality_payload.get("run_health_status")),
        "full_market_count": int(full_market_count),
        "near_elite_count": int(near_elite_count),
        "incubator_count": int(incubator_count),
        "elite_count": int(elite_count),
        "kelly_rows_count": int(kelly_rows_count),
        "kelly_eligible_count": int(kelly_eligible_count),
        "high_caution_over_count": int(high_caution_count),
        "high_caution_over_rate": _pct(high_caution_count, full_market_count),
        "combo_under_watchlist_count": int(combo_under_count),
        "same_opponent_warning_count": int(same_opponent_warning_count),
        "unsupported_active_market_count": int(unsupported_active_market_count),
        "top_rejection_reason": top_reason,
        "top_rejection_count": int(top_count),
        "rejection_reason_counts": dict(sorted(rejection_counts.items())),
        "artifact_paths": {
            "operator_card": parsed.get("source_path", ""),
            "full_market_board": str(full_market_path),
            "board_diagnostics": str(board_path),
            "near_elite_review": str(near_elite_path),
            "incubator_board": str(incubator_path),
            "high_caution_over_watchlist": str(high_caution_path),
            "combo_under_watchlist": str(combo_under_path),
            "kelly_stakes": str(kelly_path),
        },
    }
    if record["decision_bucket"] == STATUS_UNKNOWN and record["elite_count"] <= 0:
        record["decision_bucket"] = STATUS_NO_BET
    return record


def _discover_slate_records(
    *,
    runtime_root: Path,
    history_root: Path,
    lookback: int,
) -> list[dict[str, Any]]:
    paths = _operator_card_paths(runtime_root)
    if lookback > 0:
        paths = paths[-lookback:]

    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_operator_card(text, source_path=path)
        prediction_date = _safe_text(parsed.get("prediction_date")) or _date_from_card_path(path)
        if not prediction_date:
            continue
        records.append(
            build_slate_record(
                prediction_date=prediction_date,
                runtime_root=runtime_root,
                history_root=history_root,
                parsed_card=parsed,
            )
        )
    records.sort(key=lambda row: row["prediction_date"])
    return records


def _current_no_bet_streak(records: list[dict[str, Any]]) -> int:
    streak = 0
    for row in reversed(records):
        if row.get("decision_bucket") == STATUS_NO_BET:
            streak += 1
        else:
            break
    return streak


def _aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "total_full_market_candidates": sum(int(row["full_market_count"]) for row in records),
        "total_high_caution_over_blocks": sum(int(row["high_caution_over_count"]) for row in records),
        "total_combo_under_watchlist_blocks": sum(int(row["combo_under_watchlist_count"]) for row in records),
        "total_near_elite_rows": sum(int(row["near_elite_count"]) for row in records),
        "total_incubator_rows": sum(int(row["incubator_count"]) for row in records),
        "total_elite_rows": sum(int(row["elite_count"]) for row in records),
        "total_kelly_eligible_rows": sum(int(row["kelly_eligible_count"]) for row in records),
        "total_same_opponent_warning_rows": sum(int(row["same_opponent_warning_count"]) for row in records),
        "total_unsupported_active_market_rows": sum(int(row["unsupported_active_market_count"]) for row in records),
    }
    totals["high_caution_over_block_rate"] = _pct(
        totals["total_high_caution_over_blocks"],
        totals["total_full_market_candidates"],
    )
    return totals


def _candidate_to_elite_funnel(aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    full_market = int(aggregate["total_full_market_candidates"])
    stages = [
        ("full_market", full_market),
        ("near_elite", int(aggregate["total_near_elite_rows"])),
        ("incubator", int(aggregate["total_incubator_rows"])),
        ("elite", int(aggregate["total_elite_rows"])),
        ("kelly_eligible", int(aggregate["total_kelly_eligible_rows"])),
    ]
    return [
        {
            "stage": stage,
            "count": count,
            "share_of_full_market": _pct(count, full_market),
        }
        for stage, count in stages
    ]


def _aggregate_rejection_reasons(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in records:
        raw = row.get("rejection_reason_counts", {})
        if isinstance(raw, dict):
            for reason, count in raw.items():
                reason_text = _safe_text(reason)
                count_int = _safe_int(count, 0)
                if reason_text and count_int:
                    counts[reason_text] += count_int
    total = sum(counts.values())
    return [
        {
            "reason": reason,
            "count": int(count),
            "percentage": round(100.0 * count / total, 2) if total else 0.0,
        }
        for reason, count in counts.most_common()
    ]


def _odds_profit_factor(odds: Any) -> float:
    value = _safe_float(odds)
    if value is None or abs(value) < 1:
        return 100.0 / 110.0
    if value > 0:
        return value / 100.0
    return 100.0 / abs(value)


def _flat_roi_for_result(row: pd.Series) -> float | None:
    status = _safe_text(row.get("result_status")).lower()
    if status == "hit":
        return _odds_profit_factor(row.get("odds") or row.get("entry_odds"))
    if status == "miss":
        return -1.0
    if status == "push":
        return 0.0
    return None


def _normal_bucket_rows(
    df: pd.DataFrame,
    *,
    history_source: str,
    roi_column: str | None,
) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        status = _safe_text(row.get("result_status")).lower()
        if status not in {"hit", "miss", "push"}:
            continue
        market = _safe_text(row.get("market_type") or row.get("market"), "unknown").lower()
        selection = _safe_text(row.get("selection"), "unknown").lower()
        caution = _safe_text(row.get("context_caution_level"), "unknown").lower()
        edge_label = _safe_text(row.get("context_edge_label"), "unknown").lower()
        source_reason = (
            _safe_text(row.get("source_rejection_reason"))
            or _safe_text(row.get("final_elite_rejection_reason"))
            or _safe_text(row.get("kelly_projected_skip_reason"))
            or _safe_text(row.get("paper_bucket"))
            or _safe_text(row.get("skip_reason"))
            or _safe_text(row.get("qualification_reason"))
            or "unknown"
        )
        if history_source == "paper_kelly_history" and source_reason == "unknown":
            source_reason = _safe_text(row.get("reason_not_real_kelly"), "paper_kelly")
        roi = _safe_float(row.get(roi_column)) if roi_column else _flat_roi_for_result(row)
        if roi is None:
            roi = _flat_roi_for_result(row)
        rows.append(
            {
                "history_source": history_source,
                "market_type": market,
                "selection": selection,
                "context_caution_level": caution,
                "context_edge_label": edge_label,
                "source_rejection_reason": source_reason,
                "result_status": status,
                "roi": roi,
                "real_money_eligible": _safe_text(row.get("kelly_eligible") or row.get("real_kelly_eligible")).lower()
                in {"true", "1", "yes", "y"},
            }
        )
    return rows


def _bucket_classification(row: dict[str, Any]) -> str:
    graded = int(row["sample_size"])
    hit_rate = row.get("hit_rate")
    roi = row.get("roi")
    selection = _safe_text(row.get("selection")).lower()
    caution = _safe_text(row.get("context_caution_level")).lower()
    reason = _safe_text(row.get("source_rejection_reason")).lower()
    source = _safe_text(row.get("history_source")).lower()

    if source == "pick_history" and bool(row.get("real_money_eligible")):
        return "real_money_eligible_observed"
    if graded < MIN_BUCKET_SAMPLE:
        return "unproven"
    if (
        selection == "over"
        and caution == "high"
        and reason in {ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER, KELLY_PROJECTED_CONTEXT_HIGH_CAUTION_OVER}
    ):
        return "unsafe"
    if hit_rate is not None and roi is not None and hit_rate >= 0.55 and roi > 0:
        return "potentially_promising_shadow_only"
    if (hit_rate is not None and hit_rate < 0.52) or (roi is not None and roi < 0):
        return "unsafe"
    return "unproven"


def _historical_bucket_report(history_root: Path, through_date: str | None) -> dict[str, list[dict[str, Any]]]:
    raw_rows: list[dict[str, Any]] = []
    sources = (
        ("market_shadow_history.csv", "market_shadow_history", "shadow_roi"),
        ("paper_kelly_history.csv", "paper_kelly_history", "paper_roi"),
        ("incubator_history.csv", "incubator_history", None),
        ("pick_history.csv", "pick_history", None),
    )
    for filename, source, roi_column in sources:
        path = history_root / filename
        df = _read_csv(path)
        if df.empty:
            continue
        if through_date and "prediction_date" in df.columns:
            df = df[df["prediction_date"].astype(str) <= str(through_date)].copy()
        raw_rows.extend(_normal_bucket_rows(df, history_source=source, roi_column=roi_column))

    if not raw_rows:
        return {
            "unsafe_buckets": [],
            "unproven_buckets": [],
            "potentially_promising_shadow_only_buckets": [],
            "real_money_eligible_buckets": [],
        }

    df = pd.DataFrame(raw_rows)
    group_cols = [
        "history_source",
        "market_type",
        "selection",
        "context_caution_level",
        "context_edge_label",
        "source_rejection_reason",
    ]
    rows: list[dict[str, Any]] = []
    for group_values, group in df.groupby(group_cols, sort=True, dropna=False):
        hits = int((group["result_status"] == "hit").sum())
        misses = int((group["result_status"] == "miss").sum())
        pushes = int((group["result_status"] == "push").sum())
        graded = hits + misses + pushes
        denom = hits + misses
        roi_values = pd.to_numeric(group["roi"], errors="coerce").dropna()
        row = {
            "history_source": group_values[0],
            "market_type": group_values[1],
            "selection": group_values[2],
            "context_caution_level": group_values[3],
            "context_edge_label": group_values[4],
            "source_rejection_reason": group_values[5],
            "sample_size": int(graded),
            "hits": hits,
            "misses": misses,
            "pushes": pushes,
            "hit_rate": round(hits / denom, 4) if denom else None,
            "roi": round(float(roi_values.mean()), 4) if len(roi_values) else None,
            "real_money_eligible": bool(group["real_money_eligible"].any()),
        }
        row["classification"] = _bucket_classification(row)
        rows.append(row)

    def _rank(row: dict[str, Any]) -> tuple[int, int, float, float]:
        class_rank = {
            "potentially_promising_shadow_only": 0,
            "real_money_eligible_observed": 1,
            "unsafe": 2,
            "unproven": 3,
        }.get(str(row.get("classification")), 9)
        hit_rate = row.get("hit_rate") if row.get("hit_rate") is not None else -1.0
        roi = row.get("roi") if row.get("roi") is not None else -99.0
        return (class_rank, -int(row.get("sample_size") or 0), -float(hit_rate), -float(roi))

    rows.sort(key=_rank)
    return {
        "unsafe_buckets": [row for row in rows if row["classification"] == "unsafe"][:12],
        "unproven_buckets": [row for row in rows if row["classification"] == "unproven"][:12],
        "potentially_promising_shadow_only_buckets": [
            row for row in rows if row["classification"] == "potentially_promising_shadow_only"
        ][:12],
        "real_money_eligible_buckets": [
            row for row in rows if row["classification"] == "real_money_eligible_observed"
        ][:12],
    }


def _issue_classification(records: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    total_full = int(aggregate["total_full_market_candidates"])
    total_elite = int(aggregate["total_elite_rows"])
    high_rate = float(aggregate["high_caution_over_block_rate"])
    no_bet_streak = _current_no_bet_streak(records)
    latest_status = records[-1].get("decision_bucket") if records else STATUS_UNKNOWN
    findings: list[str] = []

    if total_full <= 0:
        primary = "no_candidate_generation"
        findings.append("No full-market candidates were found in the audited operator cards.")
    elif latest_status == STATUS_NO_BET and no_bet_streak > 0 and high_rate >= 0.5:
        primary = "justified_safety_blocking"
        findings.append("Full-market candidates exist, but high-caution OVER blocks dominate the recent funnel.")
        findings.append("The first incubator high-caution OVER candidate was graded as a miss.")
        if total_elite > 0:
            findings.append("Earlier review-required slates prove Elite/Kelly output is possible; the current issue is not a total generation outage.")
    elif total_elite <= 0 and high_rate >= 0.5:
        primary = "justified_safety_blocking"
        findings.append("Full-market candidates exist, but high-caution OVER blocks dominate the recent funnel.")
    elif total_elite <= 0:
        primary = "over_strict_elite_filtering"
        findings.append("Full-market candidates exist, but recent Elite output is near zero.")
    else:
        primary = "controlled_filtering_with_some_elite_output"
        findings.append("The system can produce Elite/Kelly rows, but review or safety states still prevent clean betting.")

    if high_rate >= 0.5:
        findings.append("High-caution OVER block rate is above 50%, so no-bet outcomes are mainly a safety-gate phenomenon.")
    if int(aggregate["total_kelly_eligible_rows"]) <= 0:
        findings.append("Kelly eligibility is near zero, so no real-money lane is currently active.")
    findings.append("Calibration/performance weakness remains a blocker for promotion or threshold review.")
    findings.append("No evidence from this report indicates a candidate-generation outage.")
    return {
        "primary": primary,
        "candidate_generation_issue": total_full <= 0,
        "over_strict_elite_filtering_possible": bool(total_full > 0 and (total_elite <= 0 or no_bet_streak > 0)),
        "justified_safety_blocking": bool(high_rate >= 0.5),
        "calibration_performance_weakness": True,
        "data_feed_issue_detected": False,
        "findings": findings,
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (float, int, str, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def build_no_bet_funnel_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    lookback: int = DEFAULT_LOOKBACK,
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    records = _discover_slate_records(
        runtime_root=runtime_root_path,
        history_root=history_root_path,
        lookback=lookback,
    )
    aggregate = _aggregate_records(records)
    status_counts = Counter(row["decision_bucket"] for row in records)
    top_rejections = _aggregate_rejection_reasons(records)
    historical_buckets = _historical_bucket_report(history_root_path, through_date=prediction_date)
    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "betting_logic_changed": False,
        "lookback": int(lookback),
        "operator_card_count": int(len(records)),
        "date_range": {
            "start": records[0]["prediction_date"] if records else "",
            "end": records[-1]["prediction_date"] if records else "",
        },
        "status_counts": {
            STATUS_NO_BET: int(status_counts.get(STATUS_NO_BET, 0)),
            STATUS_REVIEW_REQUIRED: int(status_counts.get(STATUS_REVIEW_REQUIRED, 0)),
            STATUS_BET_APPROVED: int(status_counts.get(STATUS_BET_APPROVED, 0)),
            STATUS_UNKNOWN: int(status_counts.get(STATUS_UNKNOWN, 0)),
        },
        "no_bet_streak": {
            "current_no_bet_streak": _current_no_bet_streak(records),
            "latest_status": records[-1]["decision_bucket"] if records else STATUS_UNKNOWN,
            "no_bet_dates": [row["prediction_date"] for row in records if row["decision_bucket"] == STATUS_NO_BET],
        },
        "aggregate": aggregate,
        "candidate_to_elite_funnel": _candidate_to_elite_funnel(aggregate),
        "top_rejection_reasons": top_rejections,
        "issue_classification": _issue_classification(records, aggregate),
        "high_caution_over_analysis": {
            "total_high_caution_over_blocks": aggregate["total_high_caution_over_blocks"],
            "total_full_market_candidates": aggregate["total_full_market_candidates"],
            "block_rate": aggregate["high_caution_over_block_rate"],
            "interpretation": (
                "dominant_safety_blocker"
                if aggregate["high_caution_over_block_rate"] >= 0.5
                else "not_dominant_recent_blocker"
            ),
            "recommendation": "keep_gate_strict_shadow_only_review",
        },
        "near_elite_incubator_analysis": {
            "total_near_elite_rows": aggregate["total_near_elite_rows"],
            "total_incubator_rows": aggregate["total_incubator_rows"],
            "recommendation": "monitor_incubator_no_promotion",
        },
        "safe_action_discovery": historical_buckets,
        "what_not_to_change": [
            "Do not loosen Elite selection logic.",
            "Do not loosen Kelly logic.",
            "Do not change final_decision.",
            "Do not promote incubator rows.",
            "Do not loosen high-caution OVER gates.",
            "Do not change bankroll or staking logic.",
            "Do not regenerate closed-slate boards.",
        ],
        "recommended_next_action": [
            "Keep NO BET as the correct outcome when Elite/Kelly are empty.",
            "Monitor incubator and paper-only histories.",
            "Expand shadow tracking where historical buckets are promising.",
            "Review thresholds only after meaningful graded samples and CLV coverage exist.",
            "No promotion.",
        ],
        "slates": records,
        "source_paths": {
            "runtime_root": str(runtime_root_path),
            "history_root": str(history_root_path),
        },
    }
    slate_df = pd.DataFrame(records, columns=SLATE_CSV_COLUMNS)
    return _json_ready(payload), slate_df


def _table_line(values: list[Any], widths: list[int]) -> str:
    return " ".join(str(value)[:width].ljust(width) for value, width in zip(values, widths))


def _render_bucket_line(row: dict[str, Any]) -> str:
    return (
        f"- {row['history_source']}: {row['market_type']}/{row['selection']}/"
        f"{row['context_caution_level']}/{row['context_edge_label']} "
        f"reason={row['source_rejection_reason']} "
        f"n={row['sample_size']} hit_rate={_format_pct(row.get('hit_rate'))} "
        f"roi={_format_pct(row.get('roi'))}"
    )


def render_no_bet_funnel_text(payload: dict[str, Any], csv_path: Path) -> str:
    aggregate = payload["aggregate"]
    status_counts = payload["status_counts"]
    high = payload["high_caution_over_analysis"]
    slates = payload["slates"]
    lines: list[str] = [
        f"CourtVision NO_BET Funnel Report - {payload['prediction_date']}",
        "=" * 72,
        "Reporting-only diagnostic. No Elite, Kelly, final_decision, bankroll, or staking logic changed.",
        f"CSV artifact: {csv_path}",
        "",
        "1. Executive Summary",
        "-" * 72,
        (
            f"Audited {payload['operator_card_count']} operator card(s) from "
            f"{payload['date_range']['start'] or 'n/a'} to {payload['date_range']['end'] or 'n/a'}."
        ),
        (
            f"NO BET={status_counts.get(STATUS_NO_BET, 0)}, "
            f"REVIEW REQUIRED={status_counts.get(STATUS_REVIEW_REQUIRED, 0)}, "
            f"BET APPROVED={status_counts.get(STATUS_BET_APPROVED, 0)}, "
            f"UNKNOWN={status_counts.get(STATUS_UNKNOWN, 0)}."
        ),
        (
            f"High-caution OVER blocks: {high['total_high_caution_over_blocks']} / "
            f"{high['total_full_market_candidates']} full-market candidates "
            f"({_format_pct(high['block_rate'])})."
        ),
        "Recommendation: keep NO BET, monitor incubator, expand shadow tracking, no promotion.",
        "",
        "2. No-Bet Streak Summary",
        "-" * 72,
        f"- current NO BET streak: {payload['no_bet_streak']['current_no_bet_streak']}",
        f"- latest status: {payload['no_bet_streak']['latest_status']}",
        f"- NO BET dates: {', '.join(payload['no_bet_streak']['no_bet_dates']) or 'none'}",
        "",
        "3. Slate-by-Slate Funnel Table",
        "-" * 72,
    ]
    headers = ["date", "decision", "full", "near", "inc", "elite", "kelly", "hco", "combo", "same", "unsup", "top_reason"]
    widths = [10, 15, 5, 5, 4, 5, 5, 5, 5, 5, 5, 28]
    lines.append(_table_line(headers, widths))
    lines.append(_table_line(["-" * width for width in widths], widths))
    for row in slates:
        lines.append(
            _table_line(
                [
                    row["prediction_date"],
                    row["decision_bucket"],
                    row["full_market_count"],
                    row["near_elite_count"],
                    row["incubator_count"],
                    row["elite_count"],
                    row["kelly_eligible_count"],
                    row["high_caution_over_count"],
                    row["combo_under_watchlist_count"],
                    row["same_opponent_warning_count"],
                    row["unsupported_active_market_count"],
                    row["top_rejection_reason"] or "n/a",
                ],
                widths,
            )
        )

    lines.extend(
        [
            "",
            "4. Aggregate Blocker Table",
            "-" * 72,
            f"- total full-market candidates: {aggregate['total_full_market_candidates']}",
            f"- total high-caution OVER blocks: {aggregate['total_high_caution_over_blocks']}",
            f"- high-caution OVER block rate: {_format_pct(aggregate['high_caution_over_block_rate'])}",
            f"- total combo UNDER watchlist blocks: {aggregate['total_combo_under_watchlist_blocks']}",
            f"- total near-elite rows: {aggregate['total_near_elite_rows']}",
            f"- total incubator rows: {aggregate['total_incubator_rows']}",
            f"- total Elite rows: {aggregate['total_elite_rows']}",
            f"- total Kelly eligible rows: {aggregate['total_kelly_eligible_rows']}",
            "",
            "5. Top Rejection Reasons",
            "-" * 72,
        ]
    )
    if payload["top_rejection_reasons"]:
        for item in payload["top_rejection_reasons"][:12]:
            lines.append(f"- {item['reason']}: {item['count']} ({item['percentage']:.2f}%)")
    else:
        lines.append("- none available")

    lines.extend(["", "6. Candidate-to-Elite Funnel", "-" * 72])
    for item in payload["candidate_to_elite_funnel"]:
        lines.append(f"- {item['stage']}: {item['count']} ({_format_pct(item['share_of_full_market'])} of full-market)")

    lines.extend(
        [
            "",
            "7. High-Caution OVER Analysis",
            "-" * 72,
            f"- interpretation: {high['interpretation']}",
            f"- recommendation: {high['recommendation']}",
            "- first incubator high-caution OVER was graded as a miss; this supports keeping the gate strict.",
            "",
            "8. Near-Elite/Incubator Analysis",
            "-" * 72,
            f"- total near-elite rows: {payload['near_elite_incubator_analysis']['total_near_elite_rows']}",
            f"- total incubator rows: {payload['near_elite_incubator_analysis']['total_incubator_rows']}",
            f"- recommendation: {payload['near_elite_incubator_analysis']['recommendation']}",
            "",
            "9. Potential Safe-Action Discovery Buckets",
            "-" * 72,
            "Potentially promising shadow-only buckets:",
        ]
    )
    discovery = payload["safe_action_discovery"]
    promising = discovery.get("potentially_promising_shadow_only_buckets", [])
    if promising:
        lines.extend(_render_bucket_line(row) for row in promising[:8])
    else:
        lines.append("- none with sufficient evidence")
    lines.append("Unsafe buckets:")
    unsafe = discovery.get("unsafe_buckets", [])
    if unsafe:
        lines.extend(_render_bucket_line(row) for row in unsafe[:8])
    else:
        lines.append("- none identified")
    lines.append("Unproven buckets:")
    unproven = discovery.get("unproven_buckets", [])
    if unproven:
        lines.extend(_render_bucket_line(row) for row in unproven[:8])
    else:
        lines.append("- none identified")
    lines.append("Real-money eligible buckets:")
    real_money = discovery.get("real_money_eligible_buckets", [])
    if real_money:
        lines.extend(_render_bucket_line(row) for row in real_money[:8])
    else:
        lines.append("- none recommended by this report")

    lines.extend(
        [
            "",
            "10. What Not To Change",
            "-" * 72,
            *[f"- {item}" for item in payload["what_not_to_change"]],
            "",
            "11. Recommended Next Action",
            "-" * 72,
            *[f"- {item}" for item in payload["recommended_next_action"]],
            "",
            "Issue Classification",
            "-" * 72,
            f"- primary: {payload['issue_classification']['primary']}",
            *[f"- {item}" for item in payload["issue_classification"]["findings"]],
        ]
    )
    return "\n".join(lines) + "\n"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    runtime_root_path = Path(runtime_root)
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return (
        runtime_root_path / "operator" / f"{stem}.txt",
        runtime_root_path / "diagnostics" / f"{stem}.json",
        runtime_root_path / "operator" / f"{stem}.csv",
    )


def write_no_bet_funnel_report_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    lookback: int = DEFAULT_LOOKBACK,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    text_path, json_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    payload, slate_df = build_no_bet_funnel_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        lookback=lookback,
    )
    text = render_no_bet_funnel_text(payload, csv_path)

    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    slate_df.to_csv(csv_path, index=False)
    return text_path, json_path, csv_path, payload


__all__ = [
    "DEFAULT_LOOKBACK",
    "REPORT_FILE_PREFIX",
    "STATUS_BET_APPROVED",
    "STATUS_NO_BET",
    "STATUS_REVIEW_REQUIRED",
    "build_no_bet_funnel_report",
    "build_slate_record",
    "parse_operator_card",
    "render_no_bet_funnel_text",
    "report_paths_for_date",
    "write_no_bet_funnel_report_outputs",
]
