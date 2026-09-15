"""Offline recovery configuration, task identity, and input revision contracts."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

from tools import mlb_hr_recovery_contract as recovery


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration(tmp_path):
    return {
        "schema_version": "mlb-hr-recovery-v1",
        "repository_root": str(tmp_path / "repository"),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "trial_root": str(tmp_path / "disposable_trial"),
        "control_id": "mlb-hr-control-v1-" + "a" * 20,
        "control_manifest_sha256": "b" * 64,
        "expected_commit": "c" * 40,
        "cutover_operating_date": "2026-09-15",
        "timezone": "America/Toronto",
        "evidence_root": str(tmp_path / "evidence"),
        "odds_directory": str(tmp_path / "odds"),
        "results_csv": str(tmp_path / "results.csv"),
        "supplemental_shadow_enabled": False,
        "allow_task_registration": False,
        "allow_provider_collection": False,
        "evidence_kind": "disposable_test",
        **recovery.RESEARCH_FLAGS,
    }


def _write_config(tmp_path, config):
    path = tmp_path / "explicit_configuration.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, _digest(path)


def test_explicit_configuration_retains_control_identity(tmp_path):
    config = _configuration(tmp_path)
    path, digest = _write_config(tmp_path, config)
    assert recovery.load_configuration(path, digest) == config


@pytest.mark.parametrize("field", ["control_id", "control_manifest_sha256", "trial_root", "expected_commit"])
def test_missing_configuration_pin_has_no_fallback(tmp_path, field):
    config = _configuration(tmp_path)
    config.pop(field)
    path, digest = _write_config(tmp_path, config)
    with pytest.raises(recovery.RecoveryContractError):
        recovery.load_configuration(path, digest)


@pytest.mark.parametrize("control", ["current", "default", "legacy", "", "mlb-hr-control-v1-"])
def test_control_aliases_rejected(tmp_path, control):
    config = _configuration(tmp_path)
    config["control_id"] = control
    path, digest = _write_config(tmp_path, config)
    with pytest.raises(recovery.RecoveryContractError):
        recovery.load_configuration(path, digest)


@pytest.mark.parametrize("field,value", [
    ("supplemental_shadow_enabled", True),
    ("allow_task_registration", True),
    ("allow_provider_collection", True),
    ("evidence_kind", "historical_replay"),
    ("eligible_for_betting", True),
    ("eligible_for_official_pick", True),
    ("approval_status", "approved"),
    ("research_only", False),
    ("research_only", 1),
    ("timezone", "UTC"),
])
def test_rehearsal_cannot_cross_research_boundary(tmp_path, field, value):
    config = _configuration(tmp_path)
    config[field] = value
    path, digest = _write_config(tmp_path, config)
    with pytest.raises(recovery.RecoveryContractError):
        recovery.load_configuration(path, digest)


def test_config_digest_checked_before_consumption(tmp_path):
    path, _ = _write_config(tmp_path, _configuration(tmp_path))
    with pytest.raises(recovery.RecoveryContractError, match="digest mismatch"):
        recovery.load_configuration(path, "0" * 64)


@pytest.mark.parametrize("configured_kind,activation,accepted", [
    ("prospective_research", {}, True),
    ("prospective_research", {"evidence_kind": "prospective_research"}, True),
    ("prospective_research", {"promotion_excluded": False}, True),
    ("prospective_research", {"evidence_kind": "prospective_research", "promotion_excluded": False}, True),
    ("disposable_test", {"evidence_kind": "disposable_test", "promotion_excluded": True}, True),
    ("disposable_test", {}, False),
    ("disposable_test", {"evidence_kind": "disposable_test"}, False),
    ("disposable_test", {"promotion_excluded": True}, False),
    ("disposable_test", {"evidence_kind": "prospective_research", "promotion_excluded": True}, False),
    ("disposable_test", {"evidence_kind": "disposable_test", "promotion_excluded": False}, False),
    ("disposable_test", {"evidence_kind": "disposable_test", "promotion_excluded": 1}, False),
    ("disposable_test", {"evidence_kind": "disposable_test", "promotion_excluded": None}, False),
    ("prospective_research", {"evidence_kind": "disposable_test", "promotion_excluded": True}, False),
    ("prospective_research", {"evidence_kind": "disposable_test", "promotion_excluded": False}, False),
    ("prospective_research", {"evidence_kind": "historical_replay"}, False),
    ("prospective_research", {"evidence_kind": "unknown"}, False),
    ("prospective_research", {"evidence_kind": None}, False),
    ("prospective_research", {"promotion_excluded": True}, False),
    ("prospective_research", {"promotion_excluded": 0}, False),
    ("prospective_research", {"promotion_excluded": None}, False),
])
def test_runtime_binds_immutable_control_evidence_category(
    tmp_path, monkeypatch, configured_kind, activation, accepted,
):
    from courtvision.sports.mlb.training import hr_prospective_trial as trial

    config = _configuration(tmp_path)
    config["evidence_kind"] = configured_kind
    repository = Path(config["repository_root"])
    repository.mkdir()
    control_dir = Path(config["trial_root"]) / "controls" / config["control_id"]
    control_dir.mkdir(parents=True)
    control = {
        "identity_material": {
            "activation_configuration": activation,
            "activation_git_provenance": {"commit": config["expected_commit"]},
        },
        "created_at_utc": "2026-09-15T04:00:00Z",
    }
    manifest = control_dir / "control_manifest_v1.json"
    manifest.write_text(json.dumps(control), encoding="utf-8")
    manifest_before = manifest.read_bytes()
    config["control_manifest_sha256"] = _digest(manifest)
    path, digest = _write_config(tmp_path, config)
    monkeypatch.setattr(recovery, "__file__", str(repository / "tools" / "mlb_hr_recovery_contract.py"))

    def clean_git(arguments, **kwargs):
        assert arguments[:4] == ["git", "--no-optional-locks", "-C", str(repository)]
        if arguments[4:] == ["rev-parse", "HEAD"]:
            return config["expected_commit"] + "\n"
        assert arguments[4:] == ["status", "--porcelain=v1", "--untracked-files=all"]
        return ""

    def verified_control(actual_dir, **kwargs):
        assert actual_dir == control_dir
        assert kwargs == {
            "trial_root": Path(config["trial_root"]),
            "repository_root": repository,
            "revalidate_model": True,
        }
        return control, config["control_manifest_sha256"], control_dir

    monkeypatch.setattr(recovery.subprocess, "check_output", clean_git)
    monkeypatch.setattr(trial, "_read_control", verified_control)
    monkeypatch.setattr(trial, "_git_from_mapping", lambda mapping: mapping)
    monkeypatch.setattr(trial, "_validate_current_git", lambda frozen, root: None)
    if accepted:
        result = recovery.validate_runtime(path, digest)
        assert result["evidence_kind"] == configured_kind
        assert result["control_dir"] == str(control_dir)
    else:
        with pytest.raises(recovery.RecoveryContractError, match="configuration requires"):
            recovery.validate_runtime(path, digest)
    assert manifest.read_bytes() == manifest_before
    assert control["identity_material"]["activation_configuration"] == activation


def _expected_task():
    return {"TaskName": recovery.PARENTS["closing"], "TaskPath": "\\",
            "Execute": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            "Arguments": '-File "versioned-closing.ps1" -ConfigurationSha256 "' + "a" * 64 + '"',
            "WorkingDirectory": "C:\\isolated-runtime"}


def _task(expected):
    return {"TaskName": expected["TaskName"], "TaskPath": expected["TaskPath"],
            "Actions": [{key: expected[key] for key in ("Execute", "Arguments", "WorkingDirectory")}],
            "Enabled": False, "State": "Disabled"}


def test_missing_task_is_read_only_no_action():
    assert recovery.verify_task_identity([], _expected_task()) == "MISSING_NO_ACTION"


def test_disabled_task_remains_disabled():
    expected = _expected_task()
    task = _task(expected)
    before = json.dumps(task, sort_keys=True)
    assert recovery.verify_task_identity([task], expected) == "EXACT_DISABLED_NO_ACTION"
    assert json.dumps(task, sort_keys=True) == before


@pytest.mark.parametrize("field,value", [
    ("Execute", "cmd.exe"),
    ("Arguments", '-File "schedule_mlb_hr_shadow_cluster_closing.ps1"'),
    ("WorkingDirectory", "C:\\retired-runtime"),
])
def test_action_identity_mismatch_fails_closed(field, value):
    expected = _expected_task()
    task = _task(expected)
    task["Actions"][0][field] = value
    with pytest.raises(recovery.RecoveryContractError, match="identity mismatch"):
        recovery.verify_task_identity([task], expected)


def test_ambiguous_task_cannot_bind_closing():
    expected = _expected_task()
    with pytest.raises(recovery.RecoveryContractError, match="ambiguous"):
        recovery.verify_task_identity([_task(expected), _task(expected)], expected)


@pytest.mark.parametrize("state,enabled", [("Ready", True), ("Running", False), ("Disabled", None)])
def test_uncontained_or_unknown_task_refused(state, enabled):
    expected = _expected_task()
    task = _task(expected)
    task.update(State=state, Enabled=enabled)
    with pytest.raises(recovery.RecoveryContractError, match="contained"):
        recovery.verify_task_identity([task], expected)


def _input_sources(tmp_path, game_type="R"):
    odds = tmp_path / "odds.csv"
    row = {"event_id": "provider-event", "commence_time": "2026-09-15T23:00:00Z",
           "home_team": "Toronto Blue Jays", "away_team": "New York Yankees",
           "market": "batter_home_runs_alternate", "side": "Over", "point": "0.5"}
    with odds.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    schedule = tmp_path / "schedule.json"
    game = {"gamePk": 12345, "gameType": game_type, "gameDate": row["commence_time"],
            "teams": {"home": {"team": {"name": row["home_team"]}},
                      "away": {"team": {"name": row["away_team"]}}}}
    schedule.write_text(json.dumps({"dates": [{"date": "2026-09-15", "games": [game]}]}), encoding="utf-8")
    return odds, schedule


def test_schedule_enrichment_preserves_source_bytes(tmp_path):
    odds, schedule = _input_sources(tmp_path)
    original = odds.read_bytes(), schedule.read_bytes()
    output = tmp_path / "new_revision.csv"
    result = recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
        schedule_path=schedule, schedule_sha256=_digest(schedule),
        operating_date="2026-09-15", output=output)
    row = next(csv.DictReader(io_text(output)))
    assert row["game_type"] == "R"
    assert row["game_pk"] == "12345"
    assert row["schedule_sha256"] == _digest(schedule)
    assert (odds.read_bytes(), schedule.read_bytes()) == original
    assert result["eligible_for_betting"] is False
    assert result["eligible_for_official_pick"] is False


def io_text(path):
    return path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("kind", ["S", "F", "D", "L", "W", "A", ""])
def test_only_authoritative_regular_season_is_accepted(tmp_path, kind):
    odds, schedule = _input_sources(tmp_path, kind)
    output = tmp_path / "must_not_exist.csv"
    with pytest.raises(recovery.RecoveryContractError, match="regular-season"):
        recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
            schedule_path=schedule, schedule_sha256=_digest(schedule),
            operating_date="2026-09-15", output=output)
    assert not output.exists()


def test_enrichment_never_overwrites_existing_evidence(tmp_path):
    odds, schedule = _input_sources(tmp_path)
    output = tmp_path / "existing.csv"
    output.write_bytes(b"historical bytes")
    with pytest.raises(FileExistsError):
        recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
            schedule_path=schedule, schedule_sha256=_digest(schedule),
            operating_date="2026-09-15", output=output)
    assert output.read_bytes() == b"historical bytes"


@pytest.mark.parametrize("game_pk", [True, False, 0, -1, "12345", "invalid", None])
def test_enrichment_requires_positive_integer_official_identity(tmp_path, game_pk):
    odds, schedule = _input_sources(tmp_path)
    document = json.loads(schedule.read_text(encoding="utf-8"))
    document["dates"][0]["games"][0]["gamePk"] = game_pk
    schedule.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "must_not_exist.csv"
    with pytest.raises(recovery.RecoveryContractError, match="regular-season"):
        recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
            schedule_path=schedule, schedule_sha256=_digest(schedule),
            operating_date="2026-09-15", output=output)
    assert not output.exists()


@pytest.mark.parametrize("malformation", ["duplicate_header", "missing_column", "extra_value", "missing_value"])
def test_ambiguous_csv_structure_fails_before_new_revision(tmp_path, malformation):
    odds, schedule = _input_sources(tmp_path)
    lines = odds.read_text(encoding="utf-8").splitlines()
    if malformation == "duplicate_header":
        lines[0] += ",market"
        lines[1] += ",batter_home_runs_alternate"
    elif malformation == "missing_column":
        lines[0] = lines[0].replace("market", "wrong_column")
    elif malformation == "extra_value":
        lines[1] += ",unexpected"
    else:
        lines[1] = lines[1].rsplit(",", 1)[0]
    odds.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = tmp_path / "must_not_exist.csv"
    with pytest.raises(recovery.RecoveryContractError, match="headers"):
        recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
            schedule_path=schedule, schedule_sha256=_digest(schedule),
            operating_date="2026-09-15", output=output)
    assert not output.exists()


def _directory_alias(tmp_path):
    target = tmp_path / "real_directory"
    target.mkdir()
    alias = tmp_path / "alias_directory"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")
    return target, alias


def test_bound_input_rejects_parent_symlink(tmp_path):
    target, alias = _directory_alias(tmp_path)
    source = target / "source.json"
    source.write_text("{}", encoding="utf-8")
    with pytest.raises(recovery.RecoveryContractError, match="path component"):
        recovery.read_bound(alias / source.name, _digest(source))


def test_configuration_rejects_parent_symlink_even_for_new_namespace(tmp_path):
    _, alias = _directory_alias(tmp_path)
    config = _configuration(tmp_path)
    config["trial_root"] = str(alias / "new_disposable_trial")
    path, digest = _write_config(tmp_path, config)
    with pytest.raises(recovery.RecoveryContractError, match="path component"):
        recovery.load_configuration(path, digest)


def test_enrichment_rejects_output_parent_alias_before_writing(tmp_path):
    target, alias = _directory_alias(tmp_path)
    odds, schedule = _input_sources(tmp_path)
    output = alias / "must_not_exist.csv"
    with pytest.raises(recovery.RecoveryContractError, match="path component"):
        recovery.enrich_odds(odds_path=odds, odds_sha256=_digest(odds),
            schedule_path=schedule, schedule_sha256=_digest(schedule),
            operating_date="2026-09-15", output=output)
    assert not (target / output.name).exists()


def test_all_versioned_wrappers_require_same_configuration():
    root = Path(__file__).resolve().parents[1] / "automation" / "mlb_hr_recovery"
    for name in ("run_mlb_hr_dynamic_prediction.ps1", "run_mlb_hr_dynamic_closing.ps1",
                 "schedule_mlb_hr_dynamic_prediction.ps1", "schedule_mlb_hr_dynamic_closing.ps1",
                 "run_mlb_hr_nightly_controller.ps1", "run_mlb_hr_pinned_finalizer.ps1",
                 "run_mlb_hr_postfinalizer_v2.ps1"):
        source = (root / name).read_text(encoding="utf-8-sig")
        assert "$ConfigurationPath" in source
        assert "$ConfigurationSha256" in source
        assert "Initialize-RecoveryRuntime" in source
        assert "fe6432b8a97716155042ea9dc0116a529d4730a7" not in source
        assert "422ed82da410d2721bb2" not in source
        assert "& py -3.13" not in source


def test_parent_schedulers_preserve_existing_tasks():
    root = Path(__file__).resolve().parents[1] / "automation" / "mlb_hr_recovery"
    for suffix in ("prediction", "closing"):
        source = (root / f"schedule_mlb_hr_dynamic_{suffix}.ps1").read_text(encoding="utf-8-sig")
        registration = source.split("Register-ScheduledTask", 1)[1]
        assert "-Force" not in registration.split("Out-Null", 1)[0]
        assert "Assert-RecoveryNewTask" in source
        assert "allow_task_registration" in source
        assert "$controlId" in source
        assert ".Enabled = $false" in source
        assert "$taskExecutable = $powershellExecutable" in source
        assert "Get-Command powershell.exe" not in source

    common = (root / "recovery_common.ps1").read_text(encoding="utf-8-sig")
    assert "[Environment]::GetFolderPath('System')" in common
    assert "'WindowsPowerShell\\v1.0\\powershell.exe'" in common
    assert "$Execute -cne $powershellExecutable" in common


def test_fresh_and_existing_publication_shadow_hooks_are_both_guarded():
    root = Path(__file__).resolve().parents[1] / "automation" / "mlb_hr_recovery"
    source = (root / "run_mlb_hr_dynamic_prediction.ps1").read_text(encoding="utf-8-sig")
    replay = source.split("if ($existingPrediction) {", 1)[1].split("# 2. Frozen Git preflight", 1)[0]
    fresh = source.split("$published = $publishText | ConvertFrom-Json", 1)[1]
    guard = re.compile(
        r"if \(\$recoveryConfig\.supplemental_shadow_enabled\) \{\s*"
        r"(?:try \{\s*)?\$null = Invoke-ShadowSchedulerAfterPublication"
    )
    for branch in (replay, fresh):
        assert branch.count("Invoke-ShadowSchedulerAfterPublication") == 1
        assert guard.search(branch)
    helper = (root / "post_publish_shadow_scheduler.ps1").read_text(encoding="utf-8-sig")
    assert "throw 'Supplemental shadow scheduling is disabled" in helper
    assert "& powershell.exe" not in helper


def test_prediction_preflight_precedes_replay_and_filesystem_side_effects():
    root = Path(__file__).resolve().parents[1] / "automation" / "mlb_hr_recovery"
    source = (root / "run_mlb_hr_dynamic_prediction.ps1").read_text(encoding="utf-8-sig")
    preflight = source.index(". Initialize-RecoveryRuntime -ConfigurationPath")
    assert preflight < source.index("New-Item") < source.index("if ($existingPrediction)")
    replay = source.split("if ($existingPrediction) {", 1)[1].split("# 2. Frozen Git preflight", 1)[0]
    assert replay.index("Assert-RecoveryPublishedEvidence") < replay.index('"REPLAY already_published=')
    assert "Ambiguous prediction publication" in source


def test_completion_markers_bind_configuration_and_validate_before_skip():
    root = Path(__file__).resolve().parents[1] / "automation" / "mlb_hr_recovery"
    for name, skip_text in (
        ("run_mlb_hr_dynamic_closing.ps1", 'Write-Log "SKIP already_completed'),
        ("run_mlb_hr_postfinalizer_v2.ps1", 'Write-Log "SKIP already_completed'),
        ("run_mlb_hr_nightly_controller.ps1", '"DATE_COMPLETE operating_date='),
    ):
        source = (root / name).read_text(encoding="utf-8-sig")
        assert "$evidenceRoot = Join-Path (Join-Path $evidenceRoot $controlId) $ConfigurationSha256" in source
        assert source.index("Read-RecoveryCompletionMarker") < source.index(skip_text)
        assert "Write-RecoveryCompletionMarker" in source
    helper = (root / "recovery_completion.ps1").read_text(encoding="utf-8-sig")
    for binding in ("$marker.operating_date", "$marker.control_id", "$marker.configuration_sha256",
                    "$marker.control_manifest_sha256", "$marker.repository_commit"):
        assert binding in helper
    assert "Assert-RecoveryPublishedEvidence" in helper
    assert "report-prospective-health" in helper
    assert "Get-RecoveryEvidencePrefixDigest" in helper
    assert "[IO.FileMode]::CreateNew" in helper
    assert "Set-Content" not in helper
