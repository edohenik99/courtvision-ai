from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import socket
import urllib.request

import pytest

import courtvision.sports.mlb.hr_pipeline as hr_pipeline
from courtvision.core.odds import NormalizedOddsQuote
from courtvision.core.research_artifact import ResearchArtifact, validate_artifact
from courtvision.sports.mlb.adapters.provider_factory import UnsupportedProviderError
from courtvision.sports.mlb.hr_pipeline import run_mlb_hr_research_pipeline
from courtvision.sports.mlb.hr_report import build_hr_report
from courtvision.sports.mlb.research_context import build_sample_mlb_hr_contexts


RUN_DATE = date(2026, 6, 19)
GENERATED_AT = datetime(2026, 6, 19, 16, 30, tzinfo=timezone.utc)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def test_sample_pipeline_runs_keylessly_and_returns_default_deny_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert result.sport == "MLB"
    assert result.league == "MLB"
    assert result.date == RUN_DATE
    assert result.mode == "research"
    assert result.provider_mode == "sample"
    assert result.provider_name == "sample"
    assert result.candidate_count == 3
    assert result.watchlist_count == 1
    assert result.generated_at == GENERATED_AT
    assert result.eligible_for_betting is False
    assert result.kelly_eligible is False
    assert result.approval_status == "research_only_not_betting_approved"
    assert result.artifact_path is None
    assert "Sample data only." in result.warnings
    assert "No historical validation." in result.warnings
    assert "No external schedule, lineup, or pitcher join." in result.warnings


def test_pipeline_builds_sample_quotes_and_valid_research_artifact() -> None:
    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )
    artifact = result.artifact

    assert isinstance(artifact, ResearchArtifact)
    assert validate_artifact(artifact).is_valid
    assert artifact.metadata.sport == "MLB"
    assert artifact.metadata.league == "MLB"
    assert artifact.metadata.mode == "sample"
    assert artifact.metadata.provider_names == ("sample",)
    assert artifact.metadata.source_types == ("sample",)
    assert artifact.metadata.approval_status == "not_approved"
    assert artifact.metadata.eligible_for_betting is False
    assert artifact.metadata.kelly_eligible is False
    assert len(artifact.rows) == result.candidate_count
    assert all(row.mode == "sample" for row in artifact.rows)
    assert all(row.approval_status == "not_approved" for row in artifact.rows)
    assert all(row.eligible_for_betting is False for row in artifact.rows)
    assert all(row.kelly_eligible is False for row in artifact.rows)

    assert len(result.normalized_odds_quotes) == result.candidate_count
    assert all(
        isinstance(quote, NormalizedOddsQuote)
        for quote in result.normalized_odds_quotes
    )
    assert all(quote.sport == "MLB" for quote in result.normalized_odds_quotes)
    assert all(quote.mode == "sample" for quote in result.normalized_odds_quotes)
    assert all(
        quote.source_type == "sample" for quote in result.normalized_odds_quotes
    )
    assert all(
        quote.eligible_for_betting is False
        and quote.kelly_eligible is False
        and quote.approval_status == "not_approved"
        for quote in result.normalized_odds_quotes
    )


def test_sample_pipeline_attaches_complete_sample_contexts_and_summary() -> None:
    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert result.sample_context_used is True
    assert result.context_count == result.candidate_count == 3
    assert result.context_complete_count == 3
    assert result.context_warning_count == len(result.context_warnings) == 9
    assert result.missing_context_rows == ()
    assert result.production_context_complete is False
    assert all(context is not None for context in result.candidate_contexts)
    assert all(
        context is not None
        and context.game is not None
        and context.lineup_status is not None
        and context.probable_pitcher is not None
        and context.hitter_features is not None
        and context.pitcher_features is not None
        and context.weather is not None
        and context.ballpark is not None
        for context in result.candidate_contexts
    )
    assert any(
        "Deterministic fixture; not externally collected." in warning
        for warning in result.context_warnings
    )
    assert set(result.context_warnings).issubset(result.warnings)


def test_context_matching_is_deterministic_when_context_order_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = build_sample_mlb_hr_contexts(RUN_DATE)
    monkeypatch.setattr(
        hr_pipeline,
        "build_sample_mlb_hr_contexts",
        lambda report_date: tuple(reversed(contexts)),
    )

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert [
        context.game.game_id  # type: ignore[union-attr]
        for context in result.candidate_contexts
    ] == [
        "mlb-sample-2026-06-19-001",
        "mlb-sample-2026-06-19-002",
        "mlb-sample-2026-06-19-003",
    ]
    event_ids = {row.player_name: row.event_id for row in result.artifact.rows}
    assert event_ids == {
        "Example Player": "mlb-sample-2026-06-19-001",
        "Sample Slugger": "mlb-sample-2026-06-19-002",
        "Demo Batter": "mlb-sample-2026-06-19-003",
    }


def test_missing_context_is_explicit_and_does_not_break_sample_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = build_sample_mlb_hr_contexts(RUN_DATE)
    monkeypatch.setattr(
        hr_pipeline,
        "build_sample_mlb_hr_contexts",
        lambda report_date: contexts[:2],
    )

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert result.context_count == 2
    assert result.context_complete_count == 2
    assert result.candidate_contexts[-1] is None
    assert len(result.missing_context_rows) == 1
    gap = result.missing_context_rows[0]
    assert gap.player_name == "Demo Batter"
    assert gap.missing_required_fields == ("context",)
    assert "No sample context matched this candidate." in gap.warnings
    row = next(row for row in result.artifact.rows if row.player_name == "Demo Batter")
    assert "context_match=missing" in row.source_refs
    assert "context_complete=false" in row.source_refs
    assert "missing_context_fields=context" in row.source_refs
    assert "No sample context matched this candidate." in row.warnings


def test_incomplete_and_unknown_context_stays_incomplete_and_surfaces_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contexts = build_sample_mlb_hr_contexts(RUN_DATE)
    first = contexts[0]
    assert first.lineup_status is not None
    assert first.probable_pitcher is not None
    unknown_player = replace(first.lineup_status.batting_order[0], status="unknown")
    unknown_lineup = replace(
        first.lineup_status,
        lineup_confirmed=False,
        batting_order=(unknown_player,),
    )
    incomplete = replace(
        first,
        lineup_status=unknown_lineup,
        probable_pitcher=replace(
            first.probable_pitcher,
            probable_status="unknown",
        ),
        weather=None,
    )
    monkeypatch.setattr(
        hr_pipeline,
        "build_sample_mlb_hr_contexts",
        lambda report_date: (incomplete, *contexts[1:]),
    )

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert result.context_count == 3
    assert result.context_complete_count == 2
    assert result.production_context_complete is False
    assert len(result.missing_context_rows) == 1
    gap = result.missing_context_rows[0]
    assert gap.player_name == "Example Player"
    assert set(gap.missing_required_fields) == {
        "weather",
        "lineup_status.hitter_status",
        "probable_pitcher.probable_status",
    }
    assert any("Missing or invalid required context: weather." in item for item in gap.warnings)
    row = next(row for row in result.artifact.rows if row.player_name == "Example Player")
    assert "context_complete=false" in row.source_refs
    assert "lineup_status=unknown" in row.source_refs
    assert "probable_pitcher_status=unknown" in row.source_refs
    assert "weather_data_quality=missing" in row.source_refs


def test_context_enrichment_preserves_existing_research_scores() -> None:
    expected = {
        assessment.player: assessment.research_score
        for assessment in build_hr_report(RUN_DATE, provider="sample")
    }
    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    assert {
        row.player_name: row.research_score for row in result.artifact.rows
    } == expected


def test_artifact_rows_include_context_metadata_and_remain_default_deny() -> None:
    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    )

    for row in result.artifact.rows:
        assert row.player_id
        assert row.event_id
        assert "context_source_type=sample" in row.source_refs
        assert "context_match=matched" in row.source_refs
        assert "context_complete=true" in row.source_refs
        assert "production_context_complete=false" in row.source_refs
        assert "lineup_status=confirmed" in row.source_refs
        assert "probable_pitcher_status=confirmed" in row.source_refs
        assert "weather_data_quality=sample_data" in row.source_refs
        assert "ballpark_data_quality=sample_data" in row.source_refs
        assert row.approval_status == "not_approved"
        assert row.eligible_for_betting is False
        assert row.kelly_eligible is False


def test_artifact_serialization_is_deterministic_and_omits_forbidden_fields() -> None:
    first = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    ).artifact
    second = run_mlb_hr_research_pipeline(
        RUN_DATE,
        generated_at=GENERATED_AT,
    ).artifact

    assert first.to_json() == second.to_json()
    keys = _all_keys(first.to_dict())
    assert {
        "stake",
        "stake_amount",
        "unit",
        "units",
        "unit_size",
        "ev",
        "expected_value",
        "fair_probability",
        "estimated_fair_probability",
    }.isdisjoint(keys)


def test_optional_artifact_write_creates_parent_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "data" / "research" / "mlb" / "hr" / "artifact.json"

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        artifact_path=artifact_path,
        generated_at=GENERATED_AT,
    )

    assert result.artifact_path == artifact_path
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == result.artifact.to_dict()
    with pytest.raises(RuntimeError, match="ARTIFACT_OVERWRITE_GUARD"):
        run_mlb_hr_research_pipeline(
            RUN_DATE,
            artifact_path=artifact_path,
            generated_at=GENERATED_AT,
        )


def test_pipeline_rejects_non_sample_provider_before_provider_io() -> None:
    with pytest.raises(
        UnsupportedProviderError,
        match="only the keyless sample and fixture",
    ):
        run_mlb_hr_research_pipeline(RUN_DATE, provider="odds_api")


def test_fixture_pipeline_runs_keylessly_with_composed_contexts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURTVISION_ODDS_API_KEY", raising=False)
    compose = hr_pipeline.compose_hr_research_contexts
    calls: list[date] = []

    def recording_compose(report_date: date, provider: object):
        calls.append(report_date)
        return compose(report_date, provider)

    monkeypatch.setattr(hr_pipeline, "compose_hr_research_contexts", recording_compose)

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        provider="fixture",
        generated_at=GENERATED_AT,
    )

    assert calls == [RUN_DATE]
    assert result.provider_name == "fixture"
    assert result.provider_mode == "sample"
    assert result.context_provider_name == "mlb_fixture"
    assert result.context_source_type == "mock"
    assert result.fixture_context_used is True
    assert result.sample_context_used is False
    assert result.candidate_count == result.context_count == 4
    assert result.context_complete_count == 2
    assert len(result.missing_context_rows) == 2
    assert result.production_context_complete is False
    assert result.mode == "research"
    assert result.eligible_for_betting is False
    assert result.kelly_eligible is False
    assert result.approval_status == "research_only_not_betting_approved"
    assert all(context is not None for context in result.candidate_contexts)
    assert {row.player_name for row in result.artifact.rows} == {
        "Fixture Hitter One",
        "Fixture Hitter Two",
        "Fixture Hitter Three",
        "Fixture Hitter Four",
    }


def test_fixture_incomplete_context_rows_and_warnings_are_explicit() -> None:
    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        provider="fixture",
        generated_at=GENERATED_AT,
    )

    gaps = {row.player_name: row for row in result.missing_context_rows}
    assert set(gaps) == {"Fixture Hitter Three", "Fixture Hitter Four"}
    assert "weather" in gaps["Fixture Hitter Three"].missing_required_fields
    assert (
        "lineup_status.hitter_status"
        in gaps["Fixture Hitter Three"].missing_required_fields
    )
    assert (
        "probable_pitcher.probable_status"
        in gaps["Fixture Hitter Four"].missing_required_fields
    )
    assert any("Fixture Hitter Three:" in warning for warning in result.context_warnings)
    assert any("Fixture Hitter Four:" in warning for warning in result.context_warnings)
    assert set(result.context_warnings).issubset(result.warnings)

    rows = {row.player_name: row for row in result.artifact.rows}
    for player_name in ("Fixture Hitter Three", "Fixture Hitter Four"):
        assert "context_complete=false" in rows[player_name].source_refs
        assert any(
            ref.startswith("missing_context_fields=")
            and ref != "missing_context_fields=none"
            for ref in rows[player_name].source_refs
        )
        assert rows[player_name].warnings


def test_fixture_artifact_is_valid_context_enriched_and_default_deny(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "fixture" / "artifact.json"

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        provider="fixture",
        artifact_path=artifact_path,
        generated_at=GENERATED_AT,
    )
    artifact = result.artifact

    assert validate_artifact(artifact).is_valid
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact.to_dict()
    assert artifact.metadata.mode == "sample"
    assert artifact.metadata.provider_names == ("mlb_fixture",)
    assert artifact.metadata.source_types == ("mock",)
    assert artifact.metadata.approval_status == "not_approved"
    assert artifact.metadata.eligible_for_betting is False
    assert artifact.metadata.kelly_eligible is False
    for row in artifact.rows:
        assert "context_provider=mlb_fixture" in row.source_refs
        assert "context_source_type=mock" in row.source_refs
        assert "context_count=4" in row.source_refs
        assert "context_complete_count=2" in row.source_refs
        assert "context_incomplete_count=2" in row.source_refs
        assert "production_context_complete=false" in row.source_refs
        assert row.approval_status == "not_approved"
        assert row.eligible_for_betting is False
        assert row.kelly_eligible is False

    forbidden = {
        "stake",
        "stake_amount",
        "unit",
        "units",
        "unit_size",
        "ev",
        "expected_value",
        "fair_probability",
        "estimated_fair_probability",
    }
    assert forbidden.isdisjoint(_all_keys(artifact.to_dict()))


def test_fixture_pipeline_makes_no_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("fixture pipeline attempted network access")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)

    result = run_mlb_hr_research_pipeline(
        RUN_DATE,
        provider="fixture",
        generated_at=GENERATED_AT,
    )

    assert result.candidate_count == 4
    assert result.context_count == 4
