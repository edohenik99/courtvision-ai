from __future__ import annotations

import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from courtvision_ai import CourtVisionAI
ai = CourtVisionAI(out_dir="outputs")


st.set_page_config(
    page_title="CourtVision AI",
    page_icon="🏀",
    layout="wide",
)

APP_TITLE = "CourtVision AI"
APP_SUBTITLE = "Player props, team totals, moneyline, history, and no-BS rejection diagnostics."


@st.cache_resource
def get_ai(out_dir: str) -> CourtVisionAI:
    return CourtVisionAI(out_dir=out_dir)


@st.cache_data(show_spinner=False)
def load_history_cached(
    out_dir: str,
    refresh_token: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    _ = refresh_token
    ai = CourtVisionAI(out_dir=out_dir)
    return (
        ai.get_history(),
        ai.get_rejection_history(),
        ai.get_feedback_history(),
        ai.get_run_log(),
        ai.get_calibration_summary(),
    )


def init_state() -> None:
    st.session_state.setdefault("history_refresh_token", 0)
    st.session_state.setdefault("latest_prediction", None)
    st.session_state.setdefault("latest_fit_metrics", None)
    st.session_state.setdefault("last_out_dir", "outputs")


def bump_history_refresh() -> None:
    st.session_state["history_refresh_token"] = int(st.session_state.get("history_refresh_token", 0)) + 1


def reset_prediction_if_out_dir_changed(out_dir: str) -> None:
    previous = str(st.session_state.get("last_out_dir", "outputs"))
    if previous != out_dir:
        st.session_state["latest_prediction"] = None
        st.session_state["latest_fit_metrics"] = None
        st.session_state["last_out_dir"] = out_dir


def pretty_market_name(market_type: str) -> str:
    mapping = {
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
    return mapping.get(market_type, market_type)



def clean_pick_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    for col in ["confidence", "edge_abs", "quality_score", "sportsbook_line", "odds"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "quality_score" not in out.columns:
        confidence_series = pd.to_numeric(out.get("confidence", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        edge_abs_series = pd.to_numeric(out.get("edge_abs", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        out["quality_score"] = confidence_series * 100.0 + edge_abs_series * 8.0

    sort_cols = [c for c in ["quality_score", "confidence", "edge_abs", "odds"] if c in out.columns]
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
                lambda row: "__".join(sorted([str(row.get("team", "")).strip().upper(), str(row.get("opponent", "")).strip().upper()])),
                axis=1,
            )
            ml = ml.sort_values(by=[c for c in ["quality_score", "confidence", "edge_abs", "odds"] if c in ml.columns], ascending=False)
            ml = ml.drop_duplicates(subset=[c for c in ["prediction_date", "matchup_key"] if c in ml.columns], keep="first")
            ml = ml.drop(columns=["matchup_key"], errors="ignore")
        out = pd.concat([non_ml, ml], ignore_index=True) if not non_ml.empty or not ml.empty else out

    sort_cols = [c for c in ["quality_score", "confidence", "edge_abs", "odds"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
    return out

def style_pick_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

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


def style_rejection_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

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



def build_top_play_view(df: pd.DataFrame, limit: int = 12) -> pd.DataFrame:
    if df.empty:
        return df

    working = clean_pick_display(df)
    for col in ["confidence", "edge_abs", "quality_score"]:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce")
    if "quality_score" not in working.columns:
        working["quality_score"] = (
            working.get("confidence", pd.Series(dtype=float)).fillna(0.0) * 100.0
            + working.get("edge_abs", pd.Series(dtype=float)).fillna(0.0) * 8.0
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


def show_top_play_block(selected_df: pd.DataFrame) -> None:
    if selected_df.empty:
        return

    st.subheader("Top Plays")
    st.caption("Elite board only: final top 20 plays after dedupe, contradiction checks, and hard filtering.")
    top_view = build_top_play_view(selected_df, limit=20)
    st.dataframe(top_view, use_container_width=True, hide_index=True)

def show_metric_row(summary: dict[str, Any]) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Games", summary.get("games_analyzed", 0))
    c2.metric("Players", summary.get("players_evaluated", 0))
    c3.metric("Markets Checked", summary.get("markets_evaluated", 0))
    c4.metric("Elite Plays", summary.get("elite_count", summary.get("selected_count", 0)))
    c5.metric("Rejected", summary.get("rejected_count", 0))


def show_no_picks_explainer(rejected_df: pd.DataFrame, selected_df: pd.DataFrame) -> None:
    if not selected_df.empty:
        return

    st.warning("No selections qualified today.")

    if rejected_df.empty:
        st.info("There were no rejected rows to explain. Usually that means no supported markets were returned.")
        return

    st.subheader("Why no picks were selected")
    reason_counts = (
        rejected_df["rejection_reason"]
        .fillna("unknown")
        .value_counts()
        .rename_axis("Reason")
        .reset_index(name="Count")
    )
    st.dataframe(reason_counts, use_container_width=True, hide_index=True)

    st.subheader("Closest misses")
    near = rejected_df.copy()
    if "edge_abs" in near.columns:
        near["edge_abs"] = pd.to_numeric(near["edge_abs"], errors="coerce").fillna(-999)
        near = near.sort_values(by="edge_abs", ascending=False)
    st.dataframe(style_rejection_table(near.head(20)), use_container_width=True, hide_index=True)



def show_board_section(title: str, df: pd.DataFrame, caption: str | None = None) -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)
    if df.empty:
        st.info("No rows in this board.")
        return

    view_df = clean_pick_display(df)
    st.dataframe(style_pick_table(view_df), use_container_width=True, hide_index=True)

    if "market_type" in view_df.columns:
        st.markdown("### By Market Type")
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
        active_markets = [m for m in market_order if m in set(view_df["market_type"].astype(str).tolist())]
        if active_markets:
            tabs = st.tabs([pretty_market_name(m) for m in active_markets])
            for tab, market in zip(tabs, active_markets):
                with tab:
                    subset = view_df[view_df["market_type"] == market].copy()
                    st.dataframe(style_pick_table(subset), use_container_width=True, hide_index=True)

def show_selected_sections(
    elite_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
    all_stats_df: pd.DataFrame,
    team_board_df: pd.DataFrame,
    near_miss_df: pd.DataFrame,
) -> None:
    tab_names = ["Elite", "Full Market", "All Stats", "Team Board", "Near Miss"]
    elite_tab, full_tab, all_stats_tab, team_tab, near_tab = st.tabs(tab_names)

    with elite_tab:
        show_board_section("Elite Board", elite_df, "Hard-filtered board for strongest market-backed plays.")
    with full_tab:
        show_board_section("Full Market Board", full_market_df, "Top 20 per market with real live lines.")
    with all_stats_tab:
        show_board_section("All Stats Projection Board", all_stats_df, "Projection board for points, rebounds, assists, 3PT made, steals, and blocks.")
    with team_tab:
        show_board_section("Team Board", team_board_df, "Team projections, team totals, game totals, and moneyline snapshots.")
    with near_tab:
        show_board_section("Near Miss Board", near_miss_df, "Rejected plays closest to qualification thresholds.")


def show_prediction_diagnostics(summary: dict[str, Any]) -> None:
    odds_diagnostics = summary.get("odds_diagnostics", {})
    model_diagnostics = summary.get("model_diagnostics", {})

    if not odds_diagnostics and not model_diagnostics:
        return

    with st.expander("Data / Model Diagnostics", expanded=False):
        if odds_diagnostics:
            st.markdown("**Odds ingestion**")
            st.json(odds_diagnostics)
        if model_diagnostics:
            st.markdown("**Model context**")
            st.json(model_diagnostics)


def show_history(out_dir: str) -> None:
    history_df, rejection_history_df, feedback_df, run_log_df, calibration_df = load_history_cached(
        out_dir,
        int(st.session_state.get("history_refresh_token", 0)),
    )

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Prediction History", "Rejection History", "Feedback", "Run Log"]
    )

    with tab1:
        st.subheader("Prediction History")
        if history_df.empty:
            st.info("No prediction history saved yet.")
        else:
            st.dataframe(
                style_pick_table(clean_pick_display(history_df.tail(500).sort_index(ascending=False))),
                use_container_width=True,
                hide_index=True,
            )
            show_history_summary(history_df)

    with tab2:
        st.subheader("Rejection History")
        if rejection_history_df.empty:
            st.info("No rejection history saved yet.")
        else:
            st.dataframe(
                style_rejection_table(rejection_history_df.tail(500).sort_index(ascending=False)),
                use_container_width=True,
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
                st.dataframe(counts, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("Result Feedback / Learning Memory")
        if feedback_df.empty:
            st.info("No feedback rows logged yet.")
        else:
            st.dataframe(
                feedback_df.tail(500).sort_index(ascending=False),
                use_container_width=True,
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
                hit_rate["hit_rate"] = (pd.to_numeric(hit_rate["hit_rate"], errors="coerce") * 100).round(2)
                st.dataframe(hit_rate, use_container_width=True, hide_index=True)
            if {"hit", "model_projection", "actual_value"}.issubset(feedback_df.columns):
                overall_hit_rate = pd.to_numeric(feedback_df["hit"], errors="coerce").mean()
                mae = (
                    pd.to_numeric(feedback_df["model_projection"], errors="coerce")
                    - pd.to_numeric(feedback_df["actual_value"], errors="coerce")
                ).abs().mean()
                overall_hit_rate_value = 0.0 if pd.isna(overall_hit_rate) else float(overall_hit_rate)
                mae_value = 0.0 if pd.isna(mae) else float(mae)
                c1, c2 = st.columns(2)
                c1.metric("Overall Hit Rate", f"{overall_hit_rate_value * 100:.2f}%")
                c2.metric("Overall MAE", f"{mae_value:.2f}")
        if calibration_df.empty:
            st.caption("Calibration summary will appear after result feedback is uploaded.")
        else:
            view = calibration_df.copy()
            view["market_type"] = view["market_type"].map(pretty_market_name)
            view["hit_rate"] = (pd.to_numeric(view["hit_rate"], errors="coerce") * 100).round(2)
            st.markdown("### Calibration Memory")
            st.dataframe(view, use_container_width=True, hide_index=True)

    with tab4:
        st.subheader("Run Log")
        if run_log_df.empty:
            st.info("No run log yet.")
        else:
            st.dataframe(
                run_log_df.tail(300).sort_index(ascending=False),
                use_container_width=True,
                hide_index=True,
            )


def show_history_summary(history_df: pd.DataFrame) -> None:
    if history_df.empty:
        return

    st.markdown("### Market Performance Snapshot")
    grp = history_df.groupby("market_type", as_index=False).agg(
        picks=("market_type", "count"),
        avg_confidence=("confidence", "mean"),
        avg_edge=("edge_abs", "mean"),
    )
    grp["market_type"] = grp["market_type"].map(pretty_market_name)
    grp["avg_confidence"] = grp["avg_confidence"].round(3)
    grp["avg_edge"] = grp["avg_edge"].round(3)
    st.dataframe(grp, use_container_width=True, hide_index=True)


def feedback_upload_block(ai: CourtVisionAI) -> None:
    st.subheader("Log Actual Results for Learning")
    st.caption(
        "Upload a CSV with columns like: prediction_date, market_type, entity_name, team, opponent, selection, sportsbook_line, model_projection, actual_value, hit"
    )

    uploaded = st.file_uploader("Upload results CSV", type=["csv"], key="feedback_csv")
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
            if st.button("Save feedback and update calibration", type="primary"):
                ai.log_results(df)
                bump_history_refresh()
                st.success("Feedback saved. Calibration memory updated.")
        except Exception as exc:
            st.error(f"Could not read feedback CSV: {exc}")


def show_latest_prediction() -> None:
    payload = st.session_state.get("latest_prediction")
    if not payload:
        st.info("Run predictions to see the multi-board workstation.")
        return

    selected_df = payload.get("selected_props", pd.DataFrame())
    elite_df = payload.get("elite_props", selected_df)
    full_market_df = payload.get("full_market_props", pd.DataFrame())
    stat_only_df = payload.get("all_stats_props", payload.get("stat_only_props", pd.DataFrame()))
    team_board_df = payload.get("team_board_props", pd.DataFrame())
    near_miss_df = payload.get("near_miss_props", pd.DataFrame())
    rejected_df = payload.get("rejected_props", pd.DataFrame())
    summary = payload["summary"]

    show_metric_row(summary)
    st.info(summary.get("data_status", ""))

    show_no_picks_explainer(rejected_df, elite_df)
    show_selected_sections(elite_df, full_market_df, stat_only_df, team_board_df, near_miss_df)
    show_prediction_diagnostics(summary)

    with st.expander("Full rejection table", expanded=False):
        if rejected_df.empty:
            st.info("No rejection rows.")
        else:
            st.dataframe(style_rejection_table(rejected_df), use_container_width=True, hide_index=True)

    with st.expander("Run summary JSON", expanded=False):
        st.json(summary)


def main() -> None:
    init_state()

    st.title(APP_TITLE)
    st.caption(APP_SUBTITLE)

    default_out_dir = "outputs"
    today = date.today()
    default_train_end = today - timedelta(days=1)
    default_train_start = default_train_end - timedelta(days=180)

    with st.sidebar:
        st.header("Controls")
        out_dir = st.text_input("Output folder", value=default_out_dir)
        prediction_date = st.date_input("Prediction date", value=today)
        train_start = st.date_input("Training start", value=default_train_start)
        train_end = st.date_input("Training end", value=default_train_end)

        st.markdown("---")
        fit_clicked = st.button("Fit / Refresh Model", use_container_width=True)
        predict_clicked = st.button("Run Predictions", type="primary", use_container_width=True)
        reload_history_clicked = st.button("Reload History", use_container_width=True)

        st.markdown("---")
        st.markdown(
            """
            **Markets targeted**
            - Player Points
            - Rebounds
            - Assists
            - 3PT Made
            - Steals
            - Blocks
            - Team Total O/U
            - Moneyline
            - Points + Rebounds
            - Points + Assists
            - Rebounds + Assists
            - PRA
            - Stocks
            """
        )

    reset_prediction_if_out_dir_changed(out_dir)
    ai = get_ai(out_dir)

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
                outputs = ai.predict(prediction_date.isoformat())
                st.session_state["latest_prediction"] = outputs
                bump_history_refresh()
            except Exception as exc:
                st.error(f"Prediction run failed: {exc}")
                st.code(traceback.format_exc())

    show_latest_prediction()

    st.markdown("---")
    show_history(out_dir)

    st.markdown("---")
    feedback_upload_block(ai)

    st.markdown("---")
    st.caption(
        "This build now runs a full workstation: elite, full market, all-stats projections, team board, and near miss."
    )


if __name__ == "__main__":
    main()