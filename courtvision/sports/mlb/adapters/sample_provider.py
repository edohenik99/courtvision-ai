"""Keyless sample data provider for the MLB HR prop report."""

from __future__ import annotations

from datetime import date, datetime, time

from courtvision.core.provider_registry import get_provider as get_provider_registration
from courtvision.sports.mlb.hr_prop_engine import HRPropInput
from courtvision.sports.mlb.research_context import MLBHRResearchContext


class SampleHRProvider:
    """Return the deterministic placeholder slate used by the HR report."""

    name = "sample"
    requires_external_keys = False
    _registration = get_provider_registration("mlb_sample")
    provider_name = _registration.name
    source_type = _registration.source_type
    supported_modes = _registration.supported_modes
    requires_credentials = _registration.requires_credentials
    required_env_vars = _registration.required_environment_variables
    capabilities = _registration.capabilities
    production_safe = _registration.production_safe
    can_be_used_for_production = _registration.can_be_used_for_production

    def get_hr_candidates(self, report_date: date) -> list[HRPropInput]:
        common = {
            "line": 0.5,
            "data_quality": "Sample data",
            "recent_plate_appearances": 30,
            "recent_batted_ball_events": [
                {"is_barrel": True},
                {"is_barrel": True},
                {"is_barrel": True},
                {"is_barrel": False},
                {"is_barrel": False},
                {"is_barrel": False},
                {"is_barrel": False},
                {"is_barrel": False},
                {"is_barrel": False},
                {"is_barrel": False},
            ],
            "handedness": "opposite",
        }
        return [
            HRPropInput(
                player="Example Player",
                team="CHC",
                opponent="STL",
                pitcher="Example Pitcher",
                sportsbook="DraftKings",
                odds=365,
                game_time=datetime.combine(report_date, time(19, 5)),
                venue="Wrigley Field",
                hard_hit_rate=0.57,
                barrel_rate=0.21,
                pull_rate=0.53,
                pull_barrel_rate=0.14,
                fly_ball_rate=0.51,
                max_exit_velocity=114.2,
                average_exit_velocity=93.1,
                recent_home_runs=3,
                pitcher_pitch_mix={"four-seam": 0.52, "slider": 0.31, "changeup": 0.17},
                hitter_vs_pitch_type={"four-seam": 0.92, "slider": 0.84, "changeup": 0.70},
                pitcher_hr_allowed_rate=0.061,
                ballpark_hr_factor=1.14,
                wind_direction="blowing out to center",
                wind_speed=14,
                temperature=84,
                **common,
            ),
            HRPropInput(
                player="Sample Slugger",
                team="NYY",
                opponent="BOS",
                pitcher="Sample Starter",
                sportsbook="FanDuel",
                odds="+310",
                game_time=datetime.combine(report_date, time(19, 10)),
                venue="Yankee Stadium",
                hard_hit_rate=0.51,
                barrel_rate=0.17,
                pull_rate=0.49,
                pull_barrel_rate=0.11,
                fly_ball_rate=0.46,
                max_exit_velocity=112.0,
                average_exit_velocity=91.5,
                recent_home_runs=2,
                pitcher_pitch_mix={"sinker": 0.44, "slider": 0.34, "changeup": 0.22},
                hitter_vs_pitch_type={"sinker": 0.76, "slider": 0.71, "changeup": 0.62},
                pitcher_hr_allowed_rate=0.048,
                ballpark_hr_factor=1.09,
                wind_direction="crosswind",
                wind_speed=8,
                temperature=78,
                **common,
            ),
            HRPropInput(
                player="Demo Batter",
                team="SEA",
                opponent="HOU",
                pitcher="Demo Pitcher",
                sportsbook="BetMGM",
                odds=440,
                game_time=datetime.combine(report_date, time(21, 40)),
                venue="T-Mobile Park",
                hard_hit_rate=0.43,
                barrel_rate=0.11,
                pull_rate=0.41,
                pull_barrel_rate=0.07,
                fly_ball_rate=0.39,
                max_exit_velocity=108.0,
                average_exit_velocity=88.6,
                recent_home_runs=1,
                pitcher_pitch_mix={"four-seam": 0.48, "sweeper": 0.33, "splitter": 0.19},
                hitter_vs_pitch_type={"four-seam": 0.59, "sweeper": 0.42, "splitter": 0.46},
                pitcher_hr_allowed_rate=0.028,
                ballpark_hr_factor=0.88,
                wind_direction="blowing in from left",
                wind_speed=9,
                temperature=64,
                **common,
            ),
        ]

    def get_candidates(self, report_date: date) -> list[HRPropInput]:
        """Compatibility-friendly alias for consumers using a generic name."""

        return self.get_hr_candidates(report_date)

    def get_hr_research_contexts(
        self, report_date: date
    ) -> list[MLBHRResearchContext]:
        """Return the deterministic Phase 2B contexts for keyless sample mode."""

        from courtvision.sports.mlb.research_context import (
            build_sample_mlb_hr_contexts,
        )

        return list(build_sample_mlb_hr_contexts(report_date))


SampleProvider = SampleHRProvider


def sample_hr_props(report_date: date) -> list[HRPropInput]:
    """Return keyless sample candidates without constructing a provider."""

    return SampleHRProvider().get_hr_candidates(report_date)


__all__ = ["SampleHRProvider", "SampleProvider", "sample_hr_props"]
