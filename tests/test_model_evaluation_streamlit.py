from __future__ import annotations

import ast
import csv
from datetime import date
from pathlib import Path
from typing import Any

import model_evaluation_streamlit_app as app
from courtvision.evaluation.model_metrics import calculate_evaluation_metrics
from courtvision.evaluation.model_sources import (
    LEGACY_PICK_HISTORY_COLUMNS,
    SourceState,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_FILE = PROJECT_ROOT / "model_evaluation_streamlit_app.py"


def _source_text() -> str:
    return APP_FILE.read_text(encoding="utf-8")


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in LEGACY_PICK_HISTORY_COLUMNS}
    row.update(
        {
            "prediction_date": "2026-05-13",
            "player_name": "Sample Player",
            "player_id": "p-1",
            "team": "TOR",
            "opponent": "BOS",
            "game_id": "g-1",
            "market": "player_points",
            "selection": "over",
            "line": "20.5",
            "projection": "22.0",
            "edge": "1.5",
            "odds": "",
            "confidence": "0.75",
            "result_status": "pending",
        }
    )
    row.update(overrides)
    return row


def _write_source(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEGACY_PICK_HISTORY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_research_only_banner_and_single_phase1_source_are_permanent() -> None:
    source = _source_text()
    assert (
        "Research-only legacy observational evaluation — not betting guidance"
        in source
    )
    assert "PHASE1_SOURCE_PATH" in source
    for forbidden_source in (
        "prediction_history.csv",
        "result_feedback.csv",
        "market_shadow_history.csv",
        "paper_kelly_history.csv",
        "performance_summary.csv",
    ):
        assert forbidden_source not in source


def test_app_has_no_operational_imports_or_mutating_controls() -> None:
    tree = ast.parse(_source_text())
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_import_fragments = (
        "promotion",
        "betting",
        "bankroll",
        "kelly",
        "execution",
        "fitting",
        "runtime",
    )
    assert not {
        module
        for module in imported_modules
        if any(fragment in module.casefold() for fragment in forbidden_import_fragments)
    }

    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called_attributes.intersection(
        {
            "button",
            "download_button",
            "file_uploader",
            "form_submit_button",
            "data_editor",
            "experimental_data_editor",
        }
    )
    assert "CourtVisionAI" not in _source_text()


def test_rendering_delegates_metrics_to_model_metrics_module() -> None:
    tree = ast.parse(_source_text())
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "courtvision.evaluation.model_metrics"
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "calculate_evaluation_metrics" in imported_names
    assert "calculate_evaluation_metrics" in called_names
    assert "select_recent_slates" in imported_names


def test_load_boundary_handles_missing_empty_and_malformed_sources(
    tmp_path: Path,
) -> None:
    missing = app.load_dashboard_data(tmp_path / "missing" / "pick_history.csv")
    assert missing.error_message is None
    assert missing.source is not None
    assert missing.source.state is SourceState.MISSING

    empty_path = tmp_path / "pick_history.csv"
    empty_path.write_text("", encoding="utf-8")
    empty = app.load_dashboard_data(empty_path)
    assert empty.error_message is None
    assert empty.source is not None
    assert empty.source.state is SourceState.EMPTY

    empty_path.write_text("wrong,columns\n1,2\n", encoding="utf-8")
    malformed = app.load_dashboard_data(empty_path)
    assert malformed.source is None
    assert malformed.error_kind == "SourceSchemaError"
    assert "exact 32-column" in (malformed.error_message or "")


def test_no_decisive_or_roi_eligible_records_are_nonfatal(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    _write_source(path, [_row()])

    state = app.load_dashboard_data(path)
    assert state.source is not None
    metrics = calculate_evaluation_metrics(state.source.records)

    assert metrics.hit_rate.hit_rate is None
    assert metrics.flat_unit_roi.roi is None
    assert app._format_percentage(metrics.hit_rate.hit_rate) == "N/A"
    assert app._format_percentage(metrics.flat_unit_roi.roi) == "N/A"


class _StateStreamlit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any, **_kwargs: Any) -> None:
        self.calls.append((name, args))

    set_page_config = lambda self, *a, **kw: self._record("set_page_config", *a, **kw)
    warning = lambda self, *a, **kw: self._record("warning", *a, **kw)
    title = lambda self, *a, **kw: self._record("title", *a, **kw)
    caption = lambda self, *a, **kw: self._record("caption", *a, **kw)
    error = lambda self, *a, **kw: self._record("error", *a, **kw)
    info = lambda self, *a, **kw: self._record("info", *a, **kw)
    code = lambda self, *a, **kw: self._record("code", *a, **kw)


def test_main_renders_banner_before_nonfatal_missing_state(
    tmp_path: Path, monkeypatch
) -> None:
    fake_streamlit = _StateStreamlit()
    monkeypatch.setattr(app, "st", fake_streamlit)

    app.main(
        as_of_date=date(2026, 8, 3),
        source_path=tmp_path / "missing" / "pick_history.csv",
    )

    assert fake_streamlit.calls[1] == ("warning", (app.RESEARCH_ONLY_BANNER,))
    assert any(name == "error" for name, _args in fake_streamlit.calls)
