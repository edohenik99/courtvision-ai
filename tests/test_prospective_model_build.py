from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import ast
import math
from pathlib import Path

import pandas as pd
import pytest

from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    GitProvenanceV1,
    ProspectiveContractError,
    ProspectiveSecretConfigurationError,
    canonical_sha256,
)
from courtvision.prospective.model_build import (
    FEATURE_SCHEMA_VERSION,
    PLAYER_BASELINE_COLUMNS,
    TEAM_BASELINE_COLUMNS,
    ModelBuildBuilderError,
    ModelBuildSerializationError,
    build_feature_schema_evidence,
    build_training_input_evidence,
    create_verified_model_build,
    derive_verified_model_version,
    serialize_baseline_frame,
)


NOW = datetime(2026, 8, 5, 12, tzinfo=UTC)
START = date(2025, 1, 1)
END = date(2025, 6, 30)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "game_id": 1001,
        "game_date": "2025-01-02",
        "player_id": 7,
        "player_name": "Example Player",
        "team_abbr": "TOR",
        "min": 32,
        "pts": 24,
        "reb": 8,
        "ast": 6,
        "stl": 1,
        "blk": 0,
        "fg3m": 3,
    }
    row.update(overrides)
    return row


def _git(commit: str = "1" * 40, fingerprint: str = "2" * 64) -> GitProvenanceV1:
    return GitProvenanceV1(commit, False, fingerprint)


def _configuration(**extra: object) -> ConfigurationProvenanceV1:
    return ConfigurationProvenanceV1.from_configuration(
        {"baseline": {"minimum_minutes": 8}, **extra}
    )


def _training(rows: list[dict[str, object]] | None = None):
    return build_training_input_evidence(
        rows or [_row()],
        provider_name="synthetic-provider",
        provider_endpoint_version="stats-v1",
        requested_start_date=START,
        requested_end_date=END,
    )


def _player_frame(*, pts: float = 20.5) -> pd.DataFrame:
    values: dict[str, object] = {
        "player_id": "7",
        "player_name": "Example Player",
        "team_abbr": "TOR",
        "games": 10,
        "min_avg": 31.0,
        "min_recent": 32.0,
        "player_key": "example player__TOR",
    }
    for stat in ("pts", "reb", "ast", "stl", "blk", "fg3m"):
        values[f"{stat}_avg"] = pts if stat == "pts" else 2.0
        values[f"{stat}_recent"] = 3.0
        values[f"{stat}_std"] = 1.0
    return pd.DataFrame([values], columns=PLAYER_BASELINE_COLUMNS)


def _team_frame(*, points: float = 110.5) -> pd.DataFrame:
    values: dict[str, object] = {"team_abbr": "TOR", "games": 10}
    for column in TEAM_BASELINE_COLUMNS[2:]:
        values[column] = points if column == "team_pts_avg" else 5.0
    return pd.DataFrame([values], columns=TEAM_BASELINE_COLUMNS)


def _create(tmp_path: Path, **overrides: object):
    values: dict[str, object] = {
        "normalized_rows": [_row()],
        "provider_name": "synthetic-provider",
        "provider_endpoint_version": "stats-v1",
        "requested_start_date": START,
        "requested_end_date": END,
        "build_configuration": {"baseline": {"minimum_minutes": 8}},
        "build_git_provenance": _git(),
        "model_build_tool_version": "synthetic-builder-v1",
        "player_baseline_builder": lambda _: _player_frame(),
        "team_baseline_builder": lambda _: _team_frame(),
        "repository_root": tmp_path,
        "output_root": tmp_path / "outputs",
        "training_run_id": "run-001",
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return create_verified_model_build(**values)  # type: ignore[arg-type]


def test_training_digest_is_deterministic_and_input_order_is_not_material() -> None:
    first_row = _row(game_id=2, player_id=2, player_name="B Player")
    second_row = _row(game_id=1, player_id=1, player_name="A Player")
    first = _training([first_row, second_row])
    second = _training([second_row, first_row])
    assert first == second
    assert first["training_data_digest"] == second["training_data_digest"]
    assert [row["game_id"] for row in first["normalized_rows"]] == ["1", "2"]


def test_duplicates_remain_represented_and_material() -> None:
    single = _training([_row()])
    duplicate = _training([_row(), _row()])
    assert duplicate["row_count"] == 2
    assert len(duplicate["normalized_rows"]) == 2
    assert duplicate["training_data_digest"] != single["training_data_digest"]
    assert duplicate["player_input_digest"] != single["player_input_digest"]


@pytest.mark.parametrize(
    "change",
    [
        {"pts": 25},
        {"game_id": 1002},
        {"player_name": "Changed Player"},
        {"team_abbr": "BOS"},
    ],
)
def test_material_training_input_change_alters_digest(change: dict[str, object]) -> None:
    assert _training([_row()])["training_data_digest"] != _training([_row(**change)])[
        "training_data_digest"
    ]


@pytest.mark.parametrize("game_date", ["2024-12-31", "2025-07-01"])
def test_dates_outside_declared_interval_fail(game_date: str) -> None:
    with pytest.raises(ProspectiveContractError, match="outside"):
        _training([_row(game_date=game_date)])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_training_values_fail(value: float) -> None:
    with pytest.raises(ProspectiveContractError, match="finite"):
        _training([_row(pts=value)])


def test_unsupported_training_fields_and_values_fail() -> None:
    with pytest.raises(ProspectiveContractError, match="fields"):
        _training([{**_row(), "unexpected": "value"}])
    with pytest.raises(ProspectiveContractError, match="finite number"):
        _training([_row(pts="24")])


def test_secret_like_policy_and_build_configuration_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProspectiveSecretConfigurationError):
        build_training_input_evidence(
            [_row()],
            provider_name="provider",
            provider_endpoint_version="v1",
            requested_start_date=START,
            requested_end_date=END,
            selection_policy={"api_key": "do-not-store"},
        )
    with pytest.raises(ProspectiveSecretConfigurationError):
        _create(tmp_path, build_configuration={"access_token": "do-not-store"})


def test_absolute_paths_and_manual_context_participation_are_rejected() -> None:
    with pytest.raises(ProspectiveContractError, match="absolute filesystem path"):
        build_training_input_evidence(
            [_row()],
            provider_name="provider",
            provider_endpoint_version="v1",
            requested_start_date=START,
            requested_end_date=END,
            selection_policy={"source_path": "C:\\checkout\\rows.json"},
        )
    with pytest.raises(ProspectiveContractError, match="manual context"):
        build_training_input_evidence(
            [_row()],
            provider_name="provider",
            provider_endpoint_version="v1",
            requested_start_date=START,
            requested_end_date=END,
            manual_context_policy={"participates_in_fitting": True},
        )


def test_feature_schema_digest_is_deterministic_and_material() -> None:
    first = build_feature_schema_evidence()
    second = build_feature_schema_evidence()
    assert first == second
    assert first["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    content = first.to_dict()
    claimed = content.pop("feature_schema_digest")
    assert canonical_sha256(content) == claimed
    content["player_baseline"]["transformations"]["recency_half_life_days"] = 19.0
    assert canonical_sha256(content) != claimed


def _version(**overrides: object) -> str:
    evidence = _training()
    feature = build_feature_schema_evidence()
    values: dict[str, object] = {
        "training_start_date": START,
        "training_end_date": END,
        "training_data_digest": evidence["training_data_digest"],
        "feature_schema_version": feature["feature_schema_version"],
        "feature_schema_digest": feature["feature_schema_digest"],
        "model_build_tool_version": "builder-v1",
        "build_git_provenance": _git(),
        "build_configuration_provenance": _configuration(),
    }
    values.update(overrides)
    return derive_verified_model_version(**values)  # type: ignore[arg-type]


def test_model_version_is_deterministic_and_has_required_shape() -> None:
    assert _version() == _version()
    assert _version().startswith("nba-baselines-v1-20250101-20250630-")
    assert len(_version().rsplit("-", 1)[1]) == 20


@pytest.mark.parametrize(
    "override",
    [
        {"training_start_date": date(2025, 1, 2)},
        {"training_end_date": date(2025, 6, 29)},
        {"training_data_digest": "a" * 64},
        {"feature_schema_digest": "b" * 64},
        {"model_build_tool_version": "builder-v2"},
        {"build_git_provenance": _git(commit="3" * 40)},
        {"build_configuration_provenance": _configuration(mode="changed")},
    ],
)
def test_every_material_build_key_input_changes_version(override: dict[str, object]) -> None:
    assert _version(**override) != _version()


def test_checkout_path_and_file_mtime_do_not_enter_model_identity(tmp_path: Path) -> None:
    first = tmp_path / "first" / "source.py"
    second = tmp_path / "other-checkout" / "source.py"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("same\n", encoding="utf-8")
    second.write_text("same\n", encoding="utf-8")
    second.touch()
    assert _version() == _version()
    assert str(tmp_path) not in _version()


def test_required_columns_and_order_are_enforced() -> None:
    missing = _player_frame().drop(columns=["pts_avg"])
    with pytest.raises(ModelBuildSerializationError, match="columns"):
        serialize_baseline_frame(missing, kind="player")
    reordered = _team_frame()[list(reversed(TEAM_BASELINE_COLUMNS))]
    with pytest.raises(ModelBuildSerializationError, match="columns"):
        serialize_baseline_frame(reordered, kind="team")


def test_artifact_serialization_is_deterministic_and_row_order_independent() -> None:
    first = _player_frame()
    second = _player_frame()
    second.loc[0, "player_id"] = "8"
    second.loc[0, "player_name"] = "Second Player"
    second.loc[0, "player_key"] = "second player__TOR"
    combined = pd.concat([second, first], ignore_index=True)
    reversed_frame = combined.iloc[::-1].reset_index(drop=True)
    assert serialize_baseline_frame(combined, kind="player") == serialize_baseline_frame(
        reversed_frame, kind="player"
    )
    data = serialize_baseline_frame(combined, kind="player")
    assert b"\r" not in data
    assert data.endswith(b"\n")


def test_duplicate_identity_invalid_key_and_non_finite_output_fail() -> None:
    duplicate = pd.concat([_player_frame(), _player_frame()], ignore_index=True)
    with pytest.raises(ModelBuildSerializationError, match="duplicate"):
        serialize_baseline_frame(duplicate, kind="player")
    invalid_key = _player_frame()
    invalid_key.loc[0, "player_key"] = "wrong"
    with pytest.raises(ModelBuildSerializationError, match="player_key"):
        serialize_baseline_frame(invalid_key, kind="player")
    non_finite = _team_frame()
    non_finite.loc[0, "team_pts_avg"] = math.inf
    with pytest.raises(ModelBuildSerializationError, match="non-finite"):
        serialize_baseline_frame(non_finite, kind="team")
    string_numeric = _team_frame().astype({"team_pts_avg": "object"})
    string_numeric.loc[0, "team_pts_avg"] = "110.5"
    with pytest.raises(ModelBuildSerializationError, match="numeric"):
        serialize_baseline_frame(string_numeric, kind="team")


def test_injected_builder_failure_publishes_nothing(tmp_path: Path) -> None:
    def fail(_: pd.DataFrame) -> pd.DataFrame:
        raise RuntimeError("synthetic builder failure")

    with pytest.raises(ModelBuildBuilderError, match="player"):
        _create(tmp_path, player_baseline_builder=fail)
    assert not (tmp_path / "outputs").exists()


def test_successful_build_has_ordered_timestamps_and_correct_provenance(tmp_path: Path) -> None:
    completed = datetime(2026, 8, 5, 12, tzinfo=UTC)
    created = datetime(2026, 8, 5, 12, 0, 1, tzinfo=UTC)
    times = iter([completed, created, created])
    result = _create(tmp_path, clock=lambda: next(times))
    manifest = result.manifest
    assert manifest.training.training_completed_at_utc == completed
    assert manifest.created_at_utc == created
    assert manifest.created_at_utc >= manifest.training.training_completed_at_utc
    assert manifest.build_git_provenance == _git()
    assert manifest.build_configuration_provenance == _configuration()
    assert manifest.training.training_data_digest == _training()["training_data_digest"]


def test_no_calibration_or_legacy_fallback_and_legacy_files_are_untouched(tmp_path: Path) -> None:
    legacy = tmp_path / "outputs" / "model"
    legacy.mkdir(parents=True)
    sentinels = {
        "player_baselines.csv": b"legacy-player\n",
        "team_baselines.csv": b"legacy-team\n",
        "calibration.json": b'{"legacy":true}\n',
    }
    for name, content in sentinels.items():
        (legacy / name).write_bytes(content)
    result = _create(tmp_path)
    assert set(path.name for path in result.path.iterdir()) == {
        "player_baselines.csv",
        "team_baselines.csv",
        "training_inputs_v1.json",
        "feature_schema_v1.json",
        "model_build_manifest_v1.json",
    }
    assert "calibration" not in {item.logical_name for item in result.manifest.artifacts}
    assert all((legacy / name).read_bytes() == content for name, content in sentinels.items())


def test_force_overwrite_is_rejected_before_any_write(tmp_path: Path) -> None:
    with pytest.raises(ProspectiveContractError, match="force overwrite"):
        _create(tmp_path, force_overwrite=True)
    assert not (tmp_path / "outputs").exists()


def test_core_has_no_operational_imports_or_calls() -> None:
    source_path = Path(__file__).parents[1] / "courtvision" / "prospective" / "model_build.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = (
        "courtvision_ai",
        "courtvision.prediction",
        "courtvision.pipeline",
        "courtvision.lifecycle",
        "courtvision.official_picks",
        "courtvision.grading",
        "courtvision.evaluation",
        "courtvision.betting",
    )
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in imported
        for prefix in forbidden
    )


def test_training_run_id_and_timestamps_are_not_in_version(tmp_path: Path) -> None:
    first = _create(tmp_path, training_run_id="attempt-one")
    manifest_bytes = (first.path / "model_build_manifest_v1.json").read_bytes()
    assert first.path.name == _version(model_build_tool_version="synthetic-builder-v1")
    assert "attempt-one" not in first.path.name
    assert hashlib.sha256(manifest_bytes).hexdigest()
