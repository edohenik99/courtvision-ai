# CourtVision Phase 2D: MLB Provider Interface Contracts

Date: 2026-06-19

## What was added

Phase 2D adds typed, runtime-checkable provider protocols under
`courtvision/sports/mlb/providers/`. The protocols cover schedule, lineup,
probable-pitcher, hitter-feature, pitcher-feature, weather, ballpark, and
combined HR research context acquisition.

The existing keyless `SampleHRProvider` now implements the optional combined
context contract by returning the deterministic Phase 2B sample contexts. The
existing statistics, weather, and ballpark placeholders expose Phase 1C
metadata and implement the applicable Phase 2D methods with explicit `None`
results.

## Why this phase is interface-only

This phase establishes the boundary between future acquisition code and the
existing MLB HR research pipeline. It allows future providers to be evaluated
against stable context and metadata contracts before any external source,
credential path, or network behavior is introduced. The Phase 2C pipeline was
not rewired and its output and CLI behavior remain unchanged.

## Provider contracts

- `MLBScheduleProvider.get_games(date) -> list[MLBGameContext]`
- `MLBLineupProvider.get_lineups(date) -> list[MLBLineupContext]`
- `MLBLineupProvider.get_lineup_for_game(game_id) -> MLBLineupContext | None`
- `MLBProbablePitcherProvider.get_probable_pitchers(date) -> list[MLBProbablePitcherContext]`
- `MLBProbablePitcherProvider.get_probable_pitcher_for_game(game_id, team) -> MLBProbablePitcherContext | None`
- `MLBHitterFeatureProvider.get_hitter_features(player_id, as_of_date, window) -> MLBHitterFeatureContext | None`
- `MLBPitcherFeatureProvider.get_pitcher_features(pitcher_id, as_of_date, window) -> MLBPitcherFeatureContext | None`
- `MLBWeatherProvider.get_weather_for_game(game) -> MLBWeatherContext | None`
- `MLBBallparkProvider.get_ballpark_context(venue_name) -> MLBBallparkContext | None`
- `MLBResearchContextProvider.get_hr_research_contexts(date) -> list[MLBHRResearchContext]`

Every return model is owned by the Phase 2B research context contract.

## Metadata requirements

Every protocol inherits the shared `MLBProviderContract` metadata surface:

- `provider_name`
- `source_type`
- `supported_modes`
- `requires_credentials`
- `required_env_vars`
- `capabilities`
- `production_safe`
- `can_be_used_for_production`

Source types, modes, and capabilities use the Phase 1C enums directly. The
sample and placeholder implementations read their declarations from the Phase
1C registry, preventing duplicate metadata from drifting. MLB production flags
remain false.

## Missing-data behavior

Collection methods use explicit absence: collection operations return an empty
list when no rows are known, while single-context lookups return `None`. The
contracts do not permit fabricated live context. Unsupported capabilities
continue to fail closed through the Phase 1C `require_provider_capability`
guard.

## Sample and stub behavior

`SampleHRProvider` remains keyless and deterministic. Its combined provider
method wraps the existing `build_sample_mlb_hr_contexts` fixture and returns a
list of Phase 2B `MLBHRResearchContext` objects.

The existing statistics, weather, and ballpark placeholders make no network
calls. Their new context methods return `None`, their supported mode and
capability sets remain empty, and they cannot be used for production.

## Relationship to the Phase 1C registry

The interfaces consume the existing `ProviderMode`, `ProviderSourceType`, and
`ProviderCapability` types. Implementations expose metadata matching their
existing Phase 1C registrations. No provider registration, capability, gate,
credential rule, or source priority was changed in Phase 2D.

## Safety confirmation

No live provider or API call was added. No historical acquisition or training
was added. MLB HR scoring, selection behavior, production gates, bankroll and
Kelly behavior, and NBA runtime internals were not changed. MLB remains
research/sample only, and the human-facing sample report remains free of
production approval or wager-sizing language.

## Validation

Commands run:

```powershell
py -3.13 -m pytest tests/test_mlb_provider_contracts.py tests/test_mlb_hr_adapters.py tests/test_mlb_research_context.py tests/test_provider_registry.py -q --basetemp=.pytest_tmp_phase2d_targeted_2
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exact results:

- Targeted provider/context regression tests: `41 passed in 0.47s`.
- Keyless sample CLI: exit code `0`; clean research-only report; no stderr.
- Full suite: `2852 passed, 31 xfailed in 242.38s (0:04:02)`.

The first full-suite invocation was stopped by a 120-second command wrapper
timeout before pytest completed. The exact required command was rerun with a
longer wrapper timeout and produced the passing result above.

## Next recommended step

Phase 2E should add provider conformance fixtures and selection/orchestration
rules around these interfaces while retaining explicit offline inputs. Live
acquisition should remain a separate, explicitly approved phase with source-
specific credential, freshness, failure, and provenance review.
