"""
Phase 12D: Edge Containment Shadow Validation.

Shadow-only historical validation and forward-tracking of Phase 12C containment
policies. Classifies each policy's readiness for continued shadow monitoring and
provides a current-slate forward-tracking section.

Phase 12C candidates carried forward:
  P1: cap_effective_edge_at_4_percent            PROMISING
  P2: suppress_high_edge_combo_over              READY_FOR_SHADOW_TEST (21n, 14.3% hit, -74.7% ROI)
  P3: suppress_high_edge_context_conflicted_over  insufficient sample but extreme results
  P4: require_context_aligned_for_6plus_edge     PROMISING
  P5: manual_review_for_6plus_edge_any_direction PROMISING
  P6: suppress_6plus_combo_any_direction         new — 0% hit, -100% ROI (n=6 only)
  P7: suppress_conflicted_6plus_any_direction    new — 0% hit, -100% ROI (n=5 only)

This module DOES NOT:
  - modify live projections, edge, confidence, Kelly sizing, elite gates
  - modify any source history CSV
  - change board generation or grading logic
  - remove or alter rows on any live board
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_VERSION = "1.0"
_GRADED = frozenset({"hit", "miss"})
_MIN_SAMPLE = 20
_MIN_CONFIDENT = 50

_COMBO_MARKETS = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# Verdict thresholds (conservative by design — favour false negatives over false positives):
#   READY_FOR_LIVE_REVIEW_LATER : n >= 50  AND hit_rate < 0.40 AND roi < -0.30
#   READY_FOR_FORWARD_SHADOW    : n >= 20  AND hit_rate < 0.35 AND roi < -0.35
#   PROMISING                   : n >= 20  AND (hit_rate < 0.43 OR roi < -0.30)
#   MONITOR                     : n >= 20  AND hit_rate in [0.43, 0.50) AND roi in [-0.30, -0.10)
#   REJECT_POLICY               : flagged rows at-or-above baseline on both hr AND roi
#   INSUFFICIENT_SAMPLE         : n_graded_flagged < 20

# Policy definitions — each spec drives both the historical mask and forward-tracking mask
_POLICY_SPECS: list[dict[str, Any]] = [
    {
        "name": "cap_effective_edge_at_4_percent",
        "edge_min": 4.0,
        "edge_max": None,
        "selection": None,
        "combo": None,
        "context": None,
        "description": (
            "Flag all picks with edge >= 4%. "
            "Tests whether high reported edges are systematically inflated."
        ),
    },
    {
        "name": "suppress_high_edge_combo_over",
        "edge_min": 4.0,
        "edge_max": None,
        "selection": "over",
        "combo": True,
        "context": None,
        "description": (
            "Flag OVER picks on combo markets with edge >= 4%. "
            "Tests whether high-edge combo OVERs are structurally toxic."
        ),
    },
    {
        "name": "suppress_high_edge_context_conflicted_over",
        "edge_min": 4.0,
        "edge_max": None,
        "selection": "over",
        "combo": None,
        "context": "conflicted",
        "description": (
            "Flag OVER picks with edge >= 4% and context = conflicted. "
            "Tests whether context-conflict eliminates validity of high-edge OVERs."
        ),
    },
    {
        "name": "require_context_aligned_for_6plus_edge",
        "edge_min": 6.0,
        "edge_max": None,
        "selection": None,
        "combo": None,
        "context": "!aligned",   # not-aligned
        "description": (
            "Flag 6%+ edge picks that are NOT context-aligned. "
            "Tests whether extreme edges require context confirmation."
        ),
    },
    {
        "name": "manual_review_for_6plus_edge_any_direction",
        "edge_min": 6.0,
        "edge_max": None,
        "selection": None,
        "combo": None,
        "context": None,
        "description": (
            "Flag all picks with edge >= 6%. "
            "Tests whether all extreme-edge picks warrant manual review."
        ),
    },
    {
        "name": "suppress_6plus_combo_any_direction",
        "edge_min": 6.0,
        "edge_max": None,
        "selection": None,
        "combo": True,
        "context": None,
        "description": (
            "Flag combo market picks with edge >= 6%, any direction. "
            "Tests whether 6%+ combo edges are too unstable to bet regardless of side."
        ),
    },
    {
        "name": "suppress_conflicted_6plus_any_direction",
        "edge_min": 6.0,
        "edge_max": None,
        "selection": None,
        "combo": None,
        "context": "conflicted",
        "description": (
            "Flag conflicted-context picks with edge >= 6%, any direction. "
            "Tests whether extreme-edge conflicted rows are toxic regardless of selection."
        ),
    },
]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _hit_rate(df: pd.DataFrame) -> float | None:
    if df.empty or "result_status" not in df.columns:
        return None
    s = df["result_status"].astype(str).str.lower()
    denom = int(s.isin(["hit", "miss"]).sum())
    return round(int((s == "hit").sum()) / denom, 4) if denom else None


def _roi(df: pd.DataFrame, col: str = "shadow_roi") -> float | None:
    if col not in df.columns:
        return None
    v = pd.to_numeric(df[col], errors="coerce").dropna()
    return round(float(v.mean()), 4) if not v.empty else None


def _mean_error(df: pd.DataFrame) -> float | None:
    proj   = pd.to_numeric(df.get("model_projection", pd.Series(dtype=float)), errors="coerce")
    actual = pd.to_numeric(df.get("actual_value",    pd.Series(dtype=float)), errors="coerce")
    errors = (proj - actual).dropna()
    return round(float(errors.mean()), 4) if not errors.empty else None


def _safe_mean(s: pd.Series) -> float | None:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return round(float(v.mean()), 4) if not v.empty else None


def _risk_reduction_score(flagged_roi: float | None, baseline_roi: float | None) -> float:
    """How many ROI percentage points are recovered by suppressing flagged rows.

    Positive = flagged rows are worse → suppression saves money.
    Capped at [0, 100].
    """
    if flagged_roi is None or baseline_roi is None:
        return 0.0
    return round(max(0.0, min(100.0, (baseline_roi - flagged_roi) * 100)), 1)


def _policy_verdict(
    n_graded: int,
    hit_rate: float | None,
    roi: float | None,
    baseline_hr: float | None,
    baseline_roi: float | None,
) -> str:
    if n_graded < _MIN_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    if hit_rate is None or roi is None:
        return "INSUFFICIENT_SAMPLE"
    # Reject if flagged matches or beats baseline on BOTH hr and roi
    if (
        baseline_hr is not None and hit_rate >= baseline_hr
        and baseline_roi is not None and roi >= baseline_roi
    ):
        return "REJECT_POLICY"
    # Conservative: n >= 50 AND clearly bad → ready for live review consideration later
    if n_graded >= _MIN_CONFIDENT and hit_rate < 0.40 and roi < -0.30:
        return "READY_FOR_LIVE_REVIEW_LATER"
    # n >= 20 AND extremely bad → ready for ongoing forward shadow
    if n_graded >= _MIN_SAMPLE and hit_rate < 0.35 and roi < -0.35:
        return "READY_FOR_FORWARD_SHADOW"
    # n >= 20 AND clearly bad
    if n_graded >= _MIN_SAMPLE and (hit_rate < 0.43 or roi < -0.30):
        return "PROMISING"
    # n >= 20 AND moderately bad
    if n_graded >= _MIN_SAMPLE and hit_rate < 0.50 and roi < -0.10:
        return "MONITOR"
    return "REJECT_POLICY"


# ---------------------------------------------------------------------------
# Policy mask application
# ---------------------------------------------------------------------------

def _build_mask(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    """Build a boolean mask for `df` matching the policy spec.

    All mask components are built against df.index to ensure safe alignment.
    """
    idx    = df.index
    n_true = pd.Series(True,  index=idx)
    n_false = pd.Series(False, index=idx)

    mask = n_true.copy()

    # Edge floor
    if spec.get("edge_min") is not None:
        edge_n = pd.to_numeric(df["edge"], errors="coerce") if "edge" in df.columns else pd.Series(float("nan"), index=idx)
        mask = mask & (edge_n >= spec["edge_min"])

    # Edge ceiling
    if spec.get("edge_max") is not None:
        edge_n = pd.to_numeric(df["edge"], errors="coerce") if "edge" in df.columns else pd.Series(float("nan"), index=idx)
        mask = mask & (edge_n < spec["edge_max"])

    # Selection filter
    if spec.get("selection") is not None:
        sel = df["selection"].astype(str).str.lower() if "selection" in df.columns else pd.Series("", index=idx)
        mask = mask & (sel == spec["selection"])

    # Combo filter
    if spec.get("combo") is not None:
        mt = df["market_type"].astype(str).str.lower() if "market_type" in df.columns else pd.Series("", index=idx)
        is_combo = mt.isin({m.lower() for m in _COMBO_MARKETS})
        mask = mask & (is_combo if spec["combo"] else ~is_combo)

    # Context filter
    ctx_filter = spec.get("context")
    if ctx_filter is not None:
        ctx_col = df["context_pick_alignment"].fillna("").astype(str).str.lower() if "context_pick_alignment" in df.columns else pd.Series("", index=idx)
        if ctx_filter.startswith("!"):
            # negated filter — e.g. "!aligned" means NOT aligned
            mask = mask & (ctx_col != ctx_filter[1:])
        else:
            mask = mask & (ctx_col == ctx_filter)

    # Replace NaN masks with False
    return mask.fillna(False)


# ---------------------------------------------------------------------------
# Historical validation per policy
# ---------------------------------------------------------------------------

def _validate_policy(
    graded_df: pd.DataFrame,
    spec: dict[str, Any],
    baseline_hr: float | None,
    baseline_roi: float | None,
    baseline_me: float | None,
) -> dict[str, Any]:
    mask    = _build_mask(graded_df, spec)
    flagged = graded_df[mask]
    rest    = graded_df[~mask]
    n_flagged = int(mask.sum())
    n_rest    = int(len(rest))

    flagged_hr  = _hit_rate(flagged)
    flagged_roi = _roi(flagged)
    flagged_me  = _mean_error(flagged)
    flagged_conf = _safe_mean(flagged.get("confidence", pd.Series(dtype=float)))
    flagged_edge = _safe_mean(flagged.get("edge",       pd.Series(dtype=float)))

    # P&L decomposition
    avoided_loss  = 0.0
    lost_profit   = 0.0
    if "shadow_roi" in flagged.columns:
        rvals = pd.to_numeric(flagged["shadow_roi"], errors="coerce").dropna()
        avoided_loss = round(float(rvals[rvals < 0].abs().sum()), 4)
        lost_profit  = round(float(rvals[rvals > 0].sum()), 4)
    net_effect = round(avoided_loss - lost_profit, 4)

    # Delta vs baseline
    delta_hr  = round(flagged_hr  - baseline_hr,  4) if (flagged_hr  is not None and baseline_hr  is not None) else None
    delta_roi = round(flagged_roi - baseline_roi, 4) if (flagged_roi is not None and baseline_roi is not None) else None

    rrs    = _risk_reduction_score(flagged_roi, baseline_roi)
    verdict = _policy_verdict(n_flagged, flagged_hr, flagged_roi, baseline_hr, baseline_roi)

    sample_warning: str | None = None
    if n_flagged < _MIN_SAMPLE:
        sample_warning = f"Only {n_flagged} graded rows — conclusions unreliable"
    elif n_flagged < _MIN_CONFIDENT:
        sample_warning = f"Only {n_flagged} graded rows — monitor for sample growth"

    return {
        "policy_name": spec["name"],
        "description": spec["description"],
        "rows_flagged": n_flagged,
        "graded_rows_flagged": n_flagged,
        "hit_rate_flagged": flagged_hr,
        "roi_flagged": flagged_roi,
        "avg_projection_error_flagged": flagged_me,
        "avg_edge_flagged": flagged_edge,
        "avg_confidence_flagged": flagged_conf,
        "delta_hit_rate": delta_hr,
        "delta_roi": delta_roi,
        "avoided_loss": avoided_loss,
        "lost_profit": lost_profit,
        "net_effect": net_effect,
        "profit_loss_if_suppressed": net_effect,
        "risk_reduction_score": rrs,
        "verdict": verdict,
        "sample_warning": sample_warning,
        "note": "shadow_validation_only_no_live_logic_changed",
    }


# ---------------------------------------------------------------------------
# Forward tracking: current slate
# ---------------------------------------------------------------------------

def _forward_track_policy(
    board_df: pd.DataFrame,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Apply policy mask to the current day's full-market board.

    Tracking only — no rows are removed or altered.
    """
    if board_df.empty:
        return {
            "policy_name": spec["name"],
            "current_rows_flagged": 0,
            "note": "board_not_available",
        }

    mask      = _build_mask(board_df, spec)
    flagged   = board_df[mask]
    n_flagged = int(mask.sum())

    players_flagged = sorted(set(flagged["player_name"].dropna().astype(str).tolist())) if "player_name" in flagged.columns else []
    markets_flagged = sorted(set(flagged["market_type"].dropna().astype(str).tolist())) if "market_type" in flagged.columns else []
    games_flagged   = sorted(set(flagged["game_id"].dropna().astype(str).tolist()))   if "game_id"     in flagged.columns else []

    avg_edge = _safe_mean(flagged.get("edge", pd.Series(dtype=float)))
    avg_conf = _safe_mean(flagged.get("confidence", pd.Series(dtype=float)))

    return {
        "policy_name": spec["name"],
        "current_rows_flagged": n_flagged,
        "current_players_flagged": players_flagged,
        "current_markets_flagged": markets_flagged,
        "current_games_flagged": games_flagged,
        "current_avg_edge": avg_edge,
        "current_avg_confidence": avg_conf,
        "current_expected_risk": (
            "HIGH" if n_flagged >= 5
            else "MODERATE" if n_flagged >= 2
            else "LOW"
        ),
        "shadow_tracking_note": (
            f"TRACKING_ONLY: {n_flagged} current-slate row(s) match policy criteria. "
            "No rows removed or suppressed."
        ),
    }


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_edge_containment_shadow_validation(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    board_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build Phase 12D edge containment shadow validation report.

    Shadow validation only — no live logic modified.
    """
    if shadow_history_df.empty:
        return _empty_report(prediction_date)

    shadow_history_df = shadow_history_df.copy()

    graded = shadow_history_df[
        shadow_history_df.get("result_status", pd.Series(dtype=str))
        .astype(str).str.lower().isin(_GRADED)
    ].copy()

    if graded.empty:
        return _empty_report(prediction_date)

    # Baseline stats
    n_baseline  = int(len(graded))
    baseline_hr  = _hit_rate(graded)
    baseline_roi = _roi(graded)
    baseline_me  = _mean_error(graded)
    baseline_conf = _safe_mean(graded.get("confidence", pd.Series(dtype=float)))

    baseline = {
        "total_graded_rows": n_baseline,
        "hit_rate": baseline_hr,
        "roi": baseline_roi,
        "avg_projection_error": baseline_me,
        "avg_confidence": baseline_conf,
    }

    # Validate each policy historically
    historical_results: dict[str, Any] = {}
    for spec in _POLICY_SPECS:
        historical_results[spec["name"]] = _validate_policy(
            graded, spec, baseline_hr, baseline_roi, baseline_me
        )

    # Forward tracking (current slate)
    forward_tracking: dict[str, Any] = {}
    if board_df is not None and not board_df.empty:
        board_df = board_df.copy()
        for spec in _POLICY_SPECS:
            forward_tracking[spec["name"]] = _forward_track_policy(board_df, spec)
    else:
        for spec in _POLICY_SPECS:
            forward_tracking[spec["name"]] = {
                "policy_name": spec["name"],
                "current_rows_flagged": 0,
                "note": "board_not_provided",
            }

    # Rankings
    ranked = sorted(
        [v for v in historical_results.values()],
        key=lambda x: (
            {"READY_FOR_LIVE_REVIEW_LATER": 0, "READY_FOR_FORWARD_SHADOW": 1,
             "PROMISING": 2, "MONITOR": 3, "REJECT_POLICY": 4,
             "INSUFFICIENT_SAMPLE": 5}.get(x.get("verdict", "INSUFFICIENT_SAMPLE"), 5),
            -(x.get("net_effect") or 0),
        ),
    )

    # Summary stats
    verdicts_count: dict[str, int] = {}
    for r in ranked:
        v = r.get("verdict", "INSUFFICIENT_SAMPLE")
        verdicts_count[v] = verdicts_count.get(v, 0) + 1

    top_policy = ranked[0] if ranked else {}
    total_current_flagged = sum(
        ft.get("current_rows_flagged", 0) for ft in forward_tracking.values()
    )

    # Executive summary
    actionable = [
        r for r in ranked
        if r.get("verdict") in ("READY_FOR_FORWARD_SHADOW", "READY_FOR_LIVE_REVIEW_LATER", "PROMISING")
    ]
    rejected = [
        r for r in ranked
        if r.get("verdict") in ("REJECT_POLICY", "INSUFFICIENT_SAMPLE")
    ]

    executive_summary = {
        "prediction_date": prediction_date,
        "total_policies_evaluated": len(_POLICY_SPECS),
        "policy_verdicts": verdicts_count,
        "top_policy": top_policy.get("policy_name"),
        "top_policy_verdict": top_policy.get("verdict"),
        "top_policy_net_effect": top_policy.get("net_effect"),
        "actionable_policies": [r["policy_name"] for r in actionable],
        "rejected_or_insufficient": [r["policy_name"] for r in rejected],
        "total_current_slate_rows_flagged": total_current_flagged,
        "recommendation": (
            "Continue forward-tracking flagged policies each slate. "
            "Do not apply live suppression until n >= 50 and evidence is consistent."
        ),
    }

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "notes": [
            "shadow_validation_only",
            "no_live_logic_changed",
            "no_projections_altered",
            "no_edges_modified",
            "no_kelly_sizing_changed",
            "no_board_rows_removed",
            "forward_tracking_is_observational_only",
        ],
        "baseline": baseline,
        "historical_validation": historical_results,
        "policy_rankings": [
            {
                "rank": i + 1,
                "policy_name": r["policy_name"],
                "verdict": r.get("verdict"),
                "n_flagged": r.get("rows_flagged"),
                "hit_rate": r.get("hit_rate_flagged"),
                "roi": r.get("roi_flagged"),
                "net_effect": r.get("net_effect"),
                "risk_reduction_score": r.get("risk_reduction_score"),
            }
            for i, r in enumerate(ranked)
        ],
        "forward_tracking": forward_tracking,
        "executive_summary": executive_summary,
    }


def _empty_report(prediction_date: str) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "baseline": {"total_graded_rows": 0},
        "historical_validation": {},
        "policy_rankings": [],
        "forward_tracking": {},
        "executive_summary": {"total_policies_evaluated": 0},
        "notes": ["history_empty"],
    }


# ---------------------------------------------------------------------------
# Operator text renderer
# ---------------------------------------------------------------------------

def _fp(v: float | None, p: int = 2) -> str:
    return f"{v:.{p}f}" if v is not None else "n/a"


def _fpc(v: float | None) -> str:
    return f"{v:.1%}" if v is not None else "n/a"


_VERDICT_DISPLAY = {
    "READY_FOR_LIVE_REVIEW_LATER": "LIVE_REVIEW_LATER",
    "READY_FOR_FORWARD_SHADOW": "FWD_SHADOW",
    "PROMISING":  "PROMISING",
    "MONITOR":    "MONITOR",
    "REJECT_POLICY": "REJECT",
    "INSUFFICIENT_SAMPLE": "N/A",
}


def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    bl   = payload.get("baseline", {})
    es   = payload.get("executive_summary", {})
    lines = [
        "Edge Containment Shadow Validation (Phase 12D — shadow only)",
        f"prediction_date       : {date}",
        "=" * 78,
        "",
        "EXECUTIVE SUMMARY",
        "-" * 78,
        f"  policies evaluated  : {es.get('total_policies_evaluated', 0)}",
        f"  baseline hit_rate   : {_fpc(bl.get('hit_rate'))}",
        f"  baseline roi        : {_fpc(bl.get('roi'))}",
        f"  baseline avg_error  : {_fp(bl.get('avg_projection_error'), 2)}",
        f"  baseline n          : {bl.get('total_graded_rows', 0)}",
        "",
    ]

    for verdict, count in sorted((es.get("policy_verdicts") or {}).items()):
        lines.append(f"  {verdict:<32} : {count}")
    lines.append("")

    actionable = es.get("actionable_policies", [])
    if actionable:
        lines.append(f"  Actionable policies : {', '.join(actionable)}")
    rejected = es.get("rejected_or_insufficient", [])
    if rejected:
        lines.append(f"  Rejected/N/A        : {', '.join(rejected)}")
    lines.append(f"  Recommendation: {es.get('recommendation', '')}")
    lines.append("")

    # Historical validation table
    lines += ["HISTORICAL POLICY VALIDATION", "=" * 78]
    hdr = "  {:<46}  {:>4}  {:>7}  {:>8}  {:>9}  {:>8}  {:<16}".format(
        "policy", "n", "hit", "roi", "net_saved", "rr_score", "verdict"
    )
    lines.append(hdr)
    lines.append("  " + "-" * 104)
    for row in payload.get("policy_rankings", []):
        lines.append(
            "  {:<46}  {:>4}  {:>7}  {:>8}  {:>9}  {:>8}  {:<16}".format(
                (row.get("policy_name") or "")[:46],
                row.get("n_flagged", 0),
                _fpc(row.get("hit_rate")),
                _fpc(row.get("roi")),
                _fp(row.get("net_effect"), 2),
                _fp(row.get("risk_reduction_score"), 1),
                _VERDICT_DISPLAY.get(row.get("verdict", ""), row.get("verdict", ""))[:16],
            )
        )
    lines.append("")

    # Policy details
    lines += ["POLICY DETAILS", "=" * 78]
    for name, v in payload.get("historical_validation", {}).items():
        if not isinstance(v, dict):
            continue
        verdict = v.get("verdict", "?")
        lines.append(f"  [{_VERDICT_DISPLAY.get(verdict, verdict):<18}]  {name}")
        lines.append(f"           {v.get('description','')}")
        lines.append(
            f"           n={v.get('rows_flagged',0)}  "
            f"hit={_fpc(v.get('hit_rate_flagged'))}  "
            f"roi={_fpc(v.get('roi_flagged'))}  "
            f"err={_fp(v.get('avg_projection_error_flagged'),2)}  "
            f"delta_roi={_fp(v.get('delta_roi'),4)}  "
            f"avoided={_fp(v.get('avoided_loss'),2)}  "
            f"lost_profit={_fp(v.get('lost_profit'),2)}  "
            f"net={_fp(v.get('net_effect'),2)}"
        )
        if v.get("sample_warning"):
            lines.append(f"           WARNING: {v.get('sample_warning')}")
        lines.append("")

    # Forward tracking
    lines += ["CURRENT SLATE FORWARD-TRACKING FLAGS", "=" * 78]
    lines.append("  (Tracking only — no rows removed or suppressed)\n")
    total = es.get("total_current_slate_rows_flagged", 0)
    lines.append(f"  Total flagged across all policies: {total}")
    lines.append("")
    for name, ft in payload.get("forward_tracking", {}).items():
        n = ft.get("current_rows_flagged", 0)
        if n == 0:
            continue
        players = ft.get("current_players_flagged", [])[:8]
        lines.append(
            f"  {name[:46]:<46}  flagged={n:>3}  risk={ft.get('current_expected_risk','?')}"
        )
        if players:
            lines.append(f"    players: {', '.join(players)}")
    lines.append("")

    # Cautions
    lines += ["CAUTIONS / LIMITATIONS", "-" * 78]
    lines.append("  - All policies based on 375 graded rows (April-May 2026 playoffs only)")
    lines.append("  - context_pick_alignment is 55% populated — context-based policies have reduced precision")
    lines.append("  - fragility_bucket is 0% populated — fragility-adjusted policies not yet possible")
    lines.append("  - n < 50 policies are monitored but NOT yet recommended for live review")
    lines.append("  - Forward-tracking is observational only; it does not affect any pick, bet, or output")
    lines.append("")
    lines.append("=" * 78)
    lines.append("No live betting behavior changed.")
    lines.append("Shadow validation only.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def containment_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "diagnostics"
        / f"edge_containment_shadow_validation_{date}.json"
    )


def containment_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "operator"
        / f"edge_containment_shadow_validation_{date}.txt"
    )


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def write_edge_containment_shadow_validation(
    shadow_history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    board_df: pd.DataFrame | None = None,
    pick_history_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write validation JSON + operator TXT. Returns (json_path, txt_path, payload).

    Shadow validation only — no live logic altered.
    """
    payload   = build_edge_containment_shadow_validation(
        shadow_history_df, prediction_date,
        board_df=board_df, pick_history_df=pick_history_df,
    )
    json_path = containment_json_path_for_date(prediction_date, runtime_root)
    txt_path  = containment_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")
    return json_path, txt_path, payload


__all__ = [
    "REPORT_VERSION",
    "build_edge_containment_shadow_validation",
    "containment_json_path_for_date",
    "containment_txt_path_for_date",
    "write_edge_containment_shadow_validation",
]
