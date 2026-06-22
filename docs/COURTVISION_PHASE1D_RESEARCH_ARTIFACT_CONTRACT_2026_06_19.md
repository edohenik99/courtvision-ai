# CourtVision Phase 1D: Research Artifact Contract

Date: 2026-06-19

## What Was Added

Phase 1D adds an immutable, sport-agnostic research artifact contract in
`courtvision/core/research_artifact.py` and exports it through
`courtvision.core`.

The contract provides:

- `ResearchArtifactMetadata`
- `ResearchArtifactRow`
- `ResearchArtifact`
- `ResearchArtifactValidationResult`
- deterministic JSON serialization
- flat CSV row serialization
- validated JSON and CSV file writers
- one internal MLB HR assessment-to-artifact conversion helper

No artifact writer was connected to the MLB command-line runtime. The existing
human-facing sample report remains unchanged and no production output is
created.

## Why This Is Contract-Only

This phase defines a safe boundary for future research output persistence. It
does not promote an artifact, select or fetch from a provider, train a model,
change a score, approve a market, or route an artifact into production.

Artifact objects must pass the Phase 1D validator before `to_dict`, `to_json`,
`to_csv_rows`, or either file writer can emit data. This keeps malformed or
approval-conflicting objects inside the process and fails closed at the
serialization boundary.

## Artifact Metadata Schema

Each artifact records:

- `artifact_id`
- `sport`
- `league`
- `market_type`
- `mode`: `research`, `sample`, or `historical`
- `artifact_type`: `watchlist`, `report`, `dataset`, `diagnostic`, or `backtest`
- `run_date`
- `generated_at`
- `provider_names`
- `source_types`
- optional `code_version`
- optional `data_version`
- `schema_version`
- `approval_status`
- `eligible_for_betting`
- `kelly_eligible`

The initial schema version is `1.0`.

## Artifact Row Schema

Each row records:

- `row_id`
- `sport`
- `league`
- optional player, team, opponent, event, and event-date identity
- `market_type`
- optional `research_score`
- `status`
- `data_quality`
- `reasons`
- `warnings`
- `source_refs`
- `mode`
- `approval_status`
- `eligible_for_betting`
- `kelly_eligible`

JSON represents tuple fields as arrays. CSV represents the three multi-value
fields as JSON arrays inside their cells. The schema has no stake, unit, or
recommendation fields.

## Default-Deny Safety Behavior

Artifact and row defaults are:

- `approval_status="not_approved"`
- `eligible_for_betting=False`
- `kelly_eligible=False`

Phase 1D contains no promotion layer. Therefore research, sample, and
historical artifacts are all required to retain those values. Any conflicting
artifact or row is invalid and cannot serialize or write.

The structures are frozen dataclasses. Sequence fields are normalized to
tuples so callers cannot mutate provenance, reasons, warnings, source
references, or rows after construction.

## Validation Rules

Validation fails closed when:

- required artifact identity or schema metadata is blank
- the run date or generation timestamp has the wrong type
- provider names or source types are missing
- the mode is unsupported
- the artifact type is unsupported
- artifact approval or eligibility fields are not default-deny
- required row identity/status fields are blank
- row sport, league, market, or mode conflicts with artifact metadata
- row safety flags conflict with artifact safety flags
- a research score is non-numeric or non-finite
- an event date has the wrong type

Serialization and file writing call this validator and raise
`ResearchArtifactValidationError` on any error.

## MLB HR Mapping

`hr_assessments_to_research_artifact` converts existing `HRPropAssessment`
objects to a `watchlist` artifact with `batter_home_runs` rows. It carries over
the player, team, opponent, date, research score, label, data quality, reasons,
and source reference. It also carries the existing MLB research-only warning.

The mapped artifact remains `not_approved`, ineligible for betting, and
ineligible for Kelly. The helper does not change the assessment engine, report
rendering, provider behavior, or CLI arguments.

## Compatibility and Scope Confirmation

- NBA runtime outputs were not migrated or modified.
- Phase 0 MLB research-only safety rules remain in place.
- Phase 1A sport/plugin registry behavior was not changed.
- Phase 1B normalized odds behavior was not changed.
- Phase 1C provider capability behavior was not changed.
- Keyless MLB sample mode remains the default and still runs without live API
  calls.
- No providers, provider fetches, API authentication, historical training,
  recalibration, scoring, thresholds, selection gates, bankroll behavior, or
  Kelly behavior were added or changed.
- No Phase 2 MLB data acquisition work began.

## Commands Run and Results

Syntax/import check:

```powershell
py -3.13 -m py_compile courtvision/core/research_artifact.py courtvision/core/__init__.py courtvision/sports/mlb/hr_report.py
py -3.13 -c "from courtvision.core import ResearchArtifact; print(ResearchArtifact.__name__)"
```

Result: passed; the import printed `ResearchArtifact`.

Focused Phase 1D and compatibility validation:

```powershell
py -3.13 -m pytest tests/test_research_artifact_contract.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py tests/test_provider_registry.py tests/test_nba_backwards_compatibility.py -q
```

Initial result: `53 passed in 1.96s`. After tightening safety flags to require
literal `False` values, the same command was rerun with the final result:
`54 passed in 1.98s`.

Required keyless sample CLI validation:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; three sample research rows rendered with the existing
presentation and no forbidden presentation tokens.

Required full-suite validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

The first invocation was stopped by the command runner after its 120-second
execution limit, before pytest returned a result. The same command was rerun
with a longer runner window.

The full suite was run again after the final strict-boolean validation change.

Final result: `2821 passed, 31 xfailed in 241.46s (0:04:01)`.

## Next Recommended Step

Add a separately approved, opt-in research artifact writer entrypoint with
explicit path controls and overwrite protection. Keep it disconnected from
NBA production outputs and bankroll paths, and require the Phase 1D validator
before any file is created.
