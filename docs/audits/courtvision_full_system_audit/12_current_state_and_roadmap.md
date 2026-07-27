# Current State And Roadmap

## Maturity Assessment

| Capability | Score 0-5 | Evidence | Main limitation | Requirement to reach next level |
| --- | ---: | --- | --- | --- |
| Repository organization | 3 | Structured package plus many scripts/docs/tests. | Overlapping active/legacy paths. | Entrypoint registry and deprecation cleanup. |
| Data ingestion | 4 | BallDontLie and MLB HR collectors with recent logs. | Provider/quota monitoring not centralized. | Health checks and alerting. |
| Data quality | 3 | Validators, coverage checkers, and a strict unresolved MLB official-pick queue model exist. | Queue persistence/automation and legacy manual states remain. | Operated reconciliation queue with SLA. |
| Prediction capability | 3 | NBA active scoring; MLB HR research scoring. | Prospective validity not proven. | Frozen forward trials. |
| Pick generation | 3 | NBA elite board plus immutable operator review, active v2-only `OfficialPick`, isolated legacy-v1 DTO, writer-locked batch authorization, one-review/one-pick enforcement, recomputed promotion identity, exact provenance, lexically writer-owned single-commit transactions, authorization mappings with no writable instance storage, and explicit paper/research publication now exist. | `operator_id` is asserted rather than authenticated; operational interfaces and automatic promotion remain disabled; no prospective paper trial is active. | Add authenticated operator identity before building an interface, then run a controlled prospective paper trial. |
| Result collection | 3 | MLB StatsAPI filler, NBA grading scripts, and append-only official-pick settlement contracts/service. | Existing collectors/graders are not migrated; hybrid/manual gaps remain. | Audited paper/research integration through committed `pick_id`. |
| Grading | 3 | NBA/MLB graders plus an official-pick-only settlement dataset. | Legacy observation grading remains separate and no automated official-pick workflow is active. | Migrate selected paper reports without mixing observations. |
| Backtesting | 3 | MLB and NBA validation/backtest scripts. | Not all artifacts verified in this audit. | Reproducible backtest registry. |
| Automation | 3 | PS wrappers and logs. | Local/mutable scheduler assumptions. | Pinned manifests, locks, alerts. |
| Monitoring | 2 | Logs/operator cards. | No central run health view. | Run-status dashboard/notifications. |
| Reproducibility | 2 | Manifests exist for some flows. | Local ignored data and mutable `main`. | Artifact storage and version pinning. |
| Documentation | 3 | Extensive docs. | Current/planned/stale docs mixed. | Documentation status index. |
| Testing | 3 | Targeted lifecycle, identity, settlement, prediction, and architecture suites exist. | Local passes do not establish production or live readiness; CI remains the release gate. | CI-gated canonical, root-coverage, stable, type, compile, and schema checks. |
| Security | 3 | `.env` pattern and no secret values exposed in audit. | `.env.example` confusion; local secrets. | Config hardening and secret scanning. |
| Deployment readiness | 2 | Windows scripts work locally. | Path/interpreter/scheduler assumptions. | Deployment runbook and environment abstraction. |
| Scalability | 2 | Multiple scripts can grow. | No service boundary or central store. | Orchestrator + normalized data model. |
| Multi-sport readiness | 2 | Sport packages/scaffolding. | Only NBA production-facing; MLB research. | Promotion gates per sport. |

## Evidence-Backed Completed Work

| Completed area | Evidence |
| --- | --- |
| Canonical NBA runtime decision | `docs/ADR_001_CANONICAL_RUNTIME.md` |
| NBA daily no-bet safe run | 2026-07-08 runtime logs/operator card |
| NBA elite/Kelly historical output | 2026-05-10/2026-05-13 runtime artifacts |
| MLB HR live odds collector | collector code and run logs |
| Consolidated MLB nightly finalizer | commit `811a078`, nightly summary 2026-07-11 |
| Evidence automation scripts | recent commits and `tools/run_courtvision_evidence_*.ps1` |
| MLB HR historical research contracts | `docs/COURTVISION_MLB_HR_*` |
| Official pick identity and settlement foundations | `courtvision/official_picks/`, `docs/architecture/official_pick_lifecycle.md`, `docs/architecture/official_pick_settlement_lifecycle.md` |

## Current Validation Phase

The repository appears to be moving from "build collectors and pipeline plumbing" toward "forward evidence and controlled validation." Evidence: recent evidence runbooks/scripts, MLB nightly consolidation, and docs emphasizing live vs shadow separation.

## Phased Roadmap

### Phase A: Stabilize Current Pipelines

Objective: make current NBA and MLB HR workflows reproducible and unambiguous.

| Required work | Dependencies | Exit criteria | Evidence of completion | Risks |
| --- | --- | --- | --- | --- |
| Entrypoint registry, docs status index, deprecation banners | Current script inventory | Operators know one command per workflow | Updated docs and script warnings | Running legacy scripts |
| Pin commit/env/model/data in every run manifest | Existing manifest writers | Every run can be reproduced | manifest with commit/hash/env summary | Local ignored data gaps |
| Add locks to scheduled collectors/finalizers | Automation wrappers | Duplicate runs prevented | lock tests/logs | Dead locks if not handled |

### Phase B: Complete Result And Grading Loop

Objective: settle every official pick or observation into an explicit final state.

Exit criteria: no blank result states for completed dates; unresolved rows have manual queue IDs; strict result schema enforced.

### Phase C: Establish Predictive Validity

Objective: prove models/rules have prospective value before live recommendations.

Exit criteria: frozen forward trial with predeclared metrics, no post-hoc selection, settled results, and evidence ledger coverage.

### Phase D: Generate Controlled Paper Picks

Objective: turn model candidates into official paper picks without bankroll exposure.

Exit criteria: immutable `pick_id`, official pick table, pick-vs-observation separation, paper reports.

Foundation status (2026-07-26): the immutable contract, explicit promotion
service, append-only lifecycle ledger, non-pick record kinds, report filtering,
append-only settlement/correction service, official-pick-only settlement
dataset, and strict MLB unresolved queue model are implemented. The phase is not
complete: no candidate pipeline auto-promotes, no grading pipeline auto-settles,
no controlled paper trial is active, and no forward-validation claim is made.

### Phase E: Automate Monitoring And Reporting

Objective: make run health visible without reading local logs manually.

Exit criteria: dashboard/status file for last run, missed runs, quota, unresolved rows, and grading status.

### Phase F: Introduce Bankroll Logic

Objective: allow Kelly only after evidence gates.

Exit criteria: evidence thresholds met, risk committee/owner approval, kill switch, daily exposure reporting.

### Phase G: Expand Sports And Markets

Objective: promote MLB HR and other sports through the same gates as NBA.

Exit criteria: provider contracts, result settlement, official pick identity, tests, forward evidence for each sport/market.

### Phase H: Integrate With Alana Or User-Facing Application

Objective: expose safe, stable outputs to an assistant/app.

Exit criteria: read-only API/JSON surface for run status, official picks, settlement, and reports; no direct access to raw secrets or mutable scripts.

## What Should Be Built Next

1. Review and approve the official-pick settlement contract and transition
   policy before wiring any runtime consumer.
2. Wire an audited, operator-controlled paper-promotion call site to selected
   candidates without changing model selection logic.
3. Persist and operate the MLB official-pick reconciliation queue with an
   unresolved-row SLA; do not migrate sportsbook observations.
4. Migrate one paper/research settlement consumer and report to the strict
   `pick_id` boundary while retaining separately labeled observation analysis.
5. Execute a forward paper trial with frozen manifests.
6. Add automation health/status monitoring.
7. Keep live and bankroll promotion gated until evidence requirements are met.

