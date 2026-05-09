"""
Fragility/Survivability Outcome Validation Report.

Measures whether fragility and survivability scores predict actual betting outcome quality
by summarising graded history rows grouped by fragility and survivability buckets.

Reporting and measurement only — no scoring, selection, or staking changes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

VALIDATION_REPORT_VERSION = "1.0"
_GRADED_STATUSES = {"hit", "miss", "push"}
_VALID_BUCKETS = {"HIGH", "MEDIUM", "LOW"}


def _safe_mean(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    if vals.empty:
        return None
    return round(float(vals.mean()), 4)


def _compute_roi(group: pd.DataFrame) -> float | None:
    """ROI from shadow_roi column (unit-stake per-bet ROI)."""
    if "shadow_roi" not in group.columns:
        return None
    roi_vals = pd.to_numeric(group["shadow_roi"], errors="coerce").dropna()
    if roi_vals.empty:
        return None
    return round(float(roi_vals.mean()), 4)


def _group_stats(seg: pd.DataFrame) -> dict[str, Any]:
    status = seg["result_status"].astype(str).str.lower()
    wins = int((status == "hit").sum())
    losses = int((status == "miss").sum())
    pushes = int((status == "push").sum())
    graded = wins + losses
    return {
        "sample_size": int(len(seg)),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": round(wins / graded, 4) if graded else None,
        "roi": _compute_roi(seg),
        "avg_profit_loss": _safe_mean(seg.get("profit_loss", pd.Series(dtype=float))),
        "avg_edge": _safe_mean(seg.get("edge", pd.Series(dtype=float))),
        "avg_confidence": _safe_mean(seg.get("confidence", pd.Series(dtype=float))),
    }


def _bucket_breakdown(
    graded: pd.DataFrame,
    group_cols: list[str],
) -> list[dict[str, Any]]:
    """Break graded rows down by the given group columns."""
    available = [c for c in group_cols if c in graded.columns]
    if not available:
        return []

    rows: list[dict[str, Any]] = []
    for keys, seg in graded.groupby(available, sort=True, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        key_dict = dict(zip(available, keys))
        # Skip rows where bucket values are null/invalid
        bucket_vals = {k: str(v).strip().upper() for k, v in key_dict.items() if "bucket" in k}
        if any(v not in _VALID_BUCKETS for v in bucket_vals.values()):
            continue
        entry: dict[str, Any] = {**{k: str(v) for k, v in key_dict.items()}, **_group_stats(seg)}
        rows.append(entry)
    return rows


def _market_summary(graded: pd.DataFrame) -> list[dict[str, Any]]:
    """Summarise by market_type + fragility_bucket."""
    cols = ["market_type", "fragility_bucket", "survivability_bucket", "selection"]
    available = [c for c in cols if c in graded.columns]
    if "market_type" not in available or "fragility_bucket" not in available:
        return []
    rows: list[dict[str, Any]] = []
    for keys, seg in graded.groupby(available, sort=True, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        key_dict = dict(zip(available, keys))
        frag = str(key_dict.get("fragility_bucket", "")).strip().upper()
        if frag not in _VALID_BUCKETS:
            continue
        entry: dict[str, Any] = {**{k: str(v) for k, v in key_dict.items()}, **_group_stats(seg)}
        rows.append(entry)
    return rows


def _operator_verdict(
    global_hit_rate: float | None,
    by_frag: list[dict[str, Any]],
    by_surv: list[dict[str, Any]],
    by_market: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    if global_hit_rate is None:
        lines.append("No graded rows — cannot evaluate fragility/survivability predictive power.")
        return lines

    lines.append(f"Global hit rate across all graded rows: {global_hit_rate:.1%}")
    lines.append("")

    # Fragility bucket performance
    if by_frag:
        lines.append("Fragility Bucket Performance:")
        for row in sorted(by_frag, key=lambda r: r.get("fragility_bucket", "")):
            bucket = row.get("fragility_bucket", "?")
            hr = row.get("hit_rate")
            n = row.get("sample_size", 0)
            roi = row.get("roi")
            if hr is None:
                lines.append(f"  {bucket}: n={n}  hit_rate=n/a  roi=n/a")
            else:
                delta = hr - global_hit_rate
                direction = "+" if delta >= 0 else ""
                roi_str = f"{roi:+.1%}" if roi is not None else "n/a"
                lines.append(
                    f"  {bucket}: n={n}  hit_rate={hr:.1%} ({direction}{delta:.1%} vs baseline)"
                    f"  roi={roi_str}"
                )
        lines.append("")

        low_row = next((r for r in by_frag if r.get("fragility_bucket", "").upper() == "LOW"), None)
        high_row = next((r for r in by_frag if r.get("fragility_bucket", "").upper() == "HIGH"), None)
        if low_row and high_row:
            low_hr = low_row.get("hit_rate") or 0.0
            high_hr = high_row.get("hit_rate") or 0.0
            if low_hr > global_hit_rate + 0.02:
                lines.append("  VERDICT: LOW fragility picks OUTPERFORM baseline. Signal is present.")
            elif high_hr < global_hit_rate - 0.02:
                lines.append("  VERDICT: HIGH fragility picks UNDERPERFORM. Dangerous bucket confirmed.")
            else:
                lines.append("  VERDICT: No meaningful fragility separation detected yet.")
            lines.append("")

    # Survivability bucket performance
    if by_surv:
        lines.append("Survivability Bucket Performance:")
        for row in sorted(by_surv, key=lambda r: r.get("survivability_bucket", "")):
            bucket = row.get("survivability_bucket", "?")
            hr = row.get("hit_rate")
            n = row.get("sample_size", 0)
            roi = row.get("roi")
            if hr is None:
                lines.append(f"  {bucket}: n={n}  hit_rate=n/a  roi=n/a")
            else:
                delta = hr - global_hit_rate
                direction = "+" if delta >= 0 else ""
                roi_str = f"{roi:+.1%}" if roi is not None else "n/a"
                lines.append(
                    f"  {bucket}: n={n}  hit_rate={hr:.1%} ({direction}{delta:.1%} vs baseline)"
                    f"  roi={roi_str}"
                )
        lines.append("")

        high_surv = next((r for r in by_surv if r.get("survivability_bucket", "").upper() == "HIGH"), None)
        if high_surv:
            hs_hr = high_surv.get("hit_rate") or 0.0
            if hs_hr > global_hit_rate + 0.02:
                lines.append("  VERDICT: HIGH survivability picks OUTPERFORM. Survivability is predictive.")
            else:
                lines.append("  VERDICT: HIGH survivability picks show no clear lift yet.")
            lines.append("")

    # Market-level suppression signals
    if by_market:
        lines.append("Markets with HIGH fragility — suppression candidates:")
        suppression_markets: list[str] = []
        trust_markets: list[str] = []
        for row in by_market:
            frag = row.get("fragility_bucket", "").upper()
            market = row.get("market_type", "?")
            hr = row.get("hit_rate")
            n = row.get("sample_size", 0)
            if frag != "HIGH" or n < 10 or hr is None:
                continue
            if hr < global_hit_rate - 0.05:
                suppression_markets.append(
                    f"  SUPPRESS: {market} / HIGH fragility  n={n}  hit_rate={hr:.1%}"
                )
            elif hr > global_hit_rate + 0.05:
                trust_markets.append(
                    f"  TRUST:    {market} / HIGH fragility  n={n}  hit_rate={hr:.1%}"
                )
        if suppression_markets:
            lines.extend(suppression_markets)
        else:
            lines.append("  No markets clearly meeting suppression criteria yet.")
        if trust_markets:
            lines.append("Markets with HIGH fragility that are still performing:")
            lines.extend(trust_markets)
        lines.append("")

    lines.append("NOTE: Diagnostics only. No betting logic, gates, or thresholds changed.")
    return lines


def build_fragility_outcome_validation(
    history_df: pd.DataFrame,
    prediction_date: str,
) -> dict[str, Any]:
    """Build the fragility outcome validation payload from a graded history DataFrame."""
    if history_df.empty:
        return {
            "report_version": VALIDATION_REPORT_VERSION,
            "prediction_date": prediction_date,
            "total_graded_rows": 0,
            "rows_with_fragility": 0,
            "global_hit_rate": None,
            "by_fragility_bucket": [],
            "by_survivability_bucket": [],
            "by_market_fragility": [],
            "by_market_selection_fragility_survivability": [],
            "operator_verdict": ["No history rows — cannot validate."],
            "notes": ["history_empty"],
        }

    status_col = history_df.get(
        "result_status", pd.Series("", index=history_df.index)
    ).astype(str).str.lower()
    graded = history_df[status_col.isin(_GRADED_STATUSES)].copy()

    rows_with_fragility = 0
    if "fragility_bucket" in history_df.columns:
        rows_with_fragility = int(
            history_df["fragility_bucket"].astype(str).str.strip().str.upper().isin(_VALID_BUCKETS).sum()
        )

    if graded.empty:
        return {
            "report_version": VALIDATION_REPORT_VERSION,
            "prediction_date": prediction_date,
            "total_graded_rows": 0,
            "rows_with_fragility": rows_with_fragility,
            "global_hit_rate": None,
            "by_fragility_bucket": [],
            "by_survivability_bucket": [],
            "by_market_fragility": [],
            "by_market_selection_fragility_survivability": [],
            "operator_verdict": ["No graded rows — cannot validate."],
            "notes": ["no_graded_rows"],
        }

    status = graded["result_status"].astype(str).str.lower()
    wins = int((status == "hit").sum())
    total_graded = int(len(graded))
    graded_hm = wins + int((status == "miss").sum())
    global_hit_rate = round(wins / graded_hm, 4) if graded_hm else None

    by_frag = _bucket_breakdown(graded, ["fragility_bucket"])
    by_surv = _bucket_breakdown(graded, ["survivability_bucket"])
    by_market = _market_summary(graded)
    by_full = _bucket_breakdown(
        graded,
        ["market_type", "selection", "fragility_bucket", "survivability_bucket"],
    )

    verdict_lines = _operator_verdict(global_hit_rate, by_frag, by_surv, by_market)

    return {
        "report_version": VALIDATION_REPORT_VERSION,
        "prediction_date": prediction_date,
        "total_graded_rows": total_graded,
        "rows_with_fragility": rows_with_fragility,
        "global_hit_rate": global_hit_rate,
        "by_fragility_bucket": by_frag,
        "by_survivability_bucket": by_surv,
        "by_market_fragility": by_market,
        "by_market_selection_fragility_survivability": by_full,
        "operator_verdict": verdict_lines,
        "notes": ["diagnostics_only", "no_betting_logic_changed"],
    }


def validation_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"fragility_outcome_validation_{date}.json"


def validation_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"fragility_outcome_validation_{date}.txt"


def _render_operator_report(payload: dict[str, Any]) -> str:
    date = payload.get("prediction_date", "unknown")
    total = payload.get("total_graded_rows", 0)
    rows_frag = payload.get("rows_with_fragility", 0)
    global_hr = payload.get("global_hit_rate")

    lines = [
        f"Fragility/Survivability Outcome Validation — {date}",
        "=" * 72,
        f"Total graded rows: {total}",
        f"Rows with fragility diagnostics: {rows_frag}",
        f"Global hit rate: {global_hr:.1%}" if global_hr is not None else "Global hit rate: n/a",
        "",
    ]
    lines.extend(payload.get("operator_verdict", []))
    lines.append("")
    lines.append("NOTE: Diagnostics only — no scoring, selection, or staking changes.")
    return "\n".join(lines) + "\n"


def write_fragility_outcome_validation(
    history_df: pd.DataFrame,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, dict[str, Any]]:
    """Write JSON + TXT outcome validation reports. Returns (json_path, txt_path, payload)."""
    payload = build_fragility_outcome_validation(history_df, prediction_date)

    json_path = validation_json_path_for_date(prediction_date, runtime_root)
    txt_path = validation_txt_path_for_date(prediction_date, runtime_root)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    txt_path.write_text(_render_operator_report(payload), encoding="utf-8")

    return json_path, txt_path, payload


__all__ = [
    "VALIDATION_REPORT_VERSION",
    "build_fragility_outcome_validation",
    "validation_json_path_for_date",
    "validation_txt_path_for_date",
    "write_fragility_outcome_validation",
]
