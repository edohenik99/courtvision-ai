# MLB HR prospective recovery candidate

This candidate is based on main `15b33588508cde4fe5ee92e467356f79a333ee1e`.
Versioned automation is in `automation/mlb_hr_recovery`. Installed wrappers,
Scheduled Tasks and all existing controls remain unchanged. Receipt integration
is reused from local main `8276a9df4ae3926a0c0d84058380859da26b939c` and its
`e62aede25ef1fed1063563ba54222bf2e972475e` implementation, with the additional
source/date/control/result binding checks documented in the finalizer notes.

## Preserved history

The three old controls retain their manifests, Git pins, models and ledgers.
Their artifact integrity can remain valid while prediction correctly rejects
their frozen source identity on this runtime. They are never upgraded in place.
The Aug 20 through Sep 13 prospective gap and earlier Aug 9/Aug 17 omissions
remain permanent gaps. Four early mutable-master source revisions remain
unreconstructed. Closing/settlement backlogs are not repaired by this candidate.

All new observations retain `research_only=true`,
`approval_status=not_approved`, `eligible_for_betting=false` and
`eligible_for_official_pick=false`. Name-only research does not establish resolved
player identity or a betting edge. Historical replay has no automation
configuration category and cannot be submitted through the recovery path.

## One explicit configuration for every wrapper

Each entrypoint requires both `-ConfigurationPath` and
`-ConfigurationSha256`. A configuration is a create-only JSON file selected
explicitly by the caller and immutable once bound into a task definition. There
is no current/default/latest control discovery. Runtime initialization verifies
the configuration bytes, exact executing source root, clean commit, Python
executable/version, control manifest digest, frozen Git fingerprint and model
bundle before logging, collection or existing-publication recovery.

The `mlb-hr-recovery-v1` configuration requires exactly these fields:

- `repository_root`, `python_executable`, `python_version`, `expected_commit`.
- `trial_root`, `control_id`, `control_manifest_sha256`,
  `cutover_operating_date`, `timezone` (`America/Toronto`).
- `evidence_root`, `odds_directory`, `results_csv`.
- `supplemental_shadow_enabled` (false), `allow_task_registration` (boolean),
  `allow_provider_collection` (boolean).
- `evidence_kind` (`prospective_research` or `disposable_test`) and the four
  research fields above.

Trial and external evidence roots must be separate. Source/result paths must
match the reviewed pinned pipeline's `data/theoddsapi/live_hr_snapshots` under
the explicitly configured repository. A disposable configuration requires a
trial directory whose name contains `disposable`, and prohibits registration
and collection. Its immutable control must also specify `evidence_kind` as
`disposable_test` and `promotion_excluded` as true. A prospective configuration
requires an ordinary control with absent or `prospective_research` category and
absent or false promotion exclusion. Configuration cannot relabel control evidence
or implicitly promote it. No production configuration or control is supplied by this PR.

Prediction and closing runners require the current Toronto operating date after
cutover. Their parent schedulers bind date and control identity into a new child
name and bind configuration path/digest into its action. Existing task names
always cause a collision failure. Registration has no force replacement and
creates a disabled task only when explicitly permitted by configuration. Missing
tasks and disabled tasks never cause automatic activation. Shadow scheduling is
outside this minimal architecture; both publication branches are gated.

## Inputs and finalization

Predictor/closing runners accept explicitly digest-bound `-LocalOddsCsv`,
`-LocalOddsSha256`, `-LocalScheduleJson`, and `-LocalScheduleSha256` for offline
manual operation. Live paths require an explicit collection setting and remain
unexecuted during candidate validation. Prediction creates a new source revision
that binds provider events to exactly one official schedule game by teams,
full start timestamp, operating date and positive gamePk. Only official game type
`R` and HR Over 0.5 are accepted. Original source files are never enriched in place.
The baseline also rejects unsupported markets, ambiguous/missing event types and
historical prediction dates. Existing historical readers remain compatible.

The nightly controller preserves the terminal-game and four-hour buffer chain:
authorization, pinned finalizer, result generation/export, internal MLB archive
grading/summary, execution receipt, V2 paper settlement and health/status.
Before result writers run, existing workbook and strict CSV bytes are retained in
a new `result_revisions` directory with a digest manifest. The execution receipt
binds the exact strict-results path and SHA256 consumed by settlement.
The separate generic grader and standalone Finalizer task remain disabled.

## Integration gates after review

1. Merge the reviewed candidate and validate CI.
2. Update a separately authorized runtime to the exact merged commit with clean
   provenance; keep the present primary checkout and historical stores preserved.
3. Validate the intended model bundle/interpreter, then use the existing explicit
   `activate-prospective-control --model-dir ... --trial-root ...
   --repository-root ...` command to publish a NEW production control in a new
   namespace bound to merged source. Never alter an old control or inherit rows.
4. Create and review its immutable configuration with a future cutover date and
   explicit source/result/evidence roots. Bind the configuration digest into
   each intended task action and separately approve collection budget/policy.
5. Complete a one-day manual production research rehearsal through finality and
   settlement. An offline fixture rehearsal does not satisfy this production gate.
6. Only then consider separately authorized activation of the exact three parents:
   Prospective Predictor, Closing Scheduler and Nightly Controller. Preserve the
   other 28 definitions and all historical children. New child activation and
   scheduling policy require an explicit reviewed decision.

Candidate validation uses fixture data in a retained disposable root with host
write/network boundaries. Linux contract tests and Windows syntax checks do not
establish live Windows Scheduler behavior. Test counts and unresolved limitations
belong in the external recovery report. No tests, controls, logs or runtime
artifacts generated during validation are committed.
