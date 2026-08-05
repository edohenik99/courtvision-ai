from __future__ import annotations

from dataclasses import asdict, fields
from datetime import UTC, date, datetime
import json

import pytest

from courtvision.prospective.contracts import (
    ConfigurationProvenanceV1,
    FrozenJSONMapping,
    GitProvenanceV1,
    ModelArtifactEntryV1,
    ModelBuildManifestV1,
    ProspectiveCohortIdentityV1,
    ProspectiveCohortSpecV1,
    ProspectiveContractError,
    ProspectiveDigestMismatchError,
    ProspectiveDirtyTreeError,
    ProspectiveSecretConfigurationError,
    ProspectiveUnverifiedModelError,
    TrainingProvenanceV1,
    canonical_sha256,
    derive_prospective_cohort_identity,
)


NOW = datetime(2026, 8, 5, 16, 30, tzinfo=UTC)


def _artifact(
    *,
    logical_name: str = "player_model",
    path: str = "models/player_model.bin",
    digest: str = "a" * 64,
    size: int = 12,
) -> ModelArtifactEntryV1:
    return ModelArtifactEntryV1(logical_name, path, digest, size)


def _training(**overrides: object) -> TrainingProvenanceV1:
    values: dict[str, object] = {
        "training_start_date": date(2024, 10, 1),
        "training_end_date": date(2025, 6, 30),
        "training_completed_at_utc": NOW,
        "training_run_id": "training-run-001",
        "training_data_digest": "b" * 64,
        "model_build_tool_version": "trainer-1.0.0",
    }
    values.update(overrides)
    return TrainingProvenanceV1(**values)  # type: ignore[arg-type]


def _manifest(
    *,
    artifacts: tuple[ModelArtifactEntryV1, ...] | None = None,
    training: TrainingProvenanceV1 | None = None,
    model_version: str = "2026.08.05",
    feature_schema_version: str = "nba-features-v3",
    feature_schema_digest: str = "c" * 64,
    created_at_utc: datetime = NOW,
) -> ModelBuildManifestV1:
    return ModelBuildManifestV1.create(
        model_id="courtvision-nba-props",
        model_version=model_version,
        sport="NBA",
        league="NBA",
        artifacts=artifacts or (_artifact(),),
        training=training or _training(),
        feature_schema_version=feature_schema_version,
        feature_schema_digest=feature_schema_digest,
        created_at_utc=created_at_utc,
    )


def _spec(**overrides: object) -> ProspectiveCohortSpecV1:
    values: dict[str, object] = {
        "schema_version": 1,
        "sport": "NBA",
        "league": "NBA",
        "candidate_population": "MODEL_CANDIDATE",
        "source_lane": "elite_board",
        "model_build_manifest": _manifest(),
        "git_provenance": GitProvenanceV1("1" * 40, False, "2" * 64),
        "configuration_provenance": ConfigurationProvenanceV1.from_configuration(
            {
                "execution": {"paper_trial": True, "publish": False},
                "market_source_policy": "primary_then_secondary",
            }
        ),
        "allowed_markets": ("player_points", "player_assists"),
        "frozen_thresholds": {"elite_min_score": 0.82, "min_edge": 0.05},
        "required_sources": ("injuries", "odds", "player_stats"),
        "sportsbook_policy": {
            "allowlist": ["draftkings", "fanduel"],
            "selection": "best_available",
        },
        "minimum_publication_lead_seconds": 1800,
        "prediction_window_start": date(2026, 10, 1),
        "prediction_window_end": date(2027, 4, 15),
        "prediction_timezone": "America/Toronto",
        "feature_schema_version": "nba-features-v3",
    }
    values.update(overrides)
    return ProspectiveCohortSpecV1(**values)  # type: ignore[arg-type]


def _identity(**overrides: object) -> ProspectiveCohortIdentityV1:
    return derive_prospective_cohort_identity(_spec(**overrides))


def test_identical_frozen_inputs_generate_identical_cohort_ids() -> None:
    first = _identity()
    second = _identity()
    assert first == second
    assert first.cohort_id.startswith("prospective-nba-v1-")
    assert len(first.cohort_digest) == 64


def test_mapping_and_artifact_order_do_not_affect_identity() -> None:
    first_artifact = _artifact(logical_name="a", path="models/a.bin", digest="a" * 64)
    second_artifact = _artifact(logical_name="b", path="models/b.bin", digest="b" * 64)
    first = _identity(
        model_build_manifest=_manifest(artifacts=(first_artifact, second_artifact)),
        configuration_provenance=ConfigurationProvenanceV1.from_configuration(
            {"b": {"right": 2, "left": 1}, "a": True}
        ),
        allowed_markets=("player_points", "player_assists"),
        frozen_thresholds={"z": 2, "a": 1},
        required_sources=("stats", "odds"),
        sportsbook_policy={"z": [2, 1], "a": "allowlisted"},
    )
    second = _identity(
        model_build_manifest=_manifest(artifacts=(second_artifact, first_artifact)),
        configuration_provenance=ConfigurationProvenanceV1.from_configuration(
            {"a": True, "b": {"left": 1, "right": 2}}
        ),
        allowed_markets=("player_assists", "player_points"),
        frozen_thresholds={"a": 1, "z": 2},
        required_sources=("odds", "stats"),
        sportsbook_policy={"a": "allowlisted", "z": [2, 1]},
    )
    assert first.cohort_id == second.cohort_id
    assert first.cohort_digest == second.cohort_digest


def test_manifest_digest_is_recomputable_and_artifact_order_independent() -> None:
    a = _artifact(logical_name="a", path="models/a", digest="a" * 64)
    b = _artifact(logical_name="b", path="models/b", digest="b" * 64)
    first = _manifest(artifacts=(a, b))
    second = _manifest(artifacts=(b, a))
    assert first.manifest_digest == second.manifest_digest
    assert first.manifest_digest == canonical_sha256(first.content_without_digest())


def test_configuration_key_order_is_canonicalized_and_values_are_immutable() -> None:
    first = ConfigurationProvenanceV1.from_configuration(
        {"z": [1, {"b": 2, "a": 3}], "a": False}
    )
    second = ConfigurationProvenanceV1.from_configuration(
        {"a": False, "z": [1, {"a": 3, "b": 2}]}
    )
    assert first.configuration_digest == second.configuration_digest
    assert first.canonical_configuration["z"][1]["a"] == 3
    with pytest.raises((AttributeError, TypeError)):
        first.canonical_configuration["z"][1]["a"] = 9  # type: ignore[index]
    detached = first.canonical_configuration.to_dict()
    detached["z"][1]["a"] = 9
    assert first.canonical_configuration["z"][1]["a"] == 3


@pytest.mark.parametrize(
    "configuration",
    [
        {"api_key": "do-not-print-this"},
        {"provider": {"accessToken": "do-not-print-this"}},
        {"password": "do-not-print-this"},
        {"client_secret": "do-not-print-this"},
        {"credentials": ["do-not-print-this"]},
    ],
)
def test_secret_configuration_keys_are_rejected_without_echoing_values(
    configuration: dict[str, object],
) -> None:
    with pytest.raises(ProspectiveSecretConfigurationError) as caught:
        ConfigurationProvenanceV1.from_configuration(configuration)
    assert "do-not-print-this" not in str(caught.value)


def test_unserializable_configuration_is_rejected() -> None:
    with pytest.raises(ProspectiveContractError, match="unsupported"):
        ConfigurationProvenanceV1.from_configuration({"markets": {"points"}})


def test_incorrect_configuration_digest_is_rejected() -> None:
    with pytest.raises(ProspectiveDigestMismatchError, match="configuration_digest"):
        ConfigurationProvenanceV1(FrozenJSONMapping({"a": 1}), "f" * 64)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("training_start_date", None),
        ("training_end_date", None),
        ("training_completed_at_utc", None),
    ],
)
def test_missing_training_metadata_is_rejected(
    field_name: str, replacement: object
) -> None:
    with pytest.raises(ProspectiveUnverifiedModelError, match=field_name):
        _training(**{field_name: replacement})


def test_training_completion_must_be_aware_utc_and_is_never_inferred() -> None:
    with pytest.raises(ProspectiveUnverifiedModelError, match="timezone-aware UTC"):
        _training(training_completed_at_utc=datetime(2026, 8, 5, 16, 30))
    with pytest.raises(ProspectiveContractError, match="UTC offset"):
        _training(
            training_completed_at_utc=datetime.fromisoformat(
                "2026-08-05T12:30:00-04:00"
            )
        )


def test_training_end_may_not_precede_start() -> None:
    with pytest.raises(ProspectiveContractError, match="may not precede"):
        _training(
            training_start_date=date(2025, 1, 2),
            training_end_date=date(2025, 1, 1),
        )


def test_training_completion_may_not_precede_training_end_date() -> None:
    with pytest.raises(
        ProspectiveUnverifiedModelError,
        match=r"training_completed_at_utc\.date\(\).*training_end_date",
    ):
        _training(
            training_completed_at_utc=datetime(2025, 6, 29, 23, 59, tzinfo=UTC)
        )


def test_manifest_creation_may_not_precede_training_completion() -> None:
    with pytest.raises(
        ProspectiveUnverifiedModelError,
        match=r"created_at_utc.*training\.training_completed_at_utc",
    ):
        _manifest(
            training=_training(training_completed_at_utc=NOW),
            created_at_utc=datetime(2026, 8, 5, 16, 29, 59, tzinfo=UTC),
        )


def test_equal_training_completion_and_manifest_creation_are_allowed() -> None:
    manifest = _manifest(
        training=_training(training_completed_at_utc=NOW),
        created_at_utc=NOW,
    )

    assert manifest.created_at_utc == manifest.training.training_completed_at_utc


@pytest.mark.parametrize(
    ("feature_version", "feature_digest", "message"),
    [
        ("", "c" * 64, "feature_schema_version"),
        ("v1", "", "feature_schema_digest"),
    ],
)
def test_missing_feature_schema_evidence_is_rejected(
    feature_version: str, feature_digest: str, message: str
) -> None:
    with pytest.raises(ProspectiveContractError, match=message):
        _manifest(
            feature_schema_version=feature_version,
            feature_schema_digest=feature_digest,
        )


def test_matching_cohort_and_manifest_feature_schema_versions_are_allowed() -> None:
    manifest = _manifest(feature_schema_version="nba-features-v4")

    spec = _spec(
        model_build_manifest=manifest,
        feature_schema_version="nba-features-v4",
    )

    assert spec.feature_schema_version == manifest.feature_schema_version


def test_cohort_and_manifest_feature_schema_mismatch_is_rejected() -> None:
    with pytest.raises(
        ProspectiveUnverifiedModelError,
        match=r"feature_schema_version.*model_build_manifest\.feature_schema_version",
    ):
        _spec(feature_schema_version="nba-features-v4")


def test_model_artifact_paths_are_normalized_and_absolute_paths_rejected() -> None:
    artifact = _artifact(path="models\\nested//./model.bin")
    assert artifact.repository_relative_path == "models/nested/model.bin"
    with pytest.raises(ProspectiveContractError, match="must not be absolute"):
        _artifact(path="C:/checkout/models/model.bin")
    with pytest.raises(ProspectiveContractError, match="traverse"):
        _artifact(path="models/../secrets/model.bin")


def test_sha256_fields_require_lowercase_hexadecimal() -> None:
    with pytest.raises(ProspectiveContractError, match="lowercase"):
        _artifact(digest="A" * 64)


def test_incorrect_manifest_digest_is_rejected() -> None:
    manifest = _manifest()
    values = manifest.to_dict()
    with pytest.raises(ProspectiveDigestMismatchError, match="manifest_digest"):
        ModelBuildManifestV1(
            schema_version=manifest.schema_version,
            model_id=manifest.model_id,
            model_version=manifest.model_version,
            sport=manifest.sport,
            league=manifest.league,
            artifacts=manifest.artifacts,
            training=manifest.training,
            feature_schema_version=manifest.feature_schema_version,
            feature_schema_digest=manifest.feature_schema_digest,
            created_at_utc=manifest.created_at_utc,
            manifest_digest="0" * 64,
        )
    assert values["manifest_digest"] == manifest.manifest_digest


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("sport", "MLB", "sport must be NBA"),
        ("league", "WNBA", "league must be NBA"),
        ("source_lane", "full_market", "source_lane must be elite_board"),
        ("source_lane", "legacy_elite", "source_lane must be elite_board"),
        ("candidate_population", "OfficialPick", "MODEL_CANDIDATE"),
        ("candidate_population", "feedback", "MODEL_CANDIDATE"),
        ("candidate_population", "shadow", "MODEL_CANDIDATE"),
        ("candidate_population", "rehearsal", "MODEL_CANDIDATE"),
        ("candidate_population", "backfilled", "MODEL_CANDIDATE"),
    ],
)
def test_unsupported_population_and_lane_inputs_are_rejected(
    field_name: str, value: str, message: str
) -> None:
    with pytest.raises(ProspectiveContractError, match=message):
        _spec(**{field_name: value})


def test_dirty_git_state_blocks_identity_activation() -> None:
    with pytest.raises(ProspectiveDirtyTreeError, match="dirty Git state"):
        _identity(git_provenance=GitProvenanceV1("1" * 40, True, "2" * 64))


def test_unknown_git_state_is_not_a_valid_contract() -> None:
    with pytest.raises(ProspectiveContractError, match="unknown Git state"):
        GitProvenanceV1("1" * 40, None, "2" * 64)  # type: ignore[arg-type]


def test_unmanifested_legacy_files_cannot_be_labeled_verified() -> None:
    with pytest.raises(ProspectiveUnverifiedModelError, match="legacy artifacts"):
        _spec(model_build_manifest=None)


def test_absolute_paths_and_mutable_status_are_absent_from_identity_payload() -> None:
    identity = _identity()
    serialized = json.dumps(identity.canonical_identity_payload.to_dict())
    assert "C:\\" not in serialized
    assert "/checkout/" not in serialized
    assert "status" not in identity.canonical_identity_payload
    assert "status_changed_at_utc" not in identity.canonical_identity_payload
    assert "superseded_at_utc" not in identity.canonical_identity_payload


def test_later_status_metadata_does_not_affect_identity() -> None:
    identity = _identity()
    first_status = {
        "identity": identity,
        "status": "ACTIVE",
        "status_changed_at_utc": NOW,
    }
    later_status = {
        "identity": identity,
        "status": "SUPERSEDED",
        "status_changed_at_utc": datetime(2027, 4, 16, tzinfo=UTC),
        "superseded_at_utc": datetime(2027, 4, 16, tzinfo=UTC),
    }
    assert first_status["identity"].cohort_id == later_status["identity"].cohort_id


def test_all_required_contracts_are_frozen_and_slotted() -> None:
    contracts = (
        _artifact(),
        _training(),
        GitProvenanceV1("1" * 40, False, "2" * 64),
        ConfigurationProvenanceV1.from_configuration({"a": 1}),
        _manifest(),
        _spec(),
        _identity(),
    )
    for contract in contracts:
        assert not hasattr(contract, "__dict__")
        with pytest.raises((AttributeError, TypeError)):
            setattr(contract, fields(contract)[0].name, "changed")


def test_generic_dataclass_serialization_emits_json_objects_not_byte_strings() -> None:
    serialized = json.dumps(asdict(_identity()), sort_keys=True)
    assert '"canonical_identity_payload": {' in serialized
    assert '"configuration_provenance": {' in serialized


def test_every_material_frozen_input_changes_or_invalidates_identity() -> None:
    baseline = _identity().cohort_id
    changed_manifests = {
        "artifact": _manifest(
            artifacts=(_artifact(digest="d" * 64),)
        ),
        "model_version": _manifest(model_version="2026.08.06"),
        "training_run": _manifest(
            training=_training(training_run_id="training-run-002")
        ),
        "training_data": _manifest(
            training=_training(training_data_digest="d" * 64)
        ),
        "feature_digest": _manifest(feature_schema_digest="d" * 64),
    }
    alternatives = [
        _identity(model_build_manifest=manifest)
        for manifest in changed_manifests.values()
    ]
    alternatives.extend(
        [
            _identity(git_provenance=GitProvenanceV1("3" * 40, False, "2" * 64)),
            _identity(git_provenance=GitProvenanceV1("1" * 40, False, "3" * 64)),
            _identity(
                configuration_provenance=ConfigurationProvenanceV1.from_configuration(
                    {"execution": {"paper_trial": True}, "policy": "changed"}
                )
            ),
            _identity(
                model_build_manifest=_manifest(
                    feature_schema_version="nba-features-v4"
                ),
                feature_schema_version="nba-features-v4",
            ),
            _identity(frozen_thresholds={"elite_min_score": 0.83}),
            _identity(allowed_markets=("player_points",)),
            _identity(required_sources=("odds", "player_stats")),
            _identity(sportsbook_policy={"selection": "single_book"}),
            _identity(minimum_publication_lead_seconds=3600),
            _identity(prediction_window_start=date(2026, 10, 2)),
            _identity(prediction_window_end=date(2027, 4, 16)),
            _identity(prediction_timezone="UTC"),
        ]
    )
    assert all(identity.cohort_id != baseline for identity in alternatives)
    assert len({identity.cohort_id for identity in alternatives}) == len(alternatives)


def test_identity_digest_and_id_are_validated_against_payload() -> None:
    identity = _identity()
    with pytest.raises(ProspectiveDigestMismatchError, match="cohort_digest"):
        ProspectiveCohortIdentityV1(
            identity.cohort_id,
            "f" * 64,
            identity.canonical_identity_payload,
        )
