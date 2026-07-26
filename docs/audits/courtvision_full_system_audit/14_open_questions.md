# Open Questions

These questions could not be conclusively answered from the repository without running live services, querying host scheduler state, or asking the project owner.

| Question | Why it matters | Evidence searched | Missing information | How to verify |
| --- | --- | --- | --- | --- |
| Are the documented Windows Scheduled Tasks currently installed and enabled on this machine? | Automation health depends on actual scheduler state, not just scripts. | PS wrappers, installer scripts, docs, logs. | Current Task Scheduler registry/state. | Run read-only `Get-ScheduledTask`/`schtasks /query` with owner permission. |
| Are all provider subscriptions active as of 2026-07-11? | Live runs depend on credentials/quota. | Existing logs and config names. | Current API responses/account status. | Run approved smoke checks that do not alter outputs; inspect provider dashboards. |
| Is BallDontLie fully available for future NBA odds dates? | NBA canonical path depends on it. | 2026-07-08 smoke log, code. | Current provider subscription and future market coverage. | Controlled provider smoke in a scratch/no-output mode. |
| Which exact docs are considered authoritative by the owner? | Many docs are plans or old audits. | `docs/` inventory. | Owner intent/current doc index. | Create docs status registry and confirm with owner. |
| Which generated artifacts should be preserved as canonical evidence? | Local ignored data is large and operationally important. | `outputs/`, `data/`, `courtvision-raw/`. | Retention and artifact-storage policy. | Owner decision plus artifact registry. |
| Have all current tests passed on commit `811a078`? | Static audit cannot prove current test health. | Test inventory and older doc claims. | Current test results. | Run targeted non-live tests, then full suite if approved. |
| Are dashboard scripts still expected to be user-facing? | UI maturity affects roadmap. | Streamlit/dashboard files. | Product intent and current UI expectations. | Launch dashboard in local read-only mode with approval and inspect. |
| Is any external Alana repo expected to consume CourtVision outputs? | This repo has no Alana code, but integration may live elsewhere. | `rg -i "alana"`, output surfaces. | External repo/service context. | Ask owner or inspect external Alana project. |
| Which bankroll thresholds are approved by a human decision maker? | Bankroll-facing logic should not be inferred from code alone. | Kelly scripts and docs. | Business/risk approval. | Owner/risk review and signed policy. |
| Are there hidden manual steps in the MLB HR workbook workflow? | Manual settlement can affect grading integrity. | workbook scripts/docs/logs. | Operator SOP and actual manual edits. | Review operator runbook and workbook change history. |
| Should old finalizer scripts be removed or kept as fallback? | Prevents accidental wrong entrypoint usage. | `run_live_hr_final_auto.ps1`, consolidated pipeline docs. | Owner migration decision. | Add deprecation note after confirmation. |
| Which model artifacts are intended as current production models? | `outputs/model` and baselines are not a formal registry. | model/calibration files and runtime logs. | Model ownership/versioning policy. | Create model registry with active flag and provenance. |

