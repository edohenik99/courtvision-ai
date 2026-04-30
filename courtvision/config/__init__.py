"""Configuration package for operator controls.

Config-driven (not hardcoded) operator control surface:
- conservative / balanced / aggressive mode
- max daily plays
- max portfolio exposure
- enable/disable simulation gate
- enable/disable market-adaptive thresholds
- enable/disable SGP builder
- enable/disable feedback adjustments

VALIDATE + CALIBRATE mode - Configuration and control only.

Task D: Add operator controls config layer
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from courtvision.balldontlie_auth import (
    BALLDONTLIE_API_KEY_ENV_VAR,
    clean_api_key,
    load_env_file as load_runtime_env_file,
)
from courtvision.config.operator_config import (
    ModePreset,
    OperatorConfig,
    create_aggressive_mode,
    create_balanced_mode,
    create_conservative_mode,
    create_shadow_mode,
    load_config,
    save_config,
)


@dataclass(slots=True)
class EliteThresholds:
    """Centralized elite board filtering thresholds.

    Used by both predict_pipeline.py and courtvision_ai.py to ensure
    consistent elite selection criteria across the codebase.
    """

    quality_score: float = 50.0
    confidence: float = 0.68
    player_minutes: float = 26.0
    player_edge: float = 1.5
    player_confidence: float = 0.63
    moneyline_edge: float = 0.055
    moneyline_confidence: float = 0.70
    max_plus_moneyline_odds: int = 225
    max_negative_moneyline_odds: int = -400
    board_limit: int = 20
    team_cap: int = 3
    game_cap: int = 4

    @classmethod
    def default(cls) -> "EliteThresholds":
        """Return default elite thresholds."""
        return cls()


# Kelly betting configuration
DEFAULT_BANKROLL = 1000


@dataclass(slots=True)
class Settings:
    api_key: str = ""
    base_url: str = "https://api.balldontlie.io/v1"
    per_page: int = 100
    request_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        load_runtime_env_file()
        return cls(
            api_key=clean_api_key(os.getenv(BALLDONTLIE_API_KEY_ENV_VAR, "")),
            base_url=os.getenv("BALLDONTLIE_BASE_URL", "https://api.balldontlie.io/v1").strip(),
            per_page=int(os.getenv("BALLDONTLIE_PER_PAGE", "100")),
            request_timeout_seconds=int(os.getenv("BALLDONTLIE_TIMEOUT", "30")),
        )


@dataclass(slots=True)
class ProviderSettings:
    """Configuration for data provider priority and settings."""

    provider_priority: list[str] = field(default_factory=lambda: ["balldontlie"])
    sportsdataio_api_key: str = ""
    sportsdataio_base_url: str = "https://api.sportsdata.io/v3/nba"
    balldontlie_api_key: str = ""

    @classmethod
    def from_env(cls) -> "ProviderSettings":
        """Load provider settings from environment variables."""
        load_runtime_env_file()

        # Parse provider priority from env or use default (balldontlie only)
        # Supports both DATA_PROVIDER_PRIORITY (new) and NBA_PROVIDER_PRIORITY (legacy)
        priority_env = os.getenv("DATA_PROVIDER_PRIORITY", os.getenv("NBA_PROVIDER_PRIORITY", "")).strip()
        if priority_env:
            priority = [p.strip().lower() for p in priority_env.split(",") if p.strip()]
        else:
            priority = ["balldontlie"]  # Default to balldontlie only

        return cls(
            provider_priority=priority,
            sportsdataio_api_key=os.getenv("SPORTSDATAIO_API_KEY", "").strip(),
            sportsdataio_base_url=os.getenv(
                "SPORTSDATAIO_BASE_URL",
                "https://api.sportsdata.io/v3/nba",
            ).strip(),
            balldontlie_api_key=clean_api_key(os.getenv(BALLDONTLIE_API_KEY_ENV_VAR, "")),
        )

    def get_provider_status(self) -> dict[str, Any]:
        """Return diagnostic status of provider configuration."""
        return {
            "provider_priority": self.provider_priority,
            "sportsdataio_configured": bool(self.sportsdataio_api_key),
            "balldontlie_configured": bool(self.balldontlie_api_key),
            "sportsdataio_env_var": "SPORTSDATAIO_API_KEY",
            "balldontlie_env_var": BALLDONTLIE_API_KEY_ENV_VAR,
        }


__all__ = [
    "Settings",
    "ProviderSettings",
    "EliteThresholds",
    "OperatorConfig",
    "ModePreset",
    "create_conservative_mode",
    "create_balanced_mode",
    "create_aggressive_mode",
    "create_shadow_mode",
    "load_config",
    "save_config",
]
