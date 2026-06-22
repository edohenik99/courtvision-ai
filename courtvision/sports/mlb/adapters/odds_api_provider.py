"""The Odds API adapter for MLB batter home run markets.

The adapter is inert until ``COURTVISION_ODDS_API_KEY`` is configured.  It
normalizes provider payloads at the MLB boundary so raw API response shapes do
not leak into report or scoring code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import json
import os
from typing import Any
from urllib import error, parse, request

from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
)
from courtvision.sports.mlb.research_safety import (
    MLB_BETTING_APPROVAL_STATUS,
    MLB_NO_BETTING_REASON,
    MLB_RESEARCH_MODE,
)


ODDS_API_CONFIGURATION_MESSAGE = (
    "Odds API provider is not configured. Set COURTVISION_ODDS_API_KEY."
)
DEFAULT_REGION = "us"
DEFAULT_MARKETS = "batter_home_runs"


class OddsAPIProviderError(RuntimeError):
    """Base exception for cleanly reportable Odds API adapter failures."""


class OddsAPIConfigurationError(OddsAPIProviderError):
    """Raised before any request when required provider configuration is absent."""


class OddsAPIRequestError(OddsAPIProviderError):
    """Raised when a configured Odds API request cannot be completed."""


@dataclass(frozen=True, slots=True)
class HROddsCandidate:
    """Normalized sportsbook quote awaiting stats and context enrichment."""

    player: str
    team: str
    opponent: str
    pitcher: str
    sportsbook: str
    odds: int | str
    line: float
    market: str
    game_id: str
    commence_time: str
    timestamp: str
    home_team: str = field(default="", compare=False, repr=False)
    away_team: str = field(default="", compare=False, repr=False)
    sport: str = field(default="MLB", init=False)
    mode: str = field(default=MLB_RESEARCH_MODE, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    betting_approval_status: str = field(
        default=MLB_BETTING_APPROVAL_STATUS, init=False
    )
    no_betting_reason: str = field(default=MLB_NO_BETTING_REASON, init=False)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_normalized_quote(
        self,
        *,
        provider: str = "odds_api",
        region: str | None = None,
        source_type: str = "live",
        collected_at: datetime | None = None,
    ) -> NormalizedOddsQuote:
        """Map this existing MLB candidate into the shared quote contract."""

        event_start_time = _parse_iso_datetime(self.commence_time, "commence_time")
        quote_timestamp = _parse_iso_datetime(self.timestamp, "timestamp")
        home_team = self.home_team or self.team
        away_team = self.away_team or self.opponent
        return NormalizedOddsQuote(
            market_identity=OddsMarketIdentity(
                sport="MLB",
                league="MLB",
                event_id=self.game_id,
                event_date=event_start_time.date(),
                home_team=home_team,
                away_team=away_team,
                market_type=self.market,
            ),
            selection=OddsSelection(
                selection_name=self.player,
                line=self.line,
            ),
            source_metadata=OddsSourceMetadata(
                sportsbook=self.sportsbook,
                provider=provider,
                region=region,
                mode=MLB_RESEARCH_MODE,
                source_type=source_type,
                raw_provider_market_id=self.market,
                raw_event_id=self.game_id,
                data_quality="unenriched_price_reference",
            ),
            american_odds=self.odds,
            quote_timestamp=quote_timestamp,
            collected_at=collected_at,
            event_start_time=event_start_time,
            is_live=False,
        )


def _parse_iso_datetime(value: str, field_name: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required for normalized odds quotes")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class OddsAPIProvider:
    """Fetch and normalize MLB HR props from The Odds API when configured."""

    name = "odds_api"
    requires_external_keys = True
    endpoint = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        region: str | None = None,
        markets: str | None = None,
        http_get: Callable[[str], object] | None = None,
    ) -> None:
        self.api_key = (
            os.getenv("COURTVISION_ODDS_API_KEY", "") if api_key is None else api_key
        ).strip()
        self.region = (
            region if region is not None else os.getenv("COURTVISION_ODDS_REGION", DEFAULT_REGION)
        ).strip() or DEFAULT_REGION
        self.markets = (
            markets
            if markets is not None
            else os.getenv("COURTVISION_ODDS_MARKETS", DEFAULT_MARKETS)
        ).strip() or DEFAULT_MARKETS
        self._http_get = http_get or self._default_http_get

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def market_keys(self) -> tuple[str, ...]:
        return tuple(key.strip() for key in self.markets.split(",") if key.strip())

    def ensure_configured(self) -> None:
        """Validate credentials before building a URL or invoking a transport."""

        if not self.is_configured:
            raise OddsAPIConfigurationError(ODDS_API_CONFIGURATION_MESSAGE)

    def build_url(self) -> str:
        self.ensure_configured()
        query = parse.urlencode(
            {
                "apiKey": self.api_key,
                "regions": self.region,
                "markets": self.markets,
                "oddsFormat": "american",
                "dateFormat": "iso",
            }
        )
        return f"{self.endpoint}?{query}"

    @staticmethod
    def _default_http_get(url: str) -> object:
        try:
            with request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed HTTPS host
                return response.read()
        except (error.HTTPError, error.URLError, TimeoutError) as exc:
            raise OddsAPIRequestError("Unable to retrieve MLB HR odds from The Odds API.") from exc

    @staticmethod
    def _decode_payload(payload: object) -> Sequence[Mapping[str, Any]]:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise OddsAPIRequestError("The Odds API returned invalid JSON.") from exc
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
            raise OddsAPIRequestError("The Odds API returned an unexpected response shape.")
        return [event for event in payload if isinstance(event, Mapping)]

    def fetch_payload(self) -> Sequence[Mapping[str, Any]]:
        """Fetch the provider response; missing credentials fail before transport use."""

        self.ensure_configured()
        try:
            payload = self._http_get(self.build_url())
        except OddsAPIProviderError:
            raise
        except Exception as exc:
            raise OddsAPIRequestError("Unable to retrieve MLB HR odds from The Odds API.") from exc
        return self._decode_payload(payload)

    @staticmethod
    def _candidate_identity(
        outcome: Mapping[str, Any],
    ) -> tuple[str, bool]:
        name = str(outcome.get("name") or "").strip()
        description = str(outcome.get("description") or "").strip()
        selection = name.casefold()

        if description:
            if selection in {"under", "no"}:
                return description, False
            return description, selection in {"over", "yes"} or bool(description)

        if selection in {"over", "under", "yes", "no"}:
            return "", False
        return name, bool(name)

    @staticmethod
    def _teams(
        event: Mapping[str, Any], outcome: Mapping[str, Any]
    ) -> tuple[str, str]:
        team = str(
            outcome.get("team")
            or outcome.get("team_name")
            or event.get("team")
            or "TBD"
        ).strip()
        explicit_opponent = str(
            outcome.get("opponent") or event.get("opponent") or ""
        ).strip()
        if explicit_opponent:
            return team, explicit_opponent

        home_team = str(event.get("home_team") or "").strip()
        away_team = str(event.get("away_team") or "").strip()
        if team.casefold() == home_team.casefold() and away_team:
            return team, away_team
        if team.casefold() == away_team.casefold() and home_team:
            return team, home_team
        return team, "TBD"

    def normalize_payload(
        self, payload: object
    ) -> list[HROddsCandidate]:
        """Normalize supported over/yes HR outcomes from a mocked or live payload."""

        candidates: list[HROddsCandidate] = []
        for event in self._decode_payload(payload):
            game_id = str(event.get("id") or event.get("game_id") or "").strip()
            commence_time = str(event.get("commence_time") or "").strip()
            bookmakers = event.get("bookmakers") or ()
            if not isinstance(bookmakers, Sequence) or isinstance(bookmakers, (str, bytes)):
                continue
            for bookmaker in bookmakers:
                if not isinstance(bookmaker, Mapping):
                    continue
                sportsbook = str(
                    bookmaker.get("title") or bookmaker.get("key") or "Unknown"
                ).strip()
                book_timestamp = str(bookmaker.get("last_update") or "").strip()
                markets = bookmaker.get("markets") or ()
                if not isinstance(markets, Sequence) or isinstance(markets, (str, bytes)):
                    continue
                for market_payload in markets:
                    if not isinstance(market_payload, Mapping):
                        continue
                    market = str(market_payload.get("key") or "").strip()
                    if market not in self.market_keys:
                        continue
                    timestamp = str(
                        market_payload.get("last_update")
                        or book_timestamp
                        or event.get("last_update")
                        or commence_time
                    ).strip()
                    outcomes = market_payload.get("outcomes") or ()
                    if not isinstance(outcomes, Sequence) or isinstance(outcomes, (str, bytes)):
                        continue
                    for outcome in outcomes:
                        if not isinstance(outcome, Mapping) or outcome.get("price") is None:
                            continue
                        player, include = self._candidate_identity(outcome)
                        if not include:
                            continue
                        team, opponent = self._teams(event, outcome)
                        candidates.append(
                            HROddsCandidate(
                                player=player,
                                team=team,
                                opponent=opponent,
                                pitcher=str(outcome.get("pitcher") or "TBD").strip(),
                                sportsbook=sportsbook,
                                odds=outcome["price"],
                                line=float(outcome.get("point", 0.5)),
                                market=market,
                                game_id=game_id,
                                commence_time=commence_time,
                                timestamp=timestamp,
                                home_team=str(event.get("home_team") or "").strip(),
                                away_team=str(event.get("away_team") or "").strip(),
                            )
                        )
        return candidates

    def normalize_quotes(
        self,
        payload: object,
        *,
        source_type: str = "live",
        collected_at: datetime | None = None,
    ) -> list[NormalizedOddsQuote]:
        """Map a provider payload to the shared contract without changing runtime use."""

        return [
            candidate.to_normalized_quote(
                provider=self.name,
                region=self.region,
                source_type=source_type,
                collected_at=collected_at,
            )
            for candidate in self.normalize_payload(payload)
        ]

    def get_hr_candidates(self, report_date: date) -> list[HROddsCandidate]:
        """Return normalized odds candidates for later MLB context enrichment."""

        del report_date  # The endpoint returns the configured upcoming MLB slate.
        return self.normalize_payload(self.fetch_payload())

    def get_candidates(self, report_date: date) -> list[HROddsCandidate]:
        return self.get_hr_candidates(report_date)

    def get_odds(self, report_date: date) -> list[HROddsCandidate]:
        """OddsProvider-compatible alias retaining the enriched candidate fields."""

        return self.get_hr_candidates(report_date)


OddsAPICandidate = HROddsCandidate
NormalizedOddsCandidate = HROddsCandidate


__all__ = [
    "DEFAULT_MARKETS",
    "DEFAULT_REGION",
    "HROddsCandidate",
    "ODDS_API_CONFIGURATION_MESSAGE",
    "NormalizedOddsCandidate",
    "OddsAPICandidate",
    "OddsAPIConfigurationError",
    "OddsAPIProvider",
    "OddsAPIProviderError",
    "OddsAPIRequestError",
]
