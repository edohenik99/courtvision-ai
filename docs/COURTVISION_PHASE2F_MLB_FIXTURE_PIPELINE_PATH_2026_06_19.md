# CourtVision Phase 2F: MLB Fixture Pipeline Path

Date: 2026-06-19

## What was added

Phase 2F adds an explicit, opt-in `provider="fixture"` path to
`run_mlb_hr_research_pipeline`. The existing `provider="sample"` path remains
the default.

Fixture mode constructs `MLBFixtureContextProvider` for the requested date and
passes it through the Phase 2E `compose_hr_research_contexts` function. The
pipeline projects those deterministic context rows into the existing
`HRPropInput` contract, runs the unchanged MLB HR research engine, re-matches
each result to its composed context, and returns the existing structured
pipeline result and `ResearchArtifact` contracts.

The structured result now exposes the context provider name, context source
type, and explicit sample/fixture context-use flags.

## Why fixture mode is opt-in

Fixture data is local, synthetic, and intended only to exercise the offline
pipeline shape. It is not externally collected or validated and is not a
production data source. Requiring `provider="fixture"` prevents the fixture
slate from replacing the familiar keyless sample default.

The fixture provider remains local to the MLB research pipeline. It was not
added to or used to override the global provider registry. The human-facing
sample CLI path and output were not changed; no fixture CLI route was added.

## Pipeline flow

```text
MLBFixtureContextProvider
  -> compose_hr_research_contexts
  -> four deterministic MLBHRResearchContext rows
  -> local HRPropInput projection
  -> unchanged HRPropEngine
  -> deterministic candidate/context match
  -> context-enriched ResearchArtifact
  -> MLBHRResearchPipelineResult
```

The fixture contains four hitters on two games. Two contexts are complete for
research use. Two are deliberately incomplete because confirmation or weather
data is unavailable.

## Context matching behavior

The shared matching helper retains explicit identity precedence:

- `game_id`, when available and identity-compatible
- `player_id`, when available and identity-compatible
- normalized `player_name + team + game_date` fallback

The fixture projection carries the composed player, team, and game date into
the existing candidate contract, so the fallback is deterministic and remains
stable if context ordering changes.

A matched but incomplete context is never counted as complete. It produces an
`MLBHRMissingContextRow` with the exact `missing_required_fields` and warnings.
An unmatched candidate also remains non-fatal and produces an explicit
`context` gap. `production_context_complete` remains false for every result and
artifact row.

## Artifact behavior

When `artifact_path` is provided, fixture mode uses the existing overwrite
guard and `ResearchArtifact` JSON writer. The artifact remains schema-valid and
sample-mode/default-deny.

Artifact metadata records:

- provider: `mlb_fixture`
- source type: `mock`
- mode: `sample`
- approval status: `not_approved`
- eligibility flags: false

Each row carries fixture source and completeness references, including
`context_provider`, `context_source_type`, `context_count`,
`context_complete_count`, `context_incomplete_count`, row-level
`context_complete`, exact missing fields, and
`production_context_complete=false`. Context warnings are also preserved on
the row.

The serialized artifact contains no stake, unit, expected-value, or estimated
fair-probability fields.

## Safety and default-deny behavior

Fixture mode is keyless and performs no network or API calls. The fixture
provider continues to advertise research/sample modes only, no credentials,
and no production safety.

This phase made no changes to:

- live providers, API authentication, provider priority, or odds normalization
- historical acquisition or training
- MLB HR scoring formulas, weights, thresholds, or labels
- bankroll, Kelly, wager sizing, grading, feedback, result history, or ROI
- production approval or promotion gates
- NBA runtime internals or behavior
- dashboards, UI assets, scripts, batch files, or scheduled entrypoints

## Validation

Commands run:

```powershell
py -3.13 -m pytest tests/test_mlb_hr_research_pipeline.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py -q --basetemp=.pytest_tmp_phase2f_baseline
py -3.13 -m py_compile courtvision/sports/mlb/hr_pipeline.py
py -3.13 -m pytest tests/test_mlb_hr_research_pipeline.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py -q --basetemp=.pytest_tmp_phase2f_targeted1
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests/test_mlb_hr_research_pipeline.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py tests/test_research_artifact_contract.py tests/test_provider_registry.py tests/test_normalized_odds_quote.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py tests/test_mlb_hr_adapters.py tests/test_mlb_hr_prop_engine.py -q --basetemp=.pytest_tmp_phase2f_cross_phase
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exact final results:

- Pre-change focused baseline: `24 passed in 0.39s`.
- Final Phase 2F/provider focused tests: `28 passed in 0.46s`.
- Keyless sample CLI: exit code `0`; prior sample output preserved; no stderr.
- Cross-phase and NBA compatibility tests: `109 passed in 2.53s`.
- Full suite: `2863 passed, 31 xfailed in 250.41s (0:04:10)`.

One initial full-suite invocation was interrupted by the command harness timeout
before completion; the exact full-suite command was rerun with a sufficient
timeout and produced the passing result above.

## Next recommended step

Define a small provider-neutral fixture candidate contract so future offline
providers do not need a pipeline-local projection into `HRPropInput`. Keep live
acquisition and historical training as separate, explicitly approved phases.
