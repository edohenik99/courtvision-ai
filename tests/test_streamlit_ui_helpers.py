from courtvision.streamlit_ui_helpers import (
    dataframe_height,
    env_flag_enabled,
    mutation_actions_enabled,
    raw_diagnostics_visible,
    raw_review_artifacts_visible,
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


def test_raw_diagnostics_hidden_in_demo_mode() -> None:
    assert not raw_diagnostics_visible(True)
    assert raw_diagnostics_visible(False)


def test_raw_review_artifacts_hidden_in_demo_mode() -> None:
    assert not raw_review_artifacts_visible(True)
    assert raw_review_artifacts_visible(False)


def test_dataframe_height_is_compact_and_bounded() -> None:
    assert dataframe_height(0) == 120
    assert dataframe_height(1) == 120
    assert dataframe_height(5) == 266
    assert dataframe_height(100) == 420
    assert dataframe_height(100, max_height=360) == 360
