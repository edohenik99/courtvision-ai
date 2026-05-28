# CourtVision AI Phase 2 Matchup Context Gate Audit
## Analysis of the High-Caution OVER Matchup Context Safety Gate
**Date of Audit:** 2026-05-28  
**Repository Path:** `C:\dev\Sport_Project1`  
**Main Runner:** `courtvision_ai.py`  
**Status:** Complete  

---

## 1. Executive Summary

This audit evaluates the behavior and performance of the CourtVision Matchup Context safety gate (`elite_reject_context_high_caution_over` / `context_high_caution_over`) to determine if it is correctly protective or overbroad, causing repeated `NO_BET` slates during the NBA postseason. 

The primary findings indicate:
* **The gate is highly protective and empirically correct.** Graded historical records show that `player_points/over/conflicted/high` props have a hit rate of only **41.53%** and a disastrous **-22.79% ROI** over 118 graded trials. Bypassing or weakening this gate for standard Elite bets would lead to severe bankroll drawdowns.
* **The postseason logic is structurally rigid.** The current engine applies a blanket `supports_under` playoff signal to all postseason games. When combined with tight postseason defensive ratings (≤ 112.0), the engine forces a net `supports_under` signal on every points-over prop. On single-game slates, this leads to a total betting blackout.
* **A structured pathway is required.** Rather than weakening the Elite safety gate, the system should maintain its strict boundaries and introduce a selective **Incubator Board** to track and evaluate elite, low-fragility candidates that meet high edge and stability thresholds in playoff conditions.

---

## 2. Current NO_BET Root Cause

The immediate cause of the `NO_BET` decision on the 2026-05-28 slate is that the Elite board was completely emptied by the late matchup context gate. 

The detailed execution pathway shows how this occurred:
1. **Single-Game Slate:** The slate consisted of a single postseason matchup: `OKC @ SAS` (Game ID: `21713533`).
2. **Automatic Postseason Block:** Because the game has `postseason = True`, the context engine automatically sets `playoff_context_signal = "supports_under"` for all OVER candidates in `courtvision/context/game_context.py#L155`.
3. **Playoff Defensive Baseline:** SAS's opponent (OKC) has a defensive rating of 106.5. OKC's opponent (SAS) has a defensive rating of 110.4. Because both ratings are ≤ 112.0, the context engine flags `defense_context_signal = "supports_under"` for all candidates on both teams.
4. **Pace Override Failure:** The matchup pace is projected at 100.545, yielding `pace_context_signal = "supports_over"`. However, when counting votes across Pace (1 `supports_over`), Defense (1 `supports_under`), Playoff (1 `supports_under`), and Rest (neutral), the final vote is 2 to 1 in favor of under.
5. **Blanket Rejection:** This forces `overall_context_signal = "supports_under"` for all candidates. Every single points-over candidate is classified with `context_pick_alignment = "conflicted"` and `context_caution_level = "high"`.
6. **Funnel Block:** The late safety gate `elite_context_rejection_reason()` in `courtvision/runtime_audit.py` rejects 100% of these high-caution conflicted OVERs from the Elite board. With zero candidates surviving, the pipeline logs a `NO_BET` decision and skips Kelly staking.

---

## 3. High-Caution OVER Gate Location and Logic

The matchup context gate is calculated and enforced through a multi-step pipeline across three main files:

### A. Context Calculation: `courtvision/context/game_context.py`
* **Playoff Heuristic (Line 155):**
  ```python
  def _playoff_signal(postseason: Any) -> str:
      flag = _bool_or_none(postseason)
      if flag is None:
          return "insufficient_data"
      return "supports_under" if flag else "neutral"
  ```
  This automatically treats all postseason games as positive votes for under.
* **Defense Heuristic (Line 123):**
  An opponent defensive rating ≤ 112.0 or net rating ≥ 5.0 returns `"supports_under"`.
* **Pace Heuristic (Line 108):**
  A matchup pace ≥ 100.0 returns `"supports_over"`, and a matchup pace ≤ 97.0 returns `"supports_under"`.
* **Overall Signal Assembly (Line 162):**
  Counts non-neutral votes. If the count of `supports_under` is strictly greater than `supports_over`, it returns `"supports_under"`.
* **Alignment and Caution (Lines 175, 189):**
  If selection is `"over"` and the overall signal is `"supports_under"`, the pick alignment becomes `"conflicted"` and the caution level is set to `"high"`.

### B. Gate Enforcement: `courtvision/runtime_audit.py`
* **Elite Context Rejection (Line 1699):**
  ```python
  def elite_context_rejection_reason(row: Mapping[str, Any]) -> str | None:
      ...
      selection = str(row.get("selection", row.get("side", "")) or "").strip().lower()
      caution = str(row.get("context_caution_level", "") or "").strip().lower()
      alignment = str(row.get("context_pick_alignment", "") or "").strip().lower()
      if selection == "over" and caution == "high" and alignment == "conflicted":
          return ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER
      return None
  ```

### C. Pipeline Integration: `courtvision_ai.py`
During final board construction, `_apply_elite_context_safety_gate` queries the rejection reason. Blocked candidates are removed from the Elite board and routed to `outputs/runtime/operator/high_caution_over_watchlist_<DATE>.csv`.

---

## 4. Candidate Funnel from Full Market to Elite

The table below details the candidate funnel during the 2026-05-28 slate, illustrating where candidates were filtered out:

| Funnel Stage | Row Count | Source / Function | Description / Notes |
| :--- | :--- | :--- | :--- |
| **Qualified Pool** | 63 | `board_diagnostics.json` | Total candidates passing base mathematical filters. |
| **Full-Market Board** | 58 | `full_market_board.csv` | Diagnostic-only board after removing blocks/steals/milestones. |
| **High-Caution Watchlist** | 43 | `high_caution_over_watchlist.csv` | Points-over and combo-over candidates blocked by the context gate. |
| **Combo UNDER Watchlist** | 8 | `combo_under_watchlist.csv` | Under candidates blocked from active staking (points-only lock). |
| **Unsupported Markets** | 5 | `predict_pipeline.py` | 1 block prop and 4 steal props dropped. |
| **Near-Elite Review Lane** | 9 | `near_elite_review.csv` | Points-over rows meeting elite thresholds but blocked by context. |
| **Elite Board** | 0 | `elite_board.csv` | Empty after the context safety gate. |
| **Kelly Eligible** | 0 | `run_kelly_stakes.py` | Staking skipped completely due to empty Elite board. |

---

## 5. Breakdown of the 43 High-Caution OVER Rows

The 43 high-caution OVER candidates represent the entirety of the OVER prop market for the active players on both squads. Because SAS and OKC both have defensive ratings ≤ 112.0 and the game is in the postseason, every player OVER prop is locked out.

The distribution of the 43 blocked rows across markets is:
* **`player_points`:** 14 rows
* **`player_assists`:** 7 rows
* **`player_rebounds`:** 7 rows
* **`player_points_assists`:** 4 rows
* **`player_points_rebounds`:** 3 rows
* **`player_points_rebounds_assists`:** 4 rows
* **`player_rebounds_assists`:** 4 rows

Key players blocked across multiple categories include:
* **Jalen Williams (OKC):** 4 high-caution OVER rows, including `player_points` over 13.5 (edge=7.28) and `player_points_rebounds_assists` over 21.5 (edge=9.60).
* **De'Aaron Fox (SAS):** `player_points` over 14.5 (edge=6.18).
* **Keldon Johnson (SAS):** `player_points` over 6.5 (edge=7.57).
* **Devin Vassell (SAS):** `player_points` over 13.5 (edge=3.32).
* **Stephon Castle (SAS):** `player_points` over 16.5 (edge=3.13).

---

## 6. Breakdown of the 9 Near-Elite Blocked Rows

The table below lists the 9 points-over candidates that met the near-elite thresholds (`edge >= 3.0`, `confidence >= 0.70`, `quality_score >= 48`) but were completely blocked from Elite by the context gate:

| Player | Market | Side | Line | Projection | Edge | Conf. | Quality | Fragility | Role Stability | Rejection Cause |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Keldon Johnson** (SAS) | points | over | 6.5 | 14.07 | 7.57 | 0.715 | 71.00 | 85.0 (High) | mixed | playoff_defense_combined |
| **Jalen Williams** (OKC) | points | over | 13.5 | 20.78 | 7.28 | 0.800 | 76.59 | 60.0 (Med) | mostly stable | playoff_defense_combined |
| **De'Aaron Fox** (SAS) | points | over | 14.5 | 20.68 | 6.18 | 0.819 | 71.93 | 77.0 (High) | mixed | playoff_defense_combined |
| **Dylan Harper** (SAS) | points | over | 8.5 | 14.27 | 5.77 | 0.715 | 59.98 | 77.0 (High) | mixed | playoff_defense_combined |
| **Luguentz Dort** (OKC) | points | over | 4.5 | 8.42 | 3.92 | 0.750 | 51.82 | 60.0 (Med) | mixed | playoff_defense_combined |
| **Isaiah Hartenstein** (OKC) | points | over | 6.5 | 10.04 | 3.54 | 0.750 | 49.86 | 60.0 (Med) | mixed | playoff_defense_combined |
| **Devin Vassell** (SAS) | points | over | 13.5 | 16.82 | 3.32 | 0.826 | 54.54 | 77.0 (High) | volatile | playoff_defense_combined |
| **Stephon Castle** (SAS) | points | over | 16.5 | 19.63 | 3.13 | 0.826 | 53.57 | 77.0 (High) | volatile | playoff_defense_combined |
| **Julian Champagnie** (SAS) | points | over | 9.5 | 12.56 | 3.06 | 0.765 | 48.54 | 77.0 (High) | volatile | playoff_defense_combined |

### Analysis of Blocked Near-Elites:
* **Fragility and Volatility Risks:** Although these players met the edge and quality thresholds, six of the nine players have High Fragility (scores ≥ 77.0) due to low projected minutes, teammate absences, or high context caution.
* **Role Stability Failures:** Three players are flagged as volatile under Player Role Stability (Vassell, Castle, and Champagnie) due to high projection averages and injury role pressure. If the context gate did not block them, role stability and fragility checks would have marked them as high-risk.
* **Elite Candidates:** Jalen Williams, Luguentz Dort, and Isaiah Hartenstein represent the highest quality candidates with medium fragility (60.0) and stable/mixed roles. However, because they are OVER selections in a postseason game against a strong defensive opponent, they are still blocked.

---

## 7. Historical Performance of High-Caution OVER Buckets

The `promotion_readiness_report` from the 2026-05-28 slate lists the historical performance of high-caution conflicted OVERs across all markets:

* **`player_points/over/conflicted/high`:** Graded=118, Hits=49, Misses=69, Hit Rate=41.53%, ROI=-22.79%
* **`player_rebounds/over/conflicted/high`:** Graded=53, Hits=24, Misses=29, Hit Rate=45.28%, ROI=-16.68%
* **`player_assists/over/conflicted/high`:** Graded=46, Hits=21, Misses=25, Hit Rate=45.65%, ROI=-13.16%
* **`player_points_rebounds/over/conflicted/high`:** Graded=53, Hits=27, Misses=26, Hit Rate=50.94%, ROI=-4.90%
* **`player_points_assists/over/conflicted/high`:** Graded=50, Hits=21, Misses=29, Hit Rate=42.00%, ROI=-21.24%
* **`player_points_rebounds_assists/over/conflicted/high`:** Graded=40, Hits=20, Misses=20, Hit Rate=50.00%, ROI=-7.28%

These results confirm that across every single market, high-caution conflicted OVERs are highly unprofitable, with hit rates well below the 55% profitability threshold and negative ROIs across the board. 

---

## 8. Gate Correctness Assessment

### Is the Gate Correctly Protective?
**Yes.** The empirical data is clear. Over 118 graded trials, points-over high-caution props have failed to hit even 42% of the time, resulting in a -22.79% ROI. The gate is highly protective and performs its primary function of protecting the bankroll from unprofitable situations. Bypassing or weakening this gate for active Kelly staking would result in immediate capital losses.

### Is the Gate Overbroad?
**Yes.** While the gate is protective, it operates as a blunt constant during the playoffs. In a matchup with a fast pace projection (100.545), a postseason tag automatically votes for `supports_under` and, when combined with defensive ratings ≤ 112.0, completely overrides the fast pace. 

This creates a rigid postseason heuristic:
1. postseason = True $\rightarrow$ playoff signal is always `"supports_under"`.
2. playoff defenses are ≤ 112.0 $\rightarrow$ defense signal is always `"supports_under"`.
3. The overall signal count always leans under $\rightarrow$ overall signal is always `"supports_under"`.
4. 100% of OVER props become conflicted and are blocked.

### Conclusion
The gate must remain **fully intact** for the main Elite board and Kelly staking to preserve capital. However, the system's absolute lockout of these props on postseason slates is overbroad. The solution is to introduce a secondary, selective lane (the **Incubator Board**) to track high-edge, low-fragility outliers without placing active bankroll funds at risk.

---

## 9. Recommended Phase 2 Implementation Plan

To address the postseason lockout without weakening the core safety gates, the following Phase 2 fix order is proposed:

### Step 1: Minor Postseason Pace Tuning
* **Action:** Refine `_playoff_signal()` or `_overall_signal()` in `courtvision/context/game_context.py` to allow the postseason signal to be neutralized if the matchup pace is exceptionally high (e.g., projected matchup pace ≥ 102.0).
* **Reason:** Allows selective OVERs in high-tempo postseason environments while keeping grind-out series protected.

### Step 2: Establish the Incubator Board
* **Action:** Programmatically establish the Incubator Board to route near-elite conflicted OVERs that meet strict stability and margin-of-safety rules.
* **Reason:** Allows the system to track high-edge postseason selections on paper or micro-stakes (0.05% Kelly) to gather data.

### Step 3: Promote Non-Points Under Markets
* **Action:** Update `run_kelly_stakes.py` to promote `player_rebounds/under/aligned/low` (64.71% hit rate) and `player_points_rebounds_assists/under/aligned/low` (66.67% hit rate) from shadow to active staking once they cross the 20-graded-row sample floor.
* **Reason:** Expands the active betting portfolio into highly profitable and well-calibrated sectors.

---

## 10. Proposed Incubator-Only Criteria

Candidates routed to the Incubator Board must meet much stricter criteria to absorb the postseason context drag:
* **Edge Margin:** `edge >= 5.0` (to ensure a substantial margin of safety).
* **Confidence Floor:** `confidence >= 0.75`.
* **Quality Score Floor:** `quality_score >= 60`.
* **Fragility Cap:** `fragility_score <= 65` (excludes high-fragility players like Johnson, Fox, or Vassell).
* **Role Stability Guard:** Player role stability must be `stable` or `mostly stable` (excludes volatile profiles like Vassell or Castle).
* **Active Staking Cap:** Capped strictly at micro-staking (0.05% Kelly) or kept as paper-only observation.

---

## 11. Tests Required Before Future Implementation

Before any calibration changes are applied to the active pipeline, the following test verification suite must be run to ensure zero regressions:
1. **`tests/test_elite_context_gate.py`:** Confirm that regular-season high-caution conflicted OVERs are still blocked.
2. **`tests/test_game_context.py`:** Verify that the tuned pace/playoff calculations return correct signals and do not break historical outputs.
3. **`tests/test_near_elite.py`:** Confirm that near-elite candidates are correctly classified and separated from Elite.
4. **`tests/test_incubator_board.py` (New):** Verify that the incubator board is generated and populated only with eligible candidates who meet the edge, confidence, and role stability thresholds.

---

## 12. Do-Not-Change List

To preserve betting safety and protect bankroll integrity, **NEVER** alter the following parameters:
* **DO NOT** weaken or remove the `elite_reject_context_high_caution_over` gate on the main Elite board.
* **DO NOT** lower the base Elite thresholds (`edge >= 3.0`, `confidence >= 0.70`, `quality_score >= 48`) to artificially generate volume.
* **DO NOT** promote shadow combo OVER markets to active Kelly staking.
* **DO NOT** bypass player identity or quarantine checks.
