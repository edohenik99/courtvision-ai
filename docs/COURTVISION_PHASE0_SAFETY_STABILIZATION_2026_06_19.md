# CourtVision Phase 0 Safety Stabilization Verification

Date: 2026-06-19  
Verification verdict: **Clean.**

The bankroll-safety controls pass, the MLB HR CLI presentation now uses neutral research/product language, and the full test suite is green. The prior CLI language issue was fixed without changing machine-readable safety metadata, normalized sample provider data, or NBA behavior. No Phase 1 work began during this verification.

## What changed in Phase 0

- Centralized immutable MLB research-safety metadata.
- Forced MLB HR inputs, assessments, and normalized odds candidates to identify themselves as unapproved research.
- Replaced production recommendation tiers with research labels: `Research Watchlist`, `Candidate`, and `Not Selected`.
- Removed estimated/fair probability and sizing fields from serialized MLB HR assessments.
- Added pre-sizing and pre-artifact hard blocks for MLB, research-mode, unapproved, and sample rows in the existing staking runner.
- Preserved the keyless MLB sample provider.
- Replaced the remaining MLB HR CLI presentation terms with neutral research/product language and sample-only source display labels.
- Added regression coverage for MLB safety boundaries and NBA compatibility.

## Files touched

The effective Phase 0 safety implementation and its focused regression coverage were inspected in:

- `courtvision/sports/mlb/research_safety.py`
- `courtvision/sports/mlb/hr_prop_engine.py`
- `courtvision/sports/mlb/hr_report.py`
- `courtvision/sports/mlb/adapters/odds_api_provider.py`
- `scripts/run_kelly_stakes.py`
- `tests/test_mlb_research_safety.py`
- `tests/test_mlb_hr_prop_engine.py`
- `tests/test_mlb_hr_odds_provider.py`
- `tests/test_nba_backwards_compatibility.py`

NBA compatibility was also inspected in:

- `courtvision/projection/base_model.py`
- `courtvision/sports/nba/__init__.py`
- `courtvision/sports/nba/runtime.py`
- `courtvision/sports/nba/projection.py`

This final verification pass changed only `courtvision/sports/mlb/hr_report.py`, `tests/test_mlb_hr_prop_engine.py`, and this document. The working tree was already dirty and much of the inspected Phase 0 code is untracked, so Git cannot reliably attribute every existing file to a particular prior phase or author.

## Safety guarantees added

MLB HR assessments serialize the required safety contract:

```text
mode="research"
eligible_for_betting=False
kelly_eligible=False
betting_approval_status="research_only_not_betting_approved"
no_betting_reason="MLB HR mode is unvalidated research-only. Historical training, calibration, EV validation, and promotion approval are required before betting use."
```

The dataclass safety fields are `init=False`, and assessment serialization reapplies the centralized safety payload. Callers therefore cannot construct or serialize an MLB assessment as betting-eligible through ordinary dataclass arguments or replacement.

The staking path applies two defensive boundaries:

1. Individual MLB/research rows are rejected before sizing, with zero eligibility, stake fraction, stake amount, and expected value.
2. MLB/research/sample rows are removed before artifact construction; an input containing only those rows exits without creating an output artifact.

The focused regression test also confirms that approval-like provider data cannot override the categorical `sport=MLB` block, while an explicit NBA row retains its existing staking behavior.

## Verification findings

### Passed

- MLB assessment serialization contains every required research-only field and the unvalidated-model explanation.
- Serialized MLB HR assessments contain neither estimated/fair probability fields nor stake/unit fields.
- MLB/sample rows cannot produce staking artifacts through `scripts/run_kelly_stakes.py`.
- The sample provider runs without API keys.
- The rendered keyless sample CLI contains none of the forbidden presentation terms.
- Sample-mode source names render as `Sample Source A`, `Sample Source B`, and `Sample Source C` without changing normalized provider/source data internally.
- The full suite is green.
- The canonical NBA runtime (`courtvision.engine.CourtVisionPro`) is unchanged. The NBA projection implementation was moved behind compatibility exports with its calculation preserved verbatim; regression tests confirm the legacy and sport-module imports resolve to the same objects.

### Prior CLI language issue: fixed

The prior sample CLI banner used wagering and sizing presentation terminology, and the sample slate exposed a source proper name containing a forbidden token. The renderer now emits:

```text
Sample data | Research-only | No Actionable Recommendation | Not Production Approved
Research output only; excluded from production approvals.
```

`test_cli_report_smoke` now asserts the neutral banner and sample source labels, then checks the captured human-facing output for every forbidden token. Existing serialization tests continue to prove that the internal safety field names and values remain present and unchanged.

## Commands run

### Keyless MLB sample command

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Exit code: `0`

Exact output:

```text
CourtVision MLB HR Research Watchlist — 2026-06-19
Sample data | Research-only | No Actionable Recommendation | Not Production Approved
Research output only; excluded from production approvals.
==============================================================
1. Example Player
   Research Score: 89/100 | Status: Research Watchlist
   Data Quality: Sample data
   Price reference: +365 | Source: Sample Source A
   Matchup: CHC vs STL — Example Player vs Example Pitcher
   Venue: Wrigley Field
   Key reasons: Elevated recent barrel profile; Positive pitch-type matchup; Wind blowing out; Favorable HR park factor; Elevated hard-hit rate
2. Sample Slugger
   Research Score: 73/100 | Status: Candidate
   Data Quality: Sample data
   Price reference: +310 | Source: Sample Source B
   Matchup: NYY vs BOS — Sample Slugger vs Sample Starter
   Venue: Yankee Stadium
   Key reasons: Elevated recent barrel profile; Positive pitch-type matchup; Favorable HR park factor; Elevated hard-hit rate; Impact-level max exit velocity
3. Demo Batter
   Research Score: 49/100 | Status: Not Selected
   Data Quality: Sample data
   Price reference: +440 | Source: Sample Source C
   Matchup: SEA vs HOU — Demo Batter vs Demo Pitcher
   Venue: T-Mobile Park
   Key reasons: Elevated recent barrel profile; Elevated hard-hit rate
```

### Full suite

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Exit code: `0`

Exact result:

```text
2771 passed, 31 xfailed in 257.88s (0:04:17)
```

## Remaining risks

- The live-provider renderer now uses the same neutral safety banner, but live source-name display policy remains separate from this sample-only Phase 0 fix.
- The MLB HR score is an uncalibrated ranking signal. It has no historical training, probability calibration, out-of-sample validation, EV validation, or promotion approval.
- The working tree contains extensive pre-existing modified and untracked files. Because the Phase 0 files are largely untracked, the current Git diff cannot provide reliable provenance or a clean phase boundary.
- NBA numeric behavior is supported by the full suite and compatibility identity tests, but the current tree does contain a structural projection relocation. This is not a runtime rewrite, yet it should be reviewed as an existing architectural change before Phase 1.
- The suite reports 31 expected failures. They do not fail this run, but each remains deferred coverage or behavior by definition.

## Why MLB HR remains research-only

The MLB HR model currently produces only an uncalibrated research ranking. It has not completed historical training, calibration, out-of-sample validation, EV validation, or explicit production promotion. Market prices are context inputs, not proof of a calibrated fair probability or actionable edge. MLB must therefore remain categorically excluded from eligibility and sizing paths even when provider data resembles an approved production row.

## Commit status

No commit was made. No commit was requested, and all pre-existing dirty-tree changes were preserved.

## Phase 1 Proposed Plan

Phase 1 may start only after this clean verification. This document records that gate as satisfied; no Phase 1 implementation was included in this work.

1. Define a typed sport/plugin registry with explicit plugin identity, supported markets, execution mode, and capabilities.
2. Introduce a capability-based provider registry so providers advertise supported operations without being coupled to sport runtime internals.
3. Define a normalized odds quote contract for sport, event, market, selection, line, price, source, and timestamps while preserving raw-source traceability.
4. Make research and betting modes separate explicit types and enforce default-deny boundaries at serialization, selection, and sizing entry points.
5. Wrap the existing NBA runtime as a compatibility plugin. Preserve its internals and legacy imports; add contract and golden-regression tests instead of rewriting NBA behavior.
6. Register MLB as a research-only plugin with no betting or sizing capability. Preserve the keyless sample provider and the existing categorical staking block.
7. Land the contracts and adapters incrementally, with targeted compatibility tests first and the full suite required before each boundary is adopted.
