# CourtVision Invariants

These rules are non-negotiable unless the user explicitly approves a policy change.

## Safety Gates

- Completed, live, in-progress, postponed, cancelled, locked, or otherwise non-bettable games cannot enter Elite or Kelly.
- Unknown game status without a usable future game datetime cannot enter Elite or Kelly.
- Stale odds cannot enter Elite or Kelly.
- Out-of-game team context must be suppressed before it can influence candidate context.
- The strong `player_points` OVER guard stays active.
- Kelly must only run on validated Elite rows.
- Recalibration remains opt-in or shadow mode unless explicitly enabled.
- Full Market may include diagnostic rows, but Elite and Kelly must stay safety-gated.
- Never loosen thresholds, gates, guards, or scoring rules to make tests pass.

## Test And Fixture Policy

- If production behavior is correct and tests fail, update fixtures or assertions to reflect current policy.
- Fixtures that require Elite candidates must be clearly valid under current policy.
- Prefer future scheduled game datetimes and fresh odds for positive-path betting fixtures.
- Use `player_points` UNDER fixtures with strong positive side edge when avoiding the strong OVER guard.
- Negative-path tests should assert rejection reasons, not weaken gates.

## Runtime Policy

- Game status and datetime enrichment must preserve enough metadata to classify scheduled, locked, live, and final games.
- Completed games must remain blocked as final or equivalent non-bettable reasons.
- Odds freshness must remain enforced for Elite and Kelly eligibility.
- Diagnostics should explain gate outcomes without changing outcomes.

