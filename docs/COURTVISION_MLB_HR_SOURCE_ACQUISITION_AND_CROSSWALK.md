# CourtVision MLB HR Source Acquisition and Crosswalk Workflow

Status: research-only, local-file-only, default-deny. This workflow prepares
inputs for the first genuine MLB HR historical pack. It does not fetch data,
enable MLB betting, approve production use, or alter EV, Kelly, Elite, scoring,
or selection gates.

Stage every source in a new directory outside `data/manual`, `manual-data`,
`outputs`, history, runtime, and cache directories. Keep original downloads or
exports immutable. Perform column selection and crosswalk work on copies in the
staging directory.

## Acquisition record required for every source

Before transforming a source, record the following in staging notes or the
future pack manifest:

- source/provider name and the exact export product;
- local filename and whether it is an original export or a derived copy;
- acquisition timestamp with UTC offset and the operator who acquired it;
- requested and observed date range;
- row count, byte size, and SHA-256 hash of the original local file;
- source timezone, game-start-time semantics, and known coverage gaps;
- license/usage restrictions and any manual filters applied;
- the source field used for every player, team, game, venue, and time identity.

Do not label a renamed fixture or sample as real. Do not fill a missing identity
by guessing from a name, probable matchup, or row order.

## Source-acquisition checklist

### 1. Statcast pitch export

- Manually export a completed, narrow date/game range from the operator's
  approved Statcast/Baseball Savant access. No CourtVision command in this
  workflow performs the download.
- Retain pitch-level rows, including non-batted-ball pitches, for every selected
  batter-game.
- Preserve native `game_pk`, `batter`, `pitcher`, `game_date`, `player_name`,
  `home_team`, and `away_team` fields. The IDs must remain MLBAM IDs.
- Also retain the event and batted-ball fields required by
  `statcast.csv`: `events`, `description`, `stand`, `p_throws`, `inning`,
  `inning_topbot`, `pitch_type`, `launch_speed`, `launch_angle`,
  `hit_distance_sc`, and `bb_type`.
- Confirm all selected games are completed and that one `game_pk` never appears
  with two dates or team pairs.

Acceptance evidence: immutable original export, source metadata, hash, observed
row count, and a list of selected `game_pk` values.

### 2. Retrosheet game rows and HR labels

- Manually obtain the already-published Retrosheet event/game files needed for
  the same completed games. Retain the original files and Retrosheet's native
  game, player, and team identifiers.
- Derive `retrosheet_games.csv` and `retrosheet_events.csv` only in staging.
  The pack-facing `game_id`, `batter_id`, and `pitcher_id` fields are canonical
  MLBAM IDs after verified crosswalking; they are not raw Retrosheet IDs.
- Derive `is_home_run` from the Retrosheet event record. Retain `event_text`,
  `event_type`, and `rbi` so a reviewer can trace the label.
- Require one label row for every included Statcast batter-game, including
  zero-HR outcomes. Do not construct labels from odds or Statcast features.

Acceptance evidence: original Retrosheet files, transformation notes, source
hashes, native ID counts, label counts, and the validated crosswalk CSV.

### 3. MLBAM player ID crosswalk

- Use an independently saved, authoritative MLBAM/MLB player export or local
  provider file that carries MLBAM player IDs. Record the product name, export
  time, and source field names.
- Build the proposed crosswalk with one row per batter-game. Preserve the native
  Retrosheet batter ID when it exists and the canonical numeric MLBAM batter ID
  in separate columns.
- Treat name agreement as supporting evidence only. Names never create a match.
- Review traded players by game date and team. A stable player mapping may
  repeat across games; one Retrosheet player ID mapping to multiple MLBAM IDs,
  or the reverse, fails.
- If the native Retrosheet player ID is unavailable, leave it blank and retain
  the MLBAM evidence. The validator warns because it can check only the MLBAM
  side; do not invent a Retrosheet ID.

Acceptance evidence: local authoritative export, its hash, mapping method,
review timestamp, and a crosswalk dry run with `status: PASS`.

### 4. Historical weather file

- Use an already-local historical observation export, not a current forecast.
- Select the observation appropriate to the verified venue and first-pitch
  instant. Record station/provider, observation timestamp, timezone, and any
  interpolation rule.
- Produce one row per game with canonical `game_id`, `game_date`,
  `event_start_time`, exact `venue_name`, temperature, wind speed/direction,
  source name/type, collection time, and as-of date.
- Review domed/retractable-roof games explicitly. Never infer roof status or
  zero wind solely because a venue can close its roof.

Acceptance evidence: original observation export, venue/time selection notes,
unit conversions, hash, and one-to-one game coverage.

### 5. Ballpark and venue file

- Use an already-local, versioned ballpark/venue table with a documented HR
  factor methodology. Record whether factors are single-season, multi-season,
  handedness-specific, or normalized to 1.00.
- Preserve the provider's venue identifier/name, then map it explicitly to the
  exact pack `venue_name`. Do not fuzzy-match two similarly named venues.
- Produce exactly one factor row for each venue in the selected game set and no
  unrelated venues. Keep `team`, `park_factor_hr`, source/version, collection
  time, and as-of date.
- Confirm every factor is finite and positive and is valid for the game date.

Acceptance evidence: original factor/venue table, methodology/version, mapping
notes, hash, and exact venue coverage.

### 6. Historical HR odds/context file

- Use an already-local historical sportsbook or provider export containing
  genuine pregame batter HR prices. Synthetic prices, reconstructed prices,
  and current odds are not historical evidence.
- Retain the sportsbook/provider, original market and selection labels,
  American price, capture timestamp, scheduled start, player identity, team,
  opponent, home/away teams, and provider event ID when available.
- Crosswalk provider event/player IDs to the same canonical MLBAM game/player
  IDs used elsewhere. Preserve provider-native IDs as extra columns for audit.
- Reject prices captured at/after first pitch or more than 24 hours before the
  verified start. Keep distinct sportsbooks; reject repeated snapshots for the
  same game/player/sportsbook/capture identity.

Acceptance evidence: original timestamped export, timezone interpretation,
market filters, source hash, capture-time audit, and exact crosswalk evidence.

## Proposed batter-game crosswalk contract

The crosswalk is a staging/audit file. It is deliberately not written into
CourtVision operational folders and is not automatically consumed by the
historical builder.

Required headers are:

```text
game_date,retrosheet_game_id,mlbam_game_id,game_number,
retrosheet_batter_id,mlbam_batter_id,batter_name,
retrosheet_home_team_id,home_team,retrosheet_away_team_id,away_team,
retrosheet_batting_team_id,batting_team,
retrosheet_fielding_team_id,fielding_team,
player_mapping_source,game_mapping_source,team_mapping_source,verified_at
```

Additional audit columns are allowed. Every required header must be present.
`retrosheet_batter_id` is the only identity cell allowed to be empty; when it
is empty, the dry run warns and cannot verify the native-to-MLBAM player link.

| Column group | Rule |
|---|---|
| `game_date` | Exact `YYYY-MM-DD`. |
| `mlbam_game_id` | Positive 6-10 digit canonical MLBAM game ID. |
| `mlbam_batter_id` | Positive 6-10 digit canonical MLBAM batter ID. |
| `retrosheet_game_id` | Native `TTTYYYYMMDDN`; encoded home team/date/game number must match the row. |
| `retrosheet_batter_id` | If present, lowercase Retrosheet form ending in three digits. |
| Retrosheet team IDs | Supported three-letter Retrosheet codes such as `NYA`, `CHA`, and `LAN`. |
| MLB team fields | Canonical pack abbreviations such as `NYY`, `CWS`, and `LAD`; exact mappings only. |
| Batter/fielding context | Must be the same two-team set as home/away and the two roles must differ. |
| Mapping source fields | Nonempty, truthful local-source labels; sample/fixture provenance fails. |
| `verified_at` | ISO-8601 timestamp with UTC offset. |

For an ordinary single game, Retrosheet's terminal game marker is `0` and the
crosswalk `game_number` is `1`. Doubleheader markers `1`, `2`, or `3` must equal
`game_number`.

## Duplicate and conflict rules

The dry run fails when it finds:

- a repeated `(mlbam_game_id, mlbam_batter_id)` batter-game row;
- a repeated native `(retrosheet_game_id, retrosheet_batter_id)` row;
- one Retrosheet player ID mapped to multiple MLBAM IDs, or the reverse;
- one MLBAM player ID associated with conflicting normalized names;
- one Retrosheet game mapped to multiple MLBAM game/date/team contexts, or the
  reverse;
- one Retrosheet team ID mapped to conflicting or noncanonical MLB team codes;
- an MLBAM game/date/team context inconsistent with the encoded Retrosheet game
  ID; or
- a sample, fixture, mock, test, synthetic, dummy, fake, example, or placeholder
  batter identity/provenance label.

Repeated, consistent game/team/player mappings across different batter-games
are expected and pass.

## Read-only dry run

Run from the repository root against a staging file:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python scripts\mlb_dry_run_hr_crosswalk.py `
  C:\courtvision_staging\mlb_hr\2024-04-10-crosswalk.csv
```

The command performs no network access and writes no report. It prints `PASS`
or `FAIL`, total/valid/invalid row counts, distinct MLBAM and Retrosheet player
and game counts, duplicate/conflict counts, missing required ID cells, sample
identity rows, warnings, and every validation error. Exit code `0` means the
CSV passed structural and internal-consistency checks; exit code `2` means it
failed.

A pass does not prove that a plausible numeric ID exists in MLBAM. The operator
must compare the row with the retained authoritative local source evidence.

## Pack handoff and remaining blockers

After a crosswalk passes, review every warning and spot-check mappings against
the immutable sources. The research-only staging builder can then apply the
reviewed mapping to local Retrosheet labels, weather, and odds/context copies.
It derives the six fixed pack CSVs, writes an immutable manifest, and runs the
separate historical input-pack preflight described in
`docs/COURTVISION_MLB_HR_HISTORICAL_INPUT_PACK.md`.

The following still block the first genuine pack until supplied by an operator:

- independently collected and licensed real source files for the same games;
- authoritative local evidence proving each MLBAM player/game mapping;
- pitcher-ID crosswalking and review for the transformed Retrosheet labels;
- complete, timestamped pregame HR odds coverage and reviewed timezone rules;
- reviewed weather-station, first-pitch, venue, and park-factor mappings;
- enough aligned game and season coverage for dataset-readiness and leakage
  review; and
- explicit future approval before any production promotion.
