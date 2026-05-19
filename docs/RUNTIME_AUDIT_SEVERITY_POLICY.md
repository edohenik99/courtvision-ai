# Runtime Audit Severity Policy

This policy defines how CourtVision runtime audits and artifact manifests label issues. It is documentation and reporting only; it must not change prediction, scoring, selection, Kelly, grading, or history behavior.

## Severity Levels

| Severity | Meaning | Operator posture |
|---|---|---|
| `fatal` | A core operator artifact or invariant needed to trust a normal prediction run is missing or broken. | Treat the run as incomplete until inspected. Do not use stake-facing outputs from that run. |
| `warning` | A validation, diagnostic, reporting, or post-run artifact is missing or reports an issue, but the condition is not itself proof that model generation failed. | Investigate before relying on summaries/cards. Continue only if the core boards and safety gates are otherwise clean. |
| `informational` | Research, verbose/debug, or explanatory artifacts that improve visibility but are not required for stake-facing operation. | Useful for diagnosis. Missing artifacts in this class do not block operator use. |
| `shadow_only` | Shadow, simulation, watchlist, or forward-looking audit artifacts that are not live betting inputs and should not grant betting permission. | Review for learning and monitoring only. Do not use as direct stake instructions. |

## Artifact Manifest Policy

`courtvision.reporting.artifact_manifest.build_artifact_manifest` and `scripts/write_artifact_manifest.py` are read-only with respect to existing runtime artifacts. They inspect expected paths and write only:

- `outputs/runtime/diagnostics/artifact_manifest_<date>.json`
- `outputs/runtime/operator/artifact_manifest_<date>.txt`

The manifest records each artifact's category, name, expected path, existence, size, CSV row count where cheap/safe, severity, and notes.

## Fatal Manifest Conditions

The manifest marks these core operator artifacts as `fatal` when missing after a normal prediction run:

- `outputs/runtime/operator/elite_board_<date>.csv`
- `outputs/runtime/operator/full_market_board_<date>.csv`
- `outputs/runtime/operator/sgp_board_<date>.csv`

These files are the core operator board contract. Empty boards may be valid for a no-bet slate, but missing board files mean the prediction run did not produce the expected operator package.

## Warning Manifest Conditions

The manifest marks missing expected diagnostics, validation audits, and post-run reports as `warning`, including:

- Elite pipeline audit CSV/summary.
- Board diagnostics and market coverage diagnostics.
- Top plays and elite decision reports.
- Kelly stakes when expected by context.
- Daily summary, quality summary, completion audit, and operator card.
- Full-market sanity and candidate quality drift audit outputs.

Warnings are not automatic proof of bad predictions. They mean the operator should inspect the run before trusting downstream summaries.

## Informational Manifest Conditions

Research and verbose/debug artifacts are `informational`. Examples:

- Research predictions and edge CSVs.
- Model metrics JSON.
- Optional lane boards such as `outputs/runtime/optional/stat_only_board_<date>.csv`.

Missing informational artifacts should not block operator use.

## Shadow-Only Manifest Conditions

Shadow-only artifacts include diagnostics and simulations that are not live stake instructions:

- Market shadow grading/report.
- High-caution over watchlists.
- Combo under watchlists.
- Same-opponent warning reports.
- Other forward-looking research or simulation reports.

Shadow-only reports can motivate future changes, but they must not be used to bypass elite/Kelly gates.

## Non-Goals

- The manifest does not rerun prediction.
- The manifest does not rewrite existing board, history, grading, Kelly, or summary artifacts.
- The manifest does not decide whether to bet.
- The manifest does not loosen gates, thresholds, or validation checks.
