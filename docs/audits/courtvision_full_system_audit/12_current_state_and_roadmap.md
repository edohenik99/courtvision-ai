# Current State And Roadmap

## Maturity Assessment

| Capability | Score 0-5 | Evidence | Main limitation | Requirement to reach next level |
| --- | ---: | --- | --- | --- |
| Repository organization | 3 | Structured package plus many scripts/docs/tests. | Overlapping active/legacy paths. | Entrypoint registry and deprecation cleanup. |
| Data ingestion | 4 | BallDontLie and MLB HR collectors with recent logs. | Provider/quota monitoring not centralized. | Health checks and alerting. |
| Data quality | 3 | Validators and coverage checkers exist. | Manual/unresolved states remain. | Reconciliation queue and strict schemas. |
| Prediction capability | 3 | NBA active scoring; MLB HR research scoring. | Prospective validity not proven. | Frozen forward trials. |
| Pick generation | 3 | NBA elite board plus a universal explicit paper/research `OfficialPick` schema/service and immutable ledger now exist. | No runtime automatically promotes candidates; no controlled paper-pick workflow is active. | Add an audited paper-trial promotion call site. |
| Result collection | 3 | MLB StatsAPI filler and NBA grading scripts. | Hybrid/manual gaps. | Fully auditable settlement. |
| Grading | 3 | NBA and MLB graders. | Observation vs pick ambiguity. | Separate performance ledgers. |
| Backtesting | 3 | MLB and NBA validation/backtest scripts. | Not all artifacts verified in this audit. | Reproducible backtest registry. |
| Automation | 3 | PS wrappers and logs. | Local/mutable scheduler assumptions. | Pinned manifests, locks, alerts. |
| Monitoring | 2 | Logs/operator cards. | No central run health view. | Run-status dashboard/notifications. |
| Reproducibility | 2 | Manifests exist for some flows. | Local ignored data and mutable `main`. | Artifact storage and version pinning. |
| Documentation | 3 | Extensive docs. | Current/planned/stale docs mixed. | Documentation status index. |
| Testing | 3 | 293 tests, many targeted areas. | Current pass/fail not verified. | CI-gated canonical tests. |
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
and settlement-reference boundary are implemented. The phase is not complete:
no candidate pipeline auto-promotes (by design), no controlled paper trial is
active, settlement is not migrated to `pick_id`, and no forward-validation claim
is made.

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

1. Wire an audited, operator-controlled paper-promotion call site to selected
   candidates without changing model selection logic.
2. Implement append-only settlement events that require a committed `pick_id`.
3. Migrate official-pick reports to the strict official-only dataset boundary
   while retaining separately labeled observation analysis.
4. Add the MLB HR settlement reconciliation queue.
5. Execute a forward paper trial with frozen manifests.
6. Add automation health/status monitoring.
7. Keep live and bankroll promotion gated until evidence requirements are met.

