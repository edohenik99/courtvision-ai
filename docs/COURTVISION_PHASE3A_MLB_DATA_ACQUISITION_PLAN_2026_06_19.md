# CourtVision Phase 3A: MLB Data Acquisition Plan

**Plan date:** 2026-06-19  
**Status:** Planning only; no provider implementation, external data acquisition, or production promotion  
**Scope:** MLB home-run research data and future leakage-safe historical training

## 1. Executive summary

CourtVision next needs reproducible, source-attributed MLB data for game identity,
player and pitcher outcomes, Statcast contact quality, weather, ballparks, pregame
lineup and probable-pitcher state, and timestamped market snapshots. The first
implementation should establish immutable raw storage and manifests before any
source adapter or feature builder is added.

The initial real-data path must remain research-only. Public baseball datasets
have revisions, coverage gaps, identity differences, and mixed time semantics.
Lineups, probable pitchers, weather, and market data are especially vulnerable to
future-data leakage unless the exact observation time is retained. Historical
results alone cannot prove that a feature was available before an event began.

No production, approval, or staking path is opened by this plan. Real source data
does not establish calibrated probability, validated historical market coverage,
or operational reliability. The Phase 0 through Phase 2F safety defaults remain
authoritative: MLB artifacts and quotes stay `not_approved`,
`eligible_for_betting=False`, and `kelly_eligible=False`. Keyless sample mode and
all existing NBA behavior remain unchanged.

This document does not authorize automated collection from any source. Before an
implementation phase begins, the source's current documentation, license, terms,
rate limits, attribution rules, and redistribution restrictions must be reviewed
and recorded in its manifest.

## 2. Source matrix

Risk means acquisition, licensing, identity, temporal-integrity, and maintenance
risk for CourtVision; it is not a judgment about the publisher. “Near-real-time”
does not imply an availability guarantee. Access classifications and terms must be
rechecked at implementation time.

### 2.1 Public and historical baseball data

| Source name | Data type | Access class | Expected fields | Historical availability | Real-time availability | API/key requirement | Reliability notes | Legal/terms caution | Recommended phase | Risk level |
|---|---|---|---|---|---|---|---|---|---|---|
| [Baseball Savant / Statcast](https://baseballsavant.mlb.com/csv-docs) | Pitch-, plate-appearance-, batted-ball-, player-, game-, and venue-level Statcast data | Public/free web export; automation status uncertain | `game_pk`, `game_date`, batter/pitcher MLBAM IDs, `events`, `description`, pitch type, release metrics, launch speed/angle, `estimated_woba_using_speedangle`, `launch_speed_angle`, batted-ball type, spray coordinates, teams, inning, score state | Statcast-era history, with field coverage varying by season | Search data may update during or after games; no service-level guarantee for research automation | No key for normal public search/export; no supported public API contract should be assumed | Best primary source for contact-quality and pitch-mix features; values and classifications can be corrected after initial publication | MLB terms, copyright, automated-access limits, attribution, and redistribution must be reviewed; store source references rather than republishing bulk data | 3C | Medium |
| [Retrosheet event and game-log files](https://www.retrosheet.org/game.htm) | Play-by-play events, game logs, rosters, schedules, and outcomes | Public/free download | Retrosheet game ID, date, doubleheader number, teams, park, player IDs, starters, batting order, plays, batter/pitcher, event result, home runs, game status | Multi-decade historical coverage; completeness and file format vary by season | No | No key | Strong independent source for game and outcome reconstruction; identifiers differ from MLBAM IDs and event parsing is specialized | Follow the [Retrosheet use notice](https://www.retrosheet.org/notice.txt), attribution requirements, and any redistribution limitations | 3D | Medium |
| [Lahman Baseball Database / SABR](https://sabr.org/lahman-database/) | Season-level batting, pitching, people, teams, parks, and franchises | Public/free downloadable database | Lahman player ID, names, handedness where present, season totals, team/league, pitching totals, park and franchise identity | Long-run historical seasons; release currency depends on version | No | No key | Useful for identity support, long-run totals, and sanity checks; not sufficient for pitch-level or batter-game training | Record the exact release and license; verify attribution and redistribution terms for the selected distribution | 3B reference; optional 3D support | Low–Medium |
| Baseball Savant park factors | Park-factor reference and venue effects | Public/free web presentation; export method uncertain | Venue, season/window, overall and HR-specific factors, handedness splits when published | Recent multi-season windows | Updated periodically, not an event feed | No key for public pages | Useful reference for a versioned static table; methodology and displayed windows can change | Review MLB terms and do not scrape or redistribute without authorization | 3F | Medium |

### 2.2 Weather and environment

| Source name | Data type | Access class | Expected fields | Historical availability | Real-time availability | API/key requirement | Reliability notes | Legal/terms caution | Recommended phase | Risk level |
|---|---|---|---|---|---|---|---|---|---|---|
| [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) | Reanalysis/historical hourly weather | Public/free for qualifying non-commercial use; commercial plans available | Latitude, longitude, timestamp, temperature, relative humidity, wind speed, wind direction, precipitation, pressure, model/dataset metadata | Long-run model-dependent history | Historical/reanalysis only; not a record of a pregame forecast | Keyless public endpoint for qualifying use; commercial customer endpoints may require credentials | Reproducible and easy to join by venue coordinates; gridded model values are not ballpark sensor observations | Review [Open-Meteo terms](https://open-meteo.com/en/terms), attribution, fair-use limits, and commercial-use requirements | 3E | Low–Medium |
| [Open-Meteo Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api) | Archived forecast runs suitable for as-of backtests | Public/free or paid depending use and endpoint | Forecast initialization/run time, valid time, temperature, humidity, wind speed/direction, precipitation, model | Limited to the provider's archived forecast period and models | Archived forecasts; separate forecast API supports current pregame collection | Plan and endpoint dependent | Preferred weather source for leakage-safe recent backtests because it can represent what was forecast before an event | Same terms and attribution review; retain forecast run time and model version | 3E | Medium |
| [NOAA/NCEI](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation) | Station observations and climate archives | Public/free government data; some services have access controls | Station ID, observation time, temperature, humidity/dew point, wind speed/direction/gust, precipitation, quality flags | Long-run, dataset- and station-dependent | Some NOAA services are current, but this plan treats NCEI as a historical cross-check | Dataset/endpoint dependent; some NOAA APIs require a token | Authoritative observation source, but station selection, distance, missingness, and observation timing add complexity | Record dataset-specific use guidance and attribution; do not imply a station measurement occurred inside the venue | Optional 3E cross-check | Medium |

Roof status is not reliably supplied by these weather sources. It must remain
explicitly missing or come from a separately timestamped lineup/game-status
source. `wind_out_to_field` is a derived field requiring wind direction plus a
versioned park orientation; it must not be copied directly from a generic weather
feed.

### 2.3 Odds and market data

| Source name | Data type | Access class | Expected fields | Historical availability | Real-time availability | API/key requirement | Reliability notes | Legal/terms caution | Recommended phase | Risk level |
|---|---|---|---|---|---|---|---|---|---|---|
| [The Odds API](https://the-odds-api.com/liveapi/guides/v4/) via the existing MLB adapter | Sportsbook event, market, selection, price, and update timestamps | Paid/keyed with plan-dependent quota; trial availability may vary | Event ID, commence time, home/away teams, bookmaker key/title, market key, market/bookmaker update time, outcome name/description, point, price | [Historical snapshots](https://the-odds-api.com/historical-odds-data/) are plan-, sport-, market-, and date-dependent; HR-prop coverage must be proven before use | Yes, subject to provider/book/plan coverage | `COURTVISION_ODDS_API_KEY` for the existing adapter | An adapter already exists and normalizes current MLB HR quotes; current fetching does not by itself create historical pregame snapshots | Verify plan rights, storage/redistribution, supported books/regions, request costs, and historical player-prop coverage | 3G, only if credentials already exist | Medium–High |
| [SportsGameOdds](https://sportsgameodds.com/) | Multi-book event, player-prop, and odds feed | Paid; trial/plan details provider-controlled | Provider event/player IDs, teams, start time, market/selection, line, price, sportsbook, status, update timestamp | Plan- and market-dependent; must be confirmed for MLB HR props | Yes, plan dependent | API key | Possible future alternative; no CourtVision adapter exists and field/coverage claims require a contract review | Commercial terms, storage rights, derived-data rights, and redistribution limits require review | Future evaluation after 3G | High |
| [SportsDataIO MLB API](https://sportsdata.io/developers/api-documentation/mlb) | MLB schedules, players, lineups, probable pitchers, stats, injuries, and betting feeds depending subscription | Paid | Stable provider IDs, game status/start, teams, stadium, projected/confirmed lineups, probable/starting pitchers, player stats, markets and timestamps where licensed | Product- and subscription-dependent | Yes, product dependent | API subscription key | Broad structured coverage could reduce joins, but availability and field semantics must be verified against a sample contract | Commercial license, caching, retention, display, and redistribution rules require review | 3H source evaluation; later odds evaluation | High |
| [OpticOdds](https://developer.opticodds.com/) | Sportsbook odds, player props, fixtures, and results | Paid | Fixture/team/player IDs, start time, sportsbook, market, selection, line, price, status, timestamps | Product- and market-dependent | Yes | API key | Potential specialist odds source; MLB HR history, timestamp granularity, and book coverage must be validated | Commercial storage, replay, derived-data, and redistribution rights require review | Future evaluation after 3G | High |
| [Sportradar MLB](https://developer.sportradar.com/baseball/reference/mlb-overview) | MLB schedules, game state, rosters, probable pitchers, lineups/statistics, and separately licensed odds products | Paid/enterprise | Sportradar IDs, game/venue status, scheduled start, teams, players, lineups, probable pitchers, statistics; odds fields depend on product | Product- and package-dependent | Yes, feed dependent | API credentials and commercial agreement | Mature enterprise option with structured identities; cost and package boundaries are material | Contract controls retention, redistribution, attribution, and permitted use; odds may require a separate product | 3H source evaluation; future enterprise option | High |

Phase 3G must use only the existing The Odds API adapter, only when credentials
are already configured, and only in research mode. It must not introduce another
odds provider. Historical training must not assume that current quotes are valid
historical snapshots or that a paid archive includes the required MLB HR market.

### 2.4 Lineups and probable pitchers

| Source name | Data type | Access class | Expected fields | Historical availability | Real-time availability | API/key requirement | Reliability notes | Legal/terms caution | Recommended phase | Risk level |
|---|---|---|---|---|---|---|---|---|---|---|
| MLB.com game pages / Gameday | Official public game, probable-pitcher, and lineup presentation | Public/manual; automated interface support uncertain | MLB game/player IDs where exposed, scheduled start, game status, probable pitchers, batting order, position, handedness | Public pages are not an assured historical as-of archive | Yes as a public presentation, without a feed guarantee | No key for manual access; no supported automated API should be assumed | Highest-authority public presentation, but historical snapshots and stable automated access are not guaranteed | MLB terms and automated-access rules require review; manual transcription must be labeled `manual` with `collected_at` | 3H | Medium–High |
| MLB Stats API endpoints used by community clients | Structured game/schedule/feed data hosted by MLB | Publicly reachable but support and authorization are uncertain | `gamePk`, dates/status, teams, venue, probable pitchers, live boxscore lineup/order, player IDs and handedness | Final game feeds may reconstruct starters, but generally do not prove what was known pregame | Near-real-time, without a public service-level agreement | Usually keyless; supported-contract status uncertain | Technically attractive for canonical IDs; must not be treated as an approved provider until terms and stability are resolved | Explicit terms and permission review required before automation; endpoints may change without notice | 3H decision only | High |
| [Baseball Press lineups](https://www.baseballpress.com/lineups) | Projected/confirmed public lineup presentation | Public/manual; automated use uncertain | Teams, game time, batting order, positions, handedness, probable pitchers, confirmation indicators | No guaranteed timestamped archive | Yes as a web presentation | No key for manual access | Useful human cross-check; not suitable as an automated historical source without permission and retained timestamps | Review site terms, attribution, and automated-access restrictions | 3H manual fallback evaluation | Medium–High |
| SportsDataIO MLB | Structured lineup and probable-pitcher feed | Paid | See SportsDataIO row above; must include status and provider update time | Contract dependent | Yes | API key | Candidate paid source when pregame snapshots and stable identifiers are contractually supported | Commercial terms review required | 3H | High |
| Sportradar MLB | Structured lineup and probable-pitcher feed | Paid/enterprise | See Sportradar row above; must include status and provider update time | Contract dependent | Yes | API credentials | Candidate enterprise source; package must be confirmed | Commercial terms review required | 3H | High |

Phase 3H should end with a written source decision, a terms review, a field sample,
and a timestamp-retention test. If no source can establish pregame observation
time, lineup and probable-pitcher fields must remain missing for historical rows;
final starters must not be relabeled as pregame probable or confirmed information.

## 3. Field mapping to existing contracts

Raw provider payloads must be retained unchanged. Normalization then maps them to
the current Phase 2B/2D contracts. Provider-native IDs and timestamps remain in a
crosswalk or source manifest even where the current contract has no direct field.
Names alone are not valid join keys.

### 3.1 `MLBGameContext`

| Contract field | Proposed source mapping and rule |
|---|---|
| `game_id` | Canonical CourtVision ID backed by native `game_pk`, Retrosheet game ID, or paid-provider event ID. Maintain an effective-dated crosswalk and preserve every native ID. Include the doubleheader game number. |
| `game_date` | Official local game date from schedule/game log; do not derive only from a UTC timestamp. |
| `event_start_time` | Scheduled start with timezone from a schedule source. Preserve original scheduled, revised, postponed, and actual-start values outside the current contract. |
| `home_team`, `away_team` | Map native team IDs to versioned canonical codes; retain historical franchise/team identity. |
| `venue_name` | Map native venue/park ID to a canonical venue version. Park changes and temporary venues must remain distinct. |
| `source_type` | `historical`, `live`, `manual`, `fixture`, or `sample` according to actual provenance; never infer from freshness. |
| `collected_at` | UTC time CourtVision observed or acquired the record, not the event time. |
| `data_quality`, `warnings` | Coverage, revision, ID-crosswalk, postponed-status, or schedule-time warnings. |

Statcast can supply `game_pk`, game date, teams, and venue-related identity for its
covered rows; Retrosheet can supply historical game identity and outcomes. A
schedule/lineup source is still needed for trustworthy future scheduled starts and
pregame status.

### 3.2 `MLBLineupContext`

| Contract field | Proposed source mapping and rule |
|---|---|
| `game_id`, `team` | Canonical IDs from the game and team crosswalks. |
| `lineup_confirmed` | True only when the source explicitly marks the lineup confirmed at or before `collected_at`; never infer from a final boxscore. |
| `batting_order[].player_id`, `player_name` | Native player ID mapped to canonical player ID; retain the source spelling for audit. |
| `batting_order[].bats`, `position`, `batting_order` | Source fields as observed. Position and order remain nullable when absent. |
| `batting_order[].status` | Map explicit states to `confirmed`, `projected`, `unknown`, or `not_starting`. Unknown provider semantics map to `unknown`, not `confirmed`. |
| `collected_at`, `source_type`, `data_quality`, `warnings` | Record observation time, source class, completeness, and late/change warnings. Multiple snapshots must not overwrite one another. |

### 3.3 `MLBProbablePitcherContext`

| Contract field | Proposed source mapping and rule |
|---|---|
| `game_id`, `team` | Canonical crosswalk values. The team is the pitcher's team, not the opposing offense. |
| `pitcher_id`, `pitcher_name`, `throws` | Native player ID mapped to canonical identity plus source name/handedness. |
| `probable_status` | Map only explicit provider states to `confirmed`, `probable`, `projected`, or `unknown`. A pitcher who eventually starts is not retroactively “confirmed.” |
| `collected_at`, `source_type`, `data_quality`, `warnings` | Retain every observation, changes, scratches, opener/bulk ambiguity, and missing state. |

### 3.4 `MLBHitterFeatureContext`

Features are derived normalized data, not copied source claims. For a row with
cutoff `as_of_date`/event time, eligible Statcast plate appearances and batted
balls must occur strictly before the cutoff.

| Contract field | Proposed derivation |
|---|---|
| `player_id`, `player_name`, `bats` | Canonical identity from MLBAM/Lahman/Retrosheet crosswalks; handedness must be effective for the relevant season where possible. |
| `sample_window` | Explicit rule such as previous 30 completed team games, previous 50 plate appearances, or season-to-date before cutoff. Store the full rule in dataset metadata. |
| `recent_hr_rate` | Home-run outcomes divided by the declared eligible plate-appearance denominator; document exclusions. |
| `barrel_rate` | Statcast barrel classifications divided by eligible batted-ball events, with coverage checks. |
| `hard_hit_rate` | Eligible batted balls at the documented exit-velocity threshold divided by eligible tracked batted balls. Threshold changes require a new data version. |
| `fly_ball_rate` | Declared fly-ball events divided by the declared batted-ball denominator. Source classification and denominator must be versioned. |
| `pull_rate` | Derived from batted-ball direction using batter handedness and documented coordinate rules, or copied only from a licensed source with matching semantics. |
| `avg_exit_velocity`, `max_exit_velocity` | Aggregate non-null Statcast launch-speed observations within the window; missing tracking is not zero. |
| `source_type`, `as_of_date`, `data_quality` | Use `historical` for backtest-derived features, preserve the cutoff date, and report coverage/missingness. |

### 3.5 `MLBPitcherFeatureContext`

| Contract field | Proposed derivation |
|---|---|
| `pitcher_id`, `pitcher_name`, `throws` | Canonical identity and effective handedness from source IDs. |
| `pitch_mix` | Counts by normalized Statcast pitch type divided by classified pitches strictly before cutoff; unknown pitch types remain explicit. |
| `hr_allowed_rate` | Home runs allowed divided by the documented denominator, such as batters faced or eligible plate appearances. |
| `barrel_allowed_rate`, `hard_hit_allowed_rate`, `fly_ball_allowed_rate` | Rates over eligible tracked batted balls, using the same versioned definitions as hitter features. |
| `source_type`, `as_of_date`, `data_quality` | Historical provenance, cutoff date, tracking coverage, role/sample warnings, and nulls for insufficient samples. |

### 3.6 `MLBWeatherContext`

| Contract field | Proposed source mapping and rule |
|---|---|
| `game_id`, `venue_name` | Canonical game and effective-dated venue version with coordinates. |
| `temperature`, `humidity` | Hourly value nearest the documented pregame target time; retain source units and normalized units in metadata. |
| `wind_speed`, `wind_direction` | Source 10 m wind fields with documented units and compass conversion. Calm/variable values remain explicit. |
| `wind_out_to_field` | Derived only from timestamped wind plus versioned home-plate-to-field orientation. Null when orientation or roof state is unknown. |
| `roof_status` | Separate timestamped game/venue source; not inferred from weather. |
| `source_type`, `collected_at`, `data_quality`, `warnings` | Distinguish observed/reanalysis from forecast-as-of. For backtests, retain model run/issue time and valid time. |

### 3.7 `MLBBallparkContext`

| Contract field | Proposed source mapping and rule |
|---|---|
| `venue_name` | Canonical, effective-dated venue version. |
| `park_factor_hr` | Versioned published factor or a separately versioned CourtVision historical calculation; never silently mix methodologies. |
| `handedness_factor` | Source-provided or independently calculated left/right splits with window and denominator metadata. |
| `altitude`, `dimensions` | Versioned venue facts with units. Dimensions should use named directions/markers and effective dates. |
| `source_type`, `data_version`, `data_quality`, `warnings` | Source, release/calculation version, coverage, neutral baseline, park-renovation, and temporary-venue warnings. |

### 3.8 `MLBHRResearchContext`

This remains a composed, per-hitter view. Join components by canonical game,
team, player, opponent pitcher, and temporal cutoff. The assembly layer must:

- choose only component snapshots observable at the row cutoff;
- retain `None` for unavailable components;
- propagate component warnings and source references;
- let the existing contract compute `context_complete` and
  `missing_required_fields`;
- reject cross-game, cross-team, and post-start joins; and
- remain `sport="MLB"`, `league="MLB"`, and `mode="research"`.

No acquisition source may alter those safety fields.

### 3.9 `NormalizedOddsQuote`

| Contract area | Proposed source mapping and rule |
|---|---|
| `market_identity` | Map sport/league to MLB, native event ID through the canonical game crosswalk, event date, canonical teams, and provider market key to the existing normalized HR market type. |
| `selection` | Map player display name and native player ID; preserve the provider line, normally the HR proposition line exposed by the source. |
| `source_metadata` | Sportsbook title/key, provider name, `mode="research"`, actual source type, region, raw market/event IDs, and a coverage/freshness quality label. |
| Price fields | Map the native price to `american_odds`; the contract-derived decimal price and implied price conversion are not a calibrated CourtVision probability. |
| Time fields | `quote_timestamp` is the provider/book update time, `collected_at` is CourtVision receipt time, and `event_start_time` is the scheduled start. All require timezone-aware UTC normalization upstream. |
| State and safety | Reject or isolate `is_live=True` for pregame datasets. Preserve `approval_status="not_approved"`, `eligible_for_betting=False`, and `kelly_eligible=False`. |

Deduplicate odds by provider, sportsbook, event, market, selection, line, and quote
timestamp. Do not collapse distinct books or overwrite earlier snapshots.

### 3.10 `ResearchArtifact`

The artifact is a safe research serialization boundary, not the raw data store.

| Artifact field | Proposed mapping and rule |
|---|---|
| Metadata identity | Stable artifact ID, MLB sport/league, HR market type, `mode="research"` or `historical`, and `artifact_type="dataset"`, `diagnostic`, or another existing supported type. |
| Run/provenance | `run_date`, UTC `generated_at`, all provider names/source types, code version, normalized data version, and current schema version. |
| Row identity | Deterministic row ID from game/player/cutoff/version; canonical player/team/opponent/event IDs and event date. |
| Quality/provenance | `status`, `data_quality`, warnings, and `source_refs` pointing to manifests/raw checksums or normalized partition IDs. Do not embed untraceable provider data in free text. |
| Safety | Every metadata and row object remains `not_approved`, ineligible, and non-Kelly. The existing validator remains unchanged. |

## 4. Recommended implementation order

### Phase 3B: local raw/normalized storage layout and source manifests

- Add only directory placeholders that are justified, `.gitignore` rules, manifest
  schemas, canonical ID-crosswalk schemas, and validation tests.
- Define immutable raw partitions, normalized schema versions, UTC timestamp
  conventions, hashes, and atomic write behavior.
- Do not acquire source data in this phase.

### Phase 3C: Baseball Savant/Statcast historical ingestion prototype

- Obtain terms approval before implementation.
- Use a small, fixed historical date range and save the native response unchanged.
- Normalize game/player IDs, pitch/plate-appearance keys, outcomes, batted-ball
  fields, and tracking-missingness indicators.
- Prove repeatability and revision detection; do not connect ingestion to scoring.

### Phase 3D: Retrosheet game/outcome ingestion prototype

- Parse one fixed season or smaller fixture from an approved Retrosheet release.
- Normalize game identity, doubleheader number, players, starters, plate
  appearances, and outcomes.
- Build an explicit Retrosheet-to-MLBAM crosswalk with unresolved identities
  retained, not guessed.

### Phase 3E: weather historical ingestion prototype

- Version stadium coordinates and timezones.
- Prototype reanalysis/observation data separately from archived forecast data.
- Preserve forecast issue time, valid time, model/dataset, units, and collection
  time; do not use historical observations as if they were pregame forecasts.

### Phase 3F: ballpark factor static table

- Create a reviewed, versioned static table for canonical venues, effective dates,
  altitude, dimensions/orientation, roof type, and HR factor provenance.
- Keep published and CourtVision-derived factors distinguishable.
- Do not change MLB HR scoring weights.

### Phase 3G: odds snapshot integration through the existing adapter

- Proceed only if existing credentials are present and the plan permits the MLB HR
  market; never print or persist credentials.
- Wrap existing normalized quotes with immutable timestamped snapshots and
  manifests. Do not add a provider or change normalization behavior.
- Validate pregame freshness, source timestamps, quota behavior, duplicate
  snapshots, and absent-market handling.

### Phase 3H: lineup/probable-pitcher source decision

- Compare official/manual, uncertain public interfaces, and paid feeds on terms,
  identifiers, confirmation semantics, timestamps, history, corrections, cost,
  and retention rights.
- Require a field sample and proof that snapshot time is retained.
- Document a selected source or explicitly defer the fields; do not implement the
  provider in the decision phase.

### Phase 4A: leakage-safe historical batter-game dataset builder

- Build one row per batter/game/cutoff from versioned normalized inputs.
- Fit/derive rolling features using only records strictly before the cutoff.
- Attach outcomes after feature materialization in a separate step.
- Emit deterministic `ResearchArtifact` datasets and a leakage audit. This phase
  remains research-only and does not alter scoring or bankroll-facing behavior.

## 5. Storage plan

The following folders are proposed; Phase 3A does not create them:

```text
data/
  raw/mlb/
    statcast/
    retrosheet/
    lahman/
    weather/
    odds/
  normalized/mlb/
  research/mlb/hr/
  training/mlb/hr/
```

### 5.1 Layout and immutability

- Raw paths should partition by source, dataset, source release or request date,
  and acquisition batch ID. Example only:
  `data/raw/mlb/statcast/search/2026/06/<batch_id>/`.
- Each acquisition batch contains one manifest and one or more immutable native
  payloads. A retry creates another batch; it does not overwrite the first.
- Normalized partitions include `schema_version`, source release/batch IDs, and
  an effective date range. Research and training artifacts identify all normalized
  input versions.
- UTC ISO 8601 timestamps with `Z` or explicit offset are required. Local event
  dates and venue timezones are retained separately.
- Large raw, normalized, research, and training files are ignored by git by
  default. Only schemas, tiny licensed fixtures, manifests without secrets, and
  explicitly reviewed static reference tables may be candidates for version
  control.
- No credentials, signed URLs, authorization headers, or paid payload excerpts are
  stored in manifests.

### 5.2 Source manifest

Use a sidecar such as `manifest.json` per immutable acquisition batch. Proposed
required fields:

```json
{
  "manifest_schema_version": "1.0",
  "batch_id": "stable-unique-id",
  "source_name": "provider-name",
  "source_dataset": "dataset-or-endpoint-name",
  "source_documentation_url": "https://example.invalid/docs",
  "source_terms_url": "https://example.invalid/terms",
  "access_class": "public|paid|manual|unknown",
  "acquisition_mode": "historical|live|manual",
  "collected_at": "UTC timestamp",
  "as_of": "source release, snapshot, or forecast issue timestamp",
  "requested_range": {"start": "date/time", "end": "date/time"},
  "request_parameters_redacted": {},
  "source_version": "release/model/feed version",
  "parser_version": "code version",
  "files": [
    {
      "relative_path": "payload.ext",
      "content_type": "source content type",
      "byte_size": 0,
      "record_count": 0,
      "sha256": "lowercase SHA-256"
    }
  ],
  "status": "complete|partial|failed",
  "warnings": []
}
```

`collected_at` answers when CourtVision received the data. `as_of` answers when
the source says the information was valid or issued. They are not interchangeable.
For odds and lineup snapshots, retain provider update time as an additional native
field. For forecasts, retain both issue/run time and valid time.

SHA-256 should be computed over the exact raw bytes before parsing. A normalized
dataset version should hash a canonical build manifest containing ordered raw
hashes, schema version, parser/feature code version, parameters, and row count.
This allows deterministic rebuild checks without committing bulk data.

## 6. Data-quality and leakage rules

1. Define a timezone-aware feature cutoff for every batter-game row. No current-
   game pitch, plate appearance, lineup change observed after cutoff, actual
   weather, outcome, or market update may enter pregame features.
2. Rolling features exclude the current game. A date-only source is insufficient
   to include an earlier same-day game safely; exclude the whole date unless event
   ordering and availability timestamps are proven.
3. For doubleheader game two, game-one information may be used only if game one
   was complete and the derived feature was available before the game-two cutoff.
   Otherwise use the last unambiguous prior cutoff.
4. Odds snapshots require `quote_timestamp < event_start_time` and
   `collected_at < event_start_time`. Missing or inconsistent timestamps make the
   quote ineligible for a pregame historical row. Live/in-play quotes are isolated.
5. Pregame weather uses a forecast issued at or before cutoff. Reanalysis,
   station observations, final roof state, and actual game-time conditions must be
   clearly labeled historical/observed and cannot masquerade as forecast inputs.
6. Lineup and probable-pitcher values require observation timestamps and explicit
   source status. Final starters cannot backfill pregame confirmation. Changes and
   scratches create new snapshots rather than overwrites.
7. Missing data is represented by `None`/null, an allowed explicit status, quality
   flags, and warnings. Zero, empty text, or a fabricated league average is not a
   missing-value substitute.
8. Sample, mock, fixture, and manual data remain visibly labeled at raw,
   normalized, context, artifact, and report boundaries. They cannot be merged into
   real-source partitions without per-row provenance.
9. Canonical joins use source IDs plus versioned crosswalks. Ambiguous player,
   team, venue, or game matches remain unresolved. Name-only fuzzy matches do not
   silently pass.
10. Revisions are append-only. Changed raw hashes create a new source/batch
    version, and downstream artifacts record which version they used.
11. Duplicate natural keys with conflicting values fail validation. Exact duplicate
    raw records may be retained, but normalization reports and deterministically
    resolves them under a versioned rule.
12. Postponed, suspended, resumed, canceled, and rescheduled games preserve status,
    original ID/start, revised ID/start where applicable, and source history. They
    are not treated as ordinary completed games.
13. No source, freshness level, completeness flag, or paid contract can override
    MLB's research-only safety status.

## 7. Safety boundary

- MLB remains research-only.
- No MLB output receives betting approval.
- Kelly calculations and eligibility remain unavailable for MLB research data.
- No staking or unit-sizing behavior is added.
- EV is not introduced until calibrated probability and timestamp-valid historical
  odds coverage have been separately established and approved.
- Production promotion requires a separate, explicit approval gate and is outside
  this plan.
- Existing MLB scoring weights, thresholds, labels, provider selection, sample
  mode, and all NBA runtime behavior remain unchanged.

## 8. Tests planned for later phases

These tests are plans, not Phase 3A additions:

- **Source manifest validation:** required fields, UTC timestamps, redacted request
  data, valid status/access enums, file presence, byte size, record count, and
  SHA-256 verification.
- **Schema validation:** raw-to-normalized required fields, types, units, allowed
  nulls/statuses, ID-crosswalk integrity, and existing Phase 2B/2D contract checks.
- **No future-data leakage:** inject post-cutoff pitches, results, forecasts,
  lineups, probable-pitcher changes, and quotes; assert none enter pregame rows.
- **Rolling-feature exclusion:** current game and current plate appearance are
  excluded; windows are stable at cutoff boundaries.
- **Duplicate game/player handling:** exact duplicates are deterministic;
  conflicting duplicates fail with diagnostics; ambiguous identities remain
  unresolved.
- **Doubleheaders:** game number participates in identity; game-one data enters a
  game-two row only under the documented completion/availability rule.
- **Postponed games:** original/revised times and statuses are retained; stale
  snapshots are not joined to the wrong event instance.
- **Missing probable pitchers:** context remains incomplete with explicit missing
  fields and warnings; no pitcher is synthesized.
- **Unconfirmed lineups:** projected/unknown states stay unconfirmed and cannot be
  upgraded from a final boxscore.
- **Odds freshness:** quotes at/after start, live quotes, missing timestamps, and
  implausibly stale quotes are rejected or isolated according to the research
  snapshot policy.
- **Weather as-of integrity:** forecast issue time precedes cutoff; observation and
  reanalysis records cannot populate forecast-designated fields.
- **Deterministic rebuilds:** identical raw hashes, schemas, code version, and
  parameters produce identical ordered normalized output and artifact hashes.
- **Revision detection:** changed raw bytes create a new batch/data version and do
  not overwrite prior artifacts.
- **Safety invariants:** all resulting contexts/artifacts remain research or
  historical, `not_approved`, ineligible for betting, and non-Kelly.
- **Regression coverage:** sample MLB CLI output and NBA backward-compatibility
  tests remain unchanged.

## 9. Phase 3A validation

Only the existing commands are used for this documentation-only phase:

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Success requires a clean sample CLI run and a passing full suite. No live provider
is invoked by either command. Test/runtime outputs remain uncommitted.

## 10. Phase 3A completion boundary

Phase 3A is complete when this plan exists and the validation commands pass. It
does not create the proposed data folders, fetch a payload, download a dataset,
modify an adapter, build training rows, change scoring, or alter a production
gate. The next authorized change, if separately approved, is Phase 3B storage and
manifest scaffolding only.
