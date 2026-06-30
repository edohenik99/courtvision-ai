from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HISTORICAL_FEATURE_PACK_VERSION,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    build_label_custody_payload,
)
from courtvision.sports.mlb.training.hr_temporal_split_artifact import (
    TEMPORAL_SPLIT_ARTIFACT_FILENAME,
    MLBHRTemporalSplitArtifactError,
    load_mlb_hr_temporal_split_artifact,
    write_mlb_hr_temporal_split_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    plan_mlb_hr_preprocessing,
)
import scripts.mlb_dry_run_hr_preprocessing as preprocessing_cli
import scripts.mlb_write_hr_fitted_preprocessing as fitted_cli
import scripts.mlb_write_hr_temporal_split as split_cli


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_sealed_inputs(tmp_path: Path) -> tuple[Path, Path]:
    input_dir = tmp_path / "feature_staging"
    input_dir.mkdir()
    rows: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []
    for index, game_date in enumerate(GAME_DATES):
        date_text = game_date.isoformat()
        cutoff = f"{date_text}T20:00:00+00:00"
        values: dict[str, object] = {
            "weather_temperature": 65.0 + index,
            "hitter_recent_hr_rate": round(0.04 + index / 1000, 6),
            "batter_hand": "R" if index < 18 else "S" if index < 24 else "L",
            "sportsbook": "Historical Book A",
            "hr_market_available": True,
            "odds_collected_at": cutoff,
            "odds_as_of": cutoff,
        }
        availability = [
            {
                "feature_name": name,
                "available_at": f"{date_text}T18:00:00+00:00",
                "source_latest_game_date": (
                    (game_date - timedelta(days=1)).isoformat()
                    if name == "hitter_recent_hr_rate"
                    else None
                ),
            }
            for name in FEATURE_NAMES
        ]
        row_id = f"row-{index:02d}"
        rows.append(
            {
                "row_id": row_id,
                "game_id": f"game-{index:02d}",
                "game_date": date_text,
                "player_id": f"player-{index:02d}",
                "player_name": f"Batter {index:02d}",
                "odds_collected_at": cutoff,
                "event_start_time": f"{date_text}T23:00:00+00:00",
                "feature_values": values,
                "feature_availability": availability,
            }
        )
        labels.append({"row_id": row_id, "is_home_run": index % 7 == 0})

    feature_payload = {
        "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
        "mode": "historical_research",
        "readiness_verdict": (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ),
        "feature_names": list(FEATURE_NAMES),
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
    feature_pack = input_dir / "mlb_hr_historical_feature_pack.json"
    feature_pack.write_text(
        json.dumps(feature_payload, indent=2) + "\n", encoding="utf-8"
    )
    label_custody = input_dir / LABEL_CUSTODY_FILENAME
    label_custody.write_text(
        json.dumps(
            build_label_custody_payload(
                feature_payload=feature_payload,
                feature_pack_sha256=_sha256(feature_pack),
                labels=labels,
                created_at="2026-06-30T00:00:00+00:00",
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return feature_pack, label_custody


def test_valid_split_writes_once(tmp_path: Path) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    output_dir = tmp_path / "split_staging"

    result = write_mlb_hr_temporal_split_artifact(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        output_staging_dir=output_dir,
    )

    assert result.artifact_path == output_dir / TEMPORAL_SPLIT_ARTIFACT_FILENAME
    assert list(output_dir.iterdir()) == [result.artifact_path]
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["feature_pack_sha256"] == _sha256(feature_pack)
    assert payload["label_custody_sha256"] == _sha256(label_custody)
    assert payload["feature_pack_row_identity_sha256"]
    assert payload["code_version_sha256"]
    assert payload["split_rules_sha256"]
    assert payload["thresholds_sha256"]
    assert payload["train"]["game_dates"] == [
        value.isoformat() for value in GAME_DATES[:18]
    ]
    assert payload["validation"]["game_dates"] == [
        value.isoformat() for value in GAME_DATES[18:24]
    ]
    assert payload["test"]["game_dates"] == [
        value.isoformat() for value in GAME_DATES[24:]
    ]


def test_overwrite_fails(tmp_path: Path) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    output_dir = tmp_path / "split_staging"
    write_mlb_hr_temporal_split_artifact(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        output_staging_dir=output_dir,
    )

    with pytest.raises(MLBHRTemporalSplitArtifactError, match="must be empty"):
        write_mlb_hr_temporal_split_artifact(
            feature_pack_path=feature_pack,
            label_custody_path=label_custody,
            output_staging_dir=output_dir,
        )


def test_hash_mismatch_fails(tmp_path: Path) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    custody_payload = json.loads(label_custody.read_text(encoding="utf-8"))
    custody_payload["feature_pack_sha256"] = "0" * 64
    label_custody.write_text(json.dumps(custody_payload), encoding="utf-8")

    with pytest.raises(MLBHRTemporalSplitArtifactError, match="does not match"):
        write_mlb_hr_temporal_split_artifact(
            feature_pack_path=feature_pack,
            label_custody_path=label_custody,
            output_staging_dir=tmp_path / "split_staging",
        )


def test_persisted_artifact_hash_mismatch_fails(tmp_path: Path) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    result = write_mlb_hr_temporal_split_artifact(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        output_staging_dir=tmp_path / "split_staging",
    )
    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    payload["artifact_sha256"] = "0" * 64
    result.artifact_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MLBHRTemporalSplitArtifactError, match="content hash mismatch"):
        load_mlb_hr_temporal_split_artifact(
            result.artifact_path,
            feature_pack_path=feature_pack,
            label_custody_path=label_custody,
        )


def test_labels_remain_sealed(tmp_path: Path) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    result = write_mlb_hr_temporal_split_artifact(
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
        output_staging_dir=tmp_path / "split_staging",
    )

    payload = json.loads(result.artifact_path.read_text(encoding="utf-8"))
    assert payload["labels_opened"] is False
    assert "is_home_run" not in result.artifact_path.read_text(encoding="utf-8")
    assert not any("positive" in name or "negative" in name for name in payload)


@pytest.mark.parametrize(
    "folder_name", ["operational", "manual", "cache", "outputs", "history", "runtime"]
)
def test_operational_path_fails(tmp_path: Path, folder_name: str) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)

    with pytest.raises(MLBHRTemporalSplitArtifactError, match="cannot be inside"):
        write_mlb_hr_temporal_split_artifact(
            feature_pack_path=feature_pack,
            label_custody_path=label_custody,
            output_staging_dir=tmp_path / folder_name,
        )


def test_downstream_commands_can_consume_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feature_pack, label_custody = _build_sealed_inputs(tmp_path)
    output_dir = tmp_path / "split_staging"
    assert (
        split_cli.main(
            [
                "--feature-pack",
                str(feature_pack),
                "--label-custody",
                str(label_custody),
                "--output-staging-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    split_path = output_dir / TEMPORAL_SPLIT_ARTIFACT_FILENAME
    loaded = load_mlb_hr_temporal_split_artifact(
        split_path,
        feature_pack_path=feature_pack,
        label_custody_path=label_custody,
    )
    plan = plan_mlb_hr_preprocessing(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=split_path,
    )
    assert loaded.plan == plan.split_plan
    assert (
        preprocessing_cli.main(
            [
                "--feature-pack",
                str(feature_pack),
                "--temporal-split-plan",
                str(split_path),
            ]
        )
        == 0
    )
    assert (
        fitted_cli.main(
            [
                "--feature-pack",
                str(feature_pack),
                "--temporal-split-plan",
                str(split_path),
                "--output-staging-dir",
                str(tmp_path / "fitted_staging"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "labels_opened: false" in output
    assert "split_source_kind: temporal_split_plan" in output
    assert "CourtVision MLB HR fitted preprocessing artifact" in output
