from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

MANUAL_CONTEXT_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "player_name",
    "team",
    "status",
    "minutes_limit",
    "projection_adjustment",
    "confidence_adjustment",
    "reason",
)

MANUAL_CONTEXT_OUTPUT_COLUMNS: tuple[str, ...] = (
    "manual_status",
    "manual_minutes_limit",
    "manual_projection_adjustment",
    "manual_confidence_adjustment",
    "manual_context_reason",
    "manual_context_applied",
)

_NUMERIC_COLUMNS: tuple[str, ...] = (
    "minutes_limit",
    "projection_adjustment",
    "confidence_adjustment",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _player_key(value: Any) -> str:
    return " ".join(_text(value).lower().split())


def _team_key(value: Any) -> str:
    return _text(value).upper()


def _manual_context_path(prediction_date: str, config_dir: str | Path) -> Path:
    return Path(config_dir) / f"manual_player_context_{prediction_date}.csv"


def _default_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    defaults: dict[str, Any] = {
        "manual_status": "",
        "manual_minutes_limit": pd.NA,
        "manual_projection_adjustment": pd.NA,
        "manual_confidence_adjustment": pd.NA,
        "manual_context_reason": "",
        "manual_context_applied": False,
    }
    for column, value in defaults.items():
        if column not in out.columns:
            out[column] = value
    return out


def _coerce_optional_float(value: Any, *, column: str, row_number: int, warnings: list[str]) -> float | None:
    raw = _text(value)
    if raw == "":
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        warnings.append(f"row {row_number}: invalid numeric value for {column}: {raw}")
        return None
    if math.isnan(number) or math.isinf(number):
        warnings.append(f"row {row_number}: invalid numeric value for {column}: {raw}")
        return None
    return number


def load_manual_player_context(
    prediction_date: str,
    *,
    config_dir: str | Path = "config",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load operator-supplied context notes without applying any model effect."""

    path = _manual_context_path(prediction_date, config_dir)
    diagnostics: dict[str, Any] = {
        "prediction_date": prediction_date,
        "path": str(path),
        "file_found": bool(path.exists()),
        "rows": 0,
        "date_filtered_rows": 0,
        "warnings": [],
        "missing_columns": [],
    }
    if not path.exists():
        return pd.DataFrame(columns=MANUAL_CONTEXT_COLUMNS), diagnostics

    try:
        raw = pd.read_csv(path, dtype=str).fillna("")
    except Exception as exc:
        diagnostics["warnings"].append(f"could not read manual context file: {exc}")
        return pd.DataFrame(columns=MANUAL_CONTEXT_COLUMNS), diagnostics

    diagnostics["rows"] = int(len(raw))
    missing = [column for column in MANUAL_CONTEXT_COLUMNS if column not in raw.columns]
    diagnostics["missing_columns"] = missing
    for column in missing:
        raw[column] = ""

    warnings: list[str] = list(diagnostics["warnings"])
    rows: list[dict[str, Any]] = []
    for idx, row in raw.iterrows():
        row_number = int(idx) + 2
        row_date = _text(row.get("prediction_date"))
        if row_date and row_date != prediction_date:
            continue

        parsed = {column: _text(row.get(column)) for column in MANUAL_CONTEXT_COLUMNS}
        for column in _NUMERIC_COLUMNS:
            parsed[column] = _coerce_optional_float(
                row.get(column),
                column=column,
                row_number=row_number,
                warnings=warnings,
            )
        parsed["player_name_key"] = _player_key(parsed.get("player_name"))
        parsed["team_key"] = _team_key(parsed.get("team"))
        if not parsed["player_name_key"] or not parsed["team_key"]:
            warnings.append(f"row {row_number}: missing player_name or team; row ignored")
            continue
        rows.append(parsed)

    diagnostics["warnings"] = warnings
    diagnostics["date_filtered_rows"] = int(len(rows))
    return pd.DataFrame(rows), diagnostics


def apply_manual_player_context(
    candidates: pd.DataFrame,
    manual_context: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach manual context fields to candidate-like rows without changing scores."""

    out = _default_columns(candidates)
    diagnostics: dict[str, Any] = {
        "candidate_rows": int(len(out)),
        "candidate_matches": 0,
        "matched_context_rows": 0,
        "matched_players": [],
    }
    if out.empty or manual_context.empty:
        return out, diagnostics

    if "player_name" not in out.columns:
        return out, diagnostics

    team_source = "team_abbr" if "team_abbr" in out.columns else "team" if "team" in out.columns else ""
    if not team_source:
        return out, diagnostics

    context_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in manual_context.iterrows():
        key = (_player_key(row.get("player_name")), _team_key(row.get("team")))
        if key[0] and key[1]:
            context_lookup[key] = dict(row)

    if not context_lookup:
        return out, diagnostics

    matched_context_keys: set[tuple[str, str]] = set()
    matched_players: set[str] = set()
    matched_candidate_rows = 0
    for idx, row in out.iterrows():
        key = (_player_key(row.get("player_name")), _team_key(row.get(team_source)))
        context = context_lookup.get(key)
        if not context:
            continue
        matched_candidate_rows += 1
        out.at[idx, "manual_status"] = _text(context.get("status"))
        out.at[idx, "manual_minutes_limit"] = context.get("minutes_limit")
        out.at[idx, "manual_projection_adjustment"] = context.get("projection_adjustment")
        out.at[idx, "manual_confidence_adjustment"] = context.get("confidence_adjustment")
        out.at[idx, "manual_context_reason"] = _text(context.get("reason"))
        out.at[idx, "manual_context_applied"] = False
        matched_context_keys.add(key)
        matched_players.add(_text(row.get("player_name")))

    diagnostics["candidate_matches"] = int(matched_candidate_rows)
    diagnostics["matched_context_rows"] = int(len(matched_context_keys))
    diagnostics["matched_players"] = sorted(matched_players)
    return out, diagnostics


def build_manual_context_diagnostics(
    *,
    prediction_date: str,
    load_diagnostics: dict[str, Any],
    candidate_diagnostics: dict[str, Any],
    context_rows: pd.DataFrame,
) -> dict[str, Any]:
    warnings = list(load_diagnostics.get("warnings") or [])
    return {
        "prediction_date": prediction_date,
        "file_found": bool(load_diagnostics.get("file_found", False)),
        "path": str(load_diagnostics.get("path", "")),
        "rows": int(load_diagnostics.get("rows", 0) or 0),
        "date_filtered_rows": int(load_diagnostics.get("date_filtered_rows", 0) or 0),
        "candidate_rows": int(candidate_diagnostics.get("candidate_rows", 0) or 0),
        "candidate_matches": int(candidate_diagnostics.get("candidate_matches", 0) or 0),
        "matched_context_rows": int(candidate_diagnostics.get("matched_context_rows", 0) or 0),
        "matched_players": list(candidate_diagnostics.get("matched_players", []) or []),
        "missing_columns": list(load_diagnostics.get("missing_columns", []) or []),
        "warnings": warnings,
        "context_rows": context_rows.drop(columns=["player_name_key", "team_key"], errors="ignore").to_dict("records")
        if not context_rows.empty
        else [],
        "passive_mode": True,
        "projection_changed": False,
        "confidence_changed": False,
        "selection_logic_changed": False,
    }


def write_manual_context_diagnostics(
    *,
    prediction_date: str,
    runtime_root: str | Path,
    load_diagnostics: dict[str, Any],
    candidate_diagnostics: dict[str, Any],
    context_rows: pd.DataFrame,
) -> tuple[Path, dict[str, Any]]:
    payload = build_manual_context_diagnostics(
        prediction_date=prediction_date,
        load_diagnostics=load_diagnostics,
        candidate_diagnostics=candidate_diagnostics,
        context_rows=context_rows,
    )
    diagnostics_dir = Path(runtime_root) / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    path = diagnostics_dir / f"manual_context_{prediction_date}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path, payload
