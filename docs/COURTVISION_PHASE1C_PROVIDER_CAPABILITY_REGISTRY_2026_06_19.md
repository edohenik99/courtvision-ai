# CourtVision Phase 1C: Provider Capability Registry

Date: 2026-06-19  
Status: Complete; full suite green.

## What Was Added

Phase 1C adds an immutable, typed provider capability contract in
`courtvision/core/provider_registry.py`:

- `ProviderCapability`
- `ProviderMode`
- `ProviderSourceType`
- `CredentialPolicy`
- `ProviderRequirement`
- `ProviderRegistration`
- `ProviderRegistry`
- `ProviderRegistryError`
- `ProviderCapabilityNotSupportedError`
- a known default `PROVIDER_REGISTRY`
- safe query and validation helpers

The public helpers are:

- `get_registered_providers()`
- `get_provider()`
- `providers_for_sport()`
- `providers_for_capability()`
- `provider_supports_mode()`
- `provider_requires_credentials()`
- `provider_missing_credentials()`
- `require_provider_capability()`
- `provider_can_run()`

The types and helpers are exported from `courtvision.core`. A separately
constructed `ProviderRegistry()` starts empty; the module-level registry is
explicitly populated with the existing providers described below.

## Why This Is Contract-Only

The registry is declarative metadata. It imports no provider adapter and makes
no network request. Registration does not select a provider, alter provider
priority, route a runtime, normalize an odds payload, produce a projection,
approve a market, or size a wager.

Existing provider-specific adapters keep their existing interfaces. Neither
the NBA runtime nor the MLB sample/report CLI depends on the registry.

## Registered Providers

| Registry name | Existing implementation | Sports | Modes | Source | Production-safe |
| --- | --- | --- | --- | --- | --- |
| `balldontlie` | `courtvision.clients.balldontlie_client` | NBA | production, research | live | yes; describes existing NBA use only |
| `sportsdataio` | `courtvision.clients.sportsdataio_client` | NBA | production, research | live | yes; describes existing NBA use only |
| `api_nba` | `courtvision.clients.api_nba_client` | NBA | research | live | no |
| `the_odds_api_nba` | `courtvision.providers.the_odds_api_provider` | NBA | research | live | no |
| `manual_schedule` | `courtvision.providers.research_schedule_resolver` | NBA | research | manual | no |
| `mlb_sample` | `courtvision.sports.mlb.adapters.sample_provider` | MLB | research, sample | sample | no |
| `the_odds_api_mlb` | `courtvision.sports.mlb.adapters.odds_api_provider` | MLB | research | live | no |
| `mlb_stats_placeholder` | existing stats adapter shell | MLB | none | manual placeholder | no |
| `mlb_weather_placeholder` | existing weather adapter shell | MLB | none | manual placeholder | no |
| `mlb_ballpark_placeholder` | existing ballpark adapter shell | MLB | none | manual placeholder | no |

The three MLB placeholders are explicitly inert: they advertise no mode and no
capability. Their registration records that the shells exist without implying
that a data source is configured.

SportsGameOdds, OpticOdds, Retrosheet, Baseball Savant, Lahman, and Open-Meteo
are not registered. No new provider implementation was added.

## Provider Capabilities

| Provider | Declared capabilities |
| --- | --- |
| `balldontlie` | schedule, odds, player props, player stats, injuries |
| `sportsdataio` | schedule, odds, player props, player stats, injuries |
| `api_nba` | schedule, player stats |
| `the_odds_api_nba` | odds, player props |
| `manual_schedule` | schedule |
| `mlb_sample` | odds, player props, player stats, probable pitchers, weather, ballpark factors, research watchlist |
| `the_odds_api_mlb` | odds, player props |
| MLB placeholders | none |

Capabilities are intentionally conservative. For example, API-NBA's team-list
endpoint is not described as team statistics, and inert MLB shells expose no
future capability.

## Environment Variables

| Provider | Required credential contract | Optional configuration |
| --- | --- | --- |
| `balldontlie` | `BALLDONTLIE_API_KEY` | none registered |
| `sportsdataio` | `SPORTSDATAIO_API_KEY` | `SPORTSDATAIO_BASE_URL` |
| `api_nba` | either `API_NBA_KEY` or `API_SPORTS_KEY` | none registered |
| `the_odds_api_nba` | `THE_ODDS_API_KEY` | none registered |
| `manual_schedule` | none | none |
| `mlb_sample` | none | none |
| `the_odds_api_mlb` | `COURTVISION_ODDS_API_KEY` | `COURTVISION_ODDS_REGION`, `COURTVISION_ODDS_MARKETS` |
| MLB placeholders | none | none |

`ProviderRequirement` models the existing API-NBA alternative-key contract.
Blank values do not satisfy a credential requirement.

## Default-Deny Rules

- Registration grants no capability, mode, production approval, betting
  approval, or Kelly eligibility by default.
- Duplicate provider registration fails.
- Unknown provider lookup fails clearly.
- Provider, sport, capability, and mode must all be explicitly supported.
- A provider operation is rejected when the Phase 1A sport/plugin registration
  does not approve the corresponding mode or sport-level capability.
- Historical provider operations additionally require the sport plugin's
  explicit historical-training capability.
- Production use requires both `production_safe=True` and an explicit
  production-use declaration.
- Sample, mock, manual, and historical sources cannot expose production mode.
- MLB provider registrations cannot expose production mode.
- Live providers use the fail-closed credential policy.
- A live provider with a missing required credential cannot run.
- Sample providers cannot require credentials.
- Placeholder providers cannot advertise modes or capabilities.
- `require_provider_capability()` raises a clear fail-closed error.
- `provider_can_run()` returns `False` for an unknown provider, invalid or
  unsupported operation, sport/plugin rejection, or missing credential.

The credential policy type reserves an explicit sample-fallback description,
but no live provider is allowed to use it. Existing provider-specific fallback
behavior was not changed or migrated.

## Interaction With Sport/Plugin Approval

Provider capability is subordinate to the Phase 1A sport registry. A provider
cannot make an unapproved sport executable, add a sport mode, or add a required
sport-level capability. MLB therefore remains research/sample only even when a
provider advertises odds or player-prop data.

An odds capability means only that an existing adapter can supply odds data.
It does not make a normalized Phase 1B quote betting-approved. The Phase 1B
quote defaults (`eligible_for_betting=False`, `kelly_eligible=False`, and
`approval_status="not_approved"`) remain unchanged.

## Scope Confirmation

- NBA runtime behavior was not migrated to or made dependent on this registry.
- No NBA runtime implementation file was changed.
- No live provider or live API call was added.
- No provider fetching, authentication, priority, or odds-normalization logic
  was changed.
- No MLB runtime, HR scoring, grading, feedback, ROI, bankroll, Kelly, gate, or
  threshold logic was changed.
- Phase 0 MLB research-only metadata and staking isolation remain unchanged.
- Phase 1A sport/plugin behavior remains unchanged.
- Phase 1B normalized odds behavior remains unchanged.
- Keyless MLB sample mode remains unchanged.
- No historical data acquisition or Phase 2 work began.

## Files Touched

- `courtvision/core/provider_registry.py`
- `courtvision/core/__init__.py`
- `tests/test_provider_registry.py`
- `docs/COURTVISION_PHASE1C_PROVIDER_CAPABILITY_REGISTRY_2026_06_19.md`

## Commands Run and Exact Results

Initial syntax and focused Phase 1A/1B/1C validation:

```powershell
py -3.13 -m py_compile courtvision/core/provider_registry.py courtvision/core/__init__.py
py -3.13 -m pytest tests/test_provider_registry.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py -q --basetemp=.pytest_tmp_phase1c_targeted
```

Result: `40 passed in 0.43s`.

Adjacent provider-contract, MLB safety, and NBA compatibility validation:

```powershell
py -3.13 -m py_compile courtvision/core/provider_registry.py courtvision/core/__init__.py
py -3.13 -m pytest tests/test_provider_registry.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py tests/test_mlb_hr_adapters.py tests/test_mlb_hr_odds_provider.py tests/test_mlb_hr_prop_engine.py tests/test_mlb_research_safety.py tests/test_nba_backwards_compatibility.py -q --basetemp=.pytest_tmp_phase1c_adjacent
```

Result: `66 passed in 4.43s`.

Required keyless MLB sample command:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code `0`. The existing research-only sample report rendered. The
adjacent safety test confirmed that it contained no forbidden presentation
terms.

Required full validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

The first invocation was interrupted after approximately 4.6 seconds because
the command wrapper was mistakenly given a one-second timeout; no pytest
failure was reported. The identical required command was rerun with a proper
wrapper allowance and completed successfully.

Exact full-suite result:

```text
2809 passed, 31 xfailed in 252.94s (0:04:12)
```

## Next Recommended Step

Review and land Phase 1C as a standalone metadata contract. The next phase
should first agree on provider health/availability reporting and adapter-to-
registry identity mapping. Any runtime adoption, new provider, live data
acquisition, provider-priority change, or betting approval should remain a
separately approved change with explicit compatibility and safety validation.
