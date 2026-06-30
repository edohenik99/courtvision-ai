from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from courtvision.sports.mlb.data.crosswalk_validation import (
    REQUIRED_CROSSWALK_COLUMNS,
    validate_mlb_hr_crosswalk_csv,
)
import scripts.mlb_dry_run_hr_crosswalk as crosswalk_cli


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mlb" / "crosswalk"


def _fixture(name: str) -> Path:
    return FIXTURE_DIR / name


def test_valid_crosswalk_passes_with_realistic_identity_counts() -> None:
    result = validate_mlb_hr_crosswalk_csv(
        _fixture("valid_batter_game_crosswalk.csv")
    )

    assert result.is_valid
    assert result.errors == ()
    assert result.row_count == 2
    assert result.valid_row_count == 2
    assert result.invalid_row_count == 0
    assert result.duplicate_mapping_count == 0
    assert result.conflicting_mapping_count == 0
    assert result.missing_required_id_count == 0
    assert result.sample_identity_count == 0
    assert result.mlbam_batter_count == 2
    assert result.retrosheet_batter_count == 2
    assert result.mlbam_game_count == 1
    assert result.retrosheet_game_count == 1
    assert "mlbam_batter_id" in REQUIRED_CROSSWALK_COLUMNS


def test_duplicate_mlbam_mapping_fails() -> None:
    result = validate_mlb_hr_crosswalk_csv(
        _fixture("duplicate_mlbam_mapping.csv")
    )

    assert not result.is_valid
    assert result.duplicate_mapping_count >= 1
    assert any(
        "duplicate MLBAM batter-game mapping" in error for error in result.errors
    )


def test_missing_player_id_fails() -> None:
    result = validate_mlb_hr_crosswalk_csv(_fixture("missing_player_id.csv"))

    assert not result.is_valid
    assert result.missing_required_id_count == 1
    assert any("mlbam_batter_id is required" in error for error in result.errors)


def test_mismatched_team_and_date_fail() -> None:
    result = validate_mlb_hr_crosswalk_csv(_fixture("mismatched_team_date.csv"))

    assert not result.is_valid
    assert any("team mapping mismatch for BOS" in error for error in result.errors)
    assert any(
        "retrosheet_game_id date 2024-04-11 does not match game_date 2024-04-10"
        in error
        for error in result.errors
    )


def test_sample_name_fails() -> None:
    result = validate_mlb_hr_crosswalk_csv(_fixture("sample_name.csv"))

    assert not result.is_valid
    assert result.sample_identity_count == 1
    assert any(
        "batter_name uses a sample/fixture/synthetic identity" in error
        for error in result.errors
    )


def test_conflicting_player_mapping_fails(tmp_path: Path) -> None:
    crosswalk = tmp_path / "conflicting-crosswalk.csv"
    content = _fixture("valid_batter_game_crosswalk.csv").read_text(encoding="utf-8")
    crosswalk.write_text(content.replace("sotoj001", "judga001"), encoding="utf-8")

    result = validate_mlb_hr_crosswalk_csv(crosswalk)

    assert not result.is_valid
    assert result.conflicting_mapping_count >= 1
    assert any(
        "conflicting Retrosheet-to-MLBAM player mapping" in error
        for error in result.errors
    )


def test_dry_run_reports_pass_and_does_not_mutate_operational_folders(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    crosswalk = staging / "proposed-crosswalk.csv"
    shutil.copy2(_fixture("valid_batter_game_crosswalk.csv"), crosswalk)

    operational_files: list[Path] = []
    for relative in (
        "manual-data/sentinel.txt",
        "data/manual/sentinel.txt",
        "outputs/sentinel.txt",
        "history/sentinel.txt",
        "data/history/sentinel.txt",
        "runtime/sentinel.txt",
        "cache/sentinel.txt",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
        operational_files.append(path)

    before_files = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [crosswalk, *operational_files]
    }
    before_names = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    monkeypatch.chdir(tmp_path)

    exit_code = crosswalk_cli.main([str(crosswalk)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "status: PASS" in output
    assert "rows_total: 2" in output
    assert "duplicate_mappings: 0" in output
    assert "missing_required_ids: 0" in output
    assert "sample_identities: 0" in output
    assert before_files == {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in [crosswalk, *operational_files]
    }
    assert before_names == {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}


def test_dry_run_failure_report_is_clear(capsys) -> None:
    exit_code = crosswalk_cli.main([str(_fixture("duplicate_mlbam_mapping.csv"))])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "status: FAIL" in output
    assert "rows_total: 2" in output
    assert "duplicate_mappings:" in output
    assert "error: row 3: duplicate MLBAM batter-game mapping" in output


def test_malformed_csv_row_is_included_in_total_count(tmp_path: Path) -> None:
    crosswalk = tmp_path / "malformed-crosswalk.csv"
    content = _fixture("valid_batter_game_crosswalk.csv").read_text(encoding="utf-8")
    header, first_row, *_ = content.splitlines()
    crosswalk.write_text(f"{header}\n{first_row},extra-value\n", encoding="utf-8")

    result = validate_mlb_hr_crosswalk_csv(crosswalk)

    assert not result.is_valid
    assert result.row_count == 1
    assert result.valid_row_count == 0
    assert result.invalid_row_count == 1
    assert any("crosswalk CSV has extra values" in error for error in result.errors)


def test_direct_cli_disables_project_bytecode_writes(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    crosswalk = staging / "proposed-crosswalk.csv"
    shutil.copy2(_fixture("valid_batter_game_crosswalk.csv"), crosswalk)
    script = Path(crosswalk_cli.__file__).resolve()
    bytecode_cache = tmp_path / "bytecode-cache"
    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["PYTHONPYCACHEPREFIX"] = str(bytecode_cache)

    completed = subprocess.run(
        [sys.executable, "-S", str(script), str(crosswalk)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0
    assert "status: PASS" in completed.stdout
    assert not tuple(bytecode_cache.rglob("crosswalk_validation*.pyc"))
