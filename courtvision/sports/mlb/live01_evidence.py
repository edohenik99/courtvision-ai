"""Independent LIVE-01 prior-date inventories; no market or probability inputs."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest, ProviderResponse
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
from courtvision.sports.mlb.game_facts import canonical_json
from courtvision.sports.mlb.schedule_revisions import resolve_schedule_responses

# Exact provider codes, never a generic inferred postseason type.
# https://statsapi.mlb.com/api/v1/gameTypes
OFFICIAL_TYPES = frozenset({"R", "F", "D", "L", "W"})
HISTORY_THROUGH = date(2026, 9, 24)
CATCHUP_START = HISTORY_THROUGH + timedelta(days=1)
COVERAGE_SCHEMA = "cv_hits_live01_independent_inventory_v1"
ScheduleDisposition = Literal["FACTUAL_FINAL", "ADMINISTRATIVE_NO_PARTICIPATION",
                              "PRIOR_DATE_NOT_READY", "UNRESOLVED"]


def schedule_disposition(row: dict) -> ScheduleDisposition:
    """LIVE-01 participation/readiness only; never grants factual finality.

    The administrative exception is the complete 823490 status representation
    preserved in catchup/raw/000001 (SHA-256 7f755b03...9c99d2f). No other
    cancellation code, reason, spelling, or additional status field is inferred.
    Identity/scope and revision conflicts are checked by the inventory builder.
    """
    status = row["status"]
    if not isinstance(status, dict):
        return "UNRESOLVED"
    finality = classify_game_finality(status)
    if finality.is_final:
        return "FACTUAL_FINAL"
    if (status == {"abstractGameState": "Final", "codedGameState": "C",
                   "detailedState": "Cancelled", "statusCode": "CR",
                   "startTimeTBD": False, "reason": "Rain", "abstractGameCode": "F"}
            and status["startTimeTBD"] is False
            and all("score" not in row["source_game"]["teams"][side] for side in ("away", "home"))):
        return "ADMINISTRATIVE_NO_PARTICIPATION"
    if finality.canonical_state != "NON_FINAL":
        return "UNRESOLVED"
    # NON_FINAL alone permits unknown companion fields. Require a coherent,
    # explicit readiness tuple instead of suppressing arbitrary contradictions.
    detail = status.get("detailedState")
    if detail in {"Scheduled", "Pre-Game", "Preview", "Warmup"}:
        abstract, codes, abstract_code = "Preview", {"S", "P"}, "P"
        status_codes = {"S", "P", "PW"}
    elif detail in {"In Progress", "Manager Challenge", "Live"}:
        abstract, codes, abstract_code = "Live", {"I"}, "L"
        status_codes = {"I"}
    elif detail in {"Suspended", "Delayed"}:
        abstract, codes, abstract_code = "Live", {"I", "D"}, "L"
        status_codes = {"I", "D", "DI", "DR", "DD"}
    elif detail == "Postponed":
        abstract, codes, abstract_code = "Preview", {"D"}, "P"
        status_codes = {"D", "DR"}
    else:
        return "UNRESOLVED"
    if (status.get("abstractGameState") == abstract
            and status.get("codedGameState") in codes
            and status.get("statusCode", status["codedGameState"]) in status_codes
            and status.get("abstractGameCode", abstract_code) == abstract_code):
        return "PRIOR_DATE_NOT_READY"
    return "UNRESOLVED"


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


def inventory_from_captures(journal, records, start: date, end: date) -> dict:
    """Validate every capture, then use the shared revision selection contract."""
    inventories, sources = [], []
    for record in records:
        request = schedule_request(start, end, record["claim"]["request_id"])
        if record["claim"]["url"] != request.url:
            raise BackfillError("catch-up schedule window differs from plan")
        inventories.append(inventory_from_capture(journal, record, start, end))
        captured = record["response"]["responded_at"]
        observed = datetime.fromisoformat(captured)
        sources.append((request, {"sha256": record["response"]["sha256"],
            "captured_at_utc": captured,
            "body_path": f'raw/{record["claim"]["sequence"]:06d}/body.bin'},
            ProviderResponse(canonical_json(journal.payload(record)), 200, {}, observed, observed)))
    if len(inventories) == 1:
        return inventories[0]
    resolved, summary = resolve_schedule_responses(sources)
    if summary["identity_conflict_count"]:
        raise BackfillError("IDENTITY_CONFLICT: " + canonical_json(summary["identity_conflicts"]).decode())
    rows = []
    for game_id, resolution in resolved.items():
        selected = resolution["selected_canonical_state"]
        # Bind to the selected response, not a same-status payload from a
        # different capture (whose descriptive fields may legitimately differ).
        candidates = [row for record, inventory in zip(records, inventories)
            if (record["claim"]["request_id"] == selected["source_request_id"]
                and record["response"]["sha256"] == selected["source_response_digest"])
            for row in inventory["games"] if row["gamePk"] == game_id]
        if len(candidates) != 1:
            raise BackfillError("selected schedule source is ambiguous")
        rows.append({**candidates[0], "reconciliation": resolution})
    return {**inventories[-1], "reconciliation_summary": summary,
            "games": sorted(rows, key=lambda row: (row["officialDate"], int(row["gamePk"]))),
            "source_refs": [source_ref(record) for record in records],
            "observed_game_types": sorted({t for inv in inventories for t in inv["observed_game_types"]})}


def schedule_coverage(inventory: dict, *, start: date, end: date) -> dict:
    """Declare participation and readiness independently of stored facts."""
    buckets = {name: [] for name in ("FACTUAL_FINAL", "ADMINISTRATIVE_NO_PARTICIPATION",
                                    "PRIOR_DATE_NOT_READY", "UNRESOLVED")}
    exclusions = []
    for row in inventory["games"]:
        if not start.isoformat() <= row["officialDate"] <= end.isoformat():
            continue
        disposition = schedule_disposition(row)
        buckets[disposition].append(row["gamePk"])
        if disposition == "ADMINISTRATIVE_NO_PARTICIPATION":
            exclusions.append({"gamePk": row["gamePk"], "disposition": disposition,
                "facts_required": False, "participation_records_expected": 0,
                "status": row["status"], "schedule_game_hash": digest(row["source_game"]),
                "source_response_hash": row["selected_source_response_hash"],
                "reconciliation_hash": digest(row["reconciliation"])})
    return {"factual_final_game_pks": sorted(buckets["FACTUAL_FINAL"], key=int),
            "administrative_no_participation_game_pks": sorted(buckets["ADMINISTRATIVE_NO_PARTICIPATION"], key=int),
            "pending_prior_date_game_pks": sorted(buckets["PRIOR_DATE_NOT_READY"], key=int),
            "unresolved_game_pks": sorted(buckets["UNRESOLVED"], key=int),
            "administrative_exclusions": exclusions}


def _preserved_facts(row, record, schedules, journal, *, start, end):
    """Keep the schedule binding used when a feed was acquired immutable.

    A refreshed schedule can postdate an already valid feed. Validate its
    factual identity/content against that feed, but derive ledger hashes from
    the original schedule prefix, never from a later reconciliation digest.
    """
    preceding = [s for s in schedules if s["claim"]["sequence"] < record["claim"]["sequence"]]
    if not preceding:
        raise BackfillError("final feed has no preceding schedule evidence")
    if len(schedules) == 1:
        original = row
    else:
        original = next((r for r in inventory_from_captures(journal, preceding, start, end)["games"]
                         if r["gamePk"] == row["gamePk"]), None)
    if original is None or schedule_disposition(row) != "FACTUAL_FINAL":
        raise BackfillError("preserved final feed conflicts with schedule disposition")
    selected = original["reconciliation"]["selected_canonical_state"]
    schedule = next(s for s in preceding
        if s["response"]["sha256"] == selected["source_response_digest"]
        and s["claim"]["request_id"] == selected["source_request_id"])
    feed = journal.payload(record)
    if row != original:
        facts_from_feed(row, feed, schedule, record,
                        allowed_game_types=(row["source_game"]["gameType"],))
    return facts_from_feed(original, feed, schedule, record,
                           allowed_game_types=(original["source_game"]["gameType"],))


def _fact_reference(fact):
    return {"role": fact.role, "gamePk": fact.mlbam_game_id,
            "player_id": getattr(fact, "mlbam_player_id", None),
            "factual_record_hash": fact.factual_record_hash}


def verify_expected(store: MLBFactStore, expected: list[dict], *, allow_missing: bool = False) -> list[dict]:
    """Read an independently declared universe; directory contents are never authority."""
    identities, missing = set(), []
    for ref in expected:
        key = (ref["role"], ref["gamePk"], ref["player_id"])
        if key in identities:
            raise FactLedgerConflict("duplicate expected fact identity")
        identities.add(key)
        try:
            fact = store.read(*key)
        except FileNotFoundError:
            if not allow_missing:
                raise
            missing.append(ref)
            continue
        if fact.factual_record_hash != ref["factual_record_hash"]:
            raise FactLedgerConflict("ledger differs from independent participation inventory")
    return missing


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
    if not schedules:
        raise BackfillError("independent schedule capture is required")
    feeds = {}
    for record in records:
        game_id = record["claim"]["gamePk"]
        if game_id is None:
            continue
        if game_id in feeds or record["response"] is None or record["response"]["http_status"] != 200:
            raise BackfillError("incomplete or duplicate historical feed capture")
        feeds[game_id] = record
    readiness = schedule_coverage(inventory, start=start, end=end)
    if readiness["unresolved_game_pks"]:
        raise BackfillError("unresolved prior-date finality")
    pending = bool(readiness["pending_prior_date_game_pks"])
    expected, players, games, missing = [], {}, [], []
    types = set()
    for row in inventory["games"]:
        if not start.isoformat() <= row["officialDate"] <= end.isoformat():
            continue
        raw_type = row["source_game"]["gameType"]
        types.add(raw_type)
        if raw_type not in OFFICIAL_TYPES:
            raise BackfillError("UNSUPPORTED_GAME_TYPE")
        if schedule_disposition(row) != "FACTUAL_FINAL":
            if row["gamePk"] in feeds:
                raise BackfillError("preserved final feed conflicts with schedule disposition")
            continue
        if row["gamePk"] not in feeds:
            if pending:
                missing.append(row["gamePk"])
                continue
            raise BackfillError("missing expected final-game feed")
        facts = _preserved_facts(row, feeds[row["gamePk"]], schedules, journal, start=start, end=end)
        games.append(row["gamePk"])
        for fact in facts:
            expected.append(_fact_reference(fact))
            if fact.role == "BATTER":
                if fact.at_bats is None or fact.hits is None:
                    raise BackfillError("missing AB/H in independent batter participation")
                players.setdefault(fact.mlbam_player_id, []).append({
                    "gamePk": fact.mlbam_game_id,
                    "factual_record_hash": fact.factual_record_hash})
    missing_records = verify_expected(store, expected, allow_missing=pending)
    return {"schema_version": COVERAGE_SCHEMA, "start": start.isoformat(), "through": end.isoformat(),
            "inventory_hash": digest(inventory),
            "source_refs": inventory.get("source_refs", [inventory["source_ref"]]),
            "observed_game_types": sorted(types), "games": sorted(games, key=int),
            "expected_records": expected, "players": dict(sorted(players.items())),
            **({"missing_expected_records": missing_records} if missing_records else {}),
            **readiness, "missing_final_game_pks": sorted(missing, key=int),
            "complete": not pending}


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
    """Append-only catch-up; provider=None replays preserved readiness offline."""
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
             "fact_conflicts": 0, "schedule_refreshes": 0,
             "valid_preserved_feeds_refetched": 0}
    with operation_lock(root):
        try:
            records = journal.records()
            if any(r["response"] is None or r["response"]["http_status"] != 200 for r in records):
                raise BackfillError("interrupted/failed catch-up request; no automatic retry")
            schedules = [r for r in records if r["claim"]["gamePk"] is None]
            resuming = bool(schedules)
            if not schedules:
                if provider is None:
                    raise BackfillError("offline catch-up requires preserved schedule evidence")
                schedules.append(journal.capture(
                    schedule_request(CATCHUP_START, end, "catchup-schedule"), provider))
            inventory = inventory_from_captures(journal, schedules, CATCHUP_START, end)
            # Inspect every preserved game's existing facts before any new feed.
            # A later inventory row can already contain a conflict on restart.
            preserved = {}
            for record in records:
                game_id = record["claim"]["gamePk"]
                if game_id is not None:
                    if game_id in preserved:
                        raise BackfillError("multiple captures for one final game")
                    preserved[game_id] = record
            # Validate every preserved feed/ledger before even a schedule refresh.
            def inspect_preserved():
                by_game = {r["gamePk"]: r for r in inventory["games"]}
                for game_id, record in preserved.items():
                    if game_id not in by_game:
                        raise BackfillError("preserved feed is absent from schedule inventory")
                    facts = _preserved_facts(by_game[game_id], record, schedules, journal,
                                             start=CATCHUP_START, end=end)
                    _missing_facts(store, facts)

            inspect_preserved()
            readiness = schedule_coverage(inventory, start=CATCHUP_START, end=end)
            if (resuming and readiness["pending_prior_date_game_pks"]
                    and not readiness["unresolved_game_pks"] and provider is not None):
                sequence = len(records) + 1
                schedules.append(journal.capture(schedule_request(CATCHUP_START, end,
                    f"catchup-schedule-{sequence:06d}"), provider))
                stats["schedule_refreshes"] += 1
                inventory = inventory_from_captures(journal, schedules, CATCHUP_START, end)
                inspect_preserved()
                readiness = schedule_coverage(inventory, start=CATCHUP_START, end=end)
            revision = f'{schedules[-1]["claim"]["sequence"]:06d}.json'
            publish_document(root / "inventories" / revision, inventory)
            stats["expected_final_games"] = len(readiness["factual_final_game_pks"])
            if readiness["unresolved_game_pks"]:
                publish_document(root / "coverage" / revision, {
                    **readiness, "complete": False, "inventory_hash": digest(inventory)})
                raise BackfillError("unresolved catch-up finality")
            if readiness["pending_prior_date_game_pks"]:
                coverage = participation_inventory(inventory, journal, store, start=CATCHUP_START, end=end)
                publish_document(root / "coverage" / revision, coverage)
                return coverage, stats
            rows = [r for r in inventory["games"] if r["canonical_final"]]
            for row in rows:
                records = journal.records()
                matches = [r for r in records if r["claim"]["gamePk"] == row["gamePk"]]
                if len(matches) > 1:
                    raise BackfillError("multiple captures for one final game")
                if matches:
                    record = matches[0]
                else:
                    if provider is None:
                        raise BackfillError("offline catch-up is missing an expected final feed")
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
                facts = _preserved_facts(row, record, schedules, journal, start=CATCHUP_START, end=end)
                # Check the entire game's existing logical identities before publication.
                missing = _missing_facts(store, facts)
                if not missing:
                    stats["existing_identical_games"] += 1
                for fact in missing:
                    store.publish(fact)
                    stats["new_" + fact.role.lower() + "_facts"] += 1
            coverage = participation_inventory(inventory, journal, store, start=CATCHUP_START, end=end)
            publish_document(root / "coverage" / revision, coverage)
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
            **{key: sorted(history[key] + catchup[key], key=int) for key in (
                "factual_final_game_pks", "administrative_no_participation_game_pks",
                "pending_prior_date_game_pks", "unresolved_game_pks")},
            "administrative_exclusions": history["administrative_exclusions"] + catchup["administrative_exclusions"],
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
