# MLB factual backfill (research only)

The package entrypoint is `python -m courtvision.sports.mlb.fact_backfill`.
It reuses `extract_game_fact`, `extract_player_game_facts`, and `MLBFactStore`;
it does not change Hits probability, AB projection, production entrypoints,
or betting eligibility. Use the canonical Python interpreter explicitly.

## Operations

- `plan`: create a new immutable plan, explicit acquisition window, independent
  inventory window, maximum final games, and lifetime provider-request budget.
- `fetch --allow-provider`: fetch the missing schedule/feed evidence only.
- `materialize`: publish verified facts from preserved responses, with no I/O
  to providers. Publication is atomic per record; an interrupted batch is
  recovered by replaying the same evidence.
- `verify`: validate the manifest chain, raw hashes, inventory, identities,
  finality, and exact factual-record hashes.
- `resume`: replay existing evidence without network access. Adding
  `--allow-provider` explicitly allows missing requests within the original
  budget; it never raises that budget or refetches a successful response.
- `coverage`: report the gap against the preserved full-window schedule.
  `coverage_index` and `batter_coverage` expose package-owned player coverage.

`reconcile_inventory()` is an offline package operation that validates an
existing schedule capture, publishes its reconciled inventory, and checkpoints
state before any further request. Existing request budgets remain unchanged.

All operations require `--fact-root` and `--backfill-id`. `plan` also requires
`--season`, `--window-start`, `--window-end`, `--inventory-start`, and
`--inventory-end`. Defaults are three final games and four provider requests;
larger budgets require a separately authorized backfill plan. Do not launch a
full-season plan merely because the software accepts an explicit larger limit.

The persistent canonical root is `data/mlb/facts`. Raw response bytes, request
claims, inventory, and immutable manifest snapshots live below
`_backfills/<backfill_id>/`. This entire runtime root is already Git-ignored.
Source commits must never include these real response bodies or facts.

## Provider contract

Existing CourtVision paths are reused:

- `https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameTypes=R&startDate=...&endDate=...`
- `https://statsapi.mlb.com/api/v1.1/game/<gamePk>/feed/live`

The feed is selected over the standalone boxscore because it carries embedded
game identity, official date, finality, and the boxscore in the same response.
Both schedule and feed must explicitly agree on finality, teams, date, and
available final scores. Player identity uses MLBAM IDs. Provider participation
must match nonempty role stats. The provider's batters list can also contain
pitcher-only roster entries. A row is excluded from batting expectations only
when it has an explicit empty batting object, pitching stats and pitching-list
membership, explicit pitcher position, and no batting-order entry. Missing
batting objects or hitter participation still fail closed. This preserves
CORE-01's rule that empty role stats create no role fact; explicit zero counts
remain supplied facts. Missing individual stat counts remain `None`.

`schedule_revisions.py` owns the schedule policy extracted from prospective
Statcast history. The old private names remain compatibility aliases to the
same functions. One gamePk has one inventory entry, retaining every observed
state, duplicate count, revision count, immutable identity, selected state,
capture timestamp, and raw response digest. Input order never chooses a winner.
Immutable conflicts (game GUID, team IDs, venue ID, sport/league/query context,
game type, season) reject the whole factual inventory. Names remain descriptive.

Selection preserves the established order: capture time, finality rank,
official date, scheduled start, state digest, response digest. A newer non-final
capture can therefore supersede an older final capture. Window membership uses
the selected state's officialDate. Exact duplicates count once; doubleheaders
retain separate gamePk identities. Ambiguous full game payloads for the same
selected state fail closed instead of choosing between different score data.
Facts additionally reference the hash of their complete reconciliation record.

The transport sends no key or credentials, disables environment proxies and
redirects, makes one attempt per claim, and bounds response size/time. HTTP
error bodies are retained. A transport failure consumes a request slot; there
is no retry loop. Unknown-outcome interrupted requests block further network
activity. A killed operation leaves a lock for explicit investigation; never
delete a prior claim, response, manifest, or ledger record to force a resume.

## Coverage boundary

Expected participation and hashes are extracted from independent preserved
schedule/feed evidence, then compared to the ledger. Directory contents never
define the expected inventory. Unfetched games make participation unknown and
coverage incomplete. Unavailable full-window game counts are `null`, not zero.
Full coverage also requires all expected factual records, including the game
and pitching records, to match. The batter coverage object can be complete only
when a January 1 inventory covers the target's entire prior-date season window.

An opening-day retrospective rehearsal may qualify that narrowly scoped prior
date. Its observation/cutoff are the actual acquisition time, not backdated
pregame time. It does not qualify a current full season, activate predictions,
or authorize betting. Target game identity must come from the real inventory.

## Current live pilot gate

CV-MLB-FACT-BACKFILL-01 preserved one schedule response for 2026-01-01 through
2026-09-24 and initially rejected repeated gamePk observations. Follow-up 01A
reuses those exact bytes through the shared revision policy. Its 2,414 rows
resolve to 2,385 canonical final games, with 29 revisions, zero exact duplicate
states, and zero immutable conflicts. The original raw response and rejected
manifest snapshots remain preserved; reconciliation adds a new snapshot.

Two full-window games have `Completed Early` / `FR` final states accepted by
the historical schedule policy but unsupported by the existing factual-core
finality contract. They remain in expected coverage and request estimates;
they are never silently treated as nonfinal or omitted to claim completeness.
They block full-season readiness until separately resolved. No factual-core
finality rule is loosened here. The March 25 pilot contains one supported
Final/F game and remains bounded by the original three-game/four-request plan.
Full-season acquisition requires a separate checkpoint regardless of pilot success.
