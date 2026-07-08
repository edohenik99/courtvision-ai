# CourtVision Evidence Rehearsal Summary — 2026-07-07

## Purpose

The July 7, 2026 rehearsal exercised the evidence-custody tooling before an
official CourtVision NBA forward paper trial. It was an infrastructure rehearsal,
not Day 1 of an official cohort, and it did not establish model performance or
profitability.

## Completed checks

- The Day 0 manifest creator was tested and produced rehearsal manifests with
  captured Git identity, resolved non-secret configuration, and a computed
  configuration hash.
- The daily evidence-manifest appender was tested with rehearsal-only daily
  dispositions.
- The evidence-ledger row appender was tested with a synthetic recommendation.
- The run-to-evidence artifact exporter was tested, including missing-artifact
  disclosure behavior.
- The closing-line updater was tested against a rehearsal ledger row.
- The result updater was tested against a rehearsal ledger row.

The rehearsal demonstrated that the individual evidence steps can be exercised
without changing prediction, scoring, threshold, Kelly, provider, or runtime
selection behavior.

## Evidence status and known invalid row

All rows whose `trial_id` contains `nba-evidence-rehearsal-2026-07` are rehearsal
records. They are not official forward evidence and must be excluded from every
official calendar count, recommendation count, metric, chart, and investor-facing
result.

The `nba-evidence-rehearsal-2026-07-v4` daily-manifest row is specifically invalid
for identity verification: it used the literal placeholder
`YOUR_CONFIG_HASH_HERE` as `config_hash`. It must not be treated as a valid
configuration-bound row, repaired in place, or included in official evidence.
Its continued presence is a visible rehearsal artifact and a reminder that the
official workflow must load the hash from the official Day 0 JSON.

Other rehearsal rows may contain synthetic identities, artificial provider
values, updater test outcomes, or deliberately missing artifacts. Successful
tool execution does not convert any of those rows into prospective observations.

## Official-trial handoff

The official trial must:

1. use a new `trial_id` that has never appeared in a rehearsal row;
2. begin from a clean, frozen Git state;
3. create and archive a new Day 0 manifest before the first official run;
4. load `code_sha` and `config_hash` from that official manifest;
5. filter all appends, updates, reconciliation, and reporting by the exact new
   official identifier;
6. leave rehearsal rows separate and never rename, copy, or pool them into the
   official cohort.

See `docs/EVIDENCE_FORWARD_TRIAL_RUNBOOK.md` for the official operator procedure.
