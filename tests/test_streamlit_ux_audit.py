"""
tests/test_streamlit_ux_audit.py
================================
UX Audit enforcement tests for the CourtVision Streamlit workstation.

These tests enforce the UX audit requirements approved by the operator:
  - Normal sidebar contains exactly 4 items (Today's Board, Run Review, History, UNDER Lab).
  - Debug-only items (Slate, Run Log, Calibration, Feedback) are hidden in normal mode.
  - Client-facing label renames are applied in the source.
  - Phase 15 panels are NOT in normal view (only inside debug expander in run_review).
  - New helper functions exist and have correct signatures.
  - Responsible betting footer function exists.
  - Navigation routing uses 'run_review' not 'review_layers'.
  - UNDER Lab page function exists.
  - New CSS classes are present in the theme file.

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

_NORMAL_NAV_KEYS = {"today", "run_review", "history", "under_lab"}
_DEBUG_NAV_KEYS = {"slate", "run_log", "calibration", "feedback"}


def test_normal_nav_keys_are_defined():
    """Normal nav tuple keys must appear in _normal_nav literal."""
    src = _app_source()
    for key in _NORMAL_NAV_KEYS:
        assert f'"{key}"' in src or f"'{key}'" in src, (
            f"Normal nav key '{key}' not found in app source."
        )


def test_debug_nav_keys_are_in_debug_list_only():
    """Debug nav keys must only appear as part of _debug_nav, not _normal_nav."""
    src = _app_source()
    # _normal_nav block ends before _debug_nav block
    normal_nav_match = re.search(
        r"_normal_nav\s*=\s*\[(.*?)\]", src, re.DOTALL
    )
    assert normal_nav_match, "_normal_nav list not found in source."
    normal_nav_block = normal_nav_match.group(1)
    for key in _DEBUG_NAV_KEYS:
        assert f'"{key}"' not in normal_nav_block and f"'{key}'" not in normal_nav_block, (
            f"Debug nav key '{key}' should not appear in _normal_nav. "
            f"Found in: {normal_nav_block[:200]!r}"
        )


def test_debug_guard_prevents_debug_nav_in_normal_mode():
    """The debug guard condition must gate _debug_nav behind raw_ui_debug_enabled()."""
    src = _app_source()
    assert "raw_ui_debug_enabled()" in src, "raw_ui_debug_enabled() must be used in nav guard."
    # The nav options list must include the conditional join
    assert "_debug_nav if raw_ui_debug_enabled()" in src, (
        "Navigation must conditionally include _debug_nav only when debug enabled."
    )


def test_run_log_is_debug_only():
    """Run Log must only appear inside _debug_nav, never in _normal_nav."""
    src = _app_source()
    normal_nav_match = re.search(r"_normal_nav\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert normal_nav_match
    assert "run_log" not in normal_nav_match.group(1), (
        "Run Log must not appear in _normal_nav."
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


def test_today_board_calls_under_lab_preview():
    """render_under_lab_preview_card must be called inside render_today_board."""
    src = _app_source()
    fn_match = re.search(
        r"def render_today_board\s*\(.*?\).*?:(.*?)(?=\n\n\n|\n# ===|\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert fn_match
    body = fn_match.group(1)
    assert "render_under_lab_preview_card" in body, (
        "render_under_lab_preview_card must be called inside render_today_board."
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
