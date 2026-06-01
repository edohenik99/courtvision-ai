from courtvision.streamlit_ui_helpers import (
    completion_audit_raw_details_visible,
    completion_status_display,
    dataframe_height,
    env_flag_enabled,
    mutation_actions_enabled,
    raw_diagnostics_visible,
    raw_review_artifacts_visible,
    show_raw_ui_debug,
)


def test_env_flag_enabled_accepts_common_true_values() -> None:
    for value in ("1", "true", "TRUE", "yes", "on", " y "):
        assert env_flag_enabled("COURTVISION_DEMO_MODE", {"COURTVISION_DEMO_MODE": value})


def test_env_flag_enabled_rejects_missing_or_false_values() -> None:
    for value in ("", "0", "false", "off", "no"):
        assert not env_flag_enabled("COURTVISION_DEMO_MODE", {"COURTVISION_DEMO_MODE": value})
    assert not env_flag_enabled("COURTVISION_DEMO_MODE", {})


def test_mutation_actions_disabled_in_demo_mode() -> None:
    assert not mutation_actions_enabled(True)
    assert mutation_actions_enabled(False)


def test_show_raw_ui_debug_requires_explicit_flag() -> None:
    assert not show_raw_ui_debug({})
    assert not show_raw_ui_debug({"COURTVISION_SHOW_RAW_UI_DEBUG": "0"})
    assert show_raw_ui_debug({"COURTVISION_SHOW_RAW_UI_DEBUG": "1"})


def test_raw_diagnostics_hidden_by_default_and_in_demo_mode() -> None:
    assert not raw_diagnostics_visible(False, {})
    assert not raw_diagnostics_visible(True)
    assert raw_diagnostics_visible(False, {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"})
    assert not raw_diagnostics_visible(True, {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"})


def test_raw_review_artifacts_hidden_by_default_and_in_demo_mode() -> None:
    assert not raw_review_artifacts_visible(False, {})
    assert not raw_review_artifacts_visible(True)
    assert raw_review_artifacts_visible(False, {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"})
    assert not raw_review_artifacts_visible(True, {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"})


def test_completion_status_display_maps_operator_states() -> None:
    assert completion_status_display("COMPLETE") == {
        "status": "COMPLETE",
        "label": "Complete",
        "state": "success",
    }
    assert completion_status_display("COMPLETE_WITH_SHADOW_OPEN_NOISE") == {
        "status": "COMPLETE_WITH_SHADOW_OPEN_NOISE",
        "label": "Complete with shadow open noise",
        "state": "info",
    }
    assert completion_status_display("PARTIAL")["state"] == "warning"
    assert completion_status_display("STALE_PENDING_RISK")["state"] == "danger"
    assert completion_status_display("INCONSISTENT_REPORTING")["state"] == "danger"


def test_completion_raw_audit_details_hidden_by_default_and_in_demo_mode() -> None:
    assert not completion_audit_raw_details_visible(False, {})
    assert not completion_audit_raw_details_visible(True)
    assert completion_audit_raw_details_visible(
        False,
        {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"},
    )
    assert not completion_audit_raw_details_visible(
        True,
        {"COURTVISION_SHOW_RAW_UI_DEBUG": "1"},
    )


def test_dataframe_height_is_compact_and_bounded() -> None:
    assert dataframe_height(0) == 120
    assert dataframe_height(1) == 120
    assert dataframe_height(5) == 266
    assert dataframe_height(100) == 420
    assert dataframe_height(100, max_height=360) == 360
