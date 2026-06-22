# CourtVision Phase 3B: MLB Storage and Source Manifests

Date: 2026-06-19  
Status: Complete  
Scope: Local storage/schema scaffolding only

## What was added

Phase 3B adds `courtvision/sports/mlb/data_manifest.py`, an isolated contract
module containing:

- immutable `MLBSourceManifest`, `MLBSourceFileRecord`, `MLBDataPartition`,
  `MLBStorageLayout`, and `MLBManifestValidationResult` structures;
- closed enums for the approved MLB source types and data domains;
- a deterministic local storage layout planner;
- dry-run-by-default directory planning and opt-in directory creation;
- fail-closed manifest validation and deterministic JSON serialization;
- overwrite-protected manifest writing; and
- an exact-byte SHA-256 helper.

`tests/test_mlb_data_manifest.py` covers the layout, dry-run behavior, explicit
temporary-directory creation, immutability, required metadata, safety checks,
deterministic serialization, hashing, overwrite protection, and absence of raw
data side effects.

No real source manifest was added. The test manifest exists only in memory, and
all filesystem-writing tests use pytest temporary directories.

## Why this phase is storage/schema only

The module describes where future MLB data may be stored and what provenance a
future ingestion process must retain. It does not fetch, parse, normalize, join,
train on, score, or publish data. Calling `get_mlb_storage_layout`,
`manifest_path_for`, or the default `ensure_mlb_storage_dirs()` only returns
paths. No directory is created unless `dry_run=False` is explicitly supplied.

Phase 3B did not implement a provider, call an API, download a dataset, or create
a historical training pipeline.

## Proposed local folder layout

```text
data/
  raw/mlb/
    statcast/
    retrosheet/
    lahman/
    weather/
    odds/
    lineups/
    probable_pitchers/
    ballpark/
  normalized/mlb/
  research/mlb/hr/
  training/mlb/hr/
  manifests/mlb/
```

Raw inputs, normalized datasets, research artifacts, and training artifacts are
separate boundaries. This phase does not create these folders in the repository.

Manifest paths use this deterministic convention:

```text
data/manifests/mlb/<domain>-<source_name>-<season_or_date>.manifest.json
```

## Manifest schema

`MLBSourceManifest` records:

- source identity: `source_name`, `source_type`, `provider_name`,
  `source_version`;
- fixed identity: `sport="MLB"`, `league="MLB"`;
- domain and coverage: `data_domain`, `season`, `date_range_start`,
  `date_range_end`;
- temporal provenance: `collected_at`, `as_of_date`;
- storage provenance: `raw_path`, `normalized_path`;
- reproducibility: `schema_version`, `checksum`, `row_count`, `file_count`,
  `generated_by`;
- diagnostics: `notes`, `warnings`;
- optional immutable file records and partition records; and
- explicit default-deny safety state: `approval_status="not_approved"`, with
  the existing eligibility flags fixed to false.

Supported source types are `public`, `free`, `paid`, `manual`, `sample`, `mock`,
and `historical`. Supported domains are `statcast`, `retrosheet`, `lahman`,
`weather`, `odds`, `lineups`, `probable_pitchers`, `ballpark`, `research`, and
`training`.

## Validation rules

Validation fails closed when:

- `source_name`, `source_type`, `schema_version`, or `collected_at` is missing;
- a source type or data domain is unsupported;
- sport or league is not MLB;
- a date field has the wrong type or the start date follows the end date;
- a raw/historical manifest has no `raw_path`;
- a count is negative or malformed;
- a supplied checksum is not a 64-character SHA-256 digest;
- a nested file or partition record is malformed;
- the explicit safety state differs from its default-deny values; or
- notes or warnings claim production or wagering approval.

Invalid manifests cannot serialize or write. Validation occurs before the output
file is opened, so an invalid manifest cannot leave an empty artifact behind.
Manifest writing uses exclusive creation by default and overwrites only when
`overwrite=True` is explicitly supplied. It does not implicitly create parent
directories.

## Checksum strategy

`compute_file_sha256(path)` streams the exact file bytes in bounded chunks and
returns a lowercase 64-character SHA-256 digest. Future acquisition should hash
native raw bytes before parsing and store that digest in the file record and/or
manifest. A changed payload therefore produces a changed digest and must be
treated as a new source version rather than silently replacing provenance.

No external file was hashed during this phase; the checksum test uses one tiny
pytest temporary file.

## Dry-run behavior and no-raw-data commit policy

`ensure_mlb_storage_dirs(root=None, dry_run=True)` returns the complete planned
directory tuple and performs no writes. Passing `dry_run=False` is the only
directory-creation path. Tests exercise that path only below `tmp_path` and
confirm that it creates directories, not data files.

Raw datasets, normalized bulk data, generated research/training artifacts,
credentials, caches, logs, and runtime outputs must not be committed. A future
manifest is eligible for review only when it describes data that actually
exists, contains no secrets or signed URLs, and complies with source licensing.
Phase 3B commits no placeholder data and no manifest that implies acquisition
has occurred.

## Leakage-prevention metadata

Future ingestion must preserve `collected_at`, `as_of_date`, `source_version`,
`date_range_start`, `date_range_end`, `generated_by`, checksums, source type, and
warnings through normalized and derived artifacts. These fields are required to
prove that:

- current-game observations do not enter current-game features;
- no future-dated observation enters a historical row;
- odds quote and collection timestamps precede event start;
- rolling features exclude the current game;
- observed/reanalysis historical weather is never represented as a pregame
  forecast; and
- manual, sample, and mock records remain visibly labeled at every boundary.

Timestamp-valid feature cutoffs, quote timestamps, event start times, weather
issue/valid times, and row-level source references remain requirements for the
future ingestion schemas. This manifest scaffold records acquisition-level
provenance; it does not itself claim that a future dataset is leakage-free.

## Scope confirmation

This phase made no changes to:

- live providers, provider selection, API authentication, source priority, or
  odds normalization;
- external APIs or downloaded datasets;
- historical training or feature generation;
- MLB HR scoring formulas, weights, thresholds, or labels;
- bankroll, wager sizing, grading, feedback, result history, or ROI behavior;
- production approval or promotion gates;
- the keyless MLB sample provider or sample CLI behavior;
- NBA runtime internals or compatibility behavior;
- dashboards, UI assets, run scripts, batch files, PowerShell entrypoints, or
  scheduled workflows.

MLB remains research/sample only.

## Validation

Commands run:

```powershell
py -3.13 -m py_compile courtvision\sports\mlb\data_manifest.py tests\test_mlb_data_manifest.py
py -3.13 -m pytest tests\test_mlb_data_manifest.py -q --basetemp=.pytest_tmp_phase3b_targeted
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests/test_mlb_data_manifest.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py tests/test_research_artifact_contract.py tests/test_provider_registry.py tests/test_normalized_odds_quote.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py tests/test_mlb_hr_adapters.py tests/test_mlb_hr_prop_engine.py -q --basetemp=.pytest_tmp_phase3b_cross_phase
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
py -3.13 -m py_compile courtvision\sports\mlb\data_manifest.py tests\test_mlb_data_manifest.py
py -3.13 -m pytest tests\test_mlb_data_manifest.py -q --basetemp=.pytest_tmp_phase3b_final
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exact results:

- Compile check: exit code `0`.
- Initial focused Phase 3B tests: `22 passed in 0.25s`.
- Keyless MLB sample CLI: exit code `0`; the existing three-row sample watchlist
  rendered without credentials or external access.
- Phase 1A through Phase 2F, MLB adapter/engine, and NBA compatibility slice:
  `131 passed in 2.60s`.
- Initial full suite: `2885 passed, 31 xfailed in 241.04s (0:04:01)`.
- Final compile check after helper-signature compatibility review: exit code `0`.
- Final focused Phase 3B tests: `22 passed in 0.21s`.
- Final required full suite: `2885 passed, 31 xfailed in 241.37s (0:04:01)`.

No validation command invoked a live provider or downloaded external data.

## Next recommended step

After separate explicit approval and source-terms review, Phase 3C may implement
a small, fixed-date Statcast historical ingestion prototype. It should preserve
native bytes, acquisition timestamps, source versions, hashes, and an immutable
manifest; prove repeatable normalization and revision detection; and remain
disconnected from scoring, training, production selection, and bankroll-facing
runtime behavior.
