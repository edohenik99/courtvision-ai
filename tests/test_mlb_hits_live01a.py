"""Offline LIVE-01A readiness, administrative exclusion, and immutable resumes."""
from copy import deepcopy
from datetime import timedelta
import socket

import pytest

from courtvision.sports.mlb import live01, live01_evidence as module
from courtvision.sports.mlb.data.prospective_context_acquisition import ProviderResponse
from courtvision.sports.mlb.fact_backfill_evidence import (
    BackfillError, EvidenceJournal, publish_document, read_document,
)
from courtvision.sports.mlb.fact_ledger import MLBFactStore, FactLedgerConflict
from courtvision.sports.mlb.game_finality import classify_game_finality
from test_mlb_fact_backfill import game, schedule
from test_mlb_hits_live01 import NOW, TARGET, SHA, BaseballProvider, full_feed, target_game, history


# Exact status objects in failed catchup/raw/000001/body.bin, SHA-256
# 7f755b038696c562d82c7b404fa7cc95755dbde6f9454fc8660bd3c3c9c99d2f.
CANCELLED = {"abstractGameState": "Final", "codedGameState": "C",
             "detailedState": "Cancelled", "statusCode": "CR", "startTimeTBD": False,
             "reason": "Rain", "abstractGameCode": "F"}
IN_PROGRESS = {"abstractGameState": "Live", "codedGameState": "I",
               "detailedState": "In Progress", "statusCode": "I",
               "startTimeTBD": False, "abstractGameCode": "L"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("LIVE-01A must remain offline")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr("courtvision.sports.mlb.fact_backfill_evidence.utc_now",
                        lambda: NOW - timedelta(seconds=1))


def cancelled():
    row = game(823490, "2026-09-27")
    row.update(status=deepcopy(CANCELLED), gameDate="2026-09-27T17:05:00Z",
               gameGuid="60af2974-4c0b-4e44-a0d6-89de17c8ec3c", season="2026")
    row["teams"] = {"away": {"team": {"id": 110, "name": "Baltimore Orioles"}},
                    "home": {"team": {"id": 147, "name": "New York Yankees"}}}
    row["venue"] = {"id": 3313, "name": "Yankee Stadium"}
    return row


def pending(status=None):
    row = game(849844, "2026-09-30")
    row["status"] = deepcopy(IN_PROGRESS if status is None else status)
    return row


class RevisionProvider(BaseballProvider):
    def fetch(self, request):
        response = super().fetch(request)
        # A synthetic clock explicitly advances with each observation.
        captured = NOW + timedelta(seconds=len(self.calls))
        return ProviderResponse(response.body, 200, {}, captured, captured)


def inventory(tmp_path, *rows):
    journal = EvidenceJournal(tmp_path / "raw", 10)
    provider = BaseballProvider({"catchup-schedule": schedule(*rows)})
    record = journal.capture(module.schedule_request(module.CATCHUP_START,
        TARGET - timedelta(days=1), "catchup-schedule"), provider)
    value = module.inventory_from_captures(journal, [record], module.CATCHUP_START,
                                          TARGET - timedelta(days=1))
    return value, journal


def test_exact_cancelled_status_excluded_without_any_game_or_player_facts(tmp_path):
    store = MLBFactStore(tmp_path / "facts")
    provider = BaseballProvider({"catchup-schedule": schedule(cancelled())})
    coverage, stats = module.catch_up(tmp_path / "catchup", store, target=TARGET, provider=provider)
    assert not classify_game_finality(CANCELLED).is_final
    assert classify_game_finality(CANCELLED).canonical_state == "CONFLICT"
    assert provider.calls == ["catchup-schedule"]
    assert coverage["complete"] and coverage["games"] == coverage["expected_records"] == []
    assert coverage["players"] == {} and coverage["factual_final_game_pks"] == []
    assert coverage["administrative_no_participation_game_pks"] == ["823490"]
    exclusion = coverage["administrative_exclusions"][0]
    assert exclusion["status"] == CANCELLED and not exclusion["facts_required"]
    assert exclusion["participation_records_expected"] == 0
    assert len(exclusion["source_response_hash"]) == len(exclusion["reconciliation_hash"]) == 64
    assert [stats[f"new_{role}_facts"] for role in ("game", "batter", "pitcher")] == [0, 0, 0]
    assert not list(store.root.rglob("*.json"))
    assert read_document(tmp_path / "catchup/coverage/000001.json") == coverage


@pytest.mark.parametrize("change", [
    {"detailedState": "Suspended"}, {"detailedState": "Postponed"},
    {"detailedState": "Unknown"}, {"detailedState": "Canceled"},
    {"detailedState": "Cancelled Later"}, {"codedGameState": "D"},
    {"codedGameState": "F"}, {"statusCode": "C"}, {"abstractGameCode": "L"},
    {"reason": "Unknown"}, {"startTimeTBD": 0}, {"startTimeTBD": None},
    {"unexpectedStatus": "Final"}, {"abstractGameState": None},
])
def test_unsupported_cancellation_and_final_conflicts_remain_unresolved(tmp_path, change):
    row = cancelled()
    row["status"].update(change)
    inv, _ = inventory(tmp_path, row)
    assert module.schedule_disposition(inv["games"][0]) == "UNRESOLVED"
    provider = BaseballProvider({"catchup-schedule": schedule(row)})
    with pytest.raises(BackfillError, match="unresolved"):
        module.catch_up(tmp_path / "catchup", MLBFactStore(tmp_path / "facts"),
                        target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule"]
    coverage = read_document(tmp_path / "catchup/coverage/000001.json")
    assert coverage["unresolved_game_pks"] == ["823490"] and not coverage["complete"]


@pytest.mark.parametrize("field", list(CANCELLED))
def test_missing_cancellation_field_fails_closed(tmp_path, field):
    row = cancelled()
    del row["status"][field]
    inv, _ = inventory(tmp_path, row)
    assert module.schedule_disposition(inv["games"][0]) == "UNRESOLVED"


def test_cancellation_with_score_does_not_establish_no_participation(tmp_path):
    row = cancelled()
    row["teams"]["away"]["score"] = 0
    inv, _ = inventory(tmp_path, row)
    assert module.schedule_disposition(inv["games"][0]) == "UNRESOLVED"


READY_STATES = [IN_PROGRESS,
    {"abstractGameState": "Preview", "detailedState": "Scheduled",
     "codedGameState": "S", "statusCode": "S", "abstractGameCode": "P"},
    {"abstractGameState": "Live", "detailedState": "Suspended",
     "codedGameState": "I", "statusCode": "I", "abstractGameCode": "L"},
    {"abstractGameState": "Preview", "detailedState": "Postponed",
     "codedGameState": "D", "statusCode": "DR", "abstractGameCode": "P"}]


@pytest.mark.parametrize("status", READY_STATES)
def test_pending_prior_date_stops_before_fact_feed_or_publication(tmp_path, status):
    provider = BaseballProvider({"catchup-schedule": schedule(
        game(823101, "2026-09-25"), cancelled(), pending(status))})
    coverage, stats = module.catch_up(tmp_path / "catchup", MLBFactStore(tmp_path / "facts"),
                                     target=TARGET, provider=provider)
    assert not coverage["complete"]
    assert coverage["pending_prior_date_game_pks"] == ["849844"]
    assert coverage["factual_final_game_pks"] == coverage["missing_final_game_pks"] == ["823101"]
    assert coverage["unresolved_game_pks"] == []
    assert provider.calls == ["catchup-schedule"]
    assert coverage["games"] == coverage["expected_records"] == []
    assert stats["new_game_feeds"] == stats["new_game_facts"] == 0


@pytest.mark.parametrize("change", [
    {"detailedState": "Unknown"}, {"statusCode": "BAD"}, {"abstractGameState": "Unexpected"},
    {"abstractGameCode": "F"}, {"codedGameState": "S"}, {"codedGameState": "D"},
    {"statusCode": None}])
def test_nonfinal_witness_does_not_hide_unknown_companions(tmp_path, change):
    row = pending()
    row["status"].update(change)
    inv, _ = inventory(tmp_path, row)
    assert module.schedule_disposition(inv["games"][0]) == "UNRESOLVED"


@pytest.mark.parametrize("status", READY_STATES)
def test_execute_not_ready_is_yellow_and_never_predicts_freezes_or_calls_market(tmp_path, monkeypatch, status):
    monkeypatch.setattr(live01, "canonical_main", lambda _: SHA)
    monkeypatch.setattr(live01, "historical_inventory", lambda *args: {"complete": True})
    def denied(*args, **kwargs):
        raise AssertionError("pending history must stop before downstream work")
    for name in ("compose_coverage", "generate_cohort", "freeze_predictions", "capture_market",
                 "configured_odds_key"):
        monkeypatch.setattr(live01, name, denied)
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(target_game()),
                                "catchup-schedule": schedule(cancelled(), pending(status))})
    result = live01.execute_live01(tmp_path, run_id="pending", provider=provider, clock=lambda: NOW)
    assert result["status"] == "YELLOW"
    assert result["target_day_hits_qualification"] == result["reason"] == "PRIOR_DATE_COVERAGE_NOT_READY"
    assert result["frozen_prediction_rows"] == result["odds_provider_calls"] == 0
    assert not result["prediction_freeze_verified"]
    run = tmp_path / "data/mlb/prospective/hits/runs/pending"
    assert not (run / "failure.json").exists()
    assert read_document(run / "disposition.json") == result
    assert not list(tmp_path.rglob("predictions.csv"))


@pytest.mark.parametrize("failure", [None, "ledger", "score", "regression"])
def test_later_final_revision_reuses_valid_feed_and_preserves_all_bytes(tmp_path, monkeypatch, failure):
    store = MLBFactStore(tmp_path / "facts")
    root = tmp_path / "catchup"
    older = game(823101, "2026-09-25")
    unfinished = pending()
    finished = deepcopy(unfinished)
    finished["status"] = deepcopy(older["status"])
    provider = RevisionProvider({"catchup-schedule": schedule(older, unfinished, cancelled()),
        "seed-valid-feed": full_feed(older),
        "catchup-schedule-000003": schedule(older, finished, cancelled()),
        "catchup-feed-849844": full_feed(finished)})
    first, _ = module.catch_up(root, store, target=TARGET, provider=provider)
    assert not first["complete"]
    # Represent a valid feed/fact publication retained from an earlier bounded
    # acquisition. Bind it to the original single-observation inventory.
    journal = EvidenceJournal(root / "raw", 225)
    inv = read_document(root / "inventories/000001.json")
    from courtvision.sports.mlb.data.prospective_context_acquisition import EvidenceRequest
    from courtvision.sports.mlb.fact_backfill import facts_from_feed
    record = journal.capture(EvidenceRequest(request_id="seed-valid-feed", evidence_class="stable_history",
        source_name="fixture", provider="mlb_statsapi", event_id="823101",
        url="https://statsapi.mlb.com/api/v1.1/game/823101/feed/live"), provider)
    row = next(r for r in inv["games"] if r["gamePk"] == "823101")
    for fact in facts_from_feed(row, journal.payload(record), journal.records()[0], record):
        store.publish(fact)
    before = {p: p.read_bytes() for base in (root, store.root) for p in base.rglob("*") if p.is_file()}
    if failure == "ledger":
        def conflict(*args, **kwargs):
            raise FactLedgerConflict("preserved ledger conflict")
        monkeypatch.setattr(store, "read", conflict)
        for _ in range(2):
            with pytest.raises(FactLedgerConflict):
                module.catch_up(root, store, target=TARGET, provider=provider)
        assert provider.calls == ["catchup-schedule", "seed-valid-feed"]
        assert (root / "conflict.json").exists()
        assert all(p.read_bytes() == raw for p, raw in before.items())
        return
    if failure in {"score", "regression"}:
        changed = deepcopy(older)
        if failure == "score":
            changed["teams"]["away"]["score"] = 99
        else:
            changed["status"] = deepcopy(IN_PROGRESS)
        provider.payloads["catchup-schedule-000003"] = schedule(changed, finished, cancelled())
        for _ in range(2):
            with pytest.raises(BackfillError, match="score mismatch|conflicts with schedule disposition"):
                module.catch_up(root, store, target=TARGET, provider=provider)
        assert provider.calls == ["catchup-schedule", "seed-valid-feed", "catchup-schedule-000003"]
        assert all(p.read_bytes() == raw for p, raw in before.items())
        return
    complete, stats = module.catch_up(root, store, target=TARGET, provider=provider)
    assert complete["complete"] and complete["games"] == ["823101", "849844"]
    assert complete["pending_prior_date_game_pks"] == complete["unresolved_game_pks"] == []
    assert complete["administrative_no_participation_game_pks"] == ["823490"]
    assert len(complete["expected_records"]) == 10
    assert stats["new_game_feeds"] == stats["existing_identical_games"] == 1
    assert stats["valid_preserved_feeds_refetched"] == 0
    assert provider.calls == ["catchup-schedule", "seed-valid-feed", "catchup-schedule-000003",
                              "catchup-feed-849844"]
    assert all(p.read_bytes() == raw for p, raw in before.items())
    revised = read_document(root / "inventories/000003.json")
    resolution = next(r for r in revised["games"] if r["gamePk"] == "849844")["reconciliation"]
    assert resolution["observation_count"] == 2 and resolution["revision_count"] == 1
    assert resolution["selected_canonical_state"]["is_final"]
    assert resolution["identity"] == next(r for r in inv["games"] if r["gamePk"] == "849844")["reconciliation"]["identity"]
    assert [r["claim"]["sequence"] for r in journal.records()] == [1, 2, 3, 4]
    again, repeat = module.catch_up(root, store, target=TARGET, provider=provider)
    assert again == complete and repeat["new_game_feeds"] == repeat["schedule_refreshes"] == 0
    assert len(provider.calls) == 4
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("which", ["pending", "cancelled"])
def test_refresh_identity_conflict_never_fetches_or_materializes(tmp_path, which):
    first_rows = [pending(), cancelled()]
    second_rows = deepcopy(first_rows)
    second_rows[0 if which == "pending" else 1]["teams"]["home"]["team"]["id"] = 999
    provider = RevisionProvider({"catchup-schedule": schedule(*first_rows),
                                "catchup-schedule-000002": schedule(*second_rows)})
    store = MLBFactStore(tmp_path / "facts")
    root = tmp_path / "catchup"
    module.catch_up(root, store, target=TARGET, provider=provider)
    with pytest.raises(BackfillError, match="IDENTITY_CONFLICT"):
        module.catch_up(root, store, target=TARGET, provider=provider)
    assert len(provider.calls) == 2 and not list(store.root.rglob("*.json"))
    with pytest.raises(BackfillError, match="IDENTITY_CONFLICT"):
        module.catch_up(root, store, target=TARGET, provider=provider)
    assert len(provider.calls) == 2


def test_unresolved_overrides_pending_without_refresh(tmp_path):
    broken = cancelled()
    broken["status"]["detailedState"] = "Suspended"
    provider = RevisionProvider({"catchup-schedule": schedule(broken, pending())})
    root, store = tmp_path / "catchup", MLBFactStore(tmp_path / "facts")
    for _ in range(2):
        with pytest.raises(BackfillError, match="unresolved"):
            module.catch_up(root, store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule"]


def test_offline_replay_does_not_refresh_or_modify_old_inventory(tmp_path):
    inv, journal = inventory(tmp_path / "catchup", cancelled(), pending())
    root = journal.root.parent
    publish_document(root / "inventory.json", inv)  # Legacy failed-run artifact.
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result, stats = module.catch_up(root, MLBFactStore(tmp_path / "facts"), target=TARGET, provider=None)
    assert not result["complete"] and result["pending_prior_date_game_pks"] == ["849844"]
    assert stats["schedule_refreshes"] == stats["new_game_feeds"] == 0
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_schedule_final_cannot_complete_before_exact_participation_verification(tmp_path):
    unfinished = pending()
    finished = deepcopy(unfinished)
    finished["status"] = game()["status"]
    broken_feed = full_feed(finished)
    broken_feed["liveData"]["boxscore"]["teams"]["away"]["batters"] = []
    provider = RevisionProvider({"catchup-schedule": schedule(unfinished),
        "catchup-schedule-000002": schedule(finished), "catchup-feed-849844": broken_feed})
    root, store = tmp_path / "catchup", MLBFactStore(tmp_path / "facts")
    module.catch_up(root, store, target=TARGET, provider=provider)
    with pytest.raises(BackfillError, match="participation inventory"):
        module.catch_up(root, store, target=TARGET, provider=provider)
    assert not (root / "coverage/000002.json").exists()
    assert not list(store.root.rglob("*.json"))
    with pytest.raises(BackfillError, match="participation inventory"):
        module.catch_up(root, store, target=TARGET, provider=provider)
    assert len(provider.calls) == 3


def test_composed_coverage_retains_administrative_exclusions(tmp_path):
    store, historical = history(tmp_path)
    provider = BaseballProvider({"catchup-schedule": schedule(cancelled())})
    catchup, _ = module.catch_up(tmp_path / "catchup", store, target=TARGET, provider=provider)
    result = module.compose_coverage(historical, catchup, target=TARGET, store=store)
    assert result["complete"]
    assert result["administrative_no_participation_game_pks"] == ["823490"]
    assert "823490" not in result["games"]
    assert result["administrative_exclusions"] == catchup["administrative_exclusions"]


def test_historical_pending_readiness_is_yellow_before_catchup(tmp_path, monkeypatch):
    monkeypatch.setattr(live01, "canonical_main", lambda _: SHA)
    monkeypatch.setattr(live01, "historical_inventory", lambda *args: {
        "complete": False, "pending_prior_date_game_pks": ["823100"], "unresolved_game_pks": []})
    def denied(*args, **kwargs):
        raise AssertionError("historical pending gate must stop subsequent acquisition")
    monkeypatch.setattr(live01, "catch_up", denied)
    provider = BaseballProvider({"target-schedule-2026-10-01": schedule(target_game())})
    result = live01.execute_live01(tmp_path, run_id="historical-pending", provider=provider, clock=lambda: NOW)
    assert result["status"] == "YELLOW" and result["reason"] == "PRIOR_DATE_COVERAGE_NOT_READY"
    assert provider.calls == ["target-schedule-2026-10-01"]
    assert result["frozen_prediction_rows"] == result["odds_provider_calls"] == 0


@pytest.mark.parametrize("pending_ids,unresolved_ids", [([], []), (["849844"], ["823490"])])
def test_unqualified_incomplete_coverage_cannot_be_reported_yellow(pending_ids, unresolved_ids):
    with pytest.raises(BackfillError, match="not a qualified readiness state"):
        live01.prior_date_not_ready({}, {"pending_prior_date_game_pks": pending_ids,
                                        "unresolved_game_pks": unresolved_ids})


def test_repeated_pending_refreshes_append_without_overwriting(tmp_path):
    provider = RevisionProvider({"catchup-schedule": schedule(pending()),
        "catchup-schedule-000002": schedule(pending()), "catchup-schedule-000003": schedule(pending())})
    root, store = tmp_path / "catchup", MLBFactStore(tmp_path / "facts")
    before = {}
    for count in range(1, 4):
        coverage, stats = module.catch_up(root, store, target=TARGET, provider=provider)
        assert not coverage["complete"] and stats["new_game_feeds"] == 0
        assert len(provider.calls) == count
        assert all(p.read_bytes() == raw for p, raw in before.items())
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert len(list((root / "inventories").glob("*.json"))) == 3
    assert len(list((root / "coverage").glob("*.json"))) == 3


@pytest.mark.parametrize("damage", ["raw", "window"])
def test_damaged_or_wrong_window_schedule_never_refreshes(tmp_path, damage):
    provider = BaseballProvider({"catchup-schedule": schedule(pending())})
    root, store = tmp_path / "catchup", MLBFactStore(tmp_path / "facts")
    module.catch_up(root, store, target=TARGET, provider=provider)
    if damage == "raw":
        (root / "raw/000001/body.bin").write_bytes(b"{}")
    else:
        # A validly enveloped but incompatible journal still cannot establish
        # the declared window. Test the builder without damaging any receipt.
        journal = EvidenceJournal(root / "raw", 225)
        records = journal.records()
        with pytest.raises(BackfillError, match="window differs"):
            module.inventory_from_captures(journal, records, module.CATCHUP_START,
                                           TARGET - timedelta(days=2))
        return
    with pytest.raises(BackfillError, match="raw evidence hash mismatch"):
        module.catch_up(root, store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule"]
