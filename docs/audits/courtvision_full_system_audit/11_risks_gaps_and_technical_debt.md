# Risks, Gaps, And Technical Debt

## Foundation Implementation Status (2026-07-26)

| Finding | Status | Implemented here | Still open |
| --- | --- | --- | --- |
| F-001 | Foundation implemented | Canonical frozen `OfficialPick` plus append-only `OfficialPickSettlement` and settlement-correction v1 contracts; UUID identities; deterministic transition idempotency; explicit promotion/settlement; transactional lifecycle publication; provenance; replay/conflict handling; strict read validation. | Existing settlement/grading consumers still need migration to the new service; no historical IDs or settlements were guessed or backfilled. |
| F-002 | Partially implemented | Market observations and model candidates have explicit non-pick record kinds; official-pick reports exclude them; settlement reporting reconstructs verified ledger state and joins only by `pick_id`; MLB/NBA runtimes do not auto-promote or auto-settle. | Existing MLB observation grader remains separately labeled observation analysis; no automated official-pick performance workflow or paper trial is active. |
| F-005 | Foundation implemented | A strict unresolved MLB official-pick reconciliation queue model requires a committed MLB `pick_id`, rejects final/non-pick rows, and freezes the reason vocabulary. | Queue persistence, result-provider integration, automation, operator SLA, and legacy workbook migration remain open. |

## Findings Register

| ID | Severity | Area | Finding | Impact | Recommendation |
| --- | --- | --- | --- | --- | --- |
| F-001 | Critical | Pick identity | Universal immutable official-pick identity and append-only settlement foundations now exist, but legacy settlement/grading consumers are not migrated. | Duplicate grading and ambiguous settlement remain possible outside the new boundary. | Migrate selected paper/research consumers to the explicit service without historical backfill. |
| F-002 | Critical | MLB HR | MLB HR market observations are explicitly non-picks in the new boundary, but legacy observation grading/reporting still exists. | Legacy outputs can remain misleading if presented without the required observation-performance label. | Route MLB reports through the shared record-kind/report boundary; do not promote historical observations. |
| F-003 | Critical | Evidence | Prospective evidence is incomplete for trustworthy live/bankroll claims. | Live-pick trust is not established. | Run controlled paper trials with frozen manifests and settlement. |
| F-004 | High | Bankroll | Kelly staking exists before sufficient forward evidence is proven. | Real-money recommendations could outpace validation. | Keep Kelly gated/disabled for unproven sports and require evidence gates. |
| F-005 | High | MLB results | A strict official-pick reconciliation model now exists, but the legacy HR settlement workflow remains hybrid and can leave blanks/void candidates/manual review. | Not every legacy row is ready to grade; silent gaps can skew summaries. | Persist and operate the official-pick queue, then add an unresolved-row SLA without converting observations to picks. |
| F-006 | High | Automation | Some wrappers pull mutable `main` before running. | A scheduled run can use unexpected code. | Pin commit/version per run and record manifest. |
| F-007 | High | Automation | Hard-coded Windows paths and interpreter assumptions exist. | Portability and deployment reliability are limited. | Centralize runtime config and remove machine-specific paths. |
| F-008 | High | API quota | MLB collector consumes paid API credits and lacks a process-level lock. | Duplicate scheduled runs or `--force` can waste credits. | Add lock file, dry-run defaults, and quota alerting. |
| F-009 | High | Feedback loop | NBA histories/evidence are sparse or stale relative to runtime outputs. | Performance reports may omit generated candidates/picks. | Reconcile histories and require evidence manifest coverage. |
| F-010 | High | Runtime complexity | `courtvision_ai.py` remains a large monolith with legacy branches. | Higher risk of unintended coupling/regression. | Extract stable provider/runtime interfaces gradually. |
| F-011 | Medium | Config | `.env.example` and active env usage diverge. | Operators can configure inactive providers or miss active variables. | Rewrite example/config docs around canonical runtime. |
| F-012 | Medium | Documentation | Many docs are plans/contracts, not current behavior. | Reviewers may overestimate implementation status. | Label docs as current, historical, plan, or superseded. |
| F-013 | Medium | Data | Large ignored local artifacts are operationally important. | Another machine may not reproduce results. | Use manifests, checksums, and artifact storage policy. |
| F-014 | Medium | Grading | HR grader can grade many book observations per event/player. | Observation ROI can be confused with bet ROI. | Add report labels and official-pick-only performance views. |
| F-015 | Medium | Results | Player matching relies on normalized names and ambiguity handling. | Trades, aliases, duplicate names, doubleheaders can cause wrong status. | Add MLBAM IDs wherever possible and exception review reports. |
| F-016 | Medium | Time zones | Collector uses UTC run dates and commence times; operations are local. | Date scoping can confuse pre/postgame workflows. | Normalize explicit UTC/local dates in manifests and reports. |
| F-017 | Medium | Scheduler observability | No central run dashboard/alerting found. | Failures can be missed. | Add run-status table, alerting, and retention policy. |
| F-018 | Medium | Safety switches | Past-date `--force` can override closed-slate protections. | Closed-slate outputs can be regenerated incorrectly. | Require explicit audit reason and manifest entry for forced reruns. |
| F-019 | Medium | Orphaned paths | Legacy/alternate provider and finalizer scripts remain. | Operators may run wrong script. | Add deprecation banners or remove after migration. |
| F-020 | Medium | Testing | Tests were not run in this audit and some coverage may be stale. | Current pass/fail unknown. | Run targeted non-live tests under controlled conditions. |
| F-021 | Low | Book coverage | Requested HR bookmakers are not always present in output. | Book comparisons may be incomplete. | Report book coverage per run/date. |
| F-022 | Low | Master dedupe | HR master keeps latest quote per key and can lose intraday quote history there. | Research on line movement needs snapshots, not master only. | Preserve separate normalized quote-history table. |
| F-023 | Low | Dashboard | UI scripts exist but were not verified. | User-facing readiness unknown. | Add dashboard smoke/render tests. |
| F-024 | Low | Logs | Logs/data can grow without retention. | Local disk bloat. | Add retention/archive policy. |
| F-025 | Informational | Alana | No Alana integration code exists. | Integration remains future work. | Expose stable JSON/API after official-pick schema. |
| F-026 | Informational | Other sports | WNBA/NFL/NHL scaffolding exists but is not production. | Multi-sport readiness can be overstated. | Keep research status labels in docs/UI. |
| F-027 | Informational | Telegram | Telegram delivery exists but is optional and not core. | Delivery should not affect evidence. | Keep disabled unless configured and audited. |
| F-028 | Informational | API-NBA | API-NBA is research-only. | It should not be treated as active odds path. | Preserve docs/guards. |
| F-029 | Informational | MLB historical packs | Raw data packs are large and useful for research. | Storage/versioning matters. | Add artifact registry if promoted. |
| F-030 | Informational | No-bet behavior | No-bet outputs are intentional and useful. | Empty elite is not automatically failure. | Keep no-bet summaries explicit. |

## Expanded Finding Details

| ID | Evidence | Blocks live picks? | Risks incorrect grading? | Risks data loss? | Risks API credits? | Complexity |
| --- | --- | --- | --- | --- | --- | --- |
| F-001 | NBA composite rows; MLB `(event_id, player)` result key | Yes | Yes | Medium | No | Medium |
| F-002 | `tools/theoddsapi_live_hr_collector.py`, `tools/grade_live_hr_results.py` | Yes for MLB | Yes | No | No | Medium |
| F-003 | sparse evidence ledger/history | Yes | Medium | No | No | Large |
| F-004 | `scripts/run_kelly_stakes.py`, Kelly outputs | Yes | No | No | No | Medium |
| F-005 | HR workbook status counts and coverage checker | Yes for automation | Yes | No | No | Medium |
| F-006 | PS wrappers with git pull | No | Medium | No | No | Small/medium |
| F-007 | hard-coded `C:\dev\Sport_Project1`, `py -3.13` fallback | No | No | No | No | Small |
| F-008 | collector force mode, no lock found | No | No | No | Yes | Small |
| F-009 | `pick_history.csv` through 2026-05-13 | Yes | Medium | No | No | Medium |
| F-010 | `courtvision_ai.py` size/scope | No | Medium | No | No | Large |

## Top Five Blockers To Trustworthy Live Picks

1. Prospective evidence is incomplete.
2. Official pick identity and append-only settlement are first-class for new
   explicit paper/research events, but legacy consumers are not migrated.
3. MLB HR still grades market observations; those rows remain non-picks and must
   be labeled observation performance.
4. Existing result collection remains partially manual/hybrid; the new
   reconciliation queue is not yet persisted or automated.
5. Automation/config/deployment is too local and mutable for strong auditability.

## Top Five Blockers To Full Automation

1. Unresolved HR result statuses and manual review workflow.
2. Lack of scheduler lock/alerting/status dashboard.
3. API quota/failure handling is not centralized.
4. Git pull/local interpreter assumptions in scheduled wrappers.
5. Incomplete source-of-truth/evidence manifest coverage.

