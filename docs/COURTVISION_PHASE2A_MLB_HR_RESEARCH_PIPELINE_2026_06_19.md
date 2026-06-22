# CourtVision Phase 2A: MLB HR Research Pipeline

Date: 2026-06-19

## What Was Added

Phase 2A adds a callable, sample-only MLB home-run research pipeline in
`courtvision/sports/mlb/hr_pipeline.py`.

The module provides:

- `run_mlb_hr_research_pipeline`
- immutable `MLBHRResearchPipelineResult`
- composition of the existing keyless sample provider and HR scoring engine
- sample quote mapping into the existing `NormalizedOddsQuote` contract
- conversion through the existing `ResearchArtifact` mapping
- opt-in JSON artifact writing with parent-directory creation
- existing-artifact overwrite protection
- explicit research/sample warnings and default-deny safety fields

Focused coverage was added in `tests/test_mlb_hr_research_pipeline.py`.

The existing MLB HR command-line interface was deliberately left unchanged.
Artifact writing is available only through the new callable pipeline and is
never performed by default.

## Why This Remains Sample/Research-Only

The pipeline accepts only `provider="sample"`. Any other provider value is
rejected before provider construction or provider I/O. The sample path remains
keyless and deterministic.

The result remains fixed to:

- `sport="MLB"`
- `league="MLB"`
- `mode="research"`
- `provider_mode="sample"`
- `eligible_for_betting=False`
- `kelly_eligible=False`
- `approval_status="research_only_not_betting_approved"`

The shared Phase 1D artifact contract retains its existing, stricter
serialization value `approval_status="not_approved"` at both metadata and row
level. No promotion path was added.

## Pipeline Flow

1. Validate the requested date and sample provider mode.
2. Resolve the existing keyless `SampleHRProvider` through the existing
   adapter factory.
3. Load the deterministic sample candidates.
4. Score and rank candidates with the existing `HRPropEngine` without changing
   any score, threshold, label, or selection behavior.
5. Map each sample price reference into the Phase 1B `NormalizedOddsQuote`
   contract with `mode="sample"`, `source_type="sample"`, `is_live=False`, and
   default-deny approval fields.
6. Convert the scored assessments through the existing Phase 1D MLB HR
   artifact mapping using sample provenance.
7. Optionally write the validated artifact to the caller-provided path.
8. Return the immutable structured pipeline result.

All three scored sample candidates remain in the artifact with their existing
research status. `watchlist_count` reports only rows carrying the existing
`Research Watchlist` label.

## Result Schema

`MLBHRResearchPipelineResult` contains:

- `sport`
- `league`
- `date`
- `mode`
- `provider_name`
- `provider_mode`
- `candidate_count`
- `watchlist_count`
- `artifact`
- `normalized_odds_quotes`
- `warnings`
- `generated_at`
- optional `artifact_path`
- `eligible_for_betting`
- `kelly_eligible`
- `approval_status`

The dataclass is frozen and uses slots. Safety values are non-init fields, so
callers cannot construct a result with alternative approval or eligibility
values.

## Artifact Behavior

No file is written unless `artifact_path` is explicitly provided. When a path
is provided, the pipeline:

- validates the artifact through the existing Phase 1D writer
- refuses to overwrite an existing path
- creates missing parent directories
- writes deterministic, sorted-key UTF-8 JSON
- returns the written path on the result

The artifact uses `mode="sample"`, sample provider/source provenance, and the
Phase 1D default-deny approval fields. Its fixed schema contains no stake,
unit-sizing, expected-value, or fair-probability fields.

## Warnings and Data Quality

Every pipeline result surfaces these neutral warnings:

- sample data only
- unvalidated research-only model
- no historical validation
- no production approval
- provider mode is sample
- missing live enrichment
- no external schedule, lineup, or pitcher join

Artifact rows retain the existing sample data-quality label and existing MLB
research-only safety warning.

## Commands Run and Results

Syntax validation:

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/hr_pipeline.py tests/test_mlb_hr_research_pipeline.py
```

Result: passed.

Focused Phase 2A and Phase 1A-1D/NBA compatibility validation:

```powershell
py -3.13 -m pytest tests/test_mlb_hr_research_pipeline.py tests/test_research_artifact_contract.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py tests/test_provider_registry.py tests/test_nba_backwards_compatibility.py -q
```

Result: `59 passed in 2.15s`.

Required keyless sample CLI validation:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; the existing three-row sample report rendered unchanged.

Required full-suite validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2826 passed, 31 xfailed in 251.42s (0:04:11)`.

## Scope Confirmation

- No live provider was added or called.
- No external API was called.
- No historical data or training work began.
- No scoring formula, scoring threshold, label threshold, or selection gate was
  changed.
- No production approval path was added.
- No bankroll, wager-sizing, or Kelly behavior was changed.
- No NBA runtime internal was changed.
- No Phase 1A registry behavior was changed.
- No Phase 1B normalized odds contract behavior was changed.
- No Phase 1C provider capability registry behavior was changed.
- No Phase 1D research artifact contract behavior was changed.
- Keyless MLB sample behavior remains the default.
- The existing MLB human-facing CLI output remains unchanged.

## Next Recommended Step

Add a separately approved Phase 2B sample-data enrichment boundary that joins
explicit event, lineup, and probable-pitcher identities without enabling live
fetches or changing scoring. Keep that work behind the same research-only,
default-deny result and artifact contracts until historical validation is
designed and approved as a later phase.
