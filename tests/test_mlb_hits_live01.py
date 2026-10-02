"""LIVE-01 acceptance with synthetic provider bodies; real networking is forbidden."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest

from courtvision.sports.mlb import live01
from courtvision.sports.mlb.data.prospective_context_acquisition import ProviderResponse
from courtvision.sports.mlb.fact_backfill import BackfillPlan, MLBFactBackfill
from courtvision.sports.mlb import fact_backfill_evidence as evidence
from courtvision.sports.mlb.fact_backfill_evidence import BackfillError, EvidenceJournal, digest, read_document
from courtvision.sports.mlb.fact_ledger import FactLedgerConflict, MLBFactStore
from courtvision.sports.mlb.live01_evidence import (
    OFFICIAL_TYPES, batter_coverage, catch_up, compose_coverage, historical_inventory,
    inventory_from_capture, schedule_request,
)
from courtvision.sports.mlb.live01_freeze import (
    csv_bytes, freeze_predictions, read_rows, row_hash, validate_row, verify_freeze,
)
from courtvision.sports.mlb.live01_market import capture_market
from courtvision.sports.mlb.providers.the_odds_api_live import MLBOddsIngestionConfig
from courtvision.sports.mlb.providers.the_odds_api_transport import OddsAPIHTTPResponse
from test_mlb_fact_backfill import game, schedule, feed

NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
TARGET = NOW.date()
SHA = "a" * 40


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("LIVE-01 tests cannot access real providers")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(evidence, "utc_now", lambda: NOW - timedelta(seconds=1))


class BaseballProvider:
    def __init__(self, payloads):
        self.payloads, self.calls = payloads, []

    def fetch(self, request):
        self.calls.append(request.request_id)
        value = self.payloads[request.request_id]
        return ProviderResponse(json.dumps(value).encode(), 200, {}, NOW, NOW)


def full_feed(row, *, pregame=False):
    result = feed(row)
    result["gameData"]["game"]["type"] = row["gameType"]
    result["gameData"]["datetime"]["dateTime"] = row["gameDate"]
    result["gameData"]["venue"] = row["venue"]
    if pregame:
        for side, first in (("home", 710000), ("away", 720000)):
            team = result["liveData"]["boxscore"]["teams"][side]
            # Complete orders include players without qualified historical facts;
            # only the original two batters have sovereign H/AB evidence.
            additional = list(range(first, first + 8))
            team["battingOrder"] = team["batters"] + additional
            for player in additional:
                team["players"][f"ID{player}"] = {
                    "person": {"id": player, "fullName": f"Fixture Player {player}"}}
        result["liveData"]["boxscore"]["teams"]["home"]["players"]["ID700003"] = {
            "person": {"id": 700003, "fullName": "Fixture Bench Player"}}
    return result


def target_game(**changes):
    row = game(900001, TARGET.isoformat(), "Scheduled")
    row["status"] = {"abstractGameState": "Preview", "detailedState": "Scheduled",
                     "codedGameState": "S", "statusCode": "S", "abstractGameCode": "P"}
    row.update(changes)
    return row


def history(tmp_path):
    row = game(823100, "2026-09-24")
    provider = BaseballProvider({"regular-season-inventory": schedule(row),
                                 "final-feed-823100": full_feed(row)})
    job = MLBFactBackfill.create(tmp_path / "facts", BackfillPlan(
        "historical", 2026, "2026-09-24", "2026-09-24", "2026-01-01", "2026-09-24"))
    job.fetch(provider)
    job.materialize()
    index = historical_inventory(job.store.root, "historical")
    assert len(provider.calls) == 2
    return job.store, index


def catchup(tmp_path, store, *, game_type="R", rows=None):
    row = game(823101, "2026-09-25")
    row["gameType"] = game_type
    rows = [row] if rows is None else rows
    provider = BaseballProvider({"catchup-schedule": schedule(*rows),
        **{"catchup-feed-" + str(r["gamePk"]): full_feed(r) for r in rows}})
    root = tmp_path / "catchup"
    index, stats = catch_up(root, store, target=TARGET, provider=provider)
    return index, stats, provider, root


def cohort(tmp_path, *, change_feed=None, game_type="R"):
    store, historical = history(tmp_path)
    caught, _, _, _ = catchup(tmp_path, store, game_type=game_type)
    index = compose_coverage(historical, caught, target=TARGET, store=store)
    row = target_game(gameType=game_type)
    current = full_feed(row, pregame=True)
    if change_feed:
        change_feed(current)
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(row),
                                "pregame-feed-900001": current})
    journal = EvidenceJournal(tmp_path / "target-raw", 8)
    target, inventory, record, selection = live01.select_target(
        journal, provider, now=NOW, clock=lambda: NOW)
    result = live01.generate_cohort(inventory, record, journal, provider, index=index, store=store,
        run_id="synthetic-live01", repository_sha=SHA, clock=lambda: NOW + timedelta(seconds=1))
    return result, provider, index, selection


def frozen(tmp_path, **kwargs):
    result, provider, index, selection = cohort(tmp_path, **kwargs)
    rows, sources, exclusions, counts = result
    root = freeze_predictions(tmp_path / TARGET.isoformat(), run_id="synthetic-live01",
        operating_date=TARGET, repository_sha=SHA, rows=rows, sources=sources,
        exclusions=exclusions, stage_counts=counts, clock=lambda: NOW + timedelta(seconds=2))
    return root, result


def odds_config():
    return MLBOddsIngestionConfig(operating_date=TARGET, markets=("batter_hits",), regions="us",
        maximum_events=1, maximum_http_requests=2, maximum_provider_credits=1,
        event_odds_declared_max_credit_cost=1, discovery_declared_max_credit_cost=0,
        discovery_zero_cost_verified=True, network_enabled=True)


class MarketProvider:
    def __init__(self, freeze_root, *, no_quotes=False):
        self.freeze_root, self.no_quotes = freeze_root, no_quotes
        self.calls = []

    def send(self, request, **kwargs):
        # This assertion lives at the provider boundary, not just the orchestrator.
        manifest, rows = verify_freeze(self.freeze_root)
        assert manifest["prediction_row_hash_verification"] == "PASS"
        assert len(rows) == 2
        self.calls.append(request.kind)
        event = {"id": "fixture-event", "sport_key": "baseball_mlb",
            "home_team": "Fixture Home", "away_team": "Fixture Away",
            "commence_time": "2026-10-01T21:00:00Z"}
        if request.kind == "discovery":
            unrelated = {**event, "id": "unrelated", "home_team": "Other Home"}
            payload, cost = [event, unrelated], "0"
        else:
            books = []
            if not self.no_quotes:
                for key, price in (("b", -120), ("a", -110)):
                    books.append({"key": key, "title": "Book " + key, "markets": [
                        {"key": "batter_hits", "last_update": NOW.isoformat(), "outcomes": [
                            {"name": "Over", "description": "Fixture Player 700001", "point": .5, "price": price},
                            {"name": "Under", "description": "Fixture Player 700002", "point": .5, "price": price}]},
                        {"key": "batter_hits_alternate", "last_update": NOW.isoformat(), "outcomes": [
                            {"name": "Over", "description": "Fixture Player 700002", "point": .5, "price": price}]}]})
            payload, cost = {**event, "bookmakers": books}, "1"
        return OddsAPIHTTPResponse(200, (("x-requests-last", cost),), json.dumps(payload).encode())


def test_market_independent_identity_entire_eligible_cohort(tmp_path):
    (rows, sources, exclusions, counts), provider, index, selection = cohort(tmp_path)
    assert {r["mlbam_player_id"] for r in rows} == {"700001", "700002"}
    assert counts["confirmed_batters"] == 18
    assert counts["ledger_qualified_batters"] == 2
    assert any(e.get("player_id") == "700003" and e["reason"] == "NOT_IN_CONFIRMED_BATTING_ORDER"
               for e in exclusions)
    assert provider.calls == ["target-schedule-2026-10-01", "pregame-feed-900001"]
    assert selection["target_operating_date"] == "2026-10-01"
    assert not selection["target_date_advanced"]
    for row in rows:
        assert row["mlbam_game_id"] == "900001"
        assert (row["season_hits"], row["season_at_bats"], row["distinct_batting_games"]) == (2, 8, 2)
        assert row["projected_at_bats"] == 4
        assert row["model_probability"] == 1 - .75 ** 4
        assert row["model_version"] == "research-v1"
        assert "COURTVISION_GAME_FACT_LEDGER" in row["source_refs"]
        assert row["projection_model_version"] == "cv_ab_projection_v2"
        assert not {"sportsbook", "odds", "line", "edge", "result", "profit"} & set(row)


@pytest.mark.parametrize("missing_boxscore", [False, True])
def test_unconfirmed_orders_have_durable_zero_row_run(tmp_path, missing_boxscore):
    def remove(current):
        if missing_boxscore:
            current["liveData"].pop("boxscore")
            return
        for team in current["liveData"]["boxscore"]["teams"].values():
            team.pop("battingOrder", None)
    root, (rows, _, exclusions, counts) = frozen(tmp_path, change_feed=remove)
    assert rows == []
    manifest, persisted = verify_freeze(root)
    assert manifest["row_count"] == 0 and persisted == []
    assert (root / "exclusions.json").exists()
    assert counts["games_without_confirmed_lineups"] == 1
    assert any(e["reason"] == "LINEUP_NOT_CONFIRMED" for e in exclusions)


@pytest.mark.parametrize("side", ["away", "home"])
@pytest.mark.parametrize("order_size", [None, 0, 1, 8])
def test_partial_lineup_cannot_claim_the_date_or_generate_predictions(tmp_path, monkeypatch, side, order_size):
    def partial(current):
        team = current["liveData"]["boxscore"]["teams"][side]
        if order_size is None:
            team.pop("battingOrder")
        else:
            team["battingOrder"] = team["battingOrder"][:order_size]
    def forbidden(*args, **kwargs):
        raise AssertionError("incomplete game lineup must stop before model generation")
    with monkeypatch.context() as patch:
        patch.setattr(live01, "prediction_row", forbidden)
        root, (rows, _, exclusions, counts) = frozen(tmp_path, change_feed=partial)
    before = (root / "manifest.json").read_bytes()
    assert rows == verify_freeze(root)[1] == []
    assert counts["games_with_confirmed_lineups"] == 0
    assert counts["games_without_confirmed_lineups"] == 1
    assert any(e.get("detail") == "INCOMPLETE_BATTING_ORDERS" and e["team_sides"] == [side]
               for e in exclusions)
    fake = MarketProvider(root)
    assert capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake)[
        "odds_provider_calls"] == 0
    assert fake.calls == []
    (complete, sources, excluded, stages), _, _, _ = cohort(tmp_path / "later")
    for row in complete:
        row["prediction_run_id"] = "complete-lineups"
        row["prediction_payload_sha256"] = row_hash(row)
    later = freeze_predictions(root.parent, run_id="complete-lineups", operating_date=TARGET,
        repository_sha=SHA, rows=complete, sources=sources, exclusions=excluded, stage_counts=stages,
        clock=lambda: NOW + timedelta(seconds=4))
    assert len(verify_freeze(later)[1]) == 2
    assert (root / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("game_type", ["F", "D", "L", "W"])
def test_exact_observed_postseason_type_versions_coverage_without_changing_formula(tmp_path, game_type):
    (rows, _, _, _), _, index, _ = cohort(tmp_path, game_type=game_type)
    assert game_type in index["observed_game_types"]
    for row in rows:
        assert row["official_game_type"] == game_type
        assert row["model_version"] == "research-v1-live01-official-history"
        assert row["model_probability"] == 1 - .75 ** 4
        assert row["hits_coverage_policy"] == "official-regular-and-observed-postseason-prior-date-v1"


@pytest.mark.parametrize("game_type", ["S", "E", "A", "I", "C", "P", "UNKNOWN"])
def test_special_unknown_types_fail_closed(tmp_path, game_type):
    row = target_game(gameType=game_type)
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(row)})
    with pytest.raises(BackfillError, match="UNSUPPORTED_GAME_TYPE"):
        live01.select_target(EvidenceJournal(tmp_path / "raw", 8), provider, now=NOW, clock=lambda: NOW)
    assert len(provider.calls) == 1


def test_target_date_advances_only_when_no_unstarted_games(tmp_path):
    today = target_game(gameDate="2026-10-01T19:00:00Z")
    tomorrow = target_game(officialDate="2026-10-02", gameDate="2026-10-02T21:00:00Z")
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(today),
                                 "target-schedule-2026-10-02": schedule(tomorrow)})
    selected, _, _, result = live01.select_target(EvidenceJournal(tmp_path / "raw", 8),
        provider, now=NOW, clock=lambda: NOW)
    assert selected == date(2026, 10, 2) and result["target_date_advanced"]
    assert result["target_date_advance_reason"] == "NO_REMAINING_UNSTARTED_OFFICIAL_GAMES"


def test_target_search_is_bounded(tmp_path):
    provider = BaseballProvider({"target-schedule-" + (TARGET + timedelta(days=i)).isoformat(): schedule()
                                 for i in range(7)})
    selected, _, _, result = live01.select_target(EvidenceJournal(tmp_path / "raw", 8),
        provider, now=NOW, clock=lambda: NOW)
    assert selected is None and len(provider.calls) == 7


def test_catchup_reuses_history_publishes_only_missing_final_and_is_idempotent(tmp_path):
    store, historical = history(tmp_path)
    prior = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.rglob("*.json")}
    nonfinal = game(823102, "2026-09-26", "Postponed")
    caught, stats, provider, root = catchup(tmp_path, store, rows=[game(823101, "2026-09-25"), nonfinal])
    assert provider.calls == ["catchup-schedule", "catchup-feed-823101"]
    assert (stats["new_game_facts"], stats["new_batter_facts"], stats["new_pitcher_facts"]) == (1, 2, 2)
    again, repeated = catch_up(root, store, target=TARGET, provider=provider)
    assert again == caught and repeated["new_game_feeds"] == 0
    assert repeated["existing_identical_games"] == 1
    assert len(provider.calls) == 2
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in prior.items())


def test_existing_fact_without_independent_capture_never_refetched(tmp_path):
    store, historical = history(tmp_path)
    caught, _, _, _ = catchup(tmp_path, store)
    provider = BaseballProvider({"catchup-schedule": schedule(game(823101, "2026-09-25"))})
    with pytest.raises(BackfillError, match="never refetch"):
        catch_up(tmp_path / "different-journal", store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule"]


def test_catchup_conflict_stops_and_restart_makes_zero_calls(tmp_path, monkeypatch):
    store, historical = history(tmp_path)
    original = store.publish
    def conflict(fact):
        if fact.role == "BATTER":
            raise FactLedgerConflict("synthetic immutable conflict")
        return original(fact)
    monkeypatch.setattr(store, "publish", conflict)
    rows = [game(823101, "2026-09-25"), game(823102, "2026-09-26")]
    provider = BaseballProvider({"catchup-schedule": schedule(*rows),
        **{"catchup-feed-" + str(r["gamePk"]): full_feed(r) for r in rows}})
    root = tmp_path / "catchup-conflict"
    with pytest.raises(FactLedgerConflict):
        catch_up(root, store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule", "catchup-feed-823101"]
    with pytest.raises(FactLedgerConflict):
        catch_up(root, store, target=TARGET, provider=provider)
    assert len(provider.calls) == 2 and (root / "conflict.json").exists()


@pytest.mark.parametrize("withheld", [("BATTER", "823100", "700001"), ("GAME", "823101", None)])
def test_independent_composition_detects_missing_expected_fact(tmp_path, monkeypatch, withheld):
    store, historical = history(tmp_path)
    caught, _, _, _ = catchup(tmp_path, store)
    original = store.read
    def missing(role, game_id, player_id=None):
        if (role, game_id, player_id) == withheld:
            raise FileNotFoundError("synthetic withheld expected fact")
        return original(role, game_id, player_id)
    monkeypatch.setattr(store, "read", missing)
    with pytest.raises(FileNotFoundError):
        compose_coverage(historical, caught, target=TARGET, store=store)


@pytest.mark.parametrize("change", ["through", "complete", "overlap"])
def test_independent_coverage_scope_cannot_be_relabelled(tmp_path, change):
    store, historical = history(tmp_path)
    caught, _, _, _ = catchup(tmp_path, store)
    if change == "through":
        caught["through"] = "2026-09-29"
    elif change == "complete":
        caught["complete"] = False
    else:
        caught["games"] += historical["games"]
    with pytest.raises(BackfillError):
        compose_coverage(historical, caught, target=TARGET, store=store)


def test_every_hash_recomputes_and_identical_freeze_is_noop(tmp_path):
    root, (rows, sources, exclusions, counts) = frozen(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    again = freeze_predictions(root.parent, run_id="synthetic-live01", operating_date=TARGET,
        repository_sha=SHA, rows=rows, sources=sources, exclusions=exclusions, stage_counts=counts,
        clock=lambda: NOW + timedelta(seconds=5))
    assert root == again
    _, restored = verify_freeze(root)
    assert csv_bytes(restored) == (root / "predictions.csv").read_bytes()
    assert all(row_hash(r) == r["prediction_payload_sha256"] for r in restored)
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}


@pytest.mark.parametrize("field,value", [("season_hits", 3), ("player_name", "Changed"),
    ("generated_at", "2026-10-01T21:00:01+00:00"), ("research_only", False)])
def test_one_field_change_fails_row_hash(tmp_path, field, value):
    (rows, _, _, _), _, _, _ = cohort(tmp_path)
    rows[0][field] = value
    with pytest.raises(BackfillError, match="row hash"):
        validate_row(rows[0])


def test_frozen_file_tampering_fails_before_any_market_call(tmp_path):
    root, _ = frozen(tmp_path)
    path = root / "predictions.csv"
    path.write_bytes(path.read_bytes().replace(b"Fixture Player 700001", b"Changed Player 700001"))
    fake = MarketProvider(root)
    with pytest.raises(BackfillError, match="file hash"):
        capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake)
    assert fake.calls == []


def test_no_market_provider_call_without_freeze(tmp_path):
    fake = MarketProvider(tmp_path)
    with pytest.raises(FileNotFoundError):
        capture_market(tmp_path, config=odds_config(), api_key="synthetic-key", transport=fake)
    assert fake.calls == []


def test_market_call_order_binding_main_half_hit_filter_and_missing_quote(tmp_path):
    root, _ = frozen(tmp_path)
    before = (root / "predictions.csv").read_bytes()
    fake = MarketProvider(root)
    result = capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake,
                            clock=lambda: NOW + timedelta(seconds=3))
    assert fake.calls == ["discovery", "event_odds"]
    assert result["market_quotes_captured"] == 2
    assert result["frozen_rows_with_market"] == result["frozen_rows_without_market"] == 1
    assert result["odds_declared_credit_budget"] == result["odds_provider_credits_used"] == 1
    assert datetime.fromisoformat(result["first_market_request_at"]) > datetime.fromisoformat(
        result["prediction_artifact_durable_at"])
    _, rows = verify_freeze(root)
    expected = {r["prediction_id"]: r["prediction_payload_sha256"] for r in rows}
    assert all(expected[q["prediction_id"]] == q["prediction_payload_sha256"] for q in result["quotes"])
    assert all(q["provider_market_key"] == "batter_hits" and q["side"] == "OVER" and q["point"] == .5
               for q in result["quotes"])
    entry = next(r for r in result["research_board"] if r["sportsbook"])
    assert entry["sportsbook"] == "Book a"
    assert before == (root / "predictions.csv").read_bytes()
    assert result["outcome_status"] == result["closing_status"] == "PENDING"


def test_quote_absence_does_not_delete_any_prediction(tmp_path):
    root, _ = frozen(tmp_path)
    fake = MarketProvider(root, no_quotes=True)
    result = capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake,
                            clock=lambda: NOW + timedelta(seconds=3))
    assert result["market_quotes_captured"] == 0 and result["frozen_rows_without_market"] == 2
    assert len(verify_freeze(root)[1]) == 2


def test_same_date_different_run_cannot_replace_scientific_population(tmp_path):
    root, (rows, sources, exclusions, counts) = frozen(tmp_path)
    original = (root / "predictions.csv").read_bytes()
    for row in rows:
        row["prediction_run_id"] = "different-run"
        row["prediction_payload_sha256"] = row_hash(row)
    with pytest.raises(BackfillError, match="immutable"):
        freeze_predictions(root.parent, run_id="different-run", operating_date=TARGET,
            repository_sha=SHA, rows=rows, sources=sources, exclusions=exclusions, stage_counts=counts,
            clock=lambda: NOW + timedelta(seconds=3))
    assert (root / "predictions.csv").read_bytes() == original


def test_same_run_different_content_conflicts(tmp_path):
    root, (rows, sources, exclusions, counts) = frozen(tmp_path)
    with pytest.raises(BackfillError, match="same-run"):
        freeze_predictions(root.parent, run_id="synthetic-live01", operating_date=TARGET,
            repository_sha=SHA, rows=rows, sources=sources, exclusions=exclusions + [{"reason": "changed"}],
            stage_counts=counts)


def test_zero_row_run_can_be_followed_by_new_pregame_run_without_rewrite(tmp_path):
    date_root = tmp_path / TARGET.isoformat()
    first = freeze_predictions(date_root, run_id="waiting", operating_date=TARGET,
        repository_sha=SHA, rows=[], sources={}, exclusions=[{"reason": "LINEUP_NOT_CONFIRMED"}],
        stage_counts={}, clock=lambda: NOW)
    original = (first / "manifest.json").read_bytes()
    (rows, sources, exclusions, counts), _, _, _ = cohort(tmp_path / "second")
    later = freeze_predictions(date_root, run_id="synthetic-live01", operating_date=TARGET,
        repository_sha=SHA, rows=rows, sources=sources, exclusions=exclusions,
        stage_counts=counts, clock=lambda: NOW + timedelta(seconds=2))
    assert verify_freeze(first)[1] == [] and len(verify_freeze(later)[1]) == 2
    assert (first / "manifest.json").read_bytes() == original


@pytest.mark.parametrize("clock_delta", [timedelta(hours=1), timedelta(hours=2)])
def test_started_game_cannot_be_frozen(tmp_path, clock_delta):
    (rows, sources, exclusions, counts), _, _, _ = cohort(tmp_path)
    with pytest.raises(BackfillError, match="game started"):
        freeze_predictions(tmp_path / TARGET.isoformat(), run_id="synthetic-live01",
            operating_date=TARGET, repository_sha=SHA, rows=rows, sources=sources,
            exclusions=exclusions, stage_counts=counts, clock=lambda: NOW + clock_delta)


@pytest.mark.parametrize("state", ["Live", "Final", "Suspended", "unknown"])
def test_nonpregame_state_never_qualifies(state):
    row = target_game()
    row["status"]["abstractGameState"] = state
    assert not live01.unstarted(row["status"], start=NOW + timedelta(hours=1), observed=NOW)


def test_prediction_future_clock_is_rejected_even_if_rehashed(tmp_path):
    (rows, _, _, _), _, _, _ = cohort(tmp_path)
    row = rows[0]
    row["generated_at"] = (NOW + timedelta(hours=1)).isoformat()
    row["prediction_payload_sha256"] = row_hash(row)
    with pytest.raises(BackfillError, match="prospective"):
        validate_row(row)


def test_market_input_field_cannot_be_added_to_model_payload(tmp_path):
    (rows, _, _, _), _, _, _ = cohort(tmp_path)
    row = rows[0]
    row["sportsbook"] = "Forbidden"
    row["prediction_payload_sha256"] = row_hash(row)
    with pytest.raises(BackfillError, match="fields"):
        validate_row(row)


def test_equal_freeze_market_clock_prevents_provider_call(tmp_path):
    root, _ = frozen(tmp_path)
    fake = MarketProvider(root)
    with pytest.raises(BackfillError, match="earlier"):
        capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake,
                       clock=lambda: NOW + timedelta(seconds=2))
    assert fake.calls == []


def test_live_execution_rejects_feature_branch(tmp_path, monkeypatch):
    answers = iter([SHA, "feat/fixture"])
    monkeypatch.setattr(live01.subprocess, "check_output", lambda *a, **k: next(answers))
    with pytest.raises(BackfillError, match="canonical main"):
        live01.canonical_main(tmp_path)


def test_concurrent_same_date_publication_has_one_winner(tmp_path):
    (original, sources, exclusions, counts), _, _, _ = cohort(tmp_path)
    def publish(run_id):
        rows = deepcopy(original)
        for row in rows:
            row["prediction_run_id"] = run_id
            row["prediction_payload_sha256"] = row_hash(row)
        try:
            root = freeze_predictions(tmp_path / TARGET.isoformat(), run_id=run_id,
                operating_date=TARGET, repository_sha=SHA, rows=rows, sources=sources,
                exclusions=exclusions, stage_counts=counts, clock=lambda: NOW + timedelta(seconds=2))
            return verify_freeze(root)[0]["prediction_run_id"]
        except BackfillError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ["one", "two"]))
    assert sum(value is not None for value in results) == 1


def test_complete_pipeline_freezes_before_unavailable_market(tmp_path, monkeypatch):
    store, historical = history(tmp_path / "data/mlb")
    monkeypatch.setattr(live01, "canonical_main", lambda repository: SHA)
    monkeypatch.setattr(live01, "historical_inventory",
                        lambda root, backfill_id: historical_inventory(root, "historical"))
    current, prior = target_game(), game(823101, "2026-09-25")
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(current),
        "catchup-schedule": schedule(prior), "catchup-feed-823101": full_feed(prior),
        "pregame-feed-900001": full_feed(current, pregame=True)})
    result = live01.execute_live01(tmp_path, run_id="complete-fixture", provider=provider,
                                    clock=lambda: NOW + timedelta(seconds=1))
    assert result["frozen_prediction_rows"] == 2 and result["prediction_freeze_verified"]
    assert result["market"]["odds_provider_calls"] == 0
    assert result["prior_date_coverage_complete"] and result["coverage_missing_game_count"] == 0
    assert result["target_day_hits_qualification"] == "PASS"
    assert result["market"]["frozen_rows_without_market"] == 2
    assert len(verify_freeze(Path(result["freeze_directory"]))[1]) == 2
    assert not any("season" in request for request in provider.calls)


def test_selected_date_does_not_advance_to_seek_lineups(tmp_path, monkeypatch):
    store, historical = history(tmp_path / "data/mlb")
    monkeypatch.setattr(live01, "canonical_main", lambda repository: SHA)
    monkeypatch.setattr(live01, "historical_inventory",
                        lambda root, backfill_id: historical_inventory(root, "historical"))
    current, prior = target_game(gameDate="2026-10-01T23:00:00Z"), game(823101, "2026-09-25")
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(current),
        "catchup-schedule": schedule(prior), "catchup-feed-823101": full_feed(prior)})
    result = live01.execute_live01(tmp_path, run_id="lineup-fixture", provider=provider,
                                    clock=lambda: NOW + timedelta(seconds=1))
    assert result["status"] == "YELLOW"
    assert result["target_operating_date"] == "2026-10-01" and not result["target_date_advanced"]
    assert result["target_day_hits_qualification"] == "LINEUPS_NOT_YET_AVAILABLE"
    assert result["frozen_prediction_rows"] == result["market"]["odds_provider_calls"] == 0
    assert result["target_game_feeds_captured"] == 0
    assert not any("2026-10-02" in request for request in provider.calls)
    assert verify_freeze(Path(result["freeze_directory"]))[1] == []


def test_existing_catchup_conflict_is_detected_before_next_feed(tmp_path, monkeypatch):
    store, historical = history(tmp_path)
    caught, stats, provider, root = catchup(tmp_path, store)
    original = store.read
    def conflict(role, game_id, player_id=None):
        fact = original(role, game_id, player_id)
        if (role, game_id, player_id) == ("BATTER", "823101", "700001"):
            return replace(fact, hits=0)
        return fact
    monkeypatch.setattr(store, "read", conflict)
    calls = list(provider.calls)
    with pytest.raises(FactLedgerConflict):
        catch_up(root, store, target=TARGET, provider=provider)
    assert provider.calls == calls
    with pytest.raises(FactLedgerConflict):
        catch_up(root, store, target=TARGET, provider=provider)
    assert provider.calls == calls


def test_corrupt_market_evidence_fails_closed_after_freeze(tmp_path):
    root, _ = frozen(tmp_path)
    class Corrupt:
        calls = 0
        def send(self, request, **kwargs):
            self.calls += 1
            return OddsAPIHTTPResponse(200, (("x-requests-last", "0"),), b"{broken")
    fake = Corrupt()
    with pytest.raises(BackfillError, match="integrity"):
        capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake,
                       clock=lambda: NOW + timedelta(seconds=3))
    assert fake.calls == 1
    assert not (root / "market/observations.json").exists()
    assert len(verify_freeze(root)[1]) == 2


@pytest.mark.parametrize("failure", ["stage", "publish"])
def test_market_evidence_persistence_failure_cannot_publish_quotes(tmp_path, monkeypatch, failure):
    from courtvision.sports.mlb.providers import the_odds_api_live as odds_live
    root, _ = frozen(tmp_path)
    before = (root / "predictions.csv").read_bytes()
    fake = MarketProvider(root)
    original = odds_live.execute_ingestion
    observed = []
    def inspect_result(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.market_batches  # Valid normalized quotes exist in memory.
        observed.append(result.transport_failures)
        return result
    def fail_publish(*args, **kwargs):
        raise OSError("synthetic durable publication failure")
    if failure == "stage":
        stage = odds_live.stage_ingestion_exchange
        def fail_event_stage(claim, exchange):
            if len(fake.calls) == 2:
                raise OSError("synthetic event response staging failure")
            return stage(claim, exchange)
        monkeypatch.setattr(odds_live, "stage_ingestion_exchange", fail_event_stage)
    else:
        monkeypatch.setattr(odds_live, "write_ingestion_evidence", fail_publish)
    monkeypatch.setattr("courtvision.sports.mlb.live01_market.execute_ingestion", inspect_result)
    with pytest.raises(BackfillError, match="integrity"):
        capture_market(root, config=odds_config(), api_key="synthetic-key", transport=fake,
                       clock=lambda: NOW + timedelta(seconds=3))
    assert fake.calls == ["discovery", "event_odds"] and len(observed) == 1
    assert not (root / "market/observations.json").exists()
    assert not (root / "market/research-board.csv").exists()
    assert (root / "predictions.csv").read_bytes() == before
    assert len(verify_freeze(root)[1]) == 2


def test_credentials_are_read_only_after_durable_prediction_gate(tmp_path, monkeypatch):
    store, historical = history(tmp_path / "data/mlb")
    monkeypatch.setattr(live01, "canonical_main", lambda repository: SHA)
    monkeypatch.setattr(live01, "historical_inventory",
                        lambda root, backfill_id: historical_inventory(root, "historical"))
    current, prior = target_game(), game(823101, "2026-09-25")
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(current),
        "catchup-schedule": schedule(prior), "catchup-feed-823101": full_feed(prior),
        "pregame-feed-900001": full_feed(current, pregame=True)})
    from dataclasses import asdict
    config = asdict(odds_config())
    config["operating_date"] = TARGET.isoformat()
    config_path = tmp_path / "synthetic-config.json"
    config_path.write_text(json.dumps(config))
    observed = []
    def key_after_freeze(path):
        root = tmp_path / "data/mlb/prospective/hits/2026-10-01/credential-fixture"
        manifest, rows = verify_freeze(root)
        assert len(rows) == 2
        observed.append("credential-after-freeze")
        return None
    monkeypatch.setattr(live01, "configured_odds_key", key_after_freeze)
    result = live01.execute_live01(tmp_path, run_id="credential-fixture", provider=provider,
        clock=lambda: NOW + timedelta(seconds=1), odds_config_path=config_path)
    assert observed == ["credential-after-freeze"]
    assert result["market"]["odds_provider_calls"] == 0
    assert result["market"]["odds_declared_credit_budget"] == 1


@pytest.mark.parametrize("contents,expected", [
    ("THE_ODDS_API_KEY=synthetic-secret\n", "synthetic-secret"),
    ('THE_ODDS_API_KEY="synthetic-secret"\n', "synthetic-secret"),
    ("THE_ODDS_API_KEY=one\nTHE_ODDS_API_KEY=two\n", None),
    ("THE_ODDS_API_KEY=\n", None), ("UNRELATED=unused\n", None)])
def test_explicit_credential_file_has_no_fallback_or_duplicate_resolution(tmp_path, monkeypatch, contents, expected):
    monkeypatch.setenv("THE_ODDS_API_KEY", "must-not-be-fallback")
    path = tmp_path / "synthetic.env"
    path.write_text(contents)
    assert live01.configured_odds_key(path) == expected
    assert live01.configured_odds_key(tmp_path / "missing.env") is None


def test_concrete_permit_transport_keeps_freeze_gate_and_event_scope(tmp_path, monkeypatch):
    import requests
    from test_mlb_the_odds_api_permit import MockResponse
    root, _ = frozen(tmp_path)
    calls = []
    event = {"id": "fixture-event", "sport_key": "baseball_mlb",
        "home_team": "Fixture Home", "away_team": "Fixture Away",
        "commence_time": "2026-10-01T21:00:00Z", "bookmakers": []}
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def mount(self, prefix, adapter):
            assert adapter.max_retries.total == 0
        def get(self, url, **kwargs):
            assert verify_freeze(root)[0]["prediction_row_hash_verification"] == "PASS"
            assert self.trust_env is False and kwargs["allow_redirects"] is False
            calls.append(url)
            discovery = url.endswith("/events")
            payload = [event, {**event, "id": "unrelated", "home_team": "Other Home"}] if discovery else event
            return MockResponse(json.dumps(payload).encode(), last=0 if discovery else 1)
    monkeypatch.setattr(requests, "Session", Session)
    result = capture_market(root, config=odds_config(), api_key="synthetic-never-persist",
                            clock=lambda: NOW + timedelta(seconds=3))
    assert len(calls) == 2 and calls[1].endswith("/events/fixture-event/odds")
    assert result["status"] == "COMPLETE" and result["odds_provider_credits_used"] == 1
    assert result["transport_failures"] == []
    assert all(b"synthetic-never-persist" not in p.read_bytes() for p in root.rglob("*") if p.is_file())
