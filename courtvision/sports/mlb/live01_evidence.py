"""Independent LIVE-01 prior-date inventories; no market or probability inputs."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest
from courtvision.sports.mlb.fact_backfill import (
    MLBFactBackfill, facts_from_feed, schedule_inventory,
)
from courtvision.sports.mlb.fact_backfill_evidence import (
    BASE, BackfillError, EvidenceJournal, digest, operation_lock,
    publish_document, read_document, source_ref,
)
from courtvision.sports.mlb.fact_ledger import FactLedgerConflict, MLBFactStore
from courtvision.sports.mlb.game_finality import classify_game_finality
from courtvision.sports.mlb.hits_season_ledger import BatterFactReference, BatterLedgerCoverage

# Exact provider codes, never a generic inferred postseason type.
# https://statsapi.mlb.com/api/v1/gameTypes
OFFICIAL_TYPES = frozenset({"R", "F", "D", "L", "W"})
HISTORY_THROUGH = date(2026, 9, 24)
CATCHUP_START = HISTORY_THROUGH + timedelta(days=1)
COVERAGE_SCHEMA = "cv_hits_live01_independent_inventory_v1"


def schedule_request(start: date, end: date, request_id: str) -> EvidenceRequest:
    return EvidenceRequest(request_id=request_id, evidence_class="stable_history",
        source_name="mlb_live01_schedule", provider="mlb_statsapi",
        url=f"{BASE}/api/v1/schedule?sportId=1&startDate={start}&endDate={end}")


def observed_types(payload: dict) -> tuple[str, ...]:
    values = {game.get("gameType") for day in payload["dates"] for game in day["games"]}
    if any(not isinstance(value, str) or not value for value in values):
        raise BackfillError("official gameType is missing")
    return tuple(sorted(values))


def inventory_from_capture(journal, record, start: date, end: date) -> dict:
    payload = journal.payload(record)
    types = observed_types(payload)
    if not set(types) <= OFFICIAL_TYPES:
        raise BackfillError("UNSUPPORTED_GAME_TYPE: " + ",".join(sorted(set(types) - OFFICIAL_TYPES)))
    value = schedule_inventory(payload, start.isoformat(), end.isoformat(),
        request=schedule_request(start, end, record["claim"]["request_id"]),
        source_digest=record["response"]["sha256"],
        captured_at=record["response"]["responded_at"],
        source_response_path=f'raw/{record["claim"]["sequence"]:06d}/body.bin',
        allowed_game_types=types)
    value["source_ref"] = source_ref(record)
    value["observed_game_types"] = list(types)
    return value


def _fact_reference(fact):
    return {"role": fact.role, "gamePk": fact.mlbam_game_id,
            "player_id": getattr(fact, "mlbam_player_id", None),
            "factual_record_hash": fact.factual_record_hash}


def verify_expected(store: MLBFactStore, expected: list[dict]) -> None:
    """Read an independently declared universe; directory contents are never authority."""
    identities = set()
    for ref in expected:
        key = (ref["role"], ref["gamePk"], ref["player_id"])
        if key in identities:
            raise FactLedgerConflict("duplicate expected fact identity")
        identities.add(key)
        fact = store.read(*key)
        if fact.factual_record_hash != ref["factual_record_hash"]:
            raise FactLedgerConflict("ledger differs from independent participation inventory")


def _missing_facts(store, facts):
    missing = []
    for fact in facts:
        try:
            saved = store.read(fact.role, fact.mlbam_game_id, getattr(fact, "mlbam_player_id", None))
        except FileNotFoundError:
            missing.append(fact)
        else:
            if saved.factual_record_hash != fact.factual_record_hash:
                raise FactLedgerConflict("catch-up fact conflicts with preserved source")
    return missing


def participation_inventory(inventory: dict, journal: EvidenceJournal,
                            store: MLBFactStore, *, start: date, end: date) -> dict:
    """Re-derive expectations from preserved feeds before checking the ledger."""
    records = journal.records()
    schedules = [r for r in records if r["claim"]["gamePk"] is None
                 and r["response"] is not None and r["response"]["http_status"] == 200]
    if len(schedules) != 1:
        raise BackfillError("exactly one independent schedule capture is required")
    feeds = {}
    for record in records:
        game_id = record["claim"]["gamePk"]
        if game_id is None:
            continue
        if game_id in feeds or record["response"] is None or record["response"]["http_status"] != 200:
            raise BackfillError("incomplete or duplicate historical feed capture")
        feeds[game_id] = record
    expected, players, games = [], {}, []
    types = set()
    for row in inventory["games"]:
        if not start.isoformat() <= row["officialDate"] <= end.isoformat():
            continue
        raw_type = row["source_game"]["gameType"]
        types.add(raw_type)
        if raw_type not in OFFICIAL_TYPES:
            raise BackfillError("UNSUPPORTED_GAME_TYPE")
        finality = classify_game_finality(row["status"])
        if finality.canonical_state in {"CONFLICT", "AMBIGUOUS"}:
            raise BackfillError("unresolved prior-date finality")
        if not finality.is_final:
            # A prior-date game still under way/suspended is not complete history.
            detail = row["status"].get("detailedState", "").casefold()
            if detail not in {"postponed", "cancelled", "canceled", "scheduled", "preview"}:
                raise BackfillError("prior-date game has unfinished participation")
            continue
        if row["gamePk"] not in feeds:
            raise BackfillError("missing expected final-game feed")
        facts = facts_from_feed(row, journal.payload(feeds[row["gamePk"]]), schedules[0],
                                feeds[row["gamePk"]], allowed_game_types=(raw_type,))
        games.append(row["gamePk"])
        for fact in facts:
            expected.append(_fact_reference(fact))
            if fact.role == "BATTER":
                if fact.at_bats is None or fact.hits is None:
                    raise BackfillError("missing AB/H in independent batter participation")
                players.setdefault(fact.mlbam_player_id, []).append({
                    "gamePk": fact.mlbam_game_id,
                    "factual_record_hash": fact.factual_record_hash})
    verify_expected(store, expected)
    return {"schema_version": COVERAGE_SCHEMA, "start": start.isoformat(), "through": end.isoformat(),
            "inventory_hash": digest(inventory), "source_refs": [inventory["source_ref"]],
            "observed_game_types": sorted(types), "games": sorted(games, key=int),
            "expected_records": expected, "players": dict(sorted(players.items())),
            "complete": True}


def historical_inventory(fact_root: Path, backfill_id: str) -> dict:
    """Read-only reuse of the qualified historical journal; never fetch/checkpoint."""
    job = MLBFactBackfill(fact_root, backfill_id)
    if (job.plan.season != 2026 or job.plan.inventory_start != "2026-01-01"
            or job.plan.inventory_end != HISTORY_THROUGH.isoformat()):
        raise BackfillError("historical coverage window differs from LIVE-01 baseline")
    return participation_inventory(job.inventory(), job.journal, job.store,
        start=date(2026, 1, 1), end=HISTORY_THROUGH)


def catch_up(root: Path, store: MLBFactStore, *, target: date, provider,
             max_requests: int = 225) -> tuple[dict, dict]:
    """One independent bounded catch-up journal; fail before the next call on conflict."""
    end = target - timedelta(days=1)
    if target.year != 2026 or end < CATCHUP_START:
        raise BackfillError("LIVE-01 catch-up window is outside the declared season")
    root.mkdir(parents=True, exist_ok=True)
    plan = {"start": CATCHUP_START.isoformat(), "through": end.isoformat(),
            "max_requests": max_requests, "fact_root": str(store.root)}
    publish_document(root / "plan.json", plan)
    if (root / "conflict.json").exists():
        raise FactLedgerConflict("catch-up has a persisted conflict; zero requests allowed")
    journal = EvidenceJournal(root / "raw", max_requests)
    stats = {"new_game_feeds": 0, "existing_identical_games": 0,
             "new_game_facts": 0, "new_batter_facts": 0, "new_pitcher_facts": 0,
             "fact_conflicts": 0}
    with operation_lock(root):
        try:
            records = journal.records()
            if any(r["response"] is None or r["response"]["http_status"] != 200 for r in records):
                raise BackfillError("interrupted/failed catch-up request; no automatic retry")
            schedule = next((r for r in records if r["claim"]["gamePk"] is None), None)
            if schedule is None:
                schedule = journal.capture(schedule_request(CATCHUP_START, end, "catchup-schedule"), provider)
            inventory = inventory_from_capture(journal, schedule, CATCHUP_START, end)
            publish_document(root / "inventory.json", inventory)
            rows = [r for r in inventory["games"] if r["canonical_final"]]
            if any(classify_game_finality(r["status"]).canonical_state in {"AMBIGUOUS", "CONFLICT"}
                   for r in inventory["games"]):
                raise BackfillError("unresolved catch-up finality")
            stats["expected_final_games"] = len(rows)
            # Inspect every preserved game's existing facts before any new feed.
            # A later inventory row can already contain a conflict on restart.
            preserved = {}
            for record in records:
                game_id = record["claim"]["gamePk"]
                if game_id is not None:
                    if game_id in preserved:
                        raise BackfillError("multiple captures for one final game")
                    preserved[game_id] = record
            for row in rows:
                record = preserved.get(row["gamePk"])
                if record is not None:
                    facts = facts_from_feed(row, journal.payload(record), schedule, record,
                        allowed_game_types=(row["source_game"]["gameType"],))
                    _missing_facts(store, facts)
            for row in rows:
                records = journal.records()
                matches = [r for r in records if r["claim"]["gamePk"] == row["gamePk"]]
                if len(matches) > 1:
                    raise BackfillError("multiple captures for one final game")
                if matches:
                    record = matches[0]
                else:
                    try:
                        store.read("GAME", row["gamePk"])
                    except FileNotFoundError:
                        pass
                    else:
                        raise BackfillError("existing game requires its preserved independent feed; never refetch it")
                    request = EvidenceRequest(request_id="catchup-feed-" + row["gamePk"],
                        evidence_class="stable_history", source_name="mlb_live01_final",
                        provider="mlb_statsapi", event_id=row["gamePk"],
                        url=f'{BASE}/api/v1.1/game/{row["gamePk"]}/feed/live')
                    record = journal.capture(request, provider)
                    stats["new_game_feeds"] += 1
                facts = facts_from_feed(row, journal.payload(record), schedule, record,
                    allowed_game_types=(row["source_game"]["gameType"],))
                # Check the entire game's existing logical identities before publication.
                missing = _missing_facts(store, facts)
                if not missing:
                    stats["existing_identical_games"] += 1
                for fact in missing:
                    store.publish(fact)
                    stats["new_" + fact.role.lower() + "_facts"] += 1
            coverage = participation_inventory(inventory, journal, store, start=CATCHUP_START, end=end)
            publish_document(root / "coverage.json", coverage)
            return coverage, stats
        except FactLedgerConflict as exc:
            publish_document(root / "conflict.json", {"state": "FACT_CONFLICT", "detail": str(exc)})
            raise


def compose_coverage(history: dict, catchup: dict, *, target: date, store: MLBFactStore) -> dict:
    if (history["schema_version"] != COVERAGE_SCHEMA or catchup["schema_version"] != COVERAGE_SCHEMA
            or not history["complete"] or not catchup["complete"]
            or history["start"] != f"{target.year}-01-01"
            or history["through"] != HISTORY_THROUGH.isoformat()
            or catchup["start"] != CATCHUP_START.isoformat()
            or catchup["through"] != (target - timedelta(days=1)).isoformat()
            or set(history["games"]) & set(catchup["games"])):
        raise BackfillError("independent coverage windows do not compose")
    expected = history["expected_records"] + catchup["expected_records"]
    verify_expected(store, expected)
    players = {}
    for ref in expected:
        if ref["role"] == "BATTER":
            players.setdefault(ref["player_id"], []).append({
                "gamePk": ref["gamePk"], "factual_record_hash": ref["factual_record_hash"]})
    types = sorted(set(history["observed_game_types"]) | set(catchup["observed_game_types"]))
    postseason = any(value != "R" for value in types)
    return {"schema_version": COVERAGE_SCHEMA, "start": history["start"],
            "through": catchup["through"], "complete": True,
            "history_hash": digest(history), "catchup_hash": digest(catchup),
            "source_refs": [f"cv-live01-history:sha256:{digest(history)}",
                            f"cv-live01-catchup:sha256:{digest(catchup)}"],
            "observed_game_types": types,
            "coverage_policy": ("official-regular-and-observed-postseason-prior-date-v1"
                                if postseason else "regular-season-prior-date-v1"),
            "model_version": "research-v1-live01-official-history" if postseason else "research-v1",
            "games": sorted(history["games"] + catchup["games"], key=int),
            "players": dict(sorted(players.items())), "expected_records": expected}


def batter_coverage(index: dict, *, player_id: str, game_id: str,
                    target: date, observed_at: datetime) -> BatterLedgerCoverage:
    if not index["complete"] or index["through"] != (target - timedelta(days=1)).isoformat():
        raise BackfillError("complete prior-date coverage is required")
    return BatterLedgerCoverage(season=target.year, mlbam_player_id=player_id,
        target_game_id=game_id, target_game_date=target, coverage_through=target-timedelta(days=1),
        aggregation_cutoff=observed_at, observed_at=observed_at, complete=True,
        expected_records=tuple(BatterFactReference(r["gamePk"], r["factual_record_hash"])
                               for r in index["players"].get(player_id, [])),
        source_refs=tuple(index["source_refs"]) +
                    (f"cv-live01-composed-coverage:sha256:{digest(index)}",
                     "coverage-policy:" + index["coverage_policy"]))
