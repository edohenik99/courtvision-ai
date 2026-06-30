from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HISTORICAL_FEATURE_PACK_VERSION,
)
from courtvision.sports.mlb.data.historical_temporal_backtest import (
    TemporalDateWindow,
    TemporalSplitPlan,
)
import courtvision.sports.mlb.training.hr_preprocessing_plan as preprocessing
import courtvision.sports.mlb.training.hr_preprocessing_artifact as fitted_artifacts
from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    FITTED_PREPROCESSING_ARTIFACT_FILENAME,
    FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION,
    MLBHRFittedPreprocessingArtifactError,
    load_fitted_preprocessing_artifact,
    write_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    MLBHRPreprocessingPlanningError,
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
    plan_mlb_hr_preprocessing,
)
import scripts.mlb_dry_run_hr_preprocessing as preprocessing_cli
import scripts.mlb_write_hr_fitted_preprocessing as fitted_preprocessing_cli


FEATURE_NAMES = (
    "weather_temperature",
    "hitter_recent_hr_rate",
    "batter_hand",
    "sportsbook",
    "hr_market_available",
    "odds_collected_at",
    "odds_as_of",
)
FIRST_DATE = date(2024, 4, 1)
GAME_DATES = tuple(FIRST_DATE + timedelta(days=index) for index in range(30))


def _feature_value(feature_name: str, index: int) -> object:
    game_date = GAME_DATES[index]
    cutoff = f"{game_date.isoformat()}T20:00:00+00:00"
    values: dict[str, object] = {
        "weather_temperature": 65.0 + index,
        "hitter_recent_hr_rate": round(0.04 + index / 1000, 6),
        "batter_hand": "R" if index < 18 else "S" if index < 24 else "L",
        "sportsbook": "Historical Book A",
        "hr_market_available": True,
        "odds_collected_at": cutoff,
        "odds_as_of": cutoff,
    }
    return values.get(feature_name, 0)


def _write_feature_pack(
    path: Path,
    *,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    transform: Callable[[int, dict[str, object]], None] | None = None,
) -> Path:
    rows: list[dict[str, object]] = []
    for index, game_date in enumerate(GAME_DATES):
        date_text = game_date.isoformat()
        cutoff = f"{date_text}T20:00:00+00:00"
        start = f"{date_text}T23:00:00+00:00"
        values = {
            name: _feature_value(name, index) for name in feature_names
        }
        if transform is not None:
            transform(index, values)
        availability = []
        for name in feature_names:
            item: dict[str, object] = {
                "feature_name": name,
                "available_at": f"{date_text}T18:00:00+00:00",
                "source_latest_game_date": None,
            }
            if name == "hitter_recent_hr_rate":
                item["source_latest_game_date"] = (
                    game_date - timedelta(days=1)
                ).isoformat()
            availability.append(item)
        rows.append(
            {
                "row_id": f"row-{index:02d}",
                "game_id": f"game-{index:02d}",
                "game_date": date_text,
                "player_id": f"player-{index:02d}",
                "player_name": f"Batter {index:02d}",
                "odds_collected_at": cutoff,
                "event_start_time": start,
                "feature_values": values,
                "feature_availability": availability,
            }
        )
    payload = {
        "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
        "mode": "historical_research",
        "readiness_verdict": (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ),
        "feature_names": list(feature_names),
        "rows": rows,
        "feature_firewall_valid": True,
        "approval_status": "not_approved",
        "model_training_enabled": False,
        "backtesting_enabled": False,
        "predictions_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _split_plan(pack_dir: Path) -> TemporalSplitPlan:
    return TemporalSplitPlan(
        pack_dir=pack_dir,
        train=TemporalDateWindow("train", GAME_DATES[:18]),
        validation=TemporalDateWindow("validation", GAME_DATES[18:24]),
        test=TemporalDateWindow("test", GAME_DATES[24:]),
    )


def _write_temporal_plan(
    path: Path,
    *,
    feature_pack: Path,
    pack_dir: Path,
) -> Path:
    payload = {
        "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
        "mode": "historical_research",
        "feature_pack_sha256": _file_sha256(feature_pack),
        "pack_dir": str(pack_dir),
        "split_method": "whole_unique_game_dates_60_20_20",
        "readiness_verdict": (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ),
        "approval_status": "not_approved",
        "model_training_enabled": False,
        "backtesting_enabled": False,
        "predictions_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
        "train": {"game_dates": [value.isoformat() for value in GAME_DATES[:18]]},
        "validation": {
            "game_dates": [value.isoformat() for value in GAME_DATES[18:24]]
        },
        "test": {"game_dates": [value.isoformat() for value in GAME_DATES[24:]]},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _inputs(
    tmp_path: Path,
    *,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    transform: Callable[[int, dict[str, object]], None] | None = None,
) -> tuple[Path, Path, Path]:
    staged_pack = tmp_path / "staged_pack"
    staged_pack.mkdir()
    feature_pack = _write_feature_pack(
        tmp_path / "feature_pack.json",
        feature_names=feature_names,
        transform=transform,
    )
    temporal_plan = _write_temporal_plan(
        tmp_path / "temporal_plan.json",
        feature_pack=feature_pack,
        pack_dir=staged_pack,
    )
    return feature_pack, temporal_plan, staged_pack


def _snapshot_tree(root: Path) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        sorted(
            (
                str(path.relative_to(root)),
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_valid_feature_pack_produces_preprocessing_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)

    plan = plan_mlb_hr_preprocessing(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
    )

    assert plan.feature_firewall_valid
    assert plan.temporal_split_valid
    assert plan.numeric_columns == (
        "weather_temperature",
        "hitter_recent_hr_rate",
        "hr_market_available",
    )
    assert plan.categorical_columns == ("batter_hand", "sportsbook")
    assert plan.market_columns == (
        "sportsbook",
        "hr_market_available",
        "odds_collected_at",
        "odds_as_of",
    )
    assert "feature_availability" in plan.lineage_columns
    assert "odds_collected_at" in plan.lineage_columns
    assert (plan.train_row_count, plan.validation_row_count, plan.test_row_count) == (
        18,
        6,
        6,
    )
    assert not plan.model_training_enabled
    assert not plan.backtesting_enabled
    assert not plan.artifacts_written

    assert preprocessing_cli.main(
        [
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "feature_firewall: valid" in output
    assert "temporal_split: valid" in output
    assert "fit_split: train" in output
    assert "artifacts_written: false" in output


def test_label_column_is_rejected(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(
        tmp_path,
        feature_names=(*FEATURE_NAMES, "hit_hr_today"),
    )

    with pytest.raises(
        MLBHRPreprocessingPlanningError,
        match="label/outcome field cannot be used as a feature: hit_hr_today",
    ):
        plan_mlb_hr_preprocessing(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_unknown_feature_is_rejected(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(
        tmp_path,
        feature_names=(*FEATURE_NAMES, "mystery_signal"),
    )

    with pytest.raises(
        MLBHRPreprocessingPlanningError,
        match="unknown feature column is not explicitly allowlisted: mystery_signal",
    ):
        plan_mlb_hr_preprocessing(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_missing_train_values_are_summarized(tmp_path: Path) -> None:
    def add_missing(index: int, values: dict[str, object]) -> None:
        if index in {0, 3, 7}:
            values["weather_temperature"] = None
        if index in {1, 2}:
            values["batter_hand"] = None

    feature_pack, temporal_plan, _ = _inputs(tmp_path, transform=add_missing)

    plan = plan_mlb_hr_preprocessing(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
    )

    numeric = next(
        item
        for item in plan.numeric_summaries
        if item.column == "weather_temperature"
    )
    assert numeric.train_missing_count == 3
    assert numeric.train_nonmissing_count == 15
    assert numeric.train_missing_rate == pytest.approx(3 / 18)
    assert numeric.train_median == 75.0
    assert numeric.missing_indicator

    categorical = next(
        item
        for item in plan.categorical_summaries
        if item.column == "batter_hand"
    )
    assert categorical.train_missing_count == 2
    assert categorical.train_nonmissing_count == 16
    assert categorical.missing_token == "__MISSING__"


def test_validation_and_test_only_categories_are_reported_not_fitted(
    tmp_path: Path,
) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)

    plan = plan_mlb_hr_preprocessing(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
    )

    summary = next(
        item
        for item in plan.categorical_summaries
        if item.column == "batter_hand"
    )
    assert summary.retained_train_categories == ("R",)
    assert summary.rare_train_categories == ()
    assert summary.validation_only_categories == ("S",)
    assert summary.test_only_categories == ("L",)
    fitted_categories = set(summary.retained_train_categories) | set(
        summary.rare_train_categories
    )
    assert "S" not in fitted_categories
    assert "L" not in fitted_categories
    assert summary.unknown_token == "__UNKNOWN__"


def test_validation_or_test_fitting_is_rejected(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)

    for split_name in ("validation", "test"):
        with pytest.raises(
            MLBHRPreprocessingPlanningError,
            match="train split only",
        ):
            plan_mlb_hr_preprocessing(
                feature_pack_path=feature_pack,
                temporal_split_plan_path=temporal_plan,
                fit_split=split_name,
            )


def test_staged_pack_is_an_accepted_temporal_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_pack, _, staged_pack = _inputs(tmp_path)
    split_plan = _split_plan(staged_pack)

    monkeypatch.setattr(
        preprocessing,
        "dry_run_historical_research_backtest",
        lambda path, *, feature_pack: SimpleNamespace(
            feature_firewall_checked=True,
            feature_firewall_valid=True,
            feature_firewall_errors=(),
            split_plan=split_plan,
            refusal_reasons=(),
        ),
    )

    plan = plan_mlb_hr_preprocessing(
        feature_pack_path=feature_pack,
        staged_pack_path=staged_pack,
    )

    assert plan.split_source_kind == "staged_pack"
    assert plan.split_source_path == staged_pack.resolve()


def test_dry_run_does_not_mutate_operational_folders(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_pack, temporal_plan, staged_pack = _inputs(tmp_path)
    (staged_pack / "sentinel.txt").write_text("preserve pack", encoding="utf-8")
    restricted_roots = []
    for folder_name in (
        "output",
        "outputs",
        "history",
        "runtime",
        "manual-data",
    ):
        restricted = tmp_path / folder_name
        restricted.mkdir()
        (restricted / "sentinel.txt").write_text(
            f"preserve {folder_name}", encoding="utf-8"
        )
        restricted_roots.append(restricted)
    before_inputs = {
        "feature_pack": feature_pack.read_bytes(),
        "temporal_plan": temporal_plan.read_bytes(),
        "staged_pack": _snapshot_tree(staged_pack),
    }
    before_restricted = {
        root.name: _snapshot_tree(root) for root in restricted_roots
    }
    monkeypatch.chdir(tmp_path)

    assert preprocessing_cli.main(
        [
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
        ]
    ) == 0
    capsys.readouterr()

    assert feature_pack.read_bytes() == before_inputs["feature_pack"]
    assert temporal_plan.read_bytes() == before_inputs["temporal_plan"]
    assert _snapshot_tree(staged_pack) == before_inputs["staged_pack"]
    assert {
        root.name: _snapshot_tree(root) for root in restricted_roots
    } == before_restricted


def test_valid_fitted_artifact_writes_only_to_explicit_staging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    staging_dir = tmp_path / "fitted_artifact_staging"

    assert fitted_preprocessing_cli.main(
        [
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--output-staging-dir",
            str(staging_dir),
        ]
    ) == 0

    artifact_path = staging_dir / FITTED_PREPROCESSING_ARTIFACT_FILENAME
    assert artifact_path.is_file()
    assert list(staging_dir.iterdir()) == [artifact_path]
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == FITTED_PREPROCESSING_ARTIFACT_SCHEMA_VERSION
    assert payload["train_date_range"] == {
        "start": GAME_DATES[0].isoformat(),
        "end": GAME_DATES[17].isoformat(),
    }
    assert payload["numeric_medians"]["weather_temperature"] == 73.5
    assert payload["missing_indicators"]["weather_temperature"] is True
    assert payload["categorical_vocabularies"]["batter_hand"] == [
        "R",
        "__MISSING__",
        "__RARE__",
        "__UNKNOWN__",
    ]
    assert payload["rare_category_mappings"]["batter_hand"] == {}
    assert payload["category_policy"]["unknown_category_policy"] == (
        "map_non_train_category_to_unknown_token"
    )
    output = capsys.readouterr().out
    assert "model_training_enabled: false" in output
    assert "predictions_enabled: false" in output
    assert "production_enabled: false" in output


def test_loader_verifies_valid_fitted_artifact(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )

    loaded = load_fitted_preprocessing_artifact(
        result.artifact_path,
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
    )

    assert loaded == result.artifact
    assert loaded.train_row_count == 18
    assert loaded.numeric_medians["hitter_recent_hr_rate"] == pytest.approx(0.0485)
    assert loaded.category_policy["missing_token"] == "__MISSING__"
    assert not loaded.betting_enabled
    assert not loaded.production_approved


def test_loader_rejects_feature_pack_hash_mismatch(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )
    feature_pack.write_bytes(feature_pack.read_bytes() + b"\n")

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="feature-pack SHA-256 does not match",
    ):
        load_fitted_preprocessing_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_loader_rejects_temporal_split_hash_mismatch(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )
    temporal_plan.write_bytes(temporal_plan.read_bytes() + b"\n")

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="split-plan SHA-256 does not match",
    ):
        load_fitted_preprocessing_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_loader_rejects_missing_required_field(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    del payload["numeric_medians"]
    result.artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="missing required field.*numeric_medians",
    ):
        load_fitted_preprocessing_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_loader_rejects_unsupported_artifact_schema(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    payload["schema_version"] = "mlb-hr-fitted-preprocessing-artifact-v999"
    result.artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="unsupported fitted preprocessing schema_version",
    ):
        load_fitted_preprocessing_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


def test_loader_rejects_enabled_betting_gate(tmp_path: Path) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    payload["eligible_for_betting"] = True
    result.artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="explicitly disable gates: eligible_for_betting",
    ):
        load_fitted_preprocessing_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
        )


@pytest.mark.parametrize(
    "folder_name",
    ("output", "outputs", "history", "runtime", "manual-data", "cache"),
)
def test_fitted_artifact_rejects_operational_folder_write(
    tmp_path: Path,
    folder_name: str,
) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    forbidden_dir = tmp_path / folder_name
    forbidden_dir.mkdir()

    with pytest.raises(
        MLBHRFittedPreprocessingArtifactError,
        match="cannot be inside operational folders",
    ):
        write_fitted_preprocessing_artifact(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
            output_staging_dir=forbidden_dir,
        )

    assert list(forbidden_dir.iterdir()) == []


def test_fitted_artifact_performs_no_training_or_prediction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_pack, temporal_plan, _ = _inputs(tmp_path)
    planned: list[object] = []
    original_planner = fitted_artifacts.plan_mlb_hr_preprocessing

    def observe_planner(**kwargs: object) -> object:
        plan = original_planner(**kwargs)
        planned.append(plan)
        return plan

    monkeypatch.setattr(
        fitted_artifacts,
        "plan_mlb_hr_preprocessing",
        observe_planner,
    )
    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_artifact_staging",
    )

    assert planned
    assert all(not plan.model_training_enabled for plan in planned)
    assert all(not plan.predictions_enabled for plan in planned)
    assert all(not plan.backtesting_enabled for plan in planned)
    assert not result.artifact.model_training_enabled
    assert not result.artifact.predictions_enabled
    assert not result.artifact.backtesting_enabled
    assert list(result.output_dir.iterdir()) == [result.artifact_path]


def test_fitted_artifact_accepts_derived_temporal_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_pack, _, staged_pack = _inputs(tmp_path)
    split_plan = _split_plan(staged_pack)
    monkeypatch.setattr(
        preprocessing,
        "dry_run_historical_research_backtest",
        lambda path, *, feature_pack: SimpleNamespace(
            feature_firewall_checked=True,
            feature_firewall_valid=True,
            feature_firewall_errors=(),
            split_plan=split_plan,
            refusal_reasons=(),
        ),
    )

    result = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        staged_pack_path=staged_pack,
        output_staging_dir=tmp_path / "derived_fitted_artifact_staging",
    )

    assert result.artifact.split_source_kind == "staged_pack"
    assert result.artifact.split_hash_kind == "canonical_derived_plan_sha256"
