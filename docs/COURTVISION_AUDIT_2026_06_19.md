# CourtVision Repository Audit — 2026-06-19

## Audit scope and constraints

This is an audit of the working tree at `C:\dev\Sport_Project1` on 2026-06-19. It is not an audit of a clean clone or only the current Git commit.

- The repository was dirty before the audit.
- Existing changes are user-owned and were not changed or reverted.
- The multi-sport and MLB implementation is largely untracked, so it is present in this working tree but is not yet an authoritative, reproducible repository baseline.
- No bankroll, Kelly, scoring, grading, provider, dashboard, workflow, or runtime logic was edited.
- No generated output, history, cache, log, or local environment file was intentionally changed.
- The only intentional audit write is this file: `docs/COURTVISION_AUDIT_2026_06_19.md`.

## A. Executive summary

### Current state in plain English

CourtVision remains an NBA-first system with a large, production-facing legacy runtime and a substantial body of NBA safety, selection, reporting, grading, and regression logic. The canonical daily path still runs through `courtvision_ai.py` and `run_today.ps1`; it is not routed through a sport-neutral application layer.

The working tree contains a promising compatibility-first multi-sport foundation under `courtvision/core/` and `courtvision/sports/`. It adds a declarative sport registry, placeholder projection models, shared research helpers, and an isolated MLB home-run module. This work does not currently replace or control the NBA runtime. Most of it is untracked, so it would be absent from a clean checkout of the current commit.

MLB HR Phase 2 is isolated from NBA selection, grading, Kelly, and operator-board code. Keyless sample mode is preserved and is the default. The MLB implementation is not a trained model and is not an end-to-end research pipeline. It currently consists of:

- deterministic sample candidates;
- hand-authored feature and score formulas;
- placeholder stats, weather, and ballpark provider shells;
- a standalone The Odds API normalizer for HR quotes; and
- a console report.

It does not yet join schedule, pitcher, lineup, Statcast-style, weather, park, and odds data into real candidates. It has no MLB historical dataset builder, leakage-safe rolling features, training, backtesting, calibration, or live EV workflow.

### What works

- The valid full test suite is green: **`2767 passed, 31 xfailed in 235.46s`**.
- The existing NBA runtime remains the canonical path and has broad regression coverage.
- The NBA path has meaningful live-source, game-status, odds-freshness, identity, exposure, manual-review, and points-only Kelly controls.
- MLB sample mode runs without external keys:
  `py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample`.
- The MLB sample provider is deterministic and covered by tests.
- The MLB The Odds API adapter cleanly rejects missing credentials before making a network request.
- The MLB odds adapter normalizes mocked HR market payloads without passing raw provider shapes into scoring code.
- The new sport registry recognizes NBA, WNBA, MLB, NFL, and NHL, but it is configuration only.

### What is incomplete

- The multi-sport foundation is not wired into the canonical runtime.
- There is no common provider registry or sport pipeline router.
- Most production domain models, markets, projections, scoring, risk rules, reports, and Kelly logic remain NBA-specific.
- MLB live odds are not enriched with real stats or context and are not scored.
- MLB schedule, probable pitcher, confirmed lineup, Statcast, weather, and ballpark acquisition are absent or stubs.
- MLB historical training, backtesting, calibration, CLV, and ROI support is absent.
- Documentation does not provide a complete MLB HR runbook or document the MLB odds adapter's environment variables.
- Test baselines in handoff material and `TESTING.md` are stale.

### Biggest risks

1. **P0 safety semantics:** The unvalidated MLB heuristic emits `Elite`, estimated fair probability, recommended unit size, and tracking language. The sample CLI printed an `Elite` candidate and a `0.75U` recommendation. Those terms can be mistaken for validated betting advice even though the module is research-only.
2. **P0 reproducibility:** The multi-sport and MLB files and their tests are untracked. A clean clone does not reproduce the audited working tree.
3. **P1 architecture:** The canonical NBA path remains a roughly 9,000-line monolith with duplicated clients, normalization, selection, and compatibility layers. New sport-neutral modules are not authoritative.
4. **P1 data integrity:** MLB has no point-in-time historical dataset or leakage tests. No MLB probability or EV claim is currently defensible.
5. **P1 provider/config drift:** NBA and MLB The Odds API adapters use different key names (`THE_ODDS_API_KEY` and `COURTVISION_ODDS_API_KEY`), and the latter is absent from `.env.example`. Provider priority defaults are also inconsistent across settings and manager code.
6. **P1 Kelly contract:** NBA Kelly sizing requires odds, confidence, and a positive edge field, but not an explicit calibrated projection probability. It relies on upstream board correctness rather than independently proving probability provenance.

### Required safety disposition

Until MLB historical validation exists, replace MLB-facing terms as follows:

| Current term | Required research-only term |
| --- | --- |
| Elite / Strong | Research Watchlist / Candidate |
| Confidence score as bet quality | Research score plus Data Quality |
| Estimated fair probability | Uncalibrated research estimate, or omit it |
| Recommended unit size | No Betting Recommendation |
| Units won/lost / total wagered | Research outcome tracking only, with no bankroll meaning |

MLB rows should carry an explicit, immutable `eligible_for_betting=False` and a machine-readable `betting_approval_status=research_only_not_betting_approved`. No MLB path should call Kelly, create stake outputs, or emit unit recommendations before promotion criteria are met.

## B. Current repository map

### Key folders and files

| Path | Current role | Audit finding |
| --- | --- | --- |
| `courtvision_ai.py` | Canonical NBA fit, predict, board, output, and grading runtime | Still authoritative; large and heavily NBA/provider-specific. |
| `run_today.bat`, `run_today.ps1` | Main Windows operator workflow | Runs fit/predict, validation, audits, Kelly, grading, summaries, research artifacts, operator card, and manifest. Not multi-sport. |
| `scripts/run_daily.py` | Thin daily compatibility wrapper | Imports `CourtVisionAI`; resolves BallDontLie auth before argument parsing, even for `--help`. |
| `courtvision/application.py` | Application/stage facade | Useful migration layer, but not the main operator entrypoint. |
| `courtvision/pipeline/` | NBA prediction pipeline contracts and implementation | More structured than the monolith, but its candidates, markets, and features remain NBA-specific. |
| `courtvision/models.py` | Provider/domain dataclasses | `PlayerGameStats` and `Projection` contain basketball stats; not sport-neutral. |
| `courtvision/clients/` | NBA provider clients and fallback manager | BallDontLie, SportsDataIO, and API-NBA. Provider manager is NBA-specific. |
| `courtvision/providers/` | NBA research providers | The Odds API NBA research provider and API-NBA/manual schedule resolver. |
| `courtvision/data/` | NBA normalization and candidate shaping | BDL-shaped odds and basketball fields dominate. |
| `courtvision/market/`, `courtvision/markets/` | Market quality and market aliases | Canonical aliases and weights are primarily NBA markets. |
| `courtvision/scoring/`, `courtvision/selection/` | NBA candidate scoring and boards | Production thresholds and selection behavior are NBA-specific. |
| `courtvision/runtime_*` | NBA runtime gates, audit, scoring, selection, and outputs | Some concepts are reusable, but schemas and policies are tied to existing NBA rows. |
| `courtvision/betting/` | Kelly and performance | Active Kelly policy is points-only and consumed by NBA operator artifacts. |
| `courtvision/reporting/` | Large reporting/audit surface | 57 files, overwhelmingly NBA/operator/shadow specific. |
| `courtvision/evaluation/` | Evaluation/report abstractions | Separate from `reporting`; not used as a common multi-sport report interface. |
| `courtvision/core/` | New research-oriented shared foundation | Untracked. Contains small contracts/helpers, not an authoritative core runtime. |
| `courtvision/core/sport_registry.py` | Declarative sport metadata | Registers sports and markets only; does not route pipelines or providers. |
| `courtvision/sports/nba/` | NBA compatibility extraction | Moves projection helpers and re-exports `CourtVisionPro`; preserves imports. |
| `courtvision/sports/mlb/` | MLB placeholder projection and HR heuristic | Untracked, isolated, sample-first, not trained or production-ready. |
| `courtvision/sports/mlb/adapters/` | MLB provider protocols, sample provider, live-odds adapter, and stubs | Only sample and standalone odds normalization are functional. Stats/weather/park are stubs. |
| `courtvision/sports/wnba/`, `nfl/`, `nhl/` | Future sport placeholders | Offline placeholders/reserved modules, not provider-backed pipelines. |
| `courtvision/reports/` | New empty compatibility namespace | Only `__init__.py`; confusing alongside `reporting/` and `evaluation/`. |
| `scripts/` | Operational and reporting wrappers | Mostly NBA. `build_daily_research_report.py` is research-safe but aggregates NBA research artifacts, not MLB HR. |
| `tests/` | Broad regression suite | Green, extensive NBA coverage, new but shallow multi-sport/MLB coverage. |
| `data/history/` | Local long-lived NBA prediction, pick, shadow, and performance history | Contains large CSVs and backups; no MLB historical source or training tables. |
| `data/manual_schedule/` | API-NBA research schedule fallback | NBA-only template. |
| `docs/` | Architecture, safety, and audit documents | Strong historical NBA audit context; missing a complete current MLB runbook. |
| `dashboard/`, `courtvision_streamlit_app.py`, `streamlit_app.py` | NBA dashboard/UI | Explicitly outside this audit's edit scope; not multi-sport. |

### Current entrypoints and commands

| Command | Purpose | Audit status |
| --- | --- | --- |
| `.\run_today.bat [YYYY-MM-DD]` | Canonical Windows daily NBA operator flow | Main operational entrypoint. Not run during this audit because it writes live/runtime artifacts and depends on provider state. |
| `py -3.13 courtvision_ai.py --prediction-date YYYY-MM-DD --predict-only --out-dir outputs` | Direct canonical NBA prediction | CLI help validated. Runtime behavior is broadly covered by tests. |
| `py -3.13 scripts/run_daily.py --prediction-date YYYY-MM-DD --out-dir outputs` | Compatibility wrapper around `CourtVisionAI` | CLI help validated. Still NBA and provider-coupled. |
| `py -3.13 scripts/run_research_mode.py --date YYYY-MM-DD --season YYYY --stats-provider api_nba` | API-NBA stats-only research mode | NBA only. Explicitly blocks betting eligibility. |
| `py -3.13 scripts/build_daily_research_report.py --date YYYY-MM-DD` | Aggregate isolated research artifacts | NBA research artifacts; not an MLB HR orchestrator. |
| `py -3.13 -m courtvision.sports.mlb.hr_report --date YYYY-MM-DD --provider sample` | MLB HR sample console report | Works keylessly. Produces unsafe `Elite` and unit language that must be corrected. |
| `py -3.13 -m courtvision.sports.mlb.hr_report --date YYYY-MM-DD --provider odds_api` | Standalone MLB HR odds normalization report | Requires `COURTVISION_ODDS_API_KEY`; returns quotes only, without stats/context scoring. Missing key exits cleanly. |
| `py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q` | Documented full deterministic suite | Valid unrestricted result: 2767 passed, 31 xfailed. |
| `run_tests.bat` | Stable-test shortcut | Runs only `tests/stable/`, not the full documented suite; its name is ambiguous. |

### Where NBA-specific logic is hardcoded

- `courtvision_ai.py`: provider access, basketball stat keys, player markets, minutes, injuries, team totals, confidence, elite selection, grading, and Kelly metadata.
- `courtvision/models.py`: points, rebounds, assists, threes, steals, blocks, and minutes are embedded in core dataclasses.
- `courtvision/pipeline/predict_pipeline.py`: basketball baselines, combo projections, injury/minutes context, NBA market availability, and NBA selection policy.
- `courtvision/market/quality.py`, `courtvision/markets/prop_types.py`: NBA market aliases, weights, and `player_*` conventions.
- `courtvision/data/normalization.py`, `courtvision/data/candidates.py`, `courtvision/data/bdl_odds_adapter.py`: NBA/provider schema assumptions.
- `courtvision/runtime_selection.py`: points-specific elite guards and calibration rules.
- `scripts/run_kelly_stakes.py`: `player_points`-only live lock and NBA row schema.
- `courtvision/clients/provider_manager.py`: `NBA_PROVIDER_PRIORITY` and NBA client interfaces.
- Most modules under `courtvision/reporting/`: NBA markets, minutes, injuries, board lanes, and NBA history schemas.

### Sport-agnostic logic that already exists

- Pipeline stage and manifest contracts in `courtvision/pipeline/contracts.py`.
- Artifact overwrite/date guards in `courtvision/artifact_guard.py`.
- Generic portions of game-status, timestamp freshness, and identity gating in `courtvision/runtime_gates.py`, although current row schemas are NBA-shaped.
- Conservative Kelly calculation in `courtvision/betting/kelly.py`, although the active runner is NBA points-only and does not require explicit probability provenance.
- New untracked research contracts in `courtvision/core/`: sport metadata, projection result, hit-rate windows, confidence scoring, minimal odds shape, and CLV snapshots.
- Generic simulation and portfolio concepts exist, but their production integration and data assumptions are NBA-oriented.

### MLB support that currently exists

- Declarative MLB markets in `courtvision/core/sport_registry.py`.
- Placeholder `MLBProjectionModel` in `courtvision/sports/mlb/projection.py`; context features are recorded but explicitly not applied.
- HR feature scoring in `hr_features.py`, pitch matching in `pitch_matchup.py`, environment scoring in `weather_factor.py`, and park helpers in `ballpark_factors.py`.
- `HRPropEngine` in `hr_prop_engine.py`, using fixed heuristic weights and an uncalibrated fair-probability formula.
- Deterministic sample candidates in `adapters/sample_provider.py`.
- Provider protocols in `adapters/base.py`.
- The Odds API normalization in `adapters/odds_api_provider.py`.
- Provider selection in `adapters/provider_factory.py`, with `sample` as the keyless default.
- Stub stats, weather, ballpark, and generic sportsbook provider classes that raise `NotImplementedError`.
- CLI rendering in `hr_report.py`.

### Duplicate, dead, temporary, or confusing surfaces

- `courtvision_ai.py` duplicates BallDontLie client, normalization, market mapping, and selection behaviors that also exist under `courtvision/`.
- There are two `OddsQuote` dataclasses: one in `courtvision/core/odds_engine.py` and another in `courtvision/sports/mlb/adapters/base.py`.
- There are two The Odds API paths with different environment contracts: NBA uses `THE_ODDS_API_KEY`; MLB uses `COURTVISION_ODDS_API_KEY`.
- `courtvision/reporting/`, `courtvision/reports/`, and `courtvision/evaluation/` overlap conceptually; `courtvision/reports/` is currently empty.
- `courtvision/sports/mlb/adapters/odds_provider.py` is a generic stub while `odds_api_provider.py` is partially functional; the naming does not make authority obvious.
- Root-level `test_player_prop_filtering.py` is outside configured `testpaths = ["tests"]` and is not part of the normal full suite.
- Root-level `diagnose_candidates.py`, `validate_caps.py`, and `validate_caps_full.py` look like operational/scratch utilities rather than package-owned commands.
- Multiple old migration, audit, and implementation Markdown files remain at repository root. They contain useful history but make current authority hard to identify.
- The workspace contains many ignored/generated artifacts: 5,167 files under `outputs/`, 2,136 under `tests_artifacts/`, top-level logs/run outputs, many `.pytest_tmp*` directories, `test_tmp`, `test_tmp_debug`, and `__pycache__`.
- `data/history/` contains large local CSVs plus timestamped backups. These are NBA operational history, not source-controlled MLB training data.

## C. Test results

### Valid result

Exact valid command:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

The command had to run outside the managed filesystem sandbox so pytest could access its temporary directory.

```text
2767 passed, 31 xfailed in 235.46s (0:03:55)
```

There were no failures or errors in the valid run. The 31 expected failures are existing legacy/experimental xfails and should not be treated as new failures. They still need periodic review because `TESTING.md` says XPASS/xfail hygiene matters.

### Stale prior baselines

- Recent MLB handoff claim: `528 passed` — stale.
- `TESTING.md`: `659 passed, 31 xfailed` — stale.
- Current valid working-tree truth: `2767 passed, 31 xfailed in 235.46s`.

The large increase is consistent with the current suite's parametrization and accumulated tests. The valid result, not either old count, is the audit baseline.

### Invalid temp-path attempts

The following attempts are **environment/temp-path failures, not repository test failures**:

1. `py -3.13 -m pytest tests --basetemp=.pytest_tmp_audit_2026_06_19 -q`
2. `py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q` inside the managed sandbox
3. `py -3.13 -m pytest tests -q` inside the managed sandbox

In each sandboxed attempt, pytest lost access to either the repository base temp directory or `C:\Users\edohe\AppData\Local\Temp\pytest-of-edohe`. This produced cascading `tmp_path` setup errors and a final `PermissionError: [WinError 5] Access is denied`. One attempt was also terminated by the command runner while pytest was unwinding. Those progress-line `E`/`F` markers are invalid evidence because the same tree and documented command passed completely once temp-path access was available.

### Test coverage gaps

| Requested area | Existing coverage | Gap |
| --- | --- | --- |
| Multi-sport routing | `test_sport_registry.py` checks registration and duplicate protection. | No test routes a dated run from a common entrypoint to a sport plugin/provider/report boundary. |
| MLB HR adapters | Protocol conformance, sample provider, stubs, and factory are tested. | No integrated adapter composition test joins odds, stats, pitcher, weather, park, schedule, and lineup data. |
| Odds provider behavior | Mock payload normalization and missing-key behavior are tested. | No live contract test, date filtering test, quote freshness policy, deduplication policy, event/schedule join, or cache/retry behavior for MLB. |
| Missing API key behavior | Good tests for MLB odds missing-key and sample-keyless behavior; NBA research providers also have missing-key tests. | No single cross-sport policy defining fail-closed versus sample fallback, and no test for inconsistent NBA/MLB key names. |
| Sample provider behavior | Deterministic three-candidate sample and CLI smoke are tested. | No immutable `eligible_for_betting=False` contract and no proof that sample rows cannot reach betting/stake artifacts. |
| No-Kelly safety gates | NBA Kelly tests are extensive; non-`player_points` markets are blocked. | MLB is protected only by isolation and the NBA points-only lock. There is no explicit MLB/sport gate in Kelly and no explicit probability-provenance requirement. |
| Historical training dataset | None for MLB. | No schema, point-in-time joins, rolling-window tests, outcome labels, duplicate-game tests, or dataset reproducibility tests. |
| NBA regression protection | The overall suite is broad and green. New compatibility tests preserve import identity. | The new `test_nba_backwards_compatibility.py` checks imports only, not a golden NBA prediction/board comparison across the multi-sport boundary. |
| MLB score validity | Tests assert heuristic ordering, thresholds, `Elite`, fair estimates, and unit sizing. | Tests validate presentation mechanics, not predictive validity. They currently lock in unsafe betting semantics. |
| Leakage prevention | None for MLB. | No test proves that current-game outcomes, future games, closing odds, or revised lineup/weather data are excluded from training features. |

## D. Multi-sport readiness

### Readiness scores

- **Core architecture: 4/10** — useful contracts and package boundaries exist, but the canonical runtime, models, providers, scoring, and reports are still NBA-specific.
- **NBA stability: 8/10** — broad green suite and strong operating gates; reduced by monolith size, duplicated paths, stale docs, unrun live smoke, and 31 xfails.
- **MLB HR current readiness: 3/10** — sample heuristic and odds normalization work, but there is no live enrichment, trained probability, end-to-end watchlist pipeline, or safe terminology.
- **Historical training readiness: 1/10** — generic NBA history/reporting ideas exist, but MLB acquisition, dataset construction, training, backtesting, and leakage controls are absent.
- **Data provider readiness: 3/10** — NBA has several providers and fallbacks; MLB has only sample plus an unverified standalone odds adapter and stubs for all contextual data.
- **Safety gate readiness: 6/10** — NBA controls are substantial; MLB semantics and lack of explicit sport/probability gates materially reduce the score.
- **Documentation readiness: 4/10** — NBA operation/testing is documented in several places, but current authority, MLB execution, keys, limitations, and baselines are inconsistent.

### Target CourtVision Core assessment

| Core item | Status | Evidence and finding |
| --- | --- | --- |
| Odds normalization | Exists but NBA-specific | `courtvision/data/bdl_odds_adapter.py` and `courtvision/providers/the_odds_api_provider.py` are NBA-shaped. MLB has a separate normalizer and a duplicate odds schema. |
| Market models | Exists but NBA-specific | `courtvision/models.py`, `market/quality.py`, and `markets/prop_types.py` encode basketball stats/market names. |
| Projections | Exists but NBA-specific | NBA production projections exist. `core/projection_engine.py` is a small contract; MLB/WNBA/NFL models are placeholders. |
| Edge scoring | Exists but NBA-specific | Production scoring and thresholds are NBA-facing. The new confidence engine is research-only and not calibrated by sport/market. |
| Risk flags | Exists but NBA-specific | NBA has mature injury, context, identity, stale-odds, and exposure flags. There is no common sport risk contract. |
| Kelly/bankroll gates | Exists but NBA-specific | Quarter Kelly, per-pick cap, daily exposure cap, holds, and points-only lock exist. No sport/probability provenance field is required. |
| Reports | Exists but NBA-specific | Extensive NBA/operator/shadow reports exist. MLB has only a console report; no shared report contract or artifact manifest. |
| Provider registry | Exists but incomplete | `SportRegistry` stores provider-name strings and MLB has its own factory, but no shared provider registry resolves typed capabilities. |
| Config/env handling | Exists but incomplete | Multiple settings classes and env names exist. Provider defaults and The Odds API keys are inconsistent. |
| Sport plugins | Exists but incomplete | NBA compatibility module and MLB/WNBA/NFL/NHL placeholders exist. No common runtime plugin protocol or router exists. |

## E. MLB HR Research Mode readiness

| Capability | Status | Evidence and finding |
| --- | --- | --- |
| MLB schedule ingestion | Missing | No MLB schedule adapter. The odds adapter ignores `report_date` and returns its configured upcoming slate. |
| Probable pitchers | Missing | Odds candidates default pitcher to `TBD`; there is no pitcher schedule/source join. |
| Confirmed lineups | Missing | No MLB lineup provider, status model, or confirmation gate. |
| HR prop odds | Partially implemented | The Odds API payload normalization exists and is mock-tested. It is not joined to context, date-validated, freshness-gated, or live-verified in this audit. |
| Hitter Statcast-style features | Stub/sample only | Typed fields and sample values exist; `MLBStatsProvider` raises `NotImplementedError`. |
| Pitcher vulnerability features | Stub/sample only | Sample HR-allowed rate and pitch mix are scored; no real provider exists. |
| Pitch-type matchup features | Partially implemented | A deterministic weighted score exists, but inputs are sample data and handedness is explicitly a placeholder. |
| Weather features | Stub/sample only | Wind/temperature formulas exist; `MLBWeatherProvider` is a stub. Precipitation and roof status are not used by the engine. |
| Ballpark factors | Stub/sample only | Park-factor scoring exists; `MLBBallparkProvider` is a stub and dimensions/handedness adjustments are unused. |
| HR edge score | Partially implemented | A fixed heuristic confidence and uncalibrated fair-probability estimate exist. There is no fitted model, calibration, uncertainty, or validation. |
| Watchlist output | Partially implemented | Ranked console output exists. It is not a persisted, provenance-rich research artifact and uses betting-oriented tiers. |
| Research-only disclaimer | Partially implemented | Module docstrings and sample header say research/sample, but the report also says `Elite`, fair probability, and recommended units. |
| Kelly/betting blocked by default | Partially implemented | MLB is isolated and NBA Kelly is points-only, but MLB assessments lack explicit immutable betting-ineligibility fields and still emit unit advice. |

### MLB mode classification

**Current classification: sample/heuristic research prototype plus standalone live-odds normalization.**

It is not:

- a trained HR probability model;
- a calibrated edge model;
- a schedule/lineup-aware daily watchlist;
- a historical training pipeline;
- a backtesting or calibration pipeline;
- a live EV pipeline; or
- a betting/Kelly-approved mode.

## F. Historical MLB training readiness

This table assesses MLB-specific support. NBA history/reporting code does not make these items implemented for MLB.

| Capability | Status | Evidence and finding |
| --- | --- | --- |
| Historical MLB data download/storage | Missing | No MLB raw/normalized storage layout, downloader, manifest, or versioning. |
| Baseball Savant / Statcast ingestion | Missing | Only the word “Statcast-style” and placeholder fields appear. No ingestion code or dependency exists. |
| Retrosheet ingestion | Missing | No code, tests, docs, or data path. |
| Lahman/SABR ingestion | Missing | No code, tests, docs, or data path. |
| Historical weather ingestion | Missing | No provider or historical join. |
| Historical odds ingestion | Missing | Current MLB adapter is upcoming/live quote normalization only. |
| Batter-game training dataset creation | Missing | No dataset schema or builder. |
| Rolling features excluding current game | Missing | No MLB point-in-time rolling feature implementation or test. |
| HR outcome labeling | Missing | No batter-game HR label builder or source-of-truth definition. |
| Model training | Missing | `MLBProjectionModel` is a weighted-recent placeholder; `HRPropEngine` uses fixed formulas. |
| Backtesting | Missing | NBA out-of-sample tooling does not accept an MLB dataset/model contract. |
| Calibration reports | Missing | No MLB reliability, Brier/log-loss, calibration curve, or segment report. |
| CLV/ROI reports | Missing | HR tracking fields are display placeholders only. No recorded quotes, closing lines, or graded MLB bets exist. |
| Leakage tests | Missing | No temporal cut, as-of join, future-row, current-game, or closing-odds leakage tests. |

## G. Data acquisition audit

| Provider/source | Present in code? | Documented? | API key required? | Missing-key behavior | Sample fallback? |
| --- | --- | --- | --- | --- | --- |
| The Odds API | Yes. NBA research provider and separate MLB HR adapter. | NBA key is in `.env.example`; MLB key/region/market variables are not. | Yes for live calls. | NBA returns empty data plus missing-credential diagnostics. MLB raises a clean configuration error before network access and CLI exits nonzero. | MLB defaults to sample only when `--provider` is omitted or explicitly `sample`; selecting `odds_api` does not auto-fallback. |
| SportsGameOdds | No implementation. Mentioned in an older provider-migration audit. | Design/audit discussion only. | Would require credentials, but no contract exists. | Not applicable. | No. |
| API-SPORTS / API-NBA | Yes, NBA stats-only research client and manual schedule resolver. | Yes: `.env.example` and `docs/api_nba_research_mode_audit.md`. | Yes for API data. | Returns empty provider responses with `missing_credentials`; manual schedule can still resolve schedule rows, but no player stats are fabricated. | Manual schedule fallback only; no sample stats fallback. |
| BALLDONTLIE | Yes, both monolithic and package clients; canonical NBA dependency/fallback. | Yes: README, `.env.example`, and several integration docs. | Yes for live NBA data. | Package requests raise clear missing-key diagnostics; provider manager can continue to another configured provider. The monolithic runtime has no keyless sample NBA mode. | No sample fallback. SportsDataIO may be an alternate provider. |
| SportsDataIO | Yes, NBA client and provider manager. | Yes: `.env.example` and `SPORTSDATAIO_INTEGRATION.md`. | Yes. | `is_configured()` allows the manager to skip it; auth/API/empty responses fall through to BallDontLie when available. | No sample fallback. |
| OpticOdds | No. | No. | Unknown/not configured. | Not applicable. | No. |
| Baseball Savant / Statcast | No ingestion; placeholder terminology only. | Mentioned as future/placeholder. | Public data may not require a conventional key, but no acquisition policy exists. | Not applicable. | Sample feature values only, not a source fallback. |
| Retrosheet | No. | No. | No API key expected, but no ingestion exists. | Not applicable. | No. |
| Lahman / SABR | No. | No. | No key policy exists. | Not applicable. | No. |
| Open-Meteo | No. | No. | No key policy exists in the repo. | Not applicable. | No. |

### Configuration inconsistencies

- `.env.example` documents `THE_ODDS_API_KEY`, while the MLB adapter reads `COURTVISION_ODDS_API_KEY` plus undocumented `COURTVISION_ODDS_REGION` and `COURTVISION_ODDS_MARKETS`.
- `.env.example` documents `DATA_PROVIDER_PRIORITY` and says its default is BallDontLie.
- `ProviderSettings.from_env()` reads `DATA_PROVIDER_PRIORITY` or `NBA_PROVIDER_PRIORITY` and defaults to BallDontLie.
- `ProviderManager._resolve_priority()` reads only `NBA_PROVIDER_PRIORITY` and otherwise defaults to `sportsdataio, balldontlie`; it does not use `ProviderSettings.provider_priority`.
- The canonical monolithic adapter constructs `Settings()` directly before creating `ProviderManager`, further weakening the apparent environment contract.

## H. Safety and betting gates

| Safety requirement | Status | Evidence and risk |
| --- | --- | --- |
| No Kelly without projection probability | Incomplete | Kelly requires positive edge, odds, and confidence, but not an explicit calibrated probability or probability-source field. |
| No betting without odds freshness | Partially enforced | NBA elite/projected-Kelly gates block stale/missing timestamps. `run_kelly_stakes.py` itself does not require an odds timestamp and trusts the elite board. MLB odds have no freshness gate. |
| No Elite label without validation | Not satisfied for MLB | NBA has substantial gates. MLB maps heuristic scores directly to `Elite`, `Strong`, and `Watchlist` with no historical validation. |
| No live betting from sample-only data | Partially enforced | NBA operator live-source gates reject synthetic/non-live rows; MLB is not connected to NBA boards, but the sample report still recommends units. |
| Clear research-only mode | Strong for API-NBA; weak for MLB | API-NBA rows carry `mode=research` and `eligible_for_betting=False`. MLB relies on prose/docstrings and lacks equivalent hard fields. |
| Exposure caps | Implemented for NBA | Per-pick 2% Kelly cap, default 8% daily exposure cap, and board team/game/player caps exist. No MLB staking should exist. |
| Bankroll config | Implemented but fragmented | Kelly CLI defaults to $1,000 and supports a daily exposure setting. There is no central sport-aware bankroll policy. |
| Missing-data flags | Strong for NBA; incomplete for MLB | NBA produces diagnostics and rejection reasons. MLB sample values appear complete and do not expose provenance/completeness flags for every feature. |
| Manual review/hold | Implemented for NBA | Identity conflicts, review flags, context, and HOLD policies zero stakes. Not generalized to sport plugins. |
| Game status/lock | Implemented for NBA | Conservative default blocks unknown/locked/final/live games in betting mode. MLB has no schedule/lock integration. |

### Highest-priority safety gap

The MLB report's output is internally contradictory: it calls itself sample/research data while emitting `Elite`, an estimated fair probability, a recommended unit size, and wager-style tracking. This is a **P0 before any user-facing MLB run** and **P1 for architecture sequencing**. Safety language and machine gates must be fixed before the sport registry is allowed to route MLB execution or before live providers are composed.

## I. Documentation audit

| Documentation need | Status | Finding |
| --- | --- | --- |
| How to run NBA pipeline | Documented | README and `TESTING.md` describe `run_today.bat`, direct runtime, outputs, grading, and dashboards. Some authority is spread across many files. |
| How to run MLB HR research mode | Incomplete | README shows only a placeholder projection one-liner. It does not document `python -m courtvision.sports.mlb.hr_report`, provider choices, or output limitations. |
| Required API keys | Incomplete | NBA providers are documented. MLB The Odds API variables are absent and conflict with the NBA key name. |
| Sample/offline mode | Partially documented | README calls new sport models offline-safe placeholders. It does not explain MLB HR sample mode or prohibit betting use. |
| Testing | Documented but stale | Command is correct; baseline is stale (`659` versus current `2767`). `run_tests.bat` runs only stable tests. |
| Data folders | Partially documented | README explains `data/history` and `outputs/runtime`; no MLB raw/normalized/feature/dataset layout exists. |
| Model validation | Fragmented | Strong NBA audit/shadow documents exist. No MLB validation standard, metrics, minimum samples, or temporal split contract exists. |
| Limitations | Partially documented | Roadmaps say placeholders are not wager-ready, but the MLB CLI contradicts this with betting language. |
| Next roadmap | Present but untracked | `COURTVISION_2_ROADMAP.md` and `SPORTS_EXPANSION_PLAN.md` exist in the working tree but are untracked and not authoritative. |

## J. Gap table

| Area | Current status | Evidence/file path | Risk | Recommended fix | Priority |
| --- | --- | --- | --- | --- | --- |
| Audit reproducibility | Multi-sport/MLB implementation and tests are untracked. | `git status --short`; `courtvision/core/`, `courtvision/sports/`, MLB tests | Clean clones omit audited behavior. | Review and intentionally version the approved baseline after safety corrections; do not commit runtime artifacts. | P0 |
| MLB terminology | Heuristic sample can be `Elite` and recommend units. | `mlb/hr_prop_engine.py`, `mlb/hr_report.py`, `test_mlb_hr_prop_engine.py` | Users may treat research output as betting advice. | Replace with Research Watchlist/Candidate/Data Quality/No Betting Recommendation. | P0 |
| MLB betting eligibility | No immutable betting-ineligibility field on assessments. | `HRPropAssessment` | Future routing could accidentally promote rows. | Add `eligible_for_betting=False`, research mode, approval status, and hard serialization tests. | P0 |
| MLB Kelly isolation | Isolation is structural/implicit, not a sport-level gate. | `scripts/run_kelly_stakes.py` | Refactors could admit MLB rows if schemas converge. | Require supported sport, calibrated probability provenance, live source, and approval status before Kelly. | P0 |
| Test truth | Handoff and `TESTING.md` counts are stale. | `TESTING.md`; audit run | False baseline can hide collection drift or confuse CI. | Update baseline only after audit approval and record collection environment. | P0 |
| Canonical runtime | NBA is still a large monolith. | `courtvision_ai.py`, README | High change coupling and hard multi-sport routing. | Preserve behavior; wrap behind an NBA plugin adapter before extracting internals. | P1 |
| Sport routing | Registry is metadata only. | `core/sport_registry.py` | Registering a sport does not make it runnable or safe. | Add typed plugin/runtime capability contracts after MLB safety fields exist. | P1 |
| Provider registry | Separate NBA manager and MLB factory. | `clients/provider_manager.py`, `mlb/adapters/provider_factory.py` | Divergent behavior, keys, retries, and fallbacks. | Add capability-based provider registry with explicit sport/mode boundaries. | P1 |
| Provider configuration | Key names and priority defaults conflict. | `.env.example`, `config/__init__.py`, `provider_manager.py`, MLB odds adapter | Operators can configure the wrong key or receive unexpected provider order. | Define one canonical env contract with backward-compatible aliases and tests. | P1 |
| Odds schemas | Duplicate quote dataclasses and provider-specific shapes. | `core/odds_engine.py`, `mlb/adapters/base.py` | Cross-sport joins and freshness rules will diverge. | Define one versioned normalized quote contract with source timestamps and market identity. | P1 |
| MLB schedule | Missing. | No MLB schedule adapter | Cannot reliably scope date, games, postponements, or doubleheaders. | Add a research-only schedule contract/provider and fixture tests. | P1 |
| Probable pitchers/lineups | Missing. | No MLB provider/module | Matchups may be wrong or stale. | Add status/provenance models and fail closed when required context is absent. | P1 |
| MLB stats | Stubs/sample only. | `adapters/stats_provider.py`, `sample_provider.py` | Scores are demonstrations, not data-driven predictions. | Add point-in-time hitter/pitcher ingestion with explicit source and as-of time. | P1 |
| Weather/ballpark | Scorers exist; acquisition stubs. | `weather_factor.py`, `ballpark_factors.py`, provider stubs | Sample context may be mistaken for live context. | Add venue mapping, roof/weather status, park source/version, and missing-data flags. | P1 |
| MLB fair probability | Fixed formula, not calibrated. | `hr_prop_engine.py:score_market` | EV and edge are not statistically defensible. | Suppress fair/EV output until trained and calibrated out of sample. | P0 |
| MLB watchlist artifact | Console only, weak provenance. | `hr_report.py` | Not reproducible or auditable. | Write isolated research artifacts with date, source, as-of time, feature completeness, and no-bet status. | P1 |
| Historical MLB sources | Entire acquisition layer missing. | No MLB data scripts/storage | No training or validation path. | Define raw/normalized manifests for Savant/Statcast and game metadata first. | P1 |
| Dataset leakage | No MLB point-in-time tests. | No dataset/tests | Inflated backtests and unsafe promotion. | Require as-of joins and rolling features that shift/exclude current game. | P0 |
| Backtesting/calibration | Missing for MLB. | No MLB evaluation modules | No basis for labels, probability, or staking. | Add temporal splits, baseline comparisons, calibration, and segment reports. | P1 |
| Kelly probability contract | Edge can reach Kelly without explicit probability provenance. | `betting/kelly.py`, `scripts/run_kelly_stakes.py` | Unvalidated edge may generate stakes. | Require calibrated model probability and version/source before eligibility. | P1 |
| Kelly odds freshness defense | Freshness is upstream only. | `runtime_gates.py`, `run_kelly_stakes.py` | Direct/stale board input can bypass timestamp validation. | Revalidate timestamp and live source defensively in the Kelly runner. | P1 |
| NBA compatibility test | New test checks imports only. | `test_nba_backwards_compatibility.py` | Multi-sport changes could alter outputs while imports still pass. | Add deterministic golden NBA projection/selection equivalence around the plugin boundary. | P1 |
| MLB adapter integration tests | Unit tests only. | MLB adapter tests | Joins/provenance failures remain invisible. | Add an all-fixture research composition test with missing-source matrices. | P1 |
| Reports namespaces | Three overlapping report areas. | `reporting/`, `reports/`, `evaluation/` | Confusing ownership and future duplicate code. | Document authority first; consolidate only in a later surgical migration. | P2 |
| Repository hygiene | Large ignored artifact and backup footprint. | `outputs/`, `tests_artifacts/`, `.pytest_tmp*`, `data/history/*backup*` | Slow audits, accidental handling, and unclear source/runtime boundary. | Publish retention/cleanup policy; do not delete during feature work. | P2 |
| Root scripts/docs | Multiple old/scratch surfaces. | root `MIGRATION_*`, diagnostics, validators | Current instructions are hard to locate. | Add an authority index; archive only after explicit review. | P2 |

## K. Next implementation plan

### Phase 0: Stabilize current repo

- Freeze and record the dirty-tree baseline without overwriting user changes.
- Correct MLB research-only language and add machine-enforced betting-ineligibility.
- Remove all MLB unit/Kelly/bet recommendations until promotion criteria are satisfied.
- Add safety regression tests proving sample and incomplete live data cannot produce betting artifacts.
- Reconcile test baseline documentation and environment-specific pytest guidance.
- Reconcile provider environment names and priority semantics without changing current NBA defaults accidentally.
- Establish which untracked multi-sport files are approved to become authoritative; commit only with explicit user approval.

Exit criteria:

- Full suite remains green.
- MLB sample output contains no `Elite`, unit, wager, Kelly, or betting recommendation.
- Every MLB row is machine-marked research-only and betting-ineligible.
- A clean approved checkout can reproduce the test baseline.

### Phase 1: Multi-sport core registry

- Define a typed sport plugin protocol for schedule, candidate assembly, projection/research scoring, reports, and capabilities.
- Define a capability-based provider registry separate from provider implementation.
- Define one normalized odds quote with source/as-of/freshness fields.
- Wrap the existing NBA runtime as the NBA plugin; do not rewrite its internals.
- Route only explicit research commands through the registry initially.

Exit criteria:

- NBA golden behavior is unchanged.
- Unsupported sport/provider combinations fail closed.
- Registry metadata cannot imply betting approval.

### Phase 2: MLB HR research/watchlist mode

- Build a research-only candidate assembler joining schedule, probable pitchers, lineup status, odds, hitter/pitcher features, weather, and park data.
- Preserve deterministic sample mode as the offline default.
- Add completeness, freshness, provenance, and as-of fields.
- Persist isolated watchlist and diagnostics artifacts.
- Use only research rankings; no fair probability, EV, Kelly, Elite, or unit output.

Exit criteria:

- Fixture-backed end-to-end MLB watchlist works with no key.
- Missing critical inputs fail closed or downgrade data quality visibly.
- Live odds-only mode never pretends stats/context enrichment exists.

### Phase 3: Historical MLB training dataset

- Define raw, normalized, and feature storage with manifests and source versions.
- Ingest historical game/schedule, batter, pitcher, Statcast-style, park, weather, lineup, and odds data where legally/operationally available.
- Build one row per batter-game opportunity with stable IDs.
- Create HR labels and leakage-safe rolling features using strict as-of joins.
- Add deterministic rebuild and data-quality reports.

Exit criteria:

- Rebuilding a fixed date range produces the same dataset and manifest.
- Current-game and future information are excluded by tests.
- Missing historical odds/weather are explicit, never silently imputed as live truth.

### Phase 4: Backtesting and calibration

- Establish simple baselines before complex models.
- Use time-ordered train/validation/test splits and season/park/player segmentation.
- Report log loss, Brier score, calibration, discrimination, coverage, and stability.
- Add walk-forward backtesting and leakage audits.
- Keep all outputs research-only.

Exit criteria:

- Candidate model beats named baselines on held-out periods.
- Calibration and uncertainty are documented by segment.
- Results reproduce from pinned data/model versions.

### Phase 5: Live odds + EV

- Complete event/date joins, quote identity, deduplication, and freshness handling.
- Store opening/current/closing snapshots with provider provenance.
- Compute EV only from a validated, calibrated probability and valid current odds.
- Produce research EV watchlists, not stake recommendations.

Exit criteria:

- Every EV row has model version, calibrated probability, quote timestamp, book, market key, and freshness status.
- Stale, sample, incomplete, or uncalibrated rows cannot emit EV.

### Phase 6: Promotion gates for Elite/Kelly

- Define minimum sample sizes, time spans, calibration tolerances, stability, CLV, and shadow-performance criteria.
- Require explicit human approval and versioned promotion status per sport/market/model.
- Add sport/model approval checks to operator boards and Kelly.
- Introduce conservative exposure only after shadow validation; retain hard rollback.

Exit criteria:

- No model can self-promote.
- Kelly requires approved sport/market/model, calibrated probability, fresh live odds, and complete provenance.
- Promotion and rollback decisions are auditable.

## L. Immediate next 10 tasks

These are implementation tasks for later approval. They are intentionally ordered with safety before architecture and data acquisition.

### 1. Freeze the audited baseline

- **Files likely touched:** `docs/COURTVISION_AUDIT_2026_06_19.md`, `TESTING.md`, possibly a CI workflow or baseline manifest after approval.
- **Acceptance criteria:** Record the exact intended multi-sport file set; distinguish tracked, untracked, generated, and user-owned files; update the valid test baseline; do not stage runtime/history artifacts; no commit without explicit approval.
- **Tests to add/run:** `py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q`; verify a clean approved checkout collects the same suite.

### 2. Replace MLB betting language with research-only language

- **Files likely touched:** `courtvision/sports/mlb/hr_prop_engine.py`, `courtvision/sports/mlb/hr_report.py`, `tests/test_mlb_hr_prop_engine.py`, `tests/test_mlb_hr_adapters.py`.
- **Acceptance criteria:** Output uses `Research Watchlist`, `Candidate`, `Research Score`, `Data Quality`, and `No Betting Recommendation`; no `Elite`, `Strong`, recommended unit, wager, or bankroll language remains in MLB output.
- **Tests to add/run:** Update MLB report snapshots/assertions; run all MLB tests and the full suite.

### 3. Add immutable MLB research-only safety fields

- **Files likely touched:** `courtvision/sports/mlb/hr_prop_engine.py`, `courtvision/sports/mlb/hr_report.py`, possibly a new small shared research-status contract under `courtvision/core/`.
- **Acceptance criteria:** Every serialized MLB assessment contains `mode=research`, `eligible_for_betting=False`, `betting_approval_status=research_only_not_betting_approved`, `kelly_eligible=False`, and a no-betting reason that callers cannot override through provider data.
- **Tests to add/run:** Constructor/serialization tests, malicious/truthy input tests, and CLI output tests.

### 4. Hard-block MLB and unapproved models in Kelly/stake generation

- **Files likely touched:** `scripts/run_kelly_stakes.py`, `courtvision/runtime_gates.py` or a new narrow approval-gate module, `tests/test_kelly.py`.
- **Acceptance criteria:** Kelly rejects non-NBA/unapproved sport rows, sample rows, research rows, missing calibrated probability, and missing model approval/version before sizing; existing approved NBA fixture behavior is unchanged.
- **Tests to add/run:** Add parametrized sport/mode/approval/probability hard-block tests; run focused Kelly/operator tests and the full suite.

### 5. Prove sample mode cannot create betting recommendations

- **Files likely touched:** `tests/test_mlb_hr_adapters.py`, `tests/test_mlb_hr_prop_engine.py`, a new `tests/test_mlb_research_safety.py`.
- **Acceptance criteria:** The sample CLI and serialized rows contain no betting terms or positive stake/unit fields; attempting to pass sample MLB rows into betting/kelly adapters fails closed; no operator betting artifact is written.
- **Tests to add/run:** End-to-end keyless sample safety test, forbidden-token assertions, and artifact non-creation assertions.

### 6. Reconcile provider environment and fallback contracts

- **Files likely touched:** `.env.example`, `courtvision/config/__init__.py`, `courtvision/clients/provider_manager.py`, `courtvision/sports/mlb/adapters/odds_api_provider.py`, provider tests, README/docs.
- **Acceptance criteria:** One canonical The Odds API key name with a documented backward-compatible alias; provider priority has one defined source/default; missing-key outcomes are explicit by mode; NBA behavior remains unchanged.
- **Tests to add/run:** Env precedence/alias tests, provider priority tests, missing-key/fallback matrix, existing provider suites, full suite.

### 7. Add the sport/plugin registry after safety gates exist

- **Files likely touched:** `courtvision/core/sport_registry.py`, new narrow plugin/provider contract modules, `courtvision/sports/*/__init__.py`, `tests/test_sport_registry.py`, new routing tests.
- **Acceptance criteria:** Registry resolves typed research capabilities without importing/running providers; unsupported capabilities fail closed; betting approval is separate from sport registration; NBA remains routed through its compatibility adapter.
- **Tests to add/run:** Registration, duplicate, lazy-load, unsupported capability, research-only, and NBA golden routing tests.

### 8. Define MLB historical storage and batter-game schemas

- **Files likely touched:** new modules under `courtvision/sports/mlb/data/`, schema docs under `docs/`, fixture files under `tests/fixtures/mlb/`; no runtime data committed.
- **Acceptance criteria:** Versioned schemas exist for raw source manifests, games, batters, pitchers, lineups, parks, weather, odds, and one-row-per-batter-game training records; stable IDs and as-of timestamps are mandatory.
- **Tests to add/run:** Schema validation, duplicate key rejection, source manifest, timezone, doubleheader, and missing-field tests.

### 9. Build leakage-safe MLB feature and label scaffolding

- **Files likely touched:** new `courtvision/sports/mlb/training/` modules, MLB fixtures, new training tests.
- **Acceptance criteria:** HR outcome labels are defined from completed games; rolling hitter/pitcher features are shifted to exclude the current game; joins enforce source `available_at <= prediction_as_of`; future rows and closing odds cannot enter features.
- **Tests to add/run:** Current-game exclusion, future-game exclusion, revised-record as-of, doubleheader ordering, late lineup/weather, and deterministic rebuild tests.

### 10. Add the first reproducible MLB dataset build and research baseline

- **Files likely touched:** new thin script under `scripts/`, MLB training/data modules, dataset manifest/report modules, tests and docs.
- **Acceptance criteria:** A fixed fixture date range builds a deterministic batter-game dataset and manifest; a simple named baseline can train/evaluate on time-ordered splits; output is explicitly research-only and contains no EV/Elite/Kelly/unit fields.
- **Tests to add/run:** End-to-end fixture build, manifest hash/rebuild, temporal split, leakage suite, baseline metric smoke tests, and full repository suite.

## Final audit conclusion

The current working tree is a healthy NBA repository with a green deterministic suite and a useful but non-authoritative multi-sport experiment. MLB HR Phase 2 preserves NBA behavior and keyless sample execution, but it is not yet a model or a complete research pipeline. The immediate engineering move is not another provider or scoring feature. It is to make the MLB boundary unambiguously research-only in both language and machine contracts, prove that sample/uncalibrated rows cannot produce betting recommendations, and only then establish common sport/provider routing and historical dataset infrastructure.
