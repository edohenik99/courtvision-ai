"""Persisted sovereign admission; every saved artifact here is synthetic."""
from dataclasses import replace
import csv
import hashlib
import json
from types import SimpleNamespace

import pytest

from courtvision.sports.mlb.research_preview import (
    LEGACY_HITS_PROVENANCE_UNQUALIFIED, LEGACY_PREVIEW_SCHEMA_VERSION,
    PREVIEW_SCHEMA_VERSION, HR_WARNING, preview_hr_prediction, preview_summary, unavailable_row,
)
from courtvision.sports.mlb.research_preview_sources import load_preview_board, write_preview
from test_mlb_research_preview import _hits, _hr, _preview_app

DAY = "2026-09-20"
SOVEREIGN_FIELDS = ("season_source", "ab_projection_version", "season_aggregate_hash", "distinct_batting_games")


def _legacy_hits():
    """Pre-cutover v1 shape and provider AB/game inputs from the original HIGH."""
    data = _hits().to_dict()
    for field in SOVEREIGN_FIELDS:
        data.pop(field)
    data.update(preview_schema_version=LEGACY_PREVIEW_SCHEMA_VERSION,
                season_hits=125, season_at_bats=500, projected_at_bats=500 / 130,
                model_probability=0.669275477829889,
                source_refs=("fixture:mlb_statsapi_season_splits", "fixture:cv_ab_projection_v1"))
    return data


def _persist_raw(root, data, *, old_summary=False):
    """Serialize claimed rows independently of the writer's admission checks."""
    rows = data if isinstance(data, list) else [data]
    run = root / DAY / "synthetic-preserved-run"
    run.mkdir(parents=True)
    board = run / f"mlb_research_board_{DAY}.csv"
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with board.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "source_refs": json.dumps(row["source_refs"])})
    summary = preview_summary([SimpleNamespace(**row) for row in rows], DAY)
    summary["preview_schema_version"] = rows[0]["preview_schema_version"]
    if old_summary:
        for key in ("availability_schema_version", "market_status", "prediction_rows", "usable_prediction_rows"):
            summary.pop(key)
        summary["status"] = "MLB_PREVIEW_RESEARCH_ONLY"
    summary.update(board_filename=board.name, board_sha256=hashlib.sha256(board.read_bytes()).hexdigest())
    path = run / f"mlb_research_summary_{DAY}.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    return board, path


def test_sovereign_constructor_and_exact_current_roundtrip(tmp_path):
    row = _hits()
    assert row.preview_schema_version == PREVIEW_SCHEMA_VERSION == "mlb-research-preview-v2"
    assert row.prediction_status == "QUALIFIED_RESEARCH"
    board, summary_path = write_preview([row], DAY, tmp_path)
    before = board.read_bytes(), summary_path.read_bytes()
    loaded, summary = load_preview_board(tmp_path, DAY)
    assert loaded == [row]
    assert before == (board.read_bytes(), summary_path.read_bytes())
    assert summary["hits_qualified"] == summary["usable_prediction_rows"] == 1
    assert summary["source_preview_schema_version"] == PREVIEW_SCHEMA_VERSION
    for field in SOVEREIGN_FIELDS:
        assert getattr(loaded[0], field) == getattr(row, field)
    assert loaded[0].source_refs == row.source_refs


INVALID_QUALIFICATION = [
    {"season_source": None}, {"season_source": "MLB_STATSAPI_SEASON_SPLITS"},
    {"season_source": "mlb_statsapi"}, {"ab_projection_version": "cv_ab_projection_v1"},
    {"ab_projection_version": None}, {"season_aggregate_hash": None},
    {"season_aggregate_hash": "not-a-hash"}, {"season_aggregate_hash": "0" * 64},
    {"player_id": None}, {"player_id": "0700001"}, {"player_id": "700002"},
    {"canonical_event_id": None}, {"canonical_event_id": "provider-event"}, {"canonical_event_id": "823185"},
    {"identity_status": "unresolved"}, {"event_status": "unresolved"},
    {"lineup_status": "unavailable"}, {"season_at_bats": None}, {"season_at_bats": 0},
    {"season_hits": None}, {"season_hits": -1}, {"season_hits": 13}, {"season_hits": 2},
    {"projected_at_bats": None}, {"projected_at_bats": 0.0}, {"projected_at_bats": float("nan")},
    {"projected_at_bats": 3.0}, {"distinct_batting_games": None}, {"distinct_batting_games": 0},
    {"source_refs": ()}, {"preview_schema_version": LEGACY_PREVIEW_SCHEMA_VERSION},
]


@pytest.mark.parametrize("change", INVALID_QUALIFICATION)
def test_in_memory_qualified_hits_requires_persistable_provenance(change):
    with pytest.raises(ValueError):
        replace(_hits(), **change)


@pytest.mark.parametrize("change", [v for v in INVALID_QUALIFICATION if "preview_schema_version" not in v])
def test_current_saved_claim_cannot_bypass_row_contract(tmp_path, change):
    _persist_raw(tmp_path, {**_hits().to_dict(), **change})
    with pytest.raises(ValueError):
        load_preview_board(tmp_path, DAY)


@pytest.mark.parametrize("field", ["season_at_bats", "season_hits", "distinct_batting_games", "projected_at_bats"])
def test_boolean_is_not_a_baseball_count(field):
    with pytest.raises(ValueError):
        replace(_hits(), **{field: True})


@pytest.mark.parametrize("prefix", ["cv-ledger-season:", "cv-ledger-coverage:", "cv_ab_projection_provenance=", "hits_feature_provenance=", "features.season_hits="])
def test_missing_provenance_reference_cannot_qualify(prefix):
    row = _hits()
    with pytest.raises(ValueError):
        replace(row, source_refs=tuple(ref for ref in row.source_refs if not ref.startswith(prefix)))


@pytest.mark.parametrize("case", ["coverage-hash", "projection-json", "projection-version", "projection-lineup", "conflicting-ref", "duplicate-json-key"])
def test_provenance_internals_are_validated(case):
    row = _hits()
    prefix = "cv_ab_projection_provenance="
    original = next(ref for ref in row.source_refs if ref.startswith(prefix))
    projection = json.loads(original[len(prefix):])
    refs = row.source_refs
    if case == "coverage-hash":
        refs = tuple(ref if not ref.startswith("cv-ledger-coverage:") else "cv-ledger-coverage:sha256:invalid" for ref in refs)
    elif case == "conflicting-ref":
        refs = (*refs, "cv-ledger-season:sha256:" + "0" * 64)
    else:
        if case == "projection-version":
            projection["model_version"] = "cv_ab_projection_v1"
        if case == "projection-lineup":
            projection["batting_order_position"] = 0
        encoded = json.dumps(projection)
        if case == "projection-json":
            encoded = "[]"
        if case == "duplicate-json-key":
            encoded = encoded[:-1] + ', "model_version":"cv_ab_projection_v2"}'
        refs = tuple(prefix + encoded if ref == original else ref for ref in refs)
    with pytest.raises(ValueError):
        replace(row, source_refs=refs)


@pytest.mark.parametrize("change", [{"season_source": "mlb_statsapi"}, {"season_games_played": 130},
    {"projection_model_version": "cv_ab_projection_v1"}, {"game_id": "823185"},
    {"player_id": "700002"}, {"batting_order_position": True}])
def test_contradictory_feature_lineage_cannot_qualify(change):
    row = _hits()
    prefix = "hits_feature_provenance="
    original = next(ref for ref in row.source_refs if ref.startswith(prefix))
    encoded = json.dumps({**json.loads(original[len(prefix):]), **change})
    with pytest.raises(ValueError):
        replace(row, source_refs=tuple(prefix + encoded if ref == original else ref for ref in row.source_refs))


@pytest.mark.parametrize("old_summary", [False, True])
def test_original_high_saved_provider_board_is_blocked_without_rewriting(tmp_path, old_summary):
    board, summary_path = _persist_raw(tmp_path, _legacy_hits(), old_summary=old_summary)
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in (board, summary_path)]
    loaded, summary = load_preview_board(tmp_path, DAY)
    row = loaded[0]
    assert row.prediction_status == "BLOCKED"
    assert row.block_reason == LEGACY_HITS_PROVENANCE_UNQUALIFIED
    assert row.model_probability is None and row.probability_market_independence == "NOT_TESTED"
    assert row.season_source is None and row.ab_projection_version is None
    assert row.season_aggregate_hash is None
    assert summary["hits_qualified"] == summary["usable_prediction_rows"] == 0
    assert summary["hits_blocked"] == 1
    assert summary["market_status"]["batter_hits"]["usable_rows"] == 0
    assert summary["source_preview_schema_version"] == LEGACY_PREVIEW_SCHEMA_VERSION
    assert before == [(p.read_bytes(), p.stat().st_mtime_ns) for p in (board, summary_path)]


def test_v1_cannot_be_promoted_even_when_it_has_some_sovereign_fields(tmp_path):
    data = _hits().to_dict()
    data["preview_schema_version"] = LEGACY_PREVIEW_SCHEMA_VERSION
    _persist_raw(tmp_path, data)
    assert load_preview_board(tmp_path, DAY)[0][0].block_reason == LEGACY_HITS_PROVENANCE_UNQUALIFIED


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("change", [{"research_only": False}, {"eligible_for_betting": True}, {"kelly_eligible": True}, {"approval_status": "approved"}])
def test_saved_admission_preserves_safety_flags(tmp_path, legacy, change):
    data = _legacy_hits() if legacy else _hits().to_dict()
    _persist_raw(tmp_path, {**data, **change})
    with pytest.raises(ValueError):
        load_preview_board(tmp_path, DAY)


def test_legacy_summary_is_checked_before_presentation_changes(tmp_path):
    _, path = _persist_raw(tmp_path, _legacy_hits())
    summary = json.loads(path.read_text())
    summary["hits_qualified"] = 0
    path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="summary content mismatch"):
        load_preview_board(tmp_path, DAY)


def test_legacy_byte_hash_is_checked_before_adaptation(tmp_path):
    board, _ = _persist_raw(tmp_path, _legacy_hits())
    board.write_bytes(board.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="integrity mismatch"):
        load_preview_board(tmp_path, DAY)


def test_writer_revalidates_before_any_publication(tmp_path):
    row = _hits()
    object.__setattr__(row, "season_source", None)
    with pytest.raises(ValueError):
        write_preview([row], DAY, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_legacy_hr_and_source_status_remain_separate(tmp_path):
    hr = preview_hr_prediction(_hr(), day=DAY, source_ref="fixture:hr")
    marker = unavailable_row(DAY, "batter_home_runs", "SOURCE_MISSING")
    raw = [_legacy_hits(), *[replace(row, preview_schema_version=LEGACY_PREVIEW_SCHEMA_VERSION).to_dict() for row in (hr, marker)]]
    _persist_raw(tmp_path, raw)
    rows, summary = load_preview_board(tmp_path, DAY)
    assert rows[1] == replace(hr, preview_schema_version=LEGACY_PREVIEW_SCHEMA_VERSION)
    assert rows[2] == replace(marker, preview_schema_version=LEGACY_PREVIEW_SCHEMA_VERSION)
    assert summary["hr_market_contaminated"] == summary["usable_prediction_rows"] == 1
    assert summary["hits_qualified"] == 0 and summary["source_status_rows"] == 1
    assert summary["hr_warning"] == HR_WARNING


def test_streamlit_saved_legacy_hits_is_blocked_and_explained(tmp_path):
    from streamlit.testing.v1 import AppTest
    output = tmp_path / "outputs"
    _persist_raw(output / "runtime/mlb/research", _legacy_hits())
    app = AppTest.from_string(
        "from pathlib import Path\nfrom courtvision.streamlit_mlb_preview import render_mlb_research_preview\n"
        f"render_mlb_research_preview(Path({str(output)!r}), {DAY!r})\n"
    ).run(timeout=30)
    assert not app.exception
    assert app.dataframe[0].value.iloc[0]["Status"] == "Blocked"
    assert app.dataframe[0].value.iloc[0]["CV Probability"] is None
    assert "Qualified Research" not in {item.value for item in app.text}
    assert any("Legacy Hits provenance is not sovereign" in item.value for item in app.text)
    assert any(HR_WARNING in item.value for item in app.warning)
    assert json.loads(app.json[-1].value)["model_probability"] is None


def test_streamlit_sovereign_identity_is_visible_with_full_raw_provenance(tmp_path):
    row = _hits()
    app = _preview_app(tmp_path, [row])
    assert not app.exception
    values = {item.value for item in app.text}
    assert {"CourtVision Game Fact Ledger", "cv_ab_projection_v2", row.season_aggregate_hash[:12] + "..."} <= values
    assert json.loads(app.json[-1].value)["source_refs"] == list(row.source_refs)
