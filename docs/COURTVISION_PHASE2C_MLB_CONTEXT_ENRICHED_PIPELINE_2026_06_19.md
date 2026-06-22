# CourtVision Phase 2C: MLB Context-Enriched Research Pipeline

Date: 2026-06-19

## What Was Added

Phase 2C integrates the deterministic Phase 2B MLB HR sample contexts into the
Phase 2A sample pipeline in `courtvision/sports/mlb/hr_pipeline.py`.

The pipeline now:

- builds the sample context slate for the requested date
- deterministically matches one context to each sample candidate
- returns the aligned contexts on `candidate_contexts`
- reports context counts, research completeness, warnings, and explicit gaps
- enriches artifact rows through existing player/event IDs, warnings, and
  source references
- preserves the existing HR scoring and ranking path
- preserves opt-in-only artifact writing

Focused Phase 2C coverage was added to
`tests/test_mlb_hr_research_pipeline.py`. The sample CLI safety banner was also
cleaned in `courtvision/sports/mlb/hr_report.py` so the human-facing output
keeps its sample/research warnings without production or recommendation
presentation terms.

## Why This Is Still Sample/Offline Only

The pipeline still accepts only `provider="sample"`. It builds contexts with
the deterministic, keyless `build_sample_mlb_hr_contexts()` fixture and makes
no network request, credential lookup, provider fetch, or external API call.

No live source, provider adapter, historical dataset, training path, or
promotion path was added. Context research completeness is diagnostic only;
MLB production context completeness remains fixed to `False`.

## Context Matching Rules

Matching uses stable identity precedence:

1. game ID, when present
2. player ID, when present
3. normalized player name, team, and game date

The sample pipeline derives the same stable game IDs used by the Phase 2B
fixtures. A key match is rejected when an available player, team, or date
identity conflicts. Reordering the input context tuple does not change the
matches.

Unknown lineup and probable-pitcher states remain literal `unknown` values.
They are never converted to confirmed states. An unmatched or incomplete
context does not stop the sample pipeline, but it creates an explicit warning
and an `MLBHRMissingContextRow` containing the affected candidate and missing
field names.

## Pipeline Result Changes

`MLBHRResearchPipelineResult` now includes:

- `candidate_contexts`
- `context_count`
- `context_complete_count`
- `context_warning_count`
- `context_warnings`
- `missing_context_rows`
- `sample_context_used`
- fixed `production_context_complete=False`

The existing fixed safety values remain unchanged:

- `eligible_for_betting=False`
- `kelly_eligible=False`
- `approval_status="research_only_not_betting_approved"`

The normal sample fixture returns three matched contexts, three
research-complete contexts, nine candidate-qualified context warnings, and no
missing-context rows. Sample warnings are retained because research
completeness does not make sample data externally verified.

## Artifact Enrichment

The shared Phase 1D artifact schema was not changed. The pipeline enriches its
existing rows using already-supported fields:

- `player_id`
- `event_id`
- row `warnings`
- row `source_refs`

Context source references include:

- `context_source_type=sample`
- `context_match=matched|missing`
- `context_complete=true|false`
- `production_context_complete=false`
- `missing_context_fields=...`
- `lineup_status=...`
- `probable_pitcher_status=...`
- `weather_data_quality=...`
- `ballpark_data_quality=...`

No artifact is written unless the caller supplies `artifact_path`. The
existing overwrite guard remains active.

## Missing-Context Behavior

- A missing match is represented by `None` in the aligned
  `candidate_contexts` tuple.
- It reduces `context_count` and creates a gap row with
  `missing_required_fields=("context",)`.
- A matched but incomplete context remains attached, reduces
  `context_complete_count`, and retains the exact Phase 2B missing field names.
- Context warnings appear on the pipeline result and the corresponding
  artifact row.
- Missing weather and unknown lineup/pitcher states were exercised without
  stopping the sample run.
- Neither research completeness nor missing-context tolerance changes scoring.

## Safety and Default-Deny Behavior

The pipeline remains research/sample only. The result, normalized quotes, and
artifact all retain their default-deny eligibility state. The Phase 1D
artifact metadata and rows remain `approval_status="not_approved"`,
`eligible_for_betting=False`, and `kelly_eligible=False`.

The serialized artifact contains no stake, unit-sizing, expected-value, or
fair-probability fields. No scoring weight, scoring threshold, research label,
or selection gate changed.

## Commands Run and Exact Results

Syntax validation:

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/hr_pipeline.py
```

Result: exit code 0.

Focused Phase 2A, 2B, 2C, and artifact validation:

```powershell
py -3.13 -m pytest tests/test_mlb_hr_prop_engine.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_research_context.py tests/test_research_artifact_contract.py -q
```

Result: `44 passed in 0.53s`.

Phase 1A-1D, MLB safety, Phase 2A-2C, and NBA compatibility validation:

```powershell
py -3.13 -m pytest tests/test_mlb_hr_adapters.py tests/test_mlb_hr_odds_provider.py tests/test_mlb_hr_prop_engine.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_module.py tests/test_mlb_research_context.py tests/test_mlb_research_safety.py tests/test_normalized_odds_quote.py tests/test_provider_registry.py tests/test_research_artifact_contract.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py -q
```

Result: `104 passed in 2.57s`.

Required keyless sample CLI validation:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; all three sample rows rendered with the clean
sample/research-only banner.

Required full-suite validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2846 passed, 31 xfailed in 251.89s (0:04:11)`.

## Scope Confirmation

- No live provider was added or called.
- No external API was called.
- No provider fetching, authentication, source priority, capability, or odds
  normalization behavior changed.
- No historical acquisition or training work began.
- No MLB HR scoring formula, weight, threshold, label, or selection behavior
  changed.
- No production promotion path was added.
- No bankroll-facing, wager-sizing, or Kelly behavior changed.
- No NBA runtime internal changed.
- No Phase 1A registry behavior changed.
- No Phase 1B normalized odds contract behavior changed.
- No Phase 1C provider capability registry behavior changed.
- No Phase 1D research artifact schema or validation behavior changed.
- Keyless sample mode remains the only Phase 2C pipeline mode.

## Next Recommended Step

Define a separately approved Phase 2D offline ingestion boundary for supplied
MLB context fixtures, including duplicate-key and partial-slate diagnostics.
Keep it keyless and default-deny; defer live providers and historical training
until their own explicitly approved phases.
