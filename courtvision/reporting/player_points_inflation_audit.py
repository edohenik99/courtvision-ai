"""
Phase 13B: Player Points Inflation Source Audit.

Analytics-only audit of why player_points projections are inflated,
especially when feeding high-edge combo OVER failures.

Data sources:
  - data/history/market_shadow_history.csv  (primary: graded player_points rows)
  - data/history/pick_history.csv  (field coverage only)
  - outputs/runtime/operator/full_market_board_*.csv  (minutes/context/injury enrichment)
  - outputs/runtime/operator/elite_board_*.csv  (elite comparison/enrichment)
  - outputs/runtime/operator/edge_containment_review_flags_*.csv  (combo-held rows)

This module DOES NOT:
  - modify projections, edge, confidence, or Kelly sizing
  - change elite gates, selection thresholds, or candidate generation
  - alter grading logic, model formulas, or player baselines
"""
from __future__ import annotations

import glob
import json
import statistics
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HISTORY_CSV    = "data/history/market_shadow_history.csv"
DEFAULT_PICK_HISTORY_CSV = "data/history/pick_history.csv"
DEFAULT_FULL_MARKET_GLOB = "outputs/runtime/operator/full_market_board_*.csv"
DEFAULT_ELITE_BOARD_GLOB = "outputs/runtime/operator/elite_board_*.csv"
DEFAULT_GRADING_GLOB   = "outputs/runtime/research/grading_results_*.csv"
DEFAULT_OPERATOR_DIR   = "outputs/runtime/operator"
DEFAULT_RUNTIME_ROOT   = "outputs/runtime"

COMBO_MARKETS: frozenset[str] = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

_GRADED_STATUSES: frozenset[str] = frozenset({"hit", "miss", "push"})
_DNP_ACTUAL_THRESHOLD: float = 2.0   # actual <= this treated as probable DNP
EDGE_THRESHOLD: float = 4.0

# Root cause labels
RC_MINUTES       = "MINUTES_OVERPROJECTION"
RC_USAGE         = "USAGE_OVERPROJECTION"
RC_PACE          = "PACE_OR_MATCHUP_INFLATION"
RC_INJURY        = "INJURY_ROLE_OVERREACTION"
RC_LOW_LINE      = "LOW_LINE_UPSIDE_INFLATION"
RC_HIGH_EDGE     = "HIGH_EDGE_FALSE_POSITIVE"
RC_DNP           = "DNP_OR_LOW_MINUTES_FAILURE"
RC_CONFLICT      = "CONTEXT_CONFLICT_IGNORED"
RC_INSUFFICIENT  = "INSUFFICIENT_FIELDS"

# Enrichment columns to pull from grading_results
_ENRICH_COLS: list[str] = [
    "minutes_avg",
    "minutes_bucket",
    "manual_minutes_limit",
    "manual_projection_adjustment",
    "manual_context_reason",
    "manual_context_applied",
    "injury_influence_bucket",
    "injury_status",
    "injury_adjusted_projection",
    "injury_baseline_projection",
    "injury_projection_delta",
    "injury_impact_score",
    "player_points_recent_form_ratio",
    "player_points_injury_independent_support",
    "team_pace",
    "opponent_pace",
    "matchup_pace",
    "opponent",
    "pace_context_signal",
    "defense_context_signal",
    "rest_days",
    "rest_edge",
    "context_pick_alignment",
    "context_caution_level",
    "context_conflict_cause",
    "context_preview_applied",
    "side_edge_pct",
    "source_lane",
    "is_elite",
    "stake_fraction",
    "recommended_bet",
    "recalibrated_projection",
    "projection",
    "player_points_line_band",
    "player_points_realism_dampened",
    "player_points_realism_dampener_reason",
    "blocked_by_player_points_strong_over_calibration",
    "player_points_strong_over_calibration_reason",
    "blocked_by_elite_points_risk_guard",
    "elite_points_risk_guard_reason",
]

_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "minutes_projection_or_bucket": (
        "minutes_avg",
        "minutes_bucket",
        "manual_minutes_limit",
    ),
    "usage_or_context": (
        "player_points_recent_form_ratio",
        "context_pick_alignment",
        "context_caution_level",
        "context_conflict_cause",
        "manual_context_reason",
        "manual_context_applied",
    ),
    "pace_opponent_or_context": (
        "team_pace",
        "opponent_pace",
        "matchup_pace",
        "opponent",
        "pace_context_signal",
        "defense_context_signal",
        "overall_context_signal",
    ),
    "injury_or_role": (
        "injury_status",
        "injury_impact_score",
        "injury_projection_delta",
        "injury_influence_bucket",
        "player_points_injury_independent_support",
        "manual_status",
    ),
    "projected_and_actual_points": (
        "model_projection",
        "projection",
        "actual_value",
        "result_status",
    ),
    "source_attribution": (
        "line_source",
        "source_lane",
        "provider_used",
        "raw_prop_type",
        "raw_market_type",
    ),
}

_MATCHING_KEY_FIELDS: tuple[str, ...] = (
    "prediction_date",
    "player_name",
    "player_id",
    "game_id",
    "market_type",
    "selection",
    "line",
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def inflation_audit_json_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"player_points_inflation_audit_{date}.json"


def inflation_audit_txt_path_for_date(
    date: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "operator" / f"player_points_inflation_audit_{date}.txt"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_shadow_history(
    history_csv: str | Path | pd.DataFrame,
) -> pd.DataFrame:
    if isinstance(history_csv, pd.DataFrame):
        return history_csv.copy()
    p = Path(history_csv)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _load_csv_path(
    csv_path: str | Path | pd.DataFrame,
) -> pd.DataFrame:
    if isinstance(csv_path, pd.DataFrame):
        return csv_path.copy()
    p = Path(csv_path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _load_csv_glob(
    pattern: str | None,
) -> pd.DataFrame:
    if not pattern:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for f in sorted(glob.glob(pattern)):
        try:
            df = pd.read_csv(f)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _load_grading_results(
    grading_glob: str | None = None,
) -> pd.DataFrame:
    return _load_csv_glob(grading_glob or DEFAULT_GRADING_GLOB)


def _load_runtime_boards(
    full_market_glob: str | None = DEFAULT_FULL_MARKET_GLOB,
    elite_board_glob: str | None = DEFAULT_ELITE_BOARD_GLOB,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _load_csv_glob(full_market_glob), _load_csv_glob(elite_board_glob)


def _load_review_flags(operator_dir: str | Path) -> pd.DataFrame:
    pattern = str(Path(operator_dir) / "edge_containment_review_flags_*.csv")
    frames: list[pd.DataFrame] = []
    for f in sorted(glob.glob(pattern)):
        try:
            df = pd.read_csv(f)
            if not df.empty:
                frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _norm_player(name: Any) -> str:
    return str(name).lower().strip() if pd.notna(name) else ""


def _norm_text(v: Any) -> str:
    if pd.isna(v):
        return ""
    return str(v).strip()


def _join_key(date: Any, player: Any) -> str:
    return f"{date}|{_norm_player(player)}"


def _combo_link_keys_for_row(row: Any) -> set[str]:
    """Stable combo/component link keys.

    Combo markets do not share line/market_type with player_points component rows,
    so the conservative link is date + player_id when present, with an exact
    normalized name fallback.
    """
    if isinstance(row, pd.Series):
        get = row.get
    else:
        get = row.get
    date = _norm_text(get("prediction_date"))
    player_id = _norm_text(get("player_id"))
    player_name = _norm_player(get("player_name"))
    keys: set[str] = set()
    if date and player_id:
        keys.add(f"{date}|id:{player_id}")
    if date and player_name:
        keys.add(f"{date}|name:{player_name}")
        keys.add(f"{date}|{player_name}")  # Backward-compatible display/test key.
    return keys


def _line_key(v: Any) -> str:
    f = _to_float(v)
    if f is None:
        return ""
    return f"{f:.3f}".rstrip("0").rstrip(".")


def _match_keys_for_row(row: Any) -> list[tuple[str, str, str, str, str, str]]:
    """Return conservative enrichment keys using exact ids/names and line.

    Keys are ordered from most specific to least specific. Game id is used when
    available on both sources; the non-game key is kept as a fallback for older
    history rows that do not carry game_id.
    """
    get = row.get
    date = _norm_text(get("prediction_date"))
    player_id = _norm_text(get("player_id"))
    player = f"id:{player_id}" if player_id else f"name:{_norm_player(get('player_name'))}"
    game_id = _norm_text(get("game_id"))
    market = _norm_text(get("market_type")).lower()
    selection = _norm_text(get("selection")).lower()
    line = _line_key(get("line"))
    keys: list[tuple[str, str, str, str, str, str]] = []
    if date and player and market and selection and line:
        if game_id:
            keys.append((date, player, game_id, market, selection, line))
        keys.append((date, player, "", market, selection, line))
    return keys


def _str_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return df[col].astype(str)
    return pd.Series([""] * len(df), index=df.index, dtype=str)


def _num_series(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series([float("nan")] * len(df), index=df.index, dtype=float)


def _coalesce_text(*vals: Any) -> str | None:
    for v in vals:
        s = _norm_text(v)
        if s and s.lower() not in {"nan", "none"}:
            return s.lower()
    return None


def _to_float(v: Any) -> float | None:
    import math
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _safe_mean(vals: list[Any]) -> float | None:
    clean = [v for v in vals if v is not None and _to_float(v) is not None]
    return round(sum(float(v) for v in clean) / len(clean), 4) if clean else None


def _safe_median(vals: list[Any]) -> float | None:
    clean = sorted(float(v) for v in vals if v is not None and _to_float(v) is not None)
    return round(statistics.median(clean), 4) if clean else None


def _hit_rate(rows: list[dict[str, Any]]) -> float | None:
    hm = [r for r in rows if r.get("result_status") in ("hit", "miss")]
    if not hm:
        return None
    return round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4)


def _roi_mean(rows: list[dict[str, Any]]) -> float | None:
    rois = [_to_float(r.get("shadow_roi")) for r in rows]
    clean = [v for v in rois if v is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _over_projection_rate(rows: list[dict[str, Any]]) -> float | None:
    errs = [_to_float(r.get("projection_error")) for r in rows]
    clean = [v for v in errs if v is not None]
    if not clean:
        return None
    return round(sum(1 for v in clean if v > 0) / len(clean), 4)


# ---------------------------------------------------------------------------
# Field availability audit
# ---------------------------------------------------------------------------

def _audit_field_availability(
    sh_pts: pd.DataFrame,
    gr_pts: pd.DataFrame,
    source_frames: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    def _avail(df: pd.DataFrame, col: str, total: int) -> str:
        if df.empty or col not in df.columns:
            return "absent"
        n = int(df[col].notna().sum())
        return f"available_{n}_of_{total}" if n else "column_all_null"

    def _source_cols(df: pd.DataFrame, wanted: tuple[str, ...]) -> list[str]:
        if df.empty:
            return []
        return [c for c in wanted if c in df.columns]

    total_sh = len(sh_pts)
    total_gr = len(gr_pts)
    frames = source_frames or {
        "market_shadow_history": sh_pts,
        "grading_results": gr_pts,
    }

    field_groups: dict[str, dict[str, Any]] = {}
    for group, cols in _FIELD_GROUPS.items():
        by_source = {
            source: _source_cols(df, cols)
            for source, df in frames.items()
        }
        files_with_fields = [
            source for source, matched in by_source.items() if matched
        ]
        field_groups[group] = {
            "status": "supported" if files_with_fields else "insufficient_fields",
            "files": files_with_fields,
            "columns_by_source": by_source,
        }

    matching_keys = {
        source: [k for k in _MATCHING_KEY_FIELDS if not df.empty and k in df.columns]
        for source, df in frames.items()
    }
    source_files = field_groups["source_attribution"]["files"]
    source_attribution_status = "partial" if source_files else "insufficient_fields"
    missing_groups = [
        group for group, meta in field_groups.items()
        if meta["status"] == "insufficient_fields"
    ]

    flat: dict[str, Any] = {
        "shadow_history_player_points_rows": total_sh,
        "grading_results_player_points_rows": total_gr,
        # Shadow history fields
        "context_pick_alignment_sh":  _avail(sh_pts, "context_pick_alignment", total_sh),
        "context_caution_level_sh":   _avail(sh_pts, "context_caution_level", total_sh),
        "context_conflict_cause_sh":  _avail(sh_pts, "context_conflict_cause", total_sh),
        "edge_sh":                    _avail(sh_pts, "edge", total_sh),
        "confidence_sh":              _avail(sh_pts, "confidence", total_sh),
        "model_projection_sh":        _avail(sh_pts, "model_projection", total_sh),
        "actual_value_sh":            _avail(sh_pts, "actual_value", total_sh),
        "fragility_score_sh":         _avail(sh_pts, "fragility_score", total_sh),
        # Grading results enrichment fields
        "minutes_avg_gr":                  _avail(gr_pts, "minutes_avg", total_gr),
        "minutes_bucket_gr":               _avail(gr_pts, "minutes_bucket", total_gr),
        "injury_projection_delta_gr":      _avail(gr_pts, "injury_projection_delta", total_gr),
        "injury_impact_score_gr":          _avail(gr_pts, "injury_impact_score", total_gr),
        "form_ratio_gr":                   _avail(gr_pts, "player_points_recent_form_ratio", total_gr),
        "pace_context_signal_gr":          _avail(gr_pts, "pace_context_signal", total_gr),
        "matchup_pace_gr":                 _avail(gr_pts, "matchup_pace", total_gr),
        "context_pick_alignment_gr":       _avail(gr_pts, "context_pick_alignment", total_gr),
        # Fields absent from both
        "usage_rate":                      "absent_not_available_in_any_source",
        "actual_minutes":                  "absent_not_available_in_any_source",
        "minutes_bucket_sh":               "absent_from_shadow_history",
        "injury_status_sh":                "absent_from_shadow_history",
        "source_attribution_status":       source_attribution_status,
        "source_attribution_supported":    source_attribution_status,
        "field_group_status":              field_groups,
        "matching_keys_supported":         matching_keys,
        "missing_field_groups":            missing_groups,
        "source_attribution_note":         "partial: line/source lane fields exist, but row-level projection component provenance is limited",
    }
    return flat


# ---------------------------------------------------------------------------
# Build enrichment lookup from grading_results
# ---------------------------------------------------------------------------

def _build_gr_lookup(
    gr_pts: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    if gr_pts.empty:
        return {}
    avail = [c for c in _ENRICH_COLS if c in gr_pts.columns]
    gr_pts = gr_pts.copy()
    gr_pts["_key"] = gr_pts.apply(
        lambda r: _join_key(r.get("prediction_date"), r.get("player_name")), axis=1
    )
    deduped = gr_pts.drop_duplicates("_key").set_index("_key")
    return deduped[avail].to_dict("index")


def _build_enrichment_lookup(
    full_pts: pd.DataFrame,
    elite_pts: pd.DataFrame,
) -> dict[tuple[str, str, str, str, str, str], dict[str, Any]]:
    """Build conservative row enrichment lookup from runtime boards.

    The lookup keys use prediction_date + player_id/name + game_id when present
    + market_type + selection + line. Elite rows are layered over full-market
    rows only to add elite context, not to change source history values.
    """
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    avail = [c for c in _ENRICH_COLS if c in set(full_pts.columns) | set(elite_pts.columns)]

    def _store(df: pd.DataFrame, is_elite_source: bool) -> None:
        if df.empty:
            return
        market = _str_series(df, "market_type").str.lower().str.strip()
        pts = df[market == "player_points"].copy()
        for _, row in pts.iterrows():
            enrichment = {c: row.get(c) for c in avail if c in pts.columns}
            if is_elite_source:
                enrichment["is_elite"] = True
            for key in _match_keys_for_row(row):
                if key not in lookup:
                    lookup[key] = enrichment.copy()
                else:
                    merged = lookup[key].copy()
                    merged.update({k: v for k, v in enrichment.items() if pd.notna(v)})
                    lookup[key] = merged

    _store(full_pts, False)
    _store(elite_pts, True)
    return lookup


def _lookup_enrichment(
    row: pd.Series,
    lookup: dict[tuple[str, str, str, str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    for key in _match_keys_for_row(row):
        if key in lookup:
            return lookup[key]
    return {}


# ---------------------------------------------------------------------------
# Build enriched records from shadow_history
# ---------------------------------------------------------------------------

def _build_records(
    sh_pts: pd.DataFrame,
    gr_lookup: dict[Any, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for _, row in sh_pts.iterrows():
        date    = str(row.get("prediction_date", ""))
        player  = str(row.get("player_name", ""))
        player_id = _norm_text(row.get("player_id"))
        game_id = _norm_text(row.get("game_id"))
        sel     = str(row.get("selection", "")).lower().strip()
        result  = str(row.get("result_status", "")).lower().strip()
        proj    = _to_float(row.get("model_projection"))
        actual  = _to_float(row.get("actual_value"))
        edge    = _to_float(row.get("edge"))
        line    = _to_float(row.get("line"))
        conf    = _to_float(row.get("confidence"))
        roi     = _to_float(row.get("shadow_roi"))
        frag    = _to_float(row.get("fragility_score"))

        proj_err = (proj - actual) if proj is not None and actual is not None else None

        # Context from shadow_history
        ctx_align  = _coalesce_text(row.get("context_pick_alignment"))
        ctx_caut   = _coalesce_text(row.get("context_caution_level"))
        ctx_conf   = _coalesce_text(row.get("context_conflict_cause"))

        # Enrich from runtime boards when conservative row keys match. The
        # legacy date/name lookup keeps older unit tests and empty research
        # files harmless.
        key = _join_key(date, player)
        gr  = _lookup_enrichment(row, gr_lookup) if gr_lookup else {}
        if not gr and isinstance(next(iter(gr_lookup.keys()), ""), str):
            gr = gr_lookup.get(key, {})

        ctx_align = _coalesce_text(ctx_align, gr.get("context_pick_alignment"))
        ctx_caut = _coalesce_text(ctx_caut, gr.get("context_caution_level"))
        ctx_conf = _coalesce_text(ctx_conf, gr.get("context_conflict_cause"))

        rec: dict[str, Any] = {
            "prediction_date":  date,
            "player_name":      player,
            "player_id":        player_id,
            "game_id":          game_id,
            "selection":        sel,
            "result_status":    result,
            "line":             line,
            "model_projection": proj,
            "actual_value":     actual,
            "projection_error": proj_err,
            "edge":             edge,
            "confidence":       conf,
            "shadow_roi":       roi,
            "fragility_score":  frag,
            "context_pick_alignment": ctx_align,
            "context_caution_level":  ctx_caut,
            "context_conflict_cause": ctx_conf,
            # Enrichment (None if not available)
            "minutes_avg":             _to_float(gr.get("minutes_avg")),
            "minutes_bucket":          gr.get("minutes_bucket"),
            "manual_minutes_limit":    _to_float(gr.get("manual_minutes_limit")),
            "manual_projection_adjustment": _to_float(gr.get("manual_projection_adjustment")),
            "manual_context_reason":   gr.get("manual_context_reason"),
            "manual_context_applied":  gr.get("manual_context_applied"),
            "injury_status":           gr.get("injury_status"),
            "injury_influence_bucket": gr.get("injury_influence_bucket"),
            "injury_projection_delta": _to_float(gr.get("injury_projection_delta")),
            "injury_impact_score":     _to_float(gr.get("injury_impact_score")),
            "form_ratio":              _to_float(gr.get("player_points_recent_form_ratio")),
            "pace_context_signal":     gr.get("pace_context_signal"),
            "opponent_pace":           _to_float(gr.get("opponent_pace")),
            "opponent":                gr.get("opponent") or row.get("opponent"),
            "matchup_pace":            _to_float(gr.get("matchup_pace")),
            "defense_context_signal":  gr.get("defense_context_signal"),
            "source_lane":             gr.get("source_lane"),
            "is_elite":                bool(gr.get("is_elite")) if gr else False,
            "stake_fraction":          _to_float(gr.get("stake_fraction")),
            "recommended_bet":         gr.get("recommended_bet"),
            "recalibrated_projection": _to_float(gr.get("recalibrated_projection")),
            "board_projection":        _to_float(gr.get("projection")),
            "player_points_line_band": gr.get("player_points_line_band"),
            "player_points_realism_dampened": gr.get("player_points_realism_dampened"),
            "player_points_realism_dampener_reason": gr.get("player_points_realism_dampener_reason"),
            "blocked_by_player_points_strong_over_calibration": gr.get("blocked_by_player_points_strong_over_calibration"),
            "player_points_strong_over_calibration_reason": gr.get("player_points_strong_over_calibration_reason"),
            "blocked_by_elite_points_risk_guard": gr.get("blocked_by_elite_points_risk_guard"),
            "elite_points_risk_guard_reason": gr.get("elite_points_risk_guard_reason"),
        }
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Combo-linked identification
# ---------------------------------------------------------------------------

def _filter_graded(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    rs = _str_series(df, "result_status").str.lower().str.strip()
    return df[rs.isin(_GRADED_STATUSES)].copy()


def _filter_high_edge_combo_overs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    edge = _num_series(df, "edge")
    sel = _str_series(df, "selection").str.lower().str.strip()
    market = _str_series(df, "market_type").str.lower().str.strip()
    mask = (
        market.isin(COMBO_MARKETS)
        & (sel == "over")
        & (edge >= EDGE_THRESHOLD)
    )
    return df[mask].copy()


def _identify_combo_linked_keys(sh: pd.DataFrame) -> set[str]:
    """Return stable player/date keys from high-edge combo OVER failures."""
    if sh.empty or "market_type" not in sh.columns:
        return set()
    combo_fail = _filter_graded(_filter_high_edge_combo_overs(sh))
    result = _str_series(combo_fail, "result_status").str.lower().str.strip()
    combo_fail = combo_fail[result == "miss"]
    keys: set[str] = set()
    for _, row in combo_fail.iterrows():
        keys.update(_combo_link_keys_for_row(row))
    return keys


def _is_combo_linked(
    rec: dict[str, Any],
    combo_keys: set[str],
) -> bool:
    return bool(_combo_link_keys_for_row(rec) & combo_keys)


# ---------------------------------------------------------------------------
# Root cause classification
# ---------------------------------------------------------------------------

def _classify_failure_mode(rec: dict[str, Any]) -> str:
    """Classify the likely inflation source for a single failed player_points OVER."""
    result = rec.get("result_status", "")
    if result != "miss":
        return "NOT_A_FAILURE"

    actual  = rec.get("actual_value")
    edge    = rec.get("edge")
    line    = rec.get("line")
    ctx_a   = rec.get("context_pick_alignment") or ""
    ctx_c   = rec.get("context_caution_level") or ""
    inj_d   = rec.get("injury_projection_delta")
    form    = rec.get("form_ratio")
    pace    = rec.get("pace_context_signal") or ""
    actual_minutes = rec.get("actual_minutes")
    projected_minutes = rec.get("minutes_avg")

    # 1. DNP or zero-minute game
    if actual is not None and actual <= _DNP_ACTUAL_THRESHOLD:
        return RC_DNP

    if (
        projected_minutes is not None
        and actual_minutes is not None
        and projected_minutes - actual_minutes >= 8.0
    ):
        return RC_MINUTES

    # 2. Context conflict was present but bet was placed
    if ctx_a == "conflicted" and ctx_c in ("low", "medium", ""):
        return RC_CONFLICT

    # 3. Injury boost was applied and was positive (model boosted, player under-performed)
    if inj_d is not None and inj_d > 1.0:
        return RC_INJURY

    # 4. High edge that didn't deliver (possible edge inflation from model)
    if edge is not None and edge >= 6.0:
        return RC_HIGH_EDGE

    # 5. Form ratio inflation: player recent form was elevated, model over-boosted.
    if form is not None and form > 1.10:
        return RC_USAGE

    # 6. Pace context explicitly supported the OVER but it missed
    if pace == "supports_over":
        return RC_PACE

    # 7. Low-line OVER: model inflates upside unrealistically for short lines.
    if line is not None and line < 8.5:
        return RC_LOW_LINE

    # 8. Minutes context: if minutes_avg is very high and actual was much lower
    minutes = rec.get("minutes_avg")
    if minutes is not None and actual is not None:
        if minutes > 32 and actual < 10:
            return RC_MINUTES

    return RC_INSUFFICIENT


# ---------------------------------------------------------------------------
# Breakdown builders
# ---------------------------------------------------------------------------

def _edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "unknown"
    if edge < 2.0:
        return "below_2"
    if edge < 4.0:
        return "2_to_4"
    if edge < 6.0:
        return "4_to_6"
    return "6_plus"


def _line_bucket(line: float | None) -> str:
    if line is None:
        return "unknown"
    if line < 8.5:
        return "below_8.5"
    if line < 15.0:
        return "8.5_to_14.5"
    if line < 21.0:
        return "15_to_20.5"
    return "21_plus"


def _conf_bucket(conf: float | None) -> str:
    if conf is None:
        return "unknown"
    if conf < 0.70:
        return "below_0.70"
    if conf < 0.80:
        return "0.70_to_0.80"
    return "0.80_plus"


def _minutes_bucket(mb: Any, ma: float | None) -> str:
    if mb and str(mb).strip().lower() not in ("", "nan", "none"):
        return str(mb).strip().lower()
    if ma is not None:
        if ma < 20:
            return "low"
        if ma < 32:
            return "medium"
        return "high"
    return "unknown"


def _ctx_align_bucket(v: Any) -> str:
    s = str(v or "").lower().strip()
    return s if s in ("aligned", "conflicted", "neutral") else "unknown"


def _ctx_caution_bucket(v: Any) -> str:
    s = str(v or "").lower().strip()
    return s if s in ("low", "medium", "high") else "unknown"


def _injury_bucket(inj: float | None) -> str:
    if inj is None:
        return "unknown"
    if inj > 0.5:
        return "injury_influenced"
    return "not_injury_influenced"


def _breakdown_by(
    records: list[dict[str, Any]],
    bucket_fn: Any,
    key: str,
) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        buckets[bucket_fn(r.get(key))].append(r)
    result = {}
    for bkt, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in _GRADED_STATUSES]
        hm     = [r for r in graded if r["result_status"] in ("hit", "miss")]
        hr     = round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4) if hm else None
        errs   = [r["projection_error"] for r in graded if r["projection_error"] is not None]
        result[bkt] = {
            "sample_size":          len(rows),
            "graded_count":         len(graded),
            "hit_rate":             hr,
            "avg_projection_error": _safe_mean(errs),
            "over_projection_rate": _over_projection_rate(graded),
            "roi":                  _roi_mean(graded),
        }
    return result


def _breakdown_records(
    records: list[dict[str, Any]],
    bucket_fn: Any,
) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        buckets[bucket_fn(r)].append(r)
    result = {}
    for bkt, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in _GRADED_STATUSES]
        errs = [r["projection_error"] for r in graded if r["projection_error"] is not None]
        result[bkt] = {
            "sample_size":          len(rows),
            "graded_count":         len(graded),
            "hit_rate":             _hit_rate(graded),
            "avg_projection_error": _safe_mean(errs),
            "over_projection_rate": _over_projection_rate(graded),
            "roi":                  _roi_mean(graded),
        }
    return result


def _player_breakdown(
    records: list[dict[str, Any]],
    combo_keys: set[str],
) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        buckets[r["player_name"]].append(r)

    result = {}
    for player, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in _GRADED_STATUSES]
        misses = [r for r in graded if r["result_status"] == "miss"]
        errors = [r["projection_error"] for r in graded if r["projection_error"] is not None]
        miss_errors = [r["projection_error"] for r in misses if r["projection_error"] is not None]

        combo_link = sum(
            1 for r in graded
            if _is_combo_linked(r, combo_keys)
        )
        result[player] = {
            "sample_size":                len(rows),
            "graded_count":               len(graded),
            "repeat_failure_count":       len(misses),
            "avg_projection_error":       _safe_mean(errors),
            "over_projection_rate":       _over_projection_rate(graded),
            "worst_single_error":         round(max(miss_errors), 4) if miss_errors else None,
            "linked_combo_failure_count": combo_link,
        }
    return result


def _date_breakdown(records: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        buckets[r["prediction_date"]].append(r)

    result = {}
    for date, rows in sorted(buckets.items()):
        graded = [r for r in rows if r["result_status"] in _GRADED_STATUSES]
        hm     = [r for r in graded if r["result_status"] in ("hit", "miss")]
        hr     = round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4) if hm else None
        misses = sum(1 for r in graded if r["result_status"] == "miss")
        errs   = [r["projection_error"] for r in graded if r["projection_error"] is not None]
        result[date] = {
            "sample_size":          len(rows),
            "graded_count":         len(graded),
            "hit_rate":             hr,
            "avg_projection_error": _safe_mean(errs),
            "roi":                  _roi_mean(graded),
            "points_over_misses":   misses,
        }
    return result


# ---------------------------------------------------------------------------
# Root cause summary
# ---------------------------------------------------------------------------

def _root_cause_summary(
    failed_overs: list[dict[str, Any]],
) -> dict[str, Any]:
    from collections import Counter
    causes = [_classify_failure_mode(r) for r in failed_overs]
    counts = dict(Counter(c for c in causes if c != "NOT_A_FAILURE"))
    n = len(failed_overs)

    def _pct(label: str) -> float | None:
        return round(counts.get(label, 0) / n, 4) if n else None

    dominant = max(counts, key=lambda k: counts[k], default="unknown") if counts else "unknown"

    return {
        "total_failures_analyzed":    n,
        "dominant_failure_mode":      dominant,
        "root_cause_counts":          counts,
        "pct_dnp_or_low_minutes":     _pct(RC_DNP),
        "pct_context_conflict":       _pct(RC_CONFLICT),
        "pct_injury_role":            _pct(RC_INJURY),
        "pct_high_edge_false_positive": _pct(RC_HIGH_EDGE),
        "pct_usage_form":             _pct(RC_USAGE),
        "pct_pace_matchup":           _pct(RC_PACE),
        "pct_low_line_upside":        _pct(RC_LOW_LINE),
        "pct_minutes":                _pct(RC_MINUTES),
        "pct_insufficient":           _pct(RC_INSUFFICIENT),
        "percent_minutes_related":    _pct(RC_MINUTES),
        "percent_usage_related":      _pct(RC_USAGE),
        "percent_pace_matchup_related": _pct(RC_PACE),
        "percent_injury_role_related": _pct(RC_INJURY),
        "percent_low_line_upside_related": _pct(RC_LOW_LINE),
        "percent_high_edge_false_positive": _pct(RC_HIGH_EDGE),
        "percent_dnp_low_minutes":    _pct(RC_DNP),
        "percent_insufficient_fields": _pct(RC_INSUFFICIENT),
    }


# ---------------------------------------------------------------------------
# Combo-linked summary
# ---------------------------------------------------------------------------

def _combo_linked_summary(
    records: list[dict[str, Any]],
    combo_keys: set[str],
) -> dict[str, Any]:
    linked = [
        r for r in records
        if _is_combo_linked(r, combo_keys)
    ]
    graded = [r for r in linked if r["result_status"] in _GRADED_STATUSES]
    overs  = [r for r in graded if r["selection"] == "over"]
    hm     = [r for r in overs if r["result_status"] in ("hit", "miss")]
    hr     = round(sum(1 for r in hm if r["result_status"] == "hit") / len(hm), 4) if hm else None
    errs   = [r["projection_error"] for r in overs if r["projection_error"] is not None]

    return {
        "total_linked_rows":          len(linked),
        "graded_linked_rows":         len(graded),
        "over_linked_rows":           len(overs),
        "hit_rate":                   hr,
        "avg_projection_error":       _safe_mean(errs),
        "over_projection_rate":       _over_projection_rate(overs),
    }


def _summary_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [r for r in rows if r["result_status"] in _GRADED_STATUSES]
    errs = [r["projection_error"] for r in graded if r["projection_error"] is not None]
    return {
        "sample_size":          len(rows),
        "graded_count":         len(graded),
        "hit_rate":             _hit_rate(graded),
        "avg_projection_error": _safe_mean(errs),
        "over_projection_rate": _over_projection_rate(graded),
        "roi":                  _roi_mean(graded),
    }


def _readiness_verdict(payload: dict[str, Any]) -> str:
    if not payload.get("graded_player_points_rows"):
        return "AUDIT_INCOMPLETE_FIELD_COVERAGE"

    dominant = payload.get("dominant_failure_mode")
    if dominant == RC_DNP:
        return "DNP_LOW_MINUTES_FAILURE_CONFIRMED"
    if dominant == RC_CONFLICT:
        return "CONTEXT_CONFLICT_POINTS_OVER_FAILURE_CONFIRMED"
    if dominant == RC_LOW_LINE:
        return "LOW_LINE_UPSIDE_INFLATION_CONFIRMED"
    if dominant == RC_HIGH_EDGE:
        return "HIGH_EDGE_FALSE_POSITIVE_CONFIRMED"

    by_line = payload.get("by_line_bucket", {})
    low_line = by_line.get("below_8.5", {})
    if (
        low_line.get("graded_count", 0) >= 3
        and (low_line.get("avg_projection_error") or 0) > 0
        and (low_line.get("over_projection_rate") or 0) >= 0.6
    ):
        return "LOW_LINE_UPSIDE_INFLATION_CONFIRMED"

    if (
        payload.get("high_edge_points_over_rows", 0) >= 3
        and (payload.get("high_edge_over_avg_error") or 0) > 0
        and (payload.get("high_edge_over_hit_rate") or 1.0) < 0.45
    ):
        return "HIGH_EDGE_FALSE_POSITIVE_CONFIRMED"

    over_data = payload.get("by_selection", {}).get("over", {})
    under_data = payload.get("by_selection", {}).get("under", {})
    if (
        (over_data.get("avg_projection_error") or 0) > 0
        and (over_data.get("over_projection_rate") or 0) >= 0.55
        and (over_data.get("avg_projection_error") or 0)
        > (under_data.get("avg_projection_error") or -999)
    ):
        return "POINTS_OVER_INFLATION_CONFIRMED"

    rc = payload.get("root_cause_summary", {})
    if (rc.get("pct_insufficient") or 0) >= 0.5:
        return "AUDIT_INCOMPLETE_FIELD_COVERAGE"

    return "READY_FOR_PHASE_13C_DIRECTIONAL_EDGE_CALIBRATION"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_player_points_inflation_audit(
    prediction_date: str,
    history_csv:     str | Path | pd.DataFrame = DEFAULT_HISTORY_CSV,
    pick_history_csv: str | Path | pd.DataFrame = DEFAULT_PICK_HISTORY_CSV,
    full_market_glob: str | None = DEFAULT_FULL_MARKET_GLOB,
    elite_board_glob: str | None = DEFAULT_ELITE_BOARD_GLOB,
    grading_glob:    str | None = None,
    operator_dir:    str | Path = DEFAULT_OPERATOR_DIR,
) -> dict[str, Any]:
    sh = _load_shadow_history(history_csv)
    ph = _load_csv_path(pick_history_csv)
    full_board, elite_board = _load_runtime_boards(full_market_glob, elite_board_glob)
    gr = _load_grading_results(grading_glob)

    # Filter to player_points market
    sh_pts = (
        sh[sh["market_type"] == "player_points"].copy()
        if not sh.empty and "market_type" in sh.columns
        else pd.DataFrame()
    )
    gr_pts = (
        gr[gr["market_type"] == "player_points"].copy()
        if not gr.empty and "market_type" in gr.columns
        else pd.DataFrame()
    )
    full_pts = (
        full_board[_str_series(full_board, "market_type").str.lower().str.strip() == "player_points"].copy()
        if not full_board.empty else pd.DataFrame()
    )
    elite_pts = (
        elite_board[_str_series(elite_board, "market_type").str.lower().str.strip() == "player_points"].copy()
        if not elite_board.empty else pd.DataFrame()
    )
    pick_pts = (
        ph[ph["market_type"] == "player_points"].copy()
        if not ph.empty and "market_type" in ph.columns
        else pd.DataFrame()
    )

    field_avail = _audit_field_availability(
        sh_pts,
        gr_pts,
        {
            "market_shadow_history": sh_pts,
            "pick_history": pick_pts,
            "full_market_board": full_pts,
            "elite_board": elite_pts,
            "grading_results": gr_pts,
        },
    )

    if sh.empty or sh_pts.empty:
        return _empty_payload(prediction_date, field_avail)

    # Build enrichment lookup from runtime boards. Fallback to legacy research
    # lookup only when no board enrichment is available.
    gr_lookup = _build_enrichment_lookup(full_pts, elite_pts)
    if not gr_lookup:
        gr_lookup = _build_gr_lookup(gr_pts)

    # Build enriched records
    records = _build_records(sh_pts, gr_lookup)

    # Identify combo-linked player+date keys
    combo_keys = _identify_combo_linked_keys(sh)

    # Subpopulations
    graded_all  = [r for r in records if r["result_status"] in _GRADED_STATUSES]
    over_graded = [r for r in graded_all if r["selection"] == "over"]
    under_graded = [r for r in graded_all if r["selection"] == "under"]

    overs_all = [r for r in records if r["selection"] == "over"]
    he_overs  = [r for r in over_graded
                 if r["edge"] is not None and r["edge"] >= 4.0]

    failed_overs = [r for r in over_graded if r["result_status"] == "miss"]

    # Overall metrics
    all_errors = [r["projection_error"] for r in graded_all if r["projection_error"] is not None]
    over_errors = [r["projection_error"] for r in over_graded if r["projection_error"] is not None]
    under_errors = [r["projection_error"] for r in under_graded if r["projection_error"] is not None]

    hm_all  = [r for r in graded_all if r["result_status"] in ("hit", "miss")]
    hit_rate_all = (
        round(sum(1 for r in hm_all if r["result_status"] == "hit") / len(hm_all), 4)
        if hm_all else None
    )

    hm_over = [r for r in over_graded if r["result_status"] in ("hit", "miss")]
    hit_rate_over = (
        round(sum(1 for r in hm_over if r["result_status"] == "hit") / len(hm_over), 4)
        if hm_over else None
    )

    # Root cause
    rc_summary = _root_cause_summary(failed_overs)

    # Breakdowns
    by_selection = {
        "over": {
            "sample_size":          len(overs_all),
            "graded_count":         len(over_graded),
            "hit_rate":             hit_rate_over,
            "avg_projection_error": _safe_mean(over_errors),
            "over_projection_rate": _over_projection_rate(over_graded),
            "roi":                  _roi_mean(over_graded),
        },
        "under": {
            "sample_size":          len([r for r in records if r["selection"] == "under"]),
            "graded_count":         len(under_graded),
            "hit_rate":             _hit_rate(under_graded),
            "avg_projection_error": _safe_mean(under_errors),
            "over_projection_rate": _over_projection_rate(under_graded),
            "roi":                  _roi_mean(under_graded),
        },
    }

    by_edge_bucket  = _breakdown_by(over_graded, _edge_bucket, "edge")
    by_line_bucket  = _breakdown_by(over_graded, _line_bucket, "line")
    by_conf_bucket  = _breakdown_by(over_graded, _conf_bucket, "confidence")
    by_ctx_align    = _breakdown_by(over_graded, _ctx_align_bucket, "context_pick_alignment")
    by_ctx_caution  = _breakdown_by(over_graded, _ctx_caution_bucket, "context_caution_level")
    by_minutes      = _breakdown_records(
        over_graded,
        lambda r: _minutes_bucket(r.get("minutes_bucket"), r.get("minutes_avg")),
    )
    by_injury = _breakdown_by(
        over_graded,
        lambda v: _injury_bucket(v),
        "injury_impact_score",
    )

    by_player = _player_breakdown(over_graded, combo_keys)
    by_date   = _date_breakdown(over_graded)
    combo_summary = _combo_linked_summary(records, combo_keys)
    combo_linked_records = [r for r in records if _is_combo_linked(r, combo_keys)]
    non_combo_records = [r for r in records if not _is_combo_linked(r, combo_keys)]
    elite_records = [r for r in records if r.get("is_elite")]
    kelly_records = [
        r for r in records
        if (r.get("stake_fraction") or 0) > 0 or bool(_norm_text(r.get("recommended_bet")))
    ]

    # Top 10 player failures sorted by avg error
    top_failures = dict(
        sorted(by_player.items(),
               key=lambda kv: (kv[1].get("avg_projection_error") or 0),
               reverse=True)[:10]
    )

    he_errors = [r["projection_error"] for r in he_overs if r["projection_error"] is not None]
    hm_he     = [r for r in he_overs if r["result_status"] in ("hit", "miss")]
    he_hitrate = round(sum(1 for r in hm_he if r["result_status"] == "hit") / len(hm_he), 4) if hm_he else None

    payload = {
        "prediction_date":                prediction_date,
        "note":                           "audit_only_no_model_change",
        # Counts
        "total_player_points_rows":       len(records),
        "graded_player_points_rows":      len(graded_all),
        "player_points_over_rows":        len(overs_all),
        "player_points_under_rows":       len([r for r in records if r["selection"] == "under"]),
        "high_edge_points_over_rows":     len(he_overs),
        "combo_linked_points_rows":       combo_summary["total_linked_rows"],
        "failed_over_count":              len(failed_overs),
        # Overall metrics
        "avg_points_projection_error":    _safe_mean(all_errors),
        "median_points_projection_error": _safe_median(all_errors),
        "over_projection_rate":           _over_projection_rate(graded_all),
        "hit_rate":                       hit_rate_all,
        "roi":                            _roi_mean(graded_all),
        # OVER-specific
        "over_hit_rate":                  hit_rate_over,
        "over_avg_projection_error":      _safe_mean(over_errors),
        # High-edge OVER summary
        "high_edge_over_hit_rate":        he_hitrate,
        "high_edge_over_avg_error":       _safe_mean(he_errors),
        # Dominant failure mode
        "dominant_failure_mode":          rc_summary.get("dominant_failure_mode", "unknown"),
        # Detailed breakdowns
        "root_cause_summary":             rc_summary,
        "by_selection":                   by_selection,
        "by_edge_bucket":                 by_edge_bucket,
        "by_line_bucket":                 by_line_bucket,
        "by_confidence_bucket":           by_conf_bucket,
        "by_context_alignment":           by_ctx_align,
        "by_context_caution":             by_ctx_caution,
        "by_minutes_bucket":              by_minutes,
        "by_injury_bucket":               by_injury,
        "by_player_top_failures":         top_failures,
        "by_date":                        by_date,
        "combo_linked_points_summary":    combo_summary,
        "non_combo_points_summary":       _summary_for_rows(non_combo_records),
        "elite_points_summary":           _summary_for_rows(elite_records),
        "kelly_eligible_points_summary":  _summary_for_rows(kelly_records),
        "high_edge_points_over_summary":  _summary_for_rows(he_overs),
        "matching_keys_used":             [
            "prediction_date",
            "player_id when available, otherwise exact normalized player_name",
            "game_id when available on the runtime board row",
            "market_type",
            "selection",
            "line",
        ],
        "combo_matching_keys_used":       [
            "prediction_date",
            "player_id when available, otherwise exact normalized player_name",
        ],
        "field_availability":             field_avail,
    }
    payload["readiness_verdict"] = _readiness_verdict(payload)
    return payload


def _empty_payload(
    prediction_date: str,
    field_avail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "prediction_date":                prediction_date,
        "note":                           "audit_only_no_model_change",
        "total_player_points_rows":       0,
        "graded_player_points_rows":      0,
        "player_points_over_rows":        0,
        "player_points_under_rows":       0,
        "high_edge_points_over_rows":     0,
        "combo_linked_points_rows":       0,
        "failed_over_count":              0,
        "avg_points_projection_error":    None,
        "median_points_projection_error": None,
        "over_projection_rate":           None,
        "hit_rate":                       None,
        "roi":                            None,
        "over_hit_rate":                  None,
        "over_avg_projection_error":      None,
        "high_edge_over_hit_rate":        None,
        "high_edge_over_avg_error":       None,
        "dominant_failure_mode":          "unknown",
        "root_cause_summary":             {},
        "by_selection":                   {},
        "by_edge_bucket":                 {},
        "by_line_bucket":                 {},
        "by_confidence_bucket":           {},
        "by_context_alignment":           {},
        "by_context_caution":             {},
        "by_minutes_bucket":              {},
        "by_injury_bucket":               {},
        "by_player_top_failures":         {},
        "by_date":                        {},
        "combo_linked_points_summary":    {},
        "non_combo_points_summary":       {},
        "elite_points_summary":           {},
        "kelly_eligible_points_summary":  {},
        "high_edge_points_over_summary":  {},
        "matching_keys_used":             [],
        "combo_matching_keys_used":       [],
        "field_availability":             field_avail or {},
        "readiness_verdict":              "AUDIT_INCOMPLETE_FIELD_COVERAGE",
    }


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def _format_txt(payload: dict[str, Any], date: str) -> str:
    sep  = "=" * 78
    sep2 = "-" * 78

    def _line(label: str, val: Any) -> str:
        return f"  {label:<42}: {val}\n"

    lines: list[str] = [
        f"{sep}\n",
        "PLAYER POINTS INFLATION SOURCE AUDIT  (Phase 13B -- AUDIT ONLY)\n",
        f"date: {date}    note: {payload.get('note', '')}\n",
        f"{sep}\n\n",

        "OVERVIEW\n", f"{sep2}\n",
        _line("total_player_points_rows",    payload.get("total_player_points_rows", 0)),
        _line("graded_player_points_rows",   payload.get("graded_player_points_rows", 0)),
        _line("player_points_over_rows",     payload.get("player_points_over_rows", 0)),
        _line("player_points_under_rows",    payload.get("player_points_under_rows", 0)),
        _line("high_edge_points_over_rows",  payload.get("high_edge_points_over_rows", 0)),
        _line("combo_linked_points_rows",    payload.get("combo_linked_points_rows", 0)),
        _line("failed_over_count",           payload.get("failed_over_count", 0)),
        _line("readiness_verdict",           payload.get("readiness_verdict")),
        "\n",

        "OVERALL METRICS\n", f"{sep2}\n",
        _line("hit_rate (all graded)",       payload.get("hit_rate")),
        _line("roi (all graded)",            payload.get("roi")),
        _line("avg_projection_error (all)",  payload.get("avg_points_projection_error")),
        _line("median_projection_error",     payload.get("median_points_projection_error")),
        _line("over_projection_rate",        payload.get("over_projection_rate")),
        "\n",

        "DIAGNOSTIC QUESTIONS\n", f"{sep2}\n",
    ]

    # Answer the diagnostic questions
    over_err = payload.get("over_avg_projection_error")
    sel = payload.get("by_selection", {})
    over_data = sel.get("over", {})
    under_data = sel.get("under", {})

    lines += [
        f"  Q: Is inflation isolated to OVERs?\n",
        f"     OVER  avg_error={over_data.get('avg_projection_error')} hit_rate={over_data.get('hit_rate')}\n",
        f"     UNDER avg_error={under_data.get('avg_projection_error')} hit_rate={under_data.get('hit_rate')}\n",
        "\n",
    ]

    eb = payload.get("by_edge_bucket", {})
    he_hr = payload.get("high_edge_over_hit_rate")
    he_ae = payload.get("high_edge_over_avg_error")
    lines += [
        f"  Q: Is it concentrated in high-edge rows?\n",
        f"     high_edge(4+) hit_rate={he_hr} avg_error={he_ae}\n",
    ]
    for bkt, row in sorted(eb.items()):
        lines.append(f"     edge_bucket={bkt}: n={row.get('graded_count')} "
                     f"hit_rate={row.get('hit_rate')} avg_error={row.get('avg_projection_error')}\n")
    lines.append("\n")

    lb = payload.get("by_line_bucket", {})
    lines += [f"  Q: Is it concentrated in specific line ranges?\n"]
    for bkt, row in sorted(lb.items()):
        lines.append(f"     line_bucket={bkt}: n={row.get('graded_count')} "
                     f"hit_rate={row.get('hit_rate')} avg_error={row.get('avg_projection_error')}\n")
    lines.append("\n")

    by_pl = payload.get("by_player_top_failures", {})
    lines += [f"  Q: Is it concentrated in specific players?\n"]
    for p, row in list(by_pl.items())[:5]:
        lines.append(f"     {p}: failures={row.get('repeat_failure_count')} "
                     f"avg_err={row.get('avg_projection_error')} "
                     f"combo_linked={row.get('linked_combo_failure_count')}\n")
    lines.append("\n")

    cs = payload.get("combo_linked_points_summary", {})
    lines += [
        f"  Q: Is it linked to combo-over failures?\n",
        f"     combo_linked: n={cs.get('over_linked_rows')} "
        f"hit_rate={cs.get('hit_rate')} avg_error={cs.get('avg_projection_error')}\n",
        "\n",
    ]

    fa = payload.get("field_availability", {})
    missing = [k for k, v in fa.items() if "absent" in str(v).lower() or "null" in str(v).lower()]
    lines += [
        f"  Q: What fields are missing for full root-cause diagnosis?\n",
        f"     Missing/absent: {', '.join(missing[:10]) if missing else 'none'}\n",
        f"     Source attribution support: {fa.get('source_attribution_status')}\n\n",
    ]

    # Root cause section
    rc = payload.get("root_cause_summary", {})
    if rc:
        lines += ["ROOT CAUSE CLASSIFICATION\n", f"{sep2}\n",
                  f"  dominant_failure_mode     : {rc.get('dominant_failure_mode')}\n",
                  f"  total_failures_analyzed   : {rc.get('total_failures_analyzed', 0)}\n"]
        for label, count in sorted((rc.get("root_cause_counts") or {}).items()):
            lines.append(f"  {label:<42}: {count}\n")
        lines += [
            f"  pct_dnp_or_low_minutes    : {rc.get('pct_dnp_or_low_minutes')}\n",
            f"  pct_context_conflict      : {rc.get('pct_context_conflict')}\n",
            f"  pct_injury_role           : {rc.get('pct_injury_role')}\n",
            f"  pct_high_edge_false_pos   : {rc.get('pct_high_edge_false_positive')}\n",
            f"  pct_usage_form            : {rc.get('pct_usage_form')}\n",
            f"  pct_pace_matchup          : {rc.get('pct_pace_matchup')}\n",
            f"  pct_low_line_upside       : {rc.get('pct_low_line_upside')}\n",
            f"  pct_insufficient          : {rc.get('pct_insufficient')}\n\n",
        ]

    # Selection breakdown
    lines += ["BY SELECTION\n", f"{sep2}\n"]
    for sel_name, row in sorted(sel.items()):
        lines.append(f"  {sel_name}: sample={row.get('sample_size')} graded={row.get('graded_count')} "
                     f"hit_rate={row.get('hit_rate')} avg_err={row.get('avg_projection_error')} "
                     f"over_proj_rate={row.get('over_projection_rate')}\n")
    lines.append("\n")

    # Context alignment breakdown
    ca = payload.get("by_context_alignment", {})
    if ca:
        lines += ["BY CONTEXT ALIGNMENT\n", f"{sep2}\n"]
        for bkt, row in sorted(ca.items()):
            lines.append(f"  {bkt}: n={row.get('graded_count')} hit_rate={row.get('hit_rate')} "
                         f"avg_err={row.get('avg_projection_error')}\n")
        lines.append("\n")

    # Top player failures
    if by_pl:
        lines += ["TOP PLAYER FAILURES (by avg error)\n", f"{sep2}\n"]
        for p, row in list(by_pl.items())[:10]:
            lines.append(f"  {p}: n={row.get('sample_size')} failures={row.get('repeat_failure_count')} "
                         f"avg_err={row.get('avg_projection_error')} "
                         f"worst={row.get('worst_single_error')} "
                         f"combo_linked={row.get('linked_combo_failure_count')}\n")
        lines.append("\n")

    # Field availability
    lines += ["FIELD AVAILABILITY AUDIT\n", f"{sep2}\n"]
    for k, v in sorted(fa.items()):
        lines.append(f"  {k:<48}: {v}\n")
    lines.append(f"\n{sep}\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_player_points_inflation_audit(
    prediction_date: str,
    runtime_root:    str | Path = DEFAULT_RUNTIME_ROOT,
    history_csv:     str | Path | pd.DataFrame = DEFAULT_HISTORY_CSV,
    pick_history_csv: str | Path | pd.DataFrame = DEFAULT_PICK_HISTORY_CSV,
    full_market_glob: str | None = DEFAULT_FULL_MARKET_GLOB,
    elite_board_glob: str | None = DEFAULT_ELITE_BOARD_GLOB,
    grading_glob:    str | None = None,
    operator_dir:    str | Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write player_points inflation audit artifacts.

    Source data is read-only. Returns (json_path, txt_path, payload).
    """
    runtime_root = Path(runtime_root)
    if operator_dir is None:
        operator_dir = runtime_root / "operator"

    payload = build_player_points_inflation_audit(
        prediction_date=prediction_date,
        history_csv=history_csv,
        pick_history_csv=pick_history_csv,
        full_market_glob=full_market_glob,
        elite_board_glob=elite_board_glob,
        grading_glob=grading_glob,
        operator_dir=operator_dir,
    )

    json_path = inflation_audit_json_path_for_date(prediction_date, runtime_root)
    txt_path  = inflation_audit_txt_path_for_date(prediction_date, runtime_root)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    payload["json_path"] = str(json_path)
    payload["txt_path"] = str(txt_path)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_format_txt(payload, prediction_date), encoding="utf-8")

    return json_path, txt_path, payload


__all__ = [
    "build_player_points_inflation_audit",
    "inflation_audit_json_path_for_date",
    "inflation_audit_txt_path_for_date",
    "write_player_points_inflation_audit",
]
