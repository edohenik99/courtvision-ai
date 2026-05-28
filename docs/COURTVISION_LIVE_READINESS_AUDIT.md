# CourtVision Live Readiness Audit Report

This report evaluates the current state of the CourtVision sports prediction pipeline, safety gates, identity resolution, incubator lane, and reporting artifacts to determine its readiness for daily live operations, real-money wagering, and autonomous betting.

---

## 1. Executive Summary

CourtVision has transitioned from active development into a structured live shadow and production-ready evaluation phase. Recent engineering work has successfully implemented safety gates, centralized player identity resolution, paper-only incubator lanes, and completion auditing.

During the runtime verification on 2026-05-28, the pipeline executed with the following verified state:
- **Final Decision:** NO BET
- **Elite Picks Count:** 0
- **Kelly Eligible Count:** 0
- **Incubator Board Count:** 1 pick (Jalen Williams, player_points OVER 13.5)
- **Incubator History:** Persisted in `incubator_history.csv` with status `open_game_pending`
- **Data Contamination:** Checked and verified clean (zero incubator or shadow row contamination in `pick_history.csv`)
- **Unit and Integration Tests:** 715 tests passed with zero failures

While the operational foundation is stable, key modeling biases and data limitations prevent immediate real-money or autonomous deployment.

---

## 2. Overall Readiness Scores

The system has been evaluated across seven key dimensions, rated from 0 to 100 based on evidence.

| Dimension | Score | Assessment |
| :--- | :---: | :--- |
| **Controlled Daily Live Operation** | **84** | Usable under active operator supervision. Automated validation and reports are robust, but require manual cron monitoring. |
| **Real-Money Betting Readiness** | **63** | Partially ready but highly risky. Severe projection inflation on strong OVERs requires a hard calibration guard. |
| **Autonomous Betting Readiness** | **18** | Not ready. Lacks real-time sportsbook API integrations, auto-failover, and automated late-breaking injury news checks. |
| **Data Integrity Readiness** | **88** | Roster baselines, player identities, and historical tables are resolved cleanly. Minor manual maintenance is needed for odds naming conflicts. |
| **Artifact/Reporting Readiness** | **92** | Excellent reporting coverage. Manifests, cards, daily/quality summaries, and completion audits are fully automated and verified. |
| **Bankroll Safety Readiness** | **85** | Multi-layered defense including daily exposure caps, team/game exposure limits, directional validation, and calibration guards. |
| **Growth-Loop Readiness** | **82** | Separate incubator board and shadow history persistence are fully operational, permitting risk-free strategy evaluation. |
| **Calibration/Performance Readiness** | **55** | Low readiness. Strong player points OVER picks have a historical 41.6% hit rate, requiring active recalibration. |

---

## 3. What is Production-Ready Now

The following pipeline components have been successfully verified as production-ready:
1. **Stable Runner Core:** The `run_today.ps1` PowerShell runner and `run_today.bat` batch entrypoint automate slate checking, model inference, validation, grading, and artifact writing.
2. **Closed-Slate Lifecycle Guards:** Historical slate regeneration is blocked by default unless `-ForcePastDate` is explicitly passed, protecting settled wagers and historical records from accidental modification.
3. **Player Identity Resolution:** Centralized matching via `courtvision/context/player_identity.py` resolves multi-stint trades, stale team roster baselines, and downstream columns correctly.
4. **Context Safety Gates:** High-caution OVER context gates successfully detect slow-pace or strong-defense match-ups and reject conflicted OVER picks from Elite boards.
5. **Incubator Lane and History:** The paper-only incubator board isolates high-caution, high-edge picks, persisting them in `data/history/incubator_history.csv` for risk-free hit-rate evaluation.
6. **Reporting Automation:** Generation of `operator_card`, `daily_summary`, `quality_summary`, `board_diagnostics`, and `completion_state_audit` is fully automated.

---

## 4. What is Not Production-Ready Yet

The following items are the primary barriers to live production release:
1. **Projection Model Calibration:** Strong player points OVER picks (edge >= +3.0) exhibit severe historical inflation, hitting only 41.6% (n=127). They are currently blocked by a hard-coded gate.
2. **Late Lineup/Injury Feeds:** The pipeline relies on static injury diagnostics. It lacks real-time, active polling of active/inactive lineup changes close to game lock times.
3. **Sportsbook Execution Layer:** No automated mechanism exists to check bookmaker line changes, locked markets, or to place wagers.
4. **Roster Name Mapping Maintenance:** Discrepancies between external odds feeds and historical baselines require active operator monitoring and manual overrides.
5. **Small Shadow Sample Sizes:** Shadow and incubator historical files contain under 20 graded picks, which is insufficient for statistical ROI validation.

---

## 5. Top 10 Remaining Risks

1. **Roster Baseline Stale Records:** Mismatches between external odds names and baseline rosters can cause players to be quarantined (`data_invalid`) and skipped.
2. **Late-Breaking NBA Rotations:** Rest days or late inactive announcements close to tip-off drastically affect player usage, which the pipeline cannot react to automatically.
3. **Scoring Model Calibration Bias:** Projection model drift could lead to bankroll drawdown if the calibration guard fails.
4. **Uncalibrated Confidence Metrics:** Sizing calculations assume confidence values are calibrated, but current distributions may overstate success probability.
5. **Excessive NO_BET Days:** Rigid context gates reject most OVER picks, leading to highly frequent zero-pick days and under-utilized capital.
6. **Sportsbook Line Movement:** Lines often shift or lock before wagers can be placed manually, leading to missed wagers or negative CLV.
7. **External Data Source Outages:** Failures in external API endpoints can result in incomplete slates or "unsafe" provider status.
8. **Operator Execution Errors:** Manual intervention during daily runs (e.g. forced runs or manual grading) remains prone to human error.
9. **Small Sample Size Fragility:** High variance in early live outcomes could result in premature stop-loss triggers or false confidence.
10. **Historical Rerun Pollution:** Inadvertent use of `-ForcePastDate` by an operator can contaminate past records if not closely supervised.

---

## 6. NO_BET Analysis

The verified state on 2026-05-28 resulted in a `final_decision` of **NO BET**. This is caused by two protective factors:
1. **High-Caution OVER Gate:** The single candidate (Jalen Williams, player_points OVER 13.5) was correctly flagged as a high-caution pick due to context conflicts (slow pace or strong defensive opponent).
2. **Incubator Separation:** The pick was routed to the incubator board as a paper-only pick, leaving 0 active Elite picks.

The context gate remains highly protective and performs as designed. However, in low-volume slates or highly defensive match-ups, these context gates will consistently result in NO_BET slates, restricting overall staking volume.

---

## 7. Incubator Lane Assessment

The paper-only incubator board isolates picks that meet high-quality modeling criteria but fail context-specific safety gates.
- **Thresholds:** Edge >= 5.0, confidence >= 0.75, and quality_score >= 60.0 on player points OVER wagers.
- **Persistence:** Appended rows are written to `data/history/incubator_history.csv` during daily runs.
- **Grading:** Automatic grading via `grade_incubator_picks` checks player stats against finished game boxes without affecting real pick records.
- **Separation Proof:** Incubator picks are checked for a `real_money_eligible=False` field and do not write to the elite board or affect Kelly stakes, guaranteeing that paper-only picks never contaminate real wagering operations.

---

## 8. Kelly/Elite Safety Assessment

Wager sizing and candidate selection are governed by mathematical safeguards:
1. **Directional Edge Validation:** Built-in scripts (`validate_runtime_outputs.py`) enforce that OVER selections have positive edges and UNDER selections have negative edges, immediately flagging violations.
2. **Exposure Limits:** Exposure is capped at a maximum of **3% of bankroll per team** and **4% of bankroll per game**.
3. **Daily Capital Cap:** Total daily staking across all picks is scaled proportionally to never exceed **8% of the total bankroll** (under default exposure configurations).
4. **Dampeners:** Player points ranking pressure and recent form ratio dampeners scale down quality scores and stakes for highly volatile rotation situations.
5. **Edge Containment HOLDS:** Combo player prop markets are automatically held for review under Phase 12G controls.

---

## 9. Calibration and Sample-Size Assessment

The primary modeling evidence reveals significant calibration deficiencies:
- **Strong OVER Prop Bias:** Player points OVER selections with edge >= +3.0 show a historical hit rate of only 41.6%.
- **Small Sample Sizes:** The current grading histories contain very few rows (often under 20 graded picks per category). High variance makes it impossible to mathematically prove that the model has a positive expected value under live conditions.

Recalibration is mandatory before these defensive blocks can be safely removed.

---

## 10. Artifact/Reporting Assessment

The reporting artifacts demonstrate a high level of operational clarity:
- **Operator Card:** Synthesizes pipeline health, validation status, missing required files, and final run-level decisions cleanly.
- **Daily & Quality Summaries:** Document every stage of the candidate funnel, outlining precisely how many rows were dropped at each gate.
- **Completion State Audit:** Maps all pending picks, verifying that slate-closed states are free of open-game noise.
- **Validation Scripts:** Exit nonzero on any exposure cap or directional validation failures, automatically halting downstream staging.

---

## 11. Data Contamination Checks

A dedicated check of `data/history/pick_history.csv` was performed:
- **Result:** No shadow, paper, or incubator rows exist in the real pick history.
- **Deduplication:** Roster and grading histories utilize standardized line string comparisons to prevent duplicate row errors during concurrent execution.
- **Integrity:** Separation of real-money pick history and paper-only incubator history is completely enforced.

---

## 12. Go-Live Recommendation

1. **Controlled Daily Live Operation:** **APPROVED.** The operational infrastructure is highly stable and suitable for a human operator conducting shadow runs and manual tracking.
2. **Real-Money Betting:** **REJECTED.** The projection model calibration bias on OVER picks introduces unacceptable risk to capital. Staking should remain at $0.00.
3. **Autonomous Betting:** **REJECTED.** The lack of automated injury checks and real-time sportsbook integrations makes autonomous wagering unsafe.

---

## 13. Exact Rules for Safe Operation

1. **Active Roster Validation:** The operator must review `board_diagnostics_{date}.json` daily to identify any player names quarantined due to `player_identity_validation` errors.
2. **Slate-Closed Isolation:** Never rerun `run_today.ps1` on past dates without `-ForcePastDate`, and never regenerate past records unless conducting a controlled repair.
3. **Manual Review Resolution:** Any row marked as `REVIEW REQUIRED` on the operator card must be manually inspected and graded. No wagering is permitted on rows flagged by the `pre_kelly_hard_block` gate.
4. **Validation Failure Response:** If `validate_runtime_outputs.py` exits nonzero, the operator must immediately abort the run.

---

## 14. What Must Happen Before Increasing Staking Confidence

1. **Model Recalibration:** Retrain the player points projection model to eliminate the OVER selection bias.
2. **Accumulate Graded Samples:** Run the incubator lane in shadow mode until at least **100 graded picks** have been recorded to calculate statistically significant win-rates.
3. **Automate Injury Feeds:** Integrate a low-latency injury/lineup API to automatically adjust projected minutes close to game lock times.

---

## 15. Recommended Next Phase

The recommended next phase is **Phase 5: Model Recalibration and Shadow-Scale Expansion**.

This phase must focus on retraining the player points projection algorithms, integrating real-time lineup feeds, and running scale evaluations via the incubator board to compile a larger dataset of graded picks.
