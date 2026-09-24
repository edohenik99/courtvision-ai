"""Offline preview acceptance: contracts, real adapters, artifact I/O and UI."""
from __future__ import annotations

import csv
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import socket

import pytest

from courtvision.sports.mlb import research_preview_sources as sources
from courtvision.sports.mlb.hits_features import parse_batter_season_hitting_evidence
from courtvision.sports.mlb.research_preview import (
    HITS_LIMITATION, HR_LIMITATION, HR_WARNING, hits_failure_reason,
    preview_availability, preview_hits_evidence, preview_hr_prediction, preview_summary, sort_preview_rows, unavailable_row,
)
from test_mlb_ab_projection import _acquired, _record, GENERATED, START
from test_mlb_hits_acquisition import _season_payload


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("preview must not call a network")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def _hits(**kwargs):
    return preview_hits_evidence(_record(), _acquired(**kwargs), generated_at=GENERATED)


def _hr():
    return {
        "prediction_schema_version": "mlb-hr-prospective-prediction-v1", "prediction_id": "fixture-prediction",
        "prediction_run_id": "fixture-run", "research_only": "true", "eligible_for_betting": "false",
        "eligible_for_official_pick": "false", "approval_status": "not_approved", "operating_date": "2026-09-20",
        "model_id": "fixture-existing-hr", "model_version": "research-v1", "feature_schema_version": "mlb-hr-research-feature-v1",
        "event_id": "fixture-event", "player_id": "opaque-legacy-id", "player_name": "Fixture Batter",
        "home_team": "Cleveland Guardians", "away_team": "Minnesota Twins", "sportsbook": "fixture-book",
        "market_key": "batter_home_runs", "side": "Over", "point": "0.5", "prediction_time_price": "400",
        "implied_probability": "0.2", "model_probability": "0.123456789123",
        "commence_time_utc": START.isoformat(), "prediction_timestamp_utc": GENERATED.isoformat(),
        "selected_snapshot_timestamp_utc": (GENERATED - timedelta(minutes=10)).isoformat(),
    }


def test_qualified_hits_uses_existing_probability_and_evidence():
    row = _hits()
    assert row.prediction_status == "QUALIFIED_RESEARCH"
    assert row.model_probability == pytest.approx(1 - (1 - 125 / 500) ** (500 / 130))
    assert row.projected_at_bats == 500 / 130
    assert row.probability_market_independence == "YES"
    assert row.limitation_status == HITS_LIMITATION
    assert row.canonical_event_id == "823184"
    assert row.player_id == "700001"


@pytest.mark.parametrize(("kwargs", "reason", "detail"), [
    ({"games_played": None}, "AB_PROJECTION_UNAVAILABLE", "cv_ab_projection_v1 requires positive season games_played evidence"),
    ({"lineup_status": "unavailable", "batting_order_position": None}, "LINEUP_UNAVAILABLE", "cv_ab_projection_v1 requires confirmed batting-order presence"),
])
def test_blocked_hits_preserves_reason_and_null_probability(kwargs, reason, detail):
    row = _hits(**kwargs)
    assert row.prediction_status == "BLOCKED"
    assert row.block_reason == reason
    assert row.block_detail == detail
    assert row.model_probability is None


def test_no_backdated_probability_from_historical_evidence():
    row = preview_hits_evidence(_record(), _acquired(), generated_at=START + timedelta(seconds=1))
    assert row.prediction_status == "BLOCKED"
    assert row.block_reason == "PREGAME_CUTOFF_FAILED"
    assert row.model_probability is None


def test_multiteam_season_is_rejected_by_unchanged_parser():
    payload = json.loads(_season_payload())
    splits = payload["people"][0]["stats"][0]["splits"]
    splits.extend([dict(splits[0]), dict(splits[0])])
    acquired = _acquired()
    with pytest.raises(ValueError, match="exactly one unambiguous split") as error:
        parse_batter_season_hitting_evidence(
            payload, season=2026, player_binding=acquired.player_binding,
            observed_at=acquired.season_evidence.observed_at, evidence_cutoff=acquired.evidence_cutoff,
            source_refs=("fixture:multi-team",),
        )
    assert hits_failure_reason(str(error.value), "SEASON_EVIDENCE_UNAVAILABLE") == "AMBIGUOUS_SEASON_SPLITS"


@pytest.mark.parametrize("multiple_splits", [False, True])
def test_local_hits_index_uses_existing_capture_parser_and_candidate(tmp_path, multiple_splits):
    # Fixture manifests use the existing acquisition contract, including raw
    # digests and the manifest digest. No collection or clock backdating occurs.
    from test_mlb_ab_projection import _feed, _schedule, CUTOFF, OBSERVED
    source = _record()
    odds = {"id": source.provider_event_id, "sport_key": "baseball_mlb", "commence_time": START.isoformat(),
            "home_team": source.home_team, "away_team": source.away_team, "bookmakers": [{
                "key": source.bookmaker_key, "title": source.bookmaker_name, "markets": [{
                    "key": "batter_hits", "last_update": OBSERVED.isoformat(), "outcomes": [{
                        "name": "Over", "description": source.participant_name, "point": .5, "price": -150}]}]}]}
    (tmp_path / "odds.json").write_text(json.dumps(odds))
    event = _schedule()[0]
    schedule = {"dates": [{"games": [{"gamePk": int(event.event_id), "officialDate": "2026-09-20",
        "gameDate": START.isoformat(), "teams": {"home": {"team": {"id": int(event.home_team_id), "name": event.home_team}},
        "away": {"team": {"id": int(event.away_team_id), "name": event.away_team}}},
        "venue": {"id": int(event.venue_id), "name": event.venue_name}, "status": {"detailedState": "Scheduled"}}]}]}
    season = json.loads(_season_payload())
    if multiple_splits:
        season["people"][0]["stats"][0]["splits"] *= 3

    def capture(name, request_id, payload):
        root = tmp_path / name
        root.mkdir()
        raw = json.dumps(payload).encode()
        (root / "body.json").write_bytes(raw)
        manifest = {"capture_id": name, "research_only": True, "predictions_enabled": False, "wagering_enabled": False,
                    "sources": [{"request_id": request_id, "availability_status": "completed", "body_path": "body.json",
                    "sha256": hashlib.sha256(raw).hexdigest(), "first_observed_at_utc": OBSERVED.isoformat(),
                    "captured_at_utc": OBSERVED.isoformat(), "requested_as_of_utc": CUTOFF.isoformat()}]}
        manifest["manifest_digest"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return str(path)

    index = {"schema_version": "mlb-hits-preview-sources-v1", "operating_date": "2026-09-20",
             "odds": {"path": "odds.json", "collected_at": CUTOFF.isoformat()},
             "schedule": {"manifest": capture("schedule", "schedule", schedule), "request_id": "schedule"},
             "game_feeds": {"823184": capture("feed", "hits-game-feed-823184", _feed())},
             "seasons": {"700001": capture("season", "hits-season-2026-700001", season)}}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(index))
    row = sources.load_hits_sources(path, "2026-09-20", generated_at=GENERATED)[0]
    if multiple_splits:
        assert row.prediction_status == "BLOCKED"
        assert row.block_reason == "AMBIGUOUS_SEASON_SPLITS"
        assert row.block_detail == "season evidence requires exactly one unambiguous split"
        assert row.model_probability is None
    else:
        assert row.prediction_status == "QUALIFIED_RESEARCH", row.block_detail
        assert row.model_probability == _hits().model_probability


def test_hr_probability_is_copied_and_never_claims_sovereignty():
    raw = _hr()
    before = dict(raw)
    row = preview_hr_prediction(raw, day="2026-09-20", source_ref="fixture:prediction")
    assert raw == before
    assert row.prediction_status == "LEGACY_RESEARCH"
    assert row.model_probability == float(raw["model_probability"])
    assert row.probability_market_independence == "NO"
    assert row.limitation_status == HR_LIMITATION
    assert row.market_implied_probability == .2
    assert row.evidence_cutoff is None
    assert row.player_id is None  # legacy opaque IDs are not canonical player IDs
    assert row.identity_status == "name_only_research"


@pytest.mark.parametrize("change", [
    {"model_probability": "NaN"}, {"model_probability": "1.1"}, {"eligible_for_betting": "true"},
    {"operating_date": "2026-09-19"}, {"side": "Under"}, {"point": "1.5"},
    {"prediction_timestamp_utc": (START + timedelta(seconds=1)).isoformat()},
])
def test_invalid_hr_cannot_display_probability(change):
    row = preview_hr_prediction({**_hr(), **change}, day="2026-09-20", source_ref="fixture:prediction")
    assert row.prediction_status == "UNAVAILABLE"
    assert row.model_probability is None
    assert row.block_detail


@pytest.mark.parametrize("change", [
    {"research_only": False}, {"eligible_for_betting": True}, {"kelly_eligible": True},
    {"approval_status": "approved"}, {"prediction_status": "BLOCKED", "block_reason": "failure"},
])
def test_contract_rejects_unsafe_rows(change):
    with pytest.raises(ValueError):
        replace(_hits(), **change)


def test_mixed_board_sort_safety_roundtrip_and_read_only_loader(tmp_path):
    hr = preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")
    rows = [_hits(), hr, _hits(games_played=None)]
    assert sort_preview_rows(rows) == sort_preview_rows(list(reversed(rows)))
    for row in rows:
        assert row.research_only is True
        assert row.approval_status == "not_approved"
        assert row.eligible_for_betting is False
        assert row.kelly_eligible is False
    board, summary_path = sources.write_preview(rows, "2026-09-20", tmp_path)
    before = {p: p.read_bytes() for p in (board, summary_path)}
    loaded, summary = sources.load_preview_board(tmp_path, "2026-09-20")
    assert loaded == sort_preview_rows(rows)
    assert summary["hits_blocked"] == 1
    assert summary["hr_market_contaminated"] == 1
    assert summary["hits_qualified"] == 1
    assert before == {p: p.read_bytes() for p in before}


def test_writer_never_overwrites_prior_run(tmp_path):
    rows = [_hits()]
    board, summary = sources.write_preview(rows, "2026-09-20", tmp_path)
    before = board.read_bytes()
    second, _ = sources.write_preview(rows, "2026-09-20", tmp_path)
    assert second != board
    assert board.read_bytes() == before
    assert summary.exists()


def test_sort_order_breaks_ties_without_input_order():
    first = _hits(games_played=None)
    second = replace(first, block_detail="different preserved diagnostic")
    assert sort_preview_rows([first, second]) == sort_preview_rows([second, first])


def test_cli_reports_output_failure_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sources, "build_local_preview", lambda *a, **k: [_hits()])
    def denied(*args, **kwargs):
        raise PermissionError("fixture output permission denied")
    monkeypatch.setattr(sources, "write_preview", denied)
    assert sources.main(["--date", "2026-09-20", "--output-root", str(tmp_path)]) == 2
    assert capsys.readouterr().out.startswith("MLB_PREVIEW_OUTPUT_UNAVAILABLE:")


def test_missing_sources_and_date_isolation_are_explicit(tmp_path):
    rows = sources.build_local_preview("2026-09-24", repository_root=tmp_path, qualification_root=tmp_path)
    summary = preview_summary(rows, "2026-09-24")
    assert summary["status"] == "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE"
    assert summary["games_seen"] == summary["players_seen"] == 0
    assert all(r.row_kind == "SOURCE_STATUS" for r in rows)
    sources.write_preview([_hits()], "2026-09-20", tmp_path)
    loaded, _ = sources.load_preview_board(tmp_path, "2026-09-24")
    assert all(r.prediction_status == "UNAVAILABLE" for r in loaded)


def test_mixed_dates_rejected(tmp_path):
    with pytest.raises(ValueError, match="different operating dates"):
        sources.write_preview([_hits()], "2026-09-24", tmp_path)


def test_tampered_board_fails_closed(tmp_path):
    board, _ = sources.write_preview([_hits()], "2026-09-20", tmp_path)
    board.write_bytes(board.read_bytes().replace(b"QUALIFIED_RESEARCH", b"LEGACY_RESEARCH"))
    with pytest.raises(ValueError, match="integrity mismatch"):
        sources.load_preview_board(tmp_path, "2026-09-20")


def test_hr_manifest_binding(tmp_path):
    row = _hr()
    path = tmp_path / "predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    manifest_path = tmp_path / "prediction_manifest_v1.json"
    manifest = {"operating_date": "2026-09-20", "prediction_count": 1,
                "predictions_csv_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest))
    assert sources.load_hr_predictions(path, "2026-09-20")[0].prediction_status == "LEGACY_RESEARCH"
    manifest["operating_date"] = "2026-09-21"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="date mismatch"):
        sources.load_hr_predictions(path, "2026-09-20")


def test_preserved_rejection_displays_actual_reason_and_never_probability(tmp_path):
    path = tmp_path / "rejection.json"
    path.write_text(json.dumps({
        "schema_version": "cv-oct3a-r4-qualification-manifest-v1", "operating_date": "2026-09-20",
        "research_only": True, "eligible_for_betting": False, "wagering_enabled": False,
        "complete_live_bundle_ready": False, "gamePk": "823184", "player_mlbam_id": "700001",
        "player_canonical_name": "Fixture Batter", "authoritative_scheduled_start": START.isoformat(),
        "batting_order_evidence": {"lineup_status": "statsapi_batting_order_present"},
        "evidence_cutoff": GENERATED.isoformat(), "season_evidence": {"qualified": False, "diagnosis": {
            "canonical_validator_exception": "season hitting response requires exactly one split"}},
    }))
    row = sources.load_preserved_hits_rejection(path, "2026-09-20")
    assert row.block_reason == "AMBIGUOUS_SEASON_SPLITS"
    assert row.model_probability is None
    assert row.provider_event_id is None
    assert row.sportsbook is None


def test_streamlit_renders_generated_board_without_modifying_predictions(tmp_path):
    from streamlit.testing.v1 import AppTest
    output = tmp_path / "outputs"
    rows = [_hits(), _hits(games_played=None), preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")]
    board, summary = sources.write_preview(rows, "2026-09-20", output / "runtime" / "mlb" / "research")
    before = board.read_bytes(), summary.read_bytes()
    app = AppTest.from_string(
        "from pathlib import Path\nfrom courtvision.streamlit_mlb_preview import render_mlb_research_preview\n"
        f"render_mlb_research_preview(Path({str(output)!r}), '2026-09-20')\n"
    ).run(timeout=30)
    assert not app.exception
    assert any(HR_WARNING in item.value and HR_LIMITATION in item.value for item in app.warning)
    assert len(app.dataframe[0].value) == 3
    assert "Blocked" in set(app.dataframe[0].value["Status"])
    app.radio[0].set_value("Home Runs").run()
    assert len(app.dataframe[0].value) == 1
    assert before == (board.read_bytes(), summary.read_bytes())


def test_partial_availability_counts_only_real_usable_predictions():
    hr = preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")
    marker = unavailable_row("2026-09-20", "batter_hits", "HITS_SOURCES_UNAVAILABLE")
    summary = preview_summary([marker, hr], "2026-09-20")
    assert summary["status"] == "MLB_PREVIEW_PARTIAL_AVAILABILITY"
    assert summary["prediction_rows"] == summary["usable_prediction_rows"] == 1
    assert summary["market_status"]["batter_hits"]["status"] == "SOURCE UNAVAILABLE"
    assert summary["market_status"]["batter_hits"]["prediction_rows"] == 0
    assert summary["market_status"]["batter_home_runs"]["status"] == "1 LEGACY RESEARCH ROWS LOADED"


@pytest.mark.parametrize("blocked_player", [False, True])
def test_no_usable_market_means_overall_unavailable(blocked_player):
    rows = [unavailable_row("2026-09-20", market, "SOURCE_MISSING")
            for market in ("batter_hits", "batter_home_runs")]
    if blocked_player:
        rows.append(_hits(games_played=None))
    summary = preview_summary(rows, "2026-09-20")
    assert summary["status"] == "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE"
    assert summary["usable_prediction_rows"] == 0
    assert summary["prediction_rows"] == int(blocked_player)


def test_both_markets_loaded_mean_overall_research_available():
    hr = preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")
    summary = preview_summary([_hits(), hr], "2026-09-20")
    assert summary["status"] == "MLB_PREVIEW_RESEARCH_ONLY"
    assert summary["usable_prediction_rows"] == 2


def test_legacy_summary_is_validated_and_presented_without_rewriting(tmp_path):
    rows = [unavailable_row("2026-09-20", "batter_hits", "HITS_SOURCES_UNAVAILABLE"),
            preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")]
    board, summary_path = sources.write_preview(rows, "2026-09-20", tmp_path)
    summary = json.loads(summary_path.read_text())
    for key in preview_availability(rows):
        summary.pop(key)
    summary["status"] = "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE"
    summary_path.write_text(json.dumps(summary))
    before = board.read_bytes(), summary_path.read_bytes()
    loaded, presented = sources.load_preview_board(tmp_path, "2026-09-20")
    assert loaded == sort_preview_rows(rows)
    assert presented["status"] == "MLB_PREVIEW_PARTIAL_AVAILABILITY"
    assert before == (board.read_bytes(), summary_path.read_bytes())
    summary["hr_market_contaminated"] += 1
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="summary content mismatch"):
        sources.load_preview_board(tmp_path, "2026-09-20")


def _preview_app(tmp_path, rows):
    from streamlit.testing.v1 import AppTest
    output = tmp_path / "outputs"
    sources.write_preview(rows, "2026-09-20", output / "runtime" / "mlb" / "research")
    return AppTest.from_string(
        "from pathlib import Path\nfrom courtvision.streamlit_mlb_preview import render_mlb_research_preview\n"
        f"render_mlb_research_preview(Path({str(output)!r}), '2026-09-20')\n"
    ).run(timeout=30)


def test_source_availability_is_separate_and_details_default_to_real_player(tmp_path):
    marker = unavailable_row("2026-09-20", "batter_hits", "HITS_SOURCES_UNAVAILABLE")
    hr = preview_hr_prediction(_hr(), day="2026-09-20", source_ref="fixture:prediction")
    app = _preview_app(tmp_path, [marker, hr])
    assert not app.exception
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["Player"] == hr.player_name
    assert all("Source unavailable" not in label for label in app.selectbox[0].options)
    assert json.loads(app.json[-1].value) == json.loads(json.dumps(hr.to_dict()))
    assert any("Hits" in item.value and "SOURCE UNAVAILABLE" in item.value for item in app.markdown)
    assert any("Home Runs" in item.value and "LEGACY RESEARCH ROWS LOADED" in item.value for item in app.markdown)
    assert any("HITS_SOURCES_UNAVAILABLE" in item.value for item in app.caption)
    assert not any("MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE" in item.value for item in app.warning)
    assert any("Partial availability" in item.value for item in app.info)
    assert any(HR_LIMITATION in item.value and HR_WARNING in item.value for item in app.warning)
    assert any(HITS_LIMITATION in item.value for item in app.info)
    labels = {item.value for item in app.caption}
    assert {"Player", "Game", "Market", "Line", "CourtVision probability", "Market implied probability",
            "Prediction status", "Market independence", "Limitation status", "Identity status", "Lineup status",
            "Model ID", "Model version", "Evidence cutoff", "Block reason"} <= labels
    values = {item.value for item in app.text}
    assert {hr.player_name, hr.model_id, hr.model_version, "12.3%", "20.0%", "NO", HR_LIMITATION} <= values
    assert {e.label for e in app.expander} >= {"Advanced / Raw Evidence", "Advanced / Raw Source Evidence"}
    assert json.loads(app.json[0].value)[0]["row_kind"] == "SOURCE_STATUS"


def test_blocked_real_player_is_still_visible_and_readable(tmp_path):
    blocked = _hits(games_played=None)
    app = _preview_app(tmp_path, [blocked, unavailable_row("2026-09-20", "batter_home_runs", "HR_SOURCE_UNAVAILABLE")])
    assert not app.exception
    assert len(app.dataframe[0].value) == 1
    assert app.dataframe[0].value.iloc[0]["Status"] == "Blocked"
    assert blocked.block_reason in {item.value for item in app.text}
    assert blocked.block_detail in {item.value for item in app.text}
    assert json.loads(app.json[-1].value)["model_probability"] is None


def test_qualified_hits_details_show_baseline_inputs(tmp_path):
    app = _preview_app(tmp_path, [_hits()])
    assert not app.exception
    assert {"Season hits", "Season at-bats", "Projected at-bats"} <= {item.value for item in app.caption}
    assert {"125", "500", str(500 / 130)} <= {item.value for item in app.text}


def test_all_sources_unavailable_has_no_player_board_or_selected_detail(tmp_path):
    rows = [unavailable_row("2026-09-20", market, "SOURCE_MISSING")
            for market in ("batter_hits", "batter_home_runs")]
    app = _preview_app(tmp_path, rows)
    assert not app.exception
    assert not app.dataframe
    assert not app.selectbox
    assert any("MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE" in item.value for item in app.warning)
    assert "Market availability" in {item.value for item in app.subheader}
    assert len(json.loads(app.json[0].value)) == 2


def test_workstation_mlb_route_does_not_construct_nba_engine(monkeypatch):
    import courtvision_streamlit_app as app
    calls = []

    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    class UI:
        sidebar = Context()
        def radio(self, label, *args, **kwargs):
            assert label == "Sport"
            return "MLB Research"
        def text_input(self, *args, **kwargs):
            return "outputs"
        def date_input(self, *args, **kwargs):
            return GENERATED.date()
        def title(self, *args, **kwargs):
            pass
        def markdown(self, *args, **kwargs):
            pass

    monkeypatch.setattr(app, "st", UI())
    monkeypatch.setattr(app, "configure_page", lambda: None)
    monkeypatch.setattr(app, "init_state", lambda: None)
    monkeypatch.setattr(app, "_THEME_AVAILABLE", False)
    monkeypatch.setattr(app, "get_ai", lambda *a: pytest.fail("NBA engine must not be constructed"))
    import courtvision.streamlit_mlb_preview as view
    monkeypatch.setattr(view, "render_mlb_research_preview", lambda *args: calls.append(args))
    app.main()
    assert calls[0][1] == "2026-09-20"
