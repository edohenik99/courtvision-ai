"""CourtVision Streamlit Workstation — design-system styled.

UI integration only. Backend calls (``ai.fit``, ``ai.predict``,
``ai.get_history``, ``ai.get_rejection_history``, ``ai.get_feedback_history``,
``ai.get_run_log``, ``ai.get_calibration_summary``, ``ai.log_results``)
are unchanged. Prediction logic, scoring, Kelly staking, grading and
threshold engines are NOT touched.

Key UI changes vs. the previous build:
* Loads ``dashboard/styles/courtvision_theme.css`` via ``st.markdown`` so the
  dark CourtVision design system is applied to the live Streamlit app.
* New navigation: Today's Board / Slate / Review Layers / History /
  Calibration / Run Log.
* Featured pick hero, KPI cards, slate row, board tabs, diagnostics,
  daily summary panels.
* Missing-file safety: every disk read is wrapped; missing outputs render
  warning cards instead of crashing.
* Demo mode (``COURTVISION_DEMO=1`` env var) seeds illustrative picks so the
  shell is browsable before any real run.
"""

from __future__ import annotations

import json
import html
import os
import re
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
from courtvision.streamlit_review_artifacts import (  # noqa: E402
    PHASE15_READINESS_LABELS,
    extract_quality_review_statuses,
    load_quality_review_artifacts,
)

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
        render_kpi_cards,
        safe_pick_featured,
        render_review_banner,
        render_status_strip,
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


@st.cache_data(show_spinner=False)
def load_quality_review_artifacts_cached(
    out_dir: str,
    prediction_date_text: str,
    refresh_token: int,
) -> dict[str, Any]:
    """Load UI-only Quality Summary and Phase 15 review artifacts."""
    _ = refresh_token
    return load_quality_review_artifacts(
        out_dir,
        prediction_date_text,
        repo_root=_REPO_ROOT,
    )


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


DISPLAY_COLUMN_LABELS = {
    "letter_grade": "Grade",
    "recent_form_flag": "Form",
    "bet_label": "Bet",
    "recommended_action": "Action",
    "market_type": "Market",
    "entity_name": "Player",
    "player_name": "Player",
    "team": "Team",
    "opponent": "Opp",
    "selection": "Side",
    "pick_side": "Side",
    "side": "Side",
    "selection_side": "Side",
    "sportsbook_line": "Line",
    "model_projection": "Projection",
    "recent_avg": "Recent Avg",
    "season_avg": "Season Avg",
    "edge": "Edge",
    "edge_abs": "Edge Abs",
    "edge_pct": "Edge %",
    "confidence": "Confidence",
    "quality_score": "Quality",
    "odds": "Odds",
    "context_caution_level": "Caution",
    "context_pick_alignment": "Context",
    "line_source": "Line Source",
    "source_lane": "Source Lane",
    "is_live_market": "Live",
    "kelly_eligible": "Kelly",
    "manual_review_required": "Manual Review",
    "rejection_reason": "Rejection Reason",
    "minutes_bucket": "Minutes Bucket",
    "minutes_basis": "Minutes Basis",
    "projected_minutes": "Projected Minutes",
    "result_status": "Result",
    "readiness_verdict": "Readiness Verdict",
    "bucket": "Bucket",
    "policy_name": "Policy",
    "risk_verdict": "Risk Verdict",
    "sample_status": "Sample Status",
}

PICK_COLUMN_ORDER = [
    "Grade",
    "Form",
    "Bet",
    "Action",
    "Player",
    "Market",
    "Team",
    "Opp",
    "Side",
    "Line",
    "Projection",
    "Recent Avg",
    "Season Avg",
    "Edge",
    "Edge %",
    "Confidence",
    "Quality",
    "Odds",
    "Caution",
    "Context",
    "Line Source",
    "Live",
    "Kelly",
    "Manual Review",
]

REVIEW_DISPLAY_COLUMN_ORDER = [
    "Player",
    "Team",
    "Opp",
    "Market",
    "Side",
    "Line",
    "Minutes Bucket",
    "Minutes Basis",
    "Result",
    "Confidence",
    "Quality",
    "Edge",
    "Caution",
    "Context",
    "Readiness Verdict",
]


# =====================================================================
# DataFrame styling helpers (pure presentation)
# =====================================================================

def _format_number_for_display(value: Any, decimals: int = 1) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if pd.isna(number):
        return ""
    if float(number).is_integer() and decimals == 0:
        return str(int(number))
    return f"{number:.{decimals}f}"


def _format_percent_for_display(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if pd.isna(number):
        return ""
    if abs(number) <= 1:
        number *= 100
    return f"{number:.1f}%"


def _normalize_text_for_display(value: Any, title: bool = False, upper: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    text = text.replace("_", " ")
    if upper:
        return text.upper()
    if title:
        return text.title()
    return text


def _format_display_columns(
    df: pd.DataFrame,
    preferred_order: list[str] | None = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    view = df.copy()
    if "market_type" in view.columns:
        view["market_type"] = view["market_type"].astype(str).map(pretty_market_name)
    for col in ("selection", "selection_side", "pick_side", "side"):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _normalize_text_for_display(x, upper=True))
    for col in (
        "context_caution_level",
        "context_pick_alignment",
        "line_source",
        "source_lane",
        "recommended_action",
        "bet_label",
    ):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _normalize_text_for_display(x, title=True))
    for col in ("sportsbook_line", "line", "model_projection", "projection", "recent_avg", "season_avg"):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _format_number_for_display(x, decimals=1))
    for col in ("edge", "edge_abs", "side_edge", "minutes_basis", "projected_minutes"):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _format_number_for_display(x, decimals=2))
    for col in ("quality_score", "quality"):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _format_number_for_display(x, decimals=1))
    for col in ("odds",):
        if col in view.columns:
            view[col] = view[col].map(lambda x: _format_number_for_display(x, decimals=0))
    for col in ("confidence", "edge_pct", "hit_rate", "roi"):
        if col in view.columns:
            view[col] = view[col].map(_format_percent_for_display)

    view = view.rename(columns=DISPLAY_COLUMN_LABELS)
    if preferred_order:
        ordered = [col for col in preferred_order if col in view.columns]
        rest = [col for col in view.columns if col not in ordered]
        view = view[ordered + rest]
    return view


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
        "recommended_action",
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
        "context_caution_level",
        "context_pick_alignment",
        "line_source",
        "is_live_market",
        "kelly_eligible",
        "manual_review_required",
    ]
    keep = [c for c in cols if c in view.columns]
    view = view[keep]
    return _format_display_columns(view, PICK_COLUMN_ORDER)


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
    return _format_display_columns(
        view,
        [
            "Market",
            "Player",
            "Team",
            "Opp",
            "Rejection Reason",
            "Line",
            "Projection",
            "Edge",
            "Confidence",
        ],
    )


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
    return _format_display_columns(view, PICK_COLUMN_ORDER)


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

def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _status_state(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("healthy", "ready", "live", "clean", "success")):
        return "success"
    if any(token in text for token in ("degraded", "review", "warning", "fallback", "pending", "caution")):
        return "warning"
    if any(token in text for token in ("error", "failed", "blocked", "not_ready", "not ready")):
        return "danger"
    if "simulation" in text:
        return "info"
    return "neutral"


def _extract_pending_grading_count(daily_summary_text: str) -> int | str:
    if not daily_summary_text:
        return "not_available"
    match = re.search(r"Pending grading count:\s*(\d+)", daily_summary_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"pending picks:\s*(\d+)", daily_summary_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return "not_available"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _operator_decision(quality_json: dict[str, Any], summary: dict[str, Any]) -> str:
    recommendation = str(
        quality_json.get("run_health_recommendation")
        or _nested_get(quality_json, "run_health", "recommendation")
        or ""
    )
    status = str(quality_json.get("run_health_status") or _nested_get(quality_json, "run_health", "status") or "")
    elite_count = _safe_int(summary.get("elite_count", summary.get("selected_count", 0)))
    if "bet-ready" in recommendation.lower() or "bet ready" in recommendation.lower():
        return "Bet-ready"
    if "no bet" in recommendation.lower() or "no_bet" in status.lower():
        return "No-bet"
    if elite_count > 0:
        return "Board qualified"
    return "No elite pick"


def render_operator_status_strip(
    summary: dict[str, Any],
    quality_json: dict[str, Any],
    prediction_date_text: str,
    daily_summary_text: str,
) -> None:
    run_health = (
        quality_json.get("run_health_status")
        or _nested_get(quality_json, "run_health", "status")
        or "runtime_loaded"
    )
    provider_counts = quality_json.get("slate_provider_counts") or {}
    live_count = int(provider_counts.get("live_odds_count", 0) or 0)
    fallback_count = int(provider_counts.get("synthetic_or_fallback_odds_count", 0) or 0)
    odds_value = f"Live {live_count} / fallback {fallback_count}"
    odds_state = "success" if live_count and fallback_count == 0 else ("warning" if fallback_count else "neutral")
    kelly_eligible = (
        _nested_get(quality_json, "kelly_safety_summary", "kelly_eligible_count")
        or _nested_get(quality_json, "candidate_funnel", "kelly_rows_count")
        or 0
    )
    manual_review_count = (
        _nested_get(quality_json, "manual_review_summary", "manual_review_required_count")
        or quality_json.get("manual_review_required_count")
        or 0
    )
    pending_grading = _extract_pending_grading_count(daily_summary_text)
    decision = _operator_decision(quality_json, summary)

    render_status_strip(
        [
            {
                "label": "Prediction date",
                "value": prediction_date_text,
                "state": "info",
                "caption": "Selected runtime date",
            },
            {
                "label": "Run health",
                "value": run_health,
                "state": _status_state(run_health),
                "caption": "Quality Summary",
            },
            {
                "label": "Final decision",
                "value": decision,
                "state": _status_state(decision),
                "caption": "Operator readout",
            },
            {
                "label": "Odds status",
                "value": odds_value,
                "state": odds_state,
                "caption": "Live vs fallback",
            },
            {
                "label": "Kelly eligible",
                "value": kelly_eligible,
                "state": "success" if _safe_int(kelly_eligible) > 0 else "neutral",
                "caption": "Rows",
            },
            {
                "label": "Manual review",
                "value": manual_review_count,
                "state": "warning" if _safe_int(manual_review_count) else "success",
                "caption": "Diagnostic flags",
            },
            {
                "label": "Pending grading",
                "value": pending_grading,
                "state": "warning" if str(pending_grading) not in {"0", "not_available"} else "success",
                "caption": "Daily Summary",
            },
        ]
    )


def render_kpi_row(summary: dict[str, Any], quality_json: dict[str, Any] | None = None) -> None:
    quality_json = quality_json or {}
    candidate_funnel = quality_json.get("candidate_funnel") or {}
    kelly_summary = quality_json.get("kelly_safety_summary") or {}
    render_kpi_cards(
        [
            {"label": "Games", "value": summary.get("games_analyzed", 0), "caption": "on slate"},
            {"label": "Players", "value": summary.get("players_evaluated", 0), "caption": "evaluated"},
            {"label": "Markets", "value": summary.get("markets_evaluated", 0), "caption": "priced rows"},
            {
                "label": "Elite picks",
                "value": candidate_funnel.get("elite_board_count", summary.get("elite_count", summary.get("selected_count", 0))),
                "caption": "final board",
                "state": "success",
            },
            {
                "label": "Full market",
                "value": candidate_funnel.get("full_market_board_count", summary.get("full_market_count", 0)),
                "caption": "operator rows",
            },
            {
                "label": "Kelly rows",
                "value": kelly_summary.get("total_rows", candidate_funnel.get("kelly_rows_count", 0)),
                "caption": "sizing output",
            },
            {"label": "Rejected", "value": summary.get("rejected_count", 0), "caption": "filtered out"},
        ]
    )


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


def _unique_filter_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns:
        return []
    values = (
        df[column]
        .dropna()
        .astype(str)
        .map(str.strip)
    )
    values = values[values != ""]
    return sorted(values.unique().tolist())


def _apply_multiselect_filter(
    df: pd.DataFrame,
    column: str,
    label: str,
    key: str,
    format_func: Any | None = None,
) -> pd.DataFrame:
    options = _unique_filter_values(df, column)
    if not options:
        return df
    selected = st.multiselect(
        label,
        options=options,
        default=[],
        format_func=format_func or (lambda x: x),
        key=key,
        placeholder="All",
    )
    if selected:
        return df[df[column].astype(str).isin(selected)].copy()
    return df


def render_board_filters(df: pd.DataFrame, key_prefix: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    with st.container(border=True):
        st.caption("Compact filters")
        filter_cols = st.columns(4)
        with filter_cols[0]:
            df = _apply_multiselect_filter(
                df,
                "market_type",
                "Market",
                f"{key_prefix}_market_type",
                pretty_market_name,
            )
        with filter_cols[1]:
            side_col = next((col for col in ("selection", "selection_side", "side") if col in df.columns), "")
            if side_col:
                df = _apply_multiselect_filter(
                    df,
                    side_col,
                    "Side",
                    f"{key_prefix}_side",
                    lambda x: str(x).upper(),
                )
        with filter_cols[2]:
            if "team" in df.columns:
                df = _apply_multiselect_filter(df, "team", "Team", f"{key_prefix}_team")
        with filter_cols[3]:
            if "context_caution_level" in df.columns:
                df = _apply_multiselect_filter(
                    df,
                    "context_caution_level",
                    "Caution",
                    f"{key_prefix}_caution",
                    lambda x: str(x).replace("_", " ").title(),
                )
        st.caption(f"{len(df)} rows visible")
    return df


def render_board_section(
    title: str,
    df: pd.DataFrame | None,
    caption: str | None = None,
    enable_filters: bool = False,
    key_prefix: str | None = None,
) -> None:
    render_section_head(title, caption)
    if df is None or df.empty:
        render_empty_state("No rows in this board")
        return
    view_df = clean_pick_display(df)
    if enable_filters:
        view_df = render_board_filters(view_df, key_prefix or title.lower().replace(" ", "_"))
        if view_df.empty:
            render_empty_state("No rows match the current filters")
            return
    st.dataframe(style_pick_table(view_df), width="stretch", hide_index=True, height=420)

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
                    st.dataframe(
                        style_pick_table(subset),
                        width="stretch",
                        hide_index=True,
                        height=360,
                    )


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


def render_today_board(
    payload: dict[str, Any] | None = None,
    out_dir: str | None = None,
    prediction_date_text: str | None = None,
) -> None:
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
    daily_summary_text = payload.get("daily_summary_text", "")
    quality_json: dict[str, Any] = {}
    if out_dir and prediction_date_text:
        review_payload = load_quality_review_artifacts_cached(
            out_dir,
            prediction_date_text,
            int(st.session_state.get("history_refresh_token", 0)),
        )
        quality_json = review_payload.get("quality_summary_json") or {}

    render_operator_status_strip(
        summary,
        quality_json,
        prediction_date_text or str(summary.get("prediction_date") or ""),
        daily_summary_text,
    )

    # Featured pick
    featured = safe_pick_featured(elite_df)
    render_featured_pick(featured)

    # KPI row
    render_kpi_row(summary, quality_json)

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
            enable_filters=True,
            key_prefix="full_market_board",
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
    if not odds_diag and not model_diag and not board_diag and not market_coverage and not daily_summary_text:
        render_empty_state("No diagnostics emitted by the latest run")
    else:
        if odds_diag:
            with st.expander("Odds ingestion - provider status", expanded=False):
                st.caption("Provider and odds-source diagnostics emitted by the runtime.")
                st.json(odds_diag)
        if model_diag:
            with st.expander("Model context - scoring inputs", expanded=False):
                st.caption("Read-only model context payload for the selected run.")
                st.json(model_diag)
        if board_diag:
            with st.expander("Board diagnostics - board build", expanded=False):
                st.caption("Board counts, filters, gates, and runtime checks.")
                st.json(board_diag)
        if market_coverage:
            with st.expander("Market coverage - live market survival", expanded=False):
                st.caption("Coverage and candidate survival by market.")
                st.json(market_coverage)
        if daily_summary_text:
            with st.expander("Daily summary - operator text", expanded=False):
                st.code(daily_summary_text, language="text")
        with st.expander("Full rejection table - filtered candidates", expanded=False):
            if rejected_df is None or rejected_df.empty:
                render_empty_state("No rejection rows")
            else:
                st.dataframe(
                    style_rejection_table(rejected_df),
                    width="stretch",
                    hide_index=True,
                    height=360,
                )
        with st.expander("Run summary JSON - UI payload", expanded=False):
            st.json(summary)


# =====================================================================
# Render: Review Layers
# =====================================================================

def _format_review_status_value(value: Any) -> str:
    if value is None:
        return "not_available"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text if text else "not_available"


def _render_review_status_cards(statuses: dict[str, Any]) -> None:
    render_kpi_cards(
        [
            {"label": "Run health", "value": statuses.get("run_health"), "caption": "Quality Summary", "state": _status_state(statuses.get("run_health"))},
            {"label": "Elite count", "value": statuses.get("elite_count"), "caption": "final board"},
            {"label": "Full market", "value": statuses.get("full_market_count"), "caption": "operator rows"},
            {"label": "Kelly eligible", "value": statuses.get("kelly_eligible_count"), "caption": "rows"},
        ]
    )


def _render_phase15_verdict_cards(statuses: dict[str, Any]) -> None:
    cards: list[str] = []
    phase_modes = {
        "phase15d_review": "REVIEW ONLY",
        "phase15e_outcome": "REVIEW ONLY",
        "phase15f_policy_simulation": "SIMULATION ONLY",
        "phase15g_missed_winner_attribution": "REVIEW ONLY",
    }
    for phase_key, label in PHASE15_READINESS_LABELS.items():
        verdict = _format_review_status_value(statuses.get(f"{phase_key}_readiness_verdict"))
        mode = phase_modes.get(phase_key, "REVIEW ONLY")
        state = _status_state(verdict)
        cards.append(
            f"""
            <div class="cv-review-layer-card" data-state="{html.escape(state)}">
                <div class="cv-review-layer-top">
                    <span>{html.escape(label)}</span>
                    <span class="cv-badge">{html.escape(mode)}</span>
                    <span class="cv-badge" data-state="muted">NOT ACTIVE GATE</span>
                </div>
                <div class="cv-review-layer-verdict">{html.escape(verdict)}</div>
            </div>
            """
        )
    st.markdown(
        f'<div class="cv-review-grid">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def _render_artifact_notice(label: str, record: dict[str, Any] | None) -> None:
    record = record or {}
    status = str(record.get("status") or "missing")
    path = str(record.get("path") or "")
    error = str(record.get("error") or "")
    state = "warning" if status in {"missing", "error"} else "info"
    detail = error if status == "error" else path
    message = (
        f"{label} could not be loaded."
        if status == "error"
        else f"{label} exists but is empty."
        if status == "empty"
        else f"{label} is not available for this date."
    )
    st.markdown(
        f"""
        <div class="cv-artifact-notice" data-state="{html.escape(state)}">
            <strong>{html.escape(message)}</strong>
            <span>{html.escape(detail)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_text_artifact(
    label: str,
    text: str,
    record: dict[str, Any] | None,
    expanded: bool = False,
) -> None:
    if text:
        with st.expander(label, expanded=expanded):
            st.code(text, language="text")
            path = str((record or {}).get("path") or "")
            if path:
                st.caption(path)
        return
    _render_artifact_notice(label, record)


def _render_csv_preview(
    label: str,
    df: pd.DataFrame,
    record: dict[str, Any] | None,
    max_rows: int = 100,
) -> None:
    if isinstance(df, pd.DataFrame) and not df.empty:
        st.markdown(f"##### {label}")
        display_df = _format_display_columns(df.head(max_rows), REVIEW_DISPLAY_COLUMN_ORDER)
        st.dataframe(display_df, width="stretch", hide_index=True, height=360)
        if len(df) > max_rows:
            st.caption(f"Showing first {max_rows} of {len(df)} rows.")
        path = str((record or {}).get("path") or "")
        if path:
            st.caption(path)
        return
    _render_artifact_notice(label, record)


def _render_phase15_review_panel(phase_key: str, phase: dict[str, Any]) -> None:
    title = str(phase.get("title") or phase_key)
    mode = str(phase.get("mode") or "REVIEW ONLY")
    with st.expander(title, expanded=phase_key == "phase15d_review"):
        render_review_banner(
            f"{mode} / NOT ACTIVE GATE",
            "No picks are suppressed. No prediction, grading, Kelly, suppression, or history changes are made from this UI.",
            "simulation" if mode == "SIMULATION ONLY" else "info",
        )
        _render_text_artifact(
            f"{phase.get('short_title', title)} text",
            str(phase.get("text") or ""),
            phase.get("text_record"),
            expanded=False,
        )
        _render_csv_preview(
            f"{phase.get('short_title', title)} CSV preview",
            phase.get("csv", pd.DataFrame()),
            phase.get("csv_record"),
        )


def render_quality_review_view(
    out_dir: str,
    prediction_date_text: str,
    payload: dict[str, Any] | None = None,
) -> None:
    payload = payload or st.session_state.get("display_prediction") or {}
    board_summary = (payload.get("summary") or {}) if isinstance(payload, dict) else {}
    review_payload = load_quality_review_artifacts_cached(
        out_dir,
        prediction_date_text,
        int(st.session_state.get("history_refresh_token", 0)),
    )
    quality_json = review_payload.get("quality_summary_json") or {}
    statuses = extract_quality_review_statuses(quality_json, board_summary)

    render_section_head(
        "Quality review",
        "Quality Summary plus Phase 15D-G review and simulation artifacts.",
    )
    render_review_banner(
        "Review-only layer",
        "Artifacts are loaded from outputs/runtime/operator. This page does not regenerate reports, suppress picks, or change prediction, grading, Kelly, or history files.",
        "info",
    )
    _render_review_status_cards(statuses)
    _render_phase15_verdict_cards(statuses)

    render_section_head("Quality Summary", "Operator-level run health and checks.")
    _render_text_artifact(
        "quality_summary text",
        str(review_payload.get("quality_summary_text") or ""),
        review_payload.get("quality_summary_text_record"),
        expanded=True,
    )
    if quality_json:
        with st.expander("quality_summary JSON", expanded=False):
            st.json(quality_json)
    else:
        _render_artifact_notice(
            "quality_summary JSON",
            review_payload.get("quality_summary_json_record"),
        )

    render_section_head(
        "Phase 15 review layers",
        "Guard review, outcome validation, policy simulation and attribution.",
    )
    phases = review_payload.get("phases") or {}
    if not phases:
        render_empty_state("No Phase 15 review artifacts found for this date")
    for phase_key, phase in phases.items():
        _render_phase15_review_panel(phase_key, phase)

    records = review_payload.get("records") or []
    if records:
        with st.expander("Quality Review artifact load", expanded=False):
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

        st.markdown('<div class="cv-sidebar-divider"></div>', unsafe_allow_html=True)
        if _THEME_AVAILABLE:
            render_sidebar_label("Navigation")
        # Navigation radio styled as nav list
        view = st.radio(
            "Navigation",
            options=[
                ("today", "Today's Board"),
                ("slate", "Slate"),
                ("review_layers", "Review Layers"),
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

        st.markdown('<div class="cv-sidebar-divider"></div>', unsafe_allow_html=True)
        if _THEME_AVAILABLE:
            render_sidebar_label("Run Config")
        st.markdown(
            '<div class="cv-sidebar-helper">Select the runtime folder and prediction date to inspect.</div>',
            unsafe_allow_html=True,
        )
        out_dir = st.text_input("Output folder", value=default_out_dir)
        prediction_date = st.date_input("Prediction date", value=today)
        prediction_date_text = normalize_prediction_date_text(prediction_date)

        with st.expander("Training window", expanded=False):
            train_start = st.date_input("Training start", value=default_train_start)
            train_end = st.date_input("Training end", value=default_train_end)

        st.markdown('<div class="cv-sidebar-divider"></div>', unsafe_allow_html=True)
        if _THEME_AVAILABLE:
            render_sidebar_label("Actions")
        fit_clicked = st.button("Fit / Refresh Model", width="stretch")
        predict_clicked = st.button(
            "Run Predictions", type="primary", width="stretch"
        )
        reload_history_clicked = st.button("Reload History", width="stretch")

        st.markdown('<div class="cv-sidebar-divider"></div>', unsafe_allow_html=True)
        if _THEME_AVAILABLE:
            render_sidebar_label("Markets Targeted")
        st.markdown(
            """
            <div class="cv-sidebar-market-list">
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
        "review_layers": (
            "Review Layers",
            "Quality Summary and Phase 15D-G review-only diagnostics.",
        ),
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
        render_today_board(payload, resolved_out_dir_text, prediction_date_text)
    elif active_key == "slate":
        render_slate_view(payload)
    elif active_key == "review_layers":
        render_quality_review_view(
            resolved_out_dir_text,
            prediction_date_text,
            payload,
        )
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
