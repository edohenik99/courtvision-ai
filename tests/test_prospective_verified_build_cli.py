from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import courtvision_ai
from courtvision.prospective.contracts import (
    GitProvenanceV1,
    ProspectiveDirtyTreeError,
)
from courtvision.prospective.model_manifest_io import (
    REQUIRED_BUILD_FILENAMES,
    load_model_build_manifest,
)
from courtvision.prospective.nba_verified_training import (
    build_nba_verified_configuration,
    build_nba_verified_tool_version,
)


START = "2025-01-01"
END = "2025-01-31"
SECRET = "synthetic-provider-secret-value"


def _canonical_args(*extra: str) -> list[str]:
    return [
        "--sport",
        "nba",
        "--mode",
        "production",
        "--fit-only",
        "--verified-model-build",
        "--train-start",
        START,
        "--train-end",
        END,
        "--out-dir",
        "outputs",
        *extra,
    ]


def _raw_stats() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": 1001,
                "game_date": "2025-01-02T00:00:00Z",
                "player_id": 1,
                "player_name": "Toronto One",
                "team_abbr": "TOR",
                "min": "32:00",
                "pts": 24,
                "reb": 8,
                "ast": 6,
                "stl": 1,
                "blk": 0,
                "fg3m": 3,
            },
            {
                "game_id": 1001,
                "game_date": "2025-01-02T00:00:00Z",
                "player_id": 2,
                "player_name": "Toronto Two",
                "team_abbr": "TOR",
                "min": "28:00",
                "pts": 16,
                "reb": 5,
                "ast": 4,
                "stl": 0,
                "blk": 1,
                "fg3m": 2,
            },
            {
                "game_id": 1001,
                "game_date": "2025-01-02T00:00:00Z",
                "player_id": 3,
                "player_name": "Boston One",
                "team_abbr": "BOS",
                "min": "34:00",
                "pts": 27,
                "reb": 7,
                "ast": 5,
                "stl": 2,
                "blk": 0,
                "fg3m": 4,
            },
            {
                "game_id": 1001,
                "game_date": "2025-01-02T00:00:00Z",
                "player_id": 4,
                "player_name": "Boston Two",
                "team_abbr": "BOS",
                "min": "26:00",
                "pts": 14,
                "reb": 6,
                "ast": 3,
                "stl": 1,
                "blk": 1,
                "fg3m": 1,
            },
        ]
    )


def _git() -> GitProvenanceV1:
    return GitProvenanceV1("1" * 40, False, "2" * 64)


def _patch_verified_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    repository_root: Path,
    *,
    raw_stats: pd.DataFrame | None = None,
) -> None:
    repository_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        courtvision_ai,
        "_verified_repository_root",
        lambda: repository_root,
    )
    monkeypatch.setattr(
        courtvision_ai,
        "capture_git_provenance",
        lambda *_args, **_kwargs: _git(),
    )
    rows = _raw_stats() if raw_stats is None else raw_stats
    monkeypatch.setattr(
        courtvision_ai,
        "_fetch_verified_nba_training_stats",
        lambda **_kwargs: rows.copy(deep=True),
    )
    monkeypatch.setattr(
        courtvision_ai,
        "build_nba_verified_tool_version",
        lambda: "synthetic-verified-cli-v1",
    )
    monkeypatch.setattr(courtvision_ai, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        courtvision_ai,
        "NBA_V1",
        "https://api.balldontlie.io/v1",
    )
    monkeypatch.setenv("BALLDONTLIE_REQUEST_TIMEOUT", "30")
    monkeypatch.setenv("COURTVISION_HTTP_RETRIES", "3")
    monkeypatch.setenv("COURTVISION_HTTP_BACKOFF", "1.5")


def _build_directories(repository_root: Path) -> list[Path]:
    store = repository_root / "outputs" / "model" / "verified_builds"
    if not store.exists():
        return []
    return sorted(
        path for path in store.iterdir() if not path.name.startswith(".")
    )


def _hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_parser_accepts_canonical_verified_build_command() -> None:
    args = courtvision_ai._build_arg_parser().parse_args(_canonical_args())
    assert args.sport == "nba"
    assert args.mode == "production"
    assert args.fit_only is True
    assert args.verified_model_build is True
    assert args.train_start == START
    assert args.train_end == END
    assert args.out_dir == "outputs"


def test_verified_model_build_is_not_implicitly_enabled() -> None:
    args = courtvision_ai._build_arg_parser().parse_args(
        ["--fit-only", "--train-start", START, "--train-end", END]
    )
    assert args.verified_model_build is False


def test_verified_flag_requires_fit_only() -> None:
    parser = courtvision_ai._build_arg_parser()
    args = parser.parse_args(
        [
            "--verified-model-build",
            "--train-start",
            START,
            "--train-end",
            END,
        ]
    )
    with pytest.raises(SystemExit):
        courtvision_ai._validate_verified_model_build_args(args, parser)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--train-end", END],
        ["--train-start", START],
        ["--train-start", "2025-1-01", "--train-end", END],
        ["--train-start", "not-a-date", "--train-end", END],
        ["--train-start", END, "--train-end", START],
    ],
)
def test_dates_are_required_and_validated_before_provider_access(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    called = False

    def forbidden(*_args: object, **_kwargs: object) -> int:
        nonlocal called
        called = True
        raise AssertionError("provider path must not run")

    monkeypatch.setattr(courtvision_ai, "_run_verified_model_build_cli", forbidden)
    with pytest.raises(SystemExit):
        courtvision_ai.main(
            [
                "--sport",
                "nba",
                "--mode",
                "production",
                "--fit-only",
                "--verified-model-build",
                *arguments,
            ]
        )
    assert called is False


@pytest.mark.parametrize(
    "extra",
    [
        ("--prediction-date", "2025-02-01"),
        ("predict",),
        ("--predict-only",),
        ("--send-telegram",),
        ("--grade-date", "2025-02-01"),
    ],
)
def test_prediction_and_grade_modes_are_rejected(extra: tuple[str, ...]) -> None:
    parser = courtvision_ai._build_arg_parser()
    args = parser.parse_args(_canonical_args(*extra))
    with pytest.raises(SystemExit):
        courtvision_ai._validate_verified_model_build_args(args, parser)


def test_force_output_overwrite_is_rejected() -> None:
    parser = courtvision_ai._build_arg_parser()
    args = parser.parse_args(_canonical_args("--force-output-overwrite"))
    with pytest.raises(SystemExit):
        courtvision_ai._validate_verified_model_build_args(args, parser)


def test_non_nba_verified_build_is_rejected() -> None:
    parser = courtvision_ai._build_arg_parser()
    arguments = _canonical_args()
    arguments[1] = "mlb"
    args = parser.parse_args(arguments)
    with pytest.raises(SystemExit):
        courtvision_ai._validate_verified_model_build_args(args, parser)


def test_legacy_fit_behavior_is_unchanged_without_verified_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class LegacyRuntime:
        def __init__(self, out_dir: str) -> None:
            assert out_dir == str(tmp_path / "legacy-outputs")

        def fit(self, train_start: str, train_end: str) -> dict[str, Any]:
            calls.append((train_start, train_end))
            return {"legacy": True}

    monkeypatch.setattr(courtvision_ai, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        courtvision_ai,
        "resolve_api_key",
        lambda **_kwargs: ("synthetic", {"masked_preview": "***"}),
    )
    monkeypatch.setattr(
        courtvision_ai,
        "smoke_test_games_api",
        lambda *_args, **_kwargs: {
            "status_code": 200,
            "resolved_url": "synthetic",
            "has_auth": True,
            "masked_key_preview": "***",
            "body_snippet": "ok",
        },
    )
    monkeypatch.setattr(courtvision_ai, "CourtVisionAI", LegacyRuntime)
    monkeypatch.setattr(
        courtvision_ai,
        "_run_verified_model_build_cli",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified path must remain opt-in")
        ),
    )
    assert (
        courtvision_ai.main(
            [
                "--fit-only",
                "--train-start",
                START,
                "--train-end",
                END,
                "--out-dir",
                str(tmp_path / "legacy-outputs"),
            ]
        )
        == 0
    )
    assert calls == [(START, END)]


def test_verified_mode_never_constructs_or_fits_courtvision_ai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(
        courtvision_ai.CourtVisionAI,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CourtVisionAI constructor is forbidden")
        ),
    )
    monkeypatch.setattr(
        courtvision_ai.CourtVisionAI,
        "fit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CourtVisionAI.fit is forbidden")
        ),
    )
    assert courtvision_ai.main(_canonical_args()) == 0


def test_successful_synthetic_provider_build_publishes_exactly_five_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    output = json.loads(capsys.readouterr().out)
    builds = _build_directories(tmp_path)
    assert len(builds) == 1
    assert {path.name for path in builds[0].iterdir()} == REQUIRED_BUILD_FILENAMES
    assert output["success"] is True
    assert output["verified_build_path"] == str(builds[0])


def test_legacy_baseline_calibration_and_history_hashes_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinels = {
        tmp_path / "outputs" / "model" / "player_baselines.csv": b"legacy-player\n",
        tmp_path / "outputs" / "model" / "team_baselines.csv": b"legacy-team\n",
        tmp_path / "outputs" / "model" / "calibration.json": b'{"legacy":true}\n',
        tmp_path / "outputs" / "runtime" / "history" / "run_log.csv": b"runtime\n",
        tmp_path / "outputs" / "runtime" / "history" / "result_feedback.csv": b"feedback\n",
        tmp_path / "data" / "history" / "prediction_history.csv": b"prediction\n",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sentinels}
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sentinels}
    assert after == before
    build = _build_directories(tmp_path)[0]
    manifest = load_model_build_manifest(build / "model_build_manifest_v1.json")
    assert {artifact.logical_name for artifact in manifest.artifacts} == {
        "player_baseline",
        "team_baseline",
    }
    assert "calibration" not in "".join(path.name for path in build.iterdir()).lower()


def test_verified_mode_does_not_call_prediction_grading_or_lifecycle_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("operational path is forbidden")

    monkeypatch.setattr(courtvision_ai, "run_nba_prediction_application", forbidden)
    monkeypatch.setattr(courtvision_ai, "_write_grading_outputs", forbidden)
    monkeypatch.setattr(courtvision_ai, "load_shadow_lifecycle_hooks", forbidden)
    monkeypatch.setattr(courtvision_ai.CourtVisionAI, "predict", forbidden)
    monkeypatch.setattr(courtvision_ai.CourtVisionAI, "auto_grade", forbidden)
    assert courtvision_ai.main(_canonical_args()) == 0


def test_dirty_git_state_fails_before_provider_access_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(courtvision_ai, "_verified_repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        courtvision_ai,
        "capture_git_provenance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProspectiveDirtyTreeError("synthetic dirty state")
        ),
    )
    called = False

    def fetch(**_kwargs: object) -> pd.DataFrame:
        nonlocal called
        called = True
        return _raw_stats()

    monkeypatch.setattr(courtvision_ai, "_fetch_verified_nba_training_stats", fetch)
    assert courtvision_ai.main(_canonical_args()) == 1
    assert called is False
    assert _build_directories(tmp_path) == []


def test_provider_failure_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(
        courtvision_ai,
        "_fetch_verified_nba_training_stats",
        lambda **_kwargs: (_ for _ in ()).throw(
            courtvision_ai.VerifiedNBAProviderError("synthetic provider failure")
        ),
    )
    assert courtvision_ai.main(_canonical_args()) == 1
    assert _build_directories(tmp_path) == []


def test_empty_training_data_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path, raw_stats=pd.DataFrame())
    assert courtvision_ai.main(_canonical_args()) == 1
    assert _build_directories(tmp_path) == []


def test_builder_failure_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)

    def fail_builder(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise RuntimeError("synthetic builder failure")

    monkeypatch.setattr(
        courtvision_ai.CourtVisionAI,
        "_build_player_baselines",
        fail_builder,
    )
    assert courtvision_ai.main(_canonical_args()) == 1
    assert _build_directories(tmp_path) == []


def test_identical_replay_preserves_bytes_and_mtimes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    first_output = json.loads(capsys.readouterr().out)
    build = Path(first_output["verified_build_path"])
    hashes = _hashes(build)
    mtimes = {path.name: path.stat().st_mtime_ns for path in build.iterdir()}

    assert courtvision_ai.main(_canonical_args()) == 0
    second_output = json.loads(capsys.readouterr().out)
    assert second_output["replayed_existing_build"] is True
    assert _hashes(build) == hashes
    assert {path.name: path.stat().st_mtime_ns for path in build.iterdir()} == mtimes


def test_conflicting_existing_destination_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    build = _build_directories(tmp_path)[0]
    before = _hashes(build)
    original_builder = courtvision_ai.CourtVisionAI._build_player_baselines

    def changed_builder(runtime: object, stats: pd.DataFrame) -> pd.DataFrame:
        frame = original_builder(runtime, stats)
        frame.loc[0, "pts_avg"] = float(frame.loc[0, "pts_avg"]) + 1.0
        return frame

    monkeypatch.setattr(
        courtvision_ai.CourtVisionAI,
        "_build_player_baselines",
        changed_builder,
    )
    assert courtvision_ai.main(_canonical_args()) == 1
    assert _hashes(build) == before


def test_corrupt_existing_destination_fails_closed_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    build = _build_directories(tmp_path)[0]
    manifest_path = build / "model_build_manifest_v1.json"
    manifest_path.write_bytes(b"not-json")
    assert courtvision_ai.main(_canonical_args()) == 1
    assert manifest_path.read_bytes() == b"not-json"
    assert len(_build_directories(tmp_path)) == 1


def test_credentials_are_absent_from_failure_output_and_exception_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingClient:
        def __init__(self, api_key: str, timeout: int, *, safe_errors: bool) -> None:
            assert api_key == SECRET
            assert timeout == 30
            assert safe_errors is True

        def get_stats(self, _start: str, _end: str) -> pd.DataFrame:
            raise RuntimeError(f"provider payload included {SECRET}")

    monkeypatch.setattr(
        courtvision_ai,
        "resolve_api_key",
        lambda **_kwargs: (SECRET, {"masked_preview": SECRET[-4:]}),
    )
    monkeypatch.setattr(courtvision_ai, "BallDontLieClient", FailingClient)
    with pytest.raises(courtvision_ai.VerifiedNBAProviderError) as error:
        courtvision_ai._fetch_verified_nba_training_stats(
            train_start=date.fromisoformat(START),
            train_end=date.fromisoformat(END),
            request_timeout_seconds=30,
        )
    assert SECRET not in str(error.value)

    _patch_verified_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(
        courtvision_ai,
        "_fetch_verified_nba_training_stats",
        lambda **_kwargs: (_ for _ in ()).throw(error.value),
    )
    assert courtvision_ai.main(_canonical_args()) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert _build_directories(tmp_path) == []


def test_credentials_are_absent_from_success_output_and_build_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_fetch = courtvision_ai._fetch_verified_nba_training_stats

    class SuccessfulClient:
        def __init__(self, api_key: str, timeout: int, *, safe_errors: bool) -> None:
            assert api_key == SECRET
            assert timeout == 30
            assert safe_errors is True

        def get_stats(self, start: str, end: str) -> pd.DataFrame:
            assert (start, end) == (START, END)
            return _raw_stats()

    _patch_verified_dependencies(monkeypatch, tmp_path)
    monkeypatch.setattr(
        courtvision_ai,
        "_fetch_verified_nba_training_stats",
        original_fetch,
    )
    monkeypatch.setattr(
        courtvision_ai,
        "resolve_api_key",
        lambda **_kwargs: (SECRET, {"masked_preview": SECRET[-4:]}),
    )
    monkeypatch.setattr(courtvision_ai, "BallDontLieClient", SuccessfulClient)
    assert courtvision_ai.main(_canonical_args()) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    build = _build_directories(tmp_path)[0]
    assert all(SECRET.encode("utf-8") not in path.read_bytes() for path in build.iterdir())


def test_provider_endpoint_with_embedded_secret_is_rejected_before_hashing() -> None:
    with pytest.raises(ValueError, match="endpoint configuration") as error:
        build_nba_verified_configuration(
            requested_start_date=date.fromisoformat(START),
            requested_end_date=date.fromisoformat(END),
            provider_base_url=f"https://user:{SECRET}@api.example.invalid/v1",
            request_timeout_seconds=30,
            retry_total=3,
            retry_backoff_seconds=1.5,
        )
    assert SECRET not in str(error.value)


def test_tool_version_contains_only_legitimate_toolchain_identity() -> None:
    payload = json.loads(build_nba_verified_tool_version())
    assert set(payload) == {
        "verified_builder_schema",
        "courtvision",
        "python",
        "pandas",
    }
    assert all(isinstance(value, str) and value for value in payload.values())
    assert "git" not in build_nba_verified_tool_version().lower()


def test_success_output_matches_exact_verified_manifest_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _patch_verified_dependencies(monkeypatch, tmp_path)
    assert courtvision_ai.main(_canonical_args()) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    output = json.loads(captured.out)
    assert set(output) == {
        "success",
        "model_id",
        "model_version",
        "manifest_digest",
        "verified_build_path",
        "replayed_existing_build",
    }
    manifest = load_model_build_manifest(
        Path(output["verified_build_path"]) / "model_build_manifest_v1.json"
    )
    configuration = (
        manifest.build_configuration_provenance.canonical_configuration.to_dict()
    )
    assert manifest.build_git_provenance == _git()
    assert manifest.training.model_build_tool_version == "synthetic-verified-cli-v1"
    assert "1" * 40 not in manifest.training.model_build_tool_version
    assert configuration["training_interval"] == {
        "start_date": START,
        "end_date": END,
        "inclusive": True,
    }
    assert configuration["provider"] == {
        "name": "balldontlie",
        "base_url": "https://api.balldontlie.io/v1",
        "stats_endpoint": "/stats",
        "endpoint_version": "nba-v1-stats",
        "page_size": 100,
        "request_timeout_seconds": 30,
        "retry_total": 3,
        "retry_backoff_seconds": 1.5,
        "retry_status_codes": [429, 500, 502, 503, 504],
        "retry_allowed_methods": ["GET", "POST"],
    }
    assert configuration["calibration_policy"] == "identity_no_calibration"
    assert output == {
        "success": True,
        "model_id": manifest.model_id,
        "model_version": manifest.model_version,
        "manifest_digest": manifest.manifest_digest,
        "verified_build_path": str(Path(output["verified_build_path"])),
        "replayed_existing_build": False,
    }
