# CourtVision Day 0 Evidence Manifest Contract

## Purpose

The Day 0 manifest is the preregistration record for one frozen NBA forward paper-trial cohort. It binds a unique `trial_id` and date window to the exact committed code, resolved non-secret operator configuration, credential availability state, and evidence-handling policy that exist before the first official trial observation.

The manifest is evidence infrastructure only. Creating it does not fetch data, run predictions, select recommendations, alter thresholds, size wagers, grade results, or authorize live betting.

## Why preregistration comes first

Preregistration prevents the cohort definition from being adapted after recommendations, market movement, or results become visible. Without a timestamped Day 0 record, an outside reviewer cannot establish that dates, markets, provider rules, configuration, line cutoffs, or settlement treatment were chosen prospectively rather than selected to improve the apparent result.

The manifest must therefore be created before the first official trial run and retained with the published evidence. A trial day observed before preregistration is not part of the primary prospective cohort.

## What is frozen

Before the trial starts, freeze and disclose:

- the immutable `trial_id`, inclusive start date, inclusive end date, and trial timezone;
- the full committed code identity and branch;
- a clean working-tree status, subject only to the explicitly approved untracked investor-audit exception;
- every behavior-affecting canonical NBA operator setting represented in `config_object`;
- credential availability only as `configured` or `missing`;
- eligible market mode and allowed markets;
- bankroll and daily-exposure assumptions used for paper sizing;
- provider endpoint URLs, vendor eligibility, and operational retry/timeout settings;
- legacy-pipeline and recalibration states;
- the source-book rule, prediction-line capture cutoff, closing-line source and cutoff, timezone, and settlement policy.

The default policy declarations are:

| Field | Default |
|---|---|
| `source_book_rule` | `best_available_canonical_operator_price` |
| `line_capture_cutoff` | `pregame_operator_run` |
| `closing_line_source` | `same_provider_when_available` |
| `closing_line_cutoff` | `market_close` |
| `timezone` | `America/Toronto` |
| `settlement_policy` | `official_final_result_flat_1u` |

## Reproducibility identifiers

### `code_sha`

`code_sha` is the complete output of `git rev-parse HEAD` captured at preregistration. The branch is separately captured with `git branch --show-current`. The manifest also retains the exact `git status --short --untracked-files=all` output. Enumerating untracked files prevents an untracked directory from hiding extra files. Ignored files do not make the tree dirty. No tracked modification, staged change, or untracked file is permitted, except `docs/CODEX_INVESTOR_AUDIT_2026_07_07.md` when the operator explicitly uses `--allow-untracked-investor-audit`.

The exception acknowledges that known document only. It does not authorize another untracked file, a modified copy of a tracked file, or uncommitted runtime code.

### `config_hash`

`config_hash` is the lowercase SHA-256 digest of `config_object` serialized as UTF-8 canonical JSON using sorted object keys and compact separators (`separators=(",", ":")`). Whitespace and the pretty-printed outer manifest do not affect the digest. `config_object` is stored in full beside the digest so an independent reviewer can reproduce it.

The object contains resolved effective values rather than relying on implicit defaults. Numeric settings are JSON numbers, boolean settings are JSON booleans, vendor and market collections are JSON arrays, and policy declarations are strings. Vendor order is normalized because the canonical runtime consumes the configured vendors as a set; allowed-market order is retained.

## Secret handling

Secret values must never appear in the manifest, `config_object`, `config_hash` input, console output, filenames, or error messages. In particular, the value of `BALLDONTLIE_API_KEY` must be represented only as `configured` or `missing`.

Other credentials, tokens, chat destinations, local `.env` contents, authorization headers, secret fingerprints, masked fragments, and key lengths are outside this artifact and must not be added. A masked secret is still secret-derived data and is not acceptable here.

## Restart rule

The primary trial must stop and restart at Day 1 under a new preregistration when any decision-affecting or evidence-interpretation input changes, including:

- committed prediction, scoring, threshold, selection, Kelly, grading, provider, or odds-normalization code;
- a value in `config_object` or its resulting `config_hash`;
- model parameters or behavior-bearing external inputs not represented by the frozen code/config identity;
- eligible markets, source-book/provider policy, capture cutoff, closing-source policy, timezone, or settlement policy;
- the trial date range or cohort definition;
- an emergency fix whose output equivalence cannot be demonstrated.

An interrupted segment may be retained and disclosed as a separate incomplete cohort. It must not be pooled silently with the restarted trial. Administrative evidence tooling that cannot affect recommendations still requires documented review; it does not authorize a decision-policy change.

## Relationship to the daily manifest and ledger

The three records form one custody chain:

1. `data/history/evidence/day0/day0_manifest_<trial_id>.json` preregisters the cohort and supplies its frozen `code_sha` and `config_hash`.
2. `data/history/evidence_daily_manifest.csv` records one original disposition for every calendar day in the preregistered window, including no-slate, no-pick, provider-failure, and failed-run days.
3. `data/history/evidence_ledger.csv` records each released recommendation, its prediction-time evidence, closing observation, and eventual settlement.

Every daily-manifest and ledger row for a cohort must use the Day 0 `trial_id`, `code_sha`, and `config_hash`. Daily released counts must reconcile to original ledger recommendation rows. The Day 0 file does not replace either CSV, and neither CSV may retrospectively redefine the Day 0 cohort.

## Creation and custody

Use `python scripts/create_evidence_day0_manifest.py --trial-id <id> --start-date YYYY-MM-DD --end-date YYYY-MM-DD` before the first trial run. The default destination is append-only in practice: an existing filename is rejected unless `--force` is deliberately supplied. `--force` is for correcting a pre-observation creation mistake, not rewriting preregistration after evidence is known. Preserve and disclose any superseded copy when evidence collection has begun.

## Investor-facing interpretation

- Treat the manifest as proof of preregistered identity and policy, not proof of profitability or live execution.
- Verify the hash independently from the stored `config_object`, then reconcile `trial_id`, `code_sha`, and `config_hash` across every daily and recommendation record.
- Report every calendar day in the frozen window and every released recommendation; do not remove no-slate, no-pick, failed, void, missing-close, or unfavorable observations.
- Disclose the allowed untracked-audit exception, detached/empty branch identity, restarts, interruptions, corrections, missing data, manual intervention, and policy deviations.
- Keep cohorts with different identifiers or hashes separate. Any combined view must name its components and must not be presented as one uninterrupted preregistered trial.
- Interpret paper stakes and flat-1u settlement as analytical conventions. They do not establish line availability, wager acceptance, capacity, suitability, or future return.
- Do not annualize a short sample, imply significance without appropriate uncertainty analysis, or present a positive result as automatic authorization to deploy bankroll.
