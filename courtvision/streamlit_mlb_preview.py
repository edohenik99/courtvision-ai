"""Read-only MLB view for the existing CourtVision Streamlit workstation."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from courtvision.sports.mlb.research_preview import HR_WARNING, OPERATING_TIMEZONE
from courtvision.sports.mlb.research_preview_sources import load_preview_board

STATUS_LABELS = {
    "QUALIFIED_RESEARCH": "Qualified Research", "LEGACY_RESEARCH": "Legacy Research",
    "BLOCKED": "Blocked", "UNAVAILABLE": "Unavailable",
}


def render_mlb_research_preview(output_dir: Path, day: str) -> None:
    today = datetime.now(OPERATING_TIMEZONE).date().isoformat()
    st.header("TODAY'S MLB RESEARCH BOARD" if day == today else "PRESERVED MLB RESEARCH BOARD")
    st.caption(f"Preview data date: {day} · Research only · Not approved · Betting eligibility: disabled")
    if day != today:
        st.info("Historical/preserved data. These are not today's predictions.")
    st.warning(HR_WARNING)
    st.info("Hits: Market Independent: YES when qualified. Calibration: unavailable / naive baseline. "
            "No pitcher, park, weather or recent-form adjustment. Blocked probabilities remain unavailable.")
    try:
        rows, summary = load_preview_board(output_dir / "runtime" / "mlb" / "research", day)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        st.error(f"MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE: preview artifact could not be validated ({exc}).")
        return
    if summary["missing_sources"]:
        st.warning("MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE: " + "; ".join(summary["missing_sources"]))
    st.caption("Local artifacts only. Refresh with: .\\run_mlb_preview.ps1 -Date " + day)
    columns = st.columns(4)
    for col, label, value in zip(columns, ("Games", "Players", "Hits qualified / blocked", "HR legacy research"),
                                (summary["games_seen"], summary["players_seen"],
                                 f"{summary['hits_qualified']} / {summary['hits_blocked']}", summary["hr_market_contaminated"])):
        col.metric(label, value)
    market = st.radio("Market", ["All", "Hits", "Home Runs"], horizontal=True, key="mlb_market")
    statuses = st.multiselect("Status", list(STATUS_LABELS.values()), default=list(STATUS_LABELS.values()), key="mlb_status")
    selected = [r for r in rows if STATUS_LABELS[r.prediction_status] in statuses
                and (market == "All" or r.market_type == ("batter_hits" if market == "Hits" else "batter_home_runs"))]
    st.caption(f"Showing {len(selected)} of {len(rows)} rows. All statuses are visible by default. "
               "Source-status rows describe missing data; they are not players or predictions.")
    if not selected:
        st.info("No rows match these filters.")
        return
    display = []
    for row in selected:
        display.append({
            "Player": row.player_name or "Source unavailable", "Game":
            f"{row.away_team} at {row.home_team}" if row.home_team and row.away_team else row.canonical_event_id or row.provider_event_id or "Unavailable",
            "Market": row.market_display, "Line": row.line, "Sportsbook": row.sportsbook,
            "Price": row.american_odds, "CV Probability": row.model_probability,
            "Market Probability": row.market_implied_probability, "Status": STATUS_LABELS[row.prediction_status],
            "Block reason": row.block_reason, "Limitation": row.limitation_status,
        })
    frame = pd.DataFrame(display)
    colors = {"Qualified Research": "#164b38", "Legacy Research": "#614b12", "Blocked": "#6b2634", "Unavailable": "#3f4651"}
    styled = frame.style.map(lambda value: f"background-color: {colors[value]}; color: white", subset=["Status"])
    styled = styled.format({"CV Probability": "{:.1%}", "Market Probability": "{:.1%}", "Line": "{:.1f}", "Price": "{:+.0f}"}, na_rep="Unavailable")
    st.dataframe(styled, hide_index=True, width="stretch")
    detail_index = st.selectbox("Row details", range(len(selected)), format_func=lambda i:
                                f"{selected[i].player_name or 'Source unavailable'} · {selected[i].market_display} · "
                                f"{STATUS_LABELS[selected[i].prediction_status]} · {selected[i].sportsbook or 'No quote'}",
                                key="mlb_detail")
    with st.expander("Evidence, identity and limitations", expanded=True):
        row = selected[detail_index]
        st.json(row.to_dict())
    if summary.get("board_path"):
        st.caption(f"Board: {summary['board_path']}")
