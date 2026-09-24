"""Read-only MLB view for the existing CourtVision Streamlit workstation."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from courtvision.sports.mlb.research_preview import (
    HITS_LIMITATION, HR_LIMITATION, HR_WARNING, OPERATING_TIMEZONE, MLBResearchPreviewRow,
)
from courtvision.sports.mlb.research_preview_sources import load_preview_board

STATUS_LABELS = {
    "QUALIFIED_RESEARCH": "Qualified Research", "LEGACY_RESEARCH": "Legacy Research",
    "BLOCKED": "Blocked", "UNAVAILABLE": "Unavailable",
}


def _game_label(row: MLBResearchPreviewRow) -> str:
    if row.home_team and row.away_team:
        return f"{row.away_team} at {row.home_team}"
    return row.canonical_event_id or row.provider_event_id or "Unavailable"


def _detail_field(label: str, value: object) -> None:
    st.caption(label)
    st.text(str(value) if value is not None and value != "" else "Unavailable")


def render_prediction_details(row: MLBResearchPreviewRow) -> None:
    with st.container(border=True):
        st.subheader("Prediction details")
        left, right = st.columns(2)
        with left:
            for label, value in (("Player", row.player_name), ("Game", _game_label(row)),
                                 ("Market", row.market_display), ("Line", row.line)):
                _detail_field(label, value)
        with right:
            for label, value in (("CourtVision probability", row.model_probability),
                                 ("Market implied probability", row.market_implied_probability)):
                _detail_field(label, None if value is None else f"{value:.1%}")
            _detail_field("Prediction status", STATUS_LABELS[row.prediction_status])
            _detail_field("Market independence", row.probability_market_independence)
        _detail_field("Limitation status", row.limitation_status)
        if row.market_type == "batter_hits":
            _detail_field("Hits season source", row.season_source)
            _detail_field("AB projection version", row.ab_projection_version)
        left, right = st.columns(2)
        with left:
            _detail_field("Identity status", row.identity_status.replace("_", " ").capitalize())
            _detail_field("Lineup status", row.lineup_status.replace("_", " ").capitalize())
            _detail_field("Model ID", row.model_id)
            _detail_field("Model version", row.model_version)
        with right:
            _detail_field("Evidence cutoff", row.evidence_cutoff)
            _detail_field("Block reason", row.block_reason or "None")
            if row.block_detail:
                _detail_field("Block detail", row.block_detail)
        if row.market_type == "batter_hits" and row.prediction_status == "QUALIFIED_RESEARCH":
            columns = st.columns(3)
            for column, label, value in zip(columns, ("Season hits", "Season at-bats", "Projected at-bats"),
                                            (row.season_hits, row.season_at_bats, row.projected_at_bats)):
                with column:
                    _detail_field(label, value)
        with st.expander("Advanced / Raw Evidence", expanded=False):
            st.json(row.to_dict())


def render_mlb_research_preview(output_dir: Path, day: str) -> None:
    today = datetime.now(OPERATING_TIMEZONE).date().isoformat()
    st.header("TODAY'S MLB RESEARCH BOARD" if day == today else "PRESERVED MLB RESEARCH BOARD")
    st.caption(f"Preview data date: {day} · Research only · Not approved · Betting eligibility: disabled")
    if day != today:
        st.info("Historical/preserved data. These are not today's predictions.")
    st.warning(f"{HR_LIMITATION}: {HR_WARNING}")
    st.info(f"Hits: {HITS_LIMITATION}. Market Independent: YES when qualified. Calibration: unavailable / naive baseline. "
            "No pitcher, park, weather or recent-form adjustment. Blocked probabilities remain unavailable.")
    try:
        rows, summary = load_preview_board(output_dir / "runtime" / "mlb" / "research", day)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        st.error(f"MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE: preview artifact could not be validated ({exc}).")
        return
    st.subheader("Market availability")
    for availability in summary["market_status"].values():
        st.markdown(f"**{availability['label']}** — {availability['status']}")
        if availability["source_reasons"]:
            st.caption("; ".join(availability["source_reasons"]))
    if summary["status"] == "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE":
        st.warning("MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE: no market has usable prediction rows. "
                   "Any blocked player rows remain visible below.")
    elif summary["status"] == "MLB_PREVIEW_PARTIAL_AVAILABILITY":
        st.info("Partial availability: loaded research predictions are shown below. "
                "See each market's availability and any blocked player rows.")
    source_rows = [row for row in rows if row.row_kind == "SOURCE_STATUS"]
    if source_rows:
        with st.expander("Advanced / Raw Source Evidence", expanded=False):
            st.json([row.to_dict() for row in source_rows])
    st.caption("Local artifacts only. Refresh with: .\\run_mlb_preview.ps1 -Date " + day)
    columns = st.columns(4)
    for col, label, value in zip(columns, ("Games", "Players", "Hits qualified / blocked", "HR legacy research"),
                                (summary["games_seen"], summary["players_seen"],
                                 f"{summary['hits_qualified']} / {summary['hits_blocked']}", summary["hr_market_contaminated"])):
        col.metric(label, value)
    market = st.radio("Market", ["All", "Hits", "Home Runs"], horizontal=True, key="mlb_market")
    statuses = st.multiselect("Status", list(STATUS_LABELS.values()), default=list(STATUS_LABELS.values()), key="mlb_status")
    player_rows = [row for row in rows if row.row_kind == "PLAYER"]
    selected = [r for r in player_rows if STATUS_LABELS[r.prediction_status] in statuses
                and (market == "All" or r.market_type == ("batter_hits" if market == "Hits" else "batter_home_runs"))]
    st.caption(f"Showing {len(selected)} of {len(player_rows)} player rows. All statuses are visible by default. "
               "Source availability is listed separately above.")
    if not selected:
        st.info("No rows match these filters.")
        return
    display = []
    for row in selected:
        display.append({
            "Player": row.player_name or "Unavailable", "Game": _game_label(row),
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
                                f"{selected[i].player_name or 'Unavailable'} · {selected[i].market_display} · "
                                f"{STATUS_LABELS[selected[i].prediction_status]} · {selected[i].sportsbook or 'No quote'}",
                                key="mlb_detail")
    render_prediction_details(selected[detail_index])
    if summary.get("board_path"):
        st.caption(f"Board: {summary['board_path']}")
