# Sports Expansion Plan

## Shared contract

Every sport configuration declares:

- canonical sport name and active-season status;
- normalized supported prop markets;
- data and odds provider status;
- projection model status.

New integrations should emit normalized records at the sport boundary. Provider
payloads should not leak into core hit-rate, confidence, or reporting logic.

## WNBA first

The WNBA placeholder uses the same 50% last-five, 35% last-ten, and 15% season
blend as the extracted NBA baseline. PRA is built from points, rebounds, and
assists before blending. Until providers are connected, results are marked
`is_placeholder=True` with intentionally low data quality.

Required integration work:

- schedule, teams, players, box scores, and availability;
- player prop odds with timestamps and book identity;
- stable player/team identity resolution;
- WNBA-specific minutes, rotation, pace, and role features;
- shadow grading and CLV capture.

## MLB framework

Supported markets are hits, total bases, runs, RBIs, home runs, strikeouts, and
pitcher outs. The model contract reserves these Statcast-style inputs without
applying them yet:

- handedness matchup;
- pitcher matchup;
- ballpark factor;
- weather factor;
- recent form.

MLB needs lineup confirmation, probable-pitcher changes, doubleheader identity,
and postponement handling before recommendations can be trusted.

## NFL framework

Supported markets are passing yards, rushing yards, receiving yards, receptions,
touchdowns, completions, and interceptions. The model contract reserves:

- defensive matchup;
- snap share;
- target share;
- usage trend;
- injury status.

NFL research must preserve point-in-time depth charts and injury designations to
avoid backtest leakage.

## NHL framework

NHL is registry-only in this phase. Its initial market vocabulary is points,
goals, assists, shots on goal, and saves. Projection and provider work should
follow WNBA, MLB, and NFL validation.

## Integration sequence

For each sport:

1. Add provider adapters and fixtures without changing provider priority.
2. Normalize markets, names, times, lines, odds, and status fields.
3. Build historical datasets and data-quality diagnostics.
4. Calibrate sport- and market-specific projections in shadow mode.
5. Add grading and CLV coverage for all result states.
6. Review confidence weights and tier performance empirically.
7. Request explicit approval before changing Kelly or bankroll-facing selection.

## Safety invariants

- Existing NBA entrypoints and imports remain valid.
- Placeholder models never present themselves as live or wager-ready.
- Missing data lowers quality; it does not silently become a positive signal.
- Core recommendation tiers do not override NBA production gates.
- Provider failures must fail closed for bankroll-facing output.
