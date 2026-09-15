from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.courtvision_pinned_finalizer_contract import (
    PinnedFinalizerContractError,
    claim_authorization,
    issue_authorization,
    validate_authorization,
    validate_execution_receipt,
    verify_executing_repository,
    write_execution_receipt,
)


NOW = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)
DATE = "2026-08-21"
CONTROL = "mlb-hr-control-v1-" + "1" * 20
RUN = "hrv1-" + "2" * 16


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "CourtVision Test",
        "GIT_AUTHOR_EMAIL": "test@courtvision.invalid",
        "GIT_COMMITTER_NAME": "CourtVision Test",
        "GIT_COMMITTER_EMAIL": "test@courtvision.invalid",
        "GIT_AUTHOR_DATE": "2026-08-21T06:00:00Z",
        "GIT_COMMITTER_DATE": "2026-08-21T06:00:00Z",
    }
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _scratch_git_repository(root: Path) -> tuple[Path, str]:
    repo = root / "source"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "executing_source.txt").write_text("pinned\n", encoding="utf-8")
    _git(repo, "add", "executing_source.txt")
    tree = _git(repo, "write-tree")
    commit = _git(repo, "commit-tree", tree, input_text="pinned fixture\n")
    _git(repo, "update-ref", "HEAD", commit)
    assert _git(repo, "status", "--porcelain") == ""
    return repo, commit


@pytest.fixture
def authorization_fixture(tmp_path: Path) -> dict[str, object]:
    repo, commit = _scratch_git_repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    predictions = evidence / "predictions.csv"
    predictions.write_text("prediction_id\nprediction-1\n", encoding="utf-8")
    prediction_manifest = evidence / "prediction_manifest_v1.json"
    prediction_manifest.write_text('{"prediction_count":1}\n', encoding="utf-8")
    control_manifest = evidence / "control_manifest_v1.json"
    control_manifest.write_text('{"control_id":"fixture"}\n', encoding="utf-8")
    controller = tmp_path / "controller.ps1"
    controller.write_text("# pinned controller\n", encoding="utf-8")
    results = evidence / "results.csv"
    results.write_text("event_id,game_status\nevent-1,final\n", encoding="utf-8")
    authorization = issue_authorization(
        authorization_root=tmp_path / "authorizations",
        repo_root=repo,
        expected_commit=commit,
        operating_date=DATE,
        control_id=CONTROL,
        prediction_run_id=RUN,
        prediction_manifest=prediction_manifest,
        predictions_csv=predictions,
        control_manifest=control_manifest,
        controller_run_id="cvctr-v1-fixture",
        controller_script=controller,
        now=NOW,
    )
    return {
        "repo": repo,
        "commit": commit,
        "predictions": predictions,
        "control_manifest": control_manifest,
        "results": results,
        "authorization": authorization,
        "root": tmp_path,
    }


def _claim(fixture: dict[str, object]) -> dict[str, object]:
    authorization = fixture["authorization"]
    assert isinstance(authorization, dict)
    return claim_authorization(
        authorization["receipt_path"],
        expected_commit=str(fixture["commit"]),
        operating_date=DATE,
        control_id=CONTROL,
        authorization_id=str(authorization["authorization_id"]),
        now=NOW + timedelta(minutes=1),
    )


def test_matching_executing_commit_is_accepted_in_scratch(
    authorization_fixture: dict[str, object],
) -> None:
    identity = verify_executing_repository(
        authorization_fixture["repo"], authorization_fixture["commit"]
    )
    assert identity["executing_commit"] == authorization_fixture["commit"]


def test_missing_or_mismatched_pinned_commit_fails_closed(
    authorization_fixture: dict[str, object],
) -> None:
    with pytest.raises(PinnedFinalizerContractError, match="missing or invalid"):
        verify_executing_repository(authorization_fixture["repo"], "")
    with pytest.raises(PinnedFinalizerContractError, match="commit mismatch"):
        verify_executing_repository(authorization_fixture["repo"], "f" * 40)


def test_finalizer_cannot_substitute_a_different_source_tree(
    authorization_fixture: dict[str, object],
) -> None:
    substitute = Path(authorization_fixture["repo"]) / "substitute"
    substitute.mkdir()
    with pytest.raises(PinnedFinalizerContractError, match="source tree mismatch"):
        verify_executing_repository(substitute, authorization_fixture["commit"])


@pytest.mark.parametrize(
    ("field", "wrong", "message"),
    [
        ("operating_date", "2026-08-22", "operating_date mismatch"),
        ("control_id", "mlb-hr-control-v1-" + "3" * 20, "control_id mismatch"),
        ("expected_commit", "f" * 40, "pinned_commit mismatch"),
    ],
)
def test_wrong_authorization_identity_fails_before_claim(
    authorization_fixture: dict[str, object],
    field: str,
    wrong: str,
    message: str,
) -> None:
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    arguments = {
        "expected_commit": authorization_fixture["commit"],
        "operating_date": DATE,
        "control_id": CONTROL,
    }
    arguments[field] = wrong
    with pytest.raises(PinnedFinalizerContractError, match=message):
        claim_authorization(
            authorization["receipt_path"],
            authorization_id=str(authorization["authorization_id"]),
            now=NOW + timedelta(minutes=1),
            **arguments,
        )


def test_missing_stale_and_reused_authorizations_fail_closed(
    authorization_fixture: dict[str, object], tmp_path: Path
) -> None:
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    common = {
        "expected_commit": authorization_fixture["commit"],
        "operating_date": DATE,
        "control_id": CONTROL,
        "authorization_id": str(authorization["authorization_id"]),
    }
    with pytest.raises(PinnedFinalizerContractError, match="does not exist"):
        claim_authorization(tmp_path / "missing.json", now=NOW, **common)
    with pytest.raises(PinnedFinalizerContractError, match="stale"):
        claim_authorization(
            authorization["receipt_path"],
            now=NOW + timedelta(minutes=31),
            **common,
        )
    claimed = _claim(authorization_fixture)
    assert Path(claimed["receipt_path"]).parent.name == "claimed"
    with pytest.raises(PinnedFinalizerContractError, match="does not exist"):
        claim_authorization(authorization["receipt_path"], now=NOW, **common)


def test_altered_predictions_are_rejected_before_finalization(
    authorization_fixture: dict[str, object],
) -> None:
    predictions = Path(authorization_fixture["predictions"])
    predictions.write_text("prediction_id\nforged\n", encoding="utf-8")
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    with pytest.raises(PinnedFinalizerContractError, match="no longer matches"):
        claim_authorization(
            authorization["receipt_path"],
            expected_commit=str(authorization_fixture["commit"]),
            operating_date=DATE,
            control_id=CONTROL,
            authorization_id=str(authorization["authorization_id"]),
            now=NOW,
        )


def test_execution_receipt_preserves_controller_finalizer_identity(
    authorization_fixture: dict[str, object],
) -> None:
    claimed = _claim(authorization_fixture)
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    output = Path(authorization_fixture["root"]) / "execution.json"
    written = write_execution_receipt(
        authorization_receipt=claimed["receipt_path"],
        output_path=output,
        expected_commit=str(authorization_fixture["commit"]),
        operating_date=DATE,
        control_id=CONTROL,
        authorization_id=str(authorization["authorization_id"]),
        now=NOW + timedelta(minutes=2),
        results_csv=authorization_fixture["results"],
    )
    validated = validate_execution_receipt(
        output,
        authorization_receipt=claimed["receipt_path"],
        expected_commit=str(authorization_fixture["commit"]),
        operating_date=DATE,
        control_id=CONTROL,
        authorization_id=str(authorization["authorization_id"]),
        now=NOW + timedelta(minutes=3),
        results_csv=authorization_fixture["results"],
    )
    assert written["executing_commit"] == authorization_fixture["commit"]
    assert validated["expected_commit"] == validated["executing_commit"]
    assert validated["control_manifest_sha256"] == hashlib.sha256(
        Path(authorization_fixture["control_manifest"]).read_bytes()
    ).hexdigest()


def test_changed_control_manifest_is_rejected_before_claim(
    authorization_fixture: dict[str, object],
) -> None:
    Path(authorization_fixture["control_manifest"]).write_text(
        '{"control_id":"changed"}\n', encoding="utf-8"
    )
    with pytest.raises(PinnedFinalizerContractError, match="control_manifest_sha256"):
        _claim(authorization_fixture)


def test_changed_control_manifest_is_rejected_before_execution_receipt(
    authorization_fixture: dict[str, object],
) -> None:
    claimed = _claim(authorization_fixture)
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    Path(authorization_fixture["control_manifest"]).write_text(
        '{"control_id":"changed"}\n', encoding="utf-8"
    )
    output = Path(authorization_fixture["root"]) / "execution.json"
    with pytest.raises(PinnedFinalizerContractError, match="control_manifest_sha256"):
        write_execution_receipt(
            authorization_receipt=claimed["receipt_path"],
            output_path=output,
            expected_commit=str(authorization_fixture["commit"]),
            operating_date=DATE,
            control_id=CONTROL,
            authorization_id=str(authorization["authorization_id"]),
            now=NOW + timedelta(minutes=2),
            results_csv=authorization_fixture["results"],
        )
    assert not output.exists()


def test_receipt_content_tamper_is_rejected(
    authorization_fixture: dict[str, object],
) -> None:
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    path = Path(str(authorization["receipt_path"]))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["predictions_csv_sha256"] = hashlib.sha256(b"forged").hexdigest()
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(PinnedFinalizerContractError, match="content hash mismatch"):
        validate_authorization(
            path,
            expected_state="pending",
            expected_commit=str(authorization_fixture["commit"]),
            operating_date=DATE,
            control_id=CONTROL,
            authorization_id=str(authorization["authorization_id"]),
            now=NOW,
        )


@pytest.mark.parametrize("substitute_path", [False, True])
def test_strict_result_revision_change_is_rejected_before_consumption(
    authorization_fixture: dict[str, object], substitute_path: bool,
) -> None:
    claimed = _claim(authorization_fixture)
    authorization = authorization_fixture["authorization"]
    assert isinstance(authorization, dict)
    results = Path(authorization_fixture["results"])
    output = Path(authorization_fixture["root"]) / "execution.json"
    common = {
        "authorization_receipt": claimed["receipt_path"],
        "expected_commit": str(authorization_fixture["commit"]),
        "operating_date": DATE, "control_id": CONTROL,
        "authorization_id": str(authorization["authorization_id"]),
    }
    write_execution_receipt(
        output_path=output, results_csv=results,
        now=NOW + timedelta(minutes=2), **common,
    )
    if substitute_path:
        substitute = results.with_name("substitute.csv")
        substitute.write_bytes(results.read_bytes())
        results = substitute
        message = "strict results path mismatch"
    else:
        results.write_text("event_id,game_status\nevent-1,void\n", encoding="utf-8")
        message = "changed after finalization"
    with pytest.raises(PinnedFinalizerContractError, match=message):
        validate_execution_receipt(
            output, results_csv=results,
            now=NOW + timedelta(minutes=3), **common,
        )


def test_finalizer_runtime_paths_contain_no_source_mutating_git_commands() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "tools" / "courtvision_mlb_nightly_pipeline.py",
        root / "tools" / "run_courtvision_mlb_nightly_pipeline.ps1",
        root / "tools" / "courtvision_pinned_finalizer_contract.py",
    )
    forbidden = (
        "git checkout",
        "git switch",
        "git pull",
        "git reset",
        "git merge",
        "git rebase",
    )
    text = "\n".join(path.read_text(encoding="utf-8").casefold() for path in paths)
    assert all(command not in text for command in forbidden)


def test_standalone_wrapper_requires_identity_bound_authorization() -> None:
    wrapper = (
        Path(__file__).resolve().parents[1]
        / "automation" / "mlb_hr_recovery" / "run_mlb_hr_pinned_finalizer.ps1"
    )
    text = wrapper.read_text(encoding="utf-8")
    for required in (
        "AuthorizationReceipt",
        "AuthorizationId",
        "ExpectedCommit",
        "ControlId",
        "claim",
    ):
        assert required in text
