"""Composable, offline-only MLB home-run research pipeline.

Sample remains the default.  The opt-in fixture path composes deterministic
Phase 2E contexts without provider I/O, training, production approval, or
bankroll-facing behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final

from courtvision.artifact_guard import guard_no_existing_artifact
from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
)
from courtvision.core.research_artifact import ResearchArtifact, write_artifact_json
from courtvision.sports.mlb.adapters.provider_factory import (
    UnsupportedProviderError,
    get_hr_provider,
)
from courtvision.sports.mlb.hr_prop_engine import HRPropEngine, HRPropInput, ResearchLabel
from courtvision.sports.mlb.hr_report import hr_assessments_to_research_artifact
from courtvision.sports.mlb.providers.fixture_provider import (
    FIXTURE_PROVIDER_NAME,
    MLBFixtureContextProvider,
    compose_hr_research_contexts,
)
from courtvision.sports.mlb.research_context import (
    MLBHRResearchContext,
    build_sample_mlb_hr_contexts,
    context_is_complete_for_research,
    summarize_context_warnings,
)
from courtvision.sports.mlb.research_safety import (
    MLB_BETTING_APPROVAL_STATUS,
    MLB_RESEARCH_MODE,
)


SAMPLE_PROVIDER: Final = "sample"
FIXTURE_PROVIDER: Final = "fixture"
MLB_HR_PIPELINE_WARNINGS: Final = (
    "Sample data only.",
    "Unvalidated research-only model.",
    "No historical validation.",
    "No production approval.",
    "Provider mode is sample.",
    "Missing live enrichment.",
    "No external schedule, lineup, or pitcher join.",
)
MLB_HR_FIXTURE_PIPELINE_WARNINGS: Final = (
    "Fixture data only.",
    "Unvalidated research-only model.",
    "No historical validation.",
    "No production approval.",
    "Provider path is fixture.",
    "Missing live enrichment.",
    "No external API calls.",
)


@dataclass(frozen=True, slots=True)
class MLBHRMissingContextRow:
    """Explicit context gap for one offline research candidate."""

    candidate_index: int
    player_name: str
    team: str
    game_date: date
    game_id: str | None
    missing_required_fields: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MLBHRResearchPipelineResult:
    """Immutable result from one offline-only MLB HR research run."""

    date: date
    provider_name: str
    provider_mode: str
    candidate_count: int
    watchlist_count: int
    artifact: ResearchArtifact
    normalized_odds_quotes: tuple[NormalizedOddsQuote, ...]
    candidate_contexts: tuple[MLBHRResearchContext | None, ...]
    context_count: int
    context_complete_count: int
    context_warning_count: int
    context_warnings: tuple[str, ...]
    missing_context_rows: tuple[MLBHRMissingContextRow, ...]
    sample_context_used: bool
    fixture_context_used: bool
    context_provider_name: str
    context_source_type: str
    warnings: tuple[str, ...]
    generated_at: datetime
    artifact_path: Path | None = None
    sport: str = field(default="MLB", init=False)
    league: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_RESEARCH_MODE, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    production_context_complete: bool = field(default=False, init=False)
    approval_status: str = field(
        default=MLB_BETTING_APPROVAL_STATUS,
        init=False,
    )


def _sample_event_id(report_date: date, index: int) -> str:
    return f"mlb-sample-{report_date.isoformat()}-{index:03d}"


def _normalized_text(value: object) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _candidate_game_date(candidate: HRPropInput, report_date: date) -> date:
    return (
        candidate.game_time.date()
        if isinstance(candidate.game_time, datetime)
        else report_date
    )


def _context_player_name(context: MLBHRResearchContext) -> str:
    if context.hitter_features is not None:
        return context.hitter_features.player_name
    if context.lineup_status is not None and context.lineup_status.batting_order:
        return context.lineup_status.batting_order[0].player_name
    return ""


def _context_player_id(context: MLBHRResearchContext) -> str:
    if context.hitter_features is not None:
        return context.hitter_features.player_id
    return ""


def _context_team(context: MLBHRResearchContext) -> str:
    return context.lineup_status.team if context.lineup_status is not None else ""


def _context_game_date(context: MLBHRResearchContext) -> date | None:
    return context.game.game_date if context.game is not None else None


def _context_game_id(context: MLBHRResearchContext) -> str:
    return context.game.game_id if context.game is not None else ""


def _context_identity_is_compatible(
    context: MLBHRResearchContext,
    *,
    player_name: str,
    team: str,
    game_date: date,
) -> bool:
    """Reject a key match only when an available identity conflicts."""

    context_player = _normalized_text(_context_player_name(context))
    context_team = _normalized_text(_context_team(context))
    context_date = _context_game_date(context)
    return not (
        (context_player and context_player != _normalized_text(player_name))
        or (context_team and context_team != _normalized_text(team))
        or (context_date is not None and context_date != game_date)
    )


def _match_context(
    contexts: tuple[MLBHRResearchContext, ...],
    *,
    player_name: str,
    team: str,
    game_date: date,
    game_id: str | None = None,
    player_id: str | None = None,
) -> MLBHRResearchContext | None:
    """Match one sample context using stable, explicit identity precedence."""

    if game_id:
        for context in contexts:
            if (
                _context_game_id(context) == game_id
                and _context_identity_is_compatible(
                    context,
                    player_name=player_name,
                    team=team,
                    game_date=game_date,
                )
            ):
                return context

    if player_id:
        for context in contexts:
            if (
                _context_player_id(context) == player_id
                and _context_identity_is_compatible(
                    context,
                    player_name=player_name,
                    team=team,
                    game_date=game_date,
                )
            ):
                return context

    identity = (
        _normalized_text(player_name),
        _normalized_text(team),
        game_date,
    )
    for context in contexts:
        if (
            _normalized_text(_context_player_name(context)),
            _normalized_text(_context_team(context)),
            _context_game_date(context),
        ) == identity:
            return context
    return None


def _lineup_status(context: MLBHRResearchContext) -> str:
    lineup = context.lineup_status
    if lineup is None:
        return "missing"
    player_id = _context_player_id(context)
    player_name = _normalized_text(_context_player_name(context))
    player = next(
        (
            item
            for item in lineup.batting_order
            if (player_id and item.player_id == player_id)
            or _normalized_text(item.player_name) == player_name
        ),
        None,
    )
    return player.status if player is not None else "unknown"


def _context_source_refs(
    context: MLBHRResearchContext | None,
    *,
    default_source_type: str,
) -> tuple[str, ...]:
    if context is None:
        return (
            f"context_source_type={default_source_type}",
            "context_match=missing",
            "context_complete=false",
            "production_context_complete=false",
            "missing_context_fields=context",
        )

    source_type = context.game.source_type if context.game is not None else "sample"
    probable_status = (
        context.probable_pitcher.probable_status
        if context.probable_pitcher is not None
        else "missing"
    )
    weather_quality = (
        context.weather.data_quality if context.weather is not None else "missing"
    )
    ballpark_quality = (
        context.ballpark.data_quality if context.ballpark is not None else "missing"
    )
    missing_fields = ",".join(context.missing_required_fields) or "none"
    return (
        f"context_source_type={source_type}",
        "context_match=matched",
        f"context_complete={str(context_is_complete_for_research(context)).lower()}",
        "production_context_complete=false",
        f"missing_context_fields={missing_fields}",
        f"lineup_status={_lineup_status(context)}",
        f"probable_pitcher_status={probable_status}",
        f"weather_data_quality={weather_quality}",
        f"ballpark_data_quality={ballpark_quality}",
    )


def _context_summary_refs(
    *,
    context_provider_name: str,
    context_source_type: str,
    context_count: int,
    context_complete_count: int,
) -> tuple[str, ...]:
    return (
        f"context_provider={context_provider_name}",
        f"context_source_type={context_source_type}",
        f"context_count={context_count}",
        f"context_complete_count={context_complete_count}",
        f"context_incomplete_count={context_count - context_complete_count}",
        "production_context_complete=false",
    )


def _enrich_artifact_with_context(
    artifact: ResearchArtifact,
    contexts: tuple[MLBHRResearchContext, ...],
    report_date: date,
    *,
    context_label: str,
    context_provider_name: str,
    context_source_type: str,
    context_count: int,
    context_complete_count: int,
) -> ResearchArtifact:
    rows = []
    for row in artifact.rows:
        row_date = (
            row.event_date.date()
            if isinstance(row.event_date, datetime)
            else row.event_date or report_date
        )
        context = _match_context(
            contexts,
            player_name=row.player_name or "",
            team=row.team or "",
            game_date=row_date,
            game_id=row.event_id,
            player_id=row.player_id,
        )
        context_warnings = (
            summarize_context_warnings(context)
            if context is not None
            else (f"No {context_label} context matched this candidate.",)
        )
        rows.append(
            replace(
                row,
                player_id=_context_player_id(context) if context is not None else None,
                event_id=_context_game_id(context) if context is not None else None,
                warnings=tuple(dict.fromkeys(row.warnings + context_warnings)),
                source_refs=tuple(
                    dict.fromkeys(
                        row.source_refs
                        + _context_source_refs(
                            context,
                            default_source_type=context_source_type,
                        )
                        + _context_summary_refs(
                            context_provider_name=context_provider_name,
                            context_source_type=context_source_type,
                            context_count=context_count,
                            context_complete_count=context_complete_count,
                        )
                    )
                ),
            )
        )
    return replace(artifact, rows=tuple(rows))


def _candidate_to_normalized_quote(
    candidate: HRPropInput,
    *,
    report_date: date,
    index: int,
    collected_at: datetime,
    provider_name: str,
    source_type: str,
    data_quality: str,
    event_id: str,
) -> NormalizedOddsQuote:
    """Map one offline candidate to the Phase 1B quote contract."""

    event_start_time = (
        candidate.game_time if isinstance(candidate.game_time, datetime) else None
    )
    return NormalizedOddsQuote(
        market_identity=OddsMarketIdentity(
            sport="MLB",
            league="MLB",
            event_id=event_id,
            event_date=report_date,
            home_team=candidate.team,
            away_team=candidate.opponent,
            market_type="batter_home_runs",
        ),
        selection=OddsSelection(
            selection_name=candidate.player,
            selection_id=f"{event_id}-player-{index:03d}",
            line=candidate.line,
        ),
        source_metadata=OddsSourceMetadata(
            sportsbook=candidate.sportsbook,
            provider=provider_name,
            mode=SAMPLE_PROVIDER,
            source_type=source_type,
            raw_provider_market_id="batter_home_runs",
            raw_event_id=event_id,
            data_quality=data_quality,
        ),
        american_odds=candidate.odds,
        collected_at=collected_at,
        event_start_time=event_start_time,
        is_live=False,
    )


def _fixture_candidate_from_context(
    context: MLBHRResearchContext,
    *,
    index: int,
) -> HRPropInput:
    """Project one composed fixture row into the existing HR input contract."""

    game = context.game
    lineup = context.lineup_status
    hitter = context.hitter_features
    if game is None or lineup is None or hitter is None:
        raise ValueError(
            "Fixture context cannot form an HR candidate without game, lineup, "
            "and hitter identity."
        )

    probable_pitcher = context.probable_pitcher
    pitcher = context.pitcher_features
    weather = context.weather
    ballpark = context.ballpark
    opponent = game.away_team if lineup.team == game.home_team else game.home_team
    pitch_mix = pitcher.pitch_mix if pitcher is not None else {}
    handedness = "unknown"
    if probable_pitcher is not None:
        handedness = (
            "opposite"
            if hitter.bats != probable_pitcher.throws
            else "same side"
        )
    recent_plate_appearances = 30
    return HRPropInput(
        player=hitter.player_name,
        team=lineup.team,
        opponent=opponent,
        pitcher=(
            probable_pitcher.pitcher_name
            if probable_pitcher is not None
            else "Unknown fixture pitcher"
        ),
        sportsbook="Local Fixture",
        odds=325 + (index * 25),
        line=0.5,
        game_time=game.event_start_time,
        venue=game.venue_name,
        handedness=handedness,
        recent_plate_appearances=recent_plate_appearances,
        recent_batted_ball_events=round(
            hitter.barrel_rate * recent_plate_appearances
        ),
        hard_hit_rate=hitter.hard_hit_rate,
        barrel_rate=hitter.barrel_rate,
        pull_rate=hitter.pull_rate,
        pull_barrel_rate=hitter.barrel_rate * hitter.pull_rate,
        fly_ball_rate=hitter.fly_ball_rate,
        max_exit_velocity=hitter.max_exit_velocity,
        average_exit_velocity=hitter.avg_exit_velocity,
        recent_home_runs=round(
            hitter.recent_hr_rate * recent_plate_appearances
        ),
        pitcher_pitch_mix=pitch_mix,
        hitter_vs_pitch_type={name: 0.5 for name in pitch_mix},
        pitcher_hr_allowed_rate=(
            pitcher.hr_allowed_rate if pitcher is not None else 0.0
        ),
        ballpark_hr_factor=(
            ballpark.park_factor_hr if ballpark is not None else 1.0
        ),
        wind_direction=(weather.wind_direction if weather is not None else "unknown"),
        wind_speed=(weather.wind_speed if weather is not None else 0.0),
        temperature=(weather.temperature if weather is not None else 70.0),
        data_quality=(
            "Fixture complete context"
            if context_is_complete_for_research(context)
            else "Fixture incomplete context"
        ),
    )


def run_mlb_hr_research_pipeline(
    report_date: date,
    provider: str = SAMPLE_PROVIDER,
    artifact_path: str | Path | None = None,
    *,
    generated_at: datetime | None = None,
) -> MLBHRResearchPipelineResult:
    """Run the MLB HR stack in keyless sample or opt-in fixture mode."""

    if isinstance(report_date, datetime) or not isinstance(report_date, date):
        raise TypeError("report_date must be a date")

    provider_name = provider.strip().lower()
    if provider_name not in {SAMPLE_PROVIDER, FIXTURE_PROVIDER}:
        raise UnsupportedProviderError(
            "MLB HR research pipeline supports only the keyless sample and "
            "fixture providers."
        )

    run_generated_at = generated_at or datetime.now(timezone.utc)
    if provider_name == SAMPLE_PROVIDER:
        adapter = get_hr_provider(SAMPLE_PROVIDER)
        candidates = tuple(adapter.get_hr_candidates(report_date))
        contexts = build_sample_mlb_hr_contexts(report_date)
        context_provider_name = SAMPLE_PROVIDER
        context_source_type = SAMPLE_PROVIDER
        provider_warnings = MLB_HR_PIPELINE_WARNINGS
    else:
        fixture_provider = MLBFixtureContextProvider(fixture_date=report_date)
        contexts = tuple(
            compose_hr_research_contexts(report_date, fixture_provider)
        )
        candidates = tuple(
            _fixture_candidate_from_context(context, index=index)
            for index, context in enumerate(contexts, start=1)
        )
        context_provider_name = FIXTURE_PROVIDER_NAME
        context_source_type = fixture_provider.source_type.value
        provider_warnings = MLB_HR_FIXTURE_PIPELINE_WARNINGS

    candidate_contexts = tuple(
        _match_context(
            contexts,
            player_name=candidate.player,
            team=candidate.team,
            game_date=_candidate_game_date(candidate, report_date),
            game_id=(
                _sample_event_id(report_date, index)
                if provider_name == SAMPLE_PROVIDER
                else None
            ),
            player_id=getattr(candidate, "player_id", None),
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    context_count = sum(context is not None for context in candidate_contexts)
    context_complete_count = sum(
        context is not None and context_is_complete_for_research(context)
        for context in candidate_contexts
    )

    context_warnings: list[str] = []
    missing_context_rows: list[MLBHRMissingContextRow] = []
    for index, (candidate, context) in enumerate(
        zip(candidates, candidate_contexts, strict=True), start=1
    ):
        if context is None:
            row_warnings = (
                f"No {provider_name} context matched this candidate.",
            )
            missing_fields = ("context",)
            game_id = None
        else:
            row_warnings = summarize_context_warnings(context)
            missing_fields = context.missing_required_fields
            game_id = _context_game_id(context) or None
        context_warnings.extend(
            f"{candidate.player}: {warning}" for warning in row_warnings
        )
        if missing_fields:
            missing_context_rows.append(
                MLBHRMissingContextRow(
                    candidate_index=index,
                    player_name=candidate.player,
                    team=candidate.team,
                    game_date=_candidate_game_date(candidate, report_date),
                    game_id=game_id,
                    missing_required_fields=missing_fields,
                    warnings=row_warnings,
                )
            )

    unique_context_warnings = tuple(dict.fromkeys(context_warnings))
    assessments = HRPropEngine().rank(candidates)
    quotes = tuple(
        _candidate_to_normalized_quote(
            candidate,
            report_date=report_date,
            index=index,
            collected_at=run_generated_at,
            provider_name=context_provider_name,
            source_type=context_source_type,
            data_quality=(
                "sample_data"
                if provider_name == SAMPLE_PROVIDER
                else "fixture_data"
            ),
            event_id=(
                _context_game_id(context)
                if context is not None and _context_game_id(context)
                else f"mlb-{provider_name}-{report_date.isoformat()}-{index:03d}"
            ),
        )
        for index, (candidate, context) in enumerate(
            zip(candidates, candidate_contexts, strict=True),
            start=1,
        )
    )
    artifact = hr_assessments_to_research_artifact(
        report_date,
        assessments,
        provider_names=(context_provider_name,),
        source_types=(context_source_type,),
        mode=SAMPLE_PROVIDER,
        generated_at=run_generated_at,
    )
    artifact = _enrich_artifact_with_context(
        artifact,
        contexts,
        report_date,
        context_label=provider_name,
        context_provider_name=context_provider_name,
        context_source_type=context_source_type,
        context_count=context_count,
        context_complete_count=context_complete_count,
    )

    destination: Path | None = None
    if artifact_path is not None:
        destination = Path(artifact_path)
        guard_no_existing_artifact(
            output_path=destination,
            caller="run_mlb_hr_research_pipeline",
            artifact_label="mlb_hr_research_artifact",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_artifact_json(artifact, destination)

    return MLBHRResearchPipelineResult(
        date=report_date,
        provider_name=provider_name,
        provider_mode=SAMPLE_PROVIDER,
        candidate_count=len(candidates),
        watchlist_count=sum(
            assessment.research_label is ResearchLabel.RESEARCH_WATCHLIST
            for assessment in assessments
        ),
        artifact=artifact,
        normalized_odds_quotes=quotes,
        candidate_contexts=candidate_contexts,
        context_count=context_count,
        context_complete_count=context_complete_count,
        context_warning_count=len(unique_context_warnings),
        context_warnings=unique_context_warnings,
        missing_context_rows=tuple(missing_context_rows),
        sample_context_used=(
            provider_name == SAMPLE_PROVIDER and bool(contexts)
        ),
        fixture_context_used=(
            provider_name == FIXTURE_PROVIDER and bool(contexts)
        ),
        context_provider_name=context_provider_name,
        context_source_type=context_source_type,
        warnings=tuple(
            dict.fromkeys(provider_warnings + unique_context_warnings)
        ),
        generated_at=run_generated_at,
        artifact_path=destination,
    )


__all__ = [
    "FIXTURE_PROVIDER",
    "MLBHRMissingContextRow",
    "MLBHRResearchPipelineResult",
    "MLB_HR_FIXTURE_PIPELINE_WARNINGS",
    "MLB_HR_PIPELINE_WARNINGS",
    "run_mlb_hr_research_pipeline",
]
