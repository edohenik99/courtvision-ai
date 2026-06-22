"""Deterministic, fixture-only MLB context providers and composition.

The fixture provider implements the Phase 2D acquisition contracts without
credentials, filesystem reads, or network access.  It exists to exercise the
Phase 2B research context boundary; it does not register a provider, score a
candidate, or authorize production use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Final

from courtvision.core.provider_registry import (
    ProviderCapability,
    ProviderMode,
    ProviderSourceType,
)
from courtvision.sports.mlb.providers.contracts import (
    MLBBallparkProvider,
    MLBHitterFeatureProvider,
    MLBLineupProvider,
    MLBPitcherFeatureProvider,
    MLBProbablePitcherProvider,
    MLBScheduleProvider,
    MLBWeatherProvider,
)
from courtvision.sports.mlb.research_context import (
    MLBBallparkContext,
    MLBGameContext,
    MLBHitterFeatureContext,
    MLBHRResearchContext,
    MLBLineupContext,
    MLBPitcherFeatureContext,
    MLBPlayerLineupStatus,
    MLBProbablePitcherContext,
    MLBWeatherContext,
)


FIXTURE_PROVIDER_NAME: Final = "mlb_fixture"
DEFAULT_FIXTURE_DATE: Final = date(2026, 6, 19)
DEFAULT_FEATURE_WINDOW: Final = "recent_30_pa"
_FIXTURE_WARNING: Final = "Local deterministic fixture; research use only."


@dataclass(frozen=True, slots=True)
class MLBContextProviderBundle:
    """Explicit set of providers used by fixture context composition."""

    schedule: MLBScheduleProvider
    lineups: MLBLineupProvider
    probable_pitchers: MLBProbablePitcherProvider
    hitter_features: MLBHitterFeatureProvider
    pitcher_features: MLBPitcherFeatureProvider
    weather: MLBWeatherProvider
    ballparks: MLBBallparkProvider

    @classmethod
    def from_provider(cls, provider: object) -> "MLBContextProviderBundle":
        """Use one implementation for every Phase 2D context contract."""

        required = (
            MLBScheduleProvider,
            MLBLineupProvider,
            MLBProbablePitcherProvider,
            MLBHitterFeatureProvider,
            MLBPitcherFeatureProvider,
            MLBWeatherProvider,
            MLBBallparkProvider,
        )
        missing = tuple(
            contract.__name__
            for contract in required
            if not isinstance(provider, contract)
        )
        if missing:
            raise TypeError(
                "provider does not implement required MLB contracts: "
                + ", ".join(missing)
            )
        return cls(
            schedule=provider,
            lineups=provider,
            probable_pitchers=provider,
            hitter_features=provider,
            pitcher_features=provider,
            weather=provider,
            ballparks=provider,
        )


class MLBFixtureContextProvider:
    """Serve a small, immutable MLB fixture through every Phase 2D contract."""

    provider_name = FIXTURE_PROVIDER_NAME
    source_type = ProviderSourceType.MOCK
    supported_modes = frozenset({ProviderMode.RESEARCH, ProviderMode.SAMPLE})
    requires_credentials = False
    required_env_vars: tuple[str, ...] = ()
    capabilities = frozenset(
        {
            ProviderCapability.SCHEDULE,
            ProviderCapability.LINEUPS,
            ProviderCapability.PROBABLE_PITCHERS,
            ProviderCapability.PLAYER_STATS,
            ProviderCapability.WEATHER,
            ProviderCapability.BALLPARK_FACTORS,
        }
    )
    production_safe = False
    can_be_used_for_production = False

    def __init__(self, fixture_date: date = DEFAULT_FIXTURE_DATE) -> None:
        if isinstance(fixture_date, datetime) or not isinstance(fixture_date, date):
            raise TypeError("fixture_date must be a date")
        self._fixture_date = fixture_date
        self._build_fixture()

    def _build_fixture(self) -> None:
        collected_at = datetime.combine(
            self._fixture_date, time(12, 0), tzinfo=timezone.utc
        )
        source = self.source_type.value
        warning = (_FIXTURE_WARNING,)

        game_one = MLBGameContext(
            game_id="mlb-fixture-complete",
            game_date=self._fixture_date,
            event_start_time=datetime.combine(
                self._fixture_date, time(19, 5), tzinfo=timezone.utc
            ),
            home_team="CHC",
            away_team="STL",
            venue_name="Wrigley Field",
            source_type=source,
            collected_at=collected_at,
            data_quality="fixture_complete",
            warnings=warning,
        )
        game_two = MLBGameContext(
            game_id="mlb-fixture-incomplete",
            game_date=self._fixture_date,
            event_start_time=datetime.combine(
                self._fixture_date, time(19, 10), tzinfo=timezone.utc
            ),
            home_team="NYY",
            away_team="BOS",
            venue_name="Yankee Stadium",
            source_type=source,
            collected_at=collected_at,
            data_quality="fixture_incomplete",
            warnings=warning,
        )
        self._games = (game_one, game_two)

        self._lineups = (
            MLBLineupContext(
                game_id=game_one.game_id,
                team="CHC",
                lineup_confirmed=True,
                batting_order=(
                    MLBPlayerLineupStatus(
                        player_id="fixture-hitter-1",
                        player_name="Fixture Hitter One",
                        bats="R",
                        batting_order=2,
                        status="confirmed",
                        position="RF",
                    ),
                    MLBPlayerLineupStatus(
                        player_id="fixture-hitter-2",
                        player_name="Fixture Hitter Two",
                        bats="L",
                        batting_order=4,
                        status="confirmed",
                        position="1B",
                    ),
                ),
                collected_at=collected_at,
                source_type=source,
                data_quality="fixture_complete",
                warnings=warning,
            ),
            MLBLineupContext(
                game_id=game_two.game_id,
                team="NYY",
                lineup_confirmed=False,
                batting_order=(
                    MLBPlayerLineupStatus(
                        player_id="fixture-hitter-3",
                        player_name="Fixture Hitter Three",
                        bats="L",
                        batting_order=1,
                        status="unknown",
                        position="CF",
                    ),
                    MLBPlayerLineupStatus(
                        player_id="fixture-hitter-4",
                        player_name="Fixture Hitter Four",
                        bats="R",
                        batting_order=3,
                        status="confirmed",
                        position="DH",
                    ),
                ),
                collected_at=collected_at,
                source_type=source,
                data_quality="fixture_incomplete",
                warnings=(
                    _FIXTURE_WARNING,
                    "Lineup confirmation is intentionally unavailable.",
                ),
            ),
        )

        self._probable_pitchers = (
            MLBProbablePitcherContext(
                game_id=game_one.game_id,
                team="STL",
                pitcher_id="fixture-pitcher-1",
                pitcher_name="Fixture Pitcher One",
                throws="R",
                probable_status="confirmed",
                collected_at=collected_at,
                source_type=source,
                data_quality="fixture_complete",
                warnings=warning,
            ),
            MLBProbablePitcherContext(
                game_id=game_two.game_id,
                team="BOS",
                pitcher_id="fixture-pitcher-2",
                pitcher_name="Fixture Pitcher Two",
                throws="L",
                probable_status="unknown",
                collected_at=collected_at,
                source_type=source,
                data_quality="fixture_incomplete",
                warnings=(
                    _FIXTURE_WARNING,
                    "Probable-pitcher confirmation is intentionally unavailable.",
                ),
            ),
        )

        hitter_rows = (
            (
                "fixture-hitter-1", "Fixture Hitter One", "R", 0.100,
                0.180, 0.520, 0.480, 0.500, 92.4, 113.1,
            ),
            (
                "fixture-hitter-2", "Fixture Hitter Two", "L", 0.067,
                0.150, 0.490, 0.450, 0.470, 91.2, 111.4,
            ),
            (
                "fixture-hitter-3", "Fixture Hitter Three", "L", 0.083,
                0.160, 0.500, 0.460, 0.490, 91.8, 112.0,
            ),
            (
                "fixture-hitter-4", "Fixture Hitter Four", "R", 0.050,
                0.130, 0.460, 0.410, 0.440, 90.1, 109.8,
            ),
        )
        self._hitter_features = tuple(
            MLBHitterFeatureContext(
                player_id=player_id,
                player_name=player_name,
                bats=bats,
                sample_window=DEFAULT_FEATURE_WINDOW,
                recent_hr_rate=recent_hr_rate,
                barrel_rate=barrel_rate,
                hard_hit_rate=hard_hit_rate,
                fly_ball_rate=fly_ball_rate,
                pull_rate=pull_rate,
                avg_exit_velocity=avg_exit_velocity,
                max_exit_velocity=max_exit_velocity,
                source_type=source,
                as_of_date=self._fixture_date,
                data_quality="fixture_data",
            )
            for (
                player_id,
                player_name,
                bats,
                recent_hr_rate,
                barrel_rate,
                hard_hit_rate,
                fly_ball_rate,
                pull_rate,
                avg_exit_velocity,
                max_exit_velocity,
            ) in hitter_rows
        )

        self._pitcher_features = (
            MLBPitcherFeatureContext(
                pitcher_id="fixture-pitcher-1",
                pitcher_name="Fixture Pitcher One",
                throws="R",
                pitch_mix={"four-seam": 0.54, "slider": 0.29, "changeup": 0.17},
                hr_allowed_rate=0.042,
                barrel_allowed_rate=0.087,
                hard_hit_allowed_rate=0.382,
                fly_ball_allowed_rate=0.341,
                source_type=source,
                as_of_date=self._fixture_date,
                data_quality="fixture_data",
            ),
            MLBPitcherFeatureContext(
                pitcher_id="fixture-pitcher-2",
                pitcher_name="Fixture Pitcher Two",
                throws="L",
                pitch_mix={"sinker": 0.46, "sweeper": 0.32, "changeup": 0.22},
                hr_allowed_rate=0.051,
                barrel_allowed_rate=0.096,
                hard_hit_allowed_rate=0.401,
                fly_ball_allowed_rate=0.356,
                source_type=source,
                as_of_date=self._fixture_date,
                data_quality="fixture_data",
            ),
        )

        self._weather = (
            MLBWeatherContext(
                game_id=game_one.game_id,
                venue_name=game_one.venue_name,
                temperature=81.0,
                wind_speed=11.0,
                wind_direction="out to center",
                wind_out_to_field="center",
                humidity=54.0,
                roof_status="open",
                source_type=source,
                collected_at=collected_at,
                data_quality="fixture_complete",
                warnings=warning,
            ),
        )
        self._ballparks = (
            MLBBallparkContext(
                venue_name=game_one.venue_name,
                park_factor_hr=1.08,
                handedness_factor={"L": 1.06, "R": 1.10},
                source_type=source,
                data_version="fixture-2026-06",
                data_quality="fixture_data",
                warnings=warning,
            ),
            MLBBallparkContext(
                venue_name=game_two.venue_name,
                park_factor_hr=1.11,
                handedness_factor={"L": 1.14, "R": 1.08},
                source_type=source,
                data_version="fixture-2026-06",
                data_quality="fixture_data",
                warnings=warning,
            ),
        )

    def get_games(self, report_date: date) -> list[MLBGameContext]:
        return list(self._games) if report_date == self._fixture_date else []

    def get_lineups(self, report_date: date) -> list[MLBLineupContext]:
        return list(self._lineups) if report_date == self._fixture_date else []

    def get_lineup_for_game(self, game_id: str) -> MLBLineupContext | None:
        return next((row for row in self._lineups if row.game_id == game_id), None)

    def get_probable_pitchers(
        self, report_date: date
    ) -> list[MLBProbablePitcherContext]:
        return list(self._probable_pitchers) if report_date == self._fixture_date else []

    def get_probable_pitcher_for_game(
        self, game_id: str, team: str
    ) -> MLBProbablePitcherContext | None:
        return next(
            (
                row
                for row in self._probable_pitchers
                if row.game_id == game_id and row.team == team
            ),
            None,
        )

    def get_hitter_features(
        self, player_id: str, as_of_date: date, window: str
    ) -> MLBHitterFeatureContext | None:
        if as_of_date != self._fixture_date or window != DEFAULT_FEATURE_WINDOW:
            return None
        return next(
            (row for row in self._hitter_features if row.player_id == player_id),
            None,
        )

    def get_pitcher_features(
        self, pitcher_id: str, as_of_date: date, window: str
    ) -> MLBPitcherFeatureContext | None:
        if as_of_date != self._fixture_date or window != DEFAULT_FEATURE_WINDOW:
            return None
        return next(
            (row for row in self._pitcher_features if row.pitcher_id == pitcher_id),
            None,
        )

    def get_weather_for_game(self, game: MLBGameContext) -> MLBWeatherContext | None:
        return next((row for row in self._weather if row.game_id == game.game_id), None)

    def get_ballpark_context(self, venue_name: str) -> MLBBallparkContext | None:
        return next(
            (row for row in self._ballparks if row.venue_name == venue_name),
            None,
        )

    def get_hr_research_contexts(
        self, report_date: date
    ) -> list[MLBHRResearchContext]:
        return compose_hr_research_contexts(report_date, self)


def _opposing_team(game: MLBGameContext, hitter_team: str) -> str | None:
    if hitter_team == game.home_team:
        return game.away_team
    if hitter_team == game.away_team:
        return game.home_team
    return None


def compose_hr_research_contexts(
    report_date: date,
    providers: MLBContextProviderBundle | object,
    *,
    feature_window: str = DEFAULT_FEATURE_WINDOW,
) -> list[MLBHRResearchContext]:
    """Compose per-hitter contexts using explicit Phase 2E fixture join keys."""

    if isinstance(report_date, datetime) or not isinstance(report_date, date):
        raise TypeError("report_date must be a date")
    if not isinstance(feature_window, str) or not feature_window.strip():
        raise ValueError("feature_window must be a non-empty string")
    bundle = (
        providers
        if isinstance(providers, MLBContextProviderBundle)
        else MLBContextProviderBundle.from_provider(providers)
    )

    games = bundle.schedule.get_games(report_date)
    lineups = bundle.lineups.get_lineups(report_date)
    probable_pitchers = bundle.probable_pitchers.get_probable_pitchers(report_date)
    contexts: list[MLBHRResearchContext] = []

    for game in games:
        game_lineups = tuple(row for row in lineups if row.game_id == game.game_id)
        weather = bundle.weather.get_weather_for_game(game)
        ballpark = bundle.ballparks.get_ballpark_context(game.venue_name)
        for lineup in game_lineups:
            opponent = _opposing_team(game, lineup.team)
            probable_pitcher = next(
                (
                    row
                    for row in probable_pitchers
                    if row.game_id == game.game_id and row.team == opponent
                ),
                None,
            )
            pitcher_features = (
                bundle.pitcher_features.get_pitcher_features(
                    probable_pitcher.pitcher_id,
                    report_date,
                    feature_window,
                )
                if probable_pitcher is not None
                else None
            )
            for hitter in lineup.batting_order:
                hitter_features = bundle.hitter_features.get_hitter_features(
                    hitter.player_id,
                    report_date,
                    feature_window,
                )
                warnings = [_FIXTURE_WARNING]
                if opponent is None:
                    warnings.append("Lineup team does not match the scheduled game.")
                if probable_pitcher is None:
                    warnings.append("Opposing probable pitcher is unavailable.")
                if weather is None:
                    warnings.append("Weather context is unavailable.")
                contexts.append(
                    MLBHRResearchContext(
                        game=game,
                        lineup_status=lineup,
                        probable_pitcher=probable_pitcher,
                        hitter_features=hitter_features,
                        pitcher_features=pitcher_features,
                        weather=weather,
                        ballpark=ballpark,
                        warnings=tuple(warnings),
                    )
                )
    return contexts


__all__ = [
    "DEFAULT_FEATURE_WINDOW",
    "DEFAULT_FIXTURE_DATE",
    "FIXTURE_PROVIDER_NAME",
    "MLBContextProviderBundle",
    "MLBFixtureContextProvider",
    "compose_hr_research_contexts",
]
