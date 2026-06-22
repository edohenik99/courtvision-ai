# CourtVision Phase 2B: MLB Research Context Contracts

Date: 2026-06-19

## What Was Added

Phase 2B adds `courtvision/sports/mlb/research_context.py`, a focused,
provider-neutral contract module for the context an MLB home-run research
pipeline may eventually consume. It defines frozen, slotted models for:

- `MLBGameContext`
- `MLBTeamContext`
- `MLBLineupContext`
- `MLBPlayerLineupStatus`
- `MLBProbablePitcherContext`
- `MLBHitterFeatureContext`
- `MLBPitcherFeatureContext`
- `MLBWeatherContext`
- `MLBBallparkContext`
- `MLBHRResearchContext`

Every model has `mode="research"` as a non-init field. The module also adds a
stable `to_dict()` serialization boundary, deterministic validation results,
warning aggregation, research and production completeness helpers, and the
keyless `build_sample_mlb_hr_contexts(date)` fixture builder.

Focused coverage is in `tests/test_mlb_research_context.py`.

## Why This Is Contracts-Only

The module describes data already supplied by a caller or by the existing
sample fixture. It does not select a provider, read credentials, make network
requests, call an API, train a model, score an HR candidate, or write an
artifact. No runtime pipeline integration was added in Phase 2B so the Phase
2A result contract and command-line output remain unchanged.

Research completeness means only that all required contract pieces are
present, internally valid, and identity-consistent. It does not grant any
production approval or eligibility. `context_is_complete_for_production()` is
hard-coded to return `False` for every MLB context.

## Context Schemas

### Game and Team

`MLBGameContext` requires game ID, game date, event start time, home team, away
team, venue, source type, collection time, and data quality. It carries fixed
MLB sport/league identity plus explicit warnings.

`MLBTeamContext` represents one game/team/opponent relationship, including
home/away identity, source type, collection time, data quality, and warnings.

### Lineup

`MLBLineupContext` requires game ID, team, an explicit `lineup_confirmed`
boolean, a tuple of player statuses, collection time, source type, and data
quality.

Each `MLBPlayerLineupStatus` contains player ID, player name, batting side,
batting-order position, optional fielding position, and one of:

- `confirmed`
- `projected`
- `unknown`
- `not_starting`

An unknown player is never treated as confirmed. A lineup cannot validate as
confirmed while it contains a projected or unknown listed player.

### Probable Pitcher

`MLBProbablePitcherContext` requires game ID, team, pitcher ID, pitcher name,
throwing side, collection time, source type, data quality, and one of:

- `confirmed`
- `probable`
- `projected`
- `unknown`

Only the literal `confirmed` state makes `is_confirmed` true. An unknown
pitcher makes a combined HR research context incomplete.

### Hitter and Pitcher Features

`MLBHitterFeatureContext` contains player identity, batting side, sample
window, recent HR rate, barrel rate, hard-hit rate, fly-ball rate, pull rate,
average and maximum exit velocity, source type, as-of date, and data quality.

`MLBPitcherFeatureContext` contains pitcher identity, throwing side, immutable
pitch mix, HR allowed rate, barrel allowed rate, hard-hit allowed rate,
fly-ball allowed rate, source type, as-of date, and data quality.

Feature numbers permit `None` at construction so missing provider-neutral data
can be represented explicitly. A required `None` value makes the combined
context incomplete.

### Weather and Ballpark

`MLBWeatherContext` requires game and venue identity, temperature, wind speed,
wind direction, source type, collection time, and data quality. Wind-out
field, humidity, and roof status are optional.

`MLBBallparkContext` requires venue, HR park factor, source type, data version,
and data quality. Handedness factors, altitude, and dimensions are optional.
Mappings are copied into immutable mapping proxies.

### Combined HR Research Context

`MLBHRResearchContext` combines:

- game context
- lineup context
- probable pitcher context
- hitter features
- pitcher features
- weather
- ballpark context
- explicit warnings
- derived `context_complete`
- derived `missing_required_fields`

The completeness fields are non-init and are recalculated whenever a context
is constructed or replaced. Game IDs, venue names, hitter lineup identity,
and pitcher feature identity must agree across components.

## Required and Optional Fields

The required fields are the non-optional identity, provenance, timing,
data-quality, status, and feature fields described above. A combined context
also requires all seven component objects.

The intentionally optional fields are:

- lineup player position
- weather wind-out field, humidity, and roof status
- ballpark handedness factors, altitude, and dimensions

Optional fields serialize as `null` when absent. Missing required component or
field names are retained in `missing_required_fields`; they are not inferred or
silently defaulted to confirmed data.

## Data-Quality and Missing-Data Rules

- Every source type is serialized visibly; sample, manual, and mock values are
  not relabeled as live data.
- Every sample fixture uses `source_type="sample"` and
  `data_quality="sample_data"`.
- Unknown lineup and pitcher states remain unknown.
- A missing required component, identity, date/time, feature value, source, or
  data-quality value fails closed for research completeness.
- `summarize_context_warnings()` retains explicit component warnings and adds
  one diagnostic for every missing or invalid required field.
- `validate_game_context()`, `validate_lineup_context()`,
  `validate_probable_pitcher_context()`, and
  `validate_hr_research_context()` return immutable validation results with
  `is_valid`, `errors`, and `raise_for_errors()`.
- The schemas contain no stake, unit-sizing, expected-value, or fair-probability
  fields.

## Sample Context Builder

`build_sample_mlb_hr_contexts(date)` reuses the three existing keyless sample
HR candidates and returns one combined context per candidate. It assigns
stable game, player, and pitcher IDs; a fixed noon-UTC collection timestamp;
sample-only provenance; and deterministic placeholder contact fields required
by the future enrichment boundary.

For the same date, repeated calls serialize identically. The builder performs
no provider construction, credential access, network I/O, scoring, or artifact
writing. Its contexts match the existing sample candidate names, teams,
pitchers, venues, event times, hitter inputs, pitcher mix, weather inputs, and
park factors. All three fixtures pass research completeness and fail
production completeness.

## Commands Run and Results

Syntax validation:

```powershell
py -3.13 -m py_compile courtvision\sports\mlb\research_context.py
```

Result: passed.

Focused Phase 2B validation:

```powershell
py -3.13 -m pytest tests/test_mlb_research_context.py -q
```

Result: `14 passed in 0.26s`.

Phase 2B plus Phase 1A-2A and NBA compatibility validation:

```powershell
py -3.13 -m pytest tests/test_mlb_research_context.py tests/test_mlb_hr_research_pipeline.py tests/test_research_artifact_contract.py tests/test_provider_registry.py tests/test_normalized_odds_quote.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py -q
```

Result: `73 passed in 2.19s`.

Required keyless sample CLI validation:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; the existing three-row report rendered unchanged and its
existing forbidden-presentation-term tests passed.

Required full-suite validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

The initial tool invocation was interrupted after a few seconds by an
undersized command timeout. The exact command was immediately rerun with a
long timeout.

Result: `2840 passed, 31 xfailed in 242.10s (0:04:02)`.

## Scope Confirmation

- No live provider was added or called.
- No external API was called.
- No provider authentication, capability, priority, or odds-normalization
  behavior changed.
- No historical acquisition or training work began.
- No MLB HR scoring formula, threshold, label, selection, or sample candidate
  changed.
- No production promotion path was added.
- No bankroll-facing or wager-sizing behavior changed.
- No NBA runtime internal changed.
- No Phase 1A registry behavior changed.
- No Phase 1B normalized odds contract behavior changed.
- No Phase 1C provider capability registry behavior changed.
- No Phase 1D artifact contract behavior changed.
- No Phase 2A pipeline behavior or result schema changed.
- Keyless sample mode and the existing MLB command-line output remain
  unchanged.

## Next Recommended Step

With explicit approval, add a Phase 2C sample-only context join boundary that
maps these contracts onto Phase 2A candidates for diagnostics without making
context mandatory, changing scoring, or enabling a live provider. Keep the
join default-deny and preserve the current clean command-line presentation.
