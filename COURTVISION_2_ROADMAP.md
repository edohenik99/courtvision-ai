# CourtVision 2.0 Roadmap

## Objective

Evolve the current NBA application into a reusable sports betting intelligence
platform without changing the proven NBA production path prematurely.

## Current foundation

- A case-insensitive registry declares NBA, WNBA, MLB, NFL, and NHL capabilities.
- Shared hit-rate and confidence engines are available to new sport modules.
- Recommendation tiers are Elite (85-100), Strong (75-84), Watchlist (65-74),
  and Pass (below 65).
- A provider-neutral CLV snapshot tracks open, current, and closing lines plus
  odds movement and whether the recorded pick beat close.
- NBA projection rules live under `courtvision/sports/nba/`; the former import
  path remains a compatibility shim.
- Existing NBA runtime, providers, selection gates, Kelly sizing, grading, and
  reports are unchanged.

## Delivery phases

### Phase 1: Foundation — complete

- Establish `courtvision/core/` and `courtvision/sports/` boundaries.
- Preserve NBA imports and command-line entrypoints.
- Add placeholder modules and contract tests.

### Phase 2: WNBA shadow research

- Connect schedule, roster, box-score, injury, and odds feeds.
- Normalize provider markets into the registry names.
- Backfill at least one complete season for hit-rate and calibration research.
- Run projections and recommendations in shadow mode only.
- Compare projection error and closing-line value before proposing promotion.

### Phase 3: MLB shadow research

- Add batter/pitcher identity mapping and probable-pitcher resolution.
- Ingest handedness, pitcher matchup, park, weather, and recent-form features.
- Split batter and pitcher projection families where their distributions differ.
- Validate postponements, doubleheaders, lineup status, and pitcher changes.

### Phase 4: NFL shadow research

- Add weekly schedule, roster, depth chart, snap, target, usage, and injury data.
- Model role and availability changes explicitly because weekly samples are small.
- Backtest by season and week without leaking future injury or depth-chart data.

### Phase 5: NHL research

- Define skater and goalie models separately.
- Add confirmed starter, line combination, special-teams, and opponent context.

## Promotion gates

No new sport should enter bankroll-facing output until all of the following are
documented and approved:

1. Provider coverage, freshness, identity matching, and fallback behavior.
2. Historical backtest and genuinely out-of-sample shadow results.
3. Calibration by market, direction, confidence tier, and line range.
4. Positive or defensible CLV over a meaningful sample.
5. Sport-specific grading, postponement, void, and correction handling.
6. Explicit review of Kelly eligibility and portfolio exposure rules.
7. Regression proof that the NBA daily workflow remains unchanged.

## Non-goals for the foundation

- Replacing the canonical NBA scoring or selection policy.
- Reusing basketball thresholds blindly for other sports.
- Enabling live wagers from placeholder projections.
- Selecting providers before coverage and licensing are reviewed.
