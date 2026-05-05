"""CourtVision Streamlit Workstation — design-system styled.

UI integration only. Backend calls (``ai.fit``, ``ai.predict``,
``ai.get_history``, ``ai.get_rejection_history``, ``ai.get_feedback_history``,
``ai.get_run_log``, ``ai.get_calibration_summary``, ``ai.log_results``)
are unchanged. Prediction logic, scoring, Kelly staking, grading and
threshold engines are NOT touched.

Key UI changes vs. the previous build:
* Loads ``dashboard/styles/courtvision_theme.css`` via ``st.markdown`` so the
  dark CourtVision design system is applied to the live Streamlit app.
* New navigation: Today's Board / Slate / History / Calibration / Run Log.
* Featured pick hero, KPI cards, slate row, board tabs, diagnostics,
  daily summary panels.
* Missing-file safety: every disk read is wrapped; missing outputs render
  warning cards instead of crashing.
* Demo mode (``COURTVISION_DEMO=1`` env var) seeds illustrative picks so the
  shell is browsable before any real run.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd
import streamlit as st

# Make the dashboard helpers importable regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from courtvision_ai import CourtVisionAI  # noqa: E402  (engine — untouched)

try:
    from dashboard.styles.streamlit_helpers import (  # noqa: E402
        inject_theme,
        render_brand_block,
        render_sidebar_label,
        render_page_head,
        render_status_pill,
        render_section_head,
        render_featured_pick,
        render_slate,
        render_empty_state,
        render_missing_file_warning,
        safe_pick_featured,
    )
    _THEME_AVAILABLE = True
except Exception as exc:  # pragma: no cover — pure UI fallback
    _THEME_AVAILABLE = False
    _THEME_IMPORT_ERROR = str(exc)


# =====================================================================
# Typed DataFrame helpers — silence Pylance ambiguous-return warnings
# =====================================================================

def column_or_default(df: pd.DataFrame, column: str, default: Any = "") -> pd.Series:
    if column in df.columns:
        return cast(pd.Series, df[column])
    return pd.Series([default] * len(df), index=df.index)


def numeric_column_or_default(
    df: pd.DataFrame,
    column: str,
    default: float = 0.0,
) -> pd.Series:
    series = column_or_default(df, column, default)
    return cast(pd.Series, pd.to_numeric(series, errors="coerce").fillna(default))


APP_TITLE = "CourtVision"
APP_SUBTITLE = "Player props, team totals, moneyline, history, calibration."
DEMO_MODE = os.getenv("COURTVISION_DEMO") == "1"
DISPLAY_BOARD_KEYS = (
    "elite_props",
    "full_market_props",
    "all_stats_props",
    "team_board_props",
    "near_miss_props",
    "sgp_props",
)
RUNTIME_BOARD_FILES = {
    "elite_props": ("operator", "elite_board_{date}.csv", "Elite board"),
    "full_market_props": (
        "operator",
        "full_market_board_{date}.csv",
        "Full market board",
    ),
    "sgp_props": ("operator", "sgp_board_{date}.csv", "SGP board"),
}
RUNTIME_JSON_FILES = {
    "board_diagnostics": (
        "diagnostics",
        "board_diagnostics_{date}.json",
        "Board diagnostics",
    ),
    "market_coverage": (
        "diagnostics",
        "market_coverage_{date}.json",
        "Market coverage",
    ),
}
RUNTIME_TEXT_FILES = {
    "daily_summary_text": ("operator", "daily_summary_{date}.txt", "Daily summary"),
}
RUNTIME_LOG_FILES = {
    "run_today_log": ("logs", "run_today_{date}.log", "Run log"),
    "grading_log": ("logs", "grading_{date}.log", "Grading log"),
}


# =====================================================================
# Page config + theme
# =====================================================================

st.set_page_config(
    page_title="CourtVision",
    page_icon="🏀",
    layout="wide",
    initial_sidebar_state="expanded",
)

if _THEME_AVAILABLE:
    inject_theme()
else:
    st.warning(
        "CourtVision theme helpers unavailable — falling back to default "
        f"Streamlit styling. ({_THEME_IMPORT_ERROR})"
    )


# =====================================================================
# Cached data loaders (UI-side; backend logic unchanged)
# =====================================================================

@st.cache_resource
def get_ai(out_dir: str) -> CourtVisionAI:
    return CourtVisionAI(out_dir=out_dir)


@st.cache_data(show_spinner=False)
def load_history_cached(
    out_dir: str,
    refresh_token: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Wrapper around the existing history APIs, with per-call safety.

    If any individual loader raises (e.g. missing file on disk), that
    DataFrame returns empty and the rest still load.
    """
    _ = refresh_token  # cache-buster
    ai = CourtVisionAI(out_dir=out_dir)

    def _safe(callable_, label):
        try:
            df = callable_()
            return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
        except FileNotFoundError:
            st.session_state.setdefault("missing_files", set()).add(label)
            return pd.DataFrame()
        except Exception:
            st.session_state.setdefault("missing_files", set()).add(label)
            return pd.DataFrame()

    return (
        _safe(ai.get_history, "history"),
        _safe(ai.get_rejection_history, "rejection_history"),
        _safe(ai.get_feedback_history, "feedback_history"),
        _safe(ai.get_run_log, "run_log"),
        _safe(ai.get_calibration_summary, "calibration"),
    )


def resolve_output_dir(out_dir: str) -> Path:
    """Resolve dashboard output paths from the repo root when relative."""
    path = Path(str(out_dir).strip() or "outputs")
    if path.is_absolute():
        return path
    return _REPO_ROOT / path


def normalize_prediction_date_text(value: Any) -> str:
    """Return YYYY-MM-DD for artifact filenames."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return date.today().isoformat()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text


def _runtime_path(runtime_root: Path, folder: str, template: str, date_text: str) -> Path:
    return runtime_root / folder / template.format(date=date_text)


def _file_record(label: str, path: Path, kind: str) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "label": label,
        "kind": kind,
        "path": str(path.resolve()),
        "exists": bool(exists),
        "bytes": int(stat.st_size) if stat else 0,
        "modified": (
            datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            if stat
            else ""
        ),
        "rows": None,
        "columns": None,
        "status": "found" if exists else "missing",
        "error": "",
    }


def _log_runtime_file_record(record: dict[str, Any]) -> None:
    print(
        "[STREAMLIT_RUNTIME_LOAD] "
        f"label={record.get('label')} "
        f"kind={record.get('kind')} "
        f"exists={record.get('exists')} "
        f"status={record.get('status')} "
        f"rows={record.get('rows')} "
        f"path={record.get('path')}",
        flush=True,
    )


def _inspect_runtime_file(label: str, path: Path, kind: str) -> dict[str, Any]:
    record = _file_record(label, path, kind)
    _log_runtime_file_record(record)
    return record


def _read_runtime_csv(label: str, path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    record = _file_record(label, path, "csv")
    if not record["exists"]:
        _log_runtime_file_record(record)
        return pd.DataFrame(), record

    try:
        df = pd.read_csv(path)
        record["rows"] = int(len(df))
        record["columns"] = int(len(df.columns))
        record["status"] = "loaded" if not df.empty else "empty"
    except pd.errors.EmptyDataError:
        df = pd.DataFrame()
        record["rows"] = 0
        record["columns"] = 0
        record["status"] = "empty"
        record["error"] = "CSV has no header or data rows."
    except Exception as exc:
        df = pd.DataFrame()
        record["rows"] = 0
        record["columns"] = 0
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"

    _log_runtime_file_record(record)
    return df, record


def _read_runtime_json(label: str, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = _file_record(label, path, "json")
    if not record["exists"]:
        _log_runtime_file_record(record)
        return {}, record

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        record["status"] = "loaded"
    except Exception as exc:
        payload = {}
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"

    _log_runtime_file_record(record)
    return payload if isinstance(payload, dict) else {}, record


def _read_runtime_text(label: str, path: Path) -> tuple[str, dict[str, Any]]:
    record = _file_record(label, path, "text")
    if not record["exists"]:
        _log_runtime_file_record(record)
        return "", record

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        record["status"] = "loaded" if text.strip() else "empty"
    except Exception as exc:
        text = ""
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"

    _log_runtime_file_record(record)
    return text, record


def _unique_nonblank_count(df: pd.DataFrame, columns: tuple[str, ...]) -> int:
    if df is None or df.empty:
        return 0
    for col in columns:
        if col in df.columns:
            values = cast(pd.Series, df[col]).dropna().astype(str).str.strip()
            values = values[values != ""]
            if not values.empty:
                return int(values.nunique())
    return 0


def _game_count_from_board(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    game_id_count = _unique_nonblank_count(df, ("game_id",))
    if game_id_count:
        return game_id_count
    if {"team", "opponent"}.issubset(df.columns):
        pairs = df[["team", "opponent"]].dropna().astype(str)
        if pairs.empty:
            return 0
        matchup_keys = pairs.apply(
            lambda row: "__".join(
                sorted(
                    [
                        str(row["team"]).strip().upper(),
                        str(row["opponent"]).strip().upper(),
                    ]
                )
            ),
            axis=1,
        )
        matchup_keys = matchup_keys[matchup_keys.str.strip() != "__"]
        return int(matchup_keys.nunique())
    return 0


def _primary_loaded_board(board_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    for key in ("full_market_props", "elite_props", "sgp_props"):
        df = board_data.get(key, pd.DataFrame())
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    return pd.DataFrame()


def _derive_runtime_summary(
    date_text: str,
    out_dir: Path,
    runtime_root: Path,
    board_data: dict[str, pd.DataFrame],
    board_diagnostics: dict[str, Any],
    market_coverage: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    elite_df = board_data.get("elite_props", pd.DataFrame())
    full_market_df = board_data.get("full_market_props", pd.DataFrame())
    sgp_df = board_data.get("sgp_props", pd.DataFrame())
    primary_df = _primary_loaded_board(board_data)
    board_counts = (
        board_diagnostics.get("board_counts", {})
        if isinstance(board_diagnostics, dict)
        else {}
    )

    summary: dict[str, Any] = {
        "prediction_date": date_text,
        "games_analyzed": _game_count_from_board(primary_df),
        "players_evaluated": _unique_nonblank_count(
            primary_df,
            ("player_id", "entity_name", "player_name"),
        ),
        "markets_evaluated": int(len(primary_df)),
        "selected_count": int(len(elite_df)),
        "elite_count": int(len(elite_df)),
        "full_market_count": int(len(full_market_df)),
        "sgp_count": int(len(sgp_df)),
        "rejected_count": int(board_counts.get("rejected", 0) or 0),
        "qualified_pool_count": int(board_counts.get("qualified_pool", 0) or 0),
        "data_status": (
            f"Loaded runtime artifacts for {date_text} from "
            f"{runtime_root.resolve()}"
        ),
        "runtime_output_dir": str(out_dir.resolve()),
        "runtime_operator_dir": str((runtime_root / "operator").resolve()),
        "runtime_diagnostics_dir": str((runtime_root / "diagnostics").resolve()),
        "runtime_logs_dir": str((runtime_root / "logs").resolve()),
        "market_coverage": market_coverage,
        "ui_load_diagnostics": records,
    }
    return summary


@st.cache_data(show_spinner=False)
def load_runtime_prediction_cached(
    out_dir: str,
    prediction_date_text: str,
    refresh_token: int,
) -> dict[str, Any]:
    """Load Today's Board from canonical runtime artifacts.

    This is UI-only binding code: it reads the files already emitted under
    outputs/runtime and never changes prediction, selection, scoring, Kelly,
    grading, or provider behavior.
    """
    _ = refresh_token
    date_text = normalize_prediction_date_text(prediction_date_text)
    out_dir_path = resolve_output_dir(out_dir)
    runtime_root = out_dir_path / "runtime"
    records: list[dict[str, Any]] = []
    board_data: dict[str, pd.DataFrame] = {}

    for payload_key, (folder, template, label) in RUNTIME_BOARD_FILES.items():
        path = _runtime_path(runtime_root, folder, template, date_text)
        df, record = _read_runtime_csv(label, path)
        board_data[payload_key] = df
        records.append(record)

    json_payloads: dict[str, dict[str, Any]] = {}
    for payload_key, (folder, template, label) in RUNTIME_JSON_FILES.items():
        path = _runtime_path(runtime_root, folder, template, date_text)
        payload, record = _read_runtime_json(label, path)
        json_payloads[payload_key] = payload
        records.append(record)

    text_payloads: dict[str, str] = {}
    for payload_key, (folder, template, label) in RUNTIME_TEXT_FILES.items():
        path = _runtime_path(runtime_root, folder, template, date_text)
        text, record = _read_runtime_text(label, path)
        text_payloads[payload_key] = text
        records.append(record)

    for _payload_key, (folder, template, label) in RUNTIME_LOG_FILES.items():
        path = _runtime_path(runtime_root, folder, template, date_text)
        records.append(_inspect_runtime_file(label, path, "log"))

    board_diagnostics = json_payloads.get("board_diagnostics", {})
    market_coverage = json_payloads.get("market_coverage", {})
    summary = _derive_runtime_summary(
        date_text=date_text,
        out_dir=out_dir_path,
        runtime_root=runtime_root,
        board_data=board_data,
        board_diagnostics=board_diagnostics,
        market_coverage=market_coverage,
        records=records,
    )

    return {
        "selected_props": board_data.get("elite_props", pd.DataFrame()),
        "elite_props": board_data.get("elite_props", pd.DataFrame()),
        "qualified_pool_props": board_data.get("full_market_props", pd.DataFrame()),
        "full_market_props": board_data.get("full_market_props", pd.DataFrame()),
        "stat_only_props": pd.DataFrame(),
        "all_stats_props": pd.DataFrame(),
        "team_board_props": pd.DataFrame(),
        "near_miss_props": pd.DataFrame(),
        "sgp_props": board_data.get("sgp_props", pd.DataFrame()),
        "sgp_board": board_data.get("sgp_props", pd.DataFrame()),
        "rejected_props": pd.DataFrame(),
        "board_diagnostics": board_diagnostics,
        "market_coverage": market_coverage,
        "daily_summary_text": text_payloads.get("daily_summary_text", ""),
        "runtime_load_diagnostics": records,
        "summary": summary,
    }


def payload_has_board_rows(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    for key in DISPLAY_BOARD_KEYS:
        df = payload.get(key)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return True
    return False


def payload_has_existing_runtime_artifacts(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    records = payload.get("runtime_load_diagnostics") or (
        (payload.get("summary") or {}).get("ui_load_diagnostics")
    )
    if not isinstance(records, list):
        return False
    return any(bool(record.get("exists")) for record in records if isinstance(record, dict))


def choose_display_payload(
    latest_payload: dict[str, Any] | None,
    runtime_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if payload_has_board_rows(runtime_payload):
        return runtime_payload
    if payload_has_board_rows(latest_payload):
        return latest_payload
    if payload_has_existing_runtime_artifacts(runtime_payload):
        return runtime_payload
    return latest_payload or runtime_payload


# =====================================================================
# Session helpers
# =====================================================================

def init_state() -> None:
    st.session_state.setdefault("history_refresh_token", 0)
    st.session_state.setdefault("latest_prediction", None)
    st.session_state.setdefault("latest_fit_metrics", None)
    st.session_state.setdefault("last_out_dir", "outputs")
    st.session_state.setdefault("last_prediction_date", None)
    st.session_state.setdefault("active_view", "today")
    st.session_state.setdefault("missing_files", set())


def bump_history_refresh() -> None:
    st.session_state["history_refresh_token"] = (
        int(st.session_state.get("history_refresh_token", 0)) + 1
    )


def reset_prediction_if_context_changed(out_dir: str, prediction_date_text: str) -> None:
    previous = str(st.session_state.get("last_out_dir", "outputs"))
    previous_date = st.session_state.get("last_prediction_date")
    date_changed = previous_date is not None and previous_date != prediction_date_text
    if previous != out_dir or date_changed:
        st.session_state["latest_prediction"] = None
        st.session_state["latest_fit_metrics"] = None
        st.session_state["last_out_dir"] = out_dir
        st.session_state["last_prediction_date"] = prediction_date_text
        st.session_state["missing_files"] = set()
    else:
        st.session_state["last_prediction_date"] = prediction_date_text


# =====================================================================
# Pretty mappings
# =====================================================================

PRETTY_MARKETS = {
    "player_points": "Player Points",
    "player_rebounds": "Player Rebounds",
    "player_assists": "Player Assists",
    "player_3pt_made": "Player 3PT Made",
    "player_steals": "Player Steals",
    "player_blocks": "Player Blocks",
    "player_points_rebounds": "Points + Rebounds",
    "player_points_assists": "Points + Assists",
    "player_rebounds_assists": "Rebounds + Assists",
    "player_points_rebounds_assists": "PRA",
    "player_blocks_steals": "Stocks",
    "team_total": "Team Total O/U",
    "team_projection": "Team Projection",
    "game_total_projection": "Game Total Projection",
    "moneyline": "Moneyline",
}


def pretty_market_name(market_type: str) -> str:
    return PRETTY_MARKETS.get(market_type, market_type)


# =====================================================================
# DataFrame styling helpers (pure presentation)
# =====================================================================

def clean_pick_display(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df.copy()
    for col in ["confidence", "edge_abs", "quality_score", "sportsbook_line", "odds"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "quality_score" not in out.columns:
        confidence_series = numeric_column_or_default(out, "confidence")
        edge_abs_series = numeric_column_or_default(out, "edge_abs")
        out["quality_score"] = confidence_series * 100.0 + edge_abs_series * 8.0

    sort_cols = [
        c for c in ["quality_score", "confidence", "edge_abs", "odds"] if c in out.columns
    ]
    if sort_cols:
        out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)

    dedupe_keys = [
        "prediction_date",
        "market_type",
        "entity_name",
        "team",
        "opponent",
        "selection",
        "sportsbook_line",
    ]
    dedupe_keys = [c for c in dedupe_keys if c in out.columns]
    if dedupe_keys:
        out = out.drop_duplicates(subset=dedupe_keys, keep="first").reset_index(drop=True)

    if {"market_type", "team", "opponent"}.issubset(out.columns):
        ml = out[out["market_type"].astype(str) == "moneyline"].copy()
        non_ml = out[out["market_type"].astype(str) != "moneyline"].copy()
        if not ml.empty:
            ml["matchup_key"] = ml.apply(
                lambda row: "__".join(
                    sorted(
                        [
                            str(row.get("team", "")).strip().upper(),
                            str(row.get("opponent", "")).strip().upper(),
                        ]
                    )
                ),
                axis=1,
            )
            ml_sort_cols = [
                c
                for c in ["quality_score", "confidence", "edge_abs", "odds"]
                if c in ml.columns
            ]
            if ml_sort_cols:
                ml = ml.sort_values(by=ml_sort_cols, ascending=False)
            ml = ml.drop_duplicates(
                subset=[c for c in ["prediction_date", "matchup_key"] if c in ml.columns],
                keep="first",
            )
            ml = ml.drop(columns=["matchup_key"], errors="ignore")
        out = (
            pd.concat([non_ml, ml], ignore_index=True)
            if not non_ml.empty or not ml.empty
            else out
        )

    if sort_cols:
        out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
    return out


def style_pick_table(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    view = clean_pick_display(df)
    cols = [
        "letter_grade",
        "recent_form_flag",
        "bet_label",
        "market_type",
        "entity_name",
        "team",
        "opponent",
        "selection",
        "sportsbook_line",
        "model_projection",
        "recent_avg",
        "season_avg",
        "edge",
        "edge_pct",
        "confidence",
        "quality_score",
        "odds",
    ]
    keep = [c for c in cols if c in view.columns]
    view = view[keep]
    if "market_type" in view.columns:
        view["market_type"] = view["market_type"].map(pretty_market_name)
    return view


def style_rejection_table(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    cols = [
        "market_type",
        "entity_name",
        "team",
        "opponent",
        "rejection_reason",
        "sportsbook_line",
        "model_projection",
        "edge",
        "confidence",
    ]
    view = df.copy()
    keep = [c for c in cols if c in view.columns]
    view = view[keep]
    if "market_type" in view.columns:
        view["market_type"] = view["market_type"].map(pretty_market_name)
    return view


def build_top_play_view(df: pd.DataFrame | None, limit: int = 12) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()
    working = clean_pick_display(df)
    for col in ["confidence", "edge_abs", "quality_score"]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    if "quality_score" not in working.columns:
        working["quality_score"] = (
            numeric_column_or_default(working, "confidence") * 100.0
            + numeric_column_or_default(working, "edge_abs") * 8.0
        )
    working = working.sort_values(
        by=["quality_score", "confidence", "edge_abs"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    working["tier"] = "B"
    if "quality_score" in working.columns:
        working.loc[working["quality_score"] >= 82, "tier"] = "A"
        working.loc[working["quality_score"] >= 90, "tier"] = "S"
    cols = [
        "tier",
        "letter_grade",
        "recent_form_flag",
        "market_type",
        "entity_name",
        "team",
        "opponent",
        "selection",
        "sportsbook_line",
        "model_projection",
        "recent_avg",
        "edge",
        "edge_pct",
        "confidence",
        "quality_score",
        "odds",
    ]
    keep = [c for c in cols if c in working.columns]
    view = working[keep].head(limit).copy()
    if "market_type" in view.columns:
        view["market_type"] = view["market_type"].map(pretty_market_name)
    return view


# =====================================================================
# Demo seed (gated by COURTVISION_DEMO=1)
# =====================================================================

def demo_payload() -> dict[str, Any]:
    """Illustrative picks for design review. Off by default."""
    elite = pd.DataFrame(
        [
            {
                "letter_grade": "A+",
                "market_type": "player_points",
                "entity_name": "Tyrese Maxey",
                "team": "PHI",
                "opponent": "IND",
                "selection": "UNDER",
                "sportsbook_line": 29.5,
                "model_projection": 25.16,
                "edge": -4.34,
                "edge_abs": 4.34,
                "confidence": 0.6798,
                "quality_score": 127.04,
                "odds": -115,
            },
            {
                "letter_grade": "A",
                "market_type": "player_points",
                "entity_name": "James Harden",
                "team": "CLE",
                "opponent": "ATL",
                "selection": "UNDER",
                "sportsbook_line": 22.5,
                "model_projection": 17.08,
                "edge": -5.42,
                "edge_abs": 5.42,
                "confidence": 0.7208,
                "quality_score": 124.57,
                "odds": -115,
            },
            {
                "letter_grade": "A-",
                "market_type": "player_points",
                "entity_name": "Toumani Camara",
                "team": "POR",
                "opponent": "LAC",
                "selection": "OVER",
                "sportsbook_line": 13.5,
                "model_projection": 20.08,
                "edge": 6.58,
                "edge_abs": 6.58,
                "confidence": 0.6538,
                "quality_score": 126.83,
                "odds": -114,
            },
        ]
    )
    summary = {
        "games_analyzed": 15,
        "players_evaluated": 554,
        "markets_evaluated": 3913,
        "elite_count": 6,
        "rejected_count": 412,
        "data_status": "Demo mode — data is illustrative.",
    }
    return {
        "selected_props": elite,
        "elite_props": elite,
        "full_market_props": elite,
        "all_stats_props": pd.DataFrame(),
        "team_board_props": pd.DataFrame(),
        "near_miss_props": pd.DataFrame(),
        "rejected_props": pd.DataFrame(),
        "summary": summary,
    }


# =====================================================================
# Render: Today's Board
# =====================================================================

def render_kpi_row(summary: dict[str, Any]) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Games", summary.get("games_analyzed", 0))
    c2.metric("Players", summary.get("players_evaluated", 0))
    c3.metric("Markets", summary.get("markets_evaluated", 0))
    c4.metric("Elite plays", summary.get("elite_count", summary.get("selected_count", 0)))
    c5.metric("Rejected", summary.get("rejected_count", 0))


def render_no_picks_explainer(
    rejected_df: pd.DataFrame | None,
    selected_df: pd.DataFrame | None,
    full_market_df: pd.DataFrame | None,
) -> None:
    if selected_df is not None and not selected_df.empty:
        return
    if full_market_df is not None and not full_market_df.empty:
        return
    st.warning("No selections qualified today.")
    if rejected_df is None or rejected_df.empty:
        render_empty_state(
            "No rejected rows to explain",
            "Usually that means no supported markets returned data.",
        )
        return
    render_section_head("Why no picks were selected", None)
    reason_counts = (
        rejected_df["rejection_reason"]
        .fillna("unknown")
        .value_counts()
        .rename_axis("Reason")
        .reset_index(name="Count")
    )
    st.dataframe(reason_counts, width="stretch", hide_index=True)

    render_section_head("Closest misses", None)
    near = rejected_df.copy()
    if "edge_abs" in near.columns:
        near["edge_abs"] = pd.to_numeric(near["edge_abs"], errors="coerce").fillna(-999)
        near = near.sort_values(by="edge_abs", ascending=False)
    st.dataframe(style_rejection_table(near.head(20)), width="stretch", hide_index=True)


def render_board_section(title: str, df: pd.DataFrame | None, caption: str | None = None) -> None:
    render_section_head(title, caption)
    if df is None or df.empty:
        render_empty_state("No rows in this board")
        return
    view_df = clean_pick_display(df)
    st.dataframe(style_pick_table(view_df), width="stretch", hide_index=True)

    if "market_type" in view_df.columns:
        st.markdown("##### By market type")
        market_order = [
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
            "player_points_rebounds",
            "player_points_assists",
            "player_rebounds_assists",
            "player_points_rebounds_assists",
            "player_blocks_steals",
            "team_total",
            "moneyline",
        ]
        active_markets = [
            m for m in market_order if m in set(view_df["market_type"].astype(str).tolist())
        ]
        if active_markets:
            tabs = st.tabs([pretty_market_name(m) for m in active_markets])
            for tab, market in zip(tabs, active_markets):
                with tab:
                    subset = view_df[view_df["market_type"] == market].copy()
                    st.dataframe(style_pick_table(subset), width="stretch", hide_index=True)


def render_runtime_file_diagnostics(payload: dict[str, Any]) -> None:
    records = payload.get("runtime_load_diagnostics") or (
        (payload.get("summary") or {}).get("ui_load_diagnostics")
    )
    if not isinstance(records, list) or not records:
        return

    with st.expander("Runtime file load", expanded=False):
        diag_df = pd.DataFrame(records)
        keep = [
            "label",
            "kind",
            "exists",
            "status",
            "rows",
            "columns",
            "bytes",
            "modified",
            "path",
            "error",
        ]
        keep = [col for col in keep if col in diag_df.columns]
        st.dataframe(diag_df[keep], width="stretch", hide_index=True)


def render_today_board(payload: dict[str, Any] | None = None) -> None:
    payload = payload or st.session_state.get("display_prediction")
    if not payload and DEMO_MODE:
        payload = demo_payload()

    if not payload:
        render_empty_state(
            "Run predictions to see the workstation",
            "Use the sidebar to pick a date and hit RUN PREDICTIONS.",
        )
        return

    selected_df = payload.get("selected_props", pd.DataFrame())
    elite_df = payload.get("elite_props", selected_df)
    full_market_df = payload.get("full_market_props", pd.DataFrame())
    sgp_df = payload.get("sgp_props", payload.get("sgp_board", pd.DataFrame()))
    stat_only_df = payload.get(
        "all_stats_props", payload.get("stat_only_props", pd.DataFrame())
    )
    team_board_df = payload.get("team_board_props", pd.DataFrame())
    near_miss_df = payload.get("near_miss_props", pd.DataFrame())
    rejected_df = payload.get("rejected_props", pd.DataFrame())
    summary = payload.get("summary", {}) or {}

    # Featured pick
    featured = safe_pick_featured(elite_df)
    render_featured_pick(featured)

    # KPI row
    render_kpi_row(summary)

    data_status = summary.get("data_status")
    if data_status:
        st.info(data_status)

    # No-picks explainer
    render_no_picks_explainer(rejected_df, elite_df, full_market_df)

    # Board tabs
    render_section_head(
        "Today's picks",
        "Hard-filtered boards. Top of the slate, ranked.",
    )
    tab_names = [
        "Elite",
        "Full Market",
        "SGP",
        "All Stats",
        "Team Board",
        "Near Miss",
    ]
    elite_tab, full_tab, sgp_tab, stat_tab, team_tab, near_tab = st.tabs(tab_names)
    with elite_tab:
        render_board_section(
            "Elite Board",
            elite_df,
            "Hard-filtered board for strongest market-backed plays.",
        )
    with full_tab:
        render_board_section(
            "Full Market Board",
            full_market_df,
            "Top picks per market with real live lines.",
        )
    with sgp_tab:
        render_board_section(
            "SGP Board",
            sgp_df,
            "Same-game parlay candidates when emitted by the runtime.",
        )
    with stat_tab:
        render_board_section(
            "All Stats Projection Board",
            stat_only_df,
            "Points, rebounds, assists, 3PT, steals, blocks.",
        )
    with team_tab:
        render_board_section(
            "Team Board",
            team_board_df,
            "Team projections, totals, game totals, moneyline.",
        )
    with near_tab:
        render_board_section(
            "Near Miss Board",
            near_miss_df,
            "Rejected plays closest to qualification thresholds.",
        )

    # Diagnostics
    render_section_head("Diagnostics", "Provider, ingestion and model context.")
    render_runtime_file_diagnostics(payload)
    odds_diag = summary.get("odds_diagnostics", {}) or {}
    model_diag = summary.get("model_diagnostics", {}) or {}
    board_diag = payload.get("board_diagnostics", {}) or summary.get("board_diagnostics", {}) or {}
    market_coverage = payload.get("market_coverage", {}) or summary.get("market_coverage", {}) or {}
    daily_summary_text = payload.get("daily_summary_text", "")
    if not odds_diag and not model_diag and not board_diag and not market_coverage and not daily_summary_text:
        render_empty_state("No diagnostics emitted by the latest run")
    else:
        if odds_diag:
            with st.expander("Odds ingestion", expanded=False):
                st.json(odds_diag)
        if model_diag:
            with st.expander("Model context", expanded=False):
                st.json(model_diag)
        if board_diag:
            with st.expander("Board diagnostics", expanded=False):
                st.json(board_diag)
        if market_coverage:
            with st.expander("Market coverage", expanded=False):
                st.json(market_coverage)
        if daily_summary_text:
            with st.expander("Daily summary", expanded=False):
                st.text(daily_summary_text)
        with st.expander("Full rejection table", expanded=False):
            if rejected_df is None or rejected_df.empty:
                render_empty_state("No rejection rows")
            else:
                st.dataframe(
                    style_rejection_table(rejected_df),
                    width="stretch",
                    hide_index=True,
                )
        with st.expander("Run summary JSON", expanded=False):
            st.json(summary)


# =====================================================================
# Render: Slate (game schedule cards)
# =====================================================================

def games_from_payload(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Derive the slate from whatever picks loaded — falls back to empty."""
    if not payload:
        return []
    sources = [
        payload.get("elite_props"),
        payload.get("full_market_props"),
        payload.get("all_stats_props"),
    ]
    slices: list[pd.DataFrame] = []
    for df in sources:
        if isinstance(df, pd.DataFrame) and {"team", "opponent"}.issubset(df.columns):
            slices.append(df[["team", "opponent"]])
    if not slices:
        return []

    merged = pd.concat(slices, ignore_index=True).dropna()
    merged["matchup_key"] = merged.apply(
        lambda r: "__".join(
            sorted([str(r["team"]).upper().strip(), str(r["opponent"]).upper().strip()])
        ),
        axis=1,
    )
    counts = merged.groupby("matchup_key").size().reset_index(name="picks")
    first = merged.drop_duplicates(subset=["matchup_key"], keep="first")
    games_df = first.merge(counts, on="matchup_key", how="left")
    return [
        {
            "home": row["team"],
            "away": row["opponent"],
            "separator": "vs",
            "tipoff": "—",
            "picks": int(row["picks"]),
        }
        for _, row in games_df.iterrows()
    ]


def render_slate_view(payload: dict[str, Any] | None = None) -> None:
    payload = payload or st.session_state.get("display_prediction") or (
        demo_payload() if DEMO_MODE else None
    )
    render_section_head(
        "Tonight's slate",
        "Games on the board for the selected date.",
    )
    games = games_from_payload(payload)
    if not games:
        render_empty_state(
            "No slate yet",
            "Run predictions for a date with scheduled games.",
        )
        return
    render_slate(games)


# =====================================================================
# Render: History
# =====================================================================

def render_history_view(out_dir: str) -> None:
    history_df, rejection_history_df, feedback_df, run_log_df, calibration_df = load_history_cached(
        out_dir,
        int(st.session_state.get("history_refresh_token", 0)),
    )

    missing = st.session_state.get("missing_files", set()) or set()
    if missing:
        for label in sorted(missing):
            render_missing_file_warning(
                f"{out_dir}/{label}.*",
                "Will populate after the next prediction or feedback upload.",
            )

    tab1, tab2, tab3 = st.tabs(["Prediction History", "Rejection History", "Feedback"])

    with tab1:
        render_section_head("Prediction history", "Last 500 picks.")
        if history_df.empty:
            render_empty_state("No prediction history saved yet")
        else:
            st.dataframe(
                style_pick_table(
                    clean_pick_display(history_df.tail(500).sort_index(ascending=False))
                ),
                width="stretch",
                hide_index=True,
            )
            render_history_summary(history_df)

    with tab2:
        render_section_head("Rejection history", "Reasons rolled up across runs.")
        if rejection_history_df.empty:
            render_empty_state("No rejection history saved yet")
        else:
            st.dataframe(
                style_rejection_table(
                    rejection_history_df.tail(500).sort_index(ascending=False)
                ),
                width="stretch",
                hide_index=True,
            )
            if "rejection_reason" in rejection_history_df.columns:
                counts = (
                    rejection_history_df["rejection_reason"]
                    .fillna("unknown")
                    .value_counts()
                    .rename_axis("Reason")
                    .reset_index(name="Count")
                )
                st.dataframe(counts, width="stretch", hide_index=True)

    with tab3:
        render_section_head("Result feedback", "Learning memory.")
        if feedback_df.empty:
            render_empty_state("No feedback rows logged yet")
        else:
            st.dataframe(
                feedback_df.tail(500).sort_index(ascending=False),
                width="stretch",
                hide_index=True,
            )
            if {"market_type", "hit"}.issubset(feedback_df.columns):
                hit_rate_series = feedback_df.groupby("market_type")["hit"].mean()
                hit_rate = pd.DataFrame(
                    {
                        "market_type": [str(x) for x in hit_rate_series.index],
                        "hit_rate": hit_rate_series.to_numpy(),
                    }
                )
                hit_rate["market_type"] = hit_rate["market_type"].map(pretty_market_name)
                hit_rate["hit_rate"] = (
                    pd.to_numeric(hit_rate["hit_rate"], errors="coerce") * 100
                ).round(2)
                st.dataframe(hit_rate, width="stretch", hide_index=True)
            if {"hit", "model_projection", "actual_value"}.issubset(feedback_df.columns):
                overall_hit_rate = numeric_column_or_default(feedback_df, "hit").mean()
                mae = (
                    numeric_column_or_default(feedback_df, "model_projection")
                    - numeric_column_or_default(feedback_df, "actual_value")
                ).abs().mean()
                overall_hit_rate_value = (
                    0.0 if pd.isna(overall_hit_rate) else float(overall_hit_rate)
                )
                mae_value = 0.0 if pd.isna(mae) else float(mae)
                c1, c2 = st.columns(2)
                c1.metric("Overall hit rate", f"{overall_hit_rate_value * 100:.2f}%")
                c2.metric("Overall MAE", f"{mae_value:.2f}")


def render_history_summary(history_df: pd.DataFrame | None) -> None:
    if history_df is None or history_df.empty:
        return
    if "market_type" not in history_df.columns:
        return
    render_section_head("Market performance snapshot", None)
    grp = history_df.groupby("market_type", as_index=False).agg(
        picks=("market_type", "count"),
        avg_confidence=("confidence", "mean"),
        avg_edge=("edge_abs", "mean"),
    )
    grp["market_type"] = grp["market_type"].map(pretty_market_name)
    grp["avg_confidence"] = numeric_column_or_default(grp, "avg_confidence").round(3)
    grp["avg_edge"] = numeric_column_or_default(grp, "avg_edge").round(3)
    st.dataframe(grp, width="stretch", hide_index=True)


# =====================================================================
# Render: Calibration
# =====================================================================

def render_calibration_view(out_dir: str) -> None:
    _, _, feedback_df, _, calibration_df = load_history_cached(
        out_dir,
        int(st.session_state.get("history_refresh_token", 0)),
    )
    render_section_head(
        "Calibration",
        "Per-market hit rate and projection accuracy memory.",
    )
    if calibration_df is None or calibration_df.empty:
        render_empty_state(
            "No calibration data yet",
            "Calibration is built from the Feedback uploader once results are logged.",
        )
    else:
        view = calibration_df.copy()
        if "market_type" in view.columns:
            view["market_type"] = view["market_type"].map(pretty_market_name)
        if "hit_rate" in view.columns:
            view["hit_rate"] = (numeric_column_or_default(view, "hit_rate") * 100).round(2)
        st.dataframe(view, width="stretch", hide_index=True)


# =====================================================================
# Render: Run Log
# =====================================================================

def render_run_log_view(out_dir: str) -> None:
    _, _, _, run_log_df, _ = load_history_cached(
        out_dir,
        int(st.session_state.get("history_refresh_token", 0)),
    )
    render_section_head("Run log", "Most recent predictions and fits.")
    if run_log_df is None or run_log_df.empty:
        render_empty_state("No run log yet")
        return
    st.dataframe(
        run_log_df.tail(300).sort_index(ascending=False),
        width="stretch",
        hide_index=True,
    )


# =====================================================================
# Feedback uploader
# =====================================================================

def feedback_upload_block(ai: CourtVisionAI) -> None:
    render_section_head(
        "Log results",
        "Upload a CSV of finals to update calibration memory.",
    )
    st.caption(
        "Columns: prediction_date, market_type, entity_name, team, opponent, "
        "selection, sportsbook_line, model_projection, actual_value, hit"
    )
    uploaded = st.file_uploader("Upload results CSV", type=["csv"], key="feedback_csv")
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            st.dataframe(df.head(20), width="stretch", hide_index=True)
            if st.button("Save feedback and update calibration", type="primary"):
                ai.log_results(df)
                bump_history_refresh()
                st.success("Feedback saved. Calibration memory updated.")
        except Exception as exc:
            st.error(f"Could not read feedback CSV: {exc}")


# =====================================================================
# Main
# =====================================================================

def main() -> None:
    init_state()

    today = date.today()
    default_out_dir = "outputs"
    default_train_end = today - timedelta(days=1)
    default_train_start = default_train_end - timedelta(days=180)

    # ---------------- Sidebar ----------------
    with st.sidebar:
        if _THEME_AVAILABLE:
            render_brand_block(APP_TITLE)
        else:
            st.title(APP_TITLE)

        st.markdown("---")
        # Navigation radio styled as nav list
        view = st.radio(
            "Navigation",
            options=[
                ("today", "Today's Board"),
                ("slate", "Slate"),
                ("history", "History"),
                ("calibration", "Calibration"),
                ("run_log", "Run Log"),
                ("feedback", "Feedback"),
            ],
            format_func=lambda x: x[1],
            label_visibility="collapsed",
            key="nav_radio",
        )
        active_key = view[0] if isinstance(view, tuple) else "today"
        st.session_state["active_view"] = active_key

        st.markdown("---")
        if _THEME_AVAILABLE:
            render_sidebar_label("Run config")
        out_dir = st.text_input("Output folder", value=default_out_dir)
        prediction_date = st.date_input("Prediction date", value=today)
        prediction_date_text = normalize_prediction_date_text(prediction_date)

        with st.expander("Training window", expanded=False):
            train_start = st.date_input("Training start", value=default_train_start)
            train_end = st.date_input("Training end", value=default_train_end)

        st.markdown("---")
        fit_clicked = st.button("Fit / Refresh Model", width="stretch")
        predict_clicked = st.button(
            "Run Predictions", type="primary", width="stretch"
        )
        reload_history_clicked = st.button("Reload History", width="stretch")

        st.markdown("---")
        if _THEME_AVAILABLE:
            render_sidebar_label("Markets targeted")
        st.markdown(
            """
            <div style="font-family:var(--font-body, sans-serif);
                        font-size:12px; color:var(--ink-6, #8A95A6);
                        line-height:1.7;">
              Player Points · Rebounds · Assists<br/>
              3PT Made · Steals · Blocks<br/>
              Points + Rebounds · Points + Assists<br/>
              Rebounds + Assists · PRA · Stocks<br/>
              Team Total O/U · Moneyline
            </div>
            """,
            unsafe_allow_html=True,
        )

    resolved_out_dir = resolve_output_dir(out_dir)
    resolved_out_dir_text = str(resolved_out_dir)
    reset_prediction_if_context_changed(resolved_out_dir_text, prediction_date_text)

    # Build engine handle. If construction fails, surface a friendly card.
    try:
        ai = get_ai(resolved_out_dir_text)
    except Exception as exc:
        st.error(f"Could not initialise CourtVision engine: {exc}")
        st.code(traceback.format_exc())
        return

    if reload_history_clicked:
        bump_history_refresh()

    if fit_clicked:
        with st.spinner("Training model baselines..."):
            try:
                metrics = ai.fit(train_start.isoformat(), train_end.isoformat())
                st.session_state["latest_fit_metrics"] = metrics
                bump_history_refresh()
                st.success("Model fit complete.")
                st.json(metrics)
            except Exception as exc:
                st.error(f"Fit failed: {exc}")
                st.code(traceback.format_exc())

    if predict_clicked:
        with st.spinner("Scoring markets..."):
            try:
                outputs = ai.predict(prediction_date_text)
                st.session_state["latest_prediction"] = outputs
                bump_history_refresh()
            except Exception as exc:
                st.error(f"Prediction run failed: {exc}")
                st.code(traceback.format_exc())

    runtime_payload = load_runtime_prediction_cached(
        resolved_out_dir_text,
        prediction_date_text,
        int(st.session_state.get("history_refresh_token", 0)),
    )
    display_payload = choose_display_payload(
        st.session_state.get("latest_prediction"),
        runtime_payload,
    )
    st.session_state["display_prediction"] = display_payload

    # ---------------- Header ----------------
    payload = display_payload
    has_data = payload_has_board_rows(payload) or payload_has_existing_runtime_artifacts(payload)
    pill = (
        ("Live · Last run loaded", "live")
        if has_data
        else ("Idle · No run yet", "idle")
    )
    if DEMO_MODE and not has_data:
        pill = ("Demo data", "stale")
    titles = {
        "today": ("Today's Board", APP_SUBTITLE),
        "slate": ("Slate", "Tonight's games on the board."),
        "history": ("History", "Prediction, rejection and feedback history."),
        "calibration": ("Calibration", "Hit-rate memory across markets."),
        "run_log": ("Run Log", "Most recent predictions and fits."),
        "feedback": ("Feedback", "Log finals to update calibration."),
    }
    title, sub = titles.get(active_key, (APP_TITLE, APP_SUBTITLE))
    if _THEME_AVAILABLE:
        render_page_head(title, sub, pill=pill)
    else:
        st.title(title)
        st.caption(sub)

    # ---------------- Views ----------------
    if active_key == "today":
        render_today_board(payload)
    elif active_key == "slate":
        render_slate_view(payload)
    elif active_key == "history":
        render_history_view(resolved_out_dir_text)
    elif active_key == "calibration":
        render_calibration_view(resolved_out_dir_text)
    elif active_key == "run_log":
        render_run_log_view(resolved_out_dir_text)
    elif active_key == "feedback":
        feedback_upload_block(ai)


if __name__ == "__main__":
    main()
