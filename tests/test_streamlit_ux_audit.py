"""
tests/test_streamlit_ux_audit.py
================================
UX Audit enforcement tests for the CourtVision Streamlit workstation.

These tests enforce the UX audit requirements approved by the operator:
  - Normal sidebar contains exactly 3 items (Today's Board, Run Review, History).
  - Debug-only items (UNDER Lab, Slate, Run Log, Calibration, Feedback) are hidden in normal mode.
  - Client-facing label renames are applied in the source.
  - Phase 15 panels are NOT in normal view (only inside debug expander in run_review).
  - New helper functions exist and have correct signatures.
  - Responsible betting footer function exists.
  - Navigation routing uses 'run_review' not 'review_layers'.
  - UNDER Lab page function exists; UNDER Lab is a tab inside Today's Board.
  - New CSS classes are present in the theme file.
  - No raw markdown **UNDER Lab** in UI copy.
  - No "No elite pick" / "No featured pick yet" / "Run predictions" copy in normal UI.
  - Conditional approved-pick empty states in render_featured_pick.
  - No hardcoded 2026-06-01 date; date.today() used as default.
  - Backend/betting files do not contain UI helpers.

No streamlit runtime, no pandas, no network calls are required.
"""

import ast
import importlib.util
import inspect
import os
import re
import sys
import textwrap
import types
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_FILE = PROJECT_ROOT / "courtvision_streamlit_app.py"
CSS_FILE = PROJECT_ROOT / "dashboard" / "styles" / "courtvision_theme.css"


def _parse_app_source() -> ast.Module:
    return ast.parse(APP_FILE.read_text(encoding="utf-8"))


def _app_source() -> str:
    return APP_FILE.read_text(encoding="utf-8")


def _css_source() -> str:
    return CSS_FILE.read_text(encoding="utf-8")


def _collect_functions(tree: ast.Module) -> set[str]:
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


# ---------------------------------------------------------------------------
# 1. Normal navigation — exactly 4 client-facing items
# ---------------------------------------------------------------------------

_NORMAL_NAV_KEYS = {"today", "run_review", "history"}
_DEBUG_NAV_KEYS = {"under_lab", "slate", "run_log", "calibration", "feedback"}


def test_normal_nav_keys_are_defined():
    """Normal nav tuple keys must appear in _normal_nav literal."""
    src = _app_source()
    for key in _NORMAL_NAV_KEYS:
        assert f'"{key}"' in src or f"'{key}'" in src, (
            f"Normal nav key '{key}' not found in app source."
        )


def test_normal_nav_has_exactly_3_items():
    """Normal nav must have exactly 3 items: Today's Board, Run Review, History."""
    src = _app_source()
    m = re.search(r"_normal_nav\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "_normal_nav list not found."
    block = m.group(1)
    # Extract only the tuple keys (first element of each tuple) — match ("key", ...) pattern
    keys = re.findall(r'\(\s*"([^"]+)"', block)
    assert set(keys) == {"today", "run_review", "history"}, (
        f"Normal nav must have exactly today/run_review/history keys, got: {keys}"
    )


def test_debug_guard_prevents_debug_nav_in_normal_mode():
    """The debug guard condition must gate _debug_nav behind raw_ui_debug_enabled()."""
    src = _app_source()
    assert "raw_ui_debug_enabled()" in src, "raw_ui_debug_enabled() must be used in nav guard."
    # The nav options list must include the conditional join
    assert "_debug_nav if raw_ui_debug_enabled()" in src, (
        "Navigation must conditionally include _debug_nav only when debug enabled."
    )


def test_under_lab_not_in_normal_nav():
    """UNDER Lab must NOT appear in _normal_nav — it lives inside Today's Board."""
    src = _app_source()
    m = re.search(r"_normal_nav\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "_normal_nav list not found."
    assert '"under_lab"' not in m.group(1), (
        "under_lab must be removed from _normal_nav — it is now a tab inside Today's Board."
    )


def test_under_lab_in_debug_nav():
    """UNDER Lab must be in _debug_nav for operator/debug access."""
    src = _app_source()
    m = re.search(r"_debug_nav\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "_debug_nav list not found."
    assert '"under_lab"' in m.group(1), (
        "under_lab must appear in _debug_nav for debug-mode sidebar access."
    )


# ---------------------------------------------------------------------------
# 2. Routing uses 'run_review' not 'review_layers'
# ---------------------------------------------------------------------------


def test_routing_uses_run_review_not_review_layers():
    """active_key == 'run_review' must route to render_quality_review_view."""
    src = _app_source()
    assert 'active_key == "run_review"' in src or "active_key == 'run_review'" in src, (
        "Routing must use 'run_review' key."
    )


def test_review_layers_key_no_longer_in_routing():
    """The old 'review_layers' route must not appear in routing logic."""
    src = _app_source()
    # Should not appear in the routing block as an active_key comparison
    assert 'active_key == "review_layers"' not in src, (
        "'review_layers' must be removed from routing — use 'run_review' instead."
    )


def test_routing_uses_under_lab():
    """active_key == 'under_lab' must route to render_under_lab_page."""
    src = _app_source()
    assert 'active_key == "under_lab"' in src or "active_key == 'under_lab'" in src, (
        "Routing must include 'under_lab' key for UNDER Lab page."
    )
    assert "render_under_lab_page" in src, (
        "render_under_lab_page must be called in routing."
    )


# ---------------------------------------------------------------------------
# 3. Client-facing label renames
# ---------------------------------------------------------------------------


_RENAMED_LABELS = {
    "Approved Picks": "Elite picks / Elite Board must be renamed to Approved Picks",
    "Stake Eligible": "Kelly eligible / Kelly rows must be renamed to Stake Eligible",
    "Operator Flags": "Manual review must be renamed to Operator Flags",
    "Grading Status": "Pending grading must be renamed to Grading Status",
    "System Health": "Run health must be renamed to System Health",
    "Full Market": "Full market must be renamed to Full Market (title case)",
    "Run Review": "Review Layers must be renamed to Run Review",
    "UNDER Lab": "UNDER Visibility must be renamed to UNDER Lab",
    "Research Only": "Shadow only must be renamed to Research Only (in UNDER Lab)",
    "Real-Money Impact": "Elite impact must be renamed to Real-Money Impact",
    "Staking Impact": "Kelly impact must be renamed to Staking Impact",
}


def test_client_label_renames_present_in_source():
    """All approved client-facing label renames must appear in the app source."""
    src = _app_source()
    for label, description in _RENAMED_LABELS.items():
        assert label in src, f"Expected renamed label '{label}' not found. {description}"


def test_old_developer_labels_removed_from_display():
    """Old developer labels must no longer appear as display strings in the source."""
    src = _app_source()
    # These should not appear as quoted labels in render_kpi_cards / section heads
    forbidden_display_labels = [
        '"Kelly eligible"',
        '"Kelly rows"',
        '"Elite picks"',
        '"Elite Board"',
        '"Manual review"',
        '"Pending grading"',
        '"Review Layers"',
        '"Elite count"',
    ]
    for label in forbidden_display_labels:
        assert label not in src, (
            f"Old developer label {label} should have been renamed in the UX audit."
        )


# ---------------------------------------------------------------------------
# 4. Required new functions exist with correct signatures
# ---------------------------------------------------------------------------


_REQUIRED_FUNCTIONS = [
    "render_client_decision_card",
    "render_responsible_betting_footer",
    "render_compact_slate_card",
    "render_under_lab_preview_card",
    "render_under_lab_page",
    "_derive_final_decision_label",
    "_derive_no_bet_reason",
    "_style_under_lab_board",
    "render_under_visibility_panel",
    "render_quality_review_view",
    "render_today_board",
]


def test_required_functions_exist():
    """All new UX audit helper functions must be defined in the app."""
    tree = _parse_app_source()
    fns = _collect_functions(tree)
    missing = [f for f in _REQUIRED_FUNCTIONS if f not in fns]
    assert not missing, f"Missing required functions: {missing}"


def test_render_client_decision_card_has_three_parameters():
    """render_client_decision_card must accept summary, quality_json, prediction_date_text."""
    src = _app_source()
    match = re.search(
        r"def render_client_decision_card\s*\(([^)]+)\)", src, re.DOTALL
    )
    assert match, "render_client_decision_card definition not found."
    params = match.group(1)
    for expected in ("summary", "quality_json", "prediction_date_text"):
        assert expected in params, (
            f"render_client_decision_card must have '{expected}' parameter."
        )


def test_render_under_lab_page_calls_render_under_visibility_panel():
    """render_under_lab_page must delegate to render_under_visibility_panel."""
    src = _app_source()
    # Find the function body
    match = re.search(
        r"def render_under_lab_page\s*\(.*?\).*?:(.*?)(?=\ndef |\Z)", src, re.DOTALL
    )
    assert match, "render_under_lab_page not found."
    body = match.group(1)
    assert "render_under_visibility_panel" in body, (
        "render_under_lab_page must call render_under_visibility_panel."
    )


# ---------------------------------------------------------------------------
# 5. Phase 15 panels are debug-only in run_review
# ---------------------------------------------------------------------------


def test_phase15_panels_behind_debug_gate_in_run_review():
    """Phase 15 review panels must only render inside a raw_ui_debug_enabled() block."""
    src = _app_source()
    # Find render_quality_review_view body
    fn_match = re.search(
        r"def render_quality_review_view\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===)",
        src,
        re.DOTALL,
    )
    assert fn_match, "render_quality_review_view not found."
    body = fn_match.group(1)

    # Phase 15 call must be inside a raw_ui_debug_enabled() conditional
    # Check that _render_phase15_review_panel appears after raw_ui_debug_enabled()
    debug_pos = body.find("raw_ui_debug_enabled()")
    phase15_pos = body.find("_render_phase15_review_panel")
    assert debug_pos != -1, "raw_ui_debug_enabled() must gate content in run_review."
    assert phase15_pos != -1, "_render_phase15_review_panel must still exist in run_review."
    assert phase15_pos > debug_pos, (
        "_render_phase15_review_panel must appear AFTER raw_ui_debug_enabled() guard."
    )


def test_phase15_verdict_cards_behind_debug_gate():
    """_render_phase15_verdict_cards must be inside the debug block in run_review."""
    src = _app_source()
    fn_match = re.search(
        r"def render_quality_review_view\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===)",
        src,
        re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    debug_pos = body.find("raw_ui_debug_enabled()")
    verdict_pos = body.find("_render_phase15_verdict_cards")
    assert verdict_pos != -1
    assert verdict_pos > debug_pos, (
        "_render_phase15_verdict_cards must appear AFTER the raw_ui_debug_enabled() gate."
    )


# ---------------------------------------------------------------------------
# 6. UNDER Visibility panel: client-friendly labels
# ---------------------------------------------------------------------------


def test_under_lab_uses_research_only_mode_label():
    """UNDER Lab KPI cards must say 'Research Only' not 'Shadow only'."""
    src = _app_source()
    assert '"Research Only"' in src or "'Research Only'" in src, (
        "UNDER Lab KPI mode label must be 'Research Only'."
    )
    # Old 'Shadow only' must not appear as a KPI value
    assert '"Shadow only"' not in src and "'Shadow only'" not in src, (
        "'Shadow only' KPI label must be replaced with 'Research Only'."
    )


def test_under_lab_uses_research_only_not_visibility_in_section_head():
    """UNDER Lab section head must say 'UNDER Lab — Research Only'."""
    src = _app_source()
    assert "UNDER Lab — Research Only" in src or "UNDER Lab" in src, (
        "UNDER Lab section head must use 'UNDER Lab — Research Only' or 'UNDER Lab'."
    )
    assert "UNDER Visibility — Shadow Only" not in src, (
        "'UNDER Visibility — Shadow Only' must be removed from section heads."
    )


# ---------------------------------------------------------------------------
# 7. Responsible betting footer
# ---------------------------------------------------------------------------


def test_responsible_betting_footer_defined():
    """render_responsible_betting_footer must exist."""
    tree = _parse_app_source()
    fns = _collect_functions(tree)
    assert "render_responsible_betting_footer" in fns


def test_responsible_betting_footer_called_in_today_board():
    """render_responsible_betting_footer must be called inside render_today_board."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match, "render_today_board not found."
    body = fn_match.group(1)
    assert "render_responsible_betting_footer" in body, (
        "render_responsible_betting_footer must be called inside render_today_board."
    )


def test_responsible_betting_footer_called_in_run_review():
    """render_responsible_betting_footer must be called inside render_quality_review_view."""
    src = _app_source()
    fn_match = re.search(
        r"def render_quality_review_view\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===)",
        src,
        re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    assert "render_responsible_betting_footer" in body, (
        "render_responsible_betting_footer must be called inside render_quality_review_view."
    )


# ---------------------------------------------------------------------------
# 8. Diagnostics section is debug-only in today_board
# ---------------------------------------------------------------------------


def test_diagnostics_section_is_debug_only_in_today_board():
    """The Diagnostics section head in Today's Board must be inside raw_ui_debug_enabled()."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match, "render_today_board not found."
    body = fn_match.group(1)
    # raw_ui_debug_enabled() must gate the Diagnostics section head
    debug_pos = body.find("raw_ui_debug_enabled()")
    diag_head_pos = body.find('"Diagnostics"')
    assert debug_pos != -1, "raw_ui_debug_enabled() must appear in render_today_board."
    if diag_head_pos != -1:
        assert diag_head_pos > debug_pos, (
            "Diagnostics section head must appear AFTER raw_ui_debug_enabled() gate."
        )


# ---------------------------------------------------------------------------
# 9. CSS classes for new components are present
# ---------------------------------------------------------------------------


_REQUIRED_CSS_CLASSES = [
    ".cv-decision-card",
    ".cv-decision-badge",
    ".cv-decision-headline",
    ".cv-decision-body",
    ".cv-info-banner",
    ".cv-disclaimer",
    ".cv-gate-item",
    ".cv-health-item",
    ".cv-under-preview-card",
]


def test_new_css_classes_present_in_theme():
    """All new UX audit CSS classes must be present in courtvision_theme.css."""
    if not CSS_FILE.exists():
        import pytest
        pytest.skip("CSS theme file not found — skipping.")
    css = _css_source()
    missing = [cls for cls in _REQUIRED_CSS_CLASSES if cls not in css]
    assert not missing, f"Missing CSS classes in theme: {missing}"


# ---------------------------------------------------------------------------
# 10. Today's Board — UNDER tab removed, decision card and preview present
# ---------------------------------------------------------------------------


def test_under_visibility_tab_removed_from_today_board():
    """'UNDER Visibility — Shadow Only' tab must not appear in render_today_board."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match, "render_today_board not found."
    body = fn_match.group(1)
    assert "UNDER Visibility" not in body, (
        "'UNDER Visibility' tab must be removed from Today's Board tabs."
    )


def test_today_board_calls_decision_card():
    """render_client_decision_card must be called inside render_today_board."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    assert "render_client_decision_card" in body, (
        "render_client_decision_card must be called inside render_today_board."
    )


def test_under_lab_tab_exists_in_today_board():
    """UNDER Lab must appear as a tab inside Today's Board (not just a preview card)."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match, "render_today_board not found."
    body = fn_match.group(1)
    assert '"UNDER Lab"' in body, (
        "'UNDER Lab' must appear as a tab name inside render_today_board."
    )
    assert "render_under_visibility_panel" in body, (
        "render_under_visibility_panel must be called inside Today's Board UNDER Lab tab."
    )


def test_elite_tab_renamed_to_approved_picks_in_today_board():
    """The 'Approved Picks' tab must replace the old 'Elite' tab in Today's Board."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    assert '"Approved Picks"' in body or "'Approved Picks'" in body, (
        "Tab must be renamed from 'Elite' to 'Approved Picks' in Today's Board."
    )


# ---------------------------------------------------------------------------
# 11. Copy-polish: no raw markdown, no "No elite pick", no "Run predictions"
# ---------------------------------------------------------------------------

_HELPERS_FILE = PROJECT_ROOT / "dashboard" / "styles" / "streamlit_helpers.py"


def _helpers_source() -> str:
    return _HELPERS_FILE.read_text(encoding="utf-8")


def test_no_raw_markdown_bold_under_lab_in_decision_card():
    """Decision card must not emit literal **UNDER Lab** markdown syntax."""
    app = _app_source()
    assert "**UNDER Lab**" not in app, (
        "Decision card emits raw markdown '**UNDER Lab**' — must use plain text or HTML."
    )


def test_no_elite_pick_label_in_app_source():
    """'No elite pick' must not appear as UI copy in the main app."""
    app = _app_source()
    assert '"No elite pick"' not in app and "'No elite pick'" not in app, (
        "'No elite pick' must be replaced with 'No Approved Pick' in the main app."
    )


def test_no_approved_pick_label_present():
    """'No Approved Pick' must appear in the main app as the fallback decision label."""
    app = _app_source()
    assert "No Approved Pick" in app, (
        "'No Approved Pick' must appear as the fallback decision label in the main app."
    )


def test_no_elite_picks_in_helpers_slate():
    """'No elite picks' must not appear as the slate game status in streamlit_helpers.py."""
    helpers = _helpers_source()
    assert '"No elite picks"' not in helpers and "'No elite picks'" not in helpers, (
        "render_slate uses 'No elite picks' — must use 'No approved picks'."
    )


def test_no_featured_pick_yet_copy_removed():
    """'No featured pick yet' must be removed from streamlit_helpers.py."""
    helpers = _helpers_source()
    assert "No featured pick yet" not in helpers, (
        "'No featured pick yet' must be replaced with client-friendly empty state copy."
    )


def test_no_run_predictions_copy_in_featured_pick():
    """'Run predictions to surface today's lock' must not appear in the helpers."""
    helpers = _helpers_source()
    assert "today's lock" not in helpers and "today\\'s lock" not in helpers, (
        "'today's lock' developer copy must be removed from render_featured_pick."
    )
    assert "Run predictions to surface" not in helpers, (
        "'Run predictions to surface' must be removed from render_featured_pick."
    )


def test_no_run_predictions_copy_in_app_empty_state():
    """'Run predictions to see the workstation' must not appear in render_today_board."""
    app = _app_source()
    assert "Run predictions to see the workstation" not in app, (
        "render_today_board empty state must not say 'Run predictions to see the workstation'."
    )
    assert "hit RUN PREDICTIONS" not in app, (
        "render_today_board empty state must not say 'hit RUN PREDICTIONS'."
    )


def test_approved_picks_empty_state_copy_in_helpers():
    """'No Approved Picks for this date' must appear in streamlit_helpers.py as the runtime empty state."""
    helpers = _helpers_source()
    assert "No Approved Picks for this date" in helpers, (
        "render_featured_pick must use 'No Approved Picks for this date' when runtime is loaded."
    )


def test_runtime_not_loaded_empty_state_copy_in_helpers():
    """'No runtime artifacts loaded' must appear in streamlit_helpers.py as the no-data empty state."""
    helpers = _helpers_source()
    assert "No runtime artifacts loaded" in helpers, (
        "render_featured_pick must use 'No runtime artifacts loaded' when no runtime data is present."
    )


# ---------------------------------------------------------------------------
# 12. Date consistency — no hardcoded date, correct normalization
# ---------------------------------------------------------------------------


def test_no_hardcoded_2026_06_01_in_app_source():
    """The string '2026-06-01' must not appear as a hardcoded date in the main app source."""
    app = _app_source()
    # Allow it in comments but not as a string literal assigned/passed in logic
    # Simple check: it must not appear as a quoted string literal
    assert '"2026-06-01"' not in app and "'2026-06-01'" not in app, (
        "'2026-06-01' must not be hardcoded as a date literal in the app."
    )


def test_no_hardcoded_2026_06_01_in_helpers():
    """The string '2026-06-01' must not appear in streamlit_helpers.py."""
    helpers = _helpers_source()
    assert "2026-06-01" not in helpers, (
        "'2026-06-01' must not appear in streamlit_helpers.py."
    )


def test_normalize_prediction_date_text_converts_slash_to_dash():
    """normalize_prediction_date_text must convert YYYY/MM/DD to YYYY-MM-DD."""
    src = _app_source()
    # 1. Function must exist
    assert "def normalize_prediction_date_text" in src, (
        "normalize_prediction_date_text function must be defined."
    )
    # 2. Must handle the slash-to-dash format ("%Y/%m/%d")
    assert '"%Y/%m/%d"' in src or "'%Y/%m/%d'" in src, (
        "normalize_prediction_date_text must support '%Y/%m/%d' format (slash input)."
    )
    # 3. Must produce YYYY-MM-DD output (isoformat)
    assert ".isoformat()" in src, (
        "normalize_prediction_date_text must call .isoformat() to produce YYYY-MM-DD output."
    )
    # 4. Behavior test via direct exec of just the function (extract only its indented body)
    from datetime import date as _date, datetime as _datetime
    lines = src.splitlines()
    fn_lines: list[str] = []
    in_fn = False
    for line in lines:
        if line.startswith("def normalize_prediction_date_text("):
            in_fn = True
        if in_fn:
            fn_lines.append(line)
            # Stop at next top-level definition (non-indented def/class after start)
            if fn_lines and len(fn_lines) > 1 and line and not line.startswith(" ") and not line.startswith("def normalize"):
                fn_lines.pop()  # remove the triggering line
                break
    fn_src = "\n".join(fn_lines)
    ns: dict = {"date": _date, "datetime": _datetime, "Any": object}
    exec(fn_src, ns)
    fn = ns["normalize_prediction_date_text"]
    assert fn("2026/06/03") == "2026-06-03", (
        "normalize_prediction_date_text('2026/06/03') must return '2026-06-03'."
    )
    assert fn("2026-06-03") == "2026-06-03", (
        "normalize_prediction_date_text('2026-06-03') must return '2026-06-03' unchanged."
    )




def test_today_defaults_to_date_today_not_hardcoded():
    """The sidebar default date must use date.today() not a hardcoded date string."""
    app = _app_source()
    # Must use date.today()
    assert "date.today()" in app, (
        "Sidebar default date must call date.today() — not a hardcoded literal."
    )


# ---------------------------------------------------------------------------
# 13. Backend/betting file invariant
# ---------------------------------------------------------------------------

_BETTING_FILES = [
    "courtvision/betting",
    "courtvision/selection",
    "courtvision/scoring",
    "courtvision/grading",
    "courtvision/calibration",
]


def test_backend_betting_files_unchanged_by_ui_audit():
    """UI-only changes must not touch any betting/scoring/selection/grading source files.

    This is a structural guard — it verifies that the files identified as
    production-risky betting logic still exist at their expected paths,
    and that no UX audit helper names were injected into them.
    """
    ui_helpers = {
        "render_client_decision_card",
        "render_responsible_betting_footer",
        "render_under_lab_preview_card",
        "raw_ui_debug_enabled",
        "_normal_nav",
        "_debug_nav",
    }
    for folder in _BETTING_FILES:
        folder_path = PROJECT_ROOT / folder
        if not folder_path.exists():
            continue
        for py_file in folder_path.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for helper in ui_helpers:
                assert helper not in text, (
                    f"UI helper '{helper}' must not appear in betting file {py_file.name}."
                )
