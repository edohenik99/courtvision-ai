"""Offline backfill acceptance; synthetic schedules/feeds, no provider access."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest, ProviderResponse
from courtvision.sports.mlb.fact_backfill import (
    BackfillError, BackfillPlan, MLBFactBackfill, schedule_inventory as _schedule_inventory,
)
from courtvision.sports.mlb.fact_backfill_evidence import digest, read_document, validate_url
from courtvision.sports.mlb.fact_ledger import FactLedgerConflict, MLBFactStore
from courtvision.sports.mlb.game_facts import canonical_json
from courtvision.sports.mlb.hits_season_ledger import (
    CANONICAL_HITS_SEASON_SOURCE, HitsLedgerError, load_ledger_season,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("backfill tests must remain offline")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def game(game_id=823100, day="2026-03-25", state="Final"):
    return {"gamePk": game_id, "officialDate": day, "gameType": "R", "season": 2026,
        "gameDate": f"{day}T21:00:00Z", "gameGuid": f"fixture-guid-{game_id}",
        "venue": {"id": 42, "name": "Fixture Park"},
        "status": {"abstractGameState": state, "detailedState": state,
                   "codedGameState": "F" if state == "Final" else "S"},
        "teams": {"home": {"team": {"id": 114, "name": "Fixture Home"}, "score": 2},
                  "away": {"team": {"id": 142, "name": "Fixture Away"}, "score": 1}}}


def schedule(*games):
    dates = {}
    for row in games:
        dates.setdefault(row["officialDate"], []).append(row)
    return {"totalGames": len(games), "dates": [{"date": day, "totalGames": len(rows), "games": rows}
            for day, rows in dates.items()]}


def schedule_inventory(payload, start, end):
    """Explicit synthetic provenance for pure inventory tests (never real bytes)."""
    request = EvidenceRequest(request_id="fixture", evidence_class="stable_history",
        source_name="fixture", provider="mlb_statsapi",
        url=f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameTypes=R&startDate={start}&endDate={end}")
    return _schedule_inventory(payload, start, end, request=request, source_digest="a" * 64,
        captured_at="2026-09-25T00:00:00+00:00", source_response_path="fixture/raw.json")


def feed(row):
    teams = {}
    for side, player in (("away", 700001), ("home", 700002)):
        teams[side] = {"team": deepcopy(row["teams"][side]["team"]),
            "batters": [player], "pitchers": [player], "players": {f"ID{player}": {
                "person": {"id": player, "fullName": f"Fixture Player {player}"},
                "stats": {"batting": {"atBats": 4, "hits": 1, "plateAppearances": 4},
                          "pitching": {"inningsPitched": "6.1", "strikeOuts": 3}}}}}
    return {"gamePk": row["gamePk"], "gameData": {
        "datetime": {"officialDate": row["officialDate"]},
        "game": {"type": "R", "season": "2026"}, "status": deepcopy(row["status"]),
        "teams": {side: deepcopy(row["teams"][side]["team"]) for side in ("home", "away")}},
        "liveData": {"boxscore": {"teams": teams}, "linescore": {
            "teams": {side: {"runs": row["teams"][side]["score"]} for side in ("home", "away")}}}}


class Provider:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [game(), game(823101, "2026-03-26")]
        self.values = {"regular-season-inventory": schedule(*self.rows)}
        self.values.update({f'final-feed-{row["gamePk"]}': feed(row) for row in self.rows})
        self.calls = []

    def fetch(self, request):
        self.calls.append(request.request_id)
        value = self.values[request.request_id]
        if isinstance(value, Exception):
            raise value
        status, value = value if isinstance(value, tuple) else (200, value)
        now = datetime.now(timezone.utc)
        return ProviderResponse(body=json.dumps(value, indent=2).encode(), status_code=status,
            headers={}, first_observed_at_utc=now, captured_at_utc=now)


def job(tmp_path, *, provider=None, fetch=True, materialize=True, **changes):
    values = dict(backfill_id="fixture-pilot", season=2026, window_start="2026-03-25",
        window_end="2026-03-25", inventory_start="2026-01-01", inventory_end="2026-09-24",
        max_final_games=3, max_provider_requests=4)
    values.update(changes)
    result = MLBFactBackfill.create(tmp_path / "facts", BackfillPlan(**values))
    provider = provider or Provider()
    if fetch:
        result.fetch(provider)
    if materialize:
        result.materialize()
    return result, provider


def test_inventory_deterministic_doubleheaders_separate():
    one, two = game(), game(823102)
    first = schedule_inventory(schedule(one, two), "2026-01-01", "2026-09-24")
    second = schedule_inventory(schedule(two, one), "2026-01-01", "2026-09-24")
    assert first == second
    assert [r["gamePk"] for r in first["games"]] == ["823100", "823102"]


def test_duplicate_game_id_collapses_with_observation_diagnostics():
    inventory = schedule_inventory(schedule(game(), game()), "2026-01-01", "2026-09-24")
    assert len(inventory["games"]) == 1
    assert inventory["games"][0]["reconciliation"]["observation_count"] == 2
    assert inventory["reconciliation_summary"]["duplicate_observation_count"] == 1


def test_rescheduled_duplicate_reconciles_before_pilot_window_selection(tmp_path):
    postponed = game(823100, "2026-04-03", "Postponed")
    postponed["rescheduleDate"] = "2026-04-03T18:10:00Z"
    postponed["gameDate"] = "2026-04-02T20:10:00Z"
    final = game(823100, "2026-04-03")
    final["rescheduledFrom"] = "2026-04-02T20:10:00Z"
    provider = Provider()
    provider.values["regular-season-inventory"] = {"totalGames": 2, "dates": [
        {"date": "2026-04-02", "totalGames": 1, "games": [postponed]},
        {"date": "2026-04-03", "totalGames": 1, "games": [final]}]}
    result, _ = job(tmp_path, fetch=False, materialize=False)
    result.fetch(provider)
    state = result.verify()
    assert state["status"] == "PARTIAL"
    assert state["missing_count"] == 0 and state["expected_final_game_pks"] == []
    assert result.inventory()["games"][0]["status"]["detailedState"] == "Final"
    assert len(provider.calls) == 1
    result.resume(provider)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("state", ["Scheduled", "In Progress", "Postponed", "Cancelled", "Suspended"])
def test_nonfinal_never_materialized(tmp_path, state):
    result, provider = job(tmp_path, provider=Provider([game(state=state)]))
    status = result.verify()
    assert status["game_fact_count"] == 0
    assert status["nonfinal_game_pks"] == ["823100"]
    assert provider.calls == ["regular-season-inventory"]
    assert not (result.store.root / "game").exists()


def test_extracts_final_game_batter_pitcher_missing_values_and_safety(tmp_path):
    result, _ = job(tmp_path)
    status = result.verify()
    assert status["status"] == "COMPLETE"
    assert (status["game_fact_count"], status["batter_fact_count"], status["pitcher_fact_count"]) == (1, 2, 2)
    batter = result.store.read("BATTER", "823100", "700001")
    pitcher = result.store.read("PITCHER", "823100", "700001")
    assert (batter.at_bats, batter.hits, batter.walks) == (4, 1, None)
    assert (pitcher.outs_recorded, pitcher.hits_allowed) == (19, None)
    assert batter.research_only and not batter.eligible_for_betting
    assert not batter.kelly_eligible and not batter.eligible_for_official_pick
    assert {p.name for p in tmp_path.iterdir()} == {"facts"}


def test_raw_bytes_and_request_hashes_bind_facts(tmp_path):
    result, _ = job(tmp_path)
    records = result.journal.records()
    fact = result.store.read("GAME", "823100")
    for record in records:
        raw = (result.journal.root / f'{record["claim"]["sequence"]:06d}' / "body.bin").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["response"]["sha256"]
        assert any(record["response"]["sha256"] in ref and digest(record["claim"]) in ref for ref in fact.source_refs)


def test_replay_and_resume_are_idempotent_without_requests(tmp_path):
    result, provider = job(tmp_path)
    before = {str(p): p.read_bytes() for role in ("game", "batter", "pitcher")
              for p in (result.store.root / role).rglob("*.json")}
    result.materialize()
    fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
    fresh.resume(provider)
    assert len(provider.calls) == 2
    assert before == {p: Path(p).read_bytes() for p in before}


def test_fixture_conflicting_fact_never_overwritten(tmp_path):
    result, _ = job(tmp_path)
    old = result.store.read("BATTER", "823100", "700001")
    with pytest.raises(FactLedgerConflict):
        result.store.publish(replace(old, hits=2))
    assert result.store.read("BATTER", "823100", "700001") == old


def test_resume_conflict_stops_before_provider_calls(tmp_path):
    result, provider = job(tmp_path, materialize=False)
    row = result.inventory()["games"][0]
    facts = result._expected(row, result.journal.records())
    result.store.publish(replace(facts[0], final_home_runs=10))
    before = len(provider.calls)
    with pytest.raises(FactLedgerConflict):
        result.resume(provider)
    assert result.verify()["status"] == "CONFLICT"
    assert len(provider.calls) == before


@pytest.mark.parametrize("operation", ["fetch", "resume"])
@pytest.mark.parametrize("conflict_index", [0, 1], ids=["first-game", "second-game"])
@pytest.mark.parametrize("role,change", [
    ("GAME", {"final_home_runs": 10}),
    ("BATTER", {"hits": 2}),
    ("PITCHER", {"strikeouts": 4}),
])
def test_acquisition_conflict_checkpoints_before_stopping_requests(
    tmp_path, operation, conflict_index, role, change,
):
    rows = [game(), game(823102), game(823103)]
    provider = Provider(rows)
    result, _ = job(tmp_path / "acquisition", provider=provider, fetch=False, materialize=False)
    # The schedule is already preserved, so this execution counts game requests only.
    result.journal.capture(result._request(), provider)
    result.reconcile_inventory()
    before = len(provider.calls)
    assert provider.calls == ["regular-season-inventory"]
    preserved = {p: p.read_bytes() for p in result.journal.root.rglob("*") if p.is_file()}

    seed, _ = job(tmp_path / "existing", provider=Provider([rows[conflict_index]]), materialize=False)
    seed_facts = seed._expected(seed.inventory()["games"][0], seed.journal.records())
    conflicting = replace(next(f for f in seed_facts if f.role == role), **change)
    fact_path = result.store.publish(conflicting)
    original_fact = fact_path.read_bytes()
    conflict_id = str(rows[conflict_index]["gamePk"])
    request_ids = [f'final-feed-{r["gamePk"]}' for r in rows]

    with pytest.raises(FactLedgerConflict):
        getattr(result, operation)(provider)

    # Read disk before invoking verify: the exception must follow durable publication.
    checkpoint = read_document(sorted((result.root / "manifests").glob("*.json"))[-1])
    assert checkpoint["status"] == "CONFLICT"
    assert checkpoint["conflict_count"] == 1
    assert checkpoint["failed_game_pks"] == [conflict_id]
    assert checkpoint["errors"] == {conflict_id: "FactLedgerConflict"}
    assert provider.calls[before:] == request_ids[:conflict_index + 1]
    assert len(provider.calls) - before == conflict_index + 1
    assert checkpoint["provider_request_count"] == len(provider.calls)
    assert fact_path.read_bytes() == original_fact
    assert all(p.read_bytes() == raw for p, raw in preserved.items())

    records = result.journal.records()
    assert [r["claim"]["request_id"] for r in records] == provider.calls
    assert all(r["response"] is not None for r in records)
    for record in records:
        body = result.journal.root / f'{record["claim"]["sequence"]:06d}' / "body.bin"
        assert hashlib.sha256(body.read_bytes()).hexdigest() == record["response"]["sha256"]
    conflict_record = result._successful(records, conflict_id)
    conflict_body = result.journal.root / f'{conflict_record["claim"]["sequence"]:06d}' / "body.bin"
    assert conflict_body.read_bytes() == json.dumps(provider.values[f"final-feed-{conflict_id}"], indent=2).encode()
    for row in rows[conflict_index + 1:]:
        game_id = str(row["gamePk"])
        assert result._successful(records, game_id) is None
        assert game_id in checkpoint["expected_final_game_pks"]
        assert game_id not in checkpoint["completed_game_pks"] + checkpoint["failed_game_pks"]

    # Fresh instances must reject the persisted conflict at both entry gates.
    calls_at_conflict = list(provider.calls)
    raw_at_conflict = {p: p.read_bytes() for p in result.journal.root.rglob("*") if p.is_file()}
    for restart_operation in ("fetch", "resume"):
        fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
        with pytest.raises(FactLedgerConflict):
            getattr(fresh, restart_operation)(provider)
        assert provider.calls == calls_at_conflict
        assert fresh.verify()["status"] == "CONFLICT"
    with pytest.raises(FactLedgerConflict):
        result.materialize()
    assert {p: p.read_bytes() for p in result.journal.root.rglob("*") if p.is_file()} == raw_at_conflict
    assert fact_path.read_bytes() == original_fact


def test_acquisition_identical_facts_skip_and_missing_games_continue(tmp_path):
    provider = Provider([game(), game(823102), game(823103)])
    result, _ = job(tmp_path, provider=provider, fetch=False, materialize=False)
    result.journal.capture(result._request(), provider)
    result.reconcile_inventory()
    result.journal.capture(result._request("823100"), provider)
    assert result.materialize()["status"] == "PARTIAL"
    first_facts = {p: p.read_bytes() for role in ("game", "batter", "pitcher")
                   for p in (result.store.root / role).rglob("*.json")}
    before = len(provider.calls)
    state = result.resume(provider)
    assert provider.calls[before:] == ["final-feed-823102", "final-feed-823103"]
    assert state["status"] == "COMPLETE" and state["conflict_count"] == 0
    assert (state["game_fact_count"], state["batter_fact_count"], state["pitcher_fact_count"]) == (3, 6, 6)
    assert all(p.read_bytes() == raw for p, raw in first_facts.items())
    assert len(provider.calls) == result.plan.max_provider_requests == 4
    fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
    assert fresh.resume(provider)["status"] == "COMPLETE"
    assert len(provider.calls) == 4


def test_resume_missing_facts_uses_preserved_evidence(tmp_path):
    result, provider = job(tmp_path, materialize=False)
    row = result.inventory()["games"][0]
    facts = result._expected(row, result.journal.records())
    result.store.publish(facts[0])  # Simulated interrupted per-record publication.
    fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
    assert fresh.resume(provider)["status"] == "COMPLETE"
    assert len(provider.calls) == 2


def test_resume_retries_only_missing_game_and_retains_failure(tmp_path):
    provider = Provider([game(), game(823102)])
    original = provider.values["final-feed-823102"]
    provider.values["final-feed-823102"] = OSError("synthetic transport failure")
    result, _ = job(tmp_path, provider=provider, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="transport failed"):
        result.fetch(provider)
    assert len(provider.calls) == 3
    provider.values["final-feed-823102"] = original
    fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
    assert fresh.resume(provider)["status"] == "COMPLETE"
    assert provider.calls.count("final-feed-823100") == 1
    assert len(fresh.journal.records()) == 4
    assert fresh.journal.records()[2]["response"]["provider_status"] == "TRANSPORT_ERROR"


@pytest.mark.parametrize("artifact", ["plan.json", "inventory.json", "manifests/000001.json", "raw/000002/body.bin"])
def test_damaged_artifact_fails_closed(tmp_path, artifact):
    result, provider = job(tmp_path)
    path = result.root / artifact
    path.write_bytes(path.read_bytes() + b"damage")
    calls = len(provider.calls)
    with pytest.raises((ValueError, KeyError)):
        MLBFactBackfill(result.store.root, result.plan.backfill_id).resume(provider)
    assert len(provider.calls) == calls


def test_coverage_is_independent_of_ledger_and_binds_expected_hashes(tmp_path):
    result, _ = job(tmp_path, materialize=False)
    before = result.coverage_index(through="2026-03-25")
    assert not before["complete"]
    expected_hash = before["players"]["700001"][0]["factual_record_hash"]
    assert not before["players"]["700001"][0]["ledger_match"]
    result.materialize()
    after = result.coverage_index(through="2026-03-25")
    assert after["complete"]
    assert after["players"]["700001"][0]["factual_record_hash"] == expected_hash
    assert expected_hash == result.store.read("BATTER", "823100", "700001").factual_record_hash
    incomplete = result.coverage_index(through="2026-03-26")
    assert not incomplete["complete"]
    assert incomplete["unknown_participation_game_pks"] == ["823101"]


def test_exact_inventory_loads_sovereign_season_and_compact_hash_refs(tmp_path):
    result, _ = job(tmp_path)
    index, coverage = result.batter_coverage("700001", "823101")
    assert index["complete"] and coverage.complete
    season = load_ledger_season(result.store, coverage, player_name="Fixture Player 700001")
    assert season.source == CANONICAL_HITS_SEASON_SOURCE
    assert (season.at_bats, season.hits) == (4, 1)
    assert season.source_refs == (f"cv-ledger-coverage:sha256:{coverage.manifest_hash}",
                                 f"cv-ledger-season:sha256:{season.season_aggregate_hash}")


def test_partial_season_window_cannot_attest_full_coverage(tmp_path):
    result, _ = job(tmp_path, inventory_start="2026-03-25")
    _, coverage = result.batter_coverage("700001", "823101")
    assert not coverage.complete
    with pytest.raises(HitsLedgerError):
        load_ledger_season(result.store, coverage, player_name="Fixture Player")


def test_missing_ab_is_not_zero_and_cannot_qualify(tmp_path):
    provider = Provider()
    del provider.values["final-feed-823100"]["liveData"]["boxscore"]["teams"]["away"]["players"]["ID700001"]["stats"]["batting"]["atBats"]
    result, _ = job(tmp_path, provider=provider)
    assert result.store.read("BATTER", "823100", "700001").at_bats is None
    _, coverage = result.batter_coverage("700001", "823101")
    assert not coverage.complete


def test_gap_report_exact_games_unknown_player_denominator(tmp_path):
    result, provider = job(tmp_path)
    gap = result.gap_report()
    assert (gap["total_expected_final_games"], gap["games_represented_in_ledger"],
            gap["games_requiring_acquisition"], gap["full_backfill_estimated_requests"]) == (2, 1, 1, 1)
    assert gap["remaining_batter_fact_count"] == "UNKNOWN"
    assert gap["verified_batter_fact_count"] == 2
    assert len(provider.calls) == 2


def test_gap_report_counts_unpublished_facts_when_all_feeds_are_preserved(tmp_path):
    result, _ = job(tmp_path, provider=Provider([game()]), materialize=False)
    gap = result.gap_report()
    assert gap["full_backfill_estimated_requests"] == 0
    assert gap["remaining_batter_fact_count"] == 2
    assert gap["remaining_pitcher_fact_count"] == 2
    assert gap["games_represented_in_ledger"] == 0


@pytest.mark.parametrize("mutation", ["gamePk", "date", "team", "finality", "participation", "score"])
def test_feed_identity_finality_and_participation_fail_closed(tmp_path, mutation):
    provider = Provider()
    value = provider.values["final-feed-823100"]
    if mutation == "gamePk":
        value["gamePk"] = 999999
    elif mutation == "date":
        value["gameData"]["datetime"]["officialDate"] = "2026-03-24"
    elif mutation == "team":
        value["gameData"]["teams"]["home"]["id"] = 999
    elif mutation == "finality":
        value["gameData"]["status"]["detailedState"] = "Suspended"
    elif mutation == "score":
        value["liveData"]["linescore"]["teams"]["home"]["runs"] = 999
    else:
        value["liveData"]["boxscore"]["teams"]["home"]["batters"].append(888888)
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError):
        result.fetch(provider)
    assert result.verify()["status"] == "FAILED"
    assert result.verify()["game_fact_count"] == 0
    assert len(result.journal.records()) == 2


def test_http_error_bytes_preserved_and_budget_enforced(tmp_path):
    provider = Provider()
    provider.values["final-feed-823100"] = (429, {"message": "fixture rate limit"})
    result, _ = job(tmp_path, fetch=False, materialize=False, max_provider_requests=2)
    with pytest.raises(BackfillError, match="non-200"):
        result.fetch(provider)
    error = result.journal.records()[1]
    assert error["response"]["http_status"] == 429
    assert (result.journal.root / "000002" / "body.bin").exists()
    with pytest.raises(BackfillError, match="budget exhausted"):
        result.resume(provider)
    assert len(provider.calls) == 2


def test_game_cap_checked_before_feed_requests(tmp_path):
    provider = Provider([game(823100 + n) for n in range(4)])
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="game cap"):
        result.fetch(provider)
    assert len(provider.calls) == 1


def test_schedule_count_mismatch_fails_closed():
    value = schedule(game())
    value["totalGames"] += 1
    with pytest.raises(BackfillError, match="count mismatch"):
        schedule_inventory(value, "2026-01-01", "2026-09-24")


def test_existing_ledger_alone_cannot_supply_coverage(tmp_path):
    result, _ = job(tmp_path)
    other = MLBFactBackfill.create(result.store.root, replace(result.plan, backfill_id="no-inventory"))
    with pytest.raises(BackfillError):
        other.coverage_index(through="2026-03-25")


@pytest.mark.parametrize("url", ["https://api.the-odds-api.com/v4/sports", "http://statsapi.mlb.com/api/v1/schedule",
    "https://statsapi.mlb.com/api/v1/people/700001/stats", "https://statsapi.mlb.com/api/v1.1/game/823100/feed/live?apiKey=secret"])
def test_unapproved_endpoints_and_keys_rejected(url):
    with pytest.raises(BackfillError):
        validate_url(url)


def test_interrupted_request_claim_is_durable_and_not_retried(tmp_path):
    result, _ = job(tmp_path, fetch=False, materialize=False)
    class Interrupted:
        def fetch(self, request):
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        result.fetch(Interrupted())
    provider = Provider()
    fresh = MLBFactBackfill(result.store.root, result.plan.backfill_id)
    assert fresh.verify()["provider_request_count"] == 1
    with pytest.raises(BackfillError, match="unknown outcome"):
        fresh.resume(provider)
    assert provider.calls == []


def test_raw_persistence_failure_prevents_fact_publication(tmp_path, monkeypatch):
    from courtvision.sports.mlb import fact_backfill_evidence as evidence
    result, _ = job(tmp_path, fetch=False, materialize=False)
    real_publish = evidence.publish_bytes
    def fail_body(path, raw):
        if path.name == "body.bin":
            raise OSError("fixture storage unavailable")
        return real_publish(path, raw)
    monkeypatch.setattr(evidence, "publish_bytes", fail_body)
    with pytest.raises(OSError):
        result.fetch(Provider())
    assert result.journal.records()[0]["response"] is None
    assert not (result.store.root / "game").exists()


def test_saved_manifest_contains_required_operational_state(tmp_path):
    result, _ = job(tmp_path)
    latest = read_document(sorted((result.root / "manifests").glob("*.json"))[-1])
    required = {"schema_version", "backfill_id", "season", "window_start", "window_end",
        "generated_at", "schedule_inventory_hash", "expected_final_game_pks", "completed_game_pks",
        "failed_game_pks", "nonfinal_game_pks", "raw_evidence_refs", "game_fact_count",
        "batter_fact_count", "pitcher_fact_count", "conflict_count", "missing_count",
        "provider_request_count", "status"}
    assert required <= latest.keys()
    assert latest["status"] == "COMPLETE" and latest["missing_count"] == 0


def test_missing_participation_stats_cannot_claim_coverage(tmp_path):
    provider = Provider()
    provider.values["final-feed-823100"]["liveData"]["boxscore"]["teams"]["away"]["players"]["ID700001"]["stats"]["batting"] = {}
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="participation"):
        result.fetch(provider)
    assert result.verify()["game_fact_count"] == 0


def pitcher_only_provider():
    provider = Provider()
    team = provider.values["final-feed-823100"]["liveData"]["boxscore"]["teams"]["away"]
    team["batters"].append(700003)
    team["pitchers"].append(700003)
    team["players"]["ID700003"] = {
        "person": {"id": 700003, "fullName": "Fixture Pitcher Only"},
        "position": {"code": "1", "type": "Pitcher", "abbreviation": "P"},
        "stats": {"batting": {}, "pitching": {"inningsPitched": "1.0", "strikeOuts": 1}},
    }
    return provider, team


def test_pitcher_only_listing_does_not_create_batting_fact_or_coverage(tmp_path):
    provider, _ = pitcher_only_provider()
    result, _ = job(tmp_path, provider=provider)
    assert result.verify()["pitcher_fact_count"] == 3
    assert result.verify()["batter_fact_count"] == 2
    assert result.store.read("PITCHER", "823100", "700003").outs_recorded == 3
    with pytest.raises(FileNotFoundError):
        result.store.read("BATTER", "823100", "700003")
    index = result.coverage_index(through="2026-03-25")
    assert index["complete"] and "700003" not in index["players"]


@pytest.mark.parametrize("change", ["missing_batting", "batting_order", "hitter_position"])
def test_pitcher_exception_does_not_hide_unknown_batting_participation(tmp_path, change):
    provider, team = pitcher_only_provider()
    player = team["players"]["ID700003"]
    if change == "missing_batting":
        del player["stats"]["batting"]
    elif change == "batting_order":
        player["battingOrder"] = "900"
    else:
        player["position"] = {"code": "7", "type": "Outfielder", "abbreviation": "LF"}
    result, _ = job(tmp_path, fetch=False, materialize=False)
    with pytest.raises(BackfillError, match="participation"):
        result.fetch(provider)
    assert result.verify()["game_fact_count"] == 0


def test_pitcher_with_explicit_batting_stats_keeps_both_role_facts(tmp_path):
    provider, team = pitcher_only_provider()
    team["players"]["ID700003"]["stats"]["batting"] = {"atBats": 0, "hits": 0}
    result, _ = job(tmp_path, provider=provider)
    assert result.verify()["batter_fact_count"] == result.verify()["pitcher_fact_count"] == 3
    assert result.store.read("BATTER", "823100", "700003").at_bats == 0


def test_redirect_is_not_followed_and_no_credentials_or_proxy_are_used(monkeypatch):
    import urllib.error
    import urllib.request
    from io import BytesIO
    from courtvision.sports.mlb.fact_backfill_evidence import StatsAPIProvider, _NoRedirect
    from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert timeout == 30
            assert set(dict(request.header_items())) == {"User-agent", "Accept", "Accept-encoding"}
            raise urllib.error.HTTPError(request.full_url, 302, "fixture redirect", {}, BytesIO(b"redirect"))
    def opener(proxy, redirect):
        assert isinstance(proxy, urllib.request.ProxyHandler) and proxy.proxies == {}
        assert isinstance(redirect, _NoRedirect)
        assert redirect.redirect_request(None, None, 302, "", {}, "https://example.com") is None
        return Opener()
    monkeypatch.setattr(urllib.request, "build_opener", opener)
    request = EvidenceRequest(request_id="fixture", evidence_class="stable_history", source_name="fixture",
        provider="mlb_statsapi", url="https://statsapi.mlb.com/api/v1.1/game/823100/feed/live")
    response = StatsAPIProvider().fetch(request)
    assert response.status_code == 302 and response.body == b"redirect" and len(calls) == 1
