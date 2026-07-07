# CourtVision Environment Configuration Audit

- **Audit date:** 2026-07-07
- **Scope:** `.env.example` compared with environment-variable reads in tracked Python, PowerShell, and tool source
- **Secret handling:** Variable names and defaults were inspected; `.env` values were not printed or recorded.

## Executive finding

`.env.example` is not a complete contract for the canonical `CourtVisionAI` operator path. It mixes canonical NBA credentials with non-canonical provider settings and research-only credentials, omits several behavior-affecting operator variables, documents one unused fallback flag, and exposes two conflicting provider-priority names with different defaults in source.

No runtime configuration has been changed by this audit. The recommendations below require a separately reviewed documentation/configuration task because some variables affect provider selection, gates, markets, or wager sizing.

## `.env.example` comparison

| Variable in `.env.example` | Source usage | Audit status |
|---|---|---|
| `DATA_PROVIDER_PRIORITY` | Read by `courtvision.config.ProviderSettings.from_env()`, falling back to `NBA_PROVIDER_PRIORITY`; not read by the canonical `courtvision_ai.py` operator runtime. | Non-canonical and conflicting. It does not control `ProviderManager`, which reads `NBA_PROVIDER_PRIORITY` directly. |
| `ENABLE_PROVIDER_FALLBACK` | No tracked runtime read found. | Stale/unused. It must not be represented as an effective switch. |
| `SPORTSDATAIO_API_KEY` | Read by `ProviderSettings`, `SportsDataIOClient`, and the non-canonical `CourtVisionPro` provider path. | Valid for the alternate provider path; not required by the canonical `CourtVisionAI` run. |
| `BALLDONTLIE_API_KEY` | Read through the shared BallDontLie auth layer and required by the canonical `CourtVisionAI` path. | Canonical required secret. The spelling is authoritative. |
| `SPORTSDATAIO_BASE_URL` (commented) | Read by `ProviderSettings` and `SportsDataIOClient`. | Valid optional override for the non-canonical SportsDataIO path. |
| `API_NBA_KEY` | Read by API-NBA client and smoke/research scripts. | Valid research-only secret. |
| `API_SPORTS_KEY` | Accepted as a fallback alias by API-NBA client and smoke/research scripts. | Intentional research-only alias; `API_NBA_KEY` should be preferred. |
| `THE_ODDS_API_KEY` | Read by The Odds API provider, NBA smoke/market validation, and MLB research tools. | Valid research-only secret; not the canonical NBA operator credential. |

## Variables used in source but missing from `.env.example`

### Canonical NBA operator path

| Variable | Current source behavior | Contract recommendation |
|---|---|---|
| `BALLDONTLIE_REQUEST_TIMEOUT` | Canonical request/smoke timeout; default `30`. | Document as the single canonical BallDontLie timeout for `CourtVisionAI`. Freeze its resolved value during the evidence sprint. |
| `BALLDONTLIE_V1_BASE_URL` | Canonical v1 endpoint override. | Optional advanced override; normally leave unset and record the resolved default. |
| `BALLDONTLIE_V2_BASE_URL` | Canonical v2 endpoint override. | Optional advanced override; normally leave unset and record the resolved default. |
| `BALLDONTLIE_VENDORS` | Ordered/allowed vendor set; source default is `fanduel,draftkings,fanatics,caesars,betrivers`. | Behavior-affecting. Document and freeze it. |
| `COURTVISION_HTTP_RETRIES` | HTTP retry count; default `3`. | Operational setting; document and freeze. |
| `COURTVISION_HTTP_BACKOFF` | HTTP retry backoff; default `1.5`. | Operational setting; document and freeze. |
| `COURTVISION_MODE` | `run_today.ps1` and runtime gates default to `betting`; `research` changes gate behavior. | Canonical operator runs must resolve to `betting`; record it in `config_hash`. |
| `COURTVISION_BANKROLL` | `run_today.ps1` bankroll passed to Kelly; default `1000`. | Sizing-affecting. Document the unit and freeze it; do not change it as part of env cleanup. |
| `COURTVISION_MAX_DAILY_EXPOSURE` | Optional Kelly exposure override in `scripts/run_kelly_stakes.py`. | Sizing-affecting. Treat unset versus set as explicit contract states and freeze during the trial. |
| `ELITE_MARKET_MODE` | Canonical runtime market-mode control; default `points_only`. | Selection-affecting. Document and freeze; changes require trial restart. |
| `ELITE_ALLOWED_MARKETS` | Optional allowed-market list. | Selection-affecting. Document the resolved list and freeze it. |
| `COURTVISION_ENABLE_LEGACY_PIPELINE` | Opt-in legacy pipeline flag; unset/false by default. | Keep disabled for the canonical trial and record the resolved state. |
| `COURTVISION_PLAYER_POINTS_RECALIBRATION` | Experimental recalibration mode; default `off`; enabled is currently downgraded to shadow. | Research-only flag. Keep `off` for the frozen canonical cohort unless a separate trial explicitly preregisters shadow collection. |
| `COURTVISION_TELEGRAM_ENABLED` | Enables Telegram notification behavior. | Optional delivery-only setting; should require both credentials below and must not affect evidence inclusion. |
| `TELEGRAM_BOT_TOKEN` | Telegram secret. | Optional secret; omit from shared examples or show blank with a secret warning. |
| `TELEGRAM_CHAT_ID` | Telegram destination identifier. | Optional sensitive setting; show blank and do not include its value in evidence artifacts. |

### Non-canonical package and UI surfaces

| Variable | Source usage | Audit status |
|---|---|---|
| `NBA_PROVIDER_PRIORITY` | Read directly by `ProviderManager`; default there is `sportsdataio,balldontlie`. | Legacy but effective for `CourtVisionPro`; conflicts with `DATA_PROVIDER_PRIORITY`. |
| `BALLDONTLIE_BASE_URL` | Read by package `Settings`; default v1 URL. | Non-canonical duplicate of the canonical versioned URL controls. |
| `BALLDONTLIE_PER_PAGE` | Read by package `Settings`; default `100`. | Non-canonical package setting. |
| `BALLDONTLIE_TIMEOUT` | Read by package `Settings`; default `30`. | Non-canonical duplicate of `BALLDONTLIE_REQUEST_TIMEOUT`. |
| `COURTVISION_DEMO` | Read by the Streamlit application. | UI-only and outside the canonical operator runtime. |

### Research and standalone tooling

| Variable | Source usage | Audit status |
|---|---|---|
| `THE_ODDS_API_BASE_URL` | Read by standalone The Odds API MLB tools; default v4 endpoint. | Research/tooling override. |
| `COURTVISION_ODDS_API_KEY` | Required by the MLB research odds adapter. | Research-only duplicate credential name; distinct from `THE_ODDS_API_KEY` in current source. |
| `COURTVISION_ODDS_REGION` | MLB research adapter region; has a source default. | Research-only. |
| `COURTVISION_ODDS_MARKETS` | MLB research adapter market list; has a source default. | Research-only. |
| `ODDS_PAPI_KEY` | Read by standalone OddsPAPI probes and `test_env.py`. | Standalone research/tooling secret. |
| `ODDSPAPI_BASE_URL` | Read by standalone OddsPAPI probes; default v4 endpoint. | Standalone research/tooling override; note the provider's existing spelling. |

## Duplicate, stale, missing, and conflicting names

### Conflicting provider priority

- `DATA_PROVIDER_PRIORITY` is presented by `.env.example` as the provider control and defaults there to BallDontLie.
- `ProviderSettings.from_env()` reads `DATA_PROVIDER_PRIORITY`, then `NBA_PROVIDER_PRIORITY`, and otherwise defaults to BallDontLie only.
- `ProviderManager`, used by `CourtVisionPro`, ignores `DATA_PROVIDER_PRIORITY`, reads `NBA_PROVIDER_PRIORITY`, and otherwise defaults to SportsDataIO followed by BallDontLie.
- The canonical `CourtVisionAI` path directly uses BallDontLie and reads neither priority variable.

Therefore the same environment can communicate one priority while a different code path applies another. Neither variable should be described as controlling the canonical operator run.

### Duplicate endpoint and timeout controls

- Canonical `CourtVisionAI`: `BALLDONTLIE_V1_BASE_URL`, `BALLDONTLIE_V2_BASE_URL`, and `BALLDONTLIE_REQUEST_TIMEOUT`.
- Non-canonical package settings: `BALLDONTLIE_BASE_URL` and `BALLDONTLIE_TIMEOUT`.

The names are not interchangeable. A value set under the package name will not configure the canonical runtime equivalent.

### Duplicate odds-provider credentials

- `THE_ODDS_API_KEY` is used by The Odds API research provider and several NBA/MLB research tools.
- `COURTVISION_ODDS_API_KEY` is used by the MLB research adapter.
- `ODDS_PAPI_KEY` is for a different provider and standalone probes.

These should be grouped by provider and research surface, not treated as aliases. Any future consolidation needs an explicit migration with compatibility tests; silently copying one secret into another name would obscure provenance.

### Stale flag

`ENABLE_PROVIDER_FALLBACK` is documented but not read by tracked source. Provider fallback in `ProviderManager` is implemented by its provider loop, not controlled by this variable.

### BallDontLie spelling mistakes

The shared auth diagnostics recognize `BALDONTLIE_API_KEY`, `BALLDONTLIE_KEY`, `BALLDONTLIE_APIKEY`, `BALLDONTLIE_API_TOKEN`, and `BALLDONTLIE_TOKEN` as common mistakes. They are diagnostic warnings, not supported aliases. The contract must use only `BALLDONTLIE_API_KEY`.

## Recommended canonical environment contract

Use separate documented sections or example files so canonical operator configuration cannot be confused with research tooling.

### A. Canonical NBA operator

Required:

- `BALLDONTLIE_API_KEY` — secret; blank in examples.

Explicit frozen controls:

- `COURTVISION_MODE=betting`;
- `COURTVISION_BANKROLL`;
- `COURTVISION_MAX_DAILY_EXPOSURE` or an explicit `unset` state in the Day 0 manifest;
- `BALLDONTLIE_REQUEST_TIMEOUT`;
- `BALLDONTLIE_VENDORS`;
- `COURTVISION_HTTP_RETRIES`;
- `COURTVISION_HTTP_BACKOFF`;
- `ELITE_MARKET_MODE`;
- `ELITE_ALLOWED_MARKETS`, including an explicit empty state;
- `COURTVISION_ENABLE_LEGACY_PIPELINE=false`;
- `COURTVISION_PLAYER_POINTS_RECALIBRATION=off`.

Advanced optional endpoint overrides:

- `BALLDONTLIE_V1_BASE_URL`;
- `BALLDONTLIE_V2_BASE_URL`.

Optional notification settings:

- `COURTVISION_TELEGRAM_ENABLED`;
- `TELEGRAM_BOT_TOKEN`;
- `TELEGRAM_CHAT_ID`.

The operator contract should state each source default, accepted values, whether the setting affects selection/sizing, and whether changing it restarts the evidence trial. Secret values must never enter `config_hash`, logs, or committed files.

### B. Non-canonical `CourtVisionPro` compatibility

Keep the following in a clearly labeled compatibility section until `CourtVisionPro` is either promoted or retired:

- `NBA_PROVIDER_PRIORITY`;
- `SPORTSDATAIO_API_KEY` and `SPORTSDATAIO_BASE_URL`;
- `BALLDONTLIE_API_KEY`, `BALLDONTLIE_BASE_URL`, `BALLDONTLIE_PER_PAGE`, and `BALLDONTLIE_TIMEOUT`.

Do not advertise `DATA_PROVIDER_PRIORITY` as universal. A future cleanup should choose one priority name and make every intended consumer use it before deprecating the other.

### C. Research-only providers and tools

Place `API_NBA_KEY`, `API_SPORTS_KEY`, `THE_ODDS_API_KEY`, `THE_ODDS_API_BASE_URL`, `COURTVISION_ODDS_API_KEY`, `COURTVISION_ODDS_REGION`, `COURTVISION_ODDS_MARKETS`, `ODDS_PAPI_KEY`, and `ODDSPAPI_BASE_URL` under a prominent **research-only** heading. Their presence must not imply that the corresponding sport or provider is approved for the NBA operator path.

## Safe migration sequence

1. Approve the canonical contract without changing runtime values.
2. Capture the currently resolved non-secret values and defaults for the Day 0 evidence manifest.
3. Update `.env.example` in a separate, reviewed task; keep all secret examples blank.
4. Add configuration-contract tests before removing or renaming any variable.
5. Migrate one conflicting name at a time with explicit compatibility behavior.
6. Remove `ENABLE_PROVIDER_FALLBACK` only after confirming no external launcher depends on it.

Because provider choice, selection gates, recalibration, and Kelly inputs are production-risky, this audit recommends no automatic rename, fallback, or default change.
