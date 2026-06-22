"""Typed sport/plugin registry for CourtVision.

This module is declarative only. Registering a plugin does not route a runtime,
select a provider, or grant access to bankroll-facing behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


class SportCode(str, Enum):
    """Sports reserved by the CourtVision plugin contract."""

    NBA = "NBA"
    MLB = "MLB"
    WNBA = "WNBA"
    NFL = "NFL"
    NHL = "NHL"


class SportMode(str, Enum):
    """Execution modes a sport plugin may explicitly support."""

    PRODUCTION = "production"
    RESEARCH = "research"
    SAMPLE = "sample"


class SportCapability(str, Enum):
    """Operations a sport plugin may explicitly advertise."""

    SCHEDULE = "schedule"
    ODDS = "odds"
    PROJECTIONS = "projections"
    RESEARCH_WATCHLIST = "research_watchlist"
    HISTORICAL_TRAINING = "historical_training"
    BACKTESTING = "backtesting"
    BETTING_APPROVAL = "betting_approval"
    KELLY_SIZING = "kelly_sizing"


PRODUCTION_ONLY_CAPABILITIES = frozenset(
    {SportCapability.BETTING_APPROVAL, SportCapability.KELLY_SIZING}
)
_PRODUCTION_SPORTS = frozenset({SportCode.NBA})


class CapabilityNotSupportedError(RuntimeError):
    """Raised when a caller requires a capability that was not registered."""


def _sport_code(value: SportCode | str) -> SportCode:
    if isinstance(value, SportCode):
        return value
    try:
        return SportCode(value.strip().upper())
    except (AttributeError, ValueError) as exc:
        supported = ", ".join(sport.value for sport in SportCode)
        raise KeyError(f"Unsupported sport {value!r}. Registered sport codes: {supported}") from exc


def _sport_mode(value: SportMode | str) -> SportMode:
    if isinstance(value, SportMode):
        return value
    try:
        return SportMode(value.strip().lower())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported sport mode {value!r}") from exc


def _sport_capability(value: SportCapability | str) -> SportCapability:
    if isinstance(value, SportCapability):
        return value
    try:
        return SportCapability(value.strip().lower())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported sport capability {value!r}") from exc


@dataclass(frozen=True, slots=True)
class SportPlugin:
    """Immutable declaration of one sport plugin and its allowed surface."""

    sport_code: SportCode
    plugin_name: str
    supported_markets: tuple[str, ...]
    supported_modes: frozenset[SportMode] = field(default_factory=frozenset)
    capabilities: frozenset[SportCapability] = field(default_factory=frozenset)
    active_season: bool = False
    data_provider: str = "not_configured"
    odds_provider: str = "not_configured"
    projection_model: str = "not_configured"
    reserved: bool = False

    def __post_init__(self) -> None:
        code = _sport_code(self.sport_code)
        modes = frozenset(_sport_mode(mode) for mode in self.supported_modes)
        capabilities = frozenset(
            _sport_capability(capability) for capability in self.capabilities
        )
        markets = tuple(
            dict.fromkeys(
                market.strip().lower()
                for market in self.supported_markets
                if market.strip()
            )
        )
        plugin_name = self.plugin_name.strip()

        if not plugin_name:
            raise ValueError("plugin_name must be explicit and non-empty")
        if self.reserved and (modes or capabilities):
            raise ValueError("Reserved plugins cannot advertise modes or capabilities")
        if SportMode.SAMPLE in modes and SportMode.PRODUCTION in modes:
            raise ValueError("Sample plugins cannot also expose production mode")
        if SportMode.PRODUCTION in modes and code not in _PRODUCTION_SPORTS:
            raise ValueError(f"{code.value} is not approved for production mode")

        production_capabilities = capabilities & PRODUCTION_ONLY_CAPABILITIES
        if production_capabilities and SportMode.PRODUCTION not in modes:
            names = ", ".join(sorted(capability.value for capability in production_capabilities))
            raise ValueError(
                f"Production-only capabilities require production mode: {names}"
            )
        if (
            SportCapability.KELLY_SIZING in capabilities
            and SportCapability.BETTING_APPROVAL not in capabilities
        ):
            raise ValueError("kelly_sizing requires explicit betting_approval")
        if code is SportCode.MLB and (
            SportMode.PRODUCTION in modes or production_capabilities
        ):
            raise ValueError("MLB must remain research/sample only")

        object.__setattr__(self, "sport_code", code)
        object.__setattr__(self, "plugin_name", plugin_name)
        object.__setattr__(self, "supported_markets", markets)
        object.__setattr__(self, "supported_modes", modes)
        object.__setattr__(self, "capabilities", capabilities)

    @property
    def key(self) -> str:
        return self.sport_code.value

    @property
    def sport_name(self) -> str:
        """Backward-compatible sport name used by existing sport modules."""

        return self.sport_code.value

    @property
    def name(self) -> str:
        """Backward-compatible alias for callers using generic configs."""

        return self.sport_name

    @property
    def supported_prop_markets(self) -> tuple[str, ...]:
        """Backward-compatible alias for the pre-Phase-1A market field."""

        return self.supported_markets

    @property
    def supported_props(self) -> tuple[str, ...]:
        return self.supported_markets

    def supports_market(self, market: str) -> bool:
        return market.strip().lower() in self.supported_markets

    def supports_mode(self, mode: SportMode | str) -> bool:
        return _sport_mode(mode) in self.supported_modes

    def supports_capability(self, capability: SportCapability | str) -> bool:
        return _sport_capability(capability) in self.capabilities

    def to_dict(self) -> dict[str, object]:
        """Return stable, JSON-friendly registry metadata."""

        return {
            "sport_code": self.sport_code.value,
            "sport_name": self.sport_name,
            "plugin_name": self.plugin_name,
            "supported_markets": list(self.supported_markets),
            "supported_prop_markets": list(self.supported_markets),
            "supported_modes": sorted(mode.value for mode in self.supported_modes),
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "active_season": self.active_season,
            "data_provider": self.data_provider,
            "odds_provider": self.odds_provider,
            "projection_model": self.projection_model,
            "reserved": self.reserved,
        }


# Compatibility name retained for code written against the original registry.
SportConfig = SportPlugin


class SportRegistry:
    """Case-insensitive registry with default-deny capability semantics."""

    def __init__(self, plugins: Iterable[SportPlugin] = ()) -> None:
        self._plugins: dict[str, SportPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: SportPlugin) -> None:
        key = plugin.key
        if key in self._plugins:
            raise ValueError(f"Sport {key!r} is already registered")
        self._plugins[key] = plugin

    def get(self, sport: SportCode | str) -> SportPlugin:
        code = _sport_code(sport)
        try:
            return self._plugins[code.value]
        except KeyError as exc:
            registered = ", ".join(self.keys()) or "none"
            raise KeyError(
                f"Sport {code.value!r} is not registered. Registered: {registered}"
            ) from exc

    def is_supported(self, sport: SportCode | str) -> bool:
        try:
            code = _sport_code(sport)
        except KeyError:
            return False
        return code.value in self._plugins

    def keys(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def all(self) -> tuple[SportPlugin, ...]:
        return tuple(self._plugins.values())

    @property
    def sports(self) -> Mapping[str, SportPlugin]:
        return MappingProxyType(self._plugins)


_DEFAULT_PLUGINS = (
    SportPlugin(
        sport_code=SportCode.NBA,
        plugin_name="nba_legacy_runtime",
        supported_markets=("points", "rebounds", "assists", "pra", "threes", "steals", "blocks"),
        supported_modes=frozenset({SportMode.PRODUCTION, SportMode.RESEARCH}),
        capabilities=frozenset(
            {
                SportCapability.SCHEDULE,
                SportCapability.ODDS,
                SportCapability.PROJECTIONS,
                SportCapability.RESEARCH_WATCHLIST,
                SportCapability.HISTORICAL_TRAINING,
                SportCapability.BETTING_APPROVAL,
                SportCapability.KELLY_SIZING,
            }
        ),
        active_season=False,
        data_provider="existing_nba_provider_manager",
        odds_provider="existing_nba_odds_pipeline",
        projection_model="courtvision.sports.nba.projection",
    ),
    SportPlugin(
        sport_code=SportCode.WNBA,
        plugin_name="wnba_reserved",
        supported_markets=("points", "rebounds", "assists", "pra", "threes", "steals", "blocks"),
        active_season=True,
        projection_model="courtvision.sports.wnba.projection.WNBAProjectionModel",
        reserved=True,
    ),
    SportPlugin(
        sport_code=SportCode.MLB,
        plugin_name="mlb_hr_research",
        supported_markets=("hits", "total_bases", "runs", "rbis", "home_runs", "strikeouts", "pitcher_outs"),
        supported_modes=frozenset({SportMode.RESEARCH, SportMode.SAMPLE}),
        capabilities=frozenset(
            {SportCapability.ODDS, SportCapability.RESEARCH_WATCHLIST}
        ),
        active_season=True,
        data_provider="existing_mlb_sample_provider",
        odds_provider="existing_mlb_hr_odds_adapter",
        projection_model="courtvision.sports.mlb.hr_prop_engine.HRPropEngine",
    ),
    SportPlugin(
        sport_code=SportCode.NFL,
        plugin_name="nfl_reserved",
        supported_markets=("passing_yards", "rushing_yards", "receiving_yards", "receptions", "touchdowns", "completions", "interceptions"),
        active_season=False,
        projection_model="courtvision.sports.nfl.projection.NFLProjectionModel",
        reserved=True,
    ),
    SportPlugin(
        sport_code=SportCode.NHL,
        plugin_name="nhl_reserved",
        supported_markets=("points", "goals", "assists", "shots_on_goal", "saves"),
        active_season=False,
        reserved=True,
    ),
)

SPORT_REGISTRY = SportRegistry(_DEFAULT_PLUGINS)
SUPPORTED_SPORTS = SPORT_REGISTRY.sports


def get_registered_sports() -> tuple[SportCode, ...]:
    """Return registered sport codes without importing or running plugins."""

    return tuple(plugin.sport_code for plugin in SPORT_REGISTRY.all())


def get_plugin(sport: SportCode | str) -> SportPlugin:
    """Return declarative metadata for a registered sport plugin."""

    return SPORT_REGISTRY.get(sport)


def get_sport(sport_name: SportCode | str) -> SportPlugin:
    """Backward-compatible alias for :func:`get_plugin`."""

    return get_plugin(sport_name)


def supports_capability(
    sport: SportCode | str, capability: SportCapability | str
) -> bool:
    return get_plugin(sport).supports_capability(capability)


def supports_mode(sport: SportCode | str, mode: SportMode | str) -> bool:
    return get_plugin(sport).supports_mode(mode)


def require_capability(
    sport: SportCode | str, capability: SportCapability | str
) -> SportPlugin:
    """Return the plugin or fail closed when a capability is not declared."""

    plugin = get_plugin(sport)
    normalized = _sport_capability(capability)
    if normalized not in plugin.capabilities:
        raise CapabilityNotSupportedError(
            f"{plugin.sport_name} plugin {plugin.plugin_name!r} does not support "
            f"capability {normalized.value!r}"
        )
    return plugin


def is_betting_approved(sport: SportCode | str) -> bool:
    plugin = get_plugin(sport)
    return (
        SportMode.PRODUCTION in plugin.supported_modes
        and SportCapability.BETTING_APPROVAL in plugin.capabilities
    )


def is_kelly_allowed(sport: SportCode | str) -> bool:
    plugin = get_plugin(sport)
    return (
        is_betting_approved(plugin.sport_code)
        and SportCapability.KELLY_SIZING in plugin.capabilities
    )


__all__ = [
    "CapabilityNotSupportedError",
    "PRODUCTION_ONLY_CAPABILITIES",
    "SPORT_REGISTRY",
    "SUPPORTED_SPORTS",
    "SportCapability",
    "SportCode",
    "SportConfig",
    "SportMode",
    "SportPlugin",
    "SportRegistry",
    "get_plugin",
    "get_registered_sports",
    "get_sport",
    "is_betting_approved",
    "is_kelly_allowed",
    "require_capability",
    "supports_capability",
    "supports_mode",
]
