from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import shutil
import socket
import urllib.request

import pytest

import scripts.mlb_build_hr_local_dataset as cli


FIXTURES = Path(__file__).parent / "fixtures" / "mlb"
FULL_LOCAL_ARGS = (
    "--statcast-csv",
    str(FIXTURES / "statcast_sample.csv"),
    "--retrosheet-games-csv",
    str(FIXTURES / "retrosheet_games_sample.csv"),
    "--retrosheet-events-csv",
    str(FIXTURES / "retrosheet_events_sample.csv"),
    "--weather-csv",
    str(FIXTURES / "weather_sample.csv"),
    "--ballpark-csv",
    str(FIXTURES / "ballpark_factors_sample.csv"),
)
EXPECTED_PACK_FILES = {
    "dataset.csv",
    "metadata.json",
    "audit.json",
    "source_manifest.json",
    "build_summary.txt",
    "readiness.json",
    "readiness_summary.txt",
}
STATCAST_TRIAL_ARGS = (
    "--statcast-csv",
    str(FIXTURES / "statcast_sample.csv"),
    "--statcast-trial",
)
EXPECTED_STATCAST_TRIAL_PACK_FILES = {
    "statcast_preview.json",
    "source_manifest.json",
    "build_summary.txt",
}
LABEL_PAIRING_TRIAL_ARGS = (
    "--statcast-csv",
    str(FIXTURES / "statcast_sample.csv"),
    "--retrosheet-games-csv",
    str(FIXTURES / "retrosheet_games_sample.csv"),
    "--retrosheet-events-csv",
    str(FIXTURES / "retrosheet_events_sample.csv"),
    "--label-pairing-trial",
)
CONTEXT_PAIRING_TRIAL_ARGS = (*FULL_LOCAL_ARGS, "--context-pairing-trial")
ODDS_PAIRING_TRIAL_ARGS = (
    *FULL_LOCAL_ARGS,
    "--odds-csv",
    str(FIXTURES / "hr_odds_snapshot_sample.csv"),
    "--odds-pairing-trial",
)


@pytest.mark.parametrize(
    "args",
    [
        LABEL_PAIRING_TRIAL_ARGS,
        CONTEXT_PAIRING_TRIAL_ARGS,
        ODDS_PAIRING_TRIAL_ARGS,
    ],
)
def test_pairing_trials_print_readiness_summary(args, capsys) -> None:
    assert cli.main(args) == 0
    output = capsys.readouterr().out

    for field_name in (
        "readiness_status",
        "readiness_score",
        "blocking_issue_count",
        "warning_issue_count",
        "dataset_row_count",
        "label_available_count",
        "full_context_count",
        "odds_attached_count",
        "leakage_error_count",
        "leakage_warning_count",
    ):
        assert f"{field_name}:" in output
    assert "approval_status: not_approved" in output


def test_fixtures_mode_runs_keylessly_prints_counts_and_audit(
    monkeypatch, capsys
) -> None:
    for name in ("ODDS_API_KEY", "THE_ODDS_API_KEY", "MLB_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert cli.main(["--fixtures"]) == 0
    output = capsys.readouterr().out

    assert "mode: fixtures" in output
    assert "statcast rows: 2" in output
    assert "retrosheet games: 4" in output
    assert "retrosheet events: 2" in output
    assert "weather rows: 3" in output
    assert "ballpark rows: 3" in output
    assert "HR batter-game rows: 4" in output
    assert "audit errors: 0" in output
    assert "audit warnings:" in output
    assert "audit passed: true" in output
    assert "approval_status: not_approved" in output
    assert "readiness_status: READY_FOR_LARGER_HISTORICAL_BUILD" in output
    assert "readiness_score:" in output
    assert "blocking_issue_count: 0" in output
    assert "warning_issue_count:" in output
    assert "dataset_row_count: 4" in output
    assert "label_available_count: 4" in output
    assert "full_context_count: 2" in output
    assert "odds_attached_count: 0" in output
    assert "leakage_error_count: 0" in output
    assert "leakage_warning_count: 16" in output

    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(rows) == 4
    assert tuple(rows[0]) == cli.DISPLAY_FIELDS
    assert all(row["approval_status"] == "not_approved" for row in rows)


def test_default_dry_run_writes_no_files_and_never_calls_network(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.chdir(tmp_path)

    assert cli.main(["--fixtures"]) == 0
    capsys.readouterr()
    assert not tuple(tmp_path.rglob("*"))


def test_explicit_outputs_write_default_deny_artifacts(tmp_path, capsys) -> None:
    csv_path = tmp_path / "dataset.csv"
    audit_path = tmp_path / "audit.json"
    metadata_path = tmp_path / "metadata.json"

    assert cli.main(
        [
            "--fixtures",
            "--output-csv",
            str(csv_path),
            "--audit-json",
            str(audit_path),
            "--metadata-json",
            str(metadata_path),
        ]
    ) == 0
    capsys.readouterr()

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert rows
    assert all(row["eligible_for_betting"] == "False" for row in rows)
    assert all(row["kelly_eligible"] == "False" for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)
    assert audit["eligible_for_betting"] is False
    assert audit["kelly_eligible"] is False
    assert audit["approval_status"] == "not_approved"
    assert metadata["eligible_for_betting"] is False
    assert metadata["kelly_eligible"] is False
    assert metadata["approval_status"] == "not_approved"


def test_fixture_output_dir_writes_complete_reproducibility_pack(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "fixture_pack"

    assert cli.main(["--fixtures", "--output-dir", str(output_dir)]) == 0
    capsys.readouterr()

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_PACK_FILES
    with (output_dir / "dataset.csv").open(encoding="utf-8", newline="") as handle:
        dataset_rows = list(csv.DictReader(handle))
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        (output_dir / "metadata.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")
    readiness = json.loads(
        (output_dir / "readiness.json").read_text(encoding="utf-8")
    )
    readiness_summary = (output_dir / "readiness_summary.txt").read_text(
        encoding="utf-8"
    )

    assert len(dataset_rows) == 4
    assert audit["approval_status"] == "not_approved"
    assert audit["eligible_for_betting"] is False
    assert audit["kelly_eligible"] is False
    assert metadata["approval_status"] == "not_approved"
    assert metadata["eligible_for_betting"] is False
    assert metadata["kelly_eligible"] is False
    assert metadata["schema_version"] == "1.0"
    assert manifest["mode"] == "fixtures"
    assert manifest["source_classification"] == "fixture"
    assert manifest["approval_status"] == "not_approved"
    assert manifest["eligible_for_betting"] is False
    assert manifest["kelly_eligible"] is False
    assert manifest["created_at"]
    assert manifest["dataset_date_range_start"] == "2025-04-01"
    assert manifest["dataset_date_range_end"] == "2025-04-02"
    assert manifest["dataset_schema_version"] == "1.0"
    assert manifest["audit"]["passed"] is True
    assert readiness["readiness_status"] == "READY_FOR_LARGER_HISTORICAL_BUILD"
    assert readiness["blocking_issue_count"] == 0
    assert readiness["approval_status"] == "not_approved"
    assert readiness["eligible_for_betting"] is False
    assert readiness["kelly_eligible"] is False
    assert "historical research only" in readiness_summary
    assert "readiness_status = READY_FOR_LARGER_HISTORICAL_BUILD" in readiness_summary

    entries = {entry["source_name"]: entry for entry in manifest["sources"]}
    assert set(entries) == {
        "statcast",
        "retrosheet_games",
        "retrosheet_events",
        "weather",
        "ballpark_factors",
    }
    assert {name: entry["parsed_row_count"] for name, entry in entries.items()} == {
        "statcast": 2,
        "retrosheet_games": 4,
        "retrosheet_events": 2,
        "weather": 3,
        "ballpark_factors": 3,
    }
    assert all(entry["source_type"] == "fixture" for entry in entries.values())
    assert all(
        entry["source_classification"] == "fixture" for entry in entries.values()
    )
    assert all(entry["provider_label"] for entry in entries.values())
    assert all(entry["created_at"] for entry in entries.values())
    assert all(entry["file_exists"] is True for entry in entries.values())
    assert all(entry["loaded_successfully"] is True for entry in entries.values())
    assert all(entry["byte_size"] > 0 for entry in entries.values())
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        for entry in entries.values()
    )

    for expected in (
        "mode = fixtures",
        "statcast row count = 2",
        "retrosheet game count = 4",
        "retrosheet event count = 2",
        "weather row count = 3",
        "ballpark row count = 3",
        "HR batter-game dataset row count = 4",
        "training eligible row count = 2",
        "backtest eligible row count = 2",
        "audit errors = 0",
        "audit warnings = 16",
        "audit passed = true",
        "readiness_status = READY_FOR_LARGER_HISTORICAL_BUILD",
        "blocking_issue_count = 0",
        "approval_status = not_approved",
        "eligible_for_betting = false",
        "kelly_eligible = false",
        "historical research only",
        "not production approved",
    ):
        assert expected in summary


def test_explicit_readiness_reports_are_opt_in_and_overwrite_protected(
    tmp_path, capsys
) -> None:
    json_path = tmp_path / "readiness.json"
    txt_path = tmp_path / "readiness.txt"
    args = (
        "--fixtures",
        "--readiness-report-json",
        str(json_path),
        "--readiness-report-txt",
        str(txt_path),
    )

    assert cli.main(args) == 0
    capsys.readouterr()
    assert json.loads(json_path.read_text(encoding="utf-8"))["dataset_row_count"] == 4
    assert "approval_status = not_approved" in txt_path.read_text(encoding="utf-8")

    assert cli.main(args) == 2
    assert "already exists" in capsys.readouterr().err
    assert cli.main((*args, "--overwrite")) == 0
    capsys.readouterr()


def test_fixture_pack_outputs_are_reproducible_apart_from_timestamps(
    tmp_path, capsys
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    assert cli.main(["--fixtures", "--output-dir", str(first)]) == 0
    capsys.readouterr()
    assert cli.main(["--fixtures", "--output-dir", str(second)]) == 0
    capsys.readouterr()

    assert (first / "dataset.csv").read_bytes() == (second / "dataset.csv").read_bytes()

    first_audit = json.loads((first / "audit.json").read_text(encoding="utf-8"))
    second_audit = json.loads((second / "audit.json").read_text(encoding="utf-8"))
    first_audit.pop("checked_at")
    second_audit.pop("checked_at")
    assert first_audit == second_audit

    first_metadata = json.loads(
        (first / "metadata.json").read_text(encoding="utf-8")
    )
    second_metadata = json.loads(
        (second / "metadata.json").read_text(encoding="utf-8")
    )
    for field_name in ("schema_version", "dataset_id", "row_count", "sport", "league"):
        assert first_metadata[field_name] == second_metadata[field_name]


def test_output_dir_refuses_existing_targets_and_overwrite_succeeds(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "pack"

    assert cli.main(["--fixtures", "--output-dir", str(output_dir)]) == 0
    capsys.readouterr()
    original = (output_dir / "source_manifest.json").read_text(encoding="utf-8")

    assert cli.main(["--fixtures", "--output-dir", str(output_dir)]) == 2
    assert "build pack target already exists" in capsys.readouterr().err
    assert (output_dir / "source_manifest.json").read_text(encoding="utf-8") == original

    assert cli.main(
        ["--fixtures", "--output-dir", str(output_dir), "--overwrite"]
    ) == 0
    capsys.readouterr()
    assert {path.name for path in output_dir.iterdir()} == EXPECTED_PACK_FILES


def test_output_dir_and_explicit_output_flags_are_mutually_exclusive(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "pack"
    explicit = tmp_path / "dataset.csv"

    assert cli.main(
        [
            "--fixtures",
            "--output-dir",
            str(output_dir),
            "--output-csv",
            str(explicit),
        ]
    ) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not output_dir.exists()
    assert not explicit.exists()


def test_local_file_pack_marks_sources_as_local_files(tmp_path, capsys) -> None:
    output_dir = tmp_path / "local_pack"

    assert cli.main((*FULL_LOCAL_ARGS, "--output-dir", str(output_dir))) == 0
    capsys.readouterr()
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["sources"]) == 5
    assert all(
        entry["source_type"] == "local_file" for entry in manifest["sources"]
    )


def test_build_pack_avoids_forbidden_human_facing_language(tmp_path, capsys) -> None:
    output_dir = tmp_path / "pack"
    assert cli.main(["--fixtures", "--output-dir", str(output_dir)]) == 0
    capsys.readouterr()

    payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output_dir.iterdir()
    ).lower()
    forbidden = (
        "betting recommendation",
        "staking",
        "unit sizing",
        "bankroll",
        "elite pick",
        "guaranteed edge",
    )
    assert not any(term in payload for term in forbidden)


def test_existing_output_refuses_overwrite_and_overwrite_replaces(
    tmp_path, capsys
) -> None:
    output = tmp_path / "dataset.csv"
    output.write_text("existing\n", encoding="utf-8")

    assert cli.main(["--fixtures", "--output-csv", str(output)]) == 2
    captured = capsys.readouterr()
    assert "already exists" in captured.err
    assert output.read_text(encoding="utf-8") == "existing\n"

    assert cli.main(
        ["--fixtures", "--output-csv", str(output), "--overwrite"]
    ) == 0
    capsys.readouterr()
    assert output.read_text(encoding="utf-8").startswith("sport,league,")


def test_missing_path_and_no_inputs_fail_clearly(tmp_path, capsys) -> None:
    assert cli.main([]) == 2
    assert "provide --fixtures or explicit local CSV paths" in capsys.readouterr().err

    missing = tmp_path / "missing.csv"
    assert cli.main(["--statcast-csv", str(missing), "--allow-partial"]) == 2
    assert "path does not exist" in capsys.readouterr().err


def test_unparseable_csv_fails_clearly(tmp_path, capsys) -> None:
    malformed = tmp_path / "malformed_statcast.csv"
    malformed.write_text("not,a,statcast,header\n1,2,3,4\n", encoding="utf-8")

    assert cli.main(
        ["--statcast-csv", str(malformed), "--allow-partial"]
    ) == 2
    error = capsys.readouterr().err
    assert "Statcast CSV is missing required columns" in error


def test_partial_inputs_require_opt_in_and_report_missing_sources(capsys) -> None:
    statcast = str(FIXTURES / "statcast_sample.csv")

    assert cli.main(["--statcast-csv", statcast]) == 2
    assert "partial local inputs require --allow-partial" in capsys.readouterr().err

    assert cli.main(
        ["--statcast-csv", statcast, "--allow-partial"]
    ) == 0
    output = capsys.readouterr().out
    assert "mode: local_files" in output
    assert "source warnings: 4" in output
    assert "context was not fabricated" in output
    assert "audit passed:" in output


def test_full_explicit_local_file_mode_builds_rows(capsys) -> None:
    assert cli.main(FULL_LOCAL_ARGS) == 0
    output = capsys.readouterr().out
    assert "mode: local_files" in output
    assert "HR batter-game rows: 4" in output
    assert "source warnings: 0" in output


def test_human_facing_output_avoids_forbidden_terms(capsys) -> None:
    assert cli.main(["--fixtures"]) == 0
    output = capsys.readouterr().out.lower()
    forbidden = (
        "betting recommendation",
        "staking",
        "kelly",
        "bankroll",
        "elite pick",
        "production approval",
        "guaranteed edge",
    )
    assert not any(term in output for term in forbidden)


def test_statcast_trial_runs_with_only_local_csv_and_builds_no_dataset(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("non-Statcast trial dependency was called")

    for name in (
        "ingest_local_retrosheet_csvs",
        "ingest_local_weather_csv",
        "ingest_local_ballpark_factors_csv",
        "build_hr_batter_game_rows_from_sources",
    ):
        monkeypatch.setattr(cli, name, fail_if_called)
    monkeypatch.setattr(socket, "socket", fail_if_called)
    monkeypatch.setattr(socket, "create_connection", fail_if_called)
    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)
    monkeypatch.chdir(tmp_path)

    assert cli.main(STATCAST_TRIAL_ARGS) == 0
    captured = capsys.readouterr()
    output = captured.out

    assert not captured.err
    assert "Statcast parsed successfully" in output
    assert "mode: statcast_trial" in output
    assert "parsed Statcast row count: 2" in output
    assert "detected date range: 2025-04-01 to 2025-04-02" in output
    assert "unique game count: 2" in output
    assert "unique batter count: 2" in output
    assert "HR event count: 1" in output
    assert "missing required Statcast column warnings: 0" in output
    assert "Dataset rows require Retrosheet game/event context" in output
    assert "dataset rows not built without Retrosheet labels" in output
    assert "HR batter-game rows: 0" in output
    assert not tuple(tmp_path.rglob("*"))

    preview_rows = [
        json.loads(line) for line in output.splitlines() if line.startswith("{")
    ]
    assert len(preview_rows) == 2
    assert tuple(preview_rows[0]) == tuple(sorted(cli.STATCAST_TRIAL_PREVIEW_FIELDS))
    assert preview_rows[0]["is_home_run"] is True


def test_statcast_trial_output_pack_contains_only_trial_safe_files(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "statcast_trial_pack"

    assert cli.main(
        (*STATCAST_TRIAL_ARGS, "--output-dir", str(output_dir))
    ) == 0
    capsys.readouterr()

    assert {path.name for path in output_dir.iterdir()} == (
        EXPECTED_STATCAST_TRIAL_PACK_FILES
    )
    assert not (output_dir / "dataset.csv").exists()
    assert not (output_dir / "metadata.json").exists()
    assert not (output_dir / "audit.json").exists()

    preview = json.loads(
        (output_dir / "statcast_preview.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")

    assert preview["mode"] == "statcast_trial"
    assert preview["parsed_row_count"] == 2
    assert preview["unique_game_count"] == 2
    assert preview["unique_batter_count"] == 2
    assert preview["hr_event_count"] == 1
    assert preview["dataset_row_count"] == 0
    assert len(preview["rows"]) == 2

    assert manifest["dataset_row_count"] == 0
    assert len(manifest["sources"]) == 1
    source = manifest["sources"][0]
    assert source["source_name"] == "statcast"
    assert source["source_type"] == "local_file"
    assert source["path"] == str((FIXTURES / "statcast_sample.csv").resolve())
    assert source["file_exists"] is True
    assert source["byte_size"] > 0
    assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
    assert source["parsed_row_count"] == 2
    assert source["detected_date_range_start"] == "2025-04-01"
    assert source["detected_date_range_end"] == "2025-04-02"
    assert source["unique_game_count"] == 2
    assert source["unique_batter_count"] == 2
    assert source["hr_event_count"] == 1
    assert source["loaded_successfully"] is True
    assert source["warnings"]

    for expected in (
        "historical research only",
        "local Statcast trial",
        "partial context",
        "not production approved",
        "Statcast parsed successfully",
        "parsed Statcast row count = 2",
        "unique game count = 2",
        "unique batter count = 2",
        "HR event count = 1",
        "Dataset rows require Retrosheet game/event context",
        "dataset rows not built without Retrosheet labels",
        "HR batter-game dataset row count = 0",
    ):
        assert expected in summary


def test_statcast_trial_pack_and_console_avoid_forbidden_language(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "statcast_trial_pack"
    assert cli.main(
        (*STATCAST_TRIAL_ARGS, "--output-dir", str(output_dir))
    ) == 0
    captured = capsys.readouterr()
    payload = captured.out + captured.err + "\n".join(
        path.read_text(encoding="utf-8")
        for path in output_dir.iterdir()
        if path.name != "source_manifest.json"
    )
    source_manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert source_manifest["eligible_for_betting"] is False
    assert source_manifest["kelly_eligible"] is False
    forbidden = (
        "betting recommendation",
        "staking",
        "kelly",
        "bankroll",
        "elite",
        "strong pick",
        "unit sizing",
        "fair probability",
        "production pick",
        "guaranteed edge",
    )
    lowered = payload.lower()
    assert not any(term in lowered for term in forbidden)


def test_statcast_trial_missing_and_malformed_paths_fail_clearly(
    tmp_path, capsys
) -> None:
    missing = tmp_path / "missing.csv"
    assert cli.main(
        ["--statcast-csv", str(missing), "--statcast-trial"]
    ) == 2
    assert "path does not exist" in capsys.readouterr().err

    malformed = tmp_path / "malformed.csv"
    malformed.write_text("not,a,statcast,header\n1,2,3,4\n", encoding="utf-8")
    assert cli.main(
        ["--statcast-csv", str(malformed), "--statcast-trial"]
    ) == 2
    captured = capsys.readouterr()
    assert "missing required Statcast column" in captured.err
    assert "Statcast CSV is missing required columns" in captured.err


def test_statcast_trial_rejects_non_trial_inputs_and_dataset_outputs(
    tmp_path, capsys
) -> None:
    weather = FIXTURES / "weather_sample.csv"
    assert cli.main(
        (*STATCAST_TRIAL_ARGS, "--weather-csv", str(weather))
    ) == 2
    assert "loads only --statcast-csv" in capsys.readouterr().err

    dataset = tmp_path / "dataset.csv"
    assert cli.main((*STATCAST_TRIAL_ARGS, "--output-csv", str(dataset))) == 2
    assert "does not write dataset" in capsys.readouterr().err
    assert not dataset.exists()


def test_label_pairing_trial_builds_retrosheet_labeled_rows_and_audits(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.chdir(tmp_path)

    assert cli.main(LABEL_PAIRING_TRIAL_ARGS) == 0
    captured = capsys.readouterr()
    output = captured.out

    assert not captured.err
    for expected in (
        "mode: label_pairing_trial",
        "statcast rows: 2",
        "retrosheet games: 4",
        "retrosheet events: 2",
        "HR batter-game rows: 2",
        "HR-positive rows: 1",
        "HR-negative rows: 1",
        "label_available rows: 2",
        "game_completed rows: 2",
        "training eligible rows: 0",
        "backtest eligible rows: 0",
        "audit errors: 0",
        "audit warnings: 10",
        "audit passed: true",
        "approval_status: not_approved",
        "unmatched statcast games: 2",
        "unmatched retrosheet games: 4",
        "unmatched batters: 4",
        "retrosheet events without matching batter game rows: 0",
        "statcast rows without game labels: 2",
        "duplicate batter game row ids: 0",
        "missing player ids: 0",
        "missing game ids: 0",
        "missing game dates: 0",
        "weather context missing for 2 rows; values were not fabricated",
        "ballpark context missing for 2 rows; values were not fabricated",
    ):
        assert expected in output
    assert not tuple(tmp_path.rglob("*"))

    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(rows) == 2
    assert {row["hit_hr_today"] for row in rows} == {True, False}
    assert {row["home_run_count"] for row in rows} == {0, 1}
    assert all(row["venue_name"] == "Rogers Centre" for row in rows)
    assert all(row["weather_temperature"] is None for row in rows)
    assert all(row["park_factor_hr"] is None for row in rows)
    assert all(row["eligible_for_training"] is False for row in rows)
    assert all(row["eligible_for_backtest"] is False for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)


def test_label_pairing_trial_output_pack_is_explicit_and_default_deny(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "label_pairing_pack"

    assert cli.main(
        (*LABEL_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))
    ) == 0
    captured = capsys.readouterr()

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_PACK_FILES
    with (output_dir / "dataset.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")

    assert len(rows) == 2
    assert sum(row["hit_hr_today"] == "True" for row in rows) == 1
    assert sum(row["hit_hr_today"] == "False" for row in rows) == 1
    assert all(row["label_available"] == "True" for row in rows)
    assert all(row["label_source"] == "retrosheet" for row in rows)
    assert all(row["game_completed"] == "True" for row in rows)
    assert all(row["eligible_for_betting"] == "False" for row in rows)
    assert all(row["kelly_eligible"] == "False" for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)
    assert all(row["weather_temperature"] == "" for row in rows)
    assert all(row["park_factor_hr"] == "" for row in rows)
    assert metadata["approval_status"] == "not_approved"
    assert audit["passed"] is True
    assert audit["error_count"] == 0
    assert audit["eligible_for_betting"] is False
    assert audit["kelly_eligible"] is False
    assert audit["approval_status"] == "not_approved"
    assert manifest["mode"] == "label_pairing_trial"
    assert manifest["manifest_version"] == "phase5b-label-pairing-trial-v1"
    assert manifest["pairing_quality"]["statcast_rows_without_game_labels"] == 2
    assert len(manifest["sources"]) == 3

    for expected in (
        "historical research only",
        "local label pairing trial",
        "partial context",
        "leakage audit summary",
        "default-deny",
        "mode = label_pairing_trial",
        "statcast row count = 2",
        "retrosheet game count = 4",
        "retrosheet event count = 2",
        "HR batter-game dataset row count = 2",
        "HR-positive row count = 1",
        "HR-negative row count = 1",
        "label_available row count = 2",
        "game_completed row count = 2",
        "audit errors = 0",
        "audit warnings = 10",
        "audit passed = true",
        "approval_status = not_approved",
    ):
        assert expected in summary

    human_output = (captured.out + captured.err + summary).lower()
    forbidden = (
        "betting recommendation",
        "staking",
        "kelly",
        "bankroll",
        "elite",
        "strong pick",
        "unit sizing",
        "fair probability",
        "production approval",
        "guaranteed edge",
    )
    assert not any(term in human_output for term in forbidden)


def test_label_pairing_trial_pack_overwrite_protection(tmp_path, capsys) -> None:
    output_dir = tmp_path / "label_pairing_pack"
    args = (*LABEL_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))

    assert cli.main(args) == 0
    capsys.readouterr()
    original = (output_dir / "dataset.csv").read_bytes()
    assert cli.main(args) == 2
    assert "build pack target already exists" in capsys.readouterr().err
    assert (output_dir / "dataset.csv").read_bytes() == original
    assert cli.main((*args, "--overwrite")) == 0
    capsys.readouterr()


def test_label_pairing_trial_requires_each_local_input(tmp_path, capsys) -> None:
    cases = (
        ("--statcast-csv", "--statcast-csv"),
        ("--retrosheet-games-csv", "--retrosheet-games-csv"),
        ("--retrosheet-events-csv", "--retrosheet-events-csv"),
    )
    for omitted, expected in cases:
        args = list(LABEL_PAIRING_TRIAL_ARGS)
        position = args.index(omitted)
        del args[position : position + 2]
        assert cli.main(args) == 2
        assert expected in capsys.readouterr().err

    missing = tmp_path / "missing.csv"
    args = list(LABEL_PAIRING_TRIAL_ARGS)
    args[args.index("--statcast-csv") + 1] = str(missing)
    assert cli.main(args) == 2
    assert "path does not exist" in capsys.readouterr().err


def test_label_pairing_trial_rejects_later_phase_context_and_explicit_outputs(
    tmp_path, capsys
) -> None:
    assert cli.main(
        (*LABEL_PAIRING_TRIAL_ARGS, "--weather-csv", str(FIXTURES / "weather_sample.csv"))
    ) == 2
    assert "limited to Statcast and Retrosheet" in capsys.readouterr().err

    output = tmp_path / "dataset.csv"
    assert cli.main((*LABEL_PAIRING_TRIAL_ARGS, "--output-csv", str(output))) == 2
    assert "writes only the explicit --output-dir pack" in capsys.readouterr().err
    assert not output.exists()


def test_context_pairing_trial_attaches_local_context_and_runs_audit(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.chdir(tmp_path)

    assert cli.main(CONTEXT_PAIRING_TRIAL_ARGS) == 0
    captured = capsys.readouterr()
    output = captured.out

    assert not captured.err
    for expected in (
        "historical research only",
        "local context pairing trial",
        "local files only",
        "leakage audit summary",
        "default-deny",
        "not production approved",
        "mode: context_pairing_trial",
        "statcast rows: 2",
        "retrosheet games: 4",
        "retrosheet events: 2",
        "weather rows: 3",
        "ballpark rows: 3",
        "HR batter-game rows: 2",
        "HR-positive rows: 1",
        "HR-negative rows: 1",
        "label_available rows: 2",
        "weather-attached rows: 2",
        "ballpark-attached rows: 2",
        "full-context rows: 2",
        "training eligible rows: 2",
        "backtest eligible rows: 2",
        "audit errors: 0",
        "audit warnings: 6",
        "audit passed: true",
        "approval_status: not_approved",
        "unmatched weather rows: 2",
        "games missing weather: 2",
        "games missing ballpark: 2",
        "unmatched venue names: 2",
        "duplicate weather matches: 0",
        "duplicate ballpark matches: 0",
        "weather date mismatch: 0",
        "ballpark venue normalization mismatch: 0",
        "rows with labels but missing weather: 0",
        "rows with labels but missing ballpark: 0",
        "rows with full context: 2",
    ):
        assert expected in output
    assert not tuple(tmp_path.rglob("*"))

    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(rows) == 2
    assert {row["hit_hr_today"] for row in rows} == {True, False}
    assert all(row["weather_temperature"] == 72.0 for row in rows)
    assert all(row["weather_wind_speed"] == 9.5 for row in rows)
    assert all(row["park_factor_hr"] == 1.08 for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)


def test_context_pairing_trial_output_pack_is_explicit_and_default_deny(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "context_pairing_pack"

    assert cli.main(
        (*CONTEXT_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))
    ) == 0
    captured = capsys.readouterr()

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_PACK_FILES
    with (output_dir / "dataset.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")

    assert len(rows) == 2
    assert all(row["weather_temperature"] == "72.0" for row in rows)
    assert all(row["weather_wind_speed"] == "9.5" for row in rows)
    assert all(row["weather_wind_direction"] == "out to center" for row in rows)
    assert all(row["roof_status"] == "open" for row in rows)
    assert all(row["weather_source_type"] == "historical" for row in rows)
    assert all(row["park_factor_hr"] == "1.08" for row in rows)
    assert all(row["altitude"] == "50.0" for row in rows)
    assert all(row["ballpark_source_type"] == "static" for row in rows)
    assert all(row["eligible_for_betting"] == "False" for row in rows)
    assert all(row["kelly_eligible"] == "False" for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)
    assert metadata["approval_status"] == "not_approved"
    assert audit["passed"] is True
    assert audit["eligible_for_betting"] is False
    assert audit["kelly_eligible"] is False
    assert audit["approval_status"] == "not_approved"
    assert manifest["mode"] == "context_pairing_trial"
    assert manifest["manifest_version"] == "phase5c-context-pairing-trial-v1"
    assert len(manifest["sources"]) == 5
    assert manifest["pairing_quality"]["weather_attached_rows"] == 2
    assert manifest["pairing_quality"]["ballpark_attached_rows"] == 2
    assert manifest["pairing_quality"]["rows_with_full_context"] == 2

    for expected in (
        "mode = context_pairing_trial",
        "statcast row count = 2",
        "retrosheet game count = 4",
        "retrosheet event count = 2",
        "weather row count = 3",
        "ballpark row count = 3",
        "HR batter-game dataset row count = 2",
        "HR-positive row count = 1",
        "HR-negative row count = 1",
        "label_available row count = 2",
        "weather-attached row count = 2",
        "ballpark-attached row count = 2",
        "full-context row count = 2",
        "training eligible row count = 2",
        "backtest eligible row count = 2",
        "audit errors = 0",
        "audit warnings = 6",
        "audit passed = true",
        "approval_status = not_approved",
    ):
        assert expected in summary

    human_output = (captured.out + captured.err + summary).lower()
    forbidden = (
        "betting recommendation",
        "staking",
        "kelly",
        "bankroll",
        "elite",
        "strong pick",
        "unit sizing",
        "fair probability",
        "production approval",
        "guaranteed edge",
    )
    assert not any(term in human_output for term in forbidden)


def test_context_pairing_trial_reports_incomplete_context_without_fabrication(
    tmp_path, capsys
) -> None:
    weather_path = tmp_path / "weather_unmatched.csv"
    ballpark_path = tmp_path / "ballpark_unmatched.csv"
    weather_path.write_text(
        (FIXTURES / "weather_sample.csv")
        .read_text(encoding="utf-8")
        .replace("20250401TORBOS-1", "unmatched-game")
        .replace("Rogers Centre", "Unmatched Park"),
        encoding="utf-8",
    )
    ballpark_path.write_text(
        (FIXTURES / "ballpark_factors_sample.csv")
        .read_text(encoding="utf-8")
        .replace("Rogers Centre,TOR", "Unmatched Park,TOR"),
        encoding="utf-8",
    )
    args = list(CONTEXT_PAIRING_TRIAL_ARGS)
    args[args.index("--weather-csv") + 1] = str(weather_path)
    args[args.index("--ballpark-csv") + 1] = str(ballpark_path)

    assert cli.main(args) == 0
    output = capsys.readouterr().out

    assert "weather-attached rows: 0" in output
    assert "ballpark-attached rows: 0" in output
    assert "full-context rows: 0" in output
    assert "rows with labels but missing weather: 2" in output
    assert "rows with labels but missing ballpark: 2" in output
    assert "warning: weather context missing for 2 rows; values were not fabricated" in output
    assert "warning: ballpark context missing for 2 rows; values were not fabricated" in output
    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert all(row["weather_temperature"] is None for row in rows)
    assert all(row["park_factor_hr"] is None for row in rows)


def test_context_pairing_trial_requires_context_paths_and_protects_pack(
    tmp_path, capsys
) -> None:
    for omitted in ("--weather-csv", "--ballpark-csv"):
        args = list(CONTEXT_PAIRING_TRIAL_ARGS)
        position = args.index(omitted)
        del args[position : position + 2]
        assert cli.main(args) == 2
        assert omitted in capsys.readouterr().err

    output_dir = tmp_path / "context_pairing_pack"
    args = (*CONTEXT_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))
    assert cli.main(args) == 0
    capsys.readouterr()
    original = (output_dir / "dataset.csv").read_bytes()
    assert cli.main(args) == 2
    assert "build pack target already exists" in capsys.readouterr().err
    assert (output_dir / "dataset.csv").read_bytes() == original
    assert cli.main((*args, "--overwrite")) == 0
    capsys.readouterr()


def test_odds_pairing_trial_attaches_local_market_references_and_audits(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network access is not allowed")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.chdir(tmp_path)

    assert cli.main(ODDS_PAIRING_TRIAL_ARGS) == 0
    captured = capsys.readouterr()
    output = captured.out

    assert not captured.err
    for expected in (
        "historical research only",
        "local odds snapshot trial",
        "market reference only",
        "local files only",
        "leakage audit summary",
        "default-deny",
        "not production approved",
        "mode: odds_pairing_trial",
        "statcast rows: 2",
        "retrosheet games: 4",
        "retrosheet events: 2",
        "weather rows: 3",
        "ballpark rows: 3",
        "odds snapshot rows: 3",
        "HR batter-game rows: 2",
        "HR-positive rows: 1",
        "HR-negative rows: 1",
        "label_available rows: 2",
        "weather-attached rows: 2",
        "ballpark-attached rows: 2",
        "odds-attached rows: 2",
        "full-context-plus-odds rows: 2",
        "unmatched odds rows: 1",
        "rows missing odds: 0",
        "training eligible rows: 2",
        "backtest eligible rows: 2",
        "audit errors: 0",
        "audit passed: true",
        "approval_status: not_approved",
    ):
        assert expected in output
    assert not tuple(tmp_path.rglob("*"))

    rows = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(rows) == 2
    assert {row["hit_hr_today"] for row in rows} == {True, False}
    assert {row["american_odds"] for row in rows} == {275, 425}
    assert all(row["decimal_odds"] is not None for row in rows)
    assert all(row["implied_probability"] is not None for row in rows)
    assert all(row["odds_is_fresh_for_pregame"] is True for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)


def test_odds_pairing_trial_pack_is_explicit_and_default_deny(
    tmp_path, capsys
) -> None:
    output_dir = tmp_path / "odds_pairing_pack"

    assert cli.main(
        (*ODDS_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))
    ) == 0
    captured = capsys.readouterr()

    assert {path.name for path in output_dir.iterdir()} == EXPECTED_PACK_FILES
    with (output_dir / "dataset.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")

    assert len(rows) == 2
    assert {row["american_odds"] for row in rows} == {"275", "425"}
    assert all(row["decimal_odds"] for row in rows)
    assert all(row["implied_probability"] for row in rows)
    assert all(row["eligible_for_betting"] == "False" for row in rows)
    assert all(row["kelly_eligible"] == "False" for row in rows)
    assert all(row["approval_status"] == "not_approved" for row in rows)
    assert audit["passed"] is True
    assert audit["approval_status"] == "not_approved"
    assert manifest["mode"] == "odds_pairing_trial"
    assert manifest["manifest_version"] == "phase5d-odds-pairing-trial-v1"
    assert len(manifest["sources"]) == 6
    odds_source = next(
        source for source in manifest["sources"] if source["source_name"] == "odds_snapshot"
    )
    assert odds_source["parsed_row_count"] == 3
    assert odds_source["sha256"]
    assert manifest["pairing_quality"]["odds_attached_rows"] == 2
    assert manifest["pairing_quality"]["unmatched_odds_rows"] == 1
    assert manifest["pairing_quality"]["full_context_plus_odds_rows"] == 2

    for expected in (
        "mode = odds_pairing_trial",
        "odds snapshot row count = 3",
        "odds-attached row count = 2",
        "full-context-plus-odds row count = 2",
        "unmatched odds row count = 1",
        "rows missing odds count = 0",
        "audit passed = true",
        "approval_status = not_approved",
    ):
        assert expected in summary

    human_output = (captured.out + captured.err + summary).lower()
    forbidden = (
        "betting recommendation",
        "staking",
        "kelly",
        "bankroll",
        "elite",
        "strong pick",
        "unit sizing",
        "fair probability",
        "production approval",
        "guaranteed edge",
    )
    assert not any(term in human_output for term in forbidden)


def test_odds_pairing_trial_reports_post_start_snapshot_as_stale(
    tmp_path, capsys
) -> None:
    odds_path = tmp_path / "odds_after_start.csv"
    odds_path.write_text(
        (FIXTURES / "hr_odds_snapshot_sample.csv")
        .read_text(encoding="utf-8")
        .replace("2025-04-01T22:15:00Z", "2025-04-01T23:15:00Z", 1),
        encoding="utf-8",
    )
    args = list(ODDS_PAIRING_TRIAL_ARGS)
    args[args.index("--odds-csv") + 1] = str(odds_path)

    assert cli.main(args) == 0
    output = capsys.readouterr().out

    assert "stale odds: 1" in output
    assert "odds timestamp after event start time: 1" in output
    assert "odds timestamp is not pregame" in output


def test_odds_pairing_trial_requires_odds_path_and_protects_pack(
    tmp_path, capsys
) -> None:
    args_without_odds = list(ODDS_PAIRING_TRIAL_ARGS)
    position = args_without_odds.index("--odds-csv")
    del args_without_odds[position : position + 2]
    assert cli.main(args_without_odds) == 2
    assert "--odds-csv" in capsys.readouterr().err

    missing = tmp_path / "missing.csv"
    args_missing = list(ODDS_PAIRING_TRIAL_ARGS)
    args_missing[args_missing.index("--odds-csv") + 1] = str(missing)
    assert cli.main(args_missing) == 2
    assert "path does not exist" in capsys.readouterr().err

    output_dir = tmp_path / "odds_pairing_pack"
    args = (*ODDS_PAIRING_TRIAL_ARGS, "--output-dir", str(output_dir))
    assert cli.main(args) == 0
    capsys.readouterr()
    original = (output_dir / "dataset.csv").read_bytes()
    assert cli.main(args) == 2
    assert "build pack target already exists" in capsys.readouterr().err
    assert (output_dir / "dataset.csv").read_bytes() == original
    assert cli.main((*args, "--overwrite")) == 0
    capsys.readouterr()

# Real historical builds require a semantically aligned, manifested input pack.
HISTORICAL_SAMPLE_ARGS = (
    "--historical-dry-run",
    "--statcast-csv",
    str(cli.FIXTURE_DIR / "statcast_sample.csv"),
    "--retrosheet-games-csv",
    str(cli.FIXTURE_DIR / "retrosheet_games_sample.csv"),
    "--retrosheet-events-csv",
    str(cli.FIXTURE_DIR / "retrosheet_events_sample.csv"),
    "--weather-csv",
    str(cli.FIXTURE_DIR / "weather_sample.csv"),
    "--ballpark-csv",
    str(cli.FIXTURE_DIR / "ballpark_factors_sample.csv"),
    "--odds-csv",
    str(cli.FIXTURE_DIR / "hr_odds_snapshot_sample.csv"),
)


def _real_historical_args(tmp_path: Path) -> tuple[str, ...]:
    source_dir = tmp_path / "real_historical_inputs"
    shutil.copytree(FIXTURES / "real_aligned_pack", source_dir)
    return ("--historical-input-pack", str(source_dir))


def test_historical_dry_run_uses_real_local_files_and_writes_no_files(
    monkeypatch, tmp_path, capsys
) -> None:
    args = _real_historical_args(tmp_path)
    sources = tuple((tmp_path / "real_historical_inputs").iterdir())
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sources}
    monkeypatch.chdir(tmp_path)

    assert cli.main(args) == 0

    captured = capsys.readouterr()
    output = captured.out

    assert "CourtVision MLB HR historical CSV dry run" in output
    assert "mode: historical_dry_run" in output
    assert "historical_input_pack_preflight: valid" in output
    assert "statcast rows: 4" in output
    assert "retrosheet games: 1" in output
    assert "retrosheet events: 2" in output
    assert "weather rows: 1" in output
    assert "ballpark rows: 1" in output
    assert "odds snapshot rows: 2" in output
    assert "HR batter-game rows:" in output
    assert "audit passed:" in output
    assert "readiness_status:" in output
    assert "approval_status: not_approved" in output
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in sources
    }
    assert not (tmp_path / "outputs").exists()
    assert not (tmp_path / "data" / "history").exists()
    assert not (tmp_path / "runtime").exists()


def test_historical_dry_run_output_dir_writes_full_pack(tmp_path, capsys) -> None:
    output_dir = tmp_path / "historical_dry_run_pack"
    args = _real_historical_args(tmp_path)

    assert cli.main(
        (*args, "--output-dir", str(output_dir))
    ) == 0

    captured = capsys.readouterr()
    output = captured.out

    assert {path.name for path in output_dir.iterdir()} == set(cli.PACK_FILENAMES)

    manifest = json.loads(
        (output_dir / "source_manifest.json").read_text(encoding="utf-8")
    )
    audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
    readiness = json.loads(
        (output_dir / "readiness.json").read_text(encoding="utf-8")
    )
    summary = (output_dir / "build_summary.txt").read_text(encoding="utf-8")

    assert manifest["mode"] == "historical_dry_run"
    assert manifest["manifest_version"] == "phase6c-historical-rebuild-v2"
    assert manifest["source_classification"] == "real"
    assert manifest["created_at"]
    assert manifest["dataset_date_range_start"] == "2024-04-10"
    assert manifest["dataset_date_range_end"] == "2024-04-10"
    assert all(source["source_classification"] == "real" for source in manifest["sources"])
    assert all(source["provider_label"] for source in manifest["sources"])
    assert all(source["sha256"] for source in manifest["sources"])
    assert all(source["byte_size"] > 0 for source in manifest["sources"])
    assert all(source["created_at"] for source in manifest["sources"])
    assert audit["approval_status"] == "not_approved"
    assert audit["eligible_for_betting"] is False
    assert audit["kelly_eligible"] is False
    assert readiness["approval_status"] == "not_approved"
    assert "historical_dry_run" in summary
    assert "not production approved" in summary
    assert "readiness_status" in output


def test_historical_dry_run_rejects_fixtures_shortcut(capsys) -> None:
    assert cli.main(["--historical-dry-run", "--fixtures"]) == 2

    captured = capsys.readouterr()
    assert "direct --historical-dry-run CSV mode is disabled" in captured.err


def test_historical_dry_run_rejects_explicit_sample_fixture_paths(capsys) -> None:
    assert cli.main(HISTORICAL_SAMPLE_ARGS) == 2
    captured = capsys.readouterr()
    assert "direct --historical-dry-run CSV mode is disabled" in captured.err


def test_historical_dry_run_rejects_copied_sample_identities(
    tmp_path, capsys
) -> None:
    source_dir = tmp_path / "copied_inputs"
    source_dir.mkdir()
    paths: dict[str, Path] = {}
    for filename in (
        "statcast_sample.csv",
        "retrosheet_games_sample.csv",
        "retrosheet_events_sample.csv",
        "weather_sample.csv",
        "ballpark_factors_sample.csv",
        "hr_odds_snapshot_sample.csv",
    ):
        destination = source_dir / filename.replace("_sample", "")
        destination.write_bytes((cli.FIXTURE_DIR / filename).read_bytes())
        paths[filename] = destination

    args = (
        "--historical-dry-run",
        "--statcast-csv", str(paths["statcast_sample.csv"]),
        "--retrosheet-games-csv", str(paths["retrosheet_games_sample.csv"]),
        "--retrosheet-events-csv", str(paths["retrosheet_events_sample.csv"]),
        "--weather-csv", str(paths["weather_sample.csv"]),
        "--ballpark-csv", str(paths["ballpark_factors_sample.csv"]),
        "--odds-csv", str(paths["hr_odds_snapshot_sample.csv"]),
    )
    assert cli.main(args) == 2
    captured = capsys.readouterr()
    assert "direct --historical-dry-run CSV mode is disabled" in captured.err


def test_historical_rebuild_pack_cannot_be_overwritten(tmp_path, capsys) -> None:
    args = _real_historical_args(tmp_path)
    pack = tmp_path / "pack"
    assert cli.main((*args, "--output-dir", str(pack), "--overwrite")) == 2
    captured = capsys.readouterr()
    assert "real historical rebuild packs are immutable" in captured.err
    assert not pack.exists()
