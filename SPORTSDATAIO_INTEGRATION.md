# SportsDataIO Integration Implementation Summary

## Overview
Integrated SportsDataIO as the primary NBA data provider with BallDontLie as automatic fallback.

## Files Changed

### 1. New Files Created
- `courtvision/clients/sportsdataio_client.py` - SportsDataIO API client
- `courtvision/clients/provider_manager.py` - Provider abstraction with fallback
- `tests/test_provider_manager.py` - Regression tests

### 2. Modified Files
- `courtvision/config.py` - Added `ProviderSettings` class
- `courtvision/engine.py` - Updated to use ProviderManager instead of BalldontlieClient

## Provider Order

Default priority (configurable via `NBA_PROVIDER_PRIORITY` env var):
1. **SportsDataIO** (primary)
2. **BallDontLie** (fallback)

## Environment Variables Added

```bash
# Required for SportsDataIO
SPORTSDATAIO_API_KEY=your_api_key_here

# Optional for SportsDataIO
SPORTSDATAIO_BASE_URL=https://api.sportsdata.io/v3/nba  # default

# Optional for provider priority
NBA_PROVIDER_PRIORITY=sportsdataio,balldontlie  # default
```

## Fallback Behavior

### Per-Domain Fallback
- Each data domain (games, players, stats, injuries, odds) tries providers independently
- SportsDataIO is attempted first
- If SportsDataIO fails, automatically falls back to BallDontLie
- Non-critical domains (injuries, odds) return empty list on total failure
- Critical domains (games, players, stats) raise RuntimeError on total failure

### Failure Scenarios Handled
1. **Missing credentials** - SportsDataIO skipped, uses BallDontLie
2. **Authentication errors** - Falls back to BallDontLie
3. **API errors (500, timeout)** - Falls back to BallDontLie
4. **Rate limiting** - Retries with backoff, then falls back
5. **Empty responses** - Falls back to BallDontLie
6. **All providers fail** - Raises RuntimeError with diagnostic info

## Logging / Diagnostics

### Runtime Status Tracking
```python
status = manager.get_run_status()
# Returns:
# - provider_attempted: First provider tried
# - provider_used: Provider that actually served the data
# - provider_fallback_used: True if fallback occurred
# - failure_reason: Error message if all failed
# - domain_status: Per-domain provider usage
```

### Log Output Examples
```
[courtvision.providers] ProviderManager initialized with priority: ['sportsdataio', 'balldontlie']
[courtvision.providers] SportsDataIO fetched 8 games for 2025-04-20
[courtvision.providers] Provider sportsdataio succeeded for games.get_games_by_date (returned 8 items)
[courtvision.providers] Provider run summary: status=sportsdataio_primary, attempted=sportsdataio, used=sportsdataio, fallback=False

[courtvision.providers] Provider sportsdataio failed for games.get_games_by_date: SportsDataIOAuthError
[courtvision.providers] Provider run summary: status=balldontlie_fallback, attempted=sportsdataio, used=balldontlie, fallback=True
```

### Status Labels
- `sportsdataio_primary` - SportsDataIO served all data
- `sportsdataio_partial_bdl_fallback` - Mixed provider usage
- `balldontlie_fallback` - BallDontLie served all data (SportsDataIO unavailable)
- `failed_no_provider` - All providers failed

## API Compatibility

The ProviderManager maintains the same interface as the original BalldontlieClient:

```python
manager = ProviderManager(settings)

games = manager.get_games_by_date("2025-04-20")
players = manager.get_active_players_for_team_ids(team_ids)
stats = manager.get_stats_for_player_ids(player_ids, [2025])
injuries = manager.get_team_injuries()
odds = manager.get_player_props_for_game(game_id)
```

## Configuration Options

### Via Environment Variables
```bash
export SPORTSDATAIO_API_KEY="your_key"
export NBA_PROVIDER_PRIORITY="sportsdataio,balldontlie"
```

### Via Code
```python
from courtvision.clients.provider_manager import ProviderManager

# Custom priority
manager = ProviderManager(
    settings,
    provider_priority=["balldontlie"]  # BallDontLie only
)
```

## Testing

Run regression tests:
```bash
pytest tests/test_provider_manager.py -v
```

Tests verify:
1. SportsDataIO attempted before BallDontLie
2. Missing credentials trigger fallback
3. API failures trigger fallback
4. BallDontLie-only mode works
5. Complete failure handling
6. Domain-specific fallback

## Backward Compatibility

- All existing BallDontLie-only configurations continue to work
- No changes to output file paths or schemas
- No changes to function signatures (additive only)
- Pipeline contract preserved
- Existing tests remain valid

## Normalized Data Mapping

SportsDataIO responses are normalized to match internal schema:
- Team names → abbreviations (LAL, GSW, etc.)
- Player names → first_name/last_name/full_name
- Stats → PlayerGameStats model
- Games → Game model with Team objects
- Injuries → Injury model
- Props → MarketProp model (best effort, limited coverage)

## Notes

- SportsDataIO odds coverage is limited; BallDontLie provides better odds data
- Injuries are optional - empty list returned on failure
- Odds are optional - empty list returned on failure
- Rate limiting handled with exponential backoff
- API key authentication via `Ocp-Apim-Subscription-Key` header
