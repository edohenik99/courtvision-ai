"""Offline caller-boundary tests; authorization parsing has its own fixtures."""

from pathlib import Path

import pytest

from tools import courtvision_mlb_nightly_pipeline as pipeline


def _arguments() -> list[str]:
    return [
        "--expected-commit", "a" * 40,
        "--authorization-receipt", "fixture-receipt.json",
        "--authorization-id", "cvfa-v1-" + "b" * 64,
        "--operating-date", "2026-09-16",
        "--control-id", "mlb-hr-control-v1-" + "c" * 20,
    ]


@pytest.mark.parametrize(
    "date_arguments",
    [[], ["--date", "2026-09-15"],
     ["--date", "2026-09-16", "--date", "2026-09-17"]],
)
def test_pipeline_rejects_unbound_dates_before_runtime(
    monkeypatch: pytest.MonkeyPatch, date_arguments: list[str],
) -> None:
    monkeypatch.setattr(pipeline, "validate_authorization", lambda *args, **kwargs: {
        "payload": {"source_root": str(pipeline.PROJECT_ROOT)},
        "executing_commit": "a" * 40,
    })
    monkeypatch.setattr(
        pipeline, "run_pipeline",
        lambda **kwargs: pytest.fail("runtime reached despite date mismatch"),
    )
    with pytest.raises(SystemExit) as error:
        pipeline.main(_arguments() + date_arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("substitute_receipt_root", [False, True])
def test_pipeline_rejects_source_root_substitution_before_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    substitute_receipt_root: bool,
) -> None:
    supplied_root = tmp_path if not substitute_receipt_root else pipeline.PROJECT_ROOT
    receipt_root = tmp_path if substitute_receipt_root else pipeline.PROJECT_ROOT
    monkeypatch.setattr(pipeline, "validate_authorization", lambda *args, **kwargs: {
        "payload": {"source_root": str(receipt_root)},
        "executing_commit": "a" * 40,
    })
    monkeypatch.setattr(
        pipeline, "run_pipeline",
        lambda **kwargs: pytest.fail("runtime reached despite source mismatch"),
    )
    with pytest.raises(SystemExit) as error:
        pipeline.main(_arguments() + [
            "--date", "2026-09-16", "--repo-root", str(supplied_root),
        ])
    assert error.value.code == 2


def test_pipeline_forwards_matching_authorized_source_and_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "validate_authorization", lambda *args, **kwargs: {
        "payload": {"source_root": str(pipeline.PROJECT_ROOT)},
        "executing_commit": "a" * 40,
    })
    received: dict[str, object] = {}

    def run(**kwargs: object) -> object:
        received.update(kwargs)
        return type("Result", (), {"exit_code": 0})()

    monkeypatch.setattr(pipeline, "run_pipeline", run)
    assert pipeline.main(_arguments() + ["--date", "2026-09-16"]) == 0
    assert received["target_dates"] == ["2026-09-16"]
    assert received["expected_commit"] == "a" * 40
    assert received["authorization_id"] == "cvfa-v1-" + "b" * 64
