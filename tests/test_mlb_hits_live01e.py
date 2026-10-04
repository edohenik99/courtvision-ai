"""Offline qualified-prefix custody, daily delta, and sovereign composition."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb import fact_backfill_evidence as evidence
from courtvision.sports.mlb import live01_evidence as module
from courtvision.sports.mlb.data.prospective_context_acquisition import ProviderResponse
from courtvision.sports.mlb.fact_backfill_evidence import BackfillError, EvidenceJournal, read_document, publish_document
from courtvision.sports.mlb.fact_ledger import FactLedgerConflict, MLBFactStore
from courtvision.sports.mlb.hits_season_ledger import load_ledger_season
from test_mlb_fact_backfill import game, schedule
from test_mlb_hits_live01 import BaseballProvider, NOW, full_feed, history
from test_mlb_hits_live01a import CANCELLED, IN_PROGRESS

TARGET = date(2026, 10, 4)
CAPTURED = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("LIVE-01E cannot access a real provider")
    socket_names = ("create_connection", "getaddrinfo")
    for name in socket_names:
        monkeypatch.setattr(socket, name, denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(evidence, "utc_now", lambda: NOW - timedelta(seconds=1))


class DeltaProvider(BaseballProvider):
    def __init__(self, payloads):
        super().__init__(payloads)
        self.requests = []

    def fetch(self, request):
        self.requests.append(request)
        response = super().fetch(request)
        return ProviderResponse(response.body, 200, {}, CAPTURED, CAPTURED)


def provider_for(*rows):
    return DeltaProvider({"catchup-schedule": schedule(*rows),
        **{f'catchup-feed-{r["gamePk"]}': full_feed(r) for r in rows
           if module.classify_game_finality(r["status"]).is_final}})


def seed(root, store, *, through=date(2026, 10, 2), rows=None):
    rows = [game(823101, "2026-09-25")] if rows is None else rows
    # Independent cold-start journals can legitimately qualify the same window.
    publish_document(root / "plan.json", {"start": module.CATCHUP_START.isoformat(),
        "through": through.isoformat(), "max_requests": 225, "fact_root": str(store.root)})
    coverage, stats = module.catch_up(root, store, target=through + timedelta(days=1),
                                     provider=provider_for(*rows))
    return coverage


def setup_prefix(tmp_path):
    store = MLBFactStore(tmp_path / "facts")
    root = tmp_path / "catchup"
    coverage = seed(root / "2026-10-03", store)
    return store, root, coverage


def snapshot(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def selected(root, store):
    return module.load_latest_qualified_catchup_prefix(root,
        required_before=TARGET - timedelta(days=1), store=store)


def test_daily_delta_owns_only_new_sources_and_replays_hash_bound_union(tmp_path):
    store, root, original = setup_prefix(tmp_path)
    before = snapshot(root / "2026-10-03")
    old_facts = snapshot(store.root)
    delta_row = game(849829, "2026-10-03")
    delta_row["gameType"] = "D"
    provider = provider_for(delta_row)
    new_root = root / TARGET.isoformat()
    coverage, stats = module.catch_up(new_root, store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule", "catchup-feed-849829"]
    assert "startDate=2026-10-03&endDate=2026-10-03" in provider.requests[0].url
    assert coverage["start"] == "2026-09-25" and coverage["through"] == "2026-10-03"
    assert coverage["complete"] and coverage["games"] == ["823101", "849829"]
    assert coverage["observed_game_types"] == ["D", "R"]
    assert coverage["prefix"]["coverage_sha256"] == evidence.digest(original)
    assert coverage["delta"]["start"] == coverage["delta"]["through"] == "2026-10-03"
    assert stats["new_game_feeds"] == 1 and stats["valid_preserved_feeds_refetched"] == 0
    assert len(EvidenceJournal(new_root / "raw", 225).records()) == 2
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert all(p.read_bytes() == raw for p, raw in old_facts.items())
    new_raw = [p.read_bytes() for p in (new_root / "raw").rglob("body.bin")]
    old_raw = [raw for p, raw in before.items() if p.name == "body.bin"]
    assert not any(raw in old_raw for raw in new_raw)
    assert read_document(new_root / "delta-coverage/000001.json")["games"] == ["849829"]
    assert read_document(new_root / "coverage/000001.json") == coverage
    replay, repeat = module.catch_up(new_root, store, target=TARGET, provider=None)
    assert replay == coverage and repeat["new_game_feeds"] == 0
    # A composed artifact is itself eligible on the following day, with recursive custody.
    next_plan, prefix = module.plan_catch_up(root / "2026-10-05", store, target=date(2026, 10, 5))
    assert prefix["coverage"] == coverage and next_plan["start"] == "2026-10-04"


def test_real_shape_read_only_october4_plan_does_not_create_a_journal(tmp_path):
    store, root, coverage = setup_prefix(tmp_path)
    before = snapshot(tmp_path)
    plan, prefix = module.plan_catch_up(root / TARGET.isoformat(), store, target=TARGET)
    assert prefix["coverage"] == coverage
    assert plan["start"] == plan["through"] == "2026-10-03"
    assert prefix["reference"]["through"] == "2026-10-02"
    assert snapshot(tmp_path) == before and not (root / TARGET.isoformat()).exists()


@pytest.mark.parametrize("damage", ["artifact", "source", "fact", "missing_fact", "persisted_conflict", "inventory"])
def test_damaged_prefix_stops_before_any_provider_request_or_new_journal(tmp_path, monkeypatch, damage):
    store, root, _ = setup_prefix(tmp_path)
    original_root = root / "2026-10-03"
    if damage == "artifact":
        (original_root / "coverage/000001.json").write_bytes(b"{}")
    elif damage == "source":
        original_read = Path.read_bytes
        missing = original_root / "raw/000002/body.bin"
        def missing_source(path):
            if path == missing:
                raise FileNotFoundError("synthetic missing source evidence")
            return original_read(path)
        monkeypatch.setattr(Path, "read_bytes", missing_source)
    elif damage in {"fact", "missing_fact"}:
        original_read = store.read
        def drift(role, game_id, player_id=None):
            if damage == "missing_fact" and role == "BATTER" and player_id == "700001":
                raise FileNotFoundError("synthetic missing prefix fact")
            fact = original_read(role, game_id, player_id)
            return replace(fact, hits=0) if role == "BATTER" and player_id == "700001" else fact
        monkeypatch.setattr(store, "read", drift)
    elif damage == "inventory":
        (original_root / "inventories/000001.json").write_bytes(b"{}")
    else:
        publish_document(original_root / "conflict.json", {"state": "FACT_CONFLICT"})
    provider = provider_for(game(849829, "2026-10-03"))
    with pytest.raises((BackfillError, FactLedgerConflict, FileNotFoundError)):
        module.catch_up(root / TARGET.isoformat(), store, target=TARGET, provider=provider)
    assert provider.calls == [] and not (root / TARGET.isoformat()).exists()


def test_all_prefix_expected_hashes_are_reverified(tmp_path, monkeypatch):
    store, root, coverage = setup_prefix(tmp_path)
    original_read = store.read
    identities = []
    def counted(role, game_id, player_id=None):
        identities.append((role, game_id, player_id))
        return original_read(role, game_id, player_id)
    monkeypatch.setattr(store, "read", counted)
    assert selected(root, store)["coverage"] == coverage
    assert {(r["role"], r["gamePk"], r["player_id"]) for r in coverage["expected_records"]} <= set(identities)


@pytest.mark.parametrize("change", [
    {"complete": False}, {"pending_prior_date_game_pks": ["849844"]},
    {"unresolved_game_pks": ["849844"]}, {"missing_final_game_pks": ["849844"]},
    {"missing_expected_records": [{"role": "GAME"}]}, {"complete": 1},
])
def test_unqualified_candidate_is_ineligible(tmp_path, change):
    store = MLBFactStore(tmp_path / "facts")
    path = tmp_path / "catchup/old/coverage/000001.json"
    publish_document(path, {"schema_version": module.COVERAGE_SCHEMA, "start": "2026-09-25",
        "through": "2026-10-02", "complete": True, "pending_prior_date_game_pks": [],
        "unresolved_game_pks": [], "missing_final_game_pks": [], **change})
    assert selected(path.parents[2], store) is None


def test_different_qualified_prefixes_at_same_date_fail_closed(tmp_path):
    store, root = MLBFactStore(tmp_path / "facts"), tmp_path / "catchup"
    a = game(823490, "2026-09-27")
    a["status"] = deepcopy(CANCELLED)
    for side in ("away", "home"):
        a["teams"][side].pop("score")
    b = deepcopy(a)
    b["venue"]["name"] = "Different descriptive provider evidence"
    seed(root / "a", store, rows=[a])
    seed(root / "b", store, rows=[b])
    provider = provider_for()
    with pytest.raises(BackfillError, match="conflicting qualified prefixes"):
        module.catch_up(root / TARGET.isoformat(), store, target=TARGET, provider=provider)
    assert provider.calls == []


def test_greatest_verified_date_selected_independent_of_folder_order(tmp_path):
    store, root = MLBFactStore(tmp_path / "facts"), tmp_path / "catchup"
    seed(root / "z-older", store, through=date(2026, 9, 30), rows=[])
    latest = seed(root / "a-latest", store, rows=[])
    assert selected(root, store)["coverage"] == latest
    assert selected(root, store)["reference"]["coverage_path"] == "a-latest/coverage/000001.json"


def test_exact_complete_prior_day_coverage_is_reused_with_zero_requests(tmp_path):
    store, root, original = setup_prefix(tmp_path)
    before = snapshot(tmp_path)
    provider = provider_for()
    coverage, stats = module.catch_up(root / "same-window", store,
                                     target=date(2026, 10, 3), provider=provider)
    assert coverage == original and provider.calls == []
    assert stats["new_game_feeds"] == 0 and snapshot(tmp_path) == before


def test_absent_prefix_retains_full_cold_start_window(tmp_path):
    store, root = MLBFactStore(tmp_path / "facts"), tmp_path / "catchup/2026-10-04"
    provider = provider_for(game(823101, "2026-09-25"))
    coverage, _ = module.catch_up(root, store, target=TARGET, provider=provider)
    assert "startDate=2026-09-25&endDate=2026-10-03" in provider.requests[0].url
    assert coverage["start"] == "2026-09-25" and "prefix" not in coverage


@pytest.mark.parametrize("disposition", ["cancelled", "pending", "unresolved"])
def test_delta_dispositions_preserve_no_facts_yellow_and_red(tmp_path, disposition):
    store, root, _ = setup_prefix(tmp_path)
    row = game(849829, "2026-10-03")
    row["status"] = deepcopy(CANCELLED if disposition == "cancelled" else IN_PROGRESS)
    if disposition == "cancelled":
        for side in ("away", "home"):
            row["teams"][side].pop("score")
    elif disposition == "unresolved":
        row["status"]["statusCode"] = "BAD"
    provider = provider_for(row)
    before = snapshot(store.root)
    new_root = root / TARGET.isoformat()
    if disposition == "unresolved":
        with pytest.raises(BackfillError, match="unresolved"):
            module.catch_up(new_root, store, target=TARGET, provider=provider)
    else:
        coverage, stats = module.catch_up(new_root, store, target=TARGET, provider=provider)
        assert coverage["complete"] == (disposition == "cancelled")
        assert stats["new_game_feeds"] == stats["new_game_facts"] == 0
        if disposition == "cancelled":
            assert coverage["administrative_no_participation_game_pks"] == ["849829"]
        else:
            from courtvision.sports.mlb.live01 import prior_date_not_ready
            assert prior_date_not_ready({}, coverage, stats)["status"] == "YELLOW"
    assert provider.calls == ["catchup-schedule"] and snapshot(store.root) == before


def test_delta_final_requires_independent_feed_in_its_own_journal(tmp_path):
    store, root, _ = setup_prefix(tmp_path)
    new_root = root / TARGET.isoformat()
    plan, _ = module.plan_catch_up(new_root, store, target=TARGET)
    publish_document(new_root / "plan.json", plan)
    provider = provider_for(game(849829, "2026-10-03"))
    EvidenceJournal(new_root / "raw", 225).capture(
        module.schedule_request(date(2026, 10, 3), date(2026, 10, 3), "catchup-schedule"), provider)
    with pytest.raises(BackfillError, match="missing an expected final feed"):
        module.catch_up(new_root, store, target=TARGET, provider=None)


def test_existing_delta_fact_never_self_authenticates_or_refetches(tmp_path):
    store, root, _ = setup_prefix(tmp_path)
    row = game(849829, "2026-10-03")
    # A different independent acquisition owns these facts, not the new delta journal.
    seed(tmp_path / "other-acquisition", store, through=date(2026, 10, 3), rows=[row])
    provider = provider_for(row)
    with pytest.raises(BackfillError, match="preserved independent feed; never refetch"):
        module.catch_up(root / TARGET.isoformat(), store, target=TARGET, provider=provider)
    assert provider.calls == ["catchup-schedule"]


@pytest.mark.parametrize("overlap", ["different_hash", "same_hash", "administrative"])
def test_composition_rejects_duplicate_or_conflicting_logical_participation(tmp_path, overlap):
    store, root, original = setup_prefix(tmp_path)
    prefix = selected(root, store)
    delta = deepcopy(original)
    delta["start"] = delta["through"] = "2026-10-03"
    if overlap == "different_hash":
        delta["expected_records"][0]["factual_record_hash"] = "0" * 64
    elif overlap == "administrative":
        delta["games"] = delta["expected_records"] = delta["factual_final_game_pks"] = []
        delta["administrative_no_participation_game_pks"] = original["games"]
    with pytest.raises(FactLedgerConflict):
        module.compose_catchup_coverage(prefix, delta, store=store)


def test_failed_later_target_849828_cannot_contaminate_closed_prefix(tmp_path):
    store, root, original = setup_prefix(tmp_path)
    # The unqualified target capture remains byte-for-byte outside catchup custody.
    target_root = tmp_path / "runs/failed-target"
    target_root.mkdir(parents=True)
    (target_root / "body.bin").write_bytes(b'{"gamePk":849828,"unqualified_placeholder":true}')
    publish_document(target_root / "failure.json", {"status": "RED", "gamePk": "849828"})
    before = snapshot(target_root)
    prefix = selected(root, store)
    assert prefix["coverage"] == original and "849828" not in prefix["coverage"]["games"]
    assert not any("failed-target" in ref for ref in prefix["coverage"]["source_refs"])
    assert snapshot(target_root) == before


def test_historical_plus_composed_catchup_remains_sovereign(tmp_path):
    store, historical = history(tmp_path)
    root = tmp_path / "catchup"
    seed(root / "2026-10-03", store)
    catchup, _ = module.catch_up(root / TARGET.isoformat(), store, target=TARGET,
                                provider=provider_for(game(849829, "2026-10-03")))
    index = module.compose_coverage(historical, catchup, target=TARGET, store=store)
    coverage = module.batter_coverage(index, player_id="700001", game_id="900001",
                                     target=TARGET, observed_at=CAPTURED)
    loaded = load_ledger_season(store, coverage, player_name="Fixture Player 700001")
    assert coverage.coverage_through == date(2026, 10, 3) and coverage.complete
    assert len(coverage.expected_records) == 3
    assert f"cv-live01-catchup:sha256:{evidence.digest(catchup)}" in coverage.source_refs
    assert loaded is not None


def test_restart_keeps_original_prefix_binding_when_later_candidate_appears(tmp_path):
    store, root, _ = setup_prefix(tmp_path)
    new_root = root / TARGET.isoformat()
    plan, original_prefix = module.plan_catch_up(new_root, store, target=TARGET)
    publish_document(new_root / "plan.json", plan)
    seed(root / "another-complete-window", store, through=date(2026, 10, 3), rows=[])
    repeated, prefix = module.plan_catch_up(new_root, store, target=TARGET)
    assert repeated == plan and prefix == original_prefix


def test_rehashed_prefix_tampering_cannot_replace_independent_evidence(tmp_path):
    store, root, original = setup_prefix(tmp_path)
    path = root / "2026-10-03/coverage/000001.json"
    changed = deepcopy(original)
    changed["inventory_hash"] = "0" * 64
    # A valid new envelope cannot turn altered coverage into qualified source evidence.
    path.write_bytes(module.canonical_json({"payload": changed, "sha256": evidence.digest(changed)}) + b"\n")
    with pytest.raises(BackfillError, match="independently derived participation"):
        selected(root, store)


def test_bound_prefix_hash_drift_cannot_be_reselected_on_restart(tmp_path):
    store, root, _ = setup_prefix(tmp_path)
    new_root = root / TARGET.isoformat()
    plan, _ = module.plan_catch_up(new_root, store, target=TARGET)
    plan["prefix"]["artifact_sha256"] = "0" * 64
    publish_document(new_root / "plan.json", plan)
    provider = provider_for()
    with pytest.raises(BackfillError, match="hash/identity binding differs"):
        module.catch_up(new_root, store, target=TARGET, provider=provider)
    assert provider.calls == [] and not (new_root / "raw").exists()


def test_prefix_reference_cannot_escape_canonical_catchup_root(tmp_path):
    store, root, _ = setup_prefix(tmp_path)
    new_root = root / TARGET.isoformat()
    plan, _ = module.plan_catch_up(new_root, store, target=TARGET)
    plan["prefix"]["coverage_path"] = "../../outside/coverage/000001.json"
    publish_document(new_root / "plan.json", plan)
    with pytest.raises(BackfillError, match="escapes catch-up root"):
        module.plan_catch_up(new_root, store, target=TARGET)


def test_identical_tied_qualified_candidates_choose_stable_identity(tmp_path):
    store, root = MLBFactStore(tmp_path / "facts"), tmp_path / "catchup"
    seed(root / "z", store, rows=[])
    seed(root / "a", store, rows=[])
    assert selected(root, store)["reference"]["coverage_path"] == "a/coverage/000001.json"


def test_future_qualified_coverage_is_not_a_prior_date_prefix(tmp_path):
    store, root = MLBFactStore(tmp_path / "facts"), tmp_path / "catchup"
    older = seed(root / "old", store, rows=[])
    seed(root / "future", store, through=date(2026, 10, 4), rows=[])
    assert selected(root, store)["coverage"] == older


def test_conflicting_administrative_exclusions_cannot_compose(tmp_path):
    store, root, original = setup_prefix(tmp_path)
    delta = deepcopy(original)
    delta["start"] = delta["through"] = "2026-10-03"
    delta["games"] = delta["expected_records"] = delta["factual_final_game_pks"] = []
    delta["administrative_no_participation_game_pks"] = ["849829"]
    delta["administrative_exclusions"] = [{"gamePk": "849829", "facts_required": False},
                                          {"gamePk": "849829", "facts_required": True}]
    with pytest.raises(FactLedgerConflict, match="participation declarations"):
        module.compose_catchup_coverage(selected(root, store), delta, store=store)


def test_composed_union_reverifies_prefix_facts_after_delta_completion(tmp_path, monkeypatch):
    store, root, original = setup_prefix(tmp_path)
    prefix = selected(root, store)
    delta = seed(tmp_path / "delta-source", store, through=date(2026, 10, 3),
                 rows=[game(849829, "2026-10-03")])
    delta["start"] = "2026-10-03"
    original_read = store.read
    def drift(role, game_id, player_id=None):
        fact = original_read(role, game_id, player_id)
        return replace(fact, hits=0) if game_id == "823101" and role == "BATTER" and player_id == "700001" else fact
    monkeypatch.setattr(store, "read", drift)
    with pytest.raises(FactLedgerConflict, match="ledger differs"):
        module.compose_catchup_coverage(prefix, delta, store=store)


def test_live_orchestrator_verifies_prefix_before_even_target_discovery(tmp_path, monkeypatch):
    from courtvision.sports.mlb import live01
    store = MLBFactStore(tmp_path / "data/mlb/facts")
    root = tmp_path / "data/mlb/prospective/hits/catchup/2026-10-03"
    seed(root, store)
    (root / "coverage/000001.json").write_bytes(b"{}")
    monkeypatch.setattr(live01, "canonical_main", lambda _: "a" * 40)
    provider = provider_for()
    with pytest.raises(BackfillError, match="damaged"):
        live01.execute_live01(tmp_path, run_id="bad-prefix", provider=provider, clock=lambda: CAPTURED)
    assert provider.calls == []
    assert not (tmp_path / "data/mlb/prospective/hits/runs/bad-prefix/raw").exists()
