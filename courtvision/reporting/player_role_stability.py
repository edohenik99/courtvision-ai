"""Player Role Stability diagnostics and reports.

Report-only analytics over player minutes and role context stability.
This module does not change projections, selection logic, Elite gates, Kelly
sizing, or final decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.player_role_stability import (
    apply_player_role_stability,
)


REPORT_VERSION = "1.0"
DIAGNOSTIC_ONLY_NOTE = (
    "Player Role Stability is diagnostic only and is not an Elite/Kelly input."
)

REPORT_FIELDS: tuple[str, ...] = (
    "prediction_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "selection",
    "role_stability_score",
    "role_stability_bucket",
    "role_stability_reasons",
    "role_stability_coverage",
    "minutes_avg",
    "minutes_recent",
    "minutes_projection",
    "minutes_delta_recent_avg",
    "minutes_delta_projection_avg",
    "minutes_cv_recent",
    "manual_minutes_delta",
    "injury_role_pressure",
    "starter_or_rotation_status",
    "role_data_quality",
)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except Exception:
        return pd.DataFrame()


def build_player_role_stability_report(
    prediction_date: str,
    full_market_df: pd.DataFrame | None = None,
    baseline_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build player role stability diagnostics from candidate data."""
    if full_market_df is None or full_market_df.empty:
        return {
            "report_version": REPORT_VERSION,
            "prediction_date": prediction_date,
            "scope": "player_role_stability_report_shadow",
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
                "stable_count": 0,
                "mostly_stable_count": 0,
                "mixed_count": 0,
                "volatile_count": 0,
                "highly_volatile_count": 0,
                "unknown_count": 0,
                "top_volatile_examples": [],
                "readiness": "review_only",
                "note": DIAGNOSTIC_ONLY_NOTE,
            },
            "rows": [],
        }

    enriched = apply_player_role_stability(full_market_df, baseline_df)
    
    # Deduplicate rows by player_name and team to evaluate unique players
    # However, output has all player-market rows. Let's compute summaries over unique player-team combinations!
    unique_players = enriched.drop_duplicates(subset=["player_name", "team"]).copy()
    
    stable = unique_players[unique_players["role_stability_bucket"] == "stable"]
    mostly_stable = unique_players[unique_players["role_stability_bucket"] == "mostly_stable"]
    mixed = unique_players[unique_players["role_stability_bucket"] == "mixed"]
    volatile = unique_players[unique_players["role_stability_bucket"] == "volatile"]
    highly_volatile = unique_players[unique_players["role_stability_bucket"] == "highly_volatile"]
    unknown = unique_players[unique_players["role_stability_bucket"] == "unknown"]

    # Top volatile examples: sorted by score ascending (lowest score is most volatile)
    volatile_pool = unique_players[
        unique_players["role_stability_bucket"].isin({"volatile", "highly_volatile"})
    ].copy()
    
    top_examples = []
    if not volatile_pool.empty:
        sorted_volatile = volatile_pool.sort_values(
            by=["role_stability_score", "player_name"],
            ascending=[True, True],
        )
        for _, row in sorted_volatile.head(5).iterrows():
            top_examples.append(
                {
                    "player_name": row["player_name"],
                    "team": row["team"],
                    "role_stability_score": row["role_stability_score"],
                    "role_stability_bucket": row["role_stability_bucket"],
                    "role_stability_reasons": row["role_stability_reasons"],
                }
            )

    summary = {
        "total_rows_evaluated": int(len(unique_players)),
        "stable_count": int(len(stable)),
        "mostly_stable_count": int(len(mostly_stable)),
        "mixed_count": int(len(mixed)),
        "volatile_count": int(len(volatile)),
        "highly_volatile_count": int(len(highly_volatile)),
        "unknown_count": int(len(unknown)),
        "top_volatile_examples": top_examples,
        "readiness": "review_only",
        "note": DIAGNOSTIC_ONLY_NOTE,
    }

    # Convert reasons lists to lists/strings for serialization compatibility
    rows_payload = []
    for _, row in enriched.iterrows():
        row_dict = dict(row)
        # Ensure reasons list is properly structured
        if isinstance(row_dict.get("role_stability_reasons"), list):
            row_dict["role_stability_reasons"] = list(row_dict["role_stability_reasons"])
        rows_payload.append(row_dict)

    return {
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "scope": "player_role_stability_report_shadow",
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


def player_role_stability_json_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"player_role_stability_{date}.json"


def player_role_stability_txt_path_for_date(
    date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "operator" / f"player_role_stability_{date}.txt"


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


def render_player_role_stability_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(rows, list):
        rows = []

    lines = [
        "Player Role Stability - Shadow Only",
        f"prediction_date: {payload.get('prediction_date', '')}",
        "=" * 72,
        f"total unique players evaluated: {summary.get('total_rows_evaluated', 0)}",
        f"stable count: {summary.get('stable_count', 0)}",
        f"mostly stable count: {summary.get('mostly_stable_count', 0)}",
        f"mixed count: {summary.get('mixed_count', 0)}",
        f"volatile count: {summary.get('volatile_count', 0)}",
        f"highly volatile count: {summary.get('highly_volatile_count', 0)}",
        f"unknown count: {summary.get('unknown_count', 0)}",
        "",
        "Top Volatile Examples",
        "-" * 72,
    ]
    
    top_examples = summary.get("top_volatile_examples", [])
    if not top_examples:
        lines.append("none detected")
    else:
        for ex in top_examples:
            reasons = "; ".join(ex.get("role_stability_reasons", []))
            lines.append(
                f"- {ex.get('player_name')} ({ex.get('team')}): "
                f"score={_fmt_num(ex.get('role_stability_score'))} "
                f"bucket={ex.get('role_stability_bucket')} "
                f"reasons=[{reasons}]"
            )

    lines.extend(
        [
            "",
            DIAGNOSTIC_ONLY_NOTE,
            "",
            "Player Role Stability Rows",
            "-" * 72,
        ]
    )

    if not rows:
        lines.append("n/a")
    else:
        lines.append(
            "player | team | market | side | score | bucket | coverage | avg_min | rec_min | proj_min | cv | reasons"
        )
        for row in rows[:100]:
            reasons = "; ".join(row.get("role_stability_reasons", []))
            lines.append(
                " | ".join(
                    [
                        str(row.get("player_name", "unknown")),
                        str(row.get("team", "unknown")),
                        str(row.get("market_type", "unknown")),
                        str(row.get("selection", "unknown")),
                        _fmt_num(row.get("role_stability_score")),
                        str(row.get("role_stability_bucket", "unknown")),
                        _fmt_pct(row.get("role_stability_coverage")),
                        _fmt_num(row.get("minutes_avg")),
                        _fmt_num(row.get("minutes_recent")),
                        _fmt_num(row.get("minutes_projection")),
                        _fmt_num(row.get("minutes_cv_recent"), 2),
                        reasons,
                    ]
                )
            )
        if len(rows) > 100:
            lines.append(f"... {len(rows) - 100} additional rows omitted")

    return "\n".join(lines) + "\n"


def write_player_role_stability_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    full_market_df: pd.DataFrame | None = None,
    baseline_df: pd.DataFrame | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write player role stability diagnostics JSON and operator TXT."""
    runtime_root = Path(runtime_root)
    history_root = Path(history_root)
    
    if full_market_df is None:
        full_market_df = _read_csv(runtime_root / "operator" / f"full_market_board_{prediction_date}.csv")
    if baseline_df is None:
        baseline_df = _read_csv(runtime_root.parent / "model" / "player_baselines.csv")

    payload = build_player_role_stability_report(
        prediction_date=prediction_date,
        full_market_df=full_market_df,
        baseline_df=baseline_df,
    )
    
    json_path = player_role_stability_json_path_for_date(prediction_date, runtime_root)
    txt_path = player_role_stability_txt_path_for_date(prediction_date, runtime_root)
    
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    txt_path.write_text(render_player_role_stability_report(payload), encoding="utf-8")
    
    return json_path, txt_path, payload
