"""CourtVision design-system helpers for Streamlit.

These functions emit the HTML markup that pairs with
``dashboard/styles/courtvision_theme.css``. They are pure presentation
helpers — none of them run prediction logic, fetch data, or modify any
backend state.

Usage::

    from dashboard.styles.streamlit_helpers import (
        inject_theme,
        render_brand_block,
        render_page_head,
        render_status_pill,
        render_featured_pick,
        render_empty_state,
        render_missing_file_warning,
        render_section_head,
        render_slate,
    )

    inject_theme()   # call once at the top of the app
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import streamlit as st

THEME_PATH = Path(__file__).parent / "courtvision_theme.css"
STYLE_END_TAG_RE = re.compile(r"</style", re.IGNORECASE)


# --------------------------------------------------------------------- theme

def inject_theme() -> None:
    """Inject the CourtVision theme CSS into the Streamlit app.

    Safe to call multiple times — Streamlit dedupes identical markdown.
    Falls back silently if the CSS file is missing so the dashboard
    still renders (just unstyled).
    """
    try:
        css = THEME_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        st.warning(
            f"CourtVision theme CSS not found at {THEME_PATH}. "
            "App will render with default Streamlit styles."
        )
        return
    css = STYLE_END_TAG_RE.sub(r"<\/style", css)
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# ----------------------------------------------------------------- brand block

def render_brand_block(name: str = "CourtVision") -> None:
    """Render the brand mark + wordmark inside the sidebar."""
    st.markdown(
        f"""
        <div class="cv-brand">
            <span class="cv-brand-mark">CV</span>
            <span>{html.escape(name)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_label(text: str) -> None:
    """Render a small uppercase eyebrow label in the sidebar."""
    st.markdown(
        f'<div class="cv-eyebrow">{html.escape(text)}</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------- header

def render_page_head(
    title: str,
    subtitle: str | None = None,
    pill: tuple[str, str] | None = None,
) -> None:
    """Render the main page header with optional status pill.

    ``pill`` is ``(label, state)`` where state ∈ {live, stale, error, idle}.
    """
    pill_html = ""
    if pill is not None:
        label, state = pill
        pill_html = (
            f'<span class="cv-pill" data-state="{html.escape(state)}">'
            f'<span class="cv-dot"></span>{html.escape(label)}'
            "</span>"
        )

    sub_html = (
        f"<p>{html.escape(subtitle)}</p>" if subtitle else ""
    )

    st.markdown(
        f"""
        <div class="cv-page-head">
            <div>
                <h1>{html.escape(title)}</h1>
                {sub_html}
            </div>
            {pill_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_pill(label: str, state: str = "idle") -> None:
    """Render a standalone status pill. State ∈ {live, stale, error, idle}."""
    st.markdown(
        f'<span class="cv-pill" data-state="{html.escape(state)}">'
        f'<span class="cv-dot"></span>{html.escape(label)}</span>',
        unsafe_allow_html=True,
    )


def render_section_head(title: str, caption: str | None = None) -> None:
    """Render a section header (h2 + caption)."""
    cap = f"<p>{html.escape(caption)}</p>" if caption else ""
    st.markdown(
        f'<div class="cv-section-head"><div><h2>{html.escape(title)}</h2>{cap}</div></div>',
        unsafe_allow_html=True,
    )


# -------------------------------------------------------------- featured pick

def render_featured_pick(pick: Mapping[str, Any] | None) -> None:
    """Render the hero featured pick card.

    ``pick`` should expose: entity_name, team, opponent, selection,
    sportsbook_line (optional), letter_grade.
    """
    if not pick:
        st.markdown(
            '<div class="cv-empty"><strong>No featured pick yet</strong>'
            "Run predictions to surface today's lock.</div>",
            unsafe_allow_html=True,
        )
        return

    name = html.escape(str(pick.get("entity_name", "—")))
    team = str(pick.get("team", ""))
    opp = str(pick.get("opponent", ""))
    matchup = (
        f"{html.escape(team)} vs {html.escape(opp)}"
        if team and opp
        else html.escape(team or opp or "")
    )
    selection = str(pick.get("selection", "")).upper()
    line = pick.get("sportsbook_line")
    market = str(pick.get("market_type", "")).replace("_", " ").upper()
    if line is not None and str(line) != "":
        prop = f"{selection} {line} {market}".strip()
    else:
        prop = f"{selection} {market}".strip()

    grade = str(pick.get("letter_grade", "—"))

    st.markdown(
        f"""
        <div class="cv-featured">
            <div class="cv-featured-body">
                <div class="cv-featured-eyebrow">Featured pick</div>
                <h2 class="cv-featured-name">{name}</h2>
                <div class="cv-featured-meta">{matchup}</div>
                <div class="cv-featured-prop">{html.escape(prop)}</div>
            </div>
            <div class="cv-featured-grade">
                <span class="big">{html.escape(grade)}</span>
                <span class="lbl">Lock of the day</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- slate cards

def render_slate(games: Iterable[Mapping[str, Any]]) -> None:
    """Render the tonight's-slate row.

    Each ``game`` should expose: home, away, tipoff (str), picks (int).
    """
    cards: list[str] = []
    for g in games:
        home = html.escape(str(g.get("home", "")))
        away = html.escape(str(g.get("away", "")))
        sep = html.escape(str(g.get("separator", "vs")))
        tipoff = html.escape(str(g.get("tipoff", "")))
        picks = int(g.get("picks", 0) or 0)
        picks_html = (
            f'<span style="color:var(--court-orange)">{picks} picks</span>'
            if picks
            else '<span style="color:var(--ink-5)">— picks</span>'
        )
        cards.append(
            f"""
            <div class="cv-game">
                <div class="cv-game-matchup">
                    <span class="cv-game-tm">{home}</span>
                    <span class="cv-game-vs">{sep}</span>
                    <span class="cv-game-tm">{away}</span>
                </div>
                <div class="cv-game-tipoff"><span>{tipoff}</span>{picks_html}</div>
            </div>
            """
        )

    if not cards:
        render_empty_state("No games on the slate", "Pick a date with scheduled games.")
        return

    st.markdown(
        f'<div class="cv-slate">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- empty/warn

def render_empty_state(title: str, body: str = "") -> None:
    body_html = f"<br>{html.escape(body)}" if body else ""
    st.markdown(
        f'<div class="cv-empty"><strong>{html.escape(title)}</strong>{body_html}</div>',
        unsafe_allow_html=True,
    )


def render_missing_file_warning(filename: str, hint: str = "") -> None:
    """Render a friendly warning card when an output file is missing."""
    hint_html = (
        f"<br><span style='color:var(--ink-6);font-size:12px'>{html.escape(hint)}</span>"
        if hint
        else ""
    )
    st.markdown(
        f"""
        <div class="cv-warning">
            ⚠ Missing output file <code>{html.escape(filename)}</code>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------- helpers

def safe_pick_featured(df: pd.DataFrame) -> dict[str, Any] | None:
    """Return the top row of a picks DataFrame as a dict, or None.

    Sorted by quality_score desc, then confidence desc — same priority
    the existing app uses. Pure read-only.
    """
    if df is None or len(df) == 0:
        return None
    work = df.copy()
    sort_cols = [
        c for c in ("quality_score", "confidence", "edge_abs") if c in work.columns
    ]
    if sort_cols:
        work = work.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
    row = work.iloc[0].to_dict()
    return row
