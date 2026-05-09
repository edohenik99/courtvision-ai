"""
Phase 10: Fragility/Survivability Shadow Policy Simulation.

Simulates hypothetical policy rules against historical graded results WITHOUT
affecting live betting logic, elite selection, Kelly staking, or production outputs.

Research and simulation only — no scoring, selection, or staking changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


POLICY_SIMULATION_REPORT_VERSION = "1.0"
_GRADED_STATUSES = frozenset({"hit", "miss", "push"})
_VALID_BUCKETS = frozenset({"HIGH", "MEDIUM", "LOW"})

_INSUFFICIENT_SAMPLE_THRESHOLD = 50
_MEANINGFUL_LIFT_MIN = 0.02   # minimum lift to distinguish MONITOR from PROMISING
_READY_SAMPLE_THRESHOLD = 300  # rows needed for READY_FOR_LIVE_REVIEW

_COMBO_MARKETS = frozenset({
    "player_points_assists",
    "player_points_rebounds",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
})

# (description, type: "review" | "suppression" | "promotion")
_POLICY_META: dict[str, dict[str, str]] = {
    "POLICY_A": {
        "description": "Review HIGH fragility + HIGH confidence picks",
        "type": "review",
        "criteria_summary": "fragility_bucket==HIGH AND confidence>=0.70",
    },
    "POLICY_B": {
        "description": "Suppress HIGH fragility assists OVERs",
        "type": "suppression",
        "criteria_summary": "market_type==player_assists AND selection==over AND fragility_bucket==HIGH",
    },
    "POLICY_C": {
        "description": "Suppress LOW survivability combo OVERs",
        "type": "suppression",
        "criteria_summary": (
            "market_type in combo_markets AND selection==over AND survivability_bucket==LOW"
        ),
    },
    "POLICY_D": {
        "description": "Promote LOW fragility UNDERs",
        "type": "promotion",
        "criteria_summary": "fragility_bucket==LOW AND selection==under",
    },
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_mean(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return None
    return round(float(vals.mean()), 4)


def _hit_rate(seg: pd.DataFrame) -> float | None:
    if seg.empty:
        return None
    status = seg["result_status"].astype(str).str.lower()
    wins = int((status == "hit").sum())
    misses = int((status == "miss").sum())
    graded_hm = wins + misses
    return round(wins / graded_hm, 4) if graded_hm else None


def _roi(seg: pd.DataFrame) -> float | None:
    # Priority 1: stake-weighted portfolio ROI — sum(profit_loss) / sum(stake_amount).
    # Uses a joint mask so only rows where both values are present contribute, avoiding
    # the index-misalignment error that occurs when one series has NaN dropped and the
    # other does not.
    if "profit_loss" in seg.columns and "stake_amount" in seg.columns:
        pl = pd.to_numeric(seg["profit_loss"], errors="coerce")
        stakes = pd.to_numeric(seg["stake_amount"], errors="coerce")
        valid = pl.notna() & stakes.notna()
        if valid.any():
            total_stake = float(stakes[valid].sum())
            if total_stake > 0:
                return round(float(pl[valid].sum()) / total_stake, 4)
    # Priority 2: mean of shadow_roi (unit-stake per-bet fallback, uniform-stake assumption).
    if "shadow_roi" in seg.columns:
        vals = pd.to_numeric(seg["shadow_roi"], errors="coerce").dropna()
        if not vals.empty:
            return round(float(vals.mean()), 4)
    return None


def _filter_to_date(df: pd.DataFrame, cutoff_date: str) -> pd.DataFrame:
    """Return rows whose prediction_date is at or before cutoff_date.

    Prevents look-ahead bias: simulation for date T must never see rows from T+1
    onward.  Rows with a missing or unparseable prediction_date are excluded
    (strict isolation — we cannot confirm they are not from the future).

    If the DataFrame has no prediction_date column the original DataFrame is
    returned unchanged for backward compatibility with callers that pre-filter
    before passing.
    """
    if df.empty or "prediction_date" not in df.columns:
        return df
    try:
        cutoff = pd.Timestamp(cutoff_date)
    except Exception:
        return df  # unparseable cutoff — conservative: pass through unchanged
    row_dates = pd.to_datetime(df["prediction_date"], errors="coerce")
    valid = (row_dates <= cutoff).fillna(False)  # strict: exclude undatable rows
    return df[valid]


def _policy_stats(seg: pd.DataFrame) -> dict[str, Any]:
    status = seg["result_status"].astype(str).str.lower()
    wins = int((status == "hit").sum())
    losses = int((status == "miss").sum())
    pushes = int((status == "push").sum())
    return {
        "rows_flagged": int(len(seg)),
        "sample_size": int(len(seg)),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": _hit_rate(seg),
        "roi": _roi(seg),
        "avg_profit_loss": _safe_mean(seg["profit_loss"]) if "profit_loss" in seg.columns else None,
        "avg_edge": _safe_mean(seg["edge"]) if "edge" in seg.columns else None,
        "avg_confidence": _safe_mean(seg["confidence"]) if "confidence" in seg.columns else None,
    }


# ── policy filters ────────────────────────────────────────────────────────────

def _apply_policy_a(graded: pd.DataFrame) -> pd.DataFrame:
    """POLICY_A: Review HIGH fragility + HIGH confidence picks."""
    if "fragility_bucket" not in graded.columns or "confidence" not in graded.columns:
        return graded.iloc[0:0]  # empty with same columns
    frag_high = graded["fragility_bucket"].astype(str).str.upper() == "HIGH"
    conf_high = pd.to_numeric(graded["confidence"], errors="coerce") >= 0.70
    return graded[frag_high & conf_high]


def _apply_policy_b(graded: pd.DataFrame) -> pd.DataFrame:
    """POLICY_B: Suppress HIGH fragility assists OVERs."""
    if (
        "market_type" not in graded.columns
        or "selection" not in graded.columns
        or "fragility_bucket" not in graded.columns
    ):
        return graded.iloc[0:0]
    is_assists = graded["market_type"].astype(str).str.lower() == "player_assists"
    is_over = graded["selection"].astype(str).str.lower() == "over"
    frag_high = graded["fragility_bucket"].astype(str).str.upper() == "HIGH"
    return graded[is_assists & is_over & frag_high]


def _apply_policy_c(graded: pd.DataFrame) -> pd.DataFrame:
    """POLICY_C: Suppress LOW survivability combo OVERs."""
    if (
        "market_type" not in graded.columns
        or "selection" not in graded.columns
        or "survivability_bucket" not in graded.columns
    ):
        return graded.iloc[0:0]
    is_combo = graded["market_type"].astype(str).str.lower().isin(_COMBO_MARKETS)
    is_over = graded["selection"].astype(str).str.lower() == "over"
    surv_low = graded["survivability_bucket"].astype(str).str.upper() == "LOW"
    return graded[is_combo & is_over & surv_low]


def _apply_policy_d(graded: pd.DataFrame) -> pd.DataFrame:
    """POLICY_D: Promote LOW fragility UNDERs."""
    if "fragility_bucket" not in graded.columns or "selection" not in graded.columns:
        return graded.iloc[0:0]
    frag_low = graded["fragility_bucket"].astype(str).str.upper() == "LOW"
    is_under = graded["selection"].astype(str).str.lower() == "under"
    return graded[frag_low & is_under]


_POLICY_FILTERS = {
    "POLICY_A": _apply_policy_a,
    "POLICY_B": _apply_policy_b,
    "POLICY_C": _apply_policy_c,
    "POLICY_D": _apply_policy_d,
}


# ── verdict ───────────────────────────────────────────────────────────────────

def _determine_verdict(
    sample_size: int,
    policy_lift: float | None,
) -> str:
    """Map (sample_size, policy_lift) to a verdict label.

    policy_lift is the effectiveness signal in the direction the policy intends:
    - suppression/review: baseline_hit_rate - flagged_hit_rate  (positive = good)
    - promotion: flagged_hit_rate - baseline_hit_rate           (positive = good)
    """
    if sample_size < _INSUFFICIENT_SAMPLE_THRESHOLD or policy_lift is None:
        return "INSUFFICIENT_SAMPLE"
    if policy_lift < 0:
        return "REJECT_POLICY"
    if policy_lift < _MEANINGFUL_LIFT_MIN:
        return "MONITOR"
    # meaningful positive lift
    if sample_size >= _READY_SAMPLE_THRESHOLD:
        return "READY_FOR_LIVE_REVIEW"
    return "PROMISING"


# ── impact analysis ───────────────────────────────────────────────────────────

def _compute_portfolio_impact(
    graded: pd.DataFrame,
    flagged: pd.DataFrame,
    policy_type: str,
) -> dict[str, Any]:
    """Compute portfolio-level ROI impact of applying the policy."""
    n_total = int(len(graded))
    n_flagged = int(len(flagged))
    baseline_roi = _roi(graded)
    flagged_roi = _roi(flagged)

    if policy_type in ("suppression", "review"):
        remaining = graded[~graded.index.isin(flagged.index)]
        remaining_roi = _roi(remaining)
        delta_portfolio = (
            round(remaining_roi - baseline_roi, 4)
            if (remaining_roi is not None and baseline_roi is not None)
            else None
        )
        action_word = "suppressed" if policy_type == "suppression" else "flagged for review"
        if n_flagged == 0:
            narrative = f"No bets matched criteria — policy would have had no effect."
        else:
            roi_str = f"{flagged_roi:+.1%}" if flagged_roi is not None else "unknown"
            delta_str = f"{delta_portfolio:+.1%}" if delta_portfolio is not None else "unknown"
            narrative = (
                f"If this policy had been active historically, {n_flagged} bets would have been "
                f"{action_word}. {action_word.capitalize()} bets produced {roi_str} ROI. "
                f"Portfolio ROI would have {'improved' if (delta_portfolio or 0) > 0 else 'changed'} "
                f"by {delta_str}."
            )
        return {
            "total_graded_rows": n_total,
            "bets_affected": n_flagged,
            "affected_roi": flagged_roi,
            "baseline_portfolio_roi": baseline_roi,
            "portfolio_roi_without_flagged": remaining_roi,
            "portfolio_roi_delta": delta_portfolio,
            "narrative": narrative,
        }
    else:  # promotion
        if n_flagged == 0:
            narrative = "No bets matched criteria — promotion policy would have had no effect."
        else:
            promoted_roi = flagged_roi
            roi_str = f"{promoted_roi:+.1%}" if promoted_roi is not None else "unknown"
            base_str = f"{baseline_roi:+.1%}" if baseline_roi is not None else "unknown"
            narrative = (
                f"If this policy had been active historically, {n_flagged} bets would have been "
                f"promoted. Promoted bets produced {roi_str} ROI vs portfolio baseline {base_str}."
            )
        return {
            "total_graded_rows": n_total,
            "bets_affected": n_flagged,
            "affected_roi": flagged_roi,
            "baseline_portfolio_roi": baseline_roi,
            "portfolio_roi_without_flagged": None,
            "portfolio_roi_delta": None,
            "narrative": narrative,
        }


# ── per-policy evaluation ─────────────────────────────────────────────────────

def _evaluate_policy(
    policy_key: str,
    graded: pd.DataFrame,
    baseline_hit_rate: float | None,
    baseline_roi: float | None,
) -> dict[str, Any]:
    meta = _POLICY_META[policy_key]
    policy_type = meta["type"]
    apply_fn = _POLICY_FILTERS[policy_key]

    flagged = apply_fn(graded)
    stats = _policy_stats(flagged)

    flagged_hr = stats["hit_rate"]
    flagged_roi = stats["roi"]

    delta_hit_rate = (
        round(flagged_hr - (baseline_hit_rate or 0.0), 4)
        if flagged_hr is not None and baseline_hit_rate is not None
        else None
    )
    delta_roi = (
        round(flagged_roi - (baseline_roi or 0.0), 4)
        if flagged_roi is not None and baseline_roi is not None
        else None
    )

    # policy_lift is positive when the policy criterion is correctly identifying the target group
    if policy_type in ("suppression", "review"):
        # want flagged rows to underperform → lift = how much they underperform
        policy_lift = (
            round((baseline_hit_rate or 0.0) - (flagged_hr or 0.0), 4)
            if (baseline_hit_rate is not None and flagged_hr is not None)
            else None
        )
    else:  # promotion
        # want flagged rows to outperform → lift = how much they outperform
        policy_lift = delta_hit_rate

    verdict = _determine_verdict(stats["sample_size"], policy_lift)

    impact = _compute_portfolio_impact(graded, flagged, policy_type)

    return {
        "policy": policy_key,
        "description": meta["description"],
        "type": policy_type,
        "criteria_summary": meta["criteria_summary"],
        **stats,
        "baseline_hit_rate": baseline_hit_rate,
        "baseline_roi": baseline_roi,
        "delta_hit_rate": delta_hit_rate,
        "delta_roi": delta_roi,
        "policy_lift": policy_lift,
        "impact_analysis": impact,
        "verdict": verdict,
    }


# ── operator narrative ────────────────────────────────────────────────────────

def _fmt_hr(val: float | None) -> str:
    return f"{val:.1%}" if val is not None else "n/a"


def _fmt_roi(val: float | None) -> str:
    return f"{val:+.1%}" if val is not None else "n/a"


def _build_operator_narrative(
    policies: dict[str, dict[str, Any]],
    baseline_hit_rate: float | None,
    total_graded: int,
) -> list[str]:
    lines: list[str] = []
    base_str = _fmt_hr(baseline_hit_rate)
    lines.append(f"Baseline: {total_graded} graded rows, global hit rate = {base_str}")
    lines.append("")

    # Per-policy summary
    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        p = policies.get(key, {})
        verdict = p.get("verdict", "INSUFFICIENT_SAMPLE")
        n = p.get("sample_size", 0)
        hr = p.get("hit_rate")
        dr = p.get("delta_hit_rate")
        roi = p.get("roi")
        desc = p.get("description", key)
        impact_note = p.get("impact_analysis", {}).get("narrative", "")

        lines.append(f"{key}: {desc}")
        lines.append(f"  verdict: {verdict}  |  n={n}  hit_rate={_fmt_hr(hr)}  delta={_fmt_hr(dr)}  roi={_fmt_roi(roi)}")
        if impact_note:
            lines.append(f"  {impact_note}")
        lines.append("")

    # Targeted commentary
    lines.append("--- Operator Analysis ---")
    lines.append("")

    # POLICY_A: fragility/confidence conflicts
    pa = policies.get("POLICY_A", {})
    pa_verdict = pa.get("verdict", "INSUFFICIENT_SAMPLE")
    pa_n = pa.get("sample_size", 0)
    pa_hr = pa.get("hit_rate")
    if pa_n == 0:
        lines.append(
            "Confidence/Fragility Conflicts (POLICY_A): No historical HIGH fragility + HIGH "
            "confidence bets found. Cannot assess conflict danger yet."
        )
    elif pa_verdict == "INSUFFICIENT_SAMPLE":
        lines.append(
            f"Confidence/Fragility Conflicts (POLICY_A): Only {pa_n} rows — sample too small "
            "to draw conclusions. Conflicts exist but are not yet statistically meaningful."
        )
    elif pa_verdict in ("PROMISING", "READY_FOR_LIVE_REVIEW"):
        lines.append(
            f"Confidence/Fragility Conflicts (POLICY_A): HIGH fragility + HIGH confidence bets "
            f"hit at {_fmt_hr(pa_hr)} vs {_fmt_hr(baseline_hit_rate)} baseline. "
            "These conflicts appear DANGEROUS — high model confidence does not overcome fragility. "
            "Review holds appear justified."
        )
    elif pa_verdict == "REJECT_POLICY":
        lines.append(
            f"Confidence/Fragility Conflicts (POLICY_A): HIGH fragility + HIGH confidence bets "
            f"hit at {_fmt_hr(pa_hr)}. No meaningful underperformance vs baseline — "
            "conflicts are NOT currently dangerous. Review holds NOT justified by data."
        )
    else:
        lines.append(
            f"Confidence/Fragility Conflicts (POLICY_A): n={pa_n}, hit_rate={_fmt_hr(pa_hr)}. "
            "Weak signal — insufficient evidence to conclude conflicts are dangerous."
        )
    lines.append("")

    # POLICY_B: assists OVER suppression
    pb = policies.get("POLICY_B", {})
    pb_verdict = pb.get("verdict", "INSUFFICIENT_SAMPLE")
    pb_n = pb.get("sample_size", 0)
    pb_hr = pb.get("hit_rate")
    pb_impact = pb.get("impact_analysis", {})
    pb_portfolio_delta = pb_impact.get("portfolio_roi_delta")
    if pb_n == 0:
        lines.append(
            "Assists OVER Suppression (POLICY_B): No historical player_assists OVER + HIGH "
            "fragility bets found. Cannot assess suppression value yet."
        )
    elif pb_verdict == "INSUFFICIENT_SAMPLE":
        lines.append(
            f"Assists OVER Suppression (POLICY_B): Only {pb_n} rows — not enough data to "
            "justify or reject suppression. Monitor as history accumulates."
        )
    elif pb_verdict in ("PROMISING", "READY_FOR_LIVE_REVIEW"):
        delta_str = _fmt_roi(pb_portfolio_delta)
        lines.append(
            f"Assists OVER Suppression (POLICY_B): JUSTIFIED. HIGH fragility assists OVERs "
            f"hit at {_fmt_hr(pb_hr)} vs {_fmt_hr(baseline_hit_rate)} baseline. "
            f"Suppression would improve portfolio ROI by {delta_str}. "
            f"Verdict: {pb_verdict}."
        )
    elif pb_verdict == "REJECT_POLICY":
        lines.append(
            f"Assists OVER Suppression (POLICY_B): NOT JUSTIFIED. HIGH fragility assists OVERs "
            f"hit at {_fmt_hr(pb_hr)} — no meaningful underperformance vs baseline. "
            "Suppression would not improve outcomes."
        )
    else:
        lines.append(
            f"Assists OVER Suppression (POLICY_B): Weak signal (n={pb_n}, hit={_fmt_hr(pb_hr)}). "
            "Likely noise — do not suppress yet."
        )
    lines.append("")

    # POLICY_C: combo OVER suppression
    pc = policies.get("POLICY_C", {})
    pc_verdict = pc.get("verdict", "INSUFFICIENT_SAMPLE")
    pc_n = pc.get("sample_size", 0)
    pc_hr = pc.get("hit_rate")
    pc_impact = pc.get("impact_analysis", {})
    pc_portfolio_delta = pc_impact.get("portfolio_roi_delta")
    if pc_n == 0:
        lines.append(
            "LOW Survivability Combo OVER Suppression (POLICY_C): No matching historical rows "
            "found. Cannot assess suppression value yet."
        )
    elif pc_verdict == "INSUFFICIENT_SAMPLE":
        lines.append(
            f"LOW Survivability Combo OVER Suppression (POLICY_C): Only {pc_n} rows — "
            "insufficient sample. Monitor as history accumulates."
        )
    elif pc_verdict in ("PROMISING", "READY_FOR_LIVE_REVIEW"):
        delta_str = _fmt_roi(pc_portfolio_delta)
        lines.append(
            f"LOW Survivability Combo OVER Suppression (POLICY_C): JUSTIFIED. LOW survivability "
            f"combo OVERs hit at {_fmt_hr(pc_hr)} vs {_fmt_hr(baseline_hit_rate)} baseline. "
            f"Suppression would improve portfolio ROI by {delta_str}. Verdict: {pc_verdict}."
        )
    elif pc_verdict == "REJECT_POLICY":
        lines.append(
            f"LOW Survivability Combo OVER Suppression (POLICY_C): NOT JUSTIFIED. LOW "
            f"survivability combo OVERs hit at {_fmt_hr(pc_hr)} — no meaningful underperformance. "
            "Suppression not warranted."
        )
    else:
        lines.append(
            f"LOW Survivability Combo OVER Suppression (POLICY_C): Weak signal "
            f"(n={pc_n}, hit={_fmt_hr(pc_hr)}). Likely noise — do not suppress yet."
        )
    lines.append("")

    # POLICY_D: LOW fragility UNDER promotion
    pd_ = policies.get("POLICY_D", {})
    pd_verdict = pd_.get("verdict", "INSUFFICIENT_SAMPLE")
    pd_n = pd_.get("sample_size", 0)
    pd_hr = pd_.get("hit_rate")
    if pd_n == 0:
        lines.append(
            "LOW Fragility UNDER Promotion (POLICY_D): No historical LOW fragility UNDER bets "
            "found. Cannot assess promotion value yet."
        )
    elif pd_verdict == "INSUFFICIENT_SAMPLE":
        lines.append(
            f"LOW Fragility UNDER Promotion (POLICY_D): Only {pd_n} rows — insufficient sample. "
            "Monitor as history accumulates."
        )
    elif pd_verdict in ("PROMISING", "READY_FOR_LIVE_REVIEW"):
        lines.append(
            f"LOW Fragility UNDER Promotion (POLICY_D): PROMISING. LOW fragility UNDERs hit at "
            f"{_fmt_hr(pd_hr)} vs {_fmt_hr(baseline_hit_rate)} baseline. "
            "These picks OUTPERFORM — future promotion deserves live consideration. "
            f"Verdict: {pd_verdict}."
        )
    elif pd_verdict == "REJECT_POLICY":
        lines.append(
            f"LOW Fragility UNDER Promotion (POLICY_D): NOT JUSTIFIED. LOW fragility UNDERs "
            f"hit at {_fmt_hr(pd_hr)} — no outperformance vs baseline. "
            "Promotion not warranted by current data."
        )
    else:
        lines.append(
            f"LOW Fragility UNDER Promotion (POLICY_D): Weak positive signal "
            f"(n={pd_n}, hit={_fmt_hr(pd_hr)}). Keep monitoring."
        )
    lines.append("")

    # Overall recommendation
    live_candidates = [
        k for k, p in policies.items() if p.get("verdict") == "READY_FOR_LIVE_REVIEW"
    ]
    promising = [
        k for k, p in policies.items() if p.get("verdict") == "PROMISING"
    ]
    noise = [
        k for k, p in policies.items()
        if p.get("verdict") in ("REJECT_POLICY", "INSUFFICIENT_SAMPLE")
    ]

    lines.append("--- Overall Recommendation ---")
    if live_candidates:
        lines.append(
            f"READY FOR LIVE CONSIDERATION: {', '.join(live_candidates)} — "
            "promote to live review queue."
        )
    if promising:
        lines.append(
            f"PROMISING (needs more data): {', '.join(promising)} — "
            "accumulate more history before promoting."
        )
    if noise:
        lines.append(
            f"LIKELY NOISE / INSUFFICIENT DATA: {', '.join(noise)} — "
            "do not change live behavior."
        )
    if not live_candidates and not promising:
        lines.append(
            "No policies currently meet the threshold for live consideration. "
            "Continue accumulating graded history."
        )
    lines.append("")
    lines.append("NOTE: Simulation only — no live betting logic, gates, or thresholds changed.")
    return lines


# ── main build function ───────────────────────────────────────────────────────

def build_policy_simulation_report(
    history_df: pd.DataFrame,
    prediction_date: str,
) -> dict[str, Any]:
    """Build the Phase 10 shadow policy simulation report.

    Simulation only — no scoring, selection, or staking values are altered.
    """
    if history_df.empty:
        return {
            "report_version": POLICY_SIMULATION_REPORT_VERSION,
            "prediction_date": prediction_date,
            "baseline": {
                "total_graded_rows": 0,
                "baseline_hit_rate": None,
                "baseline_roi": None,
            },
            "policies": {
                k: {
                    "policy": k,
                    "description": v["description"],
                    "type": v["type"],
                    "criteria_summary": v["criteria_summary"],
                    "rows_flagged": 0,
                    "sample_size": 0,
                    "wins": 0,
                    "losses": 0,
                    "pushes": 0,
                    "hit_rate": None,
                    "roi": None,
                    "avg_profit_loss": None,
                    "avg_edge": None,
                    "avg_confidence": None,
                    "baseline_hit_rate": None,
                    "baseline_roi": None,
                    "delta_hit_rate": None,
                    "delta_roi": None,
                    "policy_lift": None,
                    "impact_analysis": {
                        "total_graded_rows": 0,
                        "bets_affected": 0,
                        "affected_roi": None,
                        "baseline_portfolio_roi": None,
                        "portfolio_roi_without_flagged": None,
                        "portfolio_roi_delta": None,
                        "narrative": "No historical data available — cannot simulate.",
                    },
                    "verdict": "INSUFFICIENT_SAMPLE",
                }
                for k, v in _POLICY_META.items()
            },
            "operator_summary": ["No historical data available — cannot simulate policies."],
            "notes": [
                "simulation_only",
                "no_live_logic_changed",
                "history_empty",
            ],
        }

    # Apply strict date isolation BEFORE any analysis: rows from dates after
    # prediction_date are excluded to prevent look-ahead bias.  Source DataFrame
    # is never mutated — _filter_to_date returns a filtered view, and graded is
    # then an independent copy.
    df = _filter_to_date(history_df, prediction_date)
    status_col = df.get("result_status", pd.Series("", index=df.index)).astype(str).str.lower()
    graded = df[status_col.isin(_GRADED_STATUSES)].copy()

    total_graded = int(len(graded))
    baseline_hit_rate = _hit_rate(graded)
    baseline_roi = _roi(graded)

    baseline: dict[str, Any] = {
        "total_graded_rows": total_graded,
        "baseline_hit_rate": baseline_hit_rate,
        "baseline_roi": baseline_roi,
    }

    policies: dict[str, Any] = {}
    for key in _POLICY_META:
        policies[key] = _evaluate_policy(key, graded, baseline_hit_rate, baseline_roi)

    operator_summary = _build_operator_narrative(policies, baseline_hit_rate, total_graded)

    return {
        "report_version": POLICY_SIMULATION_REPORT_VERSION,
        "prediction_date": prediction_date,
        "baseline": baseline,
        "policies": policies,
        "operator_summary": operator_summary,
        "notes": [
            "simulation_only",
            "no_live_logic_changed",
            "no_selection_thresholds_changed",
            "no_kelly_stakes_changed",
            "no_elite_gates_changed",
        ],
    }


# ── path helpers ──────────────────────────────────────────────────────────────

def simulation_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "diagnostics"
        / f"fragility_shadow_policy_simulation_{date}.json"
    )


def simulation_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return (
        Path(runtime_root)
        / "operator"
        / f"fragility_shadow_policy_simulation_{date}.txt"
    )


# ── file writers ──────────────────────────────────────────────────────────────

def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    baseline = payload.get("baseline", {})
    total = baseline.get("total_graded_rows", 0)
    base_hr = baseline.get("baseline_hit_rate")
    base_roi = baseline.get("baseline_roi")

    lines = [
        f"Fragility/Survivability Shadow Policy Simulation (Phase 10 — simulation only)",
        f"prediction_date: {date}",
        "=" * 72,
        f"baseline_graded_rows: {total}",
        f"baseline_hit_rate: {_fmt_hr(base_hr)}",
        f"baseline_roi: {_fmt_roi(base_roi)}",
        "",
        "Policy Verdicts",
        "-" * 72,
    ]

    for key in ("POLICY_A", "POLICY_B", "POLICY_C", "POLICY_D"):
        p = payload.get("policies", {}).get(key, {})
        verdict = p.get("verdict", "INSUFFICIENT_SAMPLE")
        n = p.get("sample_size", 0)
        hr = p.get("hit_rate")
        delta_hr = p.get("delta_hit_rate")
        roi = p.get("roi")
        desc = p.get("description", key)
        lines.append(f"{key}: {desc}")
        lines.append(
            f"  verdict={verdict}  n={n}  hit_rate={_fmt_hr(hr)}"
            f"  delta_hit_rate={_fmt_hr(delta_hr)}  roi={_fmt_roi(roi)}"
        )
        narrative = p.get("impact_analysis", {}).get("narrative", "")
        if narrative:
            lines.append(f"  Impact: {narrative}")
        lines.append("")

    lines.extend(["", "Operator Commentary", "-" * 72])
    lines.extend(payload.get("operator_summary", []))
    lines.append("")
    lines.append(
        "NOTE: Simulation only — no scoring, selection, staking, or production "
        "outputs changed."
    )
    return "\n".join(lines) + "\n"


def write_policy_simulation_report(
    history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, dict[str, Any]]:
    """Write policy simulation JSON + operator TXT. Returns (json_path, txt_path, payload).

    Simulation only — no scoring, selection, or staking values are altered.
    """
    payload = build_policy_simulation_report(history_df, prediction_date)

    json_path = simulation_json_path_for_date(prediction_date, runtime_root)
    txt_path = simulation_txt_path_for_date(prediction_date, runtime_root)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")

    return json_path, txt_path, payload


__all__ = [
    "POLICY_SIMULATION_REPORT_VERSION",
    "_filter_to_date",
    "build_policy_simulation_report",
    "simulation_json_path_for_date",
    "simulation_txt_path_for_date",
    "write_policy_simulation_report",
]
