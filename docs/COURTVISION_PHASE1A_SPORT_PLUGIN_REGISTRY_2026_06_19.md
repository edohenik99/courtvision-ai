# CourtVision Phase 1A: Typed Sport/Plugin Registry

Date: 2026-06-19  
Status: Complete; full suite green.

## What was added

Phase 1A replaces the original string-oriented sport configuration with an
immutable, typed, metadata-only sport/plugin registry. The registry declares
which sports, modes, markets, and capabilities exist without routing runtimes,
selecting providers, or making a registered sport production-capable.

The core contract now includes:

- `SportCode`: `NBA`, `MLB`, `WNBA`, `NFL`, and `NHL`.
- `SportMode`: `production`, `research`, and `sample`.
- `SportCapability`: `schedule`, `odds`, `projections`,
  `research_watchlist`, `historical_training`, `backtesting`,
  `betting_approval`, and `kelly_sizing`.
- `SportPlugin`: an immutable declaration containing an explicit plugin name,
  sport code, supported markets, supported modes, and capabilities.
- `SportRegistry`: duplicate-safe registration and case-insensitive lookup.
- Safe query helpers: `get_registered_sports()`, `get_plugin()`,
  `supports_capability()`, `supports_mode()`, `require_capability()`,
  `is_betting_approved()`, and `is_kelly_allowed()`.

The prior `SportConfig`, `get_sport()`, `sport_name`, and
`supported_prop_markets` names remain available as compatibility aliases. The
existing sport package imports therefore continue to resolve without routing
the canonical NBA runtime through the new registry.

## Files touched

- `courtvision/core/sport_registry.py`
- `courtvision/core/__init__.py`
- `tests/test_sport_registry.py`
- `docs/COURTVISION_PHASE1A_SPORT_PLUGIN_REGISTRY_2026_06_19.md`

No NBA runtime, MLB scoring, provider, bankroll/Kelly, dashboard, workflow, or
CLI implementation file was changed in Phase 1A.

## Registered sports and capabilities

| Sport | Plugin | Modes | Capabilities | Safety status |
| --- | --- | --- | --- | --- |
| NBA | `nba_legacy_runtime` | production, research | schedule, odds, projections, research_watchlist, historical_training, betting_approval, kelly_sizing | Only production-facing sport; existing runtime remains unchanged |
| MLB | `mlb_hr_research` | research, sample | odds, research_watchlist | Research/sample only; odds means the existing research adapter, not production odds/EV |
| WNBA | `wnba_reserved` | none | none | Reserved placeholder; not production-facing |
| NFL | `nfl_reserved` | none | none | Reserved placeholder; not production-facing |
| NHL | `nhl_reserved` | none | none | Reserved placeholder; not production-facing |

NBA does not advertise `backtesting` because no explicit backtest runtime was
identified. MLB does not advertise schedule, projections, historical training,
backtesting, betting approval, or Kelly sizing. The presence of existing
placeholder projection classes for reserved sports does not make those plugins
executable or approved.

## Safety rules

- Registration grants no modes or capabilities by default.
- Betting approval and Kelly sizing are explicit, production-only capabilities.
- Kelly sizing additionally requires explicit betting approval.
- A research-only plugin cannot declare production-only capabilities.
- A sample plugin cannot also expose production mode.
- Only NBA may declare production mode in the current contract.
- MLB has a hard construction-time prohibition against production mode and
  production-only capabilities.
- WNBA, NFL, and NHL are immutable reserved registrations with no modes or
  executable capabilities.
- `require_capability()` raises `CapabilityNotSupportedError` when a requested
  capability is absent; it never infers support from sport registration.
- Registry construction performs no plugin imports, provider calls, or runtime
  routing.

These rules supplement, and do not replace, the Phase 0 row-level MLB research
metadata and categorical staking guards.

## Tests added or updated

Registry coverage proves:

- NBA is registered for production and retains its existing declared behavior.
- MLB supports only research/sample modes and research-safe capabilities.
- MLB is neither betting-approved nor Kelly-allowed.
- WNBA, NFL, and NHL remain reserved and non-production.
- Registration alone does not imply approval or sizing access.
- Missing capabilities fail closed with a clear error.
- Duplicate sport registrations fail.
- Research/sample declarations cannot acquire production-only behavior.
- MLB and other non-NBA sports cannot be constructed with production mode.
- Pre-Phase-1A registry property names remain available.

Existing compatibility and safety tests also passed, including NBA import
identity, the keyless MLB sample CLI, immutable Phase 0 metadata, staking
isolation, and the forbidden-presentation-term checks.

## Commands run

Focused registry, compatibility, and safety tests:

```powershell
py -3.13 -m pytest tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py tests/test_mlb_hr_prop_engine.py tests/test_mlb_research_safety.py -q --basetemp=.pytest_tmp_phase1a_targeted
```

Result: `28 passed in 2.40s`.

Adjacent multi-sport and core tests:

```powershell
py -3.13 -m pytest tests/test_sport_registry.py tests/test_wnba_module.py tests/test_nfl_module.py tests/test_mlb_module.py tests/test_mlb_hr_adapters.py tests/test_mlb_hr_odds_provider.py tests/test_mlb_hr_prop_engine.py tests/test_mlb_research_safety.py tests/test_nba_backwards_compatibility.py tests/test_core_line_movement.py tests/test_confidence_engine.py tests/test_hit_rate_engine.py -q --basetemp=.pytest_tmp_phase1a_adjacent
```

Result: `57 passed in 2.51s`.

Required keyless MLB sample command:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code `0`. The output retained `Research-only`,
`No Actionable Recommendation`, and `Not Production Approved`; the existing
test confirmed that no forbidden presentation terms were present.

Required full suite:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exact result:

```text
2784 passed, 31 xfailed in 263.24s (0:04:23)
```

## Scope confirmation

Phase 1A did not add providers, provider selection, API authentication, or data
source priority changes. It did not build historical training or backtesting.
It did not refactor NBA runtime internals. It did not change MLB HR scoring,
bankroll/Kelly behavior, Phase 0 guards, Phase 0 research-only metadata, or CLI
presentation rules. Keyless MLB sample mode remains available and green.

No Phase 1B provider work began.

## Next recommended step

Review and land this Phase 1A registry as an isolated foundation change. After
explicit approval for the next phase, define a capability-based provider
contract that consumes these declarations without changing provider priority,
credentials, NBA runtime routing, or any sport's production approval.
