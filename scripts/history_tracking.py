from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from courtvision_ai import CourtVisionAI
from courtvision.calibration.buckets import abs_edge_bucket as _abs_edge_bucket


PICK_HISTORY_COLUMNS = [
    "prediction_date",
    "run_timestamp",
    "player_name",
    "team",
    "opponent",
    "game_id",
    "market",
    "selection",
    "line",
    "projection",
    "edge",
    "abs_edge",
    "odds",
    "confidence",
    "quality_score",
    "qualification_reason",
    "provider_used",
    "result_status",
    # Context / Kelly columns — populated from kelly_stakes when available
    "kelly_eligible",
    "skip_reason",
    "context_caution_level",
    "context_pick_alignment",
    "line_source",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _line_key(value: Any) -> str:
    """Normalize a sportsbook line value to a consistent 4-decimal string for dict joins."""
    if value is None:
        return "__none__"
    s = str(value).strip()
    if s in ("", "nan", "None"):
        return "__none__"
    try:
        return f"{float(s):.4f}"
    except (TypeError, ValueError):
        return s.lower()


def _confidence_bucket(value: float) -> str:
    """Bucket a 0–1 float confidence; called on pd.to_numeric result (NaN-safe)."""
    if value != value:  # NaN check
        return "unknown"
    if value < 0.55:
        return "below_0.55"
    if value < 0.65:
        return "0.55-0.65"
    if value < 0.75:
        return "0.65-0.75"
    if value < 0.85:
        return "0.75-0.85"
    return "0.85+"


def _load_csv(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns or [])
    return pd.read_csv(path)


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _provider_from_audit_summary(audit_summary_path: Path) -> str:
    if not audit_summary_path.exists():
        return "unknown"
    try:
        payload = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    summary = payload.get("summary") or {}
    return _safe_text(summary.get("provider_used"), default="unknown")


def _build_kelly_lookup(kelly_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Build a lookup dict from a kelly_stakes DataFrame for joining into pick_history.

    Key format: "{player.lower()}|{market.lower()}|{selection.lower()}|{line_key}"
    Returns context columns: kelly_eligible, skip_reason, context_caution_level, context_pick_alignment.
    """
    if kelly_df.empty:
        return {}
    name_col = "player_name" if "player_name" in kelly_df.columns else "entity_name"
    mkt_col = "market_type" if "market_type" in kelly_df.columns else "market"
    line_col = next((c for c in ("line", "sportsbook_line", "line_value") if c in kelly_df.columns), None)
    lookup: dict[str, dict[str, Any]] = {}
    for _, row in kelly_df.iterrows():
        player = _safe_text(row.get(name_col)).lower()
        market = _safe_text(row.get(mkt_col)).lower()
        selection = _safe_text(row.get("selection")).lower()
        line = _line_key(row.get(line_col) if line_col else None)
        key = f"{player}|{market}|{selection}|{line}"
        eligible_raw = row.get("kelly_eligible") if row.get("kelly_eligible") is not None else row.get("eligible")
        if isinstance(eligible_raw, bool):
            kelly_eligible = "True" if eligible_raw else "False"
        elif str(eligible_raw).strip().lower() in ("true", "1", "yes"):
            kelly_eligible = "True"
        elif str(eligible_raw).strip().lower() in ("false", "0", "no"):
            kelly_eligible = "False"
        else:
            kelly_eligible = ""
        lookup[key] = {
            "kelly_eligible": kelly_eligible,
            "skip_reason": _safe_text(row.get("skip_reason")),
            "context_caution_level": _safe_text(row.get("context_caution_level")),
            "context_pick_alignment": _safe_text(row.get("context_pick_alignment")),
        }
    return lookup


def migrate_pick_history_schema(
    history_root: str | Path = "data/history",
) -> dict[str, Any]:
    """Idempotent migration: add missing PICK_HISTORY_COLUMNS to an existing pick_history.csv.

    Safe to re-run: only adds absent columns with empty-string defaults.
    Never removes, renames, or overwrites existing data.
    Returns a summary dict with keys: status, added_columns, total_rows.
    """
    history_root_path = Path(history_root)
    pick_history_path = history_root_path / "pick_history.csv"
    if not pick_history_path.exists():
        return {"status": "no_file", "added_columns": [], "total_rows": 0}
    df = pd.read_csv(pick_history_path)
    added: list[str] = []
    for col in PICK_HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = ""
            added.append(col)
    if added:
        _write_csv(pick_history_path, df[PICK_HISTORY_COLUMNS])
    return {"status": "ok", "added_columns": added, "total_rows": len(df)}


def persist_daily_picks(
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    result_status: str = "pending",
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    operator_path = runtime_root_path / "operator" / f"elite_board_{prediction_date}.csv"
    picks_output_path = runtime_root_path / "history" / f"picks_{prediction_date}.csv"
    audit_summary_path = runtime_root_path / "operator" / f"elite_pipeline_audit_summary_{prediction_date}.json"
    pick_history_path = history_root_path / "pick_history.csv"

    if not operator_path.exists():
        raise FileNotFoundError(f"Missing elite board CSV: {operator_path}")

    elite_df = pd.read_csv(operator_path)
    picks_output_path.parent.mkdir(parents=True, exist_ok=True)
    elite_df.to_csv(picks_output_path, index=False)

    # Load kelly_stakes for context / eligibility columns — best-effort, no hard failure.
    kelly_stakes_path = runtime_root_path / "operator" / f"kelly_stakes_{prediction_date}.csv"
    kelly_lookup: dict[str, dict[str, Any]] = {}
    if kelly_stakes_path.exists():
        try:
            kelly_lookup = _build_kelly_lookup(pd.read_csv(kelly_stakes_path))
        except Exception:
            kelly_lookup = {}

    provider_used = _provider_from_audit_summary(audit_summary_path)
    timestamp = datetime.now(timezone.utc).isoformat()

    normalized_rows: list[dict[str, Any]] = []
    for _, row in elite_df.iterrows():
        edge = _safe_float(row.get("edge"))
        player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name"), default="unknown")
        market = _safe_text(row.get("market_type")) or _safe_text(row.get("market"))
        selection = _safe_text(row.get("selection")).lower()
        line_raw = row.get("sportsbook_line") if row.get("sportsbook_line") is not None else row.get("line_value")
        lookup_key = f"{player.lower()}|{market.lower()}|{selection}|{_line_key(line_raw)}"
        kelly_ctx = kelly_lookup.get(lookup_key, {})
        # context_caution_level / context_pick_alignment: prefer kelly_stakes (has all candidates),
        # fall back to the columns on the elite_board row itself.
        context_caution_level = kelly_ctx.get("context_caution_level") or _safe_text(row.get("context_caution_level"))
        context_pick_alignment = kelly_ctx.get("context_pick_alignment") or _safe_text(row.get("context_pick_alignment"))
        normalized_rows.append(
            {
                "prediction_date": _safe_text(row.get("prediction_date"), default=prediction_date),
                "run_timestamp": timestamp,
                "player_name": player,
                "team": _safe_text(row.get("team")) or _safe_text(row.get("team_abbr")),
                "opponent": _safe_text(row.get("opponent")),
                "game_id": _safe_text(row.get("game_id")),
                "market": market,
                "selection": selection,
                "line": _safe_float(line_raw),
                "projection": _safe_float(row.get("model_projection"), _safe_float(row.get("projection"))),
                "edge": edge,
                "abs_edge": abs(edge),
                "odds": _safe_text(row.get("odds")),
                "confidence": _safe_text(row.get("confidence")),
                "quality_score": _safe_float(row.get("quality_score")),
                "qualification_reason": _safe_text(row.get("qualification_reason")),
                "provider_used": provider_used,
                "result_status": result_status,
                "kelly_eligible": kelly_ctx.get("kelly_eligible", ""),
                "skip_reason": kelly_ctx.get("skip_reason", ""),
                "context_caution_level": context_caution_level,
                "context_pick_alignment": context_pick_alignment,
                "line_source": _safe_text(row.get("line_source")),
            }
        )

    existing = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    incoming = pd.DataFrame(normalized_rows, columns=PICK_HISTORY_COLUMNS)
    combined = incoming.copy() if existing.empty else pd.concat([existing, incoming], ignore_index=True)
    dedupe_keys = ["prediction_date", "player_name", "market", "selection", "line"]
    combined = combined.drop_duplicates(subset=dedupe_keys, keep="last")
    combined = combined[PICK_HISTORY_COLUMNS].sort_values(["prediction_date", "player_name", "market"]).reset_index(drop=True)
    _write_csv(pick_history_path, combined)
    return {
        "picks_output_path": picks_output_path,
        "pick_history_path": pick_history_path,
        "appended_rows": len(incoming),
        "total_rows": len(combined),
    }


def _load_actual_results_for_date(prediction_date: str, runtime_root: Path) -> pd.DataFrame:
    """
    Load real graded outcomes generated from provider-backed stats/games.

    This intentionally does NOT read prior graded runtime CSVs to avoid
    circular grading dependencies.
    """
    try:
        outputs_root = runtime_root.parent if runtime_root.name == "runtime" else runtime_root
        ai = CourtVisionAI(out_dir=str(outputs_root))
        graded_df = ai.auto_grade(prediction_date)
        if graded_df is None or graded_df.empty:
            print(
                f"[history_tracking] WARNING: auto_grade returned no results for {prediction_date}. "
                "Picks will remain pending. Possible causes: provider API unavailable, "
                "game not yet final, or date has no matching game data."
            )
            return pd.DataFrame()
        return graded_df.copy()
    except Exception as exc:
        print(
            f"[history_tracking] WARNING: auto_grade raised {type(exc).__name__}: {exc} "
            f"for {prediction_date}. Picks will remain pending."
        )
        return pd.DataFrame()


def _map_actual_result(row: pd.Series, actual_df: pd.DataFrame) -> str:
    if actual_df.empty:
        return "pending"
    player = _safe_text(row.get("player_name")).lower()
    selection = _safe_text(row.get("selection")).lower()
    market = _safe_text(row.get("market")).lower()
    line = _safe_float(row.get("line"))

    candidates = actual_df.copy()
    if "entity_name" in candidates.columns:
        candidates = candidates[candidates["entity_name"].astype(str).str.lower() == player]
    elif "player_name" in candidates.columns:
        candidates = candidates[candidates["player_name"].astype(str).str.lower() == player]
    if "selection" in candidates.columns:
        candidates = candidates[candidates["selection"].astype(str).str.lower() == selection]
    elif "side" in candidates.columns:
        candidates = candidates[candidates["side"].astype(str).str.lower() == selection]
    if "market_type" in candidates.columns:
        market_candidates = candidates["market_type"].astype(str).str.lower()
        normalized_market = market.replace("player_threes", "player_3pt_made")
        candidates = candidates[market_candidates == normalized_market]
    elif "prop_type" in candidates.columns:
        candidates = candidates[candidates["prop_type"].astype(str).str.lower() == market.replace("player_", "").replace("_made", "").replace("3pt", "threes")]
    if "sportsbook_line" in candidates.columns:
        candidates = candidates[(pd.to_numeric(candidates["sportsbook_line"], errors="coerce") - line).abs() < 1e-6]
    elif "line_value" in candidates.columns:
        candidates = candidates[(candidates["line_value"].astype(float) - line).abs() < 1e-6]
    result_col = "result" if "result" in candidates.columns else ("graded_result" if "graded_result" in candidates.columns else "")
    if candidates.empty or not result_col:
        return "pending"
    result = _safe_text(candidates.iloc[0].get(result_col)).lower()
    if result == "win":
        return "hit"
    if result == "loss":
        return "miss"
    if result == "push":
        return "push"
    return "pending"


def _write_context_cross_slate_summary(history_df: pd.DataFrame, history_root_path: Path) -> None:
    """Write a cross-slate hit-rate summary grouped by context and eligibility dimensions.

    Aggregates across ALL dates in pick_history. Output:
      {history_root}/performance_context_cross_slate.csv
    Columns: dimension, group_value, total, hits, misses, pushes, pending, hit_rate
    """
    df = history_df.copy()
    # Derive bucketed dimensions from numeric columns
    if "edge" in df.columns:
        df["edge_bucket"] = pd.to_numeric(df["edge"], errors="coerce").apply(
            lambda v: _abs_edge_bucket(v) if v == v else "unknown"
        )
    else:
        df["edge_bucket"] = "unknown"
    if "confidence" in df.columns:
        df["confidence_bucket"] = pd.to_numeric(df["confidence"], errors="coerce").apply(_confidence_bucket)
    else:
        df["confidence_bucket"] = "unknown"

    dim_map = {
        "kelly_eligible": "kelly_eligible",
        "skip_reason": "skip_reason",
        "context_caution_level": "context_caution_level",
        "context_pick_alignment": "context_pick_alignment",
        "market": "market",
        "selection": "selection",
        "edge_bucket": "edge_bucket",
        "confidence_bucket": "confidence_bucket",
    }

    rows: list[dict[str, Any]] = []
    for col, display_name in dim_map.items():
        if col not in df.columns:
            continue
        group_col = df[col].fillna("").astype(str).str.strip().replace({"nan": "", "none": "", "None": ""})
        for group_val, seg in df.assign(**{col: group_col}).groupby(col, sort=True):
            if not str(group_val).strip():
                continue
            hits = int((seg["result_status"] == "hit").sum())
            misses = int((seg["result_status"] == "miss").sum())
            pushes = int((seg["result_status"] == "push").sum())
            pending = int((seg["result_status"] == "pending").sum())
            graded_total = hits + misses
            rows.append({
                "dimension": display_name,
                "group_value": group_val,
                "total": int(len(seg)),
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "hit_rate": round(float(hits / graded_total), 4) if graded_total else 0.0,
            })

    _write_csv(
        history_root_path / "performance_context_cross_slate.csv",
        pd.DataFrame(rows, columns=["dimension", "group_value", "total", "hits", "misses", "pushes", "pending", "hit_rate"]),
    )


def update_performance_summaries(
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
) -> None:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    pick_history_path = history_root_path / "pick_history.csv"
    performance_daily_path = history_root_path / "performance_summary.csv"

    history_df = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    if history_df.empty:
        _write_csv(performance_daily_path, pd.DataFrame(columns=[
            "date", "total_picks", "hits", "misses", "pushes", "pending", "hit_rate",
            "overs_count", "overs_hit_rate", "unders_count", "unders_hit_rate",
            "avg_edge", "avg_abs_edge", "max_team_exposure", "max_game_exposure",
        ]))
        per_date_cols = ["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]
        for name in ("performance_by_market.csv", "performance_by_selection.csv", "performance_by_edge_bucket.csv", "performance_by_qualification_reason.csv"):
            _write_csv(history_root_path / name, pd.DataFrame(columns=per_date_cols))
        _write_csv(
            history_root_path / "performance_context_cross_slate.csv",
            pd.DataFrame(columns=["dimension", "group_value", "total", "hits", "misses", "pushes", "pending", "hit_rate"]),
        )
        return

    history_df["prediction_date"] = history_df["prediction_date"].astype(str)
    history_df["selection"] = history_df["selection"].astype(str).str.lower()
    history_df["edge"] = pd.to_numeric(history_df["edge"], errors="coerce").fillna(0.0)
    history_df["abs_edge"] = pd.to_numeric(history_df["abs_edge"], errors="coerce").fillna(history_df["edge"].abs())

    daily_rows: list[dict[str, Any]] = []
    for prediction_date, group in history_df.groupby("prediction_date"):
        hits = int((group["result_status"] == "hit").sum())
        misses = int((group["result_status"] == "miss").sum())
        pushes = int((group["result_status"] == "push").sum())
        pending = int((group["result_status"] == "pending").sum())
        graded_total = hits + misses
        hit_rate = float(hits / graded_total) if graded_total else 0.0

        overs = group[group["selection"] == "over"]
        unders = group[group["selection"] == "under"]
        overs_graded = int((overs["result_status"].isin(["hit", "miss"])).sum())
        unders_graded = int((unders["result_status"].isin(["hit", "miss"])).sum())
        overs_hit_rate = float((overs["result_status"] == "hit").sum() / overs_graded) if overs_graded else 0.0
        unders_hit_rate = float((unders["result_status"] == "hit").sum() / unders_graded) if unders_graded else 0.0

        audit_path = runtime_root_path / "operator" / f"elite_pipeline_audit_summary_{prediction_date}.json"
        max_team_exposure = 0
        max_game_exposure = 0
        if audit_path.exists():
            try:
                payload = json.loads(audit_path.read_text(encoding="utf-8"))
                summary = payload.get("summary") or {}
                ba = summary.get("board_analytics") or {}
                max_team_exposure = int(ba.get("max_team_exposure", summary.get("elite_max_team_exposure", 0)) or 0)
                max_game_exposure = int(ba.get("max_game_exposure", summary.get("elite_max_game_exposure", 0)) or 0)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass

        daily_rows.append(
            {
                "date": prediction_date,
                "total_picks": int(len(group)),
                "hits": hits,
                "misses": misses,
                "pushes": pushes,
                "pending": pending,
                "hit_rate": round(hit_rate, 4),
                "overs_count": int(len(overs)),
                "overs_hit_rate": round(overs_hit_rate, 4),
                "unders_count": int(len(unders)),
                "unders_hit_rate": round(unders_hit_rate, 4),
                "avg_edge": round(float(group["edge"].mean()), 4) if not group.empty else 0.0,
                "avg_abs_edge": round(float(group["abs_edge"].mean()), 4) if not group.empty else 0.0,
                "max_team_exposure": max_team_exposure,
                "max_game_exposure": max_game_exposure,
            }
        )

    daily_df = pd.DataFrame(daily_rows).sort_values("date").reset_index(drop=True)
    _write_csv(performance_daily_path, daily_df)

    def grouped_summary(group_col: str, filename: str) -> None:
        rows: list[dict[str, Any]] = []
        for prediction_date, date_group in history_df.groupby("prediction_date"):
            for group_name, seg in date_group.groupby(group_col):
                hits = int((seg["result_status"] == "hit").sum())
                misses = int((seg["result_status"] == "miss").sum())
                pushes = int((seg["result_status"] == "push").sum())
                pending = int((seg["result_status"] == "pending").sum())
                graded_total = hits + misses
                rows.append(
                    {
                        "date": prediction_date,
                        "group": _safe_text(group_name, default="unknown"),
                        "total": int(len(seg)),
                        "hits": hits,
                        "misses": misses,
                        "pushes": pushes,
                        "pending": pending,
                        "hit_rate": round(float(hits / graded_total), 4) if graded_total else 0.0,
                    }
                )
        _write_csv(history_root_path / filename, pd.DataFrame(rows, columns=["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]))

    history_df["edge_bucket"] = history_df["edge"].apply(_abs_edge_bucket)
    grouped_summary("market", "performance_by_market.csv")
    grouped_summary("selection", "performance_by_selection.csv")
    grouped_summary("edge_bucket", "performance_by_edge_bucket.csv")
    grouped_summary("qualification_reason", "performance_by_qualification_reason.csv")

    _write_context_cross_slate_summary(history_df, history_root_path)


def grade_completed_picks(
    history_root: str | Path = "data/history",
    runtime_root: str | Path = "outputs/runtime",
    prediction_date: str | None = None,
) -> dict[str, Any]:
    history_root_path = Path(history_root)
    runtime_root_path = Path(runtime_root)
    pick_history_path = history_root_path / "pick_history.csv"
    pick_history_df = _load_csv(pick_history_path, columns=PICK_HISTORY_COLUMNS)
    if pick_history_df.empty:
        update_performance_summaries(history_root=history_root_path, runtime_root=runtime_root_path)
        return {"updated_rows": 0, "pending_rows": 0}

    pick_history_df = pick_history_df.reindex(columns=PICK_HISTORY_COLUMNS)
    pending_mask = pick_history_df["result_status"].astype(str).str.lower() == "pending"
    if prediction_date:
        pending_mask &= pick_history_df["prediction_date"].astype(str) == str(prediction_date)
    updated = 0
    for prediction_date in pick_history_df.loc[pending_mask, "prediction_date"].astype(str).unique():
        actual_df = _load_actual_results_for_date(prediction_date, runtime_root=runtime_root_path)
        date_mask = (pick_history_df["prediction_date"].astype(str) == prediction_date) & pending_mask
        date_rows = pick_history_df[date_mask].copy()
        if date_rows.empty:
            continue
        date_rows["result_status"] = date_rows.apply(lambda r: _map_actual_result(r, actual_df), axis=1)
        unsupported_mask = ~date_rows["market"].astype(str).str.lower().isin(
            {"player_points", "player_rebounds", "player_assists", "player_3pt_made", "player_steals", "player_blocks", "moneyline", "team_total"}
        )
        date_rows["grading_note"] = ""
        date_rows.loc[unsupported_mask & date_rows["result_status"].eq("pending"), "grading_note"] = "grading_not_supported_for_market"
        updated += int((date_rows["result_status"] != "pending").sum())
        pick_history_df.loc[date_rows.index, "result_status"] = date_rows["result_status"]

        runtime_graded_path = runtime_root_path / "history" / f"graded_picks_{prediction_date}.csv"
        _write_csv(runtime_graded_path, date_rows)

    _write_csv(pick_history_path, pick_history_df[PICK_HISTORY_COLUMNS])
    update_performance_summaries(history_root=history_root_path, runtime_root=runtime_root_path)
    pending_rows = int((pick_history_df["result_status"].astype(str).str.lower() == "pending").sum())
    return {"updated_rows": updated, "pending_rows": pending_rows}

