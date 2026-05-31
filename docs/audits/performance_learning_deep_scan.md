# CourtVision Phase 6A.0B Performance Learning Deep Scan

This is a read-only performance-learning audit. It does not change production code, betting logic, Elite logic, Kelly logic, thresholds, `final_decision`, bankroll/staking logic, runtime boards, grading, or history files.

No `run_today.bat` execution, no `2026-05-30` grading, no board regeneration, and no `--override-date-integrity` run was performed. `pick_history.csv` was read only.

Source state inspected:

- Initial branch state: clean `git status --short`
- Initial `git diff --stat`: empty
- Recent commits inspected with `git log -5 --oneline`; latest at audit start was `d98c2c6 Audit system before provider migration`
- `git ls-files data/history`: no tracked history artifacts
- `git ls-files outputs/runtime`: no tracked runtime artifacts
- Runtime/history files used were local ignored artifacts, read only

Primary inputs:

- `data/history/pick_history.csv`
- `data/history/incubator_history.csv`
- `data/history/shadow_candidate_lane_history.csv`
- `data/history/market_shadow_history.csv` for the requested full-market shadow history view
- `data/history/paper_kelly_history.csv` only as corroborating paper-only evidence where already surfaced by safe-action reports
- `outputs/runtime/operator/*` and `outputs/runtime/diagnostics/bet_readiness_report_2026-05-30.json` matching the requested report classes

## 1. Executive Verdict

**Verdict: DECISION_BRAIN_WORKING**

CourtVision is saying NO BET or RESEARCH_ONLY primarily because the active slates are dominated by high-caution, context-conflicted OVER candidates with weak or negative historical economics. The strict behavior is mostly bankroll-protective, not evidence that the brain is broken.

Key evidence:

- Recent funnel, `2026-05-11` through `2026-05-28`: 731 full-market candidates, only 2 Elite rows and 2 Kelly rows. High-caution OVER blocked 474 rows, or 64.8% of full-market candidates.
- `2026-05-30` bet readiness: RESEARCH_ONLY, score 45/100, 0 real-money candidates, 9 manual-review candidates, 18 research-only candidates, and 91 do-not-promote rows after dedupe.
- Full-market shadow OVERs are poor: 626 graded, 280-344-2, 44.7% hit rate, -18.4% flat ROI.
- Full-market high-caution OVERs are also poor: 398 graded, 185-213-0, 46.5% hit rate, -13.9% flat ROI.
- Real/Elite history is not dead: `pick_history.csv` has 156 graded, 86-70-0, 55.1% hit rate, +0.4% flat ROI after excluding 11 void rows.
- UNDER shadow evidence is directionally better, but policy and sample/control limits mean it should stay shadow-only: 183 graded full-market shadow UNDERs, 107-76-0, 58.5% hit rate, +4.5% flat ROI.

The decision brain should not be loosened. The next step is learning/reporting, not production rule changes.

## 2. Why CourtVision Keeps Saying No Bet

Historical and current blockers:

| Blocker | Evidence | Read |
| --- | ---: | --- |
| No Elite rows | Recent no-bet funnel had 2 Elite rows across 14 operator-card dates; `2026-05-30` had 0 Elite rows | Correct hard stop for staking |
| No Kelly rows | Recent no-bet funnel had 2 Kelly rows across 14 dates; `2026-05-30` had 0 Kelly rows | Correct hard stop for staking |
| High-caution OVER gate | 474/731 recent rows blocked through `2026-05-28`; `2026-05-30` had 41 high-caution OVERs out of 50 full-market rows | Dominant safety blocker |
| Same-opponent warnings | 8 recent warnings through `2026-05-28`; `2026-05-30` had 1 | Keep blocked/manual-review only |
| Identity conflicts | `2026-05-30` readiness reported 222 global source identity conflicts, but active candidate conflicts were 0 and row exposure was 0 | Diagnostic blocker is conservative; active slate identity was clear |
| Low quality | Current under audit showed 15 UNDER candidates blocked by low quality; many positive combo buckets have average quality below Elite standards | Keep out of production |
| Low confidence | Current under audit showed 14 UNDER candidates blocked by low confidence | Keep out of production |
| Unsupported market | `2026-05-30` dropped 4 active unsupported `player_steals` markets; recent no-bet funnel had 36 unsupported active-market drops | Correct gate |
| Shadow-only restriction | Shadow candidate lane is explicitly paper-only; 52 rows, all real-money/Kelly/Elite false | Correct safety wall |
| UNDER research not promoted | Current under visibility says shadow tracking only and real-money promotion strictly blocked | Keep policy |
| Combo market not Kelly eligible | Combo UNDER and paper Kelly histories show useful tracking, but not real Kelly eligibility | Correct gate |

The no-bet streak is therefore mostly a safety-gate phenomenon. It is not a candidate-generation outage: full-market boards exist, near-elite rows exist, and prior review-required slates prove Elite/Kelly output is possible when gates are satisfied.

## 3. What CourtVision Has Learned

Performance summary:

| Source / lane | Total | Graded | Pending | Void | W/L/P | Hit rate | ROI | Avg edge | Avg conf | Avg quality | Avg odds | Sample quality |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Real/Elite pick history | 167 | 156 | 0 | 11 | 86/70/0 | 55.1% | +0.4% | 0.81 | 0.78 | 100.91 | -87.69 | 100+ stronger |
| Full-market shadow history | 1793 | 809 | 110 | 874 | 387/420/2 | 47.8% | -13.2% | 1.29 | 0.74 | 59.69 | -137.01 | 100+ stronger |
| Incubator history | 6 | 1 | 5 | 0 | 0/1/0 | 0.0% | -100.0% | 10.08 | 0.81 | 92.44 | -82.00 | <20 no conclusion |
| Shadow candidate lane history | 52 | 19 | 33 | 0 | 6/13/0 | 31.6% | -46.4% | 3.96 | 0.77 | 53.88 | -96.02 | <20 no conclusion |
| Paper Kelly corroboration | 813 | 386 | 70 | 357 | 198/188/0 | 51.3% | -2.8% | 1.87 | 0.74 | 45.68 | -94.97 | 100+ stronger |

Research lane summary:

| Lane / segment | Total | Graded | Pending | Void | W/L/P | Hit rate | ROI | Avg edge | Avg conf | Avg quality | Recommendation |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| UNDER_ALIGNED_RESEARCH | 4 | 0 | 4 | 0 | 0/0/0 | n/a | n/a | -1.86 | 0.77 | 41.72 | WATCHLIST, shadow only |
| COMBO_OVER_WEAK_POSITIVE_RESEARCH | 12 | 0 | 12 | 0 | 0/0/0 | n/a | n/a | 3.77 | 0.76 | 48.93 | WATCHLIST, shadow only |
| INCUBATOR_RESEARCH | 3 | 1 | 2 | 0 | 0/1/0 | 0.0% | -100.0% | 9.25 | 0.81 | 87.98 | KEEP_BLOCKED / collect |
| HIGH_CAUTION_OVER_DO_NOT_PROMOTE | 17 | 10 | 7 | 0 | 1/9/0 | 10.0% | -82.9% | 3.34 | 0.75 | 46.72 | KEEP_BLOCKED |
| NEAR_ELITE_RESEARCH | 16 | 8 | 8 | 0 | 5/3/0 | 62.5% | +5.9% | 5.22 | 0.78 | 61.86 | MANUAL_REVIEW_CANDIDATE, not production |
| Full-market shadow UNDER | 427 | 183 | 22 | 222 | 107/76/0 | 58.5% | +4.5% | -2.10 | 0.74 | 61.36 | KEEP_SHADOW |
| Full-market shadow OVER | 1366 | 626 | 88 | 652 | 280/344/2 | 44.7% | -18.4% | 2.36 | 0.74 | 59.17 | KEEP_BLOCKED |
| Full-market high-caution OVER | 850 | 398 | 88 | 364 | 185/213/0 | 46.5% | -13.9% | 2.43 | 0.74 | 53.17 | KEEP_BLOCKED |
| Full-market HCO rejection reason | 702 | 311 | 87 | 304 | 154/157/0 | 49.5% | -8.5% | 2.52 | 0.74 | 43.72 | WATCHLIST only |

Interpretation:

- Real-money history is barely positive after void exclusion, so the core brain is not obviously overselecting.
- Broad full-market shadow evidence says OVER candidates are dangerous.
- UNDERs have the clearest positive shadow signal, but they remain policy-blocked and should not be promoted.
- Near-elite looks interesting but has only 8 graded rows in the shadow candidate lane.
- Incubator and high-caution do-not-promote lanes are not ready; the first incubator row missed, and the high-caution do-not-promote lane is 1-9.

## 4. Bucket Performance Matrix

This matrix uses primary history rows from real picks, full-market shadow, incubator, and shadow candidate lanes. Paper Kelly is not included in the primary combined matrix to avoid double-counting mirrored shadow rows; it is used below only as corroboration when a safe-action report already surfaces it.

Recommendations are intentionally conservative:

- KEEP_BLOCKED: do not promote; keep hard gate or block active.
- KEEP_SHADOW: continue shadow tracking only.
- WATCHLIST: collect more samples; no production use.
- MANUAL_REVIEW_CANDIDATE: may be proposed later for human review, not automatic staking.
- PROMOTION_CANDIDATE_REQUIRES_APPROVAL: no current bucket qualifies for production promotion.
- DEMOTE_OR_BLOCK: applies to badly underperforming buckets already in shadow/research.

| Dimension | Bucket | Total | Graded | Pending | Void | W/L/P | Hit | ROI | Avg edge | Conservative flag | Recommendation |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| market_type | player_points | 604 | 397 | 49 | 158 | 202/193/2 | 50.9% | -8.3% | 2.13 | YES: negative ROI | WATCHLIST |
| market_type | player_points_rebounds | 222 | 124 | 17 | 81 | 57/67/0 | 46.0% | -14.3% | 1.25 | YES: negative ROI | KEEP_BLOCKED |
| market_type | player_points_assists | 220 | 121 | 12 | 87 | 48/73/0 | 39.7% | -26.0% | 1.36 | YES: negative ROI | KEEP_BLOCKED |
| market_type | player_points_rebounds_assists | 221 | 106 | 22 | 93 | 51/55/0 | 48.1% | -11.8% | 1.66 | YES: negative ROI | KEEP_BLOCKED |
| market_type | player_rebounds | 249 | 85 | 17 | 147 | 45/40/0 | 52.9% | -6.0% | 0.59 | YES: negative ROI | WATCHLIST |
| market_type | player_rebounds_assists | 162 | 69 | 14 | 79 | 35/34/0 | 50.7% | -7.0% | 0.51 | YES: negative ROI | WATCHLIST |
| market_type | player_assists | 216 | 66 | 17 | 133 | 33/33/0 | 50.0% | -9.6% | 1.10 | YES: negative ROI | WATCHLIST |
| market_type | player_steals | 74 | 10 | 0 | 64 | 6/4/0 | 60.0% | -9.4% | 0.31 | YES: low sample | KEEP_BLOCKED |
| market_type | player_blocks | 50 | 7 | 0 | 43 | 2/5/0 | 28.6% | -56.4% | 0.06 | YES: low sample | KEEP_BLOCKED |
| selection | over | 1528 | 747 | 122 | 659 | 332/413/2 | 44.4% | -18.8% | 2.52 | YES: negative ROI | KEEP_BLOCKED |
| selection | under | 482 | 231 | 26 | 225 | 144/87/0 | 62.3% | +12.0% | -2.41 | NO, but policy-blocked | KEEP_SHADOW |
| selection | milestone | 8 | 7 | 0 | 1 | 3/4/0 | 42.9% | -39.1% | 3.99 | YES: low sample | KEEP_BLOCKED |
| research_lane | FULL_MARKET_SHADOW | 646 | 344 | 0 | 302 | 152/190/2 | 44.2% | -20.5% | 1.16 | YES: negative ROI | KEEP_BLOCKED |
| research_lane | elite_reject_context_high_caution_over | 702 | 311 | 87 | 304 | 154/157/0 | 49.5% | -8.5% | 2.52 | YES: negative ROI | WATCHLIST |
| research_lane | REAL_ELITE | 167 | 156 | 0 | 11 | 86/70/0 | 55.1% | +0.4% | 0.81 | NO | KEEP_SHADOW / NO_CORE_CHANGE |
| research_lane | HIGH_CAUTION_OVER_DO_NOT_PROMOTE | 17 | 10 | 7 | 0 | 1/9/0 | 10.0% | -82.9% | 3.34 | YES: low sample and severe loss | KEEP_BLOCKED |
| research_lane | NEAR_ELITE_RESEARCH | 16 | 8 | 8 | 0 | 5/3/0 | 62.5% | +5.9% | 5.22 | YES: low sample | MANUAL_REVIEW_CANDIDATE |
| research_lane | INCUBATOR_RESEARCH | 9 | 2 | 7 | 0 | 0/2/0 | 0.0% | -100.0% | 9.81 | YES: low sample | KEEP_BLOCKED |
| research_lane | COMBO_OVER_WEAK_POSITIVE_RESEARCH | 12 | 0 | 12 | 0 | 0/0/0 | n/a | n/a | 3.77 | YES: no graded sample | WATCHLIST |
| research_lane | UNDER_ALIGNED_RESEARCH | 4 | 0 | 4 | 0 | 0/0/0 | n/a | n/a | -1.86 | YES: no graded sample | WATCHLIST |
| context_caution_level | high | 920 | 434 | 122 | 364 | 197/237/0 | 45.4% | -16.1% | 2.60 | YES: negative ROI | KEEP_BLOCKED |
| context_caution_level | medium | 299 | 128 | 0 | 171 | 56/72/0 | 43.8% | -20.6% | 0.47 | YES: negative ROI | KEEP_BLOCKED |
| context_caution_level | low | 289 | 128 | 26 | 135 | 75/53/0 | 58.6% | +3.0% | -2.13 | NO, but mostly UNDER | KEEP_SHADOW |
| context_caution_level | unknown | 488 | 288 | 0 | 200 | 145/141/2 | 50.3% | -9.5% | 1.60 | YES: negative ROI | WATCHLIST |
| context_pick_alignment | conflicted | 915 | 434 | 117 | 364 | 197/237/0 | 45.4% | -16.1% | 2.55 | YES: negative ROI | KEEP_BLOCKED |
| context_pick_alignment | neutral | 299 | 128 | 0 | 171 | 56/72/0 | 43.8% | -20.6% | 0.47 | YES: negative ROI | KEEP_BLOCKED |
| context_pick_alignment | aligned | 289 | 128 | 26 | 135 | 75/53/0 | 58.6% | +3.0% | -2.13 | NO, but mostly UNDER | KEEP_SHADOW |
| context_pick_alignment | unknown | 494 | 289 | 5 | 200 | 145/142/2 | 50.2% | -9.8% | 1.70 | YES: negative ROI | WATCHLIST |
| confidence_bucket | 0.75-0.85 | 1426 | 770 | 110 | 546 | 383/385/2 | 49.7% | -9.7% | 1.42 | YES: negative ROI | WATCHLIST |
| confidence_bucket | 0.65-0.75 | 527 | 206 | 38 | 283 | 92/114/0 | 44.7% | -18.5% | 1.29 | YES: negative ROI | KEEP_BLOCKED |
| confidence_bucket | <0.65 | 65 | 9 | 0 | 56 | 4/5/0 | 44.4% | -33.0% | 0.23 | YES: low sample | KEEP_BLOCKED |
| quality_bucket | 85+ | 553 | 411 | 10 | 132 | 194/215/2 | 47.2% | -15.2% | 1.37 | YES: negative ROI | KEEP_BLOCKED |
| quality_bucket | 35-48 | 678 | 267 | 83 | 328 | 135/132/0 | 50.6% | -7.8% | 1.08 | YES: negative ROI | WATCHLIST |
| quality_bucket | <35 | 279 | 106 | 19 | 154 | 48/58/0 | 45.3% | -18.6% | 0.39 | YES: negative ROI | KEEP_BLOCKED |
| quality_bucket | 65-85 | 261 | 102 | 12 | 147 | 49/53/0 | 48.0% | -10.5% | 2.19 | YES: negative ROI | KEEP_BLOCKED |
| quality_bucket | 48-65 | 247 | 99 | 24 | 124 | 53/46/0 | 53.5% | -2.2% | 2.26 | YES: negative ROI | WATCHLIST |
| edge_bucket | <1 | 562 | 211 | 33 | 318 | 92/119/0 | 43.6% | -20.5% | 0.29 | YES: negative ROI | KEEP_BLOCKED |
| edge_bucket | 1-2 | 551 | 267 | 37 | 247 | 131/136/0 | 49.1% | -9.5% | 0.76 | YES: negative ROI | WATCHLIST |
| edge_bucket | 3-5 | 346 | 194 | 33 | 119 | 86/108/0 | 44.3% | -18.2% | 1.97 | YES: negative ROI | KEEP_BLOCKED |
| edge_bucket | 2-3 | 325 | 184 | 24 | 117 | 98/86/0 | 53.3% | -3.2% | 1.29 | YES: negative ROI | WATCHLIST |
| edge_bucket | 5+ | 234 | 129 | 21 | 84 | 72/55/2 | 55.8% | -4.8% | 4.45 | YES: negative ROI | WATCHLIST |
| odds_bucket | -120 to +100 | 917 | 477 | 69 | 371 | 214/263/0 | 44.9% | -14.9% | 1.39 | YES: negative ROI | KEEP_BLOCKED |
| odds_bucket | -149 to -121 | 542 | 281 | 49 | 212 | 142/139/0 | 50.5% | -10.8% | 1.21 | YES: negative ROI | KEEP_BLOCKED |
| odds_bucket | <=-150 | 342 | 124 | 12 | 206 | 81/42/1 | 65.3% | -1.4% | 1.46 | Vig-sensitive | WATCHLIST |
| odds_bucket | +101 to +130 | 164 | 84 | 18 | 62 | 38/46/0 | 45.2% | -5.3% | 1.50 | YES: negative ROI | KEEP_BLOCKED |
| odds_bucket | +131+ | 53 | 19 | 0 | 34 | 4/14/1 | 21.1% | -43.2% | 0.98 | YES: low sample | KEEP_BLOCKED |
| same_opponent_warning | False | 1660 | 796 | 0 | 864 | 381/413/2 | 47.9% | -13.2% | 1.30 | YES: negative ROI | KEEP_BLOCKED |
| same_opponent_warning | True | 23 | 13 | 0 | 10 | 6/7/0 | 46.2% | -12.4% | -2.39 | YES: low sample | KEEP_BLOCKED |
| manual_review_required | unknown/history | 2018 | 985 | 148 | 885 | 479/504/2 | 48.6% | -11.8% | 1.35 | YES: negative ROI | KEEP_BLOCKED |
| identity_resolution_category | unknown/history | 2018 | 985 | 148 | 885 | 479/504/2 | 48.6% | -11.8% | 1.35 | YES: negative ROI | KEEP_BLOCKED |

Current-board identity/manual-review exposure for `2026-05-30`:

| Artifact | Rows | Identity bucket | Manual review bucket | Same-opponent warning | Read |
| --- | ---: | --- | --- | --- | --- |
| full_market_board_2026-05-30.csv | 50 | 42 blank, 8 valid_current_team_override | 49 false, 1 true | 49 false, 1 true | No active identity conflict; one warning/manual row |
| near_elite_review_2026-05-30.csv | 8 | 7 blank, 1 valid_current_team_override | 8 false | 8 false | Near-Elite is review-only |

Safe-action corroboration, high-signal buckets:

| Source | Market | Sel | Caution | Align | Reason | Graded | W/L/P | Hit | ROI | Evidence | Required recommendation |
| --- | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |
| market_shadow_history | player_points | over | high | conflicted | elite_reject_context_high_caution_over | 99 | 42/57/0 | 42.4% | -21.1% | 50-99 moderate | KEEP_BLOCKED |
| market_shadow_history | player_rebounds | over | high | conflicted | elite_reject_context_high_caution_over | 53 | 24/29/0 | 45.3% | -16.7% | 50-99 moderate | KEEP_BLOCKED |
| market_shadow_history | player_assists | over | high | conflicted | elite_reject_context_high_caution_over | 46 | 21/25/0 | 45.6% | -13.2% | 20-49 weak | KEEP_BLOCKED |
| market_shadow_history | player_points_assists | over | high | conflicted | unknown | 22 | 6/16/0 | 27.3% | -48.5% | 20-49 weak | DEMOTE_OR_BLOCK |
| market_shadow_history | player_points_rebounds_assists | over | unknown | unknown | unknown | 29 | 10/19/0 | 34.5% | -37.0% | 20-49 weak | KEEP_BLOCKED |
| market_shadow_history | player_points_rebounds | over | high | conflicted | elite_reject_context_high_caution_over | 34 | 20/14/0 | 58.8% | +10.1% | 20-49 weak | KEEP_SHADOW |
| market_shadow_history | player_points_assists | over | high | conflicted | elite_reject_context_high_caution_over | 28 | 15/13/0 | 53.6% | +0.2% | 20-49 weak | KEEP_SHADOW |
| market_shadow_history | player_points_rebounds_assists | over | high | conflicted | elite_reject_context_high_caution_over | 21 | 14/7/0 | 66.7% | +23.0% | 20-49 weak | KEEP_SHADOW |
| market_shadow_history | player_rebounds_assists | over | high | conflicted | elite_reject_context_high_caution_over | 21 | 14/7/0 | 66.7% | +18.6% | 20-49 weak | KEEP_SHADOW |
| pick_history | player_points | under | unknown | unknown | player_points_high_quality_pass | 25 | 21/4/0 | 84.0% | +53.3% | 20-49 weak | KEEP_BLOCKED by no-UNDER-promotion policy |
| pick_history | player_points | over | unknown | unknown | player_points_high_quality_pass | 66 | 32/34/0 | 48.5% | -10.0% | 50-99 moderate | KEEP_BLOCKED |

No bucket qualifies for automatic production promotion.

## 5. Blocked Pick Audit

**High-caution OVERs**

These blocks are protecting bankroll overall. Broad full-market high-caution OVERs are 398 graded, 185-213-0, 46.5% hit rate, -13.9% ROI. The targeted shadow candidate lane `HIGH_CAUTION_OVER_DO_NOT_PROMOTE` is worse: 10 graded, 1-9, -82.9% ROI. Keep blocked.

**Conflicted-context OVERs**

Context-conflicted rows are 434 graded, 197-237-0, 45.4% hit rate, -16.1% ROI. This supports the context conflict gate. Some combo sub-buckets are positive, but they are weak-directional and should stay shadow-only.

**Same-opponent warning candidates**

Same-opponent warning history is low sample and negative: 13 graded, 6-7-0, 46.2% hit rate, -12.4% ROI. Keep blocked or manual-review only.

**Low-quality candidates**

Quality `<35` has 106 graded, 48-58-0, 45.3% hit rate, -18.6% ROI. Quality `35-48` is less bad but still negative at -7.8% ROI. Low-quality candidates should not be promoted.

**Identity-conflict candidates**

`2026-05-30` reported 222 global source identity conflicts, but active candidate true conflicts were 0, row identity was valid for all 50 full-market rows, and source identity conflicted was false for all 50 rows. The readiness blocker is conservative. Active slate identity handling appears protective, not overblocking.

**Unsupported combo markets**

Combo markets show pockets of shadow promise, but combo market Kelly eligibility remains correctly blocked. The strongest high-caution combo OVER buckets have only 21-34 graded rows. Combo UNDER paper-only rows remain useful for learning, not staking.

**Answer: Were these blocks protecting us or overblocking?**

Mostly protecting us. The only plausible overblocking signal is not broad gate looseness; it is research visibility. UNDERs and a few combo high-caution OVER sub-buckets deserve more shadow tracking and possibly later human-review proposals. They do not justify Elite/Kelly changes now.

## 6. Research Lane Audit

**UNDER_ALIGNED_RESEARCH**

- Current shadow candidate lane: 4 total, 0 graded, all pending.
- Broader full-market shadow UNDER history: 183 graded, 107-76-0, 58.5% hit rate, +4.5% ROI.
- Under visibility report: current slate had 144 qualified UNDERs, but only 9 reached full-market board and 0 reached Near-Elite, incubator, shadow lane board, or Elite.
- Recommendation: more sample collection and visibility reporting. Stay paper-only. Do not promote UNDERs.

**COMBO_OVER_WEAK_POSITIVE_RESEARCH**

- Current shadow candidate lane: 12 total, 0 graded, all pending.
- Corroborating high-caution combo OVER buckets have weak positive signals:
  - player_points_rebounds over, high/conflicted HCO rejection: 34 graded, 58.8%, +10.1% ROI.
  - player_points_rebounds_assists over, high/conflicted HCO rejection: 21 graded, 66.7%, +23.0% ROI.
  - player_rebounds_assists over, high/conflicted HCO rejection: 21 graded, 66.7%, +18.6% ROI.
- Recommendation: keep shadow-only. Build a larger controlled sample before any proposal.

**INCUBATOR_RESEARCH**

- Incubator history: 6 total, 1 graded, 0-1, -100.0% ROI.
- Shadow lane incubator rows: 3 total, 1 graded, 0-1, -100.0% ROI.
- Recommendation: stay paper-only. Do not promote.

**Near-Elite candidates**

- Recent no-bet funnel loaded 25 near-elite rows through `2026-05-28`; `2026-05-30` added 8 near-elite review rows.
- Shadow candidate lane near-elite: 16 total, 8 graded, 5-3, 62.5% hit rate, +5.9% ROI.
- Recommendation: manual-review candidate lane later, not automatic production. Needs at least 20-50 more graded rows and CLV/control checks.

**Which lanes deserve more sample collection?**

- UNDER_ALIGNED_RESEARCH
- COMBO_OVER_WEAK_POSITIVE_RESEARCH
- NEAR_ELITE_RESEARCH
- Low-caution/context-aligned UNDER shadow buckets

**Which lanes deserve manual-review proposals later?**

- NEAR_ELITE_RESEARCH, if the next 20-50 graded rows preserve positive ROI and quality.
- Specific combo OVER high-caution sub-buckets only as human-approved shadow rule proposals, never automatic staking.

**Which lanes should stay paper-only?**

- INCUBATOR_RESEARCH
- HIGH_CAUTION_OVER_DO_NOT_PROMOTE
- All UNDER lanes until explicit approval changes policy
- Unsupported markets and combo Kelly-ineligible markets

## 7. Core Brain Change Assessment

| Possible change class | Assessment | Reason |
| --- | --- | --- |
| NO_CORE_CHANGE | Recommended | Core gates are mostly protecting bankroll |
| COLLECT_MORE_DATA | Recommended | Positive lanes have low sample or policy limits |
| SHADOW_ONLY_RULE_EXPERIMENT | Recommended | Good fit for UNDER visibility, near-elite, and selected combo buckets |
| MANUAL_REVIEW_RULE_PROPOSAL | Later | Only after stronger samples and CLV/control evidence |
| ELITE_GATE_PROPOSAL_REQUIRES_APPROVAL | Not now | No current evidence supports Elite loosening |
| KELLY_GATE_PROPOSAL_REQUIRES_APPROVAL | Not now | Combo/Kelly and bankroll-facing gates should remain untouched |

No changes should be applied to Elite, Kelly, final decision, thresholds, or UNDER promotion.

## 8. Evolving System Design Recommendation

Recommended design path:

1. **Learning report only** now.
2. **Shadow adaptive rules engine** only after the learning report is stable and read-only.
3. **Human-approved calibration proposals** later, with explicit approval and no automatic production updates.

Do not build automatic production rule updates. CourtVision is bankroll-facing, and automatic rule mutation would be the wrong safety posture.

Recommended next design:

- Build a `Learning Brain Report` that reads histories and runtime reports and emits:
  - stable bucket performance tables,
  - sample-quality flags,
  - data-quality warnings,
  - shadow-only watchlists,
  - manual-review candidates,
  - explicit "no core change" recommendations.
- Keep it reporting-only and artifact-only.
- Add no production hooks.

## 9. Data Quality Audit

| Area | Finding | Risk |
| --- | --- | --- |
| Missing histories | Requested core histories exist locally. Histories and runtime outputs are ignored/untracked by git. | Acceptable for audit, but commit cannot preserve raw evidence |
| Duplicate rows | `incubator_history.csv` has 2 exact duplicate extra rows; `shadow_candidate_lane_history.csv` has 3 candidate-key duplicates | Can inflate tiny research samples |
| Stale pending rows | Shadow candidate lane has 7 pending rows from `2026-05-28`; market shadow has 60 pending pre-`2026-05-30`; paper Kelly has 25 pending pre-`2026-05-30` | Weakens lane conclusions |
| `2026-05-30` pending | Incubator has 5 pending, shadow lane has 26 pending, market shadow has 50 pending, paper Kelly has 45 pending | Expected because `2026-05-30` was not graded |
| Unsupported markets | `2026-05-30` dropped 4 active `player_steals`; recent no-bet funnel had 36 unsupported active-market drops | Correctly blocked |
| Missing actual values | 0 missing actual values on graded rows across audited histories | Good |
| Missing player names | 0 missing player names across audited histories | Good |
| Source artifact date mismatch | 0 mismatches in audited histories with `source_artifact_date` | Good |
| Void rows | Market shadow has 874 void rows, paper Kelly has 357 void rows, real pick history has 11 void rows | Use hit/ROI only on hit/miss/push graded rows |
| Low sample sizes | Incubator, shadow candidate lane categories, near-elite, and current UNDER_ALIGNED_RESEARCH are all small | Major overfitting risk |
| CLV coverage | Safe-action report shows 0 CLV available rows for many historical buckets | Do not promote without price-quality validation |
| Overfitting risk | Positive combo buckets have 21-34 graded rows and many pending rows | Keep shadow-only |

Data quality does not block the overall verdict, because the broad high-caution OVER and UNDER shadow signals have enough graded rows to be directionally useful. It does block production promotion of narrower lanes.

## 10. Final Recommendation

**Build Learning Brain Report next.**

Do not change core gates yet. Do not change Elite logic, Kelly logic, final decision logic, thresholds, high-caution OVER gates, combo Kelly eligibility, or UNDER promotion policy.

Promote only these to expanded shadow experiment, not production:

- UNDER_ALIGNED_RESEARCH visibility and outcome tracking.
- NEAR_ELITE_RESEARCH manual-review tracking.
- Selected high-caution combo OVER sub-buckets as shadow-only watchlists:
  - player_points_rebounds over, high/conflicted HCO rejection.
  - player_points_rebounds_assists over, high/conflicted HCO rejection.
  - player_rebounds_assists over, high/conflicted HCO rejection.

Buckets that should stay blocked:

- Broad OVERs.
- High-caution OVERs.
- Context-conflicted candidates.
- Same-opponent warning candidates.
- Low-quality candidates.
- Unsupported markets.
- Identity-conflict candidates if active row exposure appears.
- Incubator rows.
- Combo markets for real Kelly.

Production promotion candidates:

- None.

Recommended next phase:

**Phase 6A.1: Learning Brain Report, reporting-only.** It should read existing histories and artifacts, write a Markdown/CSV/JSON learning report, and produce no betting outputs or history modifications.

Safety confirmations:

- Betting logic changed: no.
- Elite logic changed: no.
- Kelly logic changed: no.
- `final_decision` changed: no.
- Thresholds changed: no.
- UNDERs promoted: no.
- `pick_history.csv` modified: no.
- `2026-05-30` graded: no.
- Boards regenerated: no.
- `run_today.bat` run: no.
- Optional pytest suite run: no, because this was a report-only audit with no code changes.
