# CourtVision Full System Audit - Post-Incubator Grade

Audit date: 2026-05-29  
Repository: `C:\dev\Sport_Project1`  
Scope: audit only, post-incubator-grade evidence included  
Baseline commit inspected: `450f85c Audit live readiness`

---

## 1. Executive Summary

CourtVision's safety design looks structurally stronger after the first incubator candidate was graded, but real-money betting confidence does not improve from a sample size of one.

The key result is not that the incubator pick lost. The key result is that a high-caution OVER candidate was blocked from Elite, excluded from Kelly, tracked paper-only, persisted separately, graded, and reported without contaminating real pick history. That is the correct safety outcome.

Verdict:
- Controlled daily live operation: approved with an active human operator.
- Real-money staking: not approved.
- Autonomous betting: not approved.
- Incubator lane: structurally improved, still statistically unproven.
- Promotion: not recommended.

The first incubator miss supports keeping the `elite_reject_context_high_caution_over` gate strict. It should not be used to weaken Elite, Kelly, final decision, staking, or promotion logic.

---

## 2. Current System State

Latest verified 2026-05-28 slate state:
- `final_decision`: `NO BET`
- Elite picks count: 0
- Kelly eligible count: 0
- Full-market candidates: 58
- High-caution OVER watchlist rows: 45
- Incubator board rows: 1
- Incubator candidate: Jalen Williams, `player_points` over 13.5, `elite_reject_context_high_caution_over`
- Incubator grade: `miss`, `actual_value=1.0`, `grading_status=graded`
- Incubator performance: 1 total, 1 graded, 0 pending, 0/1/0 wins/losses/pushes, 0.00% hit rate, -100.00% flat ROI
- `pick_history.csv` rows for 2026-05-28: 0
- `incubator_history.csv` rows for 2026-05-28: 1
- `market_shadow_history.csv` rows for 2026-05-28: 58 pending shadow rows
- `paper_kelly_history.csv` rows for 2026-05-28: 51 pending paper rows
- Completion audit: `COMPLETE_WITH_SHADOW_OPEN_NOISE`, `real_pick_pending_count=0`

Important artifact nuance: `operator_card_2026-05-28.txt` was generated before the incubator grade and still shows the daily incubator row as pending. The post-grade evidence is in `data/history/incubator_history.csv` and `outputs/runtime/operator/incubator_performance_report_2026-05-28.txt`, both updated on 2026-05-29.

---

## 3. Overall Readiness Scores

| Category | Score | Readiness Band | Assessment |
| :--- | :---: | :--- | :--- |
| Controlled daily live operation readiness | 85 | Live-operator ready with monitoring | Runner, guards, reports, and completion audit are solid. Human review is still required. |
| Real-money betting readiness | 61 | Partially ready but risky for money | Safety infrastructure is strong, but calibration, CLV, and sample evidence do not justify staking. |
| Autonomous betting readiness | 18 | Not ready | No autonomous execution, late-news handling, sportsbook validation, or unattended risk loop is mature enough. |
| Data integrity readiness | 89 | Live-operator ready with monitoring | Active source identity exposure is clean; historical global conflicts are diagnostic, not active blocks. |
| Artifact/reporting readiness | 91 | Production strong / low operational risk | Reports are broad and mostly current; manifest still has nonfatal warning/missing-metadata gaps. |
| Bankroll safety readiness | 88 | Live-operator ready with monitoring | Elite/Kelly separation, no-bet behavior, caps, and hard gates protected the bankroll. |
| Calibration/performance readiness | 54 | Not ready | Biggest blocker: overconfidence, missing CLV, sparse feature completeness, and small forward samples. |
| Growth/learning-loop readiness | 86 | Live-operator ready with monitoring | Shadow, paper Kelly, and incubator lanes are useful and separated from real staking. |
| Incubator design readiness | 90 | Production strong / low operational risk | The paper-only lifecycle worked end to end, including persistence, grading, and performance reporting. |
| Promotion-readiness readiness | 35 | Not ready | One incubator grade and incomplete feature/CLV evidence are nowhere near promotion proof. |

---

## 4. What Improved Since the Previous Audit

- The incubator lane now has completed lifecycle evidence: board row, history row, grading, and performance report.
- The first tracked incubator candidate lost while remaining paper-only, proving the real-money safety boundary did its job.
- `incubator_history.csv` preserved a graded state: `result_status=miss`, `actual_value=1.0`, `grading_status=graded`.
- The incubator performance report correctly updated to 1 total, 1 graded, 0 pending.
- Contamination checks are stronger: the same Jalen Williams row is absent from `pick_history.csv`.
- Git hygiene is cleaner: runtime outputs, test outputs, and history CSVs are ignored and untracked.

---

## 5. What Did Not Improve

- Real-money confidence did not improve; a sample size of one cannot validate edge, confidence, or ROI.
- Autonomous readiness did not improve.
- CLV coverage remains 0 / 58 for the 2026-05-28 slate.
- Feature-complete current rows remain 0, driven by missing role-stability coverage.
- Calibration remains the largest blocker: 963 graded calibration rows, 353 tiny/small-sample warnings, and a worst overconfident bucket gap of -0.6550.
- No market should be promoted from incubator, paper Kelly, shadow, or meta-label reporting.

---

## 6. Impact of First Incubator Miss

The first incubator miss makes the system's safety design look stronger structurally, not stronger statistically.

Interpretation:
- Real-money confidence: unchanged to slightly lower.
- Incubator lane confidence: improved structurally because the end-to-end workflow worked.
- High-caution OVER gate confidence: strengthened directionally because the first tracked candidate lost.
- Promotion confidence: unchanged and low.

The correct lesson is: the blocked pick losing is exactly why incubator rows must stay paper-only until there is a meaningful sample.

---

## 7. Elite/Kelly Safety Assessment

Elite and Kelly safety remains strong.

Observed controls:
- Empty Elite board led to `NO BET`.
- Kelly was skipped because Elite count was 0.
- `run_kelly_stakes.py` reads only `elite_board_<date>.csv`, not incubator artifacts.
- Kelly remains locked to `player_points`.
- Kelly hard-blocks source identity conflicts, identity quarantine, manual review holds, same-opponent warnings, high-caution OVER rows, unsupported markets, stale game states, stale odds, non-positive edges, and missing confidence.
- Daily exposure cap defaults to 8% of bankroll.
- Team/game exposure caps and player exposure caps remain active in board construction.
- `validate_runtime_outputs.py` checks cap enforcement and directional edge consistency.
- `final_decision` returns `NO BET` when `elite_count <= 0`, and `REVIEW REQUIRED` for blocking source identity or hold conditions.

No evidence suggests incubator rows can affect staking.

---

## 8. Incubator Safety Assessment

Incubator design is strong for paper-only learning.

Evidence:
- `incubator_board_2026-05-28.csv` has `incubator_status=PAPER_ONLY` and `real_money_eligible=False`.
- The row requires strict filters: `player_points`, `over`, `edge >= 5.0`, `confidence >= 0.75`, `quality_score >= 60.0`, high caution, blocked by `elite_reject_context_high_caution_over`, and clean identity.
- Manual/security/problem holds, source identity conflicts, same-opponent warnings, Elite rows, and Kelly-eligible rows are excluded.
- `persist_daily_incubator_board()` writes only `data/history/incubator_history.csv`.
- `grade_incubator_picks()` grades incubator history separately.
- `write_incubator_performance_report()` reports incubator performance separately.

The design is ready for continued paper tracking, not promotion or staking.

---

## 9. High-Caution OVER Gate Assessment

The high-caution OVER gate should remain protected.

2026-05-28 evidence:
- 45 full-market rows were high-caution OVER watchlist rows.
- 0 high-caution conflicted OVER rows reached Elite.
- The incubator candidate was a high-caution conflicted player-points OVER and lost.

Historical evidence from `promotion_readiness_report_2026-05-28.txt` remains unfavorable:
- `player_points/over/conflicted/high`: 118 graded, 49 hits, 69 misses, 41.53% hit rate, -22.79% ROI.
- `player_points_assists/over/conflicted/high`: 50 graded, 42.00% hit rate, -21.24% ROI.
- `player_rebounds/over/conflicted/high`: 53 graded, 45.28% hit rate, -16.68% ROI.
- Combo OVER markets remain explicitly blocked or review-held.

The gate may be over-conservative on some isolated fast-paced playoff cases, but it is still protective and currently justified.

---

## 10. NO_BET Analysis

The 2026-05-28 `NO BET` was correct.

Reasons:
- No Elite picks survived safety/context gates.
- Kelly rows count was 0.
- Kelly eligible count was 0.
- 45 high-caution OVER candidates were watchlist-only.
- 6 combo UNDER candidates were watchlist-only.
- 2 same-opponent warnings remained diagnostic/full-market only.
- Source identity safety was clear for active operator rows.
- Completion state was clean for real picks.

The slate did not produce a real wager because the system refused to fill the Elite board with risky paper-only or watchlist candidates. That is the intended behavior.

---

## 11. Data Contamination Checks

Checks performed:
- `pick_history.csv`: 167 rows total, 0 rows for 2026-05-28.
- `incubator_history.csv`: 1 row for 2026-05-28.
- Jalen Williams `player_points over 13.5` in `pick_history.csv`: 0 rows.
- Jalen Williams `player_points over 13.5` in `incubator_history.csv`: 1 row, graded miss.
- `market_shadow_history.csv`: 58 rows for 2026-05-28, all pending shadow rows.
- `paper_kelly_history.csv`: 51 rows for 2026-05-28, all pending paper rows.
- `completion_state_audit_2026-05-28.json`: `real_pick_pending_count=0`, `shadow_stale_pending_count=0`, `paper_stale_pending_count=0`.

Conclusion: no incubator contamination of real pick history was found. Open shadow/paper rows are classified as open-game observation noise and are not blocking.

---

## 12. Calibration and Sample-Size Assessment

Current real-pick history:
- Hit/miss rows: 156
- Hits: 86
- Misses: 70
- Voids: 11
- All-time hit rate: 55.13%
- Last 7 completed real-pick slates: 8 hits, 4 misses, 66.67%

Selection split:
- Overs: 46 hits, 55 misses, 45.54%
- Unders: 37 hits, 11 misses, 77.08%
- Milestones: 3 hits, 4 misses, 42.86%

Calibration artifact evidence:
- `calibration_bucket_report_2026-05-28.txt`: 963 graded rows used.
- Worst overconfident bucket: `confidence_bucket=0.60-0.70`, `player_points`, `over`, `graded_n=12`, gap -0.6550.
- Tiny/small sample warning count: 353.
- CLV coverage: 0 / 58, missing close-line count 58.
- Feature completeness: 0 feature-complete current rows, 0 feature-complete graded rows, Phase 4C verdict `NEED_FEATURE_BACKFILL_REVIEW`.

This evidence is insufficient for real-money staking.

---

## 13. Artifact/Reporting Assessment

Artifact/reporting coverage is strong.

Verified artifacts include:
- `operator_card_2026-05-28.txt`
- `daily_summary_2026-05-28.txt`
- `quality_summary_2026-05-28.txt` and `.json`
- `incubator_board_2026-05-28.csv`
- `incubator_performance_report_2026-05-28.txt`, `.json`, and `.csv`
- `market_shadow_report_2026-05-28.txt`
- `paper_kelly_simulation_2026-05-28.txt` and `.csv`
- `paper_kelly_performance_report_2026-05-28.txt` and `.csv`
- `calibration_bucket_report_2026-05-28.txt` and `.json`
- `meta_label_promotion_shadow_2026-05-28.txt`, `.json`, and `.csv`
- `feature_completeness_tracker_2026-05-28.txt`, `.json`, and `.csv`
- `artifact_manifest_2026-05-28.txt` and `.json`
- `board_diagnostics_2026-05-28.json`
- `completion_state_audit_2026-05-28.txt` and `.json`

Manifest state:
- Status: `warning_missing`
- Fatal artifacts: 3 total, 0 missing
- Warning artifacts: 17 total, 1 missing (`kelly_stakes`, allowed on no-bet slates)
- Shadow-only artifacts: 25 total, 1 missing (`same_opponent_under_warnings`, nonfatal)
- Incubator performance JSON exists but lacks freshness metadata fields, so manifest labels it `missing_metadata`.

The metadata gap is noncritical but worth cleaning later.

---

## 14. Git Hygiene Assessment

Git state before report creation:
- Branch: `main`
- Upstream: `origin/main`
- State: clean
- Latest commit: `450f85c Audit live readiness`

`git log -5 --oneline`:
```text
450f85c Audit live readiness
8354125 Ignore generated test outputs
86f323f Persist incubator history during daily runs
fe9ee97 Track incubator performance separately
b8193aa Add paper-only incubator board
```

Tracked generated outputs check:
- `git ls-files test_outputs`: no output
- `git ls-files outputs/runtime`: no output
- `git ls-files data/history/incubator_history.csv`: no output
- `git ls-files data/history/pick_history.csv`: no output
- `git ls-files outputs`: no output
- `git ls-files data/history`: no output

Ignore checks confirm:
- `outputs/` ignored by `.gitignore`
- `test_outputs/` ignored by `.gitignore`
- `data/history/*.csv` ignored by `.gitignore`

This is clean and consistent with the repo cleanup goal.

---

## 15. Test Results

Requested tests were run exactly as requested.

```text
py -3.13 -m pytest -k "identity or source_identity or incubator or history or performance or operator_card or artifact_manifest"
```

Result: 337 passed, 2039 deselected, 5 xfailed.

```text
py -3.13 -m pytest -k "selection or elite or kelly or runtime_outputs or quality_summary"
```

Result: 378 passed, 2003 deselected.

```text
py -3.13 -m pytest -k "grading or pick_history or market_shadow or paper_kelly or completion_state"
```

Result: 147 passed, 2233 deselected, 1 xfailed.

All requested test selections passed. Pytest printed an ignored Windows temp cleanup `PermissionError` at process exit in each run; exit codes were 0 and no assertions failed.

---

## 16. Top 10 Remaining Risks

1. Calibration remains weak, especially for player-points OVERs.
2. CLV coverage is still absent, so line-value validation is incomplete.
3. Feature completeness is not forward-ready, with role stability missing from current complete rows.
4. High-caution OVER gate may remain overbroad on some postseason slates, causing frequent no-bet outcomes.
5. Market promotion evidence is too thin and uneven across buckets.
6. Paper Kelly has useful simulation history but remains non-executable and must not drive real stakes.
7. Incubator sample size is 1, creating extreme variance and zero promotion value.
8. Artifact manifest has nonfatal missing/warning states and incubator metadata freshness gaps.
9. Real-time lineup/injury/news response is still insufficient for autonomous operation.
10. Manual operator mistakes, especially forced historical reruns, remain a production risk.

---

## 17. Go-Live Recommendation

Controlled daily live operation is approved with monitoring.

Approved operating scope:
- Run current-day slates only.
- Treat `NO BET` as a valid production outcome.
- Keep a human operator in the loop.
- Use artifacts for review and diagnostics.
- Keep all staking at zero unless a future audit explicitly approves otherwise.

Not approved:
- Real-money betting.
- Autonomous execution.
- Incubator promotion.
- High-caution OVER gate loosening.

---

## 18. Real-Money Staking Recommendation

Real-money staking is not approved.

Recommended real-money stake: `$0.00`.

Rationale:
- First incubator candidate lost.
- Sample size is 1.
- High-caution OVER historical evidence remains poor.
- CLV coverage is missing.
- Calibration and feature completeness are not ready.
- No Elite/Kelly rows existed on the verified slate.

No change should be made to bankroll, Kelly, Elite, or final-decision logic.

---

## 19. Autonomous Betting Recommendation

Autonomous betting is not approved.

CourtVision lacks the required unattended safeguards:
- No autonomous sportsbook execution validation.
- No fully reliable late injury/news loop.
- No current CLV coverage.
- No mature promotion loop.
- Not enough forward evidence for closed-loop bankroll decisions.

Autonomous readiness remains very low.

---

## 20. Next Phase Recommendation

Recommended next phase: continue the paper-only incubator and shadow learning loop.

Priorities:
- Accumulate at least 20 graded incubator rows before drawing even weak directional conclusions.
- Prefer 50 to 100 graded incubator rows before any promotion discussion.
- Add or backfill feature completeness, especially role stability.
- Improve close-line collection so CLV coverage is not 0%.
- Keep high-caution OVERs out of Elite/Kelly while collecting evidence.
- Refresh artifact manifest metadata for incubator performance reports in a later non-bankroll-facing cleanup.

No promotion phase should begin yet.

---

## 21. Exact Operating Rules Going Forward

1. Do not rerun closed slates with `run_today.ps1` unless explicitly using a controlled repair workflow.
2. Do not regenerate 2026-05-28 boards.
3. Treat `NO BET` as the correct action when Elite is empty.
4. Keep Incubator rows paper-only.
5. Do not copy incubator rows into `pick_history.csv`.
6. Do not use paper Kelly exposure as real exposure.
7. Do not weaken `elite_reject_context_high_caution_over`.
8. Do not weaken Kelly restrictions or exposure caps.
9. Do not alter `final_decision` to create action.
10. Do not promote a market or lane without statistically meaningful graded evidence, CLV coverage, and explicit approval.
11. Continue checking `completion_state_audit` for `real_pick_pending_count=0`.
12. Continue reporting open shadow/paper rows as observation noise unless stale pending rows appear.
13. Preserve source identity diagnostics as nonblocking unless active Elite/Kelly exposure appears.
14. Use incubator performance as a learning signal only.

Final answer to the key question: after the first incubator candidate graded as a miss, the safety design looks stronger structurally, real-money confidence is unchanged to slightly lower, and no promotion is recommended.
