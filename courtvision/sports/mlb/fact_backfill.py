"""Bounded, resumable first-party MLB fact acquisition; research only.

PLAN declares a window and a lifetime request budget. FETCH preserves an
independent schedule and per-game feeds. MATERIALIZE reuses CORE-01 extraction
and storage. VERIFY/RESUME replay those exact bytes. COVERAGE is derived from
the provider inventory and participation, never from a ledger directory scan.
Operational manifests are immutable, hash-chained snapshots.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re

from courtvision.core.candidates import EventIdentity, IdentityStatus
from courtvision.sports.mlb.batting_results import validate_boxscore_binding
from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest, ProviderResponse
from courtvision.sports.mlb.fact_backfill_evidence import (
    BASE, SCHEMA, BackfillError, EvidenceJournal, StatsAPIProvider, digest,
    operation_lock, publish_document, read_document, source_ref, utc_now,
)
from courtvision.sports.mlb.fact_ledger import MLBFactStore, FactLedgerConflict, _plain_path
from courtvision.sports.mlb.game_facts import (
    MLBBatterGameFact, canonical_json, extract_game_fact, extract_player_game_facts,
)
from courtvision.sports.mlb.hits_season_ledger import BatterFactReference, BatterLedgerCoverage
from courtvision.sports.mlb.game_finality import classify_game_finality
from courtvision.sports.mlb.schedule_revisions import (
    SCHEDULE_REVISION_POLICY_VERSION, resolve_schedule_responses, selected_schedule_game_payload,
)


def provider_id(value: object) -> str:
    if type(value) not in (int, str) or not re.fullmatch(r"[1-9]\d*", str(value)):
        raise BackfillError("missing or invalid provider numeric identity")
    return str(value)


def is_final(status: dict) -> bool:
    return classify_game_finality(status).is_final


def eligible_final(row: dict) -> bool:
    """Revalidate provider evidence; a persisted eligibility hint is not authority."""
    return row["canonical_final"] and is_final(row["status"])


@dataclass(frozen=True)
class BackfillPlan:
    backfill_id: str
    season: int
    window_start: str
    window_end: str
    inventory_start: str
    inventory_end: str
    max_final_games: int = 3
    max_provider_requests: int = 4

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", self.backfill_id):
            raise BackfillError("invalid backfill ID")
        dates = [date.fromisoformat(getattr(self, key)) for key in
                 ("inventory_start", "window_start", "window_end", "inventory_end")]
        if dates != sorted(dates) or any(day.year != self.season for day in dates):
            raise BackfillError("plan dates must be ordered within the explicit season")
        if any(type(n) is not int or n < 1 for n in (self.max_final_games, self.max_provider_requests)):
            raise BackfillError("positive explicit game/request limits are required")


def schedule_inventory(
    payload: dict, start: str, end: str, *, request: EvidenceRequest,
    source_digest: str, captured_at: str, source_response_path: str,
) -> dict:
    """Reconcile every preserved occurrence before counting logical final games."""
    candidates = {}
    raw_count = 0
    if not isinstance(payload.get("dates"), list):
        raise BackfillError("schedule dates are missing")
    for day in payload["dates"]:
        games = day.get("games")
        if not isinstance(games, list) or type(day.get("totalGames")) is not int or day["totalGames"] != len(games):
            raise BackfillError("schedule daily game count mismatch")
        for game in games:
            raw_count += 1
            game_id = provider_id(game.get("gamePk"))
            candidates.setdefault(game_id, []).append((str(day.get("date") or "") or None, game))
    if type(payload.get("totalGames")) is not int or payload["totalGames"] != raw_count:
        raise BackfillError("schedule total game count mismatch")
    observed = datetime.fromisoformat(captured_at)
    response = ProviderResponse(canonical_json(payload), 200, {}, observed, observed)
    resolved, summary = resolve_schedule_responses([(request, {
        "sha256": source_digest, "captured_at_utc": captured_at,
        "body_path": source_response_path,
    }, response)])
    if summary["identity_conflict_count"]:
        raise BackfillError("IDENTITY_CONFLICT: " + canonical_json(summary["identity_conflicts"]).decode())
    rows = []
    for game_id, resolution in resolved.items():
        identity = resolution["identity"]
        if (identity["sport_id"] != "1" or identity["game_type"] != "R"
                or identity["season"] != start[:4]):
            raise BackfillError("schedule game is outside regular-season query scope")
        game = selected_schedule_game_payload(candidates[game_id], resolution)
        official = date.fromisoformat(game["officialDate"]).isoformat()
        home, away = identity["home_team_id"], identity["away_team_id"]
        if home == away:
            raise BackfillError("schedule team identities are identical")
        selected = resolution["selected_canonical_state"]
        in_window = start <= official <= end
        rows.append({"gamePk": game_id, "officialDate": official,
            "home_team_id": home, "away_team_id": away,
            "status": game["status"], "in_requested_window": in_window,
            "canonical_final": in_window and selected["is_final"],
            "eligible_final": in_window and selected["is_final"],
            "source_game": game, "reconciliation": resolution,
            "selected_source_response_hash": selected["source_response_digest"]})
    return {"schema_version": SCHEMA, "window_start": start, "window_end": end,
            "schedule_revision_policy": SCHEDULE_REVISION_POLICY_VERSION,
            "reconciliation_summary": summary,
            "games": sorted(rows, key=lambda row: (row["officialDate"], int(row["gamePk"])))}


def facts_from_feed(row: dict, feed: dict, schedule_record: dict, feed_record: dict):
    """Bind both provider finalities and identities before calling core extraction."""
    if not eligible_final(row):
        raise BackfillError("non-final schedule game cannot be materialized")
    game_id = row["gamePk"]
    if provider_id(feed.get("gamePk")) != game_id:
        raise BackfillError("feed game identity mismatch")
    event = EventIdentity(game_id, "mlb_statsapi_final", IdentityStatus.RESOLVED, game_id)
    validate_boxscore_binding(feed, event, "final")
    data = feed["gameData"]
    if not is_final(data["status"]):
        raise BackfillError("feed is not explicitly final")
    if data["datetime"]["officialDate"] != row["officialDate"]:
        raise BackfillError("feed official date mismatch")
    if data["game"]["type"] != "R" or str(data["game"]["season"]) != row["officialDate"][:4]:
        raise BackfillError("feed game type/season mismatch")
    box = feed["liveData"]["boxscore"]
    for side in ("away", "home"):
        expected = row[f"{side}_team_id"]
        if (provider_id(data["teams"][side]["id"]) != expected
                or provider_id(box["teams"][side]["team"]["id"]) != expected):
            raise BackfillError("feed team identity mismatch")
        players = box["teams"][side]["players"]
        if not isinstance(players, dict) or not players:
            raise BackfillError("boxscore roster is missing")
        roster_ids = {provider_id(p["person"]["id"]) for p in players.values()}
        for role in ("batters", "pitchers"):
            participation = box["teams"][side].get(role)
            if not isinstance(participation, list) or not participation:
                raise BackfillError("boxscore participation inventory is missing")
            ids = [provider_id(value) for value in participation]
            if len(set(ids)) != len(ids) or not set(ids) <= roster_ids:
                raise BackfillError("boxscore participation/roster identity mismatch")
        score = row["source_game"]["teams"][side].get("score")
        feed_score = feed["liveData"]["linescore"]["teams"][side].get("runs")
        if score is not None and feed_score is not None and score != feed_score:
            raise BackfillError("final schedule/feed score mismatch")
    observed = datetime.fromisoformat(feed_record["response"]["responded_at"])
    schedule_observed = datetime.fromisoformat(schedule_record["response"]["responded_at"])
    if observed < schedule_observed:
        raise BackfillError("feed observation precedes schedule")
    refs = (source_ref(schedule_record), source_ref(feed_record),
            f"cv-schedule-reconciliation:{SCHEDULE_REVISION_POLICY_VERSION}:sha256:{digest(row['reconciliation'])}")
    game = extract_game_fact(row["source_game"],
        event_identity=event,
        game_date=date.fromisoformat(row["officialDate"]), game_status="final",
        observed_at=observed, source_refs=refs)
    players = extract_player_game_facts(box, game=game, observed_at=observed, source_refs=refs)
    # Provider participation is independent from both extraction and the ledger.
    for side in ("away", "home"):
        for role, listing, stat in (("BATTER", "batters", "batting"), ("PITCHER", "pitchers", "pitching")):
            team = box["teams"][side]
            expected = {provider_id(p["person"]["id"]) for p in team["players"].values()
                        if p.get("stats", {}).get(stat)}
            supplied = {provider_id(value) for value in team[listing]}
            # StatsAPI's batters array also lists pitchers without batting
            # participation. CORE-01 already excludes empty role stats. Accept
            # only explicit pitcher-only rows, not missing batting evidence for
            # a hitter, and retain exact expected/extracted fact equality.
            pitcher_only = set()
            if role == "BATTER":
                pitching_ids = {provider_id(value) for value in team["pitchers"]}
                for person in team["players"].values():
                    player_id = provider_id(person["person"]["id"])
                    position = person.get("position", {})
                    stats = person.get("stats", {})
                    if (player_id in pitching_ids and stats.get("batting") == {}
                            and stats.get("pitching") and not person.get("battingOrder")
                            and position.get("code") == "1"
                            and position.get("type") == "Pitcher"
                            and position.get("abbreviation") == "P"):
                        pitcher_only.add(player_id)
            if expected != supplied - pitcher_only:
                raise BackfillError("role stats differ from provider participation inventory")
            actual = {f.mlbam_player_id for f in players if f.role == role and f.side == side}
            if actual != expected:
                raise BackfillError("extracted role inventory differs from provider evidence")
    return (game, *players)


class MLBFactBackfill:
    def __init__(self, fact_root: Path | str, backfill_id: str):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", backfill_id):
            raise BackfillError("invalid backfill ID")
        self.store = MLBFactStore(fact_root)
        self.root = self.store.root / "_backfills" / backfill_id
        raw = read_document(self.root / "plan.json")
        if raw["schema_version"] != SCHEMA or raw["fact_root"] != str(self.store.root):
            raise BackfillError("plan schema/root mismatch")
        self.plan = BackfillPlan(**raw["plan"])
        if self.plan.backfill_id != backfill_id:
            raise BackfillError("backfill ID mismatch")
        self.plan_document = raw
        self.journal = EvidenceJournal(self.root / "raw", self.plan.max_provider_requests)
        self._history()

    @classmethod
    def create(cls, fact_root: Path | str, plan: BackfillPlan):
        store = MLBFactStore(fact_root)
        root = store.root / "_backfills" / plan.backfill_id
        _plain_path(root)
        root.mkdir(parents=True, exist_ok=False)
        publish_document(root / "plan.json", {"schema_version": SCHEMA,
            "fact_root": str(store.root), "plan": asdict(plan),
            "generated_at": utc_now().isoformat(), "research_only": True,
            "betting_enabled": False, "kelly_enabled": False, "official_pick_enabled": False})
        job = cls(fact_root, plan.backfill_id)
        job._checkpoint()
        return job

    def _history(self):
        previous = None
        history = []
        records = self.journal.records()
        for number, path in enumerate(sorted((self.root / "manifests").glob("*.json")), 1):
            if path.name != f"{number:06d}.json":
                raise BackfillError("manifest history is not contiguous")
            item = read_document(path)
            if (item["previous_manifest_hash"] != previous
                    or item["plan_hash"] != digest(self.plan_document)
                    or item["schema_version"] != SCHEMA):
                raise BackfillError("manifest chain/plan mismatch")
            count = item["provider_request_count"]
            if type(count) is not int or not 0 <= count <= len(records):
                raise BackfillError("manifest request count mismatch")
            if item["raw_evidence_refs"] != self._raw_refs(records[:count]):
                # An interrupted request may gain its response before checkpoint.
                prior = item["raw_evidence_refs"]
                current = self._raw_refs(records[:count])
                if len(prior) != len(current) or any(
                    a["request_hash"] != b["request_hash"] or
                    (a["response_hash"] is not None and a != b) for a, b in zip(prior, current)):
                    raise BackfillError("manifest raw evidence binding mismatch")
            previous = digest(item)
            history.append(item)
        if history and history[-1]["schedule_inventory_hash"] is not None:
            inv = read_document(self.root / "inventory.json")
            if digest(inv) != history[-1]["schedule_inventory_hash"]:
                raise BackfillError("manifest inventory binding mismatch")
        return history

    @staticmethod
    def _raw_refs(records):
        return [{"sequence": r["claim"]["sequence"], "request_hash": digest(r["claim"]),
            "response_hash": digest(r["response"]) if r["response"] is not None else None,
            "raw_sha256": r["response"]["sha256"] if r["response"] is not None else None}
            for r in records]

    def _request(self, game_id=None):
        if game_id is None:
            url = (f"{BASE}/api/v1/schedule?sportId=1&gameTypes=R"
                   f"&startDate={self.plan.inventory_start}&endDate={self.plan.inventory_end}")
            request_id = "regular-season-inventory"
        else:
            provider_id(game_id)
            url, request_id = f"{BASE}/api/v1.1/game/{game_id}/feed/live", f"final-feed-{game_id}"
        return EvidenceRequest(request_id=request_id, evidence_class="stable_history",
            source_name="mlb_final_facts", provider="mlb_statsapi", url=url, event_id=game_id)

    def _successful(self, records, game_id):
        candidates = [r for r in records if r["claim"]["gamePk"] == game_id
                      and r["response"] is not None and r["response"]["http_status"] == 200]
        if len(candidates) > 1:
            raise BackfillError("multiple successful captures for one request identity")
        if candidates and candidates[0]["claim"]["url"] != self._request(game_id).url:
            raise BackfillError("captured request differs from declared scope")
        return candidates[0] if candidates else None

    def inventory(self):
        path = self.root / "inventory.json"
        if not path.exists():
            return None
        saved = read_document(path)
        record = self._successful(self.journal.records(), None)
        if record is None:
            raise BackfillError("inventory has no successful provider schedule")
        derived = self._inventory_document(record)
        if saved != derived:
            # 01A inventories retained a stricter, now superseded eligibility
            # hint. Permit only false -> proven true; verify every other byte of
            # their logical content against the same preserved provider source.
            # Return the original document so manifests/fact provenance keep
            # their immutable hashes. All consumers reclassify status evidence.
            comparable = dict(derived)
            comparable["games"] = [dict(row) for row in derived["games"]]
            if len(saved.get("games", [])) != len(comparable["games"]):
                raise BackfillError("inventory differs from preserved schedule")
            for old, current in zip(saved["games"], comparable["games"]):
                if old.get("eligible_final") is False and current["eligible_final"] is True:
                    current["eligible_final"] = False
            if saved != comparable:
                raise BackfillError("inventory differs from preserved schedule")
        return saved

    def _inventory_document(self, record):
        value = schedule_inventory(self.journal.payload(record), self.plan.inventory_start, self.plan.inventory_end,
            request=self._request(), source_digest=record["response"]["sha256"],
            captured_at=record["response"]["responded_at"],
            source_response_path=f'raw/{record["claim"]["sequence"]:06d}/body.bin')
        value["source_ref"] = source_ref(record)
        return value

    def _pilot_rows(self, inventory):
        rows = [r for r in inventory["games"]
                if self.plan.window_start <= r["officialDate"] <= self.plan.window_end]
        if sum(r["canonical_final"] for r in rows) > self.plan.max_final_games:
            raise BackfillError("pilot game cap exceeded; narrow the explicit window")
        return rows

    def _expected(self, row, records):
        feed = self._successful(records, row["gamePk"])
        if feed is None:
            return None
        return facts_from_feed(row, self.journal.payload(feed), self._successful(records, None), feed)

    def _matches(self, fact):
        try:
            saved = self.store.read(fact.role, fact.mlbam_game_id, getattr(fact, "mlbam_player_id", None))
        except FileNotFoundError:
            return False
        if saved.factual_record_hash != fact.factual_record_hash:
            raise FactLedgerConflict("canonical fact conflicts with preserved backfill evidence")
        return True

    def verify(self):
        self._history()
        records = self.journal.records()
        inv = self.inventory()
        rows = self._pilot_rows(inv) if inv is not None else []
        final = [r for r in rows if r["canonical_final"]]
        completed, failed, conflicts, errors = [], [], 0, {}
        for row in rows:
            finality = classify_game_finality(row["status"])
            if finality.canonical_state in {"AMBIGUOUS", "CONFLICT"}:
                failed.append(row["gamePk"])
                errors[row["gamePk"]] = "FINALITY_" + finality.canonical_state
        counts = {"GAME": 0, "BATTER": 0, "PITCHER": 0}
        if inv is None:
            schedule = self._successful(records, None)
            if schedule is not None:
                try:
                    self._inventory_document(schedule)
                except (ValueError, TypeError, KeyError) as exc:
                    errors["schedule_inventory"] = (
                        str(exc) if isinstance(exc, BackfillError) else type(exc).__name__)
        for row in final:
            if not eligible_final(row):
                failed.append(row["gamePk"])
                errors[row["gamePk"]] = "CANONICAL_FACT_FINALITY_UNSUPPORTED"
                continue
            try:
                facts = self._expected(row, records)
                if facts is None:
                    if any(r["claim"]["gamePk"] == row["gamePk"] for r in records):
                        failed.append(row["gamePk"])
                    continue
                matches = [self._matches(f) for f in facts]
                for fact, found in zip(facts, matches):
                    counts[fact.role] += int(found)
                if all(matches):
                    completed.append(row["gamePk"])
            except FactLedgerConflict:
                conflicts += 1
                failed.append(row["gamePk"])
                errors[row["gamePk"]] = "FactLedgerConflict"
            except (ValueError, TypeError, KeyError) as exc:
                failed.append(row["gamePk"])
                errors[row["gamePk"]] = type(exc).__name__
        status = "PLANNED" if inv is None else "PARTIAL"
        if inv is not None and len(completed) == len(final) and final:
            status = "COMPLETE"
        if failed or any(r["response"] is None for r in records):
            status = "PARTIAL" if completed else "FAILED"
        if inv is None and records:
            status = "FAILED"
        if conflicts:
            status = "CONFLICT"
        return {"schema_version": SCHEMA, "backfill_id": self.plan.backfill_id,
            "season": self.plan.season, "window_start": self.plan.window_start,
            "window_end": self.plan.window_end, "generated_at": utc_now().isoformat(),
            "schedule_inventory_hash": digest(inv) if inv is not None else None,
            "expected_final_game_pks": [r["gamePk"] for r in final] if inv is not None else None,
            "completed_game_pks": completed, "failed_game_pks": failed,
            "nonfinal_game_pks": [r["gamePk"] for r in rows if not r["canonical_final"]] if inv is not None else None,
            "raw_evidence_refs": self._raw_refs(records),
            "game_fact_count": counts["GAME"], "batter_fact_count": counts["BATTER"],
            "pitcher_fact_count": counts["PITCHER"], "conflict_count": conflicts,
            "missing_count": len(final) - len(completed) if inv is not None else None,
            "provider_request_count": len(records), "status": status, "errors": errors,
            "research_only": True, "betting_enabled": False,
            "kelly_enabled": False, "official_pick_enabled": False}

    def _checkpoint(self, *, stop_on_conflict=False):
        history = self._history()
        state = self.verify()
        state.update(plan_hash=digest(self.plan_document),
                     previous_manifest_hash=digest(history[-1]) if history else None)
        publish_document(self.root / "manifests" / f"{len(history)+1:06d}.json", state)
        # Acquisition must stop, but only after the conflict is durable.
        if stop_on_conflict and state["conflict_count"]:
            raise FactLedgerConflict("fetch stopped by canonical fact conflict")
        return state

    def reconcile_inventory(self):
        """Build/verify the inventory from an existing capture, with zero network I/O."""
        with operation_lock(self.root):
            self._history()
            schedule = self._successful(self.journal.records(), None)
            if schedule is None:
                raise BackfillError("reconciliation requires preserved schedule evidence")
            if self.inventory() is None:
                publish_document(self.root / "inventory.json", self._inventory_document(schedule))
            return self._checkpoint()

    def fetch(self, provider):
        """Only missing captures; stop on provider errors or conflicts, never retry here."""
        with operation_lock(self.root):
            state = self.verify()
            if state["conflict_count"]:
                raise FactLedgerConflict("fetch blocked by canonical fact conflict")
            records = self.journal.records()
            schedule = self._successful(records, None)
            if schedule is None:
                try:
                    schedule = self.journal.capture(self._request(), provider)
                except BackfillError:
                    self._checkpoint()
                    raise
            try:
                if self.inventory() is None:
                    publish_document(self.root / "inventory.json", self._inventory_document(schedule))
            except (ValueError, TypeError, KeyError):
                self._checkpoint()
                raise
            self._checkpoint(stop_on_conflict=True)
            if any(classify_game_finality(r["status"]).canonical_state in {"AMBIGUOUS", "CONFLICT"}
                   for r in self._pilot_rows(self.inventory())):
                raise BackfillError("pilot contains unresolved finality")
            if any(r["canonical_final"] and not eligible_final(r) for r in self._pilot_rows(self.inventory())):
                raise BackfillError("pilot contains unsupported canonical fact finality")
            for row in self._pilot_rows(self.inventory()):
                if not eligible_final(row):
                    continue
                records = self.journal.records()
                if self._successful(records, row["gamePk"]) is not None:
                    self._expected(row, records)  # Validate, never refetch rejected evidence.
                    continue
                try:
                    self.journal.capture(self._request(row["gamePk"]), provider)
                    self._expected(row, self.journal.records())
                except (ValueError, TypeError, KeyError):
                    self._checkpoint()
                    raise
                self._checkpoint(stop_on_conflict=True)
            return self._checkpoint(stop_on_conflict=True)

    def materialize(self):
        with operation_lock(self.root):
            state = self.verify()
            if state["conflict_count"]:
                self._checkpoint()
                raise FactLedgerConflict("backfill conflict; no publication")
            if state["failed_game_pks"]:
                self._checkpoint()
                raise BackfillError("rejected/unavailable game evidence; no publication")
            inv = self.inventory()
            if inv is None:
                raise BackfillError("schedule must be fetched before materialization")
            records = self.journal.records()
            # Preflight the whole available batch before any record is published.
            batch = []
            for row in self._pilot_rows(inv):
                if eligible_final(row):
                    facts = self._expected(row, records)
                    if facts is not None:
                        for fact in facts:
                            self._matches(fact)
                        batch.extend(facts)
            try:
                for fact in batch:
                    self.store.publish(fact)
            except FactLedgerConflict:
                self._checkpoint()
                raise
            return self._checkpoint()

    def resume(self, provider=None):
        """Verify first; reuse captured bytes, then fetch only absent game evidence."""
        state = self.verify()
        if state["conflict_count"]:
            raise FactLedgerConflict("resume blocked by canonical fact conflict")
        if provider is not None:
            self.fetch(provider)
        return self.materialize()

    def coverage_index(self, *, through: str):
        """Independent participation expectations, including missing fact hashes."""
        self.verify()
        inv = self.inventory()
        if inv is None or not self.plan.inventory_start <= through <= self.plan.inventory_end:
            raise BackfillError("coverage date is outside the preserved inventory")
        rows = [r for r in inv["games"] if r["canonical_final"] and r["officialDate"] <= through]
        unresolved = [r["gamePk"] for r in inv["games"] if r["officialDate"] <= through
                      and classify_game_finality(r["status"]).canonical_state in {"AMBIGUOUS", "CONFLICT"}]
        records, players, unknown, invalid, missing = self.journal.records(), {}, [], [], []
        for row in rows:
            facts = self._expected(row, records)
            if facts is None:
                unknown.append(row["gamePk"])
                continue
            if not all(self._matches(f) for f in facts):
                missing.append(row["gamePk"])
            for fact in facts:
                if not isinstance(fact, MLBBatterGameFact):
                    continue
                valid = fact.at_bats is not None and fact.hits is not None
                if not valid:
                    invalid.append([fact.mlbam_game_id, fact.mlbam_player_id])
                players.setdefault(fact.mlbam_player_id, []).append({
                    "gamePk": fact.mlbam_game_id, "factual_record_hash": fact.factual_record_hash,
                    "valid_ab_h": valid, "ledger_match": self._matches(fact)})
        complete = (not unresolved and not unknown and not invalid and not missing and bool(rows)
                    and all(r["ledger_match"] for refs in players.values() for r in refs))
        return {"schema_version": SCHEMA, "scope": "PRIOR_DATE_INVENTORY",
            "inventory_hash": digest(inv), "inventory_start": self.plan.inventory_start,
            "coverage_through": through, "expected_final_game_pks": [r["gamePk"] for r in rows],
            "unknown_participation_game_pks": unknown, "invalid_ab_h_records": invalid,
            "unresolved_finality_game_pks": unresolved,
            "missing_ledger_game_pks": missing,
            "players": dict(sorted(players.items())), "complete": complete,
            "research_only": True, "full_season_hits_qualification": False}

    def batter_coverage(self, player_id: str, target_game_id: str):
        """Use a real inventory target; completeness is season-to-prior-date only."""
        provider_id(player_id)
        inv = self.inventory()
        targets = [r for r in inv["games"] if r["gamePk"] == target_game_id]
        if len(targets) != 1:
            raise BackfillError("target must be an explicit game in the preserved schedule")
        target_date = date.fromisoformat(targets[0]["officialDate"])
        through = target_date - timedelta(days=1)
        index = self.coverage_index(through=through.isoformat())
        records = index["players"].get(player_id, [])
        complete = (index["complete"] and bool(records)
                    and self.plan.inventory_start == f"{self.plan.season}-01-01")
        now = utc_now()
        coverage = BatterLedgerCoverage(season=self.plan.season, mlbam_player_id=player_id,
            target_game_id=target_game_id, target_game_date=target_date, coverage_through=through,
            aggregation_cutoff=now, observed_at=now, complete=complete,
            expected_records=tuple(BatterFactReference(r["gamePk"], r["factual_record_hash"]) for r in records),
            source_refs=(f"cv-backfill-inventory:sha256:{digest(inv)}",
                         f"cv-backfill-participation:sha256:{digest(index)}",
                         "retrospective-pipeline-rehearsal-only"))
        return index, coverage

    def gap_report(self):
        """Games are enumerable now; unfetched player participation remains UNKNOWN."""
        self.verify()
        inv = self.inventory()
        if inv is None:
            raise BackfillError("gap report requires the independent schedule")
        rows = [r for r in inv["games"] if r["canonical_final"]]
        represented, requiring, missing_feeds = [], [], []
        counts = {"GAME": 0, "BATTER": 0, "PITCHER": 0}
        expected_counts = {"GAME": 0, "BATTER": 0, "PITCHER": 0}
        records = self.journal.records()
        for row in rows:
            try:
                game = self.store.read("GAME", row["gamePk"])
            except FileNotFoundError:
                requiring.append(row["gamePk"])
            else:
                if (game.game_date.isoformat() != row["officialDate"]
                        or game.home_team_id != row["home_team_id"] or game.away_team_id != row["away_team_id"]):
                    raise FactLedgerConflict("stored game differs from independent schedule")
                represented.append(row["gamePk"])
            facts = self._expected(row, records)
            if facts is None:
                missing_feeds.append(row["gamePk"])
                continue
            matched = [self._matches(fact) for fact in facts]
            if not all(matched) and row["gamePk"] not in requiring:
                requiring.append(row["gamePk"])
            for fact, found in zip(facts, matched):
                counts[fact.role] += int(found)
                expected_counts[fact.role] += 1
        return {"schema_version": SCHEMA, "inventory_hash": digest(inv),
            "season_start": min((r["officialDate"] for r in inv["games"]), default=None),
            "inventory_window_start": self.plan.inventory_start,
            "coverage_through_target": self.plan.inventory_end,
            "total_expected_final_games": len(rows), "games_represented_in_ledger": len(represented),
            "represented_game_pks": represented, "games_requiring_acquisition": len(set(requiring) | set(missing_feeds)),
            "game_pks_requiring_acquisition": sorted(set(requiring) | set(missing_feeds), key=int),
            "games_requiring_feed_evidence": len(missing_feeds),
            "verified_batter_fact_count": counts["BATTER"], "verified_pitcher_fact_count": counts["PITCHER"],
            "remaining_batter_fact_count": "UNKNOWN" if missing_feeds else expected_counts["BATTER"] - counts["BATTER"],
            "remaining_pitcher_fact_count": "UNKNOWN" if missing_feeds else expected_counts["PITCHER"] - counts["PITCHER"],
            "full_backfill_estimated_requests": len(missing_feeds),
            "estimate_basis": "one feed per missing game; preserved schedule reused; no retries",
            "nonfinal_game_pks": [r["gamePk"] for r in inv["games"] if not r["canonical_final"]],
            "unsupported_fact_finality_game_pks": [r["gamePk"] for r in rows if not eligible_final(r)],
            "ambiguous_finality_game_pks": [r["gamePk"] for r in inv["games"]
                if classify_game_finality(r["status"]).canonical_state == "AMBIGUOUS"],
            "finality_conflict_game_pks": [r["gamePk"] for r in inv["games"]
                if classify_game_finality(r["status"]).canonical_state == "CONFLICT"],
            "full_season_hits_qualification": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "fetch", "materialize", "verify", "resume", "coverage"))
    parser.add_argument("--fact-root", type=Path, required=True)
    parser.add_argument("--backfill-id", required=True)
    parser.add_argument("--season", type=int)
    for name in ("window-start", "window-end", "inventory-start", "inventory-end"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--max-final-games", type=int, default=3)
    parser.add_argument("--max-provider-requests", type=int, default=4)
    parser.add_argument("--allow-provider", action="store_true")
    args = parser.parse_args(argv)
    if args.operation == "plan":
        plan = BackfillPlan(**{key: getattr(args, key) for key in BackfillPlan.__dataclass_fields__})
        job = MLBFactBackfill.create(args.fact_root, plan)
        result = job.verify()
    else:
        job = MLBFactBackfill(args.fact_root, args.backfill_id)
        if args.operation == "fetch":
            if not args.allow_provider:
                parser.error("fetch requires --allow-provider and an approved request budget")
            result = job.fetch(StatsAPIProvider())
        elif args.operation == "resume":
            result = job.resume(StatsAPIProvider() if args.allow_provider else None)
        elif args.operation == "materialize":
            result = job.materialize()
        elif args.operation == "coverage":
            result = job.gap_report()
        else:
            result = job.verify()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
