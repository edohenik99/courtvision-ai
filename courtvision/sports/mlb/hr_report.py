"""Command-line report for CourtVision MLB home run prop research."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import sys
from typing import Sequence

from courtvision.core.research_artifact import (
    ResearchArtifact,
    ResearchArtifactMetadata,
    ResearchArtifactRow,
)
from courtvision.sports.mlb.adapters.odds_api_provider import (
    HROddsCandidate,
    OddsAPIConfigurationError,
    OddsAPIProvider,
    OddsAPIProviderError,
)
from courtvision.sports.mlb.adapters.provider_factory import (
    UnsupportedProviderError,
    get_hr_provider,
)
from courtvision.sports.mlb.adapters.sample_provider import sample_hr_props as _sample_hr_props
from courtvision.sports.mlb.hr_prop_engine import (
    HRPropAssessment,
    HRPropEngine,
    HRPropInput,
)


def _report_date(value: str) -> date:
    if value.strip().lower() == "today":
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be 'today' or YYYY-MM-DD") from exc


def sample_hr_props(report_date: date) -> list[HRPropInput]:
    """Backward-compatible access to the keyless sample provider slate."""

    return _sample_hr_props(report_date)


def build_hr_report(
    report_date: date,
    props: Sequence[HRPropInput] | None = None,
    *,
    provider: str = "sample",
) -> list[HRPropAssessment]:
    candidates = (
        list(props)
        if props is not None
        else get_hr_provider(provider).get_hr_candidates(report_date)
    )
    return HRPropEngine().rank(candidates)


def hr_assessments_to_research_artifact(
    report_date: date,
    assessments: Sequence[HRPropAssessment],
    *,
    provider_names: Sequence[str] = ("sample",),
    source_types: Sequence[str] = ("sample",),
    mode: str = "research",
    generated_at: datetime | None = None,
    code_version: str | None = None,
    data_version: str | None = None,
) -> ResearchArtifact:
    """Map existing MLB HR assessments into the Phase 1D research contract.

    This conversion does not write an output or alter the human-facing report.
    """

    rows = tuple(
        ResearchArtifactRow(
            row_id=f"mlb-hr-{report_date.isoformat()}-{index}",
            sport="MLB",
            league="MLB",
            player_name=assessment.player,
            team=assessment.team,
            opponent=assessment.opponent,
            event_date=(
                assessment.game_time.date()
                if isinstance(assessment.game_time, datetime)
                else report_date
            ),
            market_type="batter_home_runs",
            research_score=assessment.research_score,
            status=assessment.research_label.value,
            data_quality=assessment.data_quality,
            reasons=assessment.key_reasons,
            warnings=(assessment.no_betting_reason,),
            source_refs=(assessment.sportsbook,),
            mode=mode,
        )
        for index, assessment in enumerate(assessments, start=1)
    )
    return ResearchArtifact(
        metadata=ResearchArtifactMetadata(
            artifact_id=f"mlb-hr-research-{report_date.isoformat()}",
            sport="MLB",
            league="MLB",
            market_type="batter_home_runs",
            mode=mode,
            artifact_type="watchlist",
            run_date=report_date,
            generated_at=generated_at or datetime.now(timezone.utc),
            provider_names=tuple(provider_names),
            source_types=tuple(source_types),
            code_version=code_version,
            data_version=data_version,
        ),
        rows=rows,
    )


def render_hr_report(
    report_date: date,
    assessments: Sequence[HRPropAssessment],
) -> str:
    lines = [
        f"CourtVision MLB HR Research Watchlist — {report_date.isoformat()}",
        "Sample data | Research-only | Context not externally verified",
        "Research output only.",
        "=" * 62,
    ]
    for index, result in enumerate(assessments, start=1):
        lines.extend(
            [
                f"{index}. {result.player}",
                f"   Research Score: {result.research_score}/100 | Status: {result.research_label.value}",
                f"   Data Quality: {result.data_quality}",
                f"   Price reference: {result.odds} | Source: Sample Source {chr(64 + index)}",
                f"   Matchup: {result.matchup}",
                f"   Venue: {result.venue}",
                f"   Key reasons: {'; '.join(result.key_reasons)}",
            ]
        )
    return "\n".join(lines)


def render_odds_report(report_date: date, candidates: Sequence[HROddsCandidate]) -> str:
    """Render normalized live quotes without pretending model context is available."""

    lines = [
        f"CourtVision MLB HR Research Watchlist — {report_date.isoformat()}",
        "Research-only candidate feed | Context not externally verified",
        "Research output only; excluded from production approvals.",
        "Normalized price references awaiting stats/context enrichment",
        "=" * 62,
    ]
    for index, candidate in enumerate(candidates, start=1):
        lines.extend(
            [
                f"{index}. Candidate: {candidate.player}",
                "   Data Quality: Unenriched price reference",
                f"   Price reference: {candidate.odds} | Source: {candidate.sportsbook}",
                f"   Matchup: {candidate.team} vs {candidate.opponent}",
                f"   Market: {candidate.market} | Line: {candidate.line:g}",
                f"   Game: {candidate.game_id} | Commence: {candidate.commence_time}",
            ]
        )
    if not candidates:
        lines.append("No MLB HR odds candidates returned.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CourtVision MLB home run prop research report")
    parser.add_argument("--date", default="today", type=_report_date)
    parser.add_argument("--provider", default="sample", help="HR data provider (default: sample)")
    args = parser.parse_args(argv)
    try:
        provider = get_hr_provider(args.provider)
        if isinstance(provider, OddsAPIProvider):
            candidates = provider.get_hr_candidates(args.date)
            print(render_odds_report(args.date, candidates))
            return 0
        assessments = HRPropEngine().rank(provider.get_hr_candidates(args.date))
    except OddsAPIConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OddsAPIProviderError as exc:
        print(f"Odds API provider error: {exc}", file=sys.stderr)
        return 1
    except UnsupportedProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_hr_report(args.date, assessments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "build_hr_report",
    "hr_assessments_to_research_artifact",
    "main",
    "render_hr_report",
    "render_odds_report",
    "sample_hr_props",
]
