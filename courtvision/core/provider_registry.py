"""Declarative provider capability registry for CourtVision.

The registry describes existing provider adapters.  It does not select a
provider, fetch data, approve a sport, or make an odds quote betting-eligible.
All capability and credential checks are default-deny.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from courtvision.core.sport_registry import (
    SportCapability,
    SportCode,
    SportMode,
    get_plugin,
)


class ProviderCapability(str, Enum):
    """Data domains that a provider may explicitly advertise."""

    SCHEDULE = "schedule"
    ODDS = "odds"
    PLAYER_PROPS = "player_props"
    PLAYER_STATS = "player_stats"
    TEAM_STATS = "team_stats"
    INJURIES = "injuries"
    LINEUPS = "lineups"
    PROBABLE_PITCHERS = "probable_pitchers"
    WEATHER = "weather"
    BALLPARK_FACTORS = "ballpark_factors"
    HISTORICAL_DATA = "historical_data"
    HISTORICAL_ODDS = "historical_odds"
    PROJECTIONS = "projections"
    RESEARCH_WATCHLIST = "research_watchlist"


class ProviderMode(str, Enum):
    """Execution modes independently declared by a provider."""

    PRODUCTION = "production"
    RESEARCH = "research"
    SAMPLE = "sample"
    HISTORICAL = "historical"


class ProviderSourceType(str, Enum):
    """Origin of data exposed by a provider adapter."""

    LIVE = "live"
    SAMPLE = "sample"
    MANUAL = "manual"
    MOCK = "mock"
    HISTORICAL = "historical"


class CredentialPolicy(str, Enum):
    """Behavior when a provider's credential requirements are unmet."""

    FAIL_CLOSED = "fail_closed"
    ALLOW_SAMPLE_FALLBACK = "allow_sample_fallback"


class ProviderRegistryError(RuntimeError):
    """Base error for fail-closed provider registry checks."""


class ProviderCapabilityNotSupportedError(ProviderRegistryError):
    """Raised when a requested provider operation is not explicitly allowed."""


def _provider_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("provider name must be a non-empty string")
    return value.strip().casefold()


def _sport_code(value: SportCode | str) -> SportCode:
    if isinstance(value, SportCode):
        return value
    try:
        return SportCode(value.strip().upper())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported provider sport {value!r}") from exc


def _provider_mode(value: ProviderMode | str) -> ProviderMode:
    if isinstance(value, ProviderMode):
        return value
    try:
        return ProviderMode(value.strip().casefold())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported provider mode {value!r}") from exc


def _provider_capability(
    value: ProviderCapability | str,
) -> ProviderCapability:
    if isinstance(value, ProviderCapability):
        return value
    try:
        return ProviderCapability(value.strip().casefold())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported provider capability {value!r}") from exc


def _source_type(value: ProviderSourceType | str) -> ProviderSourceType:
    if isinstance(value, ProviderSourceType):
        return value
    try:
        return ProviderSourceType(value.strip().casefold())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported provider source type {value!r}") from exc


def _credential_policy(value: CredentialPolicy | str) -> CredentialPolicy:
    if isinstance(value, CredentialPolicy):
        return value
    try:
        return CredentialPolicy(value.strip().casefold())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Unsupported credential policy {value!r}") from exc


def _environment_names(values: Iterable[str]) -> tuple[str, ...]:
    names: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("environment variable names must be non-empty strings")
        name = value.strip()
        if name not in names:
            names.append(name)
    return tuple(names)


@dataclass(frozen=True, slots=True)
class ProviderRequirement:
    """One credential requirement satisfied by any listed environment name."""

    any_of: tuple[str, ...]
    label: str = ""

    def __post_init__(self) -> None:
        names = _environment_names(self.any_of)
        if not names:
            raise ValueError("ProviderRequirement.any_of cannot be empty")
        object.__setattr__(self, "any_of", names)
        object.__setattr__(self, "label", self.label.strip() or " or ".join(names))

    def is_satisfied(self, env: Mapping[str, object]) -> bool:
        return any(_has_environment_value(env, name) for name in self.any_of)


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    """Immutable declaration of one existing provider adapter."""

    name: str
    supported_sports: frozenset[SportCode]
    supported_modes: frozenset[ProviderMode] = field(default_factory=frozenset)
    capabilities: frozenset[ProviderCapability] = field(default_factory=frozenset)
    required_environment_variables: tuple[str, ...] = ()
    optional_environment_variables: tuple[str, ...] = ()
    credential_requirements: tuple[ProviderRequirement, ...] = ()
    source_type: ProviderSourceType = ProviderSourceType.MANUAL
    production_safe: bool = False
    can_be_used_for_production: bool = False
    can_be_used_for_research: bool = False
    can_be_used_for_sample: bool = False
    missing_credentials_policy: CredentialPolicy = CredentialPolicy.FAIL_CLOSED
    placeholder: bool = False

    def __post_init__(self) -> None:
        name = _provider_key(self.name)
        sports = frozenset(_sport_code(sport) for sport in self.supported_sports)
        modes = frozenset(_provider_mode(mode) for mode in self.supported_modes)
        capabilities = frozenset(
            _provider_capability(capability) for capability in self.capabilities
        )
        required = _environment_names(self.required_environment_variables)
        optional = _environment_names(self.optional_environment_variables)
        requirements = tuple(self.credential_requirements)
        if any(
            not isinstance(requirement, ProviderRequirement)
            for requirement in requirements
        ):
            raise TypeError(
                "credential_requirements must contain ProviderRequirement values"
            )
        source_type = _source_type(self.source_type)
        credential_policy = _credential_policy(self.missing_credentials_policy)

        if not sports:
            raise ValueError("A provider must declare at least one supported sport")
        overlap = set(required) & set(optional)
        if overlap:
            raise ValueError(
                "Environment variables cannot be both required and optional: "
                + ", ".join(sorted(overlap))
            )
        if self.placeholder and (modes or capabilities):
            raise ValueError("Placeholder providers cannot advertise modes or capabilities")
        if source_type is ProviderSourceType.SAMPLE and (required or requirements):
            raise ValueError("Sample providers must not require credentials")
        if source_type is ProviderSourceType.LIVE and (
            credential_policy is not CredentialPolicy.FAIL_CLOSED
        ):
            raise ValueError("Live providers must fail closed when credentials are missing")

        non_production_sources = {
            ProviderSourceType.SAMPLE,
            ProviderSourceType.MANUAL,
            ProviderSourceType.MOCK,
            ProviderSourceType.HISTORICAL,
        }
        exposes_production = (
            ProviderMode.PRODUCTION in modes or self.can_be_used_for_production
        )
        if exposes_production and not self.production_safe:
            raise ValueError("Production capability requires production_safe=True")
        if exposes_production and source_type in non_production_sources:
            raise ValueError(
                f"{source_type.value} providers cannot expose production capability"
            )
        if self.can_be_used_for_production and ProviderMode.PRODUCTION not in modes:
            raise ValueError("Production use requires the production mode")
        if self.can_be_used_for_research and ProviderMode.RESEARCH not in modes:
            raise ValueError("Research use requires the research mode")
        if self.can_be_used_for_sample and ProviderMode.SAMPLE not in modes:
            raise ValueError("Sample/offline use requires the sample mode")
        if SportCode.MLB in sports and exposes_production:
            raise ValueError("MLB providers must remain research/sample only")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "supported_sports", sports)
        object.__setattr__(self, "supported_modes", modes)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "required_environment_variables", required)
        object.__setattr__(self, "optional_environment_variables", optional)
        object.__setattr__(self, "credential_requirements", requirements)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "missing_credentials_policy", credential_policy)

    @property
    def requires_credentials(self) -> bool:
        return bool(self.required_environment_variables or self.credential_requirements)

    def supports_sport(self, sport: SportCode | str) -> bool:
        return _sport_code(sport) in self.supported_sports

    def supports_mode(self, mode: ProviderMode | str) -> bool:
        return _provider_mode(mode) in self.supported_modes

    def supports_capability(self, capability: ProviderCapability | str) -> bool:
        return _provider_capability(capability) in self.capabilities


class ProviderRegistry:
    """Duplicate-safe registry; empty until registrations are supplied."""

    def __init__(self, providers: Iterable[ProviderRegistration] = ()) -> None:
        self._providers: dict[str, ProviderRegistration] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderRegistration) -> None:
        if not isinstance(provider, ProviderRegistration):
            raise TypeError("provider must be a ProviderRegistration")
        if provider.name in self._providers:
            raise ValueError(f"Provider {provider.name!r} is already registered")
        self._providers[provider.name] = provider

    def get(self, provider_name: str) -> ProviderRegistration:
        key = _provider_key(provider_name)
        try:
            return self._providers[key]
        except KeyError as exc:
            registered = ", ".join(self.keys()) or "none"
            raise KeyError(
                f"Provider {provider_name!r} is not registered. Registered: {registered}"
            ) from exc

    def keys(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def all(self) -> tuple[ProviderRegistration, ...]:
        return tuple(self._providers.values())

    @property
    def providers(self) -> Mapping[str, ProviderRegistration]:
        return MappingProxyType(self._providers)

    def for_sport(self, sport: SportCode | str) -> tuple[ProviderRegistration, ...]:
        code = _sport_code(sport)
        return tuple(
            provider
            for provider in self._providers.values()
            if code in provider.supported_sports
        )

    def for_capability(
        self,
        sport: SportCode | str,
        capability: ProviderCapability | str,
    ) -> tuple[ProviderRegistration, ...]:
        code = _sport_code(sport)
        normalized = _provider_capability(capability)
        return tuple(
            provider
            for provider in self._providers.values()
            if code in provider.supported_sports
            and normalized in provider.capabilities
        )

    def require_capability(
        self,
        provider_name: str,
        sport: SportCode | str,
        capability: ProviderCapability | str,
        mode: ProviderMode | str,
    ) -> ProviderRegistration:
        """Require both provider declarations and sport/plugin approval."""

        return _require_declared_operation(
            self.get(provider_name), sport, capability, mode
        )

    def can_run(
        self,
        provider_name: str,
        sport: SportCode | str,
        capability: ProviderCapability | str,
        mode: ProviderMode | str,
        env: Mapping[str, object],
    ) -> bool:
        """Check one provider operation without importing or calling an adapter."""

        try:
            provider = self.require_capability(
                provider_name, sport, capability, mode
            )
        except (KeyError, ValueError, ProviderRegistryError):
            return False
        return not _missing_credentials(provider, env)


def _has_environment_value(env: Mapping[str, object], name: str) -> bool:
    value = env.get(name)
    return value is not None and bool(str(value).strip())


def _missing_credentials(
    provider: ProviderRegistration,
    env: Mapping[str, object],
) -> tuple[str, ...]:
    missing = [
        name
        for name in provider.required_environment_variables
        if not _has_environment_value(env, name)
    ]
    missing.extend(
        requirement.label
        for requirement in provider.credential_requirements
        if not requirement.is_satisfied(env)
    )
    return tuple(missing)


_SPORT_CAPABILITY_REQUIREMENTS = {
    ProviderCapability.SCHEDULE: SportCapability.SCHEDULE,
    ProviderCapability.ODDS: SportCapability.ODDS,
    ProviderCapability.PLAYER_PROPS: SportCapability.ODDS,
    ProviderCapability.PROJECTIONS: SportCapability.PROJECTIONS,
    ProviderCapability.RESEARCH_WATCHLIST: SportCapability.RESEARCH_WATCHLIST,
    ProviderCapability.HISTORICAL_DATA: SportCapability.HISTORICAL_TRAINING,
    ProviderCapability.HISTORICAL_ODDS: SportCapability.HISTORICAL_TRAINING,
}


def _require_declared_operation(
    provider: ProviderRegistration,
    sport: SportCode | str,
    capability: ProviderCapability | str,
    mode: ProviderMode | str,
) -> ProviderRegistration:
    code = _sport_code(sport)
    normalized_capability = _provider_capability(capability)
    normalized_mode = _provider_mode(mode)

    if code not in provider.supported_sports:
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} does not support sport {code.value!r}"
        )
    if normalized_capability not in provider.capabilities:
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} does not support capability "
            f"{normalized_capability.value!r}"
        )
    if normalized_mode not in provider.supported_modes:
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} does not support mode {normalized_mode.value!r}"
        )

    plugin = get_plugin(code)
    if normalized_mode is ProviderMode.HISTORICAL:
        if not plugin.supports_capability(SportCapability.HISTORICAL_TRAINING):
            raise ProviderCapabilityNotSupportedError(
                f"Sport {code.value!r} is not approved for historical provider use"
            )
    else:
        sport_mode = SportMode(normalized_mode.value)
        if not plugin.supports_mode(sport_mode):
            raise ProviderCapabilityNotSupportedError(
                f"Sport {code.value!r} is not approved for mode {normalized_mode.value!r}"
            )

    required_sport_capability = _SPORT_CAPABILITY_REQUIREMENTS.get(
        normalized_capability
    )
    if required_sport_capability and not plugin.supports_capability(
        required_sport_capability
    ):
        raise ProviderCapabilityNotSupportedError(
            f"Sport {code.value!r} does not support capability "
            f"{required_sport_capability.value!r}; provider registration cannot override it"
        )

    if normalized_mode is ProviderMode.PRODUCTION and not (
        provider.production_safe and provider.can_be_used_for_production
    ):
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} is not explicitly production-safe"
        )
    if normalized_mode is ProviderMode.RESEARCH and not provider.can_be_used_for_research:
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} is not enabled for research use"
        )
    if normalized_mode is ProviderMode.SAMPLE and not provider.can_be_used_for_sample:
        raise ProviderCapabilityNotSupportedError(
            f"Provider {provider.name!r} is not enabled for sample/offline use"
        )
    return provider


_DEFAULT_PROVIDERS = (
    ProviderRegistration(
        name="balldontlie",
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.PRODUCTION, ProviderMode.RESEARCH}),
        capabilities=frozenset(
            {
                ProviderCapability.SCHEDULE,
                ProviderCapability.ODDS,
                ProviderCapability.PLAYER_PROPS,
                ProviderCapability.PLAYER_STATS,
                ProviderCapability.INJURIES,
            }
        ),
        required_environment_variables=("BALLDONTLIE_API_KEY",),
        source_type=ProviderSourceType.LIVE,
        production_safe=True,
        can_be_used_for_production=True,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="sportsdataio",
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.PRODUCTION, ProviderMode.RESEARCH}),
        capabilities=frozenset(
            {
                ProviderCapability.SCHEDULE,
                ProviderCapability.ODDS,
                ProviderCapability.PLAYER_PROPS,
                ProviderCapability.PLAYER_STATS,
                ProviderCapability.INJURIES,
            }
        ),
        required_environment_variables=("SPORTSDATAIO_API_KEY",),
        optional_environment_variables=("SPORTSDATAIO_BASE_URL",),
        source_type=ProviderSourceType.LIVE,
        production_safe=True,
        can_be_used_for_production=True,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="api_nba",
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset(
            {
                ProviderCapability.SCHEDULE,
                ProviderCapability.PLAYER_STATS,
            }
        ),
        credential_requirements=(
            ProviderRequirement(
                any_of=("API_NBA_KEY", "API_SPORTS_KEY"),
                label="API_NBA_KEY or API_SPORTS_KEY",
            ),
        ),
        source_type=ProviderSourceType.LIVE,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="the_odds_api_nba",
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset(
            {ProviderCapability.ODDS, ProviderCapability.PLAYER_PROPS}
        ),
        required_environment_variables=("THE_ODDS_API_KEY",),
        source_type=ProviderSourceType.LIVE,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="manual_schedule",
        supported_sports=frozenset({SportCode.NBA}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset({ProviderCapability.SCHEDULE}),
        source_type=ProviderSourceType.MANUAL,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="mlb_sample",
        supported_sports=frozenset({SportCode.MLB}),
        supported_modes=frozenset({ProviderMode.RESEARCH, ProviderMode.SAMPLE}),
        capabilities=frozenset(
            {
                ProviderCapability.ODDS,
                ProviderCapability.PLAYER_PROPS,
                ProviderCapability.PLAYER_STATS,
                ProviderCapability.PROBABLE_PITCHERS,
                ProviderCapability.WEATHER,
                ProviderCapability.BALLPARK_FACTORS,
                ProviderCapability.RESEARCH_WATCHLIST,
            }
        ),
        source_type=ProviderSourceType.SAMPLE,
        can_be_used_for_research=True,
        can_be_used_for_sample=True,
    ),
    ProviderRegistration(
        name="the_odds_api_mlb",
        supported_sports=frozenset({SportCode.MLB}),
        supported_modes=frozenset({ProviderMode.RESEARCH}),
        capabilities=frozenset(
            {ProviderCapability.ODDS, ProviderCapability.PLAYER_PROPS}
        ),
        required_environment_variables=("COURTVISION_ODDS_API_KEY",),
        optional_environment_variables=(
            "COURTVISION_ODDS_REGION",
            "COURTVISION_ODDS_MARKETS",
        ),
        source_type=ProviderSourceType.LIVE,
        can_be_used_for_research=True,
    ),
    ProviderRegistration(
        name="mlb_stats_placeholder",
        supported_sports=frozenset({SportCode.MLB}),
        source_type=ProviderSourceType.MANUAL,
        placeholder=True,
    ),
    ProviderRegistration(
        name="mlb_weather_placeholder",
        supported_sports=frozenset({SportCode.MLB}),
        source_type=ProviderSourceType.MANUAL,
        placeholder=True,
    ),
    ProviderRegistration(
        name="mlb_ballpark_placeholder",
        supported_sports=frozenset({SportCode.MLB}),
        source_type=ProviderSourceType.MANUAL,
        placeholder=True,
    ),
)


PROVIDER_REGISTRY = ProviderRegistry(_DEFAULT_PROVIDERS)


def get_registered_providers() -> tuple[str, ...]:
    """Return provider names without importing adapters or running providers."""

    return PROVIDER_REGISTRY.keys()


def get_provider(provider_name: str) -> ProviderRegistration:
    return PROVIDER_REGISTRY.get(provider_name)


def providers_for_sport(
    sport: SportCode | str,
) -> tuple[ProviderRegistration, ...]:
    return PROVIDER_REGISTRY.for_sport(sport)


def providers_for_capability(
    sport: SportCode | str,
    capability: ProviderCapability | str,
) -> tuple[ProviderRegistration, ...]:
    return PROVIDER_REGISTRY.for_capability(sport, capability)


def provider_supports_mode(provider_name: str, mode: ProviderMode | str) -> bool:
    return get_provider(provider_name).supports_mode(mode)


def provider_requires_credentials(provider_name: str) -> bool:
    return get_provider(provider_name).requires_credentials


def provider_missing_credentials(
    provider_name: str,
    env: Mapping[str, object],
) -> tuple[str, ...]:
    return _missing_credentials(get_provider(provider_name), env)


def require_provider_capability(
    provider_name: str,
    sport: SportCode | str,
    capability: ProviderCapability | str,
    mode: ProviderMode | str,
) -> ProviderRegistration:
    """Return a registration only when provider and sport rules both allow it."""

    return PROVIDER_REGISTRY.require_capability(
        provider_name, sport, capability, mode
    )


def provider_can_run(
    provider_name: str,
    sport: SportCode | str,
    capability: ProviderCapability | str,
    mode: ProviderMode | str,
    env: Mapping[str, object],
) -> bool:
    """Conservatively check contract and credentials without provider I/O."""

    return PROVIDER_REGISTRY.can_run(
        provider_name, sport, capability, mode, env
    )


__all__ = [
    "CredentialPolicy",
    "PROVIDER_REGISTRY",
    "ProviderCapability",
    "ProviderCapabilityNotSupportedError",
    "ProviderMode",
    "ProviderRegistration",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderRequirement",
    "ProviderSourceType",
    "get_provider",
    "get_registered_providers",
    "provider_can_run",
    "provider_missing_credentials",
    "provider_requires_credentials",
    "provider_supports_mode",
    "providers_for_capability",
    "providers_for_sport",
    "require_provider_capability",
]
