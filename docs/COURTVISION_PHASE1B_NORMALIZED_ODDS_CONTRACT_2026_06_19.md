# CourtVision Phase 1B: Normalized Odds Quote Contract

Date: 2026-06-19

## What Was Added

Phase 1B adds an immutable, sport-agnostic normalized odds quote contract in
`courtvision/core/odds.py`:

- `NormalizedOddsQuote`
- `OddsMarketIdentity`
- `OddsSelection`
- `OddsSourceMetadata`
- `OddsFreshnessStatus`
- American/decimal odds validation and conversion helpers
- Conservative quote freshness helpers

The types and helpers are also exported from `courtvision.core`.

## Why This Is Contract-Only

The new module defines a shared data boundary. It does not select a provider,
fetch data, route a sport runtime, calculate model value, approve a selection,
or size a wager. Existing provider-specific structures continue to work.

No provider registry, new provider, live API call, historical training path,
NBA runtime migration, MLB scoring change, or bankroll/Kelly change was added.

## Normalized Quote Fields

Market identity:

- `sport`
- `league`
- `event_id`
- `event_date`
- `home_team`
- `away_team`
- `market_type`

Selection:

- `selection_name`
- `selection_id` when available
- `line` when applicable

Price:

- `american_odds`
- `decimal_odds`
- `implied_probability`

Source and provenance:

- `sportsbook` / `source`
- `provider`
- `region` when available
- `mode`: `production`, `research`, or `sample`
- `source_type`: `live`, `sample`, `manual`, `mock`, or `historical`
- `raw_provider_market_id` when available
- `raw_event_id` when available
- `data_quality`

Timestamps and state:

- `quote_timestamp`
- `collected_at`
- `event_start_time` when available
- `is_live` when available

Approval safety:

- `eligible_for_betting`, default `False`
- `kelly_eligible`, default `False`
- `approval_status`, default `"not_approved"`

The nested structures are immutable. `NormalizedOddsQuote` exposes read-only
convenience properties for the normalized identity, selection, and source
fields.

## Conversion and Validation Rules

- American prices accept integer values or signed integer strings.
- Zero is invalid.
- Values between `-99` and `+99` are invalid American prices.
- Boolean, floating-point, empty, and non-numeric American prices are invalid.
- Positive prices use `1 + american / 100` for decimal odds.
- Negative prices use `1 + 100 / abs(american)` for decimal odds.
- Implied probability is `1 / decimal_odds`.
- Decimal odds must be finite and greater than `1.0`.
- Supplied decimal odds or implied probability must agree with the American
  price; otherwise construction fails.
- Market names normalize to lowercase snake case.
- Empty event identifiers, malformed dates, empty identity fields, and an
  identical home/away team fail clearly.

Freshness defaults to a conservative five-minute maximum age. A quote without
`quote_timestamp` is not fresh even if `collected_at` exists. Future timestamps
are not fresh. Callers can supply a different non-negative `timedelta`.

## Default-Deny Approval Behavior

Constructing a valid quote does not grant betting or Kelly approval. Every
quote defaults to ineligible and `not_approved`.

Research and sample modes cannot be constructed with betting eligibility,
Kelly eligibility, or an approval status other than `not_approved`. Sample,
mock, and historical source types receive the same fail-closed protection.
Kelly eligibility also requires betting eligibility for any future explicitly
approved production quote.

## MLB Mapping

The existing `HROddsCandidate` remains the MLB adapter's public intermediate
type. It now has an optional `to_normalized_quote()` conversion, and
`OddsAPIProvider.normalize_quotes()` maps an already-supplied payload without
fetching anything.

The mapping retains provider event/market identifiers, event teams, event and
quote times, sportsbook, region, American price, line, and player selection.
Every mapped MLB quote uses `mode="research"`, remains ineligible for betting
and Kelly, and has `approval_status="not_approved"`. The existing keyless
sample CLI does not consume the new contract and its output is unchanged.

## NBA Compatibility

The NBA runtime was not migrated to `NormalizedOddsQuote`. No NBA runtime file,
provider adapter, scoring path, selection gate, or bankroll/Kelly path was
changed. Existing NBA backward-compatibility tests were included in targeted
validation and the full suite.

## Commands Run

Syntax check:

```powershell
py -3.13 -m py_compile courtvision/core/odds.py courtvision/core/__init__.py courtvision/sports/mlb/adapters/odds_api_provider.py
```

Targeted validation:

```powershell
py -3.13 -m pytest tests/test_normalized_odds_quote.py tests/test_mlb_hr_odds_provider.py tests/test_mlb_hr_prop_engine.py tests/test_nba_backwards_compatibility.py --basetemp=.pytest_tmp_phase1b -q
```

Required keyless MLB validation:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Required full validation:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

The full command was initially interrupted by the command wrapper's 120-second
timeout without a pytest failure result. It was rerun unchanged with a longer
wrapper allowance and completed successfully.

## Exact Test Results

- Targeted validation: `26 passed in 2.05s`
- Keyless MLB sample CLI: exit code `0`; research-only sample report rendered
- Full suite: `2794 passed, 31 xfailed in 254.56s (0:04:14)`

## Next Recommended Step

Review and approve the Phase 1B field names and default-deny invariants as a
stable shared boundary. After that approval, scope any provider-registry or NBA
runtime adoption as a separate phase with explicit compatibility and safety
gates. No Phase 1C work was started here.
