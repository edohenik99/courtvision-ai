"""Immutable disposable/replay labels exclude genuine prospective promotion."""
import pytest

from tests.test_mlb_hr_prospective_trial import (
    _activate, _run, _settle, _snapshot_tree, trial, workspace,
)


@pytest.mark.parametrize("configuration", [
    {"evidence_kind": "disposable_test"},
    {"evidence_kind": "historical_replay"},
    {"promotion_excluded": True},
    {"evidence_kind": "prospective_research", "promotion_excluded": True},
])
def test_immutable_excluded_control_reports_diagnostics_without_promotion(
    workspace, configuration,
):
    control = _activate(workspace, activation_configuration=configuration)
    _run(workspace, control)
    _settle(workspace, control)
    before = _snapshot_tree(workspace["trial"])
    status = trial.report_prospective_status(
        control_dir=control.control_dir, trial_root=workspace["trial"],
    )
    health = trial.report_prospective_health(
        control_dir=control.control_dir, trial_root=workspace["trial"],
    )
    assert status["promotion_evidence_eligible"] is False
    assert health["promotion_evidence_eligible"] is False
    assert status["gate_progress"]["evidence_category"]["status"] == "fail"
    assert health["gates"]["evidence_category"]["status"] == "fail"
    assert status["counts"]["prospective_operating_dates"] == 0
    assert status["counts"]["diagnostic_operating_dates"] == 1
    assert health["evidence"]["prospective_operating_dates"] == 0
    assert health["evidence"]["diagnostic_operating_dates"] == 1
    assert status["evidence_separation"]["prospective_trial_predictions"] == 0
    assert status["evidence_separation"]["excluded_predictions"] == 1
    assert status["counts"]["settled_predictions"] == 1
    assert status["metrics"]["status"] == "measured"
    assert status["metrics"]["brier_score"] == 0.25
    assert health["performance"]["brier_score"] == 0.25
    assert status["gate_progress"]["prospective_prediction_dates"]["required_value"] == 30
    assert status["gate_progress"]["eligible_predictions"]["required_value"] == 1000
    assert _snapshot_tree(workspace["trial"]) == before


@pytest.mark.parametrize("configuration", [
    None, {"evidence_kind": "prospective_research", "promotion_excluded": False},
])
def test_genuine_or_legacy_control_retains_existing_reporting(workspace, configuration):
    kwargs = {} if configuration is None else {"activation_configuration": configuration}
    control = _activate(workspace, **kwargs)
    _run(workspace, control)
    status = trial.report_prospective_status(
        control_dir=control.control_dir, trial_root=workspace["trial"],
    )
    health = trial.report_prospective_health(
        control_dir=control.control_dir, trial_root=workspace["trial"],
    )
    assert "promotion_evidence_eligible" not in status
    assert "evidence_category" not in status["gate_progress"]
    assert "promotion_evidence_eligible" not in health
    assert "evidence_category" not in health["gates"]
    assert status["counts"]["prospective_operating_dates"] == 1
    assert status["evidence_separation"]["prospective_trial_predictions"] == 1
    assert health["evidence"]["prospective_operating_dates"] == 1
