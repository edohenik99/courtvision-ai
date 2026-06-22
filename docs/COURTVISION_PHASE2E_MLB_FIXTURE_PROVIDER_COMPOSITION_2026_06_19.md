# CourtVision Phase 2E: MLB Fixture Provider Composition

Date: 2026-06-19

## What was added

Phase 2E adds `MLBFixtureContextProvider`, a deterministic local implementation
of all Phase 2D MLB context provider protocols. It supplies two games, four
hitters, two opposing pitchers, lineups, hitter and pitcher features, weather,
and ballpark context entirely from in-memory Python fixtures.

The phase also adds `MLBContextProviderBundle` and
`compose_hr_research_contexts`. Composition returns per-hitter
`MLBHRResearchContext` objects through the existing Phase 2B contract.

## Why this is fixture-only

This phase proves that provider-neutral schedule and feature data can be joined
without introducing external acquisition behavior. The provider reads no
files, requires no credentials, imports no network client, and makes no API
calls. Its data is small, fixed, and reproducible.

The fixture provider is intentionally not added to the global Phase 1C
provider registry. Its local metadata cannot add or override a registered
capability or sport approval.

## Fixture provider behavior

`MLBFixtureContextProvider` implements the schedule, lineup, probable-pitcher,
hitter-feature, pitcher-feature, weather, ballpark, and combined research
context protocols.

Its metadata is:

- provider name: `mlb_fixture`
- source type: `mock`
- modes: research and sample only
- required credentials: none
- production-safe: false
- production use: false

The fixture exposes two games on 2026-06-19. The first game has two complete
per-hitter contexts. The second has two deliberately incomplete contexts with
an unconfirmed lineup, an unknown probable-pitcher status, and missing weather.

## Composition rules

Composition uses only the keys defined in the Phase 2D contracts:

- schedule to lineup: `game_id`
- lineup team to opposing pitcher: scheduled opponent plus `game_id`
- hitter to hitter features: `player_id`
- probable pitcher to pitcher features: `pitcher_id`
- game to weather: `game_id`
- game to ballpark: `venue_name`

Games, lineups, and batting orders retain fixture order, so repeated
composition returns equal objects in the same order.

## Complete and incomplete behavior

The composer does not create a second completeness system. It constructs the
existing `MLBHRResearchContext`, whose Phase 2B validation derives
`context_complete` and `missing_required_fields`.

Complete rows contain every required component and valid identity joins.
Incomplete rows fail closed with explicit entries such as `weather`,
`lineup_status.hitter_status`, and
`probable_pitcher.probable_status`. Unknown statuses remain unknown and are
never converted to confirmed states.

Research completeness does not grant production completeness.
`context_is_complete_for_production` remains false for every MLB fixture row.

## Pipeline integration decision

The optional `run_mlb_hr_research_pipeline(..., provider="fixture")` path was
not added. The current Phase 2C pipeline obtains candidates from the existing
sample HR adapter, while Phase 2E provides context inputs only. Combining those
responsibilities in this phase would create an implicit mixed-provider path.

Sample remains the default and its CLI behavior is unchanged.

## Safety and default-deny behavior

The fixture provider is not globally registered and cannot override Phase 1C
capability checks. It has no production mode or production approval surface.
No artifact-writing path was added, so the existing artifact overwrite guard
is unchanged. MLB remains research/sample only.

No live provider, API call, historical acquisition, training path, MLB HR
scoring change, bankroll or Kelly change, production promotion, NBA runtime
refactor, provider source-priority change, odds normalization change, or
dashboard change was made.

## Validation

Commands run:

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/providers/fixture_provider.py; py -3.13 -m pytest tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py -q --basetemp=.pytest_tmp_phase2e_smoke
py -3.13 -m pytest tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_research_context.py tests/test_provider_registry.py tests/test_research_artifact_contract.py tests/test_normalized_odds_quote.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py -q --basetemp=.pytest_tmp_phase2e_targeted
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py -q --basetemp=.pytest_tmp_phase2e_final
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exact results:

- Provider/context smoke tests: `20 passed in 0.33s`.
- Phase 2E and cross-phase targeted tests: `92 passed in 2.38s`.
- Keyless sample CLI: exit code `0`; clean research-only output; no stderr.
- Final affected-scope tests: `27 passed in 0.37s`.
- Full suite on the final tree: `2859 passed, 31 xfailed in 241.10s (0:04:01)`.

## Next recommended step

Add an explicit fixture candidate contract and only then consider an opt-in
fixture pipeline route. That keeps context composition separate from candidate
generation and preserves the current sample default. Live acquisition should
remain a separately approved phase.
