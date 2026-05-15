from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd


STATUS_PASS = "PASS"
STATUS_PASS_NO_SLATE = "PASS_NO_SLATE"
STATUS_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
STATUS_FAIL_MISSING_FULL_MARKET = "FAIL_MISSING_FULL_MARKET"
STATUS_FAIL_UNREADABLE = "FAIL_UNREADABLE"
STATUS_FAIL_SCHEMA = "FAIL_SCHEMA"
STATUS_FAIL_UNSUPPORTED_ELITE_MARKET = "FAIL_UNSUPPORTED_ELITE_MARKET"

SUPPORTED_ACTIVE_OPERATOR_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}

SIDE_VALUES = {"over", "under"}
SIDE_IMBALANCE_WARNING_SHARE = 0.80
MISSING_FIELD_WARNING_RATIO = 0.20
HIGH_CONFIDENCE_THRESHOLD = 0.75
LOW_QUALITY_THRESHOLD = 0.55
POINTS_OR_COMBO_EDGE_THRESHOLD = 15.0
REBOUNDS_ASSISTS_EDGE_THRESHOLD = 8.0


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
        "full_market_sanity": diagnostics / f"full_market_sanity_audit_{prediction_date}.json",
        "text": operator / f"candidate_quality_drift_audit_{prediction_date}.txt",
        "json": diagnostics / f"candidate_quality_drift_audit_{prediction_date}.json",
        "csv": diagnostics / f"candidate_quality_drift_audit_{prediction_date}.csv",
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
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _read_csv(path: Path) -> tuple[pd.DataFrame, str | None, bool]:
    if not path.exists():
        return pd.DataFrame(), None, False
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False), None, True
    except pd.errors.EmptyDataError:
        return pd.DataFrame(), None, True
    except Exception as exc:
        return pd.DataFrame(), str(exc), True


def _read_json(path: Path) -> tuple[dict[str, Any], str | None, bool]:
    if not path.exists():
        return {}, None, False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, str(exc), True
    return payload if isinstance(payload, dict) else {}, None, True


def _first_existing(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def _issue(
    issues: list[dict[str, Any]],
    severity: str,
    code: str,
    message: str,
    *,
    row_count: int = 0,
    examples: list[str] | None = None,
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "message": message,
            "row_count": int(row_count),
            "examples": examples or [],
        }
    )


def _examples(df: pd.DataFrame, mask: pd.Series, columns: list[str], *, limit: int = 5) -> list[str]:
    if df.empty or not bool(mask.any()):
        return []
    available = [column for column in columns if column in df.columns]
    if not available:
        return []
    rows = df.loc[mask, available].head(limit)
    return ["; ".join(f"{column}={_safe_text(row.get(column)) or 'missing'}" for column in available) for _idx, row in rows.iterrows()]


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    match = re.search(r"-?\d+", _safe_text(value))
    return int(match.group(0)) if match else None


def _diagnostic_numeric_values(payload: Any, wanted_keys: set[str]) -> list[int]:
    values: list[int] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
            if normalized_key in wanted_keys:
                parsed = _parse_int(value)
                if parsed is not None:
                    values.append(parsed)
            values.extend(_diagnostic_numeric_values(value, wanted_keys))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_diagnostic_numeric_values(item, wanted_keys))
    return values


def _has_no_slate_context(board_diagnostics: dict[str, Any], full_market_sanity: dict[str, Any]) -> bool:
    if _safe_text(full_market_sanity.get("status")) == "PASS_NO_SLATE":
        return True
    if bool(full_market_sanity.get("no_slate_context")):
        return True
    diagnostic_games_counts = _diagnostic_numeric_values(
        board_diagnostics,
        {"games_count", "game_count", "slate_games_count", "slate_game_count"},
    )
    return any(value == 0 for value in diagnostic_games_counts)


def _value_counts(df: pd.DataFrame, column: str | None) -> dict[str, int]:
    if not column or df.empty or column not in df.columns:
        return {}
    values = df[column].map(lambda value: _safe_text(value).lower() or "missing")
    return {str(key): int(value) for key, value in values.value_counts().sort_index().items()}


def _side_breakdown(df: pd.DataFrame, market_col: str | None, side_col: str | None) -> dict[str, dict[str, int]]:
    if not market_col or not side_col or df.empty:
        return {}
    grouped = (
        df.assign(
            _market=df[market_col].map(lambda value: _safe_text(value).lower() or "missing"),
            _side=df[side_col].map(lambda value: _safe_text(value).lower() or "missing"),
        )
        .groupby(["_market", "_side"], dropna=False)
        .size()
    )
    result: dict[str, dict[str, int]] = {}
    for (market, side), count in grouped.items():
        result.setdefault(str(market), {})[str(side)] = int(count)
    return result


def _side_imbalance_metrics(side_by_market: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market, counts in sorted(side_by_market.items()):
        total = sum(counts.values())
        if total <= 0:
            continue
        top_side, top_count = max(counts.items(), key=lambda item: item[1])
        rows.append(
            {
                "market_type": market,
                "total": int(total),
                "top_side": str(top_side),
                "top_side_count": int(top_count),
                "top_side_share": round(float(top_count / total), 4),
                "counts": {str(side): int(count) for side, count in sorted(counts.items())},
            }
        )
    return rows


def _coalesce_numeric(df: pd.DataFrame, columns: tuple[str, ...]) -> tuple[pd.Series, pd.Series, list[str]]:
    index = df.index
    raw = pd.Series("", index=index, dtype=object)
    has_raw = pd.Series(False, index=index)
    available = [column for column in columns if column in df.columns]
    for column in available:
        values = df[column].map(_safe_text)
        fill_mask = ~has_raw & values.ne("")
        raw.loc[fill_mask] = values.loc[fill_mask]
        has_raw = has_raw | values.ne("")
    numeric = pd.to_numeric(raw, errors="coerce")
    finite = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    missing = ~has_raw | ~finite
    return numeric.where(finite), missing, available


def _numeric_summary(series: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    clean = clean[clean.map(lambda value: math.isfinite(float(value)))]
    if clean.empty:
        return {"count": 0}
    return {
        "count": int(len(clean)),
        "min": round(float(clean.min()), 4),
        "p25": round(float(clean.quantile(0.25)), 4),
        "median": round(float(clean.median()), 4),
        "p75": round(float(clean.quantile(0.75)), 4),
        "p90": round(float(clean.quantile(0.90)), 4),
        "max": round(float(clean.max()), 4),
        "mean": round(float(clean.mean()), 4),
    }


def _metric_distribution(df: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, Any]:
    values, missing, available = _coalesce_numeric(df, columns)
    summary = _numeric_summary(values)
    summary.update(
        {
            "available_columns": available,
            "missing_count": int(missing.sum()) if not df.empty else 0,
            "missing_rate": round(float(missing.mean()), 4) if not df.empty else 0.0,
        }
    )
    return summary


def _avg_edge_by_market_side(
    df: pd.DataFrame,
    *,
    market_col: str | None,
    side_col: str | None,
    edge_values: pd.Series,
) -> list[dict[str, Any]]:
    if df.empty or not market_col or not side_col:
        return []
    working = pd.DataFrame(
        {
            "market_type": df[market_col].map(lambda value: _safe_text(value).lower() or "missing"),
            "selection": df[side_col].map(lambda value: _safe_text(value).lower() or "missing"),
            "edge": edge_values,
        }
    ).dropna(subset=["edge"])
    if working.empty:
        return []
    rows: list[dict[str, Any]] = []
    grouped = working.groupby(["market_type", "selection"], dropna=False)["edge"]
    for (market, side), series in grouped:
        rows.append(
            {
                "market_type": str(market),
                "selection": str(side),
                "count": int(len(series)),
                "avg_edge": round(float(series.mean()), 4),
                "avg_abs_edge": round(float(series.abs().mean()), 4),
                "max_abs_edge": round(float(series.abs().max()), 4),
            }
        )
    return sorted(rows, key=lambda row: (row["market_type"], row["selection"]))


def _projection_gap_by_market_side(
    df: pd.DataFrame,
    *,
    market_col: str | None,
    side_col: str | None,
    gap_values: pd.Series,
) -> list[dict[str, Any]]:
    if df.empty or not market_col or not side_col:
        return []
    working = pd.DataFrame(
        {
            "market_type": df[market_col].map(lambda value: _safe_text(value).lower() or "missing"),
            "selection": df[side_col].map(lambda value: _safe_text(value).lower() or "missing"),
            "projection_line_gap": gap_values,
        }
    ).dropna(subset=["projection_line_gap"])
    if working.empty:
        return []
    rows: list[dict[str, Any]] = []
    grouped = working.groupby(["market_type", "selection"], dropna=False)["projection_line_gap"]
    for (market, side), series in grouped:
        rows.append(
            {
                "market_type": str(market),
                "selection": str(side),
                "count": int(len(series)),
                "avg_gap": round(float(series.mean()), 4),
                "avg_abs_gap": round(float(series.abs().mean()), 4),
                "max_abs_gap": round(float(series.abs().max()), 4),
            }
        )
    return sorted(rows, key=lambda row: (row["market_type"], row["selection"]))


def _large_edge_threshold(market_type: str) -> float:
    if market_type in {"player_rebounds", "player_assists"}:
        return REBOUNDS_ASSISTS_EDGE_THRESHOLD
    return POINTS_OR_COMBO_EDGE_THRESHOLD


def _row_identity(row: pd.Series, *, market_col: str | None, side_col: str | None, line_col: str | None) -> dict[str, Any]:
    return {
        "player_name": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "unknown",
        "market_type": _safe_text(row.get(market_col)) if market_col else "",
        "selection": _safe_text(row.get(side_col)) if side_col else "",
        "line": _safe_text(row.get(line_col)) if line_col else "",
        "edge": _safe_text(row.get("edge")) or _safe_text(row.get("side_edge")),
        "confidence": _safe_text(row.get("confidence")),
        "quality_score": _safe_text(row.get("quality_score")),
    }


def _max_abs_edge_rows(
    df: pd.DataFrame,
    *,
    edge_values: pd.Series,
    market_col: str | None,
    side_col: str | None,
    line_col: str | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if df.empty:
        return []
    working = df.copy()
    working["_edge_value"] = edge_values
    working["_abs_edge"] = edge_values.abs()
    working = working.dropna(subset=["_abs_edge"]).sort_values("_abs_edge", ascending=False).head(limit)
    rows: list[dict[str, Any]] = []
    for _idx, row in working.iterrows():
        item = _row_identity(row, market_col=market_col, side_col=side_col, line_col=line_col)
        item["abs_edge"] = round(float(row["_abs_edge"]), 4)
        rows.append(item)
    return rows


def _masked_row_identities(
    df: pd.DataFrame,
    mask: pd.Series,
    *,
    market_col: str | None,
    side_col: str | None,
    line_col: str | None,
    edge_values: pd.Series | None = None,
    confidence_values: pd.Series | None = None,
    quality_values: pd.Series | None = None,
    missing_metric_masks: dict[str, pd.Series] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if df.empty or not bool(mask.any()):
        return []
    rows: list[dict[str, Any]] = []
    for idx, row in df.loc[mask].head(limit).iterrows():
        item = _row_identity(row, market_col=market_col, side_col=side_col, line_col=line_col)
        if edge_values is not None and idx in edge_values.index and pd.notna(edge_values.loc[idx]):
            item["edge_value"] = round(float(edge_values.loc[idx]), 4)
            item["abs_edge"] = round(abs(float(edge_values.loc[idx])), 4)
        if confidence_values is not None and idx in confidence_values.index and pd.notna(confidence_values.loc[idx]):
            item["confidence_value"] = round(float(confidence_values.loc[idx]), 4)
        if quality_values is not None and idx in quality_values.index and pd.notna(quality_values.loc[idx]):
            item["quality_score_value"] = round(float(quality_values.loc[idx]), 4)
        if missing_metric_masks:
            missing_fields = [
                metric
                for metric, metric_mask in missing_metric_masks.items()
                if idx in metric_mask.index and bool(metric_mask.loc[idx])
            ]
            if missing_fields:
                item["missing_fields"] = missing_fields
        rows.append(item)
    return rows


def _elite_concentration(
    *,
    full_df: pd.DataFrame,
    elite_df: pd.DataFrame,
    full_market_col: str | None,
    elite_market_col: str | None,
) -> list[dict[str, Any]]:
    full_counts = _value_counts(full_df, full_market_col)
    elite_counts = _value_counts(elite_df, elite_market_col)
    markets = sorted(set(full_counts) | set(elite_counts))
    rows: list[dict[str, Any]] = []
    for market in markets:
        full_count = int(full_counts.get(market, 0))
        elite_count = int(elite_counts.get(market, 0))
        rows.append(
            {
                "market_type": market,
                "full_market_count": full_count,
                "elite_count": elite_count,
                "elite_share_of_full_market": round(float(elite_count / full_count), 4) if full_count else None,
            }
        )
    return rows


def _status(issues: list[dict[str, Any]], *, empty_no_slate: bool) -> str:
    failure_codes = {issue["code"] for issue in issues if issue["severity"] == "failure"}
    if "missing_full_market_board" in failure_codes:
        return STATUS_FAIL_MISSING_FULL_MARKET
    if "unreadable_full_market_board" in failure_codes:
        return STATUS_FAIL_UNREADABLE
    if "unsupported_elite_market_type" in failure_codes:
        return STATUS_FAIL_UNSUPPORTED_ELITE_MARKET
    if any(code.startswith("missing_critical_column") for code in failure_codes):
        return STATUS_FAIL_SCHEMA
    if any(issue["severity"] == "failure" for issue in issues):
        return STATUS_FAIL_SCHEMA
    if any(issue["severity"] == "warning" for issue in issues):
        return STATUS_PASS_WITH_WARNINGS
    if empty_no_slate:
        return STATUS_PASS_NO_SLATE
    return STATUS_PASS


def _recommended_action(status: str) -> str:
    if status == STATUS_PASS:
        return "no action required"
    if status == STATUS_PASS_NO_SLATE:
        return "no slate / no action required"
    if status == STATUS_PASS_WITH_WARNINGS:
        return "review candidate quality drift warnings before trusting full-market candidates"
    if status == STATUS_FAIL_MISSING_FULL_MARKET:
        return "restore or generate full_market_board artifact before auditing candidate quality"
    if status == STATUS_FAIL_UNREADABLE:
        return "inspect full_market_board CSV formatting before trusting candidates"
    if status == STATUS_FAIL_UNSUPPORTED_ELITE_MARKET:
        return "remove unsupported elite market types before trusting operator outputs"
    if status == STATUS_FAIL_SCHEMA:
        return "restore critical full-market board columns before auditing candidate quality"
    return "inspect candidate quality drift audit before trusting candidates"


def build_candidate_quality_drift_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    issues: list[dict[str, Any]] = []

    full_df, full_error, full_exists = _read_csv(paths["full_market_board"])
    elite_df, elite_error, elite_exists = _read_csv(paths["elite_board"])
    board_diagnostics, board_diag_error, board_diag_exists = _read_json(paths["board_diagnostics"])
    full_market_sanity, sanity_error, sanity_exists = _read_json(paths["full_market_sanity"])

    no_slate_context = _has_no_slate_context(board_diagnostics, full_market_sanity)
    empty_no_slate = full_exists and not full_error and full_df.empty and no_slate_context

    if not full_exists:
        _issue(
            issues,
            "failure",
            "missing_full_market_board",
            f"Missing full market board: {paths['full_market_board']}",
        )
    elif full_error:
        _issue(
            issues,
            "failure",
            "unreadable_full_market_board",
            f"Could not read full market board: {full_error}",
        )
    elif full_df.empty and not no_slate_context:
        _issue(
            issues,
            "warning",
            "empty_full_market_board",
            "Full market board exists but contains no candidate rows and no no-slate context was found.",
        )

    if elite_error:
        _issue(issues, "warning", "unreadable_elite_board", f"Could not read elite board: {elite_error}")
    if board_diag_error:
        _issue(issues, "warning", "unreadable_board_diagnostics", f"Could not read board diagnostics: {board_diag_error}")
    if sanity_error:
        _issue(issues, "warning", "unreadable_full_market_sanity_audit", f"Could not read full-market sanity audit: {sanity_error}")

    market_col = _first_existing(full_df, ("market_type", "prop_type"))
    side_col = _first_existing(full_df, ("selection", "selection_side", "side"))
    line_col = _first_existing(full_df, ("line", "sportsbook_line"))
    projection_values, projection_missing, projection_cols = _coalesce_numeric(full_df, ("model_projection", "projection"))
    edge_values, edge_missing, edge_cols = _coalesce_numeric(full_df, ("edge", "side_edge"))
    confidence_values, confidence_missing, confidence_cols = _coalesce_numeric(full_df, ("confidence",))
    quality_values, quality_missing, quality_cols = _coalesce_numeric(full_df, ("quality_score",))
    line_values, line_missing, line_cols = _coalesce_numeric(full_df, ("line", "sportsbook_line"))
    side_by_market = _side_breakdown(full_df, market_col, side_col)
    side_imbalance_by_market = _side_imbalance_metrics(side_by_market)
    large_edge_mask = pd.Series(False, index=full_df.index)
    high_conf_low_quality_mask = (
        confidence_values.notna()
        & quality_values.notna()
        & (confidence_values >= HIGH_CONFIDENCE_THRESHOLD)
        & (quality_values < LOW_QUALITY_THRESHOLD)
    )
    missing_metric_masks = {
        "projection": projection_missing,
        "edge": edge_missing,
        "confidence": confidence_missing,
        "quality_score": quality_missing,
    }

    if full_exists and not full_error and not full_df.empty:
        if market_col is None:
            _issue(issues, "failure", "missing_critical_column_market_type", "Missing critical market_type column.")
        if side_col is None:
            _issue(issues, "failure", "missing_critical_column_selection", "Missing critical side/selection column.")

        if market_col:
            market_values = full_df[market_col].map(lambda value: _safe_text(value).lower())
            unsupported_full_mask = ~market_values.isin(SUPPORTED_ACTIVE_OPERATOR_MARKETS) | market_values.eq("")
            if bool(unsupported_full_mask.any()):
                bad_values = sorted(set(market_values[unsupported_full_mask].map(lambda value: value or "missing")))
                _issue(
                    issues,
                    "warning",
                    "unsupported_full_market_type",
                    f"Full market board includes unsupported market_type values: {', '.join(bad_values)}",
                    row_count=int(unsupported_full_mask.sum()),
                    examples=_examples(full_df, unsupported_full_mask, ["player_name", market_col, side_col or "", line_col or ""]),
                )

        if side_col:
            side_values = full_df[side_col].map(lambda value: _safe_text(value).lower())
            bad_side_mask = ~side_values.isin(SIDE_VALUES)
            if bool(bad_side_mask.any()):
                _issue(
                    issues,
                    "warning",
                    "missing_or_unexpected_selection",
                    "Rows have missing or unexpected selection/side values.",
                    row_count=int(bad_side_mask.sum()),
                    examples=_examples(full_df, bad_side_mask, ["player_name", "market_type", side_col, "line"]),
                )

        for market, counts in side_by_market.items():
            total = sum(counts.values())
            if total <= 0:
                continue
            top_side, top_count = max(counts.items(), key=lambda item: item[1])
            share = top_count / total
            if share > SIDE_IMBALANCE_WARNING_SHARE:
                _issue(
                    issues,
                    "warning",
                    "side_imbalance_by_market",
                    f"{market} has {top_side} share above {SIDE_IMBALANCE_WARNING_SHARE:.0%} ({top_count}/{total}).",
                    row_count=total,
                )

        if market_col:
            market_values = full_df[market_col].map(lambda value: _safe_text(value).lower())
            edge_mask = edge_values.notna()
            thresholds = market_values.map(_large_edge_threshold)
            large_edge_mask = edge_mask & (edge_values.abs() > thresholds)
            if bool(large_edge_mask.any()):
                _issue(
                    issues,
                    "warning",
                    "very_large_absolute_edge",
                    "Rows have absolute edge above market-specific drift thresholds.",
                    row_count=int(large_edge_mask.sum()),
                    examples=_examples(full_df, large_edge_mask, ["player_name", market_col, side_col or "", line_col or "", "edge", "side_edge"]),
                )

        if bool(high_conf_low_quality_mask.any()):
            _issue(
                issues,
                "warning",
                "high_confidence_low_quality",
                f"Rows have confidence >= {HIGH_CONFIDENCE_THRESHOLD} with quality_score < {LOW_QUALITY_THRESHOLD}.",
                row_count=int(high_conf_low_quality_mask.sum()),
                examples=_examples(full_df, high_conf_low_quality_mask, ["player_name", "market_type", "selection", "confidence", "quality_score"]),
            )

        for metric, mask in missing_metric_masks.items():
            missing_count = int(mask.sum())
            missing_rate = missing_count / len(full_df) if len(full_df) else 0.0
            if missing_rate > MISSING_FIELD_WARNING_RATIO:
                _issue(
                    issues,
                    "warning",
                    f"missing_{metric}_values",
                    f"Missing or non-finite {metric} values exceed {MISSING_FIELD_WARNING_RATIO:.0%} of candidate rows.",
                    row_count=missing_count,
                    examples=_examples(full_df, mask, ["player_name", "market_type", "selection", "line", metric, "model_projection", "projection"]),
                )

    elite_market_col = _first_existing(elite_df, ("market_type", "prop_type"))
    if elite_exists and not elite_error and not elite_df.empty and elite_market_col:
        elite_markets = elite_df[elite_market_col].map(lambda value: _safe_text(value).lower())
        unsupported_elite_mask = ~elite_markets.isin(SUPPORTED_ACTIVE_OPERATOR_MARKETS) | elite_markets.eq("")
        if bool(unsupported_elite_mask.any()):
            bad_values = sorted(set(elite_markets[unsupported_elite_mask].map(lambda value: value or "missing")))
            _issue(
                issues,
                "failure",
                "unsupported_elite_market_type",
                f"Elite board includes unsupported market_type values: {', '.join(bad_values)}",
                row_count=int(unsupported_elite_mask.sum()),
                examples=_examples(elite_df, unsupported_elite_mask, ["player_name", elite_market_col, "selection", "line"]),
            )

    projection_line_gap = projection_values - line_values
    valid_gap = projection_values.notna() & line_values.notna()
    projection_line_gap = projection_line_gap.where(valid_gap)

    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    failure_count = sum(1 for issue in issues if issue["severity"] == "failure")
    status = _status(issues, empty_no_slate=empty_no_slate)

    payload: dict[str, Any] = {
        "prediction_date": prediction_date,
        "read_only": True,
        "status": status,
        "recommended_action": _recommended_action(status),
        "total_rows": int(len(full_df)) if full_exists and not full_error else 0,
        "elite_rows": int(len(elite_df)) if elite_exists and not elite_error else 0,
        "warning_count": warning_count,
        "failure_count": failure_count,
        "market_counts": _value_counts(full_df, market_col),
        "selection_counts": _value_counts(full_df, side_col),
        "side_breakdown_by_market_type": side_by_market,
        "side_imbalance_by_market_type": side_imbalance_by_market,
        "avg_edge_by_market_type_side": _avg_edge_by_market_side(
            full_df,
            market_col=market_col,
            side_col=side_col,
            edge_values=edge_values,
        ),
        "max_absolute_edge_rows": _max_abs_edge_rows(
            full_df,
            edge_values=edge_values,
            market_col=market_col,
            side_col=side_col,
            line_col=line_col,
        ),
        "suspicious_rows": {
            "very_large_absolute_edge": _max_abs_edge_rows(
                full_df.loc[large_edge_mask],
                edge_values=edge_values,
                market_col=market_col,
                side_col=side_col,
                line_col=line_col,
            ),
            "high_confidence_low_quality": _masked_row_identities(
                full_df,
                high_conf_low_quality_mask,
                market_col=market_col,
                side_col=side_col,
                line_col=line_col,
                edge_values=edge_values,
                confidence_values=confidence_values,
                quality_values=quality_values,
            ),
            "missing_fields": _masked_row_identities(
                full_df,
                projection_missing | edge_missing | confidence_missing | quality_missing,
                market_col=market_col,
                side_col=side_col,
                line_col=line_col,
                missing_metric_masks=missing_metric_masks,
            ),
        },
        "confidence_distribution": _metric_distribution(full_df, ("confidence",)),
        "quality_score_distribution": _metric_distribution(full_df, ("quality_score",)),
        "edge_distribution": _metric_distribution(full_df, ("edge", "side_edge")),
        "projection_distribution": _metric_distribution(full_df, ("model_projection", "projection")),
        "projection_line_gap_distribution": _numeric_summary(projection_line_gap),
        "projection_line_gap_by_market_type_side": _projection_gap_by_market_side(
            full_df,
            market_col=market_col,
            side_col=side_col,
            gap_values=projection_line_gap,
        ),
        "missing_field_summary": {
            "projection": {
                "available_columns": projection_cols,
                "missing_count": int(projection_missing.sum()) if not full_df.empty else 0,
                "missing_rate": round(float(projection_missing.mean()), 4) if not full_df.empty else 0.0,
            },
            "edge": {
                "available_columns": edge_cols,
                "missing_count": int(edge_missing.sum()) if not full_df.empty else 0,
                "missing_rate": round(float(edge_missing.mean()), 4) if not full_df.empty else 0.0,
            },
            "confidence": {
                "available_columns": confidence_cols,
                "missing_count": int(confidence_missing.sum()) if not full_df.empty else 0,
                "missing_rate": round(float(confidence_missing.mean()), 4) if not full_df.empty else 0.0,
            },
            "quality_score": {
                "available_columns": quality_cols,
                "missing_count": int(quality_missing.sum()) if not full_df.empty else 0,
                "missing_rate": round(float(quality_missing.mean()), 4) if not full_df.empty else 0.0,
            },
            "line": {
                "available_columns": line_cols,
                "missing_count": int(line_missing.sum()) if not full_df.empty else 0,
                "missing_rate": round(float(line_missing.mean()), 4) if not full_df.empty else 0.0,
            },
        },
        "elite_vs_full_market_concentration": _elite_concentration(
            full_df=full_df,
            elite_df=elite_df,
            full_market_col=market_col,
            elite_market_col=elite_market_col,
        ),
        "no_slate_context": no_slate_context,
        "board_diagnostics_available": board_diag_exists and board_diag_error is None,
        "full_market_sanity_audit_available": sanity_exists and sanity_error is None,
        "full_market_sanity_status": _safe_text(full_market_sanity.get("status")),
        "issues": issues,
        "source_paths": {
            "full_market_board": str(paths["full_market_board"]),
            "elite_board": str(paths["elite_board"]),
            "board_diagnostics": str(paths["board_diagnostics"]),
            "full_market_sanity": str(paths["full_market_sanity"]),
        },
        "artifact_paths": {
            "text": str(paths["text"]),
            "json": str(paths["json"]),
            "csv": str(paths["csv"]),
        },
    }
    return payload


def render_candidate_quality_drift_text(payload: dict[str, Any]) -> str:
    lines = [
        "Candidate Quality Drift Audit",
        "=" * 72,
        f"prediction_date: {payload['prediction_date']}",
        f"status: {payload['status']}",
        "read_only: true",
        "",
        "Summary",
        "-" * 72,
        f"- total rows: {payload['total_rows']}",
        f"- elite rows: {payload['elite_rows']}",
        f"- warning count: {payload['warning_count']}",
        f"- failure count: {payload['failure_count']}",
        f"- full_market_sanity_status: {payload.get('full_market_sanity_status') or 'n/a'}",
        f"- recommended action: {payload['recommended_action']}",
        "",
        "Market Counts",
        "-" * 72,
    ]
    if payload["market_counts"]:
        for market, count in sorted(payload["market_counts"].items()):
            lines.append(f"- {market}: {count}")
    else:
        lines.append("- n/a")

    lines.extend(["", "Selection Counts", "-" * 72])
    if payload["selection_counts"]:
        for side, count in sorted(payload["selection_counts"].items()):
            lines.append(f"- {side}: {count}")
    else:
        lines.append("- n/a")

    lines.extend(["", "Side Imbalance By Market", "-" * 72])
    if payload["side_imbalance_by_market_type"]:
        for row in payload["side_imbalance_by_market_type"]:
            side_text = ", ".join(f"{side}={count}" for side, count in row["counts"].items())
            lines.append(
                f"- {row['market_type']}: total={row['total']} "
                f"top_side={row['top_side']} top_share={row['top_side_share']:.1%} "
                f"({side_text})"
            )
    else:
        lines.append("- n/a")

    lines.extend(["", "Average Edge By Market / Side", "-" * 72])
    if payload["avg_edge_by_market_type_side"]:
        for row in payload["avg_edge_by_market_type_side"]:
            lines.append(
                "- "
                f"{row['market_type']} / {row['selection']}: "
                f"count={row['count']} avg_edge={row['avg_edge']} "
                f"avg_abs_edge={row['avg_abs_edge']} max_abs_edge={row['max_abs_edge']}"
            )
    else:
        lines.append("- n/a")

    lines.extend(["", "Distributions", "-" * 72])
    for label, key in [
        ("confidence", "confidence_distribution"),
        ("quality_score", "quality_score_distribution"),
        ("projection_line_gap", "projection_line_gap_distribution"),
    ]:
        row = payload.get(key, {})
        if row.get("count", 0):
            lines.append(
                f"- {label}: count={row.get('count')} mean={row.get('mean')} "
                f"median={row.get('median')} p90={row.get('p90')} "
                f"min={row.get('min')} max={row.get('max')}"
            )
        else:
            lines.append(f"- {label}: n/a")

    lines.extend(["", "Missing Field Summary", "-" * 72])
    for metric, row in payload.get("missing_field_summary", {}).items():
        lines.append(
            f"- {metric}: missing={row.get('missing_count', 0)} "
            f"rate={row.get('missing_rate', 0.0):.1%} "
            f"columns={','.join(row.get('available_columns', [])) or 'none'}"
        )

    lines.extend(["", "Elite Concentration", "-" * 72])
    if payload["elite_vs_full_market_concentration"]:
        for row in payload["elite_vs_full_market_concentration"]:
            share = row["elite_share_of_full_market"]
            share_text = "n/a" if share is None else f"{share:.1%}"
            lines.append(
                f"- {row['market_type']}: full={row['full_market_count']} "
                f"elite={row['elite_count']} elite_share_of_full={share_text}"
            )
    else:
        lines.append("- n/a")

    lines.extend(["", "Top Absolute Edge Rows", "-" * 72])
    if payload["max_absolute_edge_rows"]:
        for row in payload["max_absolute_edge_rows"][:10]:
            lines.append(
                "- "
                f"{row['player_name']}: {row['market_type']} {row['selection']} "
                f"line={row['line']} edge={row['edge']} abs_edge={row['abs_edge']}"
            )
    else:
        lines.append("- n/a")

    lines.extend(["", "Top Issues", "-" * 72])
    if payload["issues"]:
        for issue in payload["issues"][:15]:
            lines.append(
                f"- [{issue['severity']}] {issue['code']}: {issue['message']} "
                f"(rows={issue.get('row_count', 0)})"
            )
            for example in issue.get("examples", [])[:3]:
                lines.append(f"  example: {example}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _issue_csv_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issue in payload["issues"]:
        examples = issue.get("examples", [])
        rows.append(
            {
                "prediction_date": payload["prediction_date"],
                "status": payload["status"],
                "severity": issue["severity"],
                "code": issue["code"],
                "message": issue["message"],
                "row_count": issue.get("row_count", 0),
                "examples": " | ".join(examples),
            }
        )
    return rows


def write_candidate_quality_drift_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path, dict[str, Any]]:
    payload = build_candidate_quality_drift_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    paths = {key: Path(value) for key, value in payload["artifact_paths"].items()}
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["text"].write_text(render_candidate_quality_drift_text(payload), encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    rows = _issue_csv_rows(payload)
    fieldnames = ["prediction_date", "status", "severity", "code", "message", "row_count", "examples"]
    with paths["csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return paths["text"], paths["json"], paths["csv"], payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit candidate quality drift for the full-market board.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    args = parser.parse_args(argv)

    text_path, json_path, csv_path, payload = write_candidate_quality_drift_audit(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
    )
    print(f"candidate_quality_drift_audit_txt={text_path}")
    print(f"candidate_quality_drift_audit_json={json_path}")
    print(f"candidate_quality_drift_audit_csv={csv_path}")
    print(
        "candidate_quality_drift_audit_status "
        f"status={payload['status']} "
        f"rows={payload['total_rows']} "
        f"elite_rows={payload['elite_rows']} "
        f"warnings={payload['warning_count']} "
        f"failures={payload['failure_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
