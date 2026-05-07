from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.calibration.buckets import abs_edge_bucket


HISTORY_FILE_NAME = "paper_kelly_history.csv"
SOURCE_FILE_PREFIX = "paper_kelly_simulation"
REPORT_FILE_PREFIX = "paper_kelly_performance_report"
REPORT_TITLE = "Paper Kelly Performance \u2014 Simulation Only"

REQUIRED_HISTORY_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "paper_bucket",
    "player_name",
    "team_abbr",
    "opponent",
    "market_type",
    "selection",
    "line",
    "odds",
    "edge",
    "directional_edge",
    "confidence",
    "quality_score",
    "context_pick_alignment",
    "context_caution_level",
    "simulated_fraction",
    "simulated_stake",
    "pre_cap_simulated_stake",
    "cap_adjustment_reason",
    "player_exposure_after_cap",
    "team_exposure_after_cap",
    "game_exposure_after_cap",
    "side_exposure_after_cap",
    "bucket_exposure_after_cap",
    "simulated_ev",
    "real_kelly_eligible",
    "simulation_only",
    "reason_not_real_kelly",
    "result_status",
    "actual_value",
    "paper_profit",
    "paper_roi",
)
HISTORY_COLUMNS: tuple[str, ...] = REQUIRED_HISTORY_COLUMNS + ("grading_skip_reason",)

GROUP_COLUMNS: tuple[str, ...] = (
    "paper_bucket",
    "market_type",
    "selection",
    "context_pick_alignment",
    "context_caution_level",
    "directional_edge_bucket",
    "confidence_bucket",
    "cap_adjustment_reason",
)

REPORT_COLUMNS: tuple[str, ...] = GROUP_COLUMNS + (
    "total",
    "graded_total",
    "hits",
    "misses",
    "pushes",
    "pending",
    "open_pending",
    "stale_pending",
    "hit_rate",
    "pre_cap_exposure",
    "post_cap_exposure",
    "exposure_reduced",
    "paper_profit",
    "paper_roi",
    "sample_status",
)

FINAL_STATUSES = {"hit", "miss", "push"}
HIT_MISS_STATUSES = {"hit", "miss"}
TERMINAL_NON_GRADED_STATUSES = {"void", "unsupported"}
PENDING_STATUS = "pending"
GAME_NOT_FINAL_REASON = "game_not_final"
OPEN_GAME_STATUSES = {"scheduled", "scheduled_future_iso_status", "not_started", "pre_game", "in_progress", "live"}
TERMINAL_GAME_STATUSES = {"final", "final_status", "completed", "complete", "closed", "post_game"}


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


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read_csv(path: Path, columns: tuple[str, ...] | None = None) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=list(columns or ()))
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=list(columns or ()))


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


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


def _today_iso() -> str:
    return datetime.now().date().isoformat()


def _truthy(value: Any) -> bool:
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _line_key(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.4f}"


def _identity_fields(row: pd.Series) -> tuple[str, str, str, str, str, str]:
    prediction_date = _safe_text(row.get("prediction_date")).lower()
    player_name = (_safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"))).lower()
    team_abbr = (_safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))).upper()
    market_type = (_safe_text(row.get("market_type")) or _safe_text(row.get("market"))).lower()
    selection = (_safe_text(row.get("selection")) or _safe_text(row.get("side"))).lower()
    line = row.get("line")
    if _safe_text(line) == "":
        line = row.get("sportsbook_line") if "sportsbook_line" in row.index else row.get("line_value")
    return prediction_date, player_name, team_abbr, market_type, selection, _line_key(line)


def _lookup_keys(row: pd.Series) -> list[tuple[str, str, str, str, str, str]]:
    prediction_date, player_name, team_abbr, market_type, selection, line = _identity_fields(row)
    keys = [(prediction_date, player_name, team_abbr, market_type, selection, line)]
    if team_abbr:
        keys.append((prediction_date, player_name, "", market_type, selection, line))
    return keys


def _actual_value(row: pd.Series) -> Any:
    for column in ("actual_value", "stat_value", "actual"):
        if column in row.index and _safe_text(row.get(column)) != "":
            return row.get(column)
    return ""


def _source_result_payload(row: pd.Series, *, source_name: str) -> dict[str, Any]:
    status = PENDING_STATUS
    for column in ("result_status", "graded_result", "result"):
        if column in row.index:
            status = _normalize_result_status(row.get(column))
            if status != PENDING_STATUS:
                break
    if status == PENDING_STATUS:
        if "hit" in row.index and _truthy(row.get("hit")):
            status = "hit"
        elif "miss" in row.index and _truthy(row.get("miss")):
            status = "miss"
        elif "push" in row.index and _truthy(row.get("push")):
            status = "push"
    if status in FINAL_STATUSES:
        reason = ""
    elif status in TERMINAL_NON_GRADED_STATUSES:
        reason = _safe_text(row.get("grading_skip_reason")) or status
    else:
        reason = f"{source_name}_result_pending"
    return {
        "result_status": status,
        "actual_value": _actual_value(row),
        "grading_skip_reason": reason,
        "source": source_name,
    }


def _merge_lookup_payload(
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]],
    key: tuple[str, str, str, str, str, str],
    payload: dict[str, Any],
) -> None:
    existing = lookup.get(key)
    if existing is None:
        lookup[key] = payload
        return
    if existing.get("result_status") not in FINAL_STATUSES and payload.get("result_status") in FINAL_STATUSES:
        lookup[key] = payload


def _add_source_to_lookup(
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]],
    df: pd.DataFrame,
    *,
    prediction_date: str,
    source_name: str,
) -> None:
    if df.empty:
        return
    scoped = df.copy()
    if "prediction_date" in scoped.columns:
        scoped = scoped[scoped["prediction_date"].astype(str) == str(prediction_date)].copy()
    for _, row in scoped.iterrows():
        payload = _source_result_payload(row, source_name=source_name)
        for key in _lookup_keys(row):
            if key[0] and key[1] and key[3] and key[4] and key[5]:
                _merge_lookup_payload(lookup, key, payload)


def _result_lookup(
    *,
    prediction_date: str,
    runtime_root: Path,
    history_root: Path,
) -> dict[tuple[str, str, str, str, str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    sources = [
        (history_root / "market_shadow_history.csv", "market_shadow_history"),
        (runtime_root / "history" / "result_feedback.csv", "result_feedback"),
        (runtime_root / "research" / f"grading_results_{prediction_date}.csv", "grading_results"),
        (runtime_root / "history" / f"graded_picks_{prediction_date}.csv", "graded_picks"),
        (history_root / "pick_history.csv", "pick_history"),
    ]
    for path, source_name in sources:
        _add_source_to_lookup(
            lookup,
            _read_csv(path),
            prediction_date=prediction_date,
            source_name=source_name,
        )
    return lookup


def _lookup_result_for_row(
    row: pd.Series,
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    missing = []
    prediction_date, player_name, _team_abbr, market_type, selection, line = _identity_fields(row)
    if not prediction_date:
        missing.append("prediction_date")
    if not player_name:
        missing.append("player_name")
    if not market_type:
        missing.append("market_type")
    if not selection:
        missing.append("selection")
    if not line:
        missing.append("line")
    if missing:
        return {
            "result_status": PENDING_STATUS,
            "actual_value": "",
            "grading_skip_reason": "missing_identity_fields:" + ",".join(missing),
        }

    for key in _lookup_keys(row):
        payload = lookup.get(key)
        if payload is not None:
            status = _normalize_result_status(payload.get("result_status"))
            return {
                "result_status": status,
                "actual_value": payload.get("actual_value", ""),
            "grading_skip_reason": "" if status in FINAL_STATUSES else str(payload.get("grading_skip_reason") or "result_pending"),
            }
    return {
        "result_status": PENDING_STATUS,
        "actual_value": "",
        "grading_skip_reason": "no_matching_shadow_or_result_data",
    }


def _american_profit_for_unit_stake(odds: Any) -> float | None:
    american = _safe_float(odds)
    if american is None or american == 0:
        return None
    if american > 0:
        return american / 100.0
    return 100.0 / abs(american)


def _paper_profit_and_roi(row: pd.Series, result_status: str) -> tuple[float | str, float | str]:
    stake = _safe_float(row.get("simulated_stake"))
    if stake is None or stake <= 0 or result_status not in FINAL_STATUSES:
        return "", ""
    if result_status == "push":
        return 0.0, 0.0
    if result_status == "miss":
        return round(-float(stake), 6), -1.0
    win_profit = _american_profit_for_unit_stake(row.get("odds"))
    if win_profit is None:
        return "", ""
    profit = float(stake) * win_profit
    return round(profit, 6), round(win_profit, 6)


def _normalize_paper_rows(
    paper_df: pd.DataFrame,
    *,
    prediction_date: str,
    result_lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]],
) -> pd.DataFrame:
    if not isinstance(paper_df, pd.DataFrame) or paper_df.empty:
        return pd.DataFrame(columns=list(HISTORY_COLUMNS))

    working = paper_df.copy()
    for column in REQUIRED_HISTORY_COLUMNS:
        if column not in working.columns:
            working[column] = ""
    working["prediction_date"] = working["prediction_date"].replace("", str(prediction_date))
    rows: list[dict[str, Any]] = []
    for _, row in working.iterrows():
        grading = _lookup_result_for_row(row, result_lookup)
        status = _normalize_result_status(grading.get("result_status"))
        profit, roi = _paper_profit_and_roi(row, status)
        out_row = {column: row.get(column, "") for column in REQUIRED_HISTORY_COLUMNS}
        out_row["result_status"] = status
        out_row["actual_value"] = grading.get("actual_value", "")
        out_row["paper_profit"] = profit
        out_row["paper_roi"] = roi
        out_row["grading_skip_reason"] = "" if status in FINAL_STATUSES else grading.get("grading_skip_reason", "")
        rows.append(out_row)
    incoming = pd.DataFrame(rows, columns=list(HISTORY_COLUMNS))
    return incoming.drop_duplicates(
        subset=["prediction_date", "paper_bucket", "player_name", "team_abbr", "opponent", "market_type", "selection", "line"],
        keep="last",
    ).reset_index(drop=True)


def read_paper_kelly_history(path: str | Path) -> pd.DataFrame:
    df = _read_csv(Path(path), HISTORY_COLUMNS)
    for column in HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    ordered_columns = list(HISTORY_COLUMNS) + [column for column in df.columns if column not in HISTORY_COLUMNS]
    return df[ordered_columns]


def source_path_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"{SOURCE_FILE_PREFIX}_{prediction_date}.csv"


def history_path(history_root: str | Path = "data/history") -> Path:
    return Path(history_root) / HISTORY_FILE_NAME


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    operator_dir = Path(runtime_root) / "operator"
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return operator_dir / f"{stem}.txt", operator_dir / f"{stem}.csv"


def persist_paper_kelly_history(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    source_path = source_path_for_date(prediction_date=prediction_date, runtime_root=runtime_root_path)
    output_path = history_path(history_root_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Missing paper Kelly simulation CSV: {source_path}")

    paper_df = _read_csv(source_path)
    lookup = _result_lookup(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        history_root=history_root_path,
    )
    incoming = _normalize_paper_rows(
        paper_df,
        prediction_date=prediction_date,
        result_lookup=lookup,
    )
    existing = read_paper_kelly_history(output_path)
    same_date_mask = (
        existing["prediction_date"].astype(str).eq(str(prediction_date))
        if "prediction_date" in existing.columns
        else pd.Series(False, index=existing.index)
    )
    replaced_rows = int(same_date_mask.sum())
    preserved = existing.loc[~same_date_mask].copy()
    if preserved.empty:
        combined = incoming.copy()
    elif incoming.empty:
        combined = preserved.copy()
    else:
        combined = pd.concat([preserved, incoming], ignore_index=True)
    for column in HISTORY_COLUMNS:
        if column not in combined.columns:
            combined[column] = ""
    ordered_columns = list(HISTORY_COLUMNS) + [column for column in combined.columns if column not in HISTORY_COLUMNS]
    combined = combined[ordered_columns].sort_values(
        ["prediction_date", "paper_bucket", "market_type", "player_name", "selection", "line"],
        kind="mergesort",
    ).reset_index(drop=True)
    _write_csv(output_path, combined)
    current_date_rows = combined[combined["prediction_date"].astype(str) == str(prediction_date)].copy()
    return {
        "paper_kelly_history_path": output_path,
        "source_path": source_path,
        "incoming_rows": int(len(incoming)),
        "replaced_rows": replaced_rows,
        "total_rows": int(len(combined)),
        "current_date_rows": int(len(current_date_rows)),
        "pending_rows": int(current_date_rows["result_status"].astype(str).eq(PENDING_STATUS).sum()) if not current_date_rows.empty else 0,
    }


def _directional_edge_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number <= 0:
        return "<=0"
    return abs_edge_bucket(number)


def _confidence_bucket(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "unknown"
    if number > 1.0:
        number = number / 100.0
    if number < 0.55:
        return "below_0.55"
    if number < 0.65:
        return "0.55-0.65"
    if number < 0.75:
        return "0.65-0.75"
    if number < 0.85:
        return "0.75-0.85"
    return "0.85+"


def _sample_status(graded_total: int) -> str:
    if graded_total <= 0:
        return "no_graded_results"
    if graded_total < 20:
        return "insufficient_sample"
    return "usable_sample"


def _normalize_group_value(value: Any) -> str:
    return _safe_text(value, default="unknown").lower()


def _history_through_date(history_df: pd.DataFrame, through_date: str | None = None) -> pd.DataFrame:
    if not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return pd.DataFrame(columns=list(HISTORY_COLUMNS))
    df = history_df.copy()
    for column in HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    if through_date and "prediction_date" in df.columns:
        df = df[df["prediction_date"].astype(str) <= str(through_date)].copy()
    df["result_status"] = df["result_status"].map(_normalize_result_status)
    df["directional_edge_bucket"] = df["directional_edge"].map(_directional_edge_bucket)
    df["confidence_bucket"] = df["confidence"].map(_confidence_bucket)
    for column in GROUP_COLUMNS:
        df[column] = df[column].map(_normalize_group_value)
    df["paper_profit"] = pd.to_numeric(df["paper_profit"], errors="coerce")
    df["simulated_stake"] = pd.to_numeric(df["simulated_stake"], errors="coerce")
    df["pre_cap_simulated_stake"] = pd.to_numeric(df["pre_cap_simulated_stake"], errors="coerce")
    return df


def _open_pending_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty or "result_status" not in df.columns:
        return pd.Series(False, index=df.index)
    pending = df["result_status"].eq(PENDING_STATUS)
    dates = (
        df["prediction_date"].map(_safe_text)
        if "prediction_date" in df.columns
        else pd.Series("", index=df.index)
    )
    reasons = (
        df["grading_skip_reason"].map(lambda value: _safe_text(value).lower())
        if "grading_skip_reason" in df.columns
        else pd.Series("", index=df.index)
    )
    game_statuses = pd.Series("", index=df.index)
    for column in ("game_status", "game_status_bucket"):
        if column in df.columns:
            game_statuses = game_statuses.mask(game_statuses.eq(""), df[column].map(lambda value: _safe_text(value).lower()))
    return pending & (
        reasons.eq(GAME_NOT_FINAL_REASON)
        | game_statuses.isin(OPEN_GAME_STATUSES)
        | (dates.ge(_today_iso()) & ~game_statuses.isin(TERMINAL_GAME_STATUSES))
    )


def summarize_paper_kelly_history(
    history_df: pd.DataFrame,
    *,
    through_date: str | None = None,
) -> dict[str, Any]:
    df = _history_through_date(history_df, through_date=through_date)
    if df.empty:
        return {
            "total": 0,
            "graded_total": 0,
            "hits": 0,
            "misses": 0,
            "pushes": 0,
            "pending": 0,
            "open_pending": 0,
            "stale_pending": 0,
            "hit_rate": "",
            "paper_profit": 0.0,
            "paper_roi": "",
            "sample_status": "no_graded_results",
            "pre_cap_exposure": 0.0,
            "post_cap_exposure": 0.0,
            "exposure_reduced": 0.0,
            "paper_profit_by_cap_reason": {},
            "paper_roi_by_cap_reason": {},
            "pending_by_cap_reason": {},
        }
    hits = int(df["result_status"].eq("hit").sum())
    misses = int(df["result_status"].eq("miss").sum())
    pushes = int(df["result_status"].eq("push").sum())
    pending = int(df["result_status"].eq(PENDING_STATUS).sum())
    open_pending = int(_open_pending_mask(df).sum())
    stale_pending = pending - open_pending
    graded_total = hits + misses + pushes
    paper_profit = float(df["paper_profit"].fillna(0.0).sum())
    roi_stake = float(df.loc[df["result_status"].isin(HIT_MISS_STATUSES), "simulated_stake"].fillna(0.0).sum())

    pre_cap_series = df["pre_cap_simulated_stake"].fillna(df["simulated_stake"]).fillna(0.0)
    pre_cap_exposure = float(pre_cap_series.sum())
    post_cap_exposure = float(df["simulated_stake"].fillna(0.0).sum())
    exposure_reduced = pre_cap_exposure - post_cap_exposure

    cap_reason_col = df["cap_adjustment_reason"].fillna("unknown").astype(str).str.strip().str.lower()
    cap_reason_col = cap_reason_col.replace("", "unknown")
    working = df.copy()
    working["_cap_reason"] = cap_reason_col
    paper_profit_by_cap: dict[str, Any] = {}
    paper_roi_by_cap: dict[str, Any] = {}
    pending_by_cap: dict[str, int] = {}
    for reason, seg in working.groupby("_cap_reason"):
        reason_str = str(reason)
        seg_profit = float(seg["paper_profit"].fillna(0.0).sum())
        seg_roi_stake = float(seg.loc[seg["result_status"].isin(HIT_MISS_STATUSES), "simulated_stake"].fillna(0.0).sum())
        paper_profit_by_cap[reason_str] = round(seg_profit, 6)
        paper_roi_by_cap[reason_str] = round(float(seg_profit / seg_roi_stake), 6) if seg_roi_stake else ""
        pending_by_cap[reason_str] = int(seg["result_status"].eq(PENDING_STATUS).sum())

    return {
        "total": int(len(df)),
        "graded_total": graded_total,
        "hits": hits,
        "misses": misses,
        "pushes": pushes,
        "pending": pending,
        "open_pending": open_pending,
        "stale_pending": stale_pending,
        "hit_rate": round(float(hits / (hits + misses)), 4) if hits + misses else "",
        "paper_profit": round(paper_profit, 6),
        "paper_roi": round(float(paper_profit / roi_stake), 6) if roi_stake else "",
        "sample_status": _sample_status(graded_total),
        "pre_cap_exposure": round(pre_cap_exposure, 6),
        "post_cap_exposure": round(post_cap_exposure, 6),
        "exposure_reduced": round(exposure_reduced, 6),
        "paper_profit_by_cap_reason": paper_profit_by_cap,
        "paper_roi_by_cap_reason": paper_roi_by_cap,
        "pending_by_cap_reason": pending_by_cap,
    }


def build_paper_kelly_performance_report(
    history_df: pd.DataFrame,
    *,
    through_date: str | None = None,
) -> pd.DataFrame:
    df = _history_through_date(history_df, through_date=through_date)
    if df.empty:
        return pd.DataFrame(columns=list(REPORT_COLUMNS))

    rows: list[dict[str, Any]] = []
    for group_values, segment in df.groupby(list(GROUP_COLUMNS), sort=True, dropna=False):
        hits = int(segment["result_status"].eq("hit").sum())
        misses = int(segment["result_status"].eq("miss").sum())
        pushes = int(segment["result_status"].eq("push").sum())
        pending = int(segment["result_status"].eq(PENDING_STATUS).sum())
        open_pending = int(_open_pending_mask(segment).sum())
        stale_pending = pending - open_pending
        graded_total = hits + misses + pushes
        profit = float(segment["paper_profit"].fillna(0.0).sum())
        roi_stake = float(segment.loc[segment["result_status"].isin(HIT_MISS_STATUSES), "simulated_stake"].fillna(0.0).sum())
        pre_cap = float(
            segment["pre_cap_simulated_stake"]
            .fillna(segment["simulated_stake"])
            .fillna(0.0)
            .sum()
        )
        post_cap = float(segment["simulated_stake"].fillna(0.0).sum())
        rows.append(
            {
                "paper_bucket": group_values[0],
                "market_type": group_values[1],
                "selection": group_values[2],
                "context_pick_alignment": group_values[3],
                "context_caution_level": group_values[4],
                "directional_edge_bucket": group_values[5],
                "confidence_bucket": group_values[6],
                "cap_adjustment_reason": group_values[7],
                "total": int(len(segment)),
                "graded_total": graded_total,
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "open_pending": open_pending,
                "stale_pending": stale_pending,
                "hit_rate": round(float(hits / (hits + misses)), 4) if hits + misses else "",
                "pre_cap_exposure": round(pre_cap, 6),
                "post_cap_exposure": round(post_cap, 6),
                "exposure_reduced": round(pre_cap - post_cap, 6),
                "paper_profit": round(profit, 6),
                "paper_roi": round(float(profit / roi_stake), 6) if roi_stake else "",
                "sample_status": _sample_status(graded_total),
            }
        )
    report = pd.DataFrame(rows, columns=list(REPORT_COLUMNS))
    report["_profit_sort"] = pd.to_numeric(report["paper_profit"], errors="coerce")
    report = report.sort_values(
        ["graded_total", "_profit_sort", "total", "paper_bucket", "market_type"],
        ascending=[False, False, False, True, True],
        kind="mergesort",
    ).drop(columns=["_profit_sort"])
    return report.reset_index(drop=True)


def _format_value(value: Any) -> str:
    text = _safe_text(value)
    return text if text else "n/a"


def report_row_line(row: pd.Series) -> str:
    group = "/".join(_format_value(row.get(column)) for column in GROUP_COLUMNS)
    return (
        f"{group}: total={int(row.get('total') or 0)}, "
        f"graded={int(row.get('graded_total') or 0)}, "
        f"hits={int(row.get('hits') or 0)}, "
        f"misses={int(row.get('misses') or 0)}, "
        f"pushes={int(row.get('pushes') or 0)}, "
        f"pending={int(row.get('pending') or 0)}, "
        f"open_pending={int(row.get('open_pending') or 0)}, "
        f"stale_pending={int(row.get('stale_pending') or 0)}, "
        f"hit_rate={_format_value(row.get('hit_rate'))}, "
        f"paper_roi={_format_value(row.get('paper_roi'))}, "
        f"pre_cap={_format_value(row.get('pre_cap_exposure'))}, "
        f"post_cap={_format_value(row.get('post_cap_exposure'))}, "
        f"reduced={_format_value(row.get('exposure_reduced'))}, "
        f"status={_format_value(row.get('sample_status'))}"
    )


def render_paper_kelly_performance_text(
    *,
    prediction_date: str,
    history_df: pd.DataFrame,
    report_df: pd.DataFrame,
    history_csv_path: Path,
    report_csv_path: Path,
) -> str:
    summary = summarize_paper_kelly_history(history_df, through_date=prediction_date)
    lines = [
        f"{REPORT_TITLE} - {prediction_date}",
        "=" * 72,
        "Simulation only; performance tracking does not affect real Kelly eligibility or staking.",
        f"History artifact: {history_csv_path}",
        f"CSV artifact: {report_csv_path}",
        "",
        "Summary",
        "-" * 72,
        f"- total: {summary['total']}",
        f"- graded_total: {summary['graded_total']}",
        f"- hits: {summary['hits']}",
        f"- misses: {summary['misses']}",
        f"- pushes: {summary['pushes']}",
        f"- pending: {summary['pending']}",
        f"- open_pending: {summary['open_pending']}",
        f"- stale_pending: {summary['stale_pending']}",
        f"- hit_rate: {_format_value(summary['hit_rate'])}",
        f"- paper_profit: {summary['paper_profit']:.6f}",
        f"- paper_roi: {_format_value(summary['paper_roi'])}",
        f"- sample_status: {summary['sample_status']}",
        f"- pre_cap_exposure: {summary['pre_cap_exposure']:.6f}",
        f"- post_cap_exposure: {summary['post_cap_exposure']:.6f}",
        f"- exposure_reduced: {summary['exposure_reduced']:.6f}",
        "",
        "Cap Exposure by Reason",
        "-" * 72,
    ]
    profit_by_cap = summary.get("paper_profit_by_cap_reason", {})
    roi_by_cap = summary.get("paper_roi_by_cap_reason", {})
    pending_by_cap_map = summary.get("pending_by_cap_reason", {})
    if not profit_by_cap:
        lines.append("- None")
    else:
        for reason in sorted(profit_by_cap):
            profit_val = profit_by_cap[reason]
            roi_val = _format_value(roi_by_cap.get(reason))
            pending_val = pending_by_cap_map.get(reason, 0)
            lines.append(
                f"- {reason}: profit={profit_val:.6f}, roi={roi_val}, pending={pending_val}"
            )
    lines.extend([
        "",
        "Grouped Performance",
        "-" * 72,
    ])
    if report_df.empty:
        lines.append("- None")
    else:
        for _, row in report_df.iterrows():
            lines.append(f"- {report_row_line(row)}")
    return "\n".join(lines) + "\n"


def write_paper_kelly_performance_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
) -> tuple[Path, Path, pd.DataFrame, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    persist_result = persist_paper_kelly_history(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        history_root=history_root_path,
    )
    history_csv_path = history_path(history_root_path)
    history_df = read_paper_kelly_history(history_csv_path)
    report_df = build_paper_kelly_performance_report(history_df, through_date=prediction_date)
    text_path, csv_path = report_paths_for_date(prediction_date=prediction_date, runtime_root=runtime_root_path)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(csv_path, index=False)
    text_path.write_text(
        render_paper_kelly_performance_text(
            prediction_date=prediction_date,
            history_df=history_df,
            report_df=report_df,
            history_csv_path=history_csv_path,
            report_csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return text_path, csv_path, report_df, persist_result


__all__ = [
    "GROUP_COLUMNS",
    "HISTORY_COLUMNS",
    "HISTORY_FILE_NAME",
    "REPORT_COLUMNS",
    "REPORT_TITLE",
    "REQUIRED_HISTORY_COLUMNS",
    "build_paper_kelly_performance_report",
    "history_path",
    "persist_paper_kelly_history",
    "read_paper_kelly_history",
    "render_paper_kelly_performance_text",
    "report_paths_for_date",
    "report_row_line",
    "source_path_for_date",
    "summarize_paper_kelly_history",
    "write_paper_kelly_performance_report",
]
