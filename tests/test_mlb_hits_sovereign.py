"""Sovereign Hits acceptance using explicitly synthetic final-game evidence."""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, timedelta
import hashlib
import inspect
import json
import socket

import pytest

from courtvision.core.candidates import EventIdentity, IdentityStatus
from courtvision.sports.mlb.ab_projection import (
    AB_PROJECTION_MODEL_VERSION, AB_PROJECTION_V2_VERSION, AtBatProjectionError,
    assemble_sovereign_batter_hits_features, project_batter_at_bats_v2,
)
from courtvision.sports.mlb.batter_hits import BatterHitsBaselineFeatures, compute_batter_hits_probability
from courtvision.sports.mlb.fact_ledger import MLBFactStore
from courtvision.sports.mlb.game_facts import canonical_json, extract_game_fact, extract_player_game_facts
from courtvision.sports.mlb.hits_acquisition import SovereignBatterHitsEvidence
from courtvision.sports.mlb.hits_season_ledger import (
    CANONICAL_HITS_SEASON_SOURCE, BatterFactReference, BatterLedgerCoverage,
    HitsLedgerError, coverage_from_bytes, coverage_from_payload, load_ledger_season, qualify_ledger_season,
)
from courtvision.sports.mlb.research_preview import preview_hits_evidence, preview_summary
from courtvision.sports.mlb.research_preview_sources import load_hits_sources
from test_mlb_ab_projection import (
    _acquired as legacy_acquired, _record, _feed, _schedule, CUTOFF, OBSERVED, GENERATED, START,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("sovereign Hits tests require no network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def final_facts(specs=((4, 1), (4, 1), (4, 1))):
    """Two prior-date doubleheader games, then a traded player's next-day game."""
    facts = []
    for index, (ab, hits) in enumerate(specs):
        game_id = str(823100 + index)
        game_date = date(2026, 9, 18 if index < 2 else 19)
        team_id = 142 if index < 2 else 114
        source = {"gamePk": int(game_id), "officialDate": game_date.isoformat(),
            "status": {"abstractGameState": "Final"},
            "teams": {"home": {"team": {"id": team_id}}, "away": {"team": {"id": 147}}}}
        game = extract_game_fact(source,
            event_identity=EventIdentity(game_id, "synthetic_final_fixture", IdentityStatus.RESOLVED, game_id),
            game_date=game_date, game_status="final", observed_at=OBSERVED - timedelta(hours=2),
            source_refs=("fixture:sovereign-final-schedule",))
        box = {"gamePk": int(game_id), "teams": {"home": {"team": {"id": team_id}, "players": {
            "ID700001": {"person": {"id": 700001, "fullName": "José Ramírez"},
                         "stats": {"batting": {"atBats": ab, "hits": hits}}}}}}}
        facts.extend(extract_player_game_facts(box, game=game, observed_at=OBSERVED - timedelta(hours=1),
                                              source_refs=("fixture:sovereign-final-boxscore",)))
    return tuple(facts)


def coverage_for(facts, **changes):
    values = dict(season=2026, mlbam_player_id="700001", target_game_id="823184",
        target_game_date=date(2026, 9, 20), coverage_through=date(2026, 9, 19),
        aggregation_cutoff=CUTOFF, observed_at=OBSERVED, complete=True,
        expected_records=tuple(BatterFactReference(f.mlbam_game_id, f.factual_record_hash) for f in facts),
        source_refs=("fixture:independently-declared-complete-season-inventory",))
    values.update(changes)
    return BatterLedgerCoverage(**values)


def sovereign_acquired(*, facts=None, coverage=None, lineup_status="statsapi_batting_order_present", batting_order_position=2):
    facts = final_facts() if facts is None else facts
    coverage = coverage_for(facts) if coverage is None else coverage
    legacy = legacy_acquired(lineup_status=lineup_status, batting_order_position=batting_order_position)
    season = qualify_ledger_season(coverage, facts, player_name=legacy.season_evidence.player_name)
    return SovereignBatterHitsEvidence(player_binding=legacy.player_binding, season_evidence=season,
        lineup_evidence=legacy.lineup_evidence, evidence_cutoff=max(CUTOFF, coverage.aggregation_cutoff))


def local_sources(tmp_path, *, provider_splits=2, withhold=None, with_ledger=True):
    """Fixture files, including ignored legacy season evidence, never real history."""
    record = _record()
    odds = {"id": record.provider_event_id, "sport_key": "baseball_mlb", "commence_time": START.isoformat(),
        "home_team": record.home_team, "away_team": record.away_team, "bookmakers": [{
            "key": record.bookmaker_key, "title": record.bookmaker_name, "markets": [{
                "key": "batter_hits", "last_update": OBSERVED.isoformat(), "outcomes": [{
                    "name": "Over", "description": record.participant_name, "point": .5, "price": -150}]}]}]}
    (tmp_path / "odds.json").write_text(json.dumps(odds))
    event = _schedule()[0]
    schedule = {"dates": [{"games": [{"gamePk": 823184, "officialDate": "2026-09-20",
        "gameDate": START.isoformat(), "teams": {
            "home": {"team": {"id": int(event.home_team_id), "name": event.home_team}},
            "away": {"team": {"id": int(event.away_team_id), "name": event.away_team}}},
        "venue": {"id": int(event.venue_id), "name": event.venue_name}, "status": {"detailedState": "Scheduled"}}]}]}
    def capture(name, request_id, payload):
        root = tmp_path / name
        root.mkdir()
        raw = json.dumps(payload).encode()
        (root / "body.json").write_bytes(raw)
        manifest = {"capture_id": name, "research_only": True, "predictions_enabled": False, "wagering_enabled": False,
            "sources": [{"request_id": request_id, "availability_status": "completed", "body_path": "body.json",
                "sha256": hashlib.sha256(raw).hexdigest(), "first_observed_at_utc": OBSERVED.isoformat(),
                "captured_at_utc": OBSERVED.isoformat(), "requested_as_of_utc": CUTOFF.isoformat()}]}
        manifest["manifest_digest"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return str(path)
    from test_mlb_hits_acquisition import _season_payload
    legacy = json.loads(_season_payload())
    legacy["people"][0]["stats"][0]["splits"] *= provider_splits
    index = {"schema_version": "mlb-hits-preview-sources-v2", "operating_date": "2026-09-20",
        "odds": {"path": "odds.json", "collected_at": CUTOFF.isoformat()},
        "schedule": {"manifest": capture("schedule", "schedule", schedule), "request_id": "schedule"},
        "game_feeds": {"823184": capture("feed", "hits-game-feed-823184", _feed())},
        "seasons": {"700001": capture("legacy-season", "hits-season-2026-700001", legacy)}}
    facts = final_facts()
    coverage = coverage_for(facts)
    store = MLBFactStore(tmp_path / "facts")
    if with_ledger:
        for fact in facts:
            if fact.mlbam_game_id != withhold:
                store.publish(fact)
        manifest_path = tmp_path / "coverage.json"
        manifest_path.write_bytes(canonical_json(coverage.to_envelope()))
        index["ledger"] = {"root": str(store.root), "coverage": {"823184": {"700001": str(manifest_path)}}}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(index))
    return path, store, coverage


def test_canonical_season_source_and_v2_arithmetic():
    acquired = sovereign_acquired()
    season = acquired.season_evidence
    projection = project_batter_at_bats_v2(acquired, generated_at=GENERATED)
    assert season.source == CANONICAL_HITS_SEASON_SOURCE
    assert season.qualification_state == "COURTVISION_LEDGER_QUALIFIED"
    assert (season.at_bats, season.hits, season.distinct_batting_games) == (12, 3, 3)
    assert season.games_played is None
    assert projection.projected_at_bats == 4
    assert projection.model_version == "cv_ab_projection_v2" != AB_PROJECTION_MODEL_VERSION
    assert projection.projection_method == "season_at_bats_per_distinct_batting_game_confirmed_lineup"
    assert projection.source == "courtvision"
    assert "season_games_played" not in projection.source_refs[-1]


def test_trade_doubleheader_and_identical_deduplication():
    facts = final_facts(((3, 1), (5, 2), (4, 0)))
    assert facts[0].game_date == facts[1].game_date
    assert facts[0].mlbam_game_id != facts[1].mlbam_game_id
    assert facts[0].team_id != facts[2].team_id
    season = qualify_ledger_season(coverage_for(facts), (*reversed(facts), facts[0]), player_name="José Ramírez")
    assert (season.at_bats, season.hits, season.distinct_batting_games) == (12, 3, 3)


def test_missing_coverage_is_not_provider_fallback():
    with pytest.raises(HitsLedgerError) as error:
        qualify_ledger_season(None, final_facts(), player_name="José Ramírez")
    assert error.value.state == "COURTVISION_LEDGER_MISSING"
    row = preview_hits_evidence(_record(), legacy_acquired(), generated_at=GENERATED)
    assert row.block_reason == "COURTVISION_LEDGER_MISSING" and row.model_probability is None
    assert row.season_hits is None and row.season_at_bats is None


@pytest.mark.parametrize("changes", [{"complete": False}, {"coverage_through": date(2026, 9, 18)}, {"expected_records": ()}])
def test_incomplete_inventory_and_zero_denominator_fail(changes):
    facts = final_facts()
    with pytest.raises(HitsLedgerError) as error:
        qualify_ledger_season(coverage_for(facts, **changes), facts, player_name="José Ramírez")
    assert error.value.state == "COURTVISION_LEDGER_INCOMPLETE"


@pytest.mark.parametrize("stats", [((None, 1), (4, 1)), ((4, None), (4, 1)), ((0, 0),)])
def test_missing_required_counts_or_zero_ab_fail(stats):
    facts = final_facts(stats)
    with pytest.raises(HitsLedgerError) as error:
        qualify_ledger_season(coverage_for(facts), facts, player_name="José Ramírez")
    assert error.value.state == "COURTVISION_LEDGER_INCOMPLETE"


def test_withheld_required_fact_blocks_without_fallback(tmp_path, monkeypatch):
    path, _, _ = local_sources(tmp_path, provider_splits=3, withhold="823101")
    prohibit_provider_season(monkeypatch)
    row = load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0]
    assert row.block_reason == "COURTVISION_LEDGER_INCOMPLETE"
    assert row.model_probability is None and row.player_id == "700001"


def prohibit_provider_season(monkeypatch):
    import courtvision.sports.mlb.hits_acquisition as acquisition
    def denied(*args, **kwargs):
        raise AssertionError("provider season endpoint/parser must not be used")
    for name in ("hits_season_hitting_request", "acquire_hits_season_hitting",
                 "parse_batter_season_hitting_evidence", "materialize_acquired_hits_evidence"):
        monkeypatch.setattr(acquisition, name, denied)


@pytest.mark.parametrize("splits", [1, 2, 3])
def test_offline_ledger_to_candidate_preview_ignores_legacy_splits(tmp_path, monkeypatch, splits):
    path, store, coverage = local_sources(tmp_path, provider_splits=splits)
    prohibit_provider_season(monkeypatch)
    row = load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0]
    assert row.prediction_status == "QUALIFIED_RESEARCH"
    assert row.model_probability == 1 - (1 - 3 / 12) ** 4
    assert row.probability_market_independence == "YES" and row.limitation_status == "NAIVE_UNCALIBRATED_BASELINE"
    assert row.season_source == CANONICAL_HITS_SEASON_SOURCE
    assert row.ab_projection_version == AB_PROJECTION_V2_VERSION and row.distinct_batting_games == 3
    loaded = load_ledger_season(store, coverage, player_name="José Ramírez")
    assert loaded.season_aggregate_hash == row.season_aggregate_hash
    assert row.research_only and not row.eligible_for_betting and not row.kelly_eligible
    assert row.approval_status == "not_approved"
    assert any(ref.startswith("cv-ledger-coverage-manifest:") for ref in row.source_refs)
    assert any(ref.startswith("cv-ledger-store:") for ref in row.source_refs)
    # No full list of per-game records is embedded in the user-facing row.
    assert all('"records"' not in ref for ref in row.source_refs)


def test_legacy_source_can_be_absent_or_corrupt_without_affecting_sovereign_path(tmp_path):
    path, _, _ = local_sources(tmp_path)
    index = json.loads(path.read_text())
    index["seasons"] = {"700001": "does-not-exist.json"}
    path.write_text(json.dumps(index))
    assert load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0].prediction_status == "QUALIFIED_RESEARCH"
    del index["seasons"]
    path.write_text(json.dumps(index))
    assert load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0].prediction_status == "QUALIFIED_RESEARCH"


def test_no_ledger_is_source_unavailability_not_model_failure(tmp_path):
    path, _, _ = local_sources(tmp_path, with_ledger=False)
    row = load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0]
    assert row.block_reason == "COURTVISION_LEDGER_MISSING" and row.player_id == "700001"
    status = preview_summary([row], "2026-09-20")["market_status"]["batter_hits"]["status"]
    assert "SEASON_LEDGER_NOT_POPULATED" in status


@pytest.mark.parametrize("case", ["duplicate", "hash", "player", "season", "nonfinal"])
def test_conflicting_facts_fail_closed(case):
    facts = final_facts()
    coverage = coverage_for(facts)
    if case == "duplicate":
        supplied = (*facts, replace(facts[0], hits=2))
    else:
        changes = {"hash": {"hits": 2}, "player": {"mlbam_player_id": "700002"},
                   "season": {"game_date": date(2025, 9, 18)}}
        if case == "nonfinal":
            object.__setattr__(facts[0], "game_status", "live")
            supplied = facts
        else:
            supplied = (replace(facts[0], **changes[case]), *facts[1:])
    with pytest.raises(HitsLedgerError) as error:
        qualify_ledger_season(coverage, supplied, player_name="José Ramírez")
    assert error.value.state == "COURTVISION_LEDGER_CONFLICT"


@pytest.mark.parametrize("changes", [{"game_date": date(2026, 9, 20)},
    {"game_date": date(2026, 9, 21), "observed_at": START + timedelta(days=2)},
    {"observed_at": CUTOFF + timedelta(seconds=1)}, {"observed_at": OBSERVED + timedelta(seconds=1)},
    {"mlbam_game_id": "823184"}])
def test_post_cutoff_target_and_same_day_records_rejected(changes):
    facts = (replace(final_facts()[0], **changes),)
    with pytest.raises(HitsLedgerError) as error:
        qualify_ledger_season(coverage_for(facts), facts, player_name="José Ramírez")
    assert error.value.state == "COURTVISION_LEDGER_INCOMPLETE"


def test_aggregate_hash_portable_deterministic_and_auditable():
    facts = final_facts()
    coverage = coverage_for(facts)
    first = qualify_ledger_season(coverage, facts, player_name="José Ramírez")
    reordered = coverage_for(tuple(reversed(facts)))
    second = qualify_ledger_season(reordered, (*reversed(facts), facts[0]), player_name="José Ramírez")
    assert first.season_aggregate_hash == second.season_aggregate_hash
    manifest = first.aggregate_manifest()
    assert manifest["season"] == 2026 and manifest["mlbam_player_id"] == "700001"
    assert manifest["aggregation_cutoff"] == CUTOFF.isoformat()
    assert [r["logical_identity"] for r in manifest["records"]] == [list(f.logical_identity) for f in facts]
    assert [r["factual_record_hash"] for r in manifest["records"]] == [f.factual_record_hash for f in facts]
    assert coverage_from_bytes(canonical_json(coverage.to_envelope())) == coverage
    for changed_facts, changed_coverage in (
        (facts, replace(coverage, aggregation_cutoff=CUTOFF + timedelta(seconds=1))),
        ((replace(facts[0], source_refs=("fixture:changed-source",)), *facts[1:]), None),
    ):
        changed = qualify_ledger_season(changed_coverage or coverage_for(changed_facts), changed_facts, player_name="José Ramírez")
        assert changed.season_aggregate_hash != first.season_aggregate_hash


def test_coverage_hash_duplicate_keys_and_wrong_target_are_rejected():
    coverage = coverage_for(final_facts())
    envelope = coverage.to_envelope()
    envelope["coverage"]["season"] = 2025
    with pytest.raises(HitsLedgerError):
        coverage_from_payload(envelope)
    with pytest.raises(HitsLedgerError):
        coverage_from_bytes(b'{"coverage":{},"coverage":{}}')
    for changed in (replace(coverage, target_game_id="823185"),
                    replace(coverage, target_game_date=date(2026, 9, 21), coverage_through=date(2026, 9, 20))):
        with pytest.raises(HitsLedgerError, match="another target"):
            sovereign_acquired(coverage=changed)


@pytest.mark.parametrize("lineup_status,position", [("unavailable", None), ("not_listed", None)])
def test_v2_requires_confirmed_lineup_without_default(lineup_status, position):
    acquired = sovereign_acquired(lineup_status=lineup_status, batting_order_position=position)
    with pytest.raises(AtBatProjectionError, match="confirmed batting-order"):
        project_batter_at_bats_v2(acquired, generated_at=GENERATED)
    row = preview_hits_evidence(_record(), acquired, generated_at=GENERATED)
    assert row.block_reason == "LINEUP_UNAVAILABLE" and row.model_probability is None


def test_slot_only_changes_provenance_and_legacy_evidence_cannot_enter_v2():
    second = project_batter_at_bats_v2(sovereign_acquired(batting_order_position=2), generated_at=GENERATED)
    ninth = project_batter_at_bats_v2(sovereign_acquired(batting_order_position=9), generated_at=GENERATED)
    assert second.projected_at_bats == ninth.projected_at_bats == 4
    assert second.source_refs != ninth.source_refs
    with pytest.raises(AtBatProjectionError, match="CourtVision ledger"):
        project_batter_at_bats_v2(legacy_acquired(), generated_at=GENERATED)


@pytest.mark.parametrize("generated", [OBSERVED, START, START + timedelta(seconds=1), GENERATED.replace(tzinfo=None)])
def test_v2_requires_all_evidence_before_generation_and_both_starts(generated):
    with pytest.raises(ValueError):
        project_batter_at_bats_v2(sovereign_acquired(), generated_at=generated)


@pytest.mark.parametrize("change", [{"american_odds": 200}, {"american_odds": -500},
    {"bookmaker_key": "other-book", "bookmaker_name": "Other Book"}])
def test_price_implied_probability_and_sportsbook_do_not_affect_model(change):
    source = _record()
    acquired = sovereign_acquired()
    baseline = preview_hits_evidence(source, acquired, generated_at=GENERATED)
    changed = preview_hits_evidence(replace(source, **change), acquired, generated_at=GENERATED)
    assert changed.prediction_status == "QUALIFIED_RESEARCH"
    assert changed.model_probability == baseline.model_probability
    if "american_odds" in change:
        assert changed.market_implied_probability != baseline.market_implied_probability


def test_only_baseball_numeric_inputs_drive_unchanged_probability():
    features = assemble_sovereign_batter_hits_features(sovereign_acquired(), generated_at=GENERATED)
    probability = compute_batter_hits_probability(features, generated_at=GENERATED)
    assert probability.model_probability == 1 - (1 - features.season_hits / features.season_at_bats) ** features.projected_at_bats
    assert set(inspect.signature(compute_batter_hits_probability).parameters) == {"features", "generated_at"}
    assert not {"odds", "line", "sportsbook", "implied_probability", "bookmaker"} & {f.name for f in fields(BatterHitsBaselineFeatures)}
    changed_season = sovereign_acquired(facts=final_facts(((4, 2), (4, 1), (4, 1))))
    changed = assemble_sovereign_batter_hits_features(changed_season, generated_at=GENERATED)
    assert compute_batter_hits_probability(changed, generated_at=GENERATED).model_probability != probability.model_probability
    assert compute_batter_hits_probability(replace(features, projected_at_bats=3), generated_at=GENERATED).model_probability != probability.model_probability


def test_sovereign_candidate_research_only_no_officialpick(monkeypatch):
    import courtvision.sports.mlb.research_preview as preview
    original = preview.assemble_batter_hits_candidate
    candidates = []
    def capture(**kwargs):
        candidate = original(**kwargs)
        candidates.append(candidate)
        return candidate
    monkeypatch.setattr(preview, "assemble_batter_hits_candidate", capture)
    row = preview.preview_hits_evidence(_record(), sovereign_acquired(), generated_at=GENERATED)
    assert row.prediction_status == "QUALIFIED_RESEARCH" and len(candidates) == 1
    candidate = candidates[0]
    assert candidate.research_only and not candidate.eligible_for_betting and not candidate.eligible_for_official_pick


def test_corrupt_immutable_record_is_exact_ledger_conflict(tmp_path):
    path, store, _ = local_sources(tmp_path)
    stored = store.root / "batter" / "823100" / "700001.json"
    stored.write_bytes(b'{"tampered":true}')
    row = load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0]
    assert row.block_reason == "COURTVISION_LEDGER_CONFLICT" and row.model_probability is None


@pytest.mark.parametrize("component,field,value", [
    ("lineup_evidence", "mlbam_player_id", "700002"),
    ("lineup_evidence", "mlbam_game_id", "823185"),
    ("lineup_evidence", "team_side", "away"),
])
def test_v2_player_game_and_lineup_team_are_bound(component, field, value):
    acquired = sovereign_acquired()
    with pytest.raises(ValueError):
        replace(acquired, **{component: replace(getattr(acquired, component), **{field: value})})


def test_plain_legacy_rejection_is_not_automatically_the_current_blocker(tmp_path):
    from courtvision.sports.mlb.research_preview_sources import build_local_preview
    root = tmp_path / "CV-OCT.3A-R4" / "preserved" / "manifests"
    root.mkdir(parents=True)
    # If the old automatic fallback were used it would try to parse this file.
    (root / "02_qualification_manifest.json").write_text('{"old_provider_split_diagnostic":true}')
    rows = build_local_preview("2026-09-20", repository_root=tmp_path, qualification_root=tmp_path,
                               generated_at=GENERATED)
    hits = next(row for row in rows if row.market_type == "batter_hits")
    assert hits.block_reason == "COURTVISION_LEDGER_MISSING" and hits.model_probability is None
