"""Provider selection for the MLB home run prop report."""

from __future__ import annotations

from courtvision.sports.mlb.adapters.odds_api_provider import OddsAPIProvider
from courtvision.sports.mlb.adapters.sample_provider import SampleHRProvider


SUPPORTED_PROVIDERS = ("sample", "odds_api")


class UnsupportedProviderError(ValueError):
    """Raised when the HR CLI is given an unknown provider name."""


def get_hr_provider(name: str = "sample") -> SampleHRProvider | OddsAPIProvider:
    """Create an MLB HR provider; sample remains the keyless default."""

    normalized = name.strip().lower()
    if normalized == "sample":
        return SampleHRProvider()
    if normalized == "odds_api":
        return OddsAPIProvider()
    supported = ", ".join(SUPPORTED_PROVIDERS)
    raise UnsupportedProviderError(
        f"Unsupported MLB HR provider {name!r}. Supported providers: {supported}."
    )


create_provider = get_hr_provider
get_provider = get_hr_provider


__all__ = [
    "SUPPORTED_PROVIDERS",
    "UnsupportedProviderError",
    "create_provider",
    "get_hr_provider",
    "get_provider",
]
