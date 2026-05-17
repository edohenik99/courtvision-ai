from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from typing import Any

import pandas as pd

from courtvision.artifact_guard import guard_no_existing_artifact


STATUS_PASS = "PASS"
STATUS_PASS_NO_SLATE = "PASS_NO_SLATE"
STATUS_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
STATUS_FAIL_MISSING_FULL_MARKET = "FAIL_MISSING_FULL_MARKET"
STATUS_FAIL_UNREADABLE = "FAIL_UNREADABLE"
STATUS_FAIL_SCHEMA = "FAIL_SCHEMA"
STATUS_FAIL_DUPLICATES = "FAIL_DUPLICATES"
STATUS_FAIL_UNSUPPORTED_MARKET = "FAIL_UNSUPPORTED_MARKET"

SUPPORTED_MARKETS = {
    "player_points": (0.5, 45.5),
    "player_rebounds": (0.5, 25.5),
    "player_assists": (0.5, 20.5),
    "player_points_rebounds_assists": (2.5, 70.5),
    "player_points_rebounds": (2.5, 60.5),
    "player_points_assists": (2.5, 60.5),
    "player_rebounds_assists": (1.5, 40.5),
}

SIDE_VALUES = {"over", "under"}
LARGE_ABS_EDGE_THRESHOLD = 25.0
SIDE_IMBALANCE_MIN_ROWS = 10
SIDE_IMBALANCE_MIN_SHARE = 0.05


def _artifact_paths(runtime_root: Path, prediction_date: str) -> dict[str, Path]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    return {
        "full_market_board": operator / f"full_market_board_{prediction_date}.csv",
        "elite_board": operator / f"elite_board_{prediction_date}.csv",
        "operator_card": operator / f"operator_card_{prediction_date}.txt",
        "board_diagnostics": diagnostics / f"board_diagnostics_{prediction_date}.json",
        "text": operator / f"full_market_sanity_audit_{prediction_date}.txt",
        "json": diagnostics / f"full_market_sanity_audit_{prediction_date}.json",
        "csv": diagnostics / f"full_market_sanity_audit_{prediction_date}.csv",
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


def _read_text(path: Path) -> tuple[str, str | None, bool]:
    if not path.exists():
        return "", None, False
    try:
        return path.read_text(encoding="utf-8"), None, True
    except Exception as exc:
        return "", str(exc), True


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


def _normalized_line(value: Any) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    number = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    if pd.isna(number) or not math.isfinite(float(number)):
        return text.lower()
    return f"{float(number):.3f}"


def _identity_key(df: pd.DataFrame, *, name_col: str, market_col: str, side_col: str, line_col: str) -> pd.Series:
    return (
        df[name_col].map(lambda value: _safe_text(value).lower())
        + "|"
        + df[market_col].map(lambda value: _safe_text(value).lower())
        + "|"
        + df[side_col].map(lambda value: _safe_text(value).lower())
        + "|"
        + df[line_col].map(_normalized_line)
    )


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce")


def _non_finite_mask(df: pd.DataFrame, column: str) -> pd.Series:
    numeric = _numeric_series(df, column)
    raw_has_value = df[column].map(lambda value: _safe_text(value) != "")
    finite = numeric.map(lambda value: pd.notna(value) and math.isfinite(float(value)))
    return raw_has_value & ~finite


def _examples(df: pd.DataFrame, mask: pd.Series, columns: list[str], *, limit: int = 5) -> list[str]:
    if df.empty or not bool(mask.any()):
        return []
    available = [column for column in columns if column in df.columns]
    if not available:
        return []
    rows = df.loc[mask, available].head(limit)
    return ["; ".join(f"{column}={_safe_text(row.get(column)) or 'missing'}" for column in available) for _idx, row in rows.iterrows()]


def _breakdown(df: pd.DataFrame, column: str | None) -> dict[str, int]:
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


def _parse_operator_context(operator_text: str) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for raw_line in operator_text.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("final_decision:"):
            context["final_decision"] = line.split(":", 1)[1].strip()
        elif lower.startswith("- games count:"):
            context["games_count"] = _parse_int(line.split(":", 1)[1])
    return context


def _has_no_slate_context(diagnostics_payload: dict[str, Any], operator_text: str) -> bool:
    diagnostic_games_counts = _diagnostic_numeric_values(
        diagnostics_payload,
        {"games_count", "game_count", "slate_games_count", "slate_game_count"},
    )
    if any(value == 0 for value in diagnostic_games_counts):
        return True

    operator_context = _parse_operator_context(operator_text)
    if operator_context.get("games_count") == 0:
        return True

    final_decision = _safe_text(operator_context.get("final_decision")).upper().replace("_", " ")
    operator_lower = operator_text.lower()
    return final_decision == "NO BET" and ("no slate" in operator_lower or "games count: 0" in operator_lower)


def _status(issues: list[dict[str, Any]], *, empty_no_slate: bool = False) -> str:
    failure_codes = {issue["code"] for issue in issues if issue["severity"] == "failure"}
    if "missing_full_market_board" in failure_codes:
        return STATUS_FAIL_MISSING_FULL_MARKET
    if "unreadable_full_market_board" in failure_codes:
        return STATUS_FAIL_UNREADABLE
    if any(code.startswith("missing_critical_column") for code in failure_codes):
        return STATUS_FAIL_SCHEMA
    if "unsupported_market_type" in failure_codes:
        return STATUS_FAIL_UNSUPPORTED_MARKET
    if "duplicate_candidate_rows" in failure_codes:
        return STATUS_FAIL_DUPLICATES
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
        return "review warnings before trusting full-market candidate quality"
    if status == STATUS_FAIL_MISSING_FULL_MARKET:
        return "restore or generate full_market_board artifact before auditing"
    if status == STATUS_FAIL_UNREADABLE:
        return "inspect full_market_board CSV formatting before trusting candidates"
    if status == STATUS_FAIL_DUPLICATES:
        return "inspect duplicate candidate rows before downstream trust"
    if status == STATUS_FAIL_UNSUPPORTED_MARKET:
        return "remove unsupported market types from downstream trust before using candidates"
    if status == STATUS_FAIL_SCHEMA:
        return "restore required full-market board columns before downstream trust"
    return "inspect full-market sanity audit before trusting candidates"


def build_full_market_sanity_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Any]:
    runtime_root = Path(runtime_root)
    paths = _artifact_paths(runtime_root, prediction_date)
    issues: list[dict[str, Any]] = []
    full_df, full_error, full_exists = _read_csv(paths["full_market_board"])
    elite_df, elite_error, elite_exists = _read_csv(paths["elite_board"])
    diagnostics_payload, diagnostics_error, diagnostics_exists = _read_json(paths["board_diagnostics"])
    operator_text, operator_error, operator_exists = _read_text(paths["operator_card"])
    no_slate_context = _has_no_slate_context(diagnostics_payload, operator_text)
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
    elif full_df.empty:
        if not no_slate_context:
            _issue(
                issues,
                "warning",
                "empty_full_market_board",
                "Full market board exists but contains no candidate rows.",
            )

    if elite_error:
        _issue(issues, "warning", "unreadable_elite_board", f"Could not read elite board: {elite_error}")
    if diagnostics_error:
        _issue(
            issues,
            "warning",
            "unreadable_board_diagnostics",
            f"Could not read board diagnostics: {diagnostics_error}",
        )
    if operator_error:
        _issue(issues, "warning", "unreadable_operator_card", f"Could not read operator card: {operator_error}")

    market_col = _first_existing(full_df, ("market_type", "prop_type"))
    side_col = _first_existing(full_df, ("selection", "selection_side", "side"))
    line_col = _first_existing(full_df, ("line", "sportsbook_line"))
    player_col = _first_existing(full_df, ("player_name", "entity_name"))

    if full_exists and not full_error and not full_df.empty:
        if market_col is None:
            _issue(issues, "failure", "missing_critical_column_market_type", "Missing critical market_type column.")
        if side_col is None:
            _issue(issues, "failure", "missing_critical_column_side", "Missing critical side/selection column.")
        if line_col is None:
            _issue(issues, "failure", "missing_critical_column_line", "Missing critical line/sportsbook_line column.")

        if "player_name" not in full_df.columns:
            _issue(issues, "warning", "missing_player_name_column", "Missing player_name column.")
        elif bool(full_df["player_name"].map(lambda value: _safe_text(value) == "").any()):
            mask = full_df["player_name"].map(lambda value: _safe_text(value) == "")
            _issue(
                issues,
                "warning",
                "missing_player_name",
                "One or more candidate rows are missing player_name.",
                row_count=int(mask.sum()),
                examples=_examples(full_df, mask, ["player_name", "entity_name", "market_type", "selection", "line"]),
            )

        for label, columns in {"team": ("team", "team_abbr"), "opponent": ("opponent",)}.items():
            present = [column for column in columns if column in full_df.columns]
            if present:
                missing_mask = full_df[present].apply(
                    lambda row: all(_safe_text(value) == "" for value in row),
                    axis=1,
                )
                if bool(missing_mask.any()):
                    _issue(
                        issues,
                        "warning",
                        f"missing_{label}",
                        f"One or more rows are missing {label}.",
                        row_count=int(missing_mask.sum()),
                        examples=_examples(full_df, missing_mask, ["player_name", "market_type", "selection", "line", *present]),
                    )

        if market_col:
            market_values = full_df[market_col].map(lambda value: _safe_text(value).lower())
            bad_market_mask = ~market_values.isin(SUPPORTED_MARKETS) | market_values.eq("")
            if bool(bad_market_mask.any()):
                bad_values = sorted(set(market_values[bad_market_mask].map(lambda value: value or "missing")))
                _issue(
                    issues,
                    "failure",
                    "unsupported_market_type",
                    f"Unsupported market_type values: {', '.join(bad_values)}",
                    row_count=int(bad_market_mask.sum()),
                    examples=_examples(full_df, bad_market_mask, ["player_name", market_col, side_col or "", line_col or ""]),
                )

        if side_col:
            side_values = full_df[side_col].map(lambda value: _safe_text(value).lower())
            bad_side_mask = ~side_values.isin(SIDE_VALUES)
            if bool(bad_side_mask.any()):
                _issue(
                    issues,
                    "warning",
                    "missing_or_unexpected_side",
                    "Rows have missing or unexpected side values.",
                    row_count=int(bad_side_mask.sum()),
                    examples=_examples(full_df, bad_side_mask, ["player_name", "market_type", side_col, "line"]),
                )

        if line_col:
            numeric_line = _numeric_series(full_df, line_col)
            missing_line_mask = full_df[line_col].map(lambda value: _safe_text(value) == "")
            non_numeric_line_mask = full_df[line_col].map(lambda value: _safe_text(value) != "") & numeric_line.isna()
            non_finite_line_mask = numeric_line.map(lambda value: pd.notna(value) and not math.isfinite(float(value)))
            line_issue_mask = missing_line_mask | non_numeric_line_mask | non_finite_line_mask
            if bool(line_issue_mask.any()):
                _issue(
                    issues,
                    "warning",
                    "missing_or_non_numeric_line",
                    "Rows have missing, non-numeric, or non-finite line values.",
                    row_count=int(line_issue_mask.sum()),
                    examples=_examples(full_df, line_issue_mask, ["player_name", "market_type", "selection", line_col]),
                )
            if market_col:
                market_values = full_df[market_col].map(lambda value: _safe_text(value).lower())
                for market, (low, high) in SUPPORTED_MARKETS.items():
                    mask = market_values.eq(market) & numeric_line.notna() & numeric_line.map(
                        lambda value: math.isfinite(float(value)) and (float(value) < low or float(value) > high)
                    )
                    if bool(mask.any()):
                        _issue(
                            issues,
                            "warning",
                            "suspicious_line",
                            f"{market} line outside expected range {low}..{high}.",
                            row_count=int(mask.sum()),
                            examples=_examples(full_df, mask, ["player_name", market_col, side_col or "", line_col]),
                        )

        if player_col and market_col and side_col and line_col:
            keys = _identity_key(full_df, name_col=player_col, market_col=market_col, side_col=side_col, line_col=line_col)
            dup_mask = keys.duplicated(keep=False) & keys.map(lambda value: bool(value.replace("|", "")))
            if bool(dup_mask.any()):
                _issue(
                    issues,
                    "failure",
                    "duplicate_candidate_rows",
                    "Duplicate player/market/side/line candidate rows found.",
                    row_count=int(dup_mask.sum()),
                    examples=_examples(full_df, dup_mask, [player_col, market_col, side_col, line_col]),
                )

        metric_groups = {
            "projection": ("model_projection", "projection"),
            "edge": ("edge", "side_edge"),
            "confidence": ("confidence",),
            "quality": ("quality_score", "selection_score"),
        }
        for metric, columns in metric_groups.items():
            present = [column for column in columns if column in full_df.columns]
            if not present:
                _issue(issues, "warning", f"missing_{metric}_column", f"Missing expected {metric} column.")
                continue
            for column in present:
                mask = _non_finite_mask(full_df, column)
                if bool(mask.any()):
                    _issue(
                        issues,
                        "warning",
                        f"non_finite_{metric}_values",
                        f"Column {column} has non-finite {metric} values.",
                        row_count=int(mask.sum()),
                        examples=_examples(full_df, mask, ["player_name", "market_type", "selection", "line", column]),
                    )
        edge_col = _first_existing(full_df, ("edge", "side_edge"))
        if edge_col:
            edge_values = _numeric_series(full_df, edge_col)
            large_edge_mask = edge_values.notna() & edge_values.map(
                lambda value: math.isfinite(float(value)) and abs(float(value)) > LARGE_ABS_EDGE_THRESHOLD
            )
            if bool(large_edge_mask.any()):
                _issue(
                    issues,
                    "warning",
                    "very_large_absolute_edge",
                    f"Rows have absolute {edge_col} greater than {LARGE_ABS_EDGE_THRESHOLD}.",
                    row_count=int(large_edge_mask.sum()),
                    examples=_examples(full_df, large_edge_mask, ["player_name", "market_type", "selection", "line", edge_col]),
                )

        side_breakdown = _side_breakdown(full_df, market_col, side_col)
        for market, counts in side_breakdown.items():
            total = sum(counts.values())
            if total < SIDE_IMBALANCE_MIN_ROWS or market not in SUPPORTED_MARKETS:
                continue
            for side in SIDE_VALUES:
                share = counts.get(side, 0) / total if total else 0.0
                if share < SIDE_IMBALANCE_MIN_SHARE:
                    _issue(
                        issues,
                        "warning",
                        "side_imbalance_by_market",
                        f"{market} has {side} share below {SIDE_IMBALANCE_MIN_SHARE:.0%} ({counts.get(side, 0)}/{total}).",
                        row_count=total,
                    )

        if elite_exists and not elite_error and not elite_df.empty:
            elite_market_col = _first_existing(elite_df, ("market_type", "prop_type"))
            elite_side_col = _first_existing(elite_df, ("selection", "selection_side", "side"))
            elite_line_col = _first_existing(elite_df, ("line", "sportsbook_line"))
            elite_player_col = _first_existing(elite_df, ("player_name", "entity_name"))
            if all([player_col, market_col, side_col, line_col, elite_player_col, elite_market_col, elite_side_col, elite_line_col]):
                full_keys = set(
                    _identity_key(full_df, name_col=player_col, market_col=market_col, side_col=side_col, line_col=line_col)
                )
                elite_keys = _identity_key(
                    elite_df,
                    name_col=elite_player_col,
                    market_col=elite_market_col,
                    side_col=elite_side_col,
                    line_col=elite_line_col,
                )
                missing_elite_mask = ~elite_keys.isin(full_keys)
                if bool(missing_elite_mask.any()):
                    _issue(
                        issues,
                        "warning",
                        "elite_rows_missing_from_full_market",
                        "Elite rows do not appear in full market board by player/market/side/line.",
                        row_count=int(missing_elite_mask.sum()),
                        examples=_examples(elite_df, missing_elite_mask, [elite_player_col, elite_market_col, elite_side_col, elite_line_col]),
                    )

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
        "market_breakdown": _breakdown(full_df, market_col),
        "side_breakdown": _side_breakdown(full_df, market_col, side_col),
        "issues": issues,
        "no_slate_context": no_slate_context,
        "board_diagnostics_available": diagnostics_exists and diagnostics_error is None,
        "board_diagnostics": diagnostics_payload,
        "operator_card_available": operator_exists and operator_error is None,
        "source_paths": {
            "full_market_board": str(paths["full_market_board"]),
            "elite_board": str(paths["elite_board"]),
            "operator_card": str(paths["operator_card"]),
            "board_diagnostics": str(paths["board_diagnostics"]),
        },
        "artifact_paths": {
            "text": str(paths["text"]),
            "json": str(paths["json"]),
            "csv": str(paths["csv"]),
        },
    }
    return payload


def render_full_market_sanity_text(payload: dict[str, Any]) -> str:
    lines = [
        "Full-Market Candidate Sanity Audit",
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
        f"- recommended action: {payload['recommended_action']}",
        "",
        "Market Breakdown",
        "-" * 72,
    ]
    if payload["market_breakdown"]:
        for market, count in sorted(payload["market_breakdown"].items()):
            lines.append(f"- {market}: {count}")
    else:
        lines.append("- n/a")
    lines.extend(["", "Side Breakdown", "-" * 72])
    if payload["side_breakdown"]:
        for market, sides in sorted(payload["side_breakdown"].items()):
            side_text = ", ".join(f"{side}={count}" for side, count in sorted(sides.items()))
            lines.append(f"- {market}: {side_text}")
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


def write_full_market_sanity_audit(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    force: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    payload = build_full_market_sanity_audit(prediction_date=prediction_date, runtime_root=runtime_root)
    paths = {key: Path(value) for key, value in payload["artifact_paths"].items()}
    for artifact_key, path in paths.items():
        guard_no_existing_artifact(
            output_path=path,
            force=force,
            caller="write_full_market_sanity_audit",
            artifact_label=f"full_market_sanity_{artifact_key}",
        )
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["text"].write_text(render_full_market_sanity_text(payload), encoding="utf-8")
    paths["json"].write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    rows = _issue_csv_rows(payload)
    fieldnames = ["prediction_date", "status", "severity", "code", "message", "row_count", "examples"]
    with paths["csv"].open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return paths["text"], paths["json"], paths["csv"], payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit full-market candidate board sanity.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow existing full-market sanity audit artifacts to be overwritten intentionally.",
    )
    args = parser.parse_args(argv)

    text_path, json_path, csv_path, payload = write_full_market_sanity_audit(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        force=args.force,
    )
    print(f"full_market_sanity_audit_txt={text_path}")
    print(f"full_market_sanity_audit_json={json_path}")
    print(f"full_market_sanity_audit_csv={csv_path}")
    print(
        "full_market_sanity_audit_status "
        f"status={payload['status']} "
        f"rows={payload['total_rows']} "
        f"elite_rows={payload['elite_rows']} "
        f"warnings={payload['warning_count']} "
        f"failures={payload['failure_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
