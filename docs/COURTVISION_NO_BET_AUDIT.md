# CourtVision AI NBA Betting Analysis Repository Audit
## Investigation: Why is the System Repeatedly Producing NO_BET Slates?
**Date of Audit:** 2026-05-28  
**Repository Path:** `C:\dev\Sport_Project1`  
**Main Runner:** `courtvision_ai.py`  
**Daily Runner:** `run_today.bat` / `run_today.ps1`  
**Status:** Complete  

---

## 1. Executive Summary

### Direct Answer
The system is **NOT broken**, but it is **severely over-gated at the postseason safety layer, under-sampled in high-performance shadow markets, and crippled by an unclean player baseline configuration**.

The persistent `NO_BET` slates are the direct result of a perfect storm:
1. **The Postseason OVER Blanket Block:** During the playoffs, the context safety engine (`game_context.py`) systematically classifies postseason games as `supports_under`. Combined with playoff-caliber opponent defensive ratings (DRtg ≤ 112.0 or Net Rating ≥ 5.0), this forces the overall matchup context signal to `supports_under` for almost all slates. Consequently, every points-over prop is flagged as `conflicted` with a `high` caution level. A late hard-gate in `courtvision_ai.py` (`_apply_elite_context_safety_gate`) removes 100% of these high-caution points-over selections from the Elite board.
2. **Hard-locked Market Scope:** While high-performing under selections (e.g. rebounds under, assists under, and combo under) are showing excellent historical hit rates (64% to 75%) and positive ROIs, both the Elite board and the Kelly staking script are hard-locked to `player_points` only.
3. **Severe Candidate Funnel Drain:** An unclean `player_baselines.csv` containing multiple historical stint rows per player (946 rows for 672 unique players) causes the player identity resolver to fail on team mismatches. This silently rejects **222 valid candidate rows** at the very first funnel gate, heavily draining potential points-under or low-caution candidate volume.

### Empirical Verdict
The caution of the **High-Caution OVER** gate is **100% empirically justified**. Performance logs in `promotion_readiness_report_2026-05-28.txt` show that `player_points/over/conflicted/high` picks have a dismal **41.53% hit rate and a -22.79% ROI** across 118 graded picks. Lowering this gate to force bets would lead to immediate bankroll drawdowns. However, the system's absolute lock on points-only markets, coupled with baseline data pollution, creates an overly restrictive architecture that prevents highly profitable, well-calibrated under picks from being staked.

---

## 2. Candidate Funnel (Slate: 2026-05-28)

The table below traces how candidates were filtered down across the entire pipeline during the `2026-05-28` slate:

| Funnel Stage | Row Count | Source / Function | Description / Notes |
| :--- | :--- | :--- | :--- |
| **Raw Input Odds** | ~6,168 | Odds Feed | Total starting odds feed rows. |
| **Initial Rejections** | 6,044 | `predict_pipeline.py` | Initial hard filters (minutes, edge direction, unsupported markets). |
| **Qualified Pool** | 62 | `board_diagnostics.json` | Candidates passing base mathematical filters. |
| **Full-Market Board** | 58 | `full_market_board_2026-05-28.csv` | Diagnostic-only board (removes steals/blocks and mileston props). |
| **Near-Elite Review** | 9 | `near_elite_review_2026-05-28.csv` | Points-over rows meeting elite thresholds but blocked by context safety. |
| **Elite Board** | 0 | `elite_board_2026-05-28.csv` | Empty after the late context safety gate. |
| **Kelly Eligible** | 0 | `run_kelly_stakes.py` | Empty. Staking is skipped entirely. |

### Top Rejection Reasons (Qualified & Pre-Qualified Funnel)
1. **`reject_negative_edge_direction` (3,450 rows):** Projections aligned against the sportsbook price direction.
2. **`unsupported_projection_market` (1,198 rows):** Non-points props or unrecognized markets filtered from the core staking lane.
3. **`market_gate_minutes_lt_24` (1,053 rows):** Players projected for less than 24 minutes, signaling unstable roles.
4. **`player_identity_validation` (222 rows):** System-wide rejections due to team mismatches against stale baseline stint rows.
5. **`elite_reject_context_high_caution_over` (43 rows):** Final late-stage context block excluding points-over props in high-caution matchups.
6. **`elite_points_risk_guard` (9 rows):** Hard points-only guard blocking injury-driven low-line overs or weak-role unders.

---

## 3. Root Cause Ranking

### [CRITICAL] Stale/Duplicate Stints in `player_baselines.csv`
* **Severity:** Critical (Silent Candidate Drain)
* **Confidence:** High
* **Evidence:** The initial validation gate threw out **222 rows** due to `player_identity_validation`. The baseline file `outputs/model/player_baselines.csv` contains 946 rows for only 672 unique player IDs. Stale stint rows for past teams (e.g., De'Aaron Fox having rows for both SAC and SAS, Kyle Anderson having 5 stint rows) mismatch with active provider teams, causing the resolver to fail validation.
* **Files Involved:** 
  - `courtvision/context/player_identity.py` (`CanonicalPlayerIdentityResolver.annotate_record()`)
  - `outputs/model/player_baselines.csv`
* **Recommended Fix:** Clean/deduplicate `player_baselines.csv` by keeping only the row corresponding to each player's most recent active team stint. Update `player_identity.py` to filter historical baseline rows against active rosters before asserting mismatches.

### [HIGH] Blunt Postseason Defensive Context Gate
* **Severity:** High (Systematic betting blackout during playoffs)
* **Confidence:** High
* **Evidence:** The elite safety gate rejected 100% of points-over candidates (43 rows) under the `elite_reject_context_high_caution_over` flag. Postseason games are classified as `supports_under` by default, and playoff defensive ratings (≤ 112.0) also force `supports_under`.
* **Files Involved:**
  - `courtvision/context/game_context.py` (`_playoff_signal()`, `_defense_signal()`, `_overall_signal()`)
  - `courtvision_ai.py` (`_apply_elite_context_safety_gate()`)
  - `courtvision/runtime_audit.py` (`elite_context_rejection_reason()`)
* **Recommended Fix:** Refine `_playoff_signal()` and `_defense_signal()` to not act as blunt blanket filters. Allow postseason overs when matchup-specific pace projections are extremely high (e.g. pace ≥ 102.0) or when rest/injury advantages override general defensive ratings.

### [HIGH] Hard-locked Market Restrictions
* **Severity:** High (Blocks high-performance growth)
* **Confidence:** High
* **Evidence:** Daily summary says: "elite board and Kelly remain locked to player_points only". `scripts/run_kelly_stakes.py` explicitly drops all non-points props at line 451.
* **Files Involved:**
  - `scripts/run_kelly_stakes.py` (Lines 451–453)
  - `courtvision_ai.py` (Pipeline configuration properties)
* **Recommended Fix:** Implement a structured promotion path allowing well-calibrated, high-performance shadow markets (e.g., rebounds under and assists under) to graduate into controlled, fractional Kelly sizing lanes.

---

## 4. No-Bet Repeat Risk

If left unchanged, the system is **guaranteed to continue producing NO_BET days** for the remainder of the postseason due to the mathematical circularity of its rules:
1. Every postseason game has `postseason = True` $\rightarrow$ `_playoff_signal` is always `"supports_under"`.
2. Almost all playoff opponents have defensive ratings $\le 112.0$ or Net Ratings $\ge 5.0$ $\rightarrow$ `_defense_signal` is always `"supports_under"`.
3. The overall signal count always leans under $\rightarrow$ `_overall_signal` is always `"supports_under"`.
4. The model's projection bias naturally outputs far more `OVER` selections than `UNDER` selections (47 overs vs 15 unders in the qualified pool).
5. Every point-over candidate becomes `conflicted` with a `high` caution level, triggering the late context safety gate and emptying the Elite board.
6. Non-points selections (which include the high-performing under options) are systematically deleted by the market lock.

The combined effect of these gates is a **100% probability of an empty Elite board on every postseason slate**.

---

## 5. Source Identity Conflict Analysis

### Likely Cause
`CanonicalPlayerIdentityResolver` queries all historical stints from `player_baselines.csv`. Because players who have moved teams have multiple historical records (e.g. De'Aaron Fox listed with SAS and SAC, Kyle Anderson with 5 stints), `len(identity.baseline_team_abbrs) > 1` triggers. When the current slate candidate is processed, if the candidate's baseline lookup maps to an older SAS stint while the odds feed maps to SAC, a mismatch occurs.

### Risk
1. **Initial Funnel Poisoning:** This is not just a diagnostic warning. It is a **hard blocker** that rejected **222 rows** inside `board_diagnostics.json` under `player_identity_validation`. This drops otherwise high-quality points-under or points-over candidates before they ever reach the Qualified Pool.
2. **Telemetry Pollution:** It produces 222 conflicts that clutter diagnostic logs and operator summaries, making real data feed errors difficult to identify.

### Recommended Repair Path
Create a preprocessing step or script that trims `player_baselines.csv` to ensure a strict $1:1$ mapping between `player_id` and their single, most recent active team stint. Modify `player_identity.py` to drop historical records representing past stints before executing conflict detection.

---

## 6. Gate Calibration Analysis

### Gates to NOT Weaken
* **The High-Caution OVER Gate:** The empirical performance in `promotion_readiness_report` validates this gate entirely. Conflicted overs with high caution have a **41.53% hit rate and -22.79% ROI**. Weakening this gate to generate volume will result in substantial bankroll drawdowns.

### Gates to Calibrate (Too Strict)
* **The Points-Only Market Lock:** Non-points shadow markets are showing outstanding calibration. Keeping them completely locked is a severe growth drag.
* **The Graded Sample Floor:** The requirement of 20 graded rows in specific, narrow sub-buckets is highly restrictive for under-sampled markets that are displaying positive performance trends.

### The Incubator Lane Alternative
Instead of weakening the Elite threshold or forcing bets, introduce a dedicated **Incubator Board / Controlled Test Lane**:
* Near-Elite review rows (which met all edge, confidence, and quality metrics but were blocked by context) should be routed to this Incubator Board.
* These rows should be tracked for paper trading or placed with **micro-staking** (e.g. 0.1% Kelly cap) to verify if high-edge entries can overcome postseason context drags without putting the main bankroll at risk.

---

## 7. Market Promotion Analysis

Empirical results from the `promotion_readiness_report_2026-05-28.txt` highlight which shadow markets are ready for promotion:

### Closest to Promotable
1. **`player_rebounds/under/aligned/low`**
   - *Metrics:* Graded=17, Hit Rate=64.71%, ROI=+2.13%
   - *Status:* Blocked only by the sample floor (17 < 20).
2. **`player_points_rebounds_assists/under/aligned/low`**
   - *Metrics:* Graded=15, Hit Rate=66.67%, ROI=+19.70%
   - *Status:* Blocked only by the sample floor (15 < 20).
3. **`player_points/over/unknown/unknown`**
   - *Metrics:* Graded=38, Hit Rate=60.53%, ROI=-10.59% (high vig/odds drag)
   - *Status:* Review-ready.

### Should Remain Shadow-Only
1. **All Combo OVER Markets (e.g. Points+Rebounds+Assists Over):**
   - *Metrics:* Conflicted overs show hit rates from 34% to 50% with massive negative ROIs (up to -36.9%). These must remain strictly locked.

### Required Promotion Conditions
To safely promote a market from shadow to a candidate staking lane:
* **Minimum Graded Sample:** $\ge 20$ graded rows for aligned under props; $\ge 30$ graded rows for all others.
* **Minimum Hit Rate:** $\ge 55\%$ over the historical sample.
* **Role Stability Guard:** Player role must not be classified as `volatile` or `highly volatile` inside `player_role_stability_<DATE>.json`.

---

## 8. Recommended Fix Order (Roadmap)

### Phase 1: Must-Fix Reliability & Data Integrity (Risk: Low | Impact: High)
* **Action:** Deduplicate `outputs/model/player_baselines.csv` to resolve the 222 player identity conflicts. 
* **Benefit:** Recovers the massive candidate drain, restoring points-under and low-caution candidates back into the staking funnel.

### Phase 2: Postseason Gate Calibration (Risk: Medium | Impact: High)
* **Action:** Tune `_playoff_signal()` inside `game_context.py` to dynamically evaluate pace and matchup rating instead of a blunt "supports_under" return.
* **Benefit:** Restores selective points-over candidates in high-scoring postseason matchups while maintaining protective blocks on slow, grind-out series.

### Phase 3: Controlled Growth Lane / Incubator Design (Risk: Low | Impact: Medium)
* **Action:** Programmatically establish the **Incubator Board** to route and track Near-Elite review rows on paper or micro-stakes.
* **Benefit:** Allows the operator to validate the performance of high-edge, gated selections without risking standard bankroll sizes.

### Phase 4: Market Promotion (Risk: Low | Impact: High)
* **Action:** Safely promote `player_rebounds` under and `player_assists` under into the active candidate lane by updating `run_kelly_stakes.py` to allow these specific markets.
* **Benefit:** Expands the system's active portfolio into its most profitable and highly-calibrated sectors.

### Phase 5: Kelly Expansion (Risk: Medium | Impact: Medium)
* **Action:** Expand Kelly exposure caps on newly promoted markets after they complete a 30-slate tracking phase with a positive ROI.

---

## 9. Test Plan

Following the implementation of the baseline cleanup and gate calibrations:
1. **Targeted Unit Tests:**
   Run the following suite to ensure that core gates, context selections, and reports are fully sane and do not regress:
   ```bash
   py -3.13 -m pytest -k "selection or elite or kelly or near_elite or high_caution or source_identity or runtime_outputs or quality_summary"
   ```
2. **Diagnostic Telemetry Audit:**
   Execute a test slate and inspect `board_diagnostics_<DATE>.json` to verify:
   - `player_identity_validation` rejection count drops to $0$.
   - `source_identity_conflict_count` in the resolver summary drops to $0$.
3. **Funnel Simulation:**
   Run a postseason pipeline simulation to verify that the Incubator Board is generated and populated with Near-Elite selections, and that under props for rebounds/assists bypass the points-only block safely.

---

## 10. Do Not Do List (Restricted Shortcuts)

> [!CAUTION]
> **To preserve betting safety and protect bankroll integrity, NEVER attempt the following shortcuts:**
> * **DO NOT blindly lower the Elite admission thresholds** (e.g. lowering edge to $\ge 1.0$ or quality to $\ge 40$) to force bets.
> * **DO NOT weaken the `elite_reject_context_high_caution_over` gate** for the main Elite board. Conflicted overs are a proven long-term loser.
> * **DO NOT bypass player identity validation** or allow player rows with unresolved team mismatches into staking. Letting data-poisoned rows through will lead to mispriced line stakes.
> * **DO NOT promote shadow combo OVER markets** to Kelly staking. Their hit rates remain deeply unprofitable.
