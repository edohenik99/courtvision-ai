from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


FINAL_STATUSES = {"hit", "miss", "push"}
PENDING_STATUS = "pending"
TERMINAL_NON_GRADED_STATUSES = {"void", "unsupported"}
GAME_NOT_FINAL_REASON = "game_not_final"
PROVIDER_MISSING_FINALITY_REASON = "provider_missing_finality"
OPEN_GAME_STATUSES = {"scheduled", "scheduled_future_iso_status", "not_started", "pre_game", "in_progress", "live"}
TERMINAL_GAME_STATUSES = {"final", "final_status", "completed", "complete", "closed", "post_game"}

STATUS_COMPLETE = "COMPLETE"
STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE = "COMPLETE_WITH_SHADOW_OPEN_NOISE"
STATUS_PARTIAL = "PARTIAL"
STATUS_STALE_PENDING_RISK = "STALE_PENDING_RISK"
STATUS_INCONSISTENT_REPORTING = "INCONSISTENT_REPORTING"


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>"}:
        return default
    return text


def _safe_int(value: Any) -> int | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _read_csv(path: Path, warnings: list[str], *, source_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    record = {"path": str(path), "exists": path.exists(), "rows": 0}
    if not path.exists():
        warnings.append(f"Missing optional history artifact: {path}")
        return pd.DataFrame(), record
    if path.stat().st_size == 0:
        warnings.append(f"Empty history artifact: {path}")
        return pd.DataFrame(), record
    try:
        df = pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        warnings.append(f"Empty history artifact: {path}")
        return pd.DataFrame(), record
    except Exception as exc:
        warnings.append(f"Could not read {source_name}: {path} ({exc})")
        return pd.DataFrame(), record
    record["rows"] = int(len(df))
    return df, record


def _read_json(path: Path, warnings: list[str], *, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        warnings.append(f"Missing optional {label}: {path}")
        return {}, record
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read {label}: {path} ({exc})")
        return {}, record
    if not isinstance(payload, dict):
        warnings.append(f"{label} must contain a JSON object: {path}")
        return {}, record
    return payload, record


def _read_text(path: Path, warnings: list[str], *, label: str) -> tuple[str, dict[str, Any]]:
    record = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        warnings.append(f"Missing optional {label}: {path}")
        return "", record
    try:
        return path.read_text(encoding="utf-8"), record
    except Exception as exc:
        warnings.append(f"Could not read {label}: {path} ({exc})")
        return "", record


def _normalize_result_status(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in {"hit", "win", "won", "true", "1"}:
        return "hit"
    if text in {"miss", "loss", "lost", "false", "0"}:
        return "miss"
    if text == "push":
        return "push"
    if text in TERMINAL_NON_GRADED_STATUSES:
        return text
    return PENDING_STATUS


def _row_status(row: pd.Series) -> str:
    for column in ("result_status", "graded_result", "result"):
        if column not in row.index:
            continue
        raw_text = _safe_text(row.get(column))
        if not raw_text:
            continue
        status = _normalize_result_status(raw_text)
        if status != PENDING_STATUS or raw_text.lower() == PENDING_STATUS:
            return status
    if "hit" in row.index and _truthy(row.get("hit")):
        return "hit"
    if "miss" in row.index and _truthy(row.get("miss")):
        return "miss"
    if "push" in row.index and _truthy(row.get("push")):
        return "push"
    return PENDING_STATUS


def _scoped_to_date(df: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    if "prediction_date" not in df.columns:
        return pd.DataFrame(columns=list(df.columns))
    return df[df["prediction_date"].astype(str).eq(str(prediction_date))].copy()


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _pending_breakdown(
    df: pd.DataFrame,
    *,
    prediction_date: str,
    today: str | None = None,
) -> dict[str, Any]:
    scoped = _scoped_to_date(df, prediction_date)
    if scoped.empty:
        return {
            "rows": 0,
            "pending_count": 0,
            "open_game_pending_count": 0,
            "stale_pending_count": 0,
            "status_counts": {},
            "direct_open_game_pending_count": 0,
            "direct_stale_pending_count": 0,
            "taxonomy_source": "history",
        }

    statuses = scoped.apply(_row_status, axis=1)
    pending = statuses.eq(PENDING_STATUS)
    reasons = (
        scoped["grading_skip_reason"].map(lambda value: _safe_text(value).lower())
        if "grading_skip_reason" in scoped.columns
        else pd.Series("", index=scoped.index)
    )
    game_statuses = pd.Series("", index=scoped.index)
    for column in ("game_status", "game_status_bucket"):
        if column in scoped.columns:
            game_statuses = game_statuses.mask(
                game_statuses.eq(""),
                scoped[column].map(lambda value: _safe_text(value).lower()),
            )
    dates = (
        scoped["prediction_date"].map(_safe_text)
        if "prediction_date" in scoped.columns
        else pd.Series("", index=scoped.index)
    )
    today_text = today or _today_iso()
    provider_missing_finality_open = reasons.eq(PROVIDER_MISSING_FINALITY_REASON) & (
        game_statuses.isin(OPEN_GAME_STATUSES)
        | (dates.ge(today_text) & ~game_statuses.isin(TERMINAL_GAME_STATUSES))
    )
    open_game = pending & (
        reasons.eq(GAME_NOT_FINAL_REASON)
        | provider_missing_finality_open
        | game_statuses.isin(OPEN_GAME_STATUSES)
        | (dates.ge(today_text) & ~game_statuses.isin(TERMINAL_GAME_STATUSES))
    )
    stale = pending & ~open_game
    status_counts = {str(key): int(value) for key, value in statuses.value_counts().sort_index().items()}
    return {
        "rows": int(len(scoped)),
        "pending_count": int(pending.sum()),
        "open_game_pending_count": int(open_game.sum()),
        "stale_pending_count": int(stale.sum()),
        "status_counts": status_counts,
        "direct_open_game_pending_count": int(open_game.sum()),
        "direct_stale_pending_count": int(stale.sum()),
        "taxonomy_source": "history",
    }


def history_pending_grading_count(path: str | Path, prediction_date: str) -> int | None:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        history = pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return None
    scoped = _scoped_to_date(history, prediction_date)
    if scoped.empty:
        return None
    statuses = scoped.apply(_row_status, axis=1)
    return int(statuses.eq(PENDING_STATUS).sum())


def _real_pick_counts(df: pd.DataFrame, *, prediction_date: str) -> dict[str, Any]:
    scoped = _scoped_to_date(df, prediction_date)
    if scoped.empty:
        return {
            "real_pick_rows": 0,
            "real_pick_pending_count": 0,
            "real_pick_graded_count": 0,
            "real_pick_hit_count": 0,
            "real_pick_miss_count": 0,
            "real_pick_push_count": 0,
            "status_counts": {},
        }
    statuses = scoped.apply(_row_status, axis=1)
    hit_count = int(statuses.eq("hit").sum())
    miss_count = int(statuses.eq("miss").sum())
    push_count = int(statuses.eq("push").sum())
    pending_count = int(statuses.eq(PENDING_STATUS).sum())
    return {
        "real_pick_rows": int(len(scoped)),
        "real_pick_pending_count": pending_count,
        "real_pick_graded_count": int(hit_count + miss_count + push_count),
        "real_pick_hit_count": hit_count,
        "real_pick_miss_count": miss_count,
        "real_pick_push_count": push_count,
        "status_counts": {str(key): int(value) for key, value in statuses.value_counts().sort_index().items()},
    }


def _repair_history_summary(payload: dict[str, Any], history_name: str) -> dict[str, int] | None:
    histories = payload.get("histories", {}) if isinstance(payload, dict) else {}
    summary = histories.get(history_name, {}) if isinstance(histories, dict) else {}
    if not isinstance(summary, dict):
        return None
    total_pending = _safe_int(summary.get("total_pending"))
    open_game_pending = _safe_int(summary.get("open_game_pending"))
    stale_pending = _safe_int(summary.get("stale_pending"))
    if total_pending is None or open_game_pending is None or stale_pending is None:
        return None
    return {
        "total_pending": total_pending,
        "open_game_pending": open_game_pending,
        "stale_pending": stale_pending,
    }


def _apply_repair_taxonomy(
    direct_counts: dict[str, Any],
    repair_summary: dict[str, int] | None,
) -> dict[str, Any]:
    if not repair_summary:
        return direct_counts
    pending_count = int(direct_counts.get("pending_count", 0) or 0)
    repair_pending = int(repair_summary.get("total_pending", 0) or 0)
    repair_open = int(repair_summary.get("open_game_pending", 0) or 0)
    repair_stale = int(repair_summary.get("stale_pending", 0) or 0)
    if repair_pending == pending_count and repair_open + repair_stale == pending_count:
        updated = dict(direct_counts)
        updated["open_game_pending_count"] = repair_open
        updated["stale_pending_count"] = repair_stale
        updated["taxonomy_source"] = "pending_repair_audit"
        return updated
    return direct_counts


def _daily_pending_count(text: str) -> int | None:
    patterns = (
        r"Pending grading count:\s*(\d+)",
        r"pending_grading=(\d+)",
        r"- pending picks:\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _quality_counts(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {
            "quality_summary_elite_count": None,
            "quality_summary_kelly_rows": None,
            "quality_summary_kelly_pending_count": None,
        }
    funnel = payload.get("candidate_funnel", {}) if isinstance(payload.get("candidate_funnel"), dict) else {}
    kelly_safety = (
        payload.get("kelly_safety_summary", {})
        if isinstance(payload.get("kelly_safety_summary"), dict)
        else {}
    )
    kelly_perf = (
        payload.get("kelly_decision_performance", {})
        if isinstance(payload.get("kelly_decision_performance"), dict)
        else {}
    )
    overall = kelly_perf.get("overall", {}) if isinstance(kelly_perf.get("overall"), dict) else {}
    kelly_rows = _safe_int(funnel.get("kelly_rows_count"))
    if kelly_rows is None:
        kelly_rows = _safe_int(kelly_safety.get("total_rows"))
    return {
        "quality_summary_elite_count": _safe_int(funnel.get("elite_board_count")),
        "quality_summary_kelly_rows": kelly_rows,
        "quality_summary_kelly_pending_count": _safe_int(overall.get("pending_count")),
    }


def _daily_pending_is_resolved_history_snapshot(
    *,
    daily_summary_pending_grading: int | None,
    real_pick_pending_count: int,
    shadow_rows: int,
    shadow_pending_count: int,
    shadow_stale_pending_count: int,
    paper_rows: int,
    paper_pending_count: int,
    paper_stale_pending_count: int,
) -> bool:
    if daily_summary_pending_grading is None or daily_summary_pending_grading <= 0:
        return False
    if real_pick_pending_count != 0:
        return False
    if shadow_pending_count != 0 or shadow_stale_pending_count != 0:
        return False
    if paper_pending_count != 0 or paper_stale_pending_count != 0:
        return False

    resolved_history_counts = {
        count
        for count in (
            shadow_rows,
            paper_rows,
            shadow_rows + paper_rows,
        )
        if count > 0
    }
    return daily_summary_pending_grading in resolved_history_counts


def _agreement_issues(
    *,
    real_pick_rows: int,
    real_pick_pending_count: int,
    pick_history_exists: bool,
    daily_summary_pending_grading: int | None,
    quality_summary_kelly_rows: int | None,
    quality_summary_kelly_pending_count: int | None,
    shadow_rows: int,
    shadow_pending_count: int,
    shadow_open_game_pending_count: int,
    shadow_stale_pending_count: int,
    paper_rows: int,
    paper_pending_count: int,
    paper_stale_pending_count: int,
) -> list[str]:
    issues: list[str] = []

    daily_pending_is_real_pick_count = daily_summary_pending_grading == real_pick_pending_count
    daily_pending_is_open_shadow_noise = (
        daily_summary_pending_grading is not None
        and real_pick_pending_count == 0
        and daily_summary_pending_grading == shadow_pending_count
        and shadow_pending_count > 0
        and shadow_pending_count == shadow_open_game_pending_count
        and shadow_stale_pending_count == 0
    )
    daily_pending_is_resolved_history_snapshot = _daily_pending_is_resolved_history_snapshot(
        daily_summary_pending_grading=daily_summary_pending_grading,
        real_pick_pending_count=real_pick_pending_count,
        shadow_rows=shadow_rows,
        shadow_pending_count=shadow_pending_count,
        shadow_stale_pending_count=shadow_stale_pending_count,
        paper_rows=paper_rows,
        paper_pending_count=paper_pending_count,
        paper_stale_pending_count=paper_stale_pending_count,
    )

    if daily_summary_pending_grading is not None and not (
        daily_pending_is_real_pick_count or daily_pending_is_open_shadow_noise
        or daily_pending_is_resolved_history_snapshot
    ):
        issues.append(
            "daily_summary_pending_grading_mismatch:"
            f"daily={daily_summary_pending_grading},real_pick_pending={real_pick_pending_count},"
            f"shadow_pending={shadow_pending_count},"
            f"shadow_open_game_pending={shadow_open_game_pending_count},"
            f"shadow_stale_pending={shadow_stale_pending_count}"
        )
    if pick_history_exists and quality_summary_kelly_rows is not None and quality_summary_kelly_rows != real_pick_rows:
        issues.append(
            "quality_summary_kelly_rows_mismatch:"
            f"quality={quality_summary_kelly_rows},real_pick_rows={real_pick_rows}"
        )
    if (
        quality_summary_kelly_pending_count is not None
        and quality_summary_kelly_pending_count != real_pick_pending_count
    ):
        issues.append(
            "quality_summary_kelly_pending_mismatch:"
            f"quality={quality_summary_kelly_pending_count},real_pick_pending={real_pick_pending_count}"
        )
    return issues


def _classify(
    *,
    real_pick_pending_count: int,
    shadow_open_game_pending_count: int,
    shadow_stale_pending_count: int,
    paper_open_game_pending_count: int,
    paper_stale_pending_count: int,
    agreement_issues: list[str],
) -> str:
    stale_total = shadow_stale_pending_count + paper_stale_pending_count
    open_noise_total = shadow_open_game_pending_count + paper_open_game_pending_count
    if stale_total > 0:
        return STATUS_STALE_PENDING_RISK
    if real_pick_pending_count > 0:
        return STATUS_PARTIAL
    if agreement_issues:
        return STATUS_INCONSISTENT_REPORTING
    if open_noise_total > 0:
        return STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE
    return STATUS_COMPLETE


def _interpretation(status: str) -> str:
    if status == STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE:
        return (
            "Real picks are fully graded. Remaining shadow or paper rows are classified as open-game "
            "observation noise, not unresolved real wagers."
        )
    if status == STATUS_STALE_PENDING_RISK:
        return "One or more shadow or paper pending rows are not classified as open-game pending."
    if status == STATUS_PARTIAL:
        return "At least one real pick is still pending."
    if status == STATUS_INCONSISTENT_REPORTING:
        return "The daily or quality summary disagrees with the history counts read by this audit."
    return "Real picks are fully graded and no stale shadow or paper pending rows were found."


def completion_state_audit_json_path(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"completion_state_audit_{prediction_date}.json"


def completion_state_audit_txt_path(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"completion_state_audit_{prediction_date}.txt"


def build_completion_state_audit(
    *,
    prediction_date: str,
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    diagnostics_output_path: str | Path | None = None,
    operator_output_path: str | Path | None = None,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    json_path = (
        Path(diagnostics_output_path)
        if diagnostics_output_path is not None
        else completion_state_audit_json_path(prediction_date, runtime_root_path)
    )
    text_path = (
        Path(operator_output_path)
        if operator_output_path is not None
        else completion_state_audit_txt_path(prediction_date, runtime_root_path)
    )

    warnings: list[str] = []
    pick_history, pick_record = _read_csv(
        history_root_path / "pick_history.csv",
        warnings,
        source_name="pick_history",
    )
    shadow_history, shadow_record = _read_csv(
        history_root_path / "market_shadow_history.csv",
        warnings,
        source_name="market_shadow_history",
    )
    paper_history, paper_record = _read_csv(
        history_root_path / "paper_kelly_history.csv",
        warnings,
        source_name="paper_kelly_history",
    )
    daily_text, daily_record = _read_text(
        runtime_root_path / "operator" / f"daily_summary_{prediction_date}.txt",
        warnings,
        label="daily summary",
    )
    quality_payload, quality_record = _read_json(
        runtime_root_path / "operator" / f"quality_summary_{prediction_date}.json",
        warnings,
        label="quality summary",
    )
    repair_warnings: list[str] = []
    repair_payload, repair_record = _read_json(
        runtime_root_path / "diagnostics" / f"pending_repair_audit_{prediction_date}.json",
        repair_warnings,
        label="pending repair audit",
    )

    real_counts = _real_pick_counts(pick_history, prediction_date=prediction_date)
    shadow_counts_direct = _pending_breakdown(shadow_history, prediction_date=prediction_date)
    paper_counts_direct = _pending_breakdown(paper_history, prediction_date=prediction_date)
    pending_rows_need_repair_context = (
        int(real_counts["real_pick_pending_count"])
        + int(shadow_counts_direct["pending_count"])
        + int(paper_counts_direct["pending_count"])
    ) > 0
    if pending_rows_need_repair_context:
        warnings.extend(repair_warnings)
    shadow_counts = _apply_repair_taxonomy(
        shadow_counts_direct,
        _repair_history_summary(repair_payload, "market_shadow_history"),
    )
    paper_counts = _apply_repair_taxonomy(
        paper_counts_direct,
        _repair_history_summary(repair_payload, "paper_kelly_history"),
    )

    daily_summary_pending_grading = _daily_pending_count(daily_text) if daily_text else None
    quality = _quality_counts(quality_payload)
    daily_summary_pending_resolution = (
        "resolved_history_snapshot"
        if _daily_pending_is_resolved_history_snapshot(
            daily_summary_pending_grading=daily_summary_pending_grading,
            real_pick_pending_count=real_counts["real_pick_pending_count"],
            shadow_rows=int(shadow_counts["rows"]),
            shadow_pending_count=int(shadow_counts["pending_count"]),
            shadow_stale_pending_count=int(shadow_counts["stale_pending_count"]),
            paper_rows=int(paper_counts["rows"]),
            paper_pending_count=int(paper_counts["pending_count"]),
            paper_stale_pending_count=int(paper_counts["stale_pending_count"]),
        )
        else "reported"
    )
    agreement_issues = _agreement_issues(
        real_pick_rows=real_counts["real_pick_rows"],
        real_pick_pending_count=real_counts["real_pick_pending_count"],
        pick_history_exists=bool(pick_record["exists"]),
        daily_summary_pending_grading=daily_summary_pending_grading,
        quality_summary_kelly_rows=quality["quality_summary_kelly_rows"],
        quality_summary_kelly_pending_count=quality["quality_summary_kelly_pending_count"],
        shadow_rows=int(shadow_counts["rows"]),
        shadow_pending_count=int(shadow_counts["pending_count"]),
        shadow_open_game_pending_count=int(shadow_counts["open_game_pending_count"]),
        shadow_stale_pending_count=int(shadow_counts["stale_pending_count"]),
        paper_rows=int(paper_counts["rows"]),
        paper_pending_count=int(paper_counts["pending_count"]),
        paper_stale_pending_count=int(paper_counts["stale_pending_count"]),
    )

    status = _classify(
        real_pick_pending_count=real_counts["real_pick_pending_count"],
        shadow_open_game_pending_count=int(shadow_counts["open_game_pending_count"]),
        shadow_stale_pending_count=int(shadow_counts["stale_pending_count"]),
        paper_open_game_pending_count=int(paper_counts["open_game_pending_count"]),
        paper_stale_pending_count=int(paper_counts["stale_pending_count"]),
        agreement_issues=agreement_issues,
    )

    payload: dict[str, Any] = {
        "prediction_date": prediction_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "note": "audit_only_no_prediction_grading_kelly_selection_suppression_or_history_change",
        "real_pick_rows": real_counts["real_pick_rows"],
        "real_pick_pending_count": real_counts["real_pick_pending_count"],
        "real_pick_graded_count": real_counts["real_pick_graded_count"],
        "real_pick_hit_count": real_counts["real_pick_hit_count"],
        "real_pick_miss_count": real_counts["real_pick_miss_count"],
        "real_pick_push_count": real_counts["real_pick_push_count"],
        "shadow_pending_count": int(shadow_counts["pending_count"]),
        "shadow_open_game_pending_count": int(shadow_counts["open_game_pending_count"]),
        "shadow_stale_pending_count": int(shadow_counts["stale_pending_count"]),
        "paper_pending_count": int(paper_counts["pending_count"]),
        "paper_open_game_pending_count": int(paper_counts["open_game_pending_count"]),
        "paper_stale_pending_count": int(paper_counts["stale_pending_count"]),
        "daily_summary_pending_grading": daily_summary_pending_grading,
        "quality_summary_elite_count": quality["quality_summary_elite_count"],
        "quality_summary_kelly_rows": quality["quality_summary_kelly_rows"],
        "report_agreement_status": status,
        "interpretation": _interpretation(status),
        "agreement_issues": agreement_issues,
        "details": {
            "real_pick_status_counts": real_counts["status_counts"],
            "shadow_status_counts": shadow_counts.get("status_counts", {}),
            "paper_status_counts": paper_counts.get("status_counts", {}),
            "shadow_pending_taxonomy_source": shadow_counts.get("taxonomy_source"),
            "paper_pending_taxonomy_source": paper_counts.get("taxonomy_source"),
            "shadow_direct_open_game_pending_count": int(shadow_counts.get("direct_open_game_pending_count", 0) or 0),
            "shadow_direct_stale_pending_count": int(shadow_counts.get("direct_stale_pending_count", 0) or 0),
            "paper_direct_open_game_pending_count": int(paper_counts.get("direct_open_game_pending_count", 0) or 0),
            "paper_direct_stale_pending_count": int(paper_counts.get("direct_stale_pending_count", 0) or 0),
            "quality_summary_kelly_pending_count": quality["quality_summary_kelly_pending_count"],
            "daily_summary_pending_grading_resolution": daily_summary_pending_resolution,
        },
        "repair_audit": {
            "path": repair_record["path"],
            "available": bool(repair_record["exists"]),
            "mode": repair_payload.get("mode"),
            "start_date": repair_payload.get("start_date"),
            "end_date": repair_payload.get("end_date"),
            "through_date": repair_payload.get("through_date"),
            "include_current_date": repair_payload.get("include_current_date"),
            "summary": repair_payload.get("summary", {}) if isinstance(repair_payload, dict) else {},
            "histories": {
                "pick_history": _repair_history_summary(repair_payload, "pick_history"),
                "market_shadow_history": _repair_history_summary(repair_payload, "market_shadow_history"),
                "paper_kelly_history": _repair_history_summary(repair_payload, "paper_kelly_history"),
            },
        },
        "source_paths": {
            "pick_history": pick_record,
            "market_shadow_history": shadow_record,
            "paper_kelly_history": paper_record,
            "daily_summary": daily_record,
            "quality_summary": quality_record,
            "pending_repair_audit": repair_record,
        },
        "artifact_paths": {
            "json_path": str(json_path),
            "txt_path": str(text_path),
        },
        "warnings": warnings,
    }
    return payload


def render_completion_state_audit_text(payload: dict[str, Any]) -> str:
    status = _safe_text(payload.get("report_agreement_status"), "UNKNOWN")
    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    repair = payload.get("repair_audit", {}) if isinstance(payload.get("repair_audit"), dict) else {}
    lines = [
        f"Completion State Audit - {payload.get('prediction_date')}",
        "=" * 72,
        "Read-only audit; no prediction, grading, Kelly, selection, suppression, or history mutation.",
        "",
        "Status",
        "-" * 72,
        f"- report_agreement_status: {status}",
        f"- interpretation: {payload.get('interpretation', '')}",
        "",
        "Real Picks",
        "-" * 72,
        f"- real_pick_rows: {payload.get('real_pick_rows', 0)}",
        f"- real_pick_pending_count: {payload.get('real_pick_pending_count', 0)}",
        f"- real_pick_graded_count: {payload.get('real_pick_graded_count', 0)}",
        f"- real_pick_hit_count: {payload.get('real_pick_hit_count', 0)}",
        f"- real_pick_miss_count: {payload.get('real_pick_miss_count', 0)}",
        f"- real_pick_push_count: {payload.get('real_pick_push_count', 0)}",
        "",
        "Shadow and Paper Pending State",
        "-" * 72,
        (
            f"- market_shadow_history: pending={payload.get('shadow_pending_count', 0)}, "
            f"open_game_pending={payload.get('shadow_open_game_pending_count', 0)}, "
            f"stale_pending={payload.get('shadow_stale_pending_count', 0)}, "
            f"taxonomy_source={details.get('shadow_pending_taxonomy_source', 'history')}"
        ),
        (
            f"- paper_kelly_history: pending={payload.get('paper_pending_count', 0)}, "
            f"open_game_pending={payload.get('paper_open_game_pending_count', 0)}, "
            f"stale_pending={payload.get('paper_stale_pending_count', 0)}, "
            f"taxonomy_source={details.get('paper_pending_taxonomy_source', 'history')}"
        ),
        "",
        "Report Cross-Checks",
        "-" * 72,
        f"- daily_summary_pending_grading: {_safe_text(payload.get('daily_summary_pending_grading'), 'missing')}",
        f"- quality_summary_elite_count: {_safe_text(payload.get('quality_summary_elite_count'), 'missing')}",
        f"- quality_summary_kelly_rows: {_safe_text(payload.get('quality_summary_kelly_rows'), 'missing')}",
        f"- quality_summary_kelly_pending_count: {_safe_text(details.get('quality_summary_kelly_pending_count'), 'missing')}",
        "",
        "Repair Audit Context",
        "-" * 72,
        f"- available: {str(bool(repair.get('available'))).lower()}",
        f"- path: {_safe_text(repair.get('path'), 'missing')}",
        f"- mode: {_safe_text(repair.get('mode'), 'missing')}",
        f"- range: {_safe_text(repair.get('start_date'), 'missing')}..{_safe_text(repair.get('end_date'), 'missing')}",
        f"- include_current_date: {_safe_text(repair.get('include_current_date'), 'missing')}",
        "",
        "Agreement Issues",
        "-" * 72,
    ]
    agreement_issues = payload.get("agreement_issues", [])
    if agreement_issues:
        for issue in agreement_issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- none")
    warnings = payload.get("warnings", [])
    lines.extend(["", "Warnings", "-" * 72])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def write_completion_state_audit(
    *,
    prediction_date: str,
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    diagnostics_output_path: str | Path | None = None,
    operator_output_path: str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    payload = build_completion_state_audit(
        prediction_date=prediction_date,
        history_root=history_root,
        runtime_root=runtime_root,
        diagnostics_output_path=diagnostics_output_path,
        operator_output_path=operator_output_path,
    )
    json_path = Path(payload["artifact_paths"]["json_path"])
    text_path = Path(payload["artifact_paths"]["txt_path"])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    text_path.write_text(render_completion_state_audit_text(payload), encoding="utf-8")
    return text_path, json_path, payload


__all__ = [
    "STATUS_COMPLETE",
    "STATUS_COMPLETE_WITH_SHADOW_OPEN_NOISE",
    "STATUS_INCONSISTENT_REPORTING",
    "STATUS_PARTIAL",
    "STATUS_STALE_PENDING_RISK",
    "build_completion_state_audit",
    "completion_state_audit_json_path",
    "completion_state_audit_txt_path",
    "history_pending_grading_count",
    "render_completion_state_audit_text",
    "write_completion_state_audit",
]
