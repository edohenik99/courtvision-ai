from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _safe_read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=columns)


def load_dashboard_data(history_root: str | Path = "data/history") -> dict[str, pd.DataFrame]:
    root = Path(history_root)
    return {
        "pick_history": _safe_read_csv(root / "pick_history.csv", ["prediction_date", "result_status", "selection", "edge", "qualification_reason"]),
        "performance_summary": _safe_read_csv(root / "performance_summary.csv", ["date", "total_picks", "hits", "misses", "pushes", "pending", "hit_rate", "max_team_exposure", "max_game_exposure"]),
        "by_side": _safe_read_csv(root / "performance_by_selection.csv", ["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]),
        "by_edge": _safe_read_csv(root / "performance_by_edge_bucket.csv", ["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]),
        "by_qualification": _safe_read_csv(root / "performance_by_qualification_reason.csv", ["date", "group", "total", "hits", "misses", "pushes", "pending", "hit_rate"]),
    }


def _overall_hit_rate(df: pd.DataFrame) -> tuple[int, float]:
    if df.empty:
        return 0, 0.0
    hits = int(df["hits"].sum()) if "hits" in df.columns else 0
    misses = int(df["misses"].sum()) if "misses" in df.columns else 0
    graded = hits + misses
    return graded, (float(hits / graded) if graded else 0.0)


def _window_hit_rate(df: pd.DataFrame, window: int) -> float:
    if df.empty:
        return 0.0
    recent = df.sort_values("date").tail(window)
    hits = int(recent["hits"].sum())
    misses = int(recent["misses"].sum())
    denom = hits + misses
    return float(hits / denom) if denom else 0.0


def _cli_report(history_root: str | Path = "data/history") -> int:
    data = load_dashboard_data(history_root=history_root)
    perf = data["performance_summary"]
    graded_total, overall_hr = _overall_hit_rate(perf)
    hr7 = _window_hit_rate(perf, 7)
    hr30 = _window_hit_rate(perf, 30)
    pending = int(perf["pending"].sum()) if not perf.empty and "pending" in perf.columns else 0

    print("CourtVision Hit-Rate Dashboard")
    print("==============================")
    print(f"Total graded picks: {graded_total}")
    print(f"Overall hit rate: {overall_hr:.2%}")
    print(f"Last 7 slates hit rate: {hr7:.2%}")
    print(f"Last 30 slates hit rate: {hr30:.2%}")
    print(f"Pending picks count: {pending}")
    return 0


def _streamlit_app(history_root: str | Path = "data/history") -> int:
    import streamlit as st

    data = load_dashboard_data(history_root=history_root)
    perf = data["performance_summary"]
    by_side = data["by_side"]
    by_edge = data["by_edge"]
    by_qualification = data["by_qualification"]

    graded_total, overall_hr = _overall_hit_rate(perf)
    hr7 = _window_hit_rate(perf, 7)
    hr30 = _window_hit_rate(perf, 30)

    st.title("CourtVision Performance Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Graded Picks", graded_total)
    c2.metric("Overall Hit Rate", f"{overall_hr:.2%}")
    c3.metric("Last 7 Slates", f"{hr7:.2%}")
    c4.metric("Last 30 Slates", f"{hr30:.2%}")

    st.subheader("Daily Hit-Rate Trend")
    if perf.empty:
        st.info("No performance data yet.")
    else:
        trend = perf.sort_values("date")[["date", "hit_rate", "total_picks", "max_team_exposure", "max_game_exposure"]]
        st.line_chart(trend.set_index("date")["hit_rate"])
        st.dataframe(trend, use_container_width=True)

    st.subheader("Hit Rate by Side")
    if by_side.empty:
        st.info("No side summary data.")
    else:
        st.dataframe(by_side.sort_values(["date", "group"]), use_container_width=True)

    st.subheader("Hit Rate by Edge Bucket")
    if by_edge.empty:
        st.info("No edge bucket data.")
    else:
        st.dataframe(by_edge.sort_values(["date", "group"]), use_container_width=True)

    st.subheader("Hit Rate by Qualification Reason")
    if by_qualification.empty:
        st.info("No qualification summary data.")
    else:
        st.dataframe(by_qualification.sort_values(["date", "group"]), use_container_width=True)
    return 0


def main() -> int:
    history_root = "data/history"
    if "--streamlit" in sys.argv:
        return _streamlit_app(history_root=history_root)
    return _cli_report(history_root=history_root)


if __name__ == "__main__":
    raise SystemExit(main())

