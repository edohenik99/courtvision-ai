# API-NBA Research Mode Audit

Date: 2026-06-03
Phase: Phase 1 smoke test plus Phase 2 design only
Status: Design only; no betting pipeline implementation

## Scope

This audit covers API-NBA from API-SPORTS as a stats and research provider. It does not replace BallDontLie, does not add any odds provider, and does not permit stats-only rows to enter Kelly staking, Elite labeling, or betting output.

Official API-NBA v2 docs reviewed: https://api-sports.io/documentation/nba/v2

## Current CourtVision Schemas

The local schema boundary is in `courtvision/models.py`:

| CourtVision model | Required fields |
|---|---|
| `Team` | `id`, `abbreviation`, `full_name` |
| `Game` | `id`, `date`, `home_team`, `visitor_team`, optional scores, `status` |
| `PlayerInfo` | `id`, `first_name`, `last_name`, `full_name`, `team_id`, `team_abbreviation`, `position` |
| `PlayerGameStats` | `player_id`, `player_name`, `team_id`, `team_abbreviation`, `game_id`, `game_date`, `minutes`, `points`, `rebounds`, `assists`, `threes`, `steals`, `blocks` |
| `MarketProp` | `game_id`, `player_id`, `player_name`, `vendor`, `prop_type`, `line_value`, `market_type`, over/under odds |

`MarketProp` is betting-facing and must not be produced by an API-NBA stats adapter.

## Field Mapping

### Games

Endpoint: `GET /games` with `date`, optional `season`, and `league=standard`.

| CourtVision field | API-NBA field | Notes |
|---|---|---|
| `Game.id` | `id` | Direct id mapping. |
| `Game.date` | `date.start` | Convert ISO datetime to CourtVision date string. |
| `home_team.id` | `teams.home.id` | Direct id mapping. |
| `home_team.abbreviation` | `teams.home.code` | API-NBA uses `code`. |
| `home_team.full_name` | `teams.home.name` | Direct name mapping. |
| `visitor_team.id` | `teams.visitors.id` | API-NBA uses `visitors`. |
| `visitor_team.abbreviation` | `teams.visitors.code` | API-NBA uses `code`. |
| `visitor_team.full_name` | `teams.visitors.name` | Direct name mapping. |
| `home_team_score` | `scores.home.points` | Available when game score exists. |
| `visitor_team_score` | `scores.visitors.points` | Available when game score exists. |
| `status` | `status.long` or `status.short` | Values differ from BallDontLie and need normalization. |

### Teams

Endpoint: `GET /teams` with `league=standard`.

| CourtVision field | API-NBA field | Notes |
|---|---|---|
| `Team.id` | `id` | Direct id mapping. |
| `Team.abbreviation` | `code` | API-NBA uses `code`; BallDontLie uses `abbreviation`. |
| `Team.full_name` | `name` | Direct name mapping. |

Useful extra API-NBA team fields include `nickname`, `city`, `logo`, `nbaFranchise`, and `leagues.standard.conference/division`. These can remain metadata until a research artifact needs them.

### Players

Endpoint: `GET /players` with at least one parameter such as `season`.

| CourtVision field | API-NBA field | Notes |
|---|---|---|
| `PlayerInfo.id` | `id` | Direct id mapping. |
| `first_name` | `firstname` | API-NBA uses no underscore. |
| `last_name` | `lastname` | API-NBA uses no underscore. |
| `full_name` | derived | Construct from `firstname` and `lastname`. |
| `team_id` | no stable top-level equivalent in player profile response | Requires roster/team cross-reference or stats-derived current team. |
| `team_abbreviation` | no stable top-level equivalent in player profile response | Requires team lookup from `teams` or player stats. |
| `position` | `leagues.standard.pos` when present | Different key path and may be absent. |

Player identity is usable, but active team assignment needs a careful adapter strategy.

### Player Game Stats

Endpoint: `GET /players/statistics`. The official endpoint accepts filters such as `game`, `id`, `team`, and `season`; it does not expose a direct `date` parameter. The smoke test should find a game id from `/games?date=...` first, then probe `players/statistics?game=<game_id>`.

| CourtVision field | API-NBA field | Notes |
|---|---|---|
| `player_id` | `player.id` | Direct id mapping. |
| `player_name` | `player.firstname` + `player.lastname` | Construct full name. |
| `team_id` | `team.id` | Direct id mapping. |
| `team_abbreviation` | `team.code` | API-NBA uses `code`. |
| `game_id` | `game.id` | Direct id mapping. |
| `game_date` | not present in player stat row in the documented sample | Join from `/games` by `game.id` when needed. |
| `minutes` | `min` | Same `MM:SS` style format; the existing BallDontLie minute parser can be copied or shared later. |
| `points` | `points` | Direct stat mapping. |
| `rebounds` | `totReb` | Total rebounds. |
| `assists` | `assists` | Direct stat mapping. |
| `threes` | `tpm` | Three-pointers made. |
| `steals` | `steals` | Direct stat mapping. |
| `blocks` | `blocks` | Direct stat mapping. |

### Team Statistics

Endpoint: `GET /teams/statistics` with `id` and `season`.

| Research field | API-NBA field | Notes |
|---|---|---|
| games played | `games` | Season aggregate. |
| points | `points` | Season aggregate. |
| rebounds | `totReb`, `offReb`, `defReb` | Season aggregate. |
| assists | `assists` | Season aggregate. |
| shooting | `fgm`, `fga`, `fgp`, `ftm`, `fta`, `ftp`, `tpm`, `tpa`, `tpp` | Useful for research baselines. |
| defense/activity | `steals`, `blocks`, `turnovers`, `pFouls`, `plusMinus` | Useful for context, not direct betting lines. |

The documented `teams/statistics` response does not include the team id in each stat object, so the adapter should carry the requested team id alongside the response.

## Missing Fields

| Missing field or feature | Impact | Design consequence |
|---|---|---|
| Betting market lines | Critical for betting | API-NBA cannot create `line`, `sportsbook_line`, or `line_source=live`. |
| Over/under prices and American odds | Critical for Kelly | API-NBA cannot create `odds`, `over_odds`, `under_odds`, or `american_odds`. |
| Sportsbook/vendor identity | Critical for betting provenance | API-NBA cannot set `vendor` or `bookmaker`. |
| Player prop market type | Critical for candidate construction | API-NBA stats can inform projections but cannot define a market offer. |
| Injury status | Medium | API-NBA v2 docs reviewed here do not provide an injury endpoint equivalent to BallDontLie injuries. |
| Active roster/current team certainty | Medium | Player profiles need roster or stats-derived team resolution. |
| Game date inside player stat rows | Low/medium | Join from `/games` by `game.id`. |

## Can API-NBA Replace BallDontLie For Stats?

Yes, for core stats, with a dedicated adapter. API-NBA covers games, teams, players, player game statistics, and team season statistics. It can support Research Mode baselines and postgame learning once mapped into `Game`, `Team`, `PlayerInfo`, and `PlayerGameStats`.

It should not be wired into the existing betting provider path during this phase. The next implementation should be additive: a new stats-only client that mirrors only the non-betting parts of the BallDontLie client interface.

## Can API-NBA Replace BallDontLie For Betting Odds?

No. The API-NBA v2 documentation reviewed for this audit does not provide player prop lines, over/under odds, sportsbook vendors, or betting market movement. BallDontLie's betting path must remain in place, and no API-NBA stat row should be normalized into a `MarketProp`.

The safe verdict is:

`usable for Betting Mode: no unless market lines/odds exist`

## Recommended Next Implementation Step

Create a separate `courtvision/clients/api_nba_client.py` in a future phase, limited to stats and research:

```python
class ApiNbaClient:
    def get_games_by_date(self, target_date: str) -> list[Game]: ...
    def get_active_players_for_team_ids(self, team_ids: set[int]) -> list[PlayerInfo]: ...
    def get_stats_for_player_ids(self, player_ids: Iterable[int], seasons: list[int]) -> list[PlayerGameStats]: ...
    def get_stats_for_player_ids_on_date(
        self,
        player_ids: Iterable[int],
        target_date: str,
        season: int | None = None,
    ) -> list[PlayerGameStats]: ...
```

Recommended guardrails for that future adapter:

- Use `x-apisports-key` with `API_NBA_KEY`, falling back to `API_SPORTS_KEY`.
- Return only stats models (`Game`, `Team`, `PlayerInfo`, `PlayerGameStats`) and research metadata.
- Never return `MarketProp`.
- Never synthesize betting lines from stats.
- Tag downstream research artifacts with `source=api_nba`, `mode=research`, and `eligible_for_betting=False`.
- Add invariant tests before any provider-manager integration so research rows cannot enter Kelly staking or Elite board selection.

## Phase 1 Files

| File | Purpose |
|---|---|
| `scripts/smoke_api_nba.py` | Read-only API-NBA endpoint smoke test. |
| `tests/test_smoke_api_nba.py` | Offline tests for key masking, provider status mapping, diagnostics, and verdict output. |
| `docs/api_nba_research_mode_audit.md` | This design audit. |
| `.env.example` | Documents `API_NBA_KEY` and `API_SPORTS_KEY`. |

BallDontLie client code, odds normalization, betting selection, Elite gates, Kelly logic, dashboards, run scripts, and scheduled workflow entrypoints are intentionally untouched in Phase 1.
