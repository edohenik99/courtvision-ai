"""Meta-Label Promotion diagnostics and reports.

Report-only analytics over candidate scoring rules.
This module does not change projections, selection logic, Elite gates, Kelly
sizing, or final decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.meta_label_promotion import (
    DIAGNOSTIC_ONLY_NOTE,
    FEATURES_VERSION,
    MODEL_VERSION,
    apply_meta_label_promotion,
)
from courtvision.reporting.shadow_artifact_metadata import apply_shadow_report_metadata

REPORT_VERSION = "1.0"
GENERATED_BY = "courtvision.reporting.meta_label_promotion.write_meta_label_promotion_report"

REPORT_FIELDS: tuple[str, ...] = (
    "prediction_date",
    "player_id",
    "player_name",
    "game_id",
    "market_type",
    "selection",
    "line",
    "odds",
    "edge",
    "confidence",
    "quality_score",
    "role_stability_bucket",
    "context_pick_alignment",
    "context_caution_level",
    "meta_label_rules_score",
    "meta_label_bucket",
    "meta_label_status",
    "reason_codes",
    "missing_feature_warnings",
    "features_version",
    "model_version",
    "diagnostic_only_note",
)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_meta_label_promotion_report(
    prediction_date: str,
    full_market_df: pd.DataFrame | None = None,
    role_payload: dict[str, Any] | None = None,
    cal_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Meta-Label Promotion diagnostics from candidate data."""
    if full_market_df is None or full_market_df.empty:
        return {
            "report_version": REPORT_VERSION,
            "prediction_date": prediction_date,
            "scope": "meta_label_promotion_shadow",
            "notes": [
                "diagnostic_report_only",
                "no_prediction_logic_changed",
                "no_elite_gates_changed",
                "no_kelly_sizing_changed",
                "no_final_decision_changed",
                DIAGNOSTIC_ONLY_NOTE,
            ],
            "summary": {
                "total_rows_evaluated": 0,
                "shadow_strong_review_candidate_count": 0,
                "shadow_watch_candidate_count": 0,
                "shadow_neutral_count": 0,
                "shadow_weak_count": 0,
                "shadow_avoid_review_count": 0,
                "top_strong_candidates": [],
                "readiness": "review_only",
                "note": DIAGNOSTIC_ONLY_NOTE,
            },
            "rows": [],
        }

    # Enrich full market board DataFrame
    enriched = apply_meta_label_promotion(
        full_market_df, role_payload=role_payload, cal_payload=cal_payload
    )

    # Calculate bucket counts
    strong = enriched[enriched["meta_label_bucket"] == "shadow_strong_review_candidate"]
    watch = enriched[enriched["meta_label_bucket"] == "shadow_watch_candidate"]
    neutral = enriched[enriched["meta_label_bucket"] == "shadow_neutral"]
    weak = enriched[enriched["meta_label_bucket"] == "shadow_weak"]
    avoid = enriched[enriched["meta_label_bucket"] == "shadow_avoid_review"]

    # Top strong candidates: sorted by score descending (highest score is strongest)
    strong_pool = enriched[
        enriched["meta_label_bucket"].isin(
            {"shadow_strong_review_candidate", "shadow_watch_candidate"}
        )
    ].copy()

    top_candidates = []
    if not strong_pool.empty:
        sorted_strong = strong_pool.sort_values(
            by=["meta_label_rules_score", "player_name"],
            ascending=[False, True],
        )
        for _, row in sorted_strong.head(5).iterrows():
            top_candidates.append(
                {
                    "player_name": row["player_name"],
                    "team": row.get("team_abbr") or row.get("team") or "unknown",
                    "market_type": row["market_type"],
                    "selection": row["selection"],
                    "meta_label_rules_score": row["meta_label_rules_score"],
                    "meta_label_bucket": row["meta_label_bucket"],
                    "reason_codes": row["reason_codes"],
                }
            )

    summary = {
        "total_rows_evaluated": int(len(enriched)),
        "shadow_strong_review_candidate_count": int(len(strong)),
        "shadow_watch_candidate_count": int(len(watch)),
        "shadow_neutral_count": int(len(neutral)),
        "shadow_weak_count": int(len(weak)),
        "shadow_avoid_review_count": int(len(avoid)),
        "top_strong_candidates": top_candidates,
        "readiness": "review_only",
        "note": DIAGNOSTIC_ONLY_NOTE,
    }

    # Convert pandas rows to dict lists safely for serialization
    rows_payload = []
    for _, row in enriched.iterrows():
        row_dict = {}
        for field in REPORT_FIELDS:
            val = row.get(field)
            if isinstance(val, list):
                row_dict[field] = list(val)
            elif pd.isna(val) if hasattr(pd, "isna") else False:
                row_dict[field] = None
            else:
                row_dict[field] = val
        rows_payload.append(row_dict)

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "meta_label_promotion_shadow",
        "notes": [
            "diagnostic_report_only",
            "no_prediction_logic_changed",
            "no_elite_gates_changed",
            "no_kelly_sizing_changed",
            "no_final_decision_changed",
            DIAGNOSTIC_ONLY_NOTE,
        ],
        "summary": summary,
        "rows": rows_payload,
    }


def meta_label_promotion_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"meta_label_promotion_shadow_{date}.json"


def meta_label_promotion_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"meta_label_promotion_shadow_{date}.txt"


def meta_label_promotion_csv_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"meta_label_promotion_shadow_{date}.csv"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def render_meta_label_promotion_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(rows, list):
        rows = []

    lines = [
        "Meta-Label Promotion - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        f"total rows evaluated: {summary.get('total_rows_evaluated', 0)}",
        f"shadow strong review candidate count: {summary.get('shadow_strong_review_candidate_count', 0)}",
        f"shadow watch candidate count: {summary.get('shadow_watch_candidate_count', 0)}",
        f"shadow neutral count: {summary.get('shadow_neutral_count', 0)}",
        f"shadow weak count: {summary.get('shadow_weak_count', 0)}",
        f"shadow avoid review count: {summary.get('shadow_avoid_review_count', 0)}",
        "",
        "Top Strong Candidates",
        "-" * 72,
    ]

    top_candidates = summary.get("top_strong_candidates", [])
    if not top_candidates:
        lines.append("none detected")
    else:
        for tc in top_candidates:
            reasons = "; ".join(tc.get("reason_codes", []))
            lines.append(
                f"- {tc.get('player_name')} ({tc.get('team')}): "
                f"market={tc.get('market_type')} side={tc.get('selection')} "
                f"score={_fmt_num(tc.get('meta_label_rules_score'))} "
                f"bucket={tc.get('meta_label_bucket')} "
                f"reasons=[{reasons}]"
            )

    lines.extend(
        [
            "",
            DIAGNOSTIC_ONLY_NOTE,
            "",
            "Meta-Label Promotion Rows",
            "-" * 72,
        ]
    )

    if not rows:
        lines.append("n/a")
    else:
        lines.append(
            "player | game_id | market | side | line | odds | edge | conf | qual | role | caution | score | bucket"
        )
        for row in rows[:100]:
            lines.append(
                " | ".join(
                    [
                        str(row.get("player_name", "unknown")),
                        str(row.get("game_id", "unknown")),
                        str(row.get("market_type", "unknown")),
                        str(row.get("selection", "unknown")),
                        _fmt_num(row.get("line")),
                        _fmt_num(row.get("odds"), 0),
                        _fmt_num(row.get("edge")),
                        _fmt_num(row.get("confidence"), 2),
                        _fmt_num(row.get("quality_score"), 1),
                        str(row.get("role_stability_bucket", "unknown")),
                        str(row.get("context_caution_level", "unknown")),
                        _fmt_num(row.get("meta_label_rules_score")),
                        str(row.get("meta_label_bucket", "unknown")),
                    ]
                )
            )
        if len(rows) > 100:
            lines.append(f"... {len(rows) - 100} additional rows omitted")

    return "\n".join(lines) + "\n"


def write_meta_label_promotion_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    full_market_df: pd.DataFrame | None = None,
    role_payload: dict[str, Any] | None = None,
    cal_payload: dict[str, Any] | None = None,
    generated_at_utc: str | None = None,
    generated_by: str = GENERATED_BY,
    source_runtime_root: str | Path | None = None,
    source_history_root: str | Path | None = None,
    report_name: str = "meta_label_promotion",
    orchestrator_run_id: str | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    """Write meta-label promotion JSON, operator TXT, and operator CSV."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)

    if full_market_df is None:
        full_market_df = _read_csv(
            runtime_root / "operator" / f"full_market_board_{prediction_date}.csv"
        )

    if role_payload is None:
        role_payload = _read_json(
            runtime_root / "diagnostics" / f"player_role_stability_{prediction_date}.json"
        )

    if cal_payload is None:
        cal_payload = _read_json(
            runtime_root
            / "diagnostics"
            / f"calibration_bucket_report_{prediction_date}.json"
        )

    payload = build_meta_label_promotion_report(
        prediction_date=prediction_date,
        full_market_df=full_market_df,
        role_payload=role_payload,
        cal_payload=cal_payload,
    )
    payload = apply_shadow_report_metadata(
        payload,
        prediction_date=prediction_date,
        generated_at_utc=generated_at_utc,
        generated_by=generated_by,
        source_runtime_root=source_runtime_root or runtime_root,
        source_history_root=source_history_root or history_root,
        report_name=report_name,
        orchestrator_run_id=orchestrator_run_id,
    )

    json_path = meta_label_promotion_json_path_for_date(prediction_date, runtime_root)
    txt_path = meta_label_promotion_txt_path_for_date(prediction_date, runtime_root)
    csv_path = meta_label_promotion_csv_path_for_date(prediction_date, runtime_root)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Save JSON
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    # Save TXT
    txt_path.write_text(render_meta_label_promotion_report(payload), encoding="utf-8")

    # Save CSV
    if full_market_df is not None and not full_market_df.empty:
        enriched_df = apply_meta_label_promotion(
            full_market_df, role_payload=role_payload, cal_payload=cal_payload
        )
        # Keep only REPORT_FIELDS in output CSV to keep it clean and focused
        csv_df = enriched_df[[f for f in REPORT_FIELDS if f in enriched_df.columns]].copy()
        csv_df.to_csv(csv_path, index=False)
    else:
        # Create empty CSV with columns
        pd.DataFrame(columns=list(REPORT_FIELDS)).to_csv(csv_path, index=False)

    return json_path, txt_path, csv_path, payload
