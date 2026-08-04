from __future__ import annotations

import ast
import csv
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import model_evaluation_streamlit_app as app
import pytest

from courtvision.evaluation.model_metrics import calculate_evaluation_metrics
from courtvision.evaluation.model_records import (
    FEEDBACK_EVALUATION_POPULATION,
)
from courtvision.evaluation.model_sources import (
    FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS,
    FEEDBACK_REQUIRED_COLUMNS,
    LEGACY_PICK_HISTORY_COLUMNS,
    SourceState,
    load_feedback_records,
    load_phase1_records,
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


def _feedback_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in FEEDBACK_REQUIRED_COLUMNS}
    row.update({column: "" for column in FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS})
    row.update(
        {
            "prediction_date": "2026-05-13",
            "market_type": "player_points",
            "entity_name": "Feedback Player",
            "team": "TOR",
            "opponent": "BOS",
            "selection": "over",
            "sportsbook_line": "20.5",
            "actual_value": "24",
            "result": "win",
            "graded_result": "win",
            "is_win": 1,
            "is_push": 0,
            "is_loss": 0,
        }
    )
    row.update(overrides)
    if "grade_key" not in overrides:
        row["grade_key"] = "|".join(
            str(row[column])
            for column in (
                "prediction_date",
                "market_type",
                "entity_name",
                "selection",
                "sportsbook_line",
            )
        )
    return row


def _write_feedback(path: Path, rows: list[dict[str, object]]) -> None:
    columns = (*FEEDBACK_REQUIRED_COLUMNS, *FEEDBACK_OPTIONAL_ANALYTICAL_COLUMNS)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(
            {column: row.get(column, "") for column in columns} for row in rows
        )


def test_research_only_banner_and_exact_population_choices_are_permanent() -> None:
    source = _source_text()
    assert (
        "Research-only legacy observational evaluation — not betting guidance"
        in source
    )
    assert app.POPULATION_OPTIONS == (
        "Legacy elite picks",
        "All graded feedback",
    )
    assert "PHASE1_SOURCE_PATH" in source
    assert "FEEDBACK_SOURCE_PATH" in source
    for forbidden_source in (
        "prediction_history.csv",
        "market_shadow_history.csv",
        "paper_kelly_history.csv",
        "performance_summary.csv",
        "official_settlement.csv",
        "mlb_result_feedback.csv",
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
    missing = app.load_dashboard_data(
        app.ELITE_POPULATION_LABEL,
        tmp_path / "missing" / "pick_history.csv",
    )
    assert missing.error_message is None
    assert missing.source is not None
    assert missing.source.state is SourceState.MISSING

    empty_path = tmp_path / "pick_history.csv"
    empty_path.write_text("", encoding="utf-8")
    empty = app.load_dashboard_data(app.ELITE_POPULATION_LABEL, empty_path)
    assert empty.error_message is None
    assert empty.source is not None
    assert empty.source.state is SourceState.EMPTY

    empty_path.write_text("wrong,columns\n1,2\n", encoding="utf-8")
    malformed = app.load_dashboard_data(app.ELITE_POPULATION_LABEL, empty_path)
    assert malformed.source is None
    assert malformed.error_kind == "SourceSchemaError"
    assert "exact 32-column" in (malformed.error_message or "")


def test_no_decisive_or_roi_eligible_records_are_nonfatal(tmp_path: Path) -> None:
    path = tmp_path / "pick_history.csv"
    _write_source(path, [_row()])

    state = app.load_dashboard_data(app.ELITE_POPULATION_LABEL, path)
    assert state.source is not None
    metrics = calculate_evaluation_metrics(state.source.records)

    assert metrics.hit_rate.hit_rate is None
    assert metrics.flat_unit_roi.roi is None
    assert app._format_percentage(metrics.hit_rate.hit_rate) == "N/A"
    assert app._format_percentage(metrics.flat_unit_roi.roi) == "N/A"


class _StateStreamlit:
    def __init__(self, selected_population: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.selectbox_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.selected_population = selected_population
        self.sidebar = self

    def _record(self, name: str, *args: Any, **_kwargs: Any) -> None:
        self.calls.append((name, args))

    set_page_config = lambda self, *a, **kw: self._record("set_page_config", *a, **kw)
    warning = lambda self, *a, **kw: self._record("warning", *a, **kw)
    title = lambda self, *a, **kw: self._record("title", *a, **kw)
    caption = lambda self, *a, **kw: self._record("caption", *a, **kw)
    error = lambda self, *a, **kw: self._record("error", *a, **kw)
    info = lambda self, *a, **kw: self._record("info", *a, **kw)
    code = lambda self, *a, **kw: self._record("code", *a, **kw)

    def selectbox(self, *args: Any, **kwargs: Any) -> str | None:
        self.selectbox_calls.append((args, kwargs))
        self._record("selectbox", *args, **kwargs)
        if args and args[0] == "Evaluation population":
            return self.selected_population
        raise AssertionError(f"Unexpected selectbox call: {args!r}")


def test_main_renders_banner_before_nonfatal_missing_state(
    tmp_path: Path, monkeypatch
) -> None:
    fake_streamlit = _StateStreamlit(app.ELITE_POPULATION_LABEL)
    monkeypatch.setattr(app, "st", fake_streamlit)

    app.main(
        as_of_date=date(2026, 8, 3),
        elite_source_path=tmp_path / "missing" / "pick_history.csv",
    )

    assert fake_streamlit.calls[1] == ("warning", (app.RESEARCH_ONLY_BANNER,))
    assert any(name == "error" for name, _args in fake_streamlit.calls)


def test_population_selector_has_no_default_and_loads_nothing(
    monkeypatch,
) -> None:
    fake_streamlit = _StateStreamlit(selected_population=None)
    monkeypatch.setattr(app, "st", fake_streamlit)
    monkeypatch.setattr(
        app,
        "load_phase1_records",
        lambda _path: pytest.fail("elite source must not load before selection"),
    )
    monkeypatch.setattr(
        app,
        "load_feedback_records",
        lambda _path: pytest.fail("feedback source must not load before selection"),
    )
    monkeypatch.setattr(
        app,
        "calculate_evaluation_metrics",
        lambda _records: pytest.fail("metrics must not run before selection"),
    )

    app.main(as_of_date=date(2026, 8, 3))

    assert len(fake_streamlit.selectbox_calls) == 1
    args, kwargs = fake_streamlit.selectbox_calls[0]
    assert args == ("Evaluation population", app.POPULATION_OPTIONS)
    assert kwargs["index"] is None
    assert kwargs["placeholder"] == "Select an evaluation population"
    assert any(name == "info" for name, _args in fake_streamlit.calls)


@pytest.mark.parametrize(
    ("selected_population", "expected_loader"),
    [
        (app.ELITE_POPULATION_LABEL, "elite"),
        (app.FEEDBACK_POPULATION_LABEL, "feedback"),
    ],
)
def test_exactly_one_selected_population_loader_is_invoked(
    tmp_path: Path,
    monkeypatch,
    selected_population: str,
    expected_loader: str,
) -> None:
    fake_streamlit = _StateStreamlit(selected_population)
    monkeypatch.setattr(app, "st", fake_streamlit)
    calls = {"elite": 0, "feedback": 0}

    def load_elite(path: str | Path):
        calls["elite"] += 1
        return load_phase1_records(path)

    def load_feedback(path: str | Path):
        calls["feedback"] += 1
        return load_feedback_records(path)

    monkeypatch.setattr(app, "load_phase1_records", load_elite)
    monkeypatch.setattr(app, "load_feedback_records", load_feedback)

    app.main(
        as_of_date=date(2026, 8, 3),
        elite_source_path=tmp_path / "missing" / "pick_history.csv",
        feedback_source_path=tmp_path / "missing" / "result_feedback.csv",
    )

    assert calls[expected_loader] == 1
    assert sum(calls.values()) == 1
    warning_messages = [args[0] for name, args in fake_streamlit.calls if name == "warning"]
    assert app.POPULATION_OVERLAP_WARNING in warning_messages


def test_feedback_load_boundary_handles_missing_empty_and_malformed(
    tmp_path: Path,
) -> None:
    missing = app.load_dashboard_data(
        app.FEEDBACK_POPULATION_LABEL,
        tmp_path / "missing" / "result_feedback.csv",
    )
    assert missing.error_message is None
    assert missing.source is not None
    assert missing.source.state is SourceState.MISSING

    path = tmp_path / "result_feedback.csv"
    path.write_text("", encoding="utf-8")
    empty = app.load_dashboard_data(app.FEEDBACK_POPULATION_LABEL, path)
    assert empty.error_message is None
    assert empty.source is not None
    assert empty.source.state is SourceState.EMPTY

    path.write_text("grade_key,result\nkey,win\n", encoding="utf-8")
    malformed = app.load_dashboard_data(app.FEEDBACK_POPULATION_LABEL, path)
    assert malformed.source is None
    assert malformed.error_kind == "SourceSchemaError"
    assert "missing required" in (malformed.error_message or "")


def test_application_contains_no_record_tuple_union() -> None:
    tree = ast.parse(_source_text())

    def references_records(node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Attribute) and child.attr == "records"
            for child in ast.walk(node)
        )

    record_additions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Add)
        and references_records(node)
    ]
    record_concats = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"concat", "extend"}
        and references_records(node)
    ]
    assert record_additions == []
    assert record_concats == []


def test_mixed_population_rendering_is_rejected(tmp_path: Path) -> None:
    elite_path = tmp_path / "pick_history.csv"
    feedback_path = tmp_path / "result_feedback.csv"
    _write_source(elite_path, [_row(result_status="hit", odds="-110")])
    _write_feedback(feedback_path, [_feedback_row(odds="-110")])
    elite = load_phase1_records(elite_path)
    feedback = load_feedback_records(feedback_path)
    mixed = replace(
        elite,
        records=(elite.records[0], feedback.records[0]),
    )

    with pytest.raises(ValueError, match="exactly the selected"):
        app.render_dashboard(
            mixed,
            as_of_date=date(2026, 8, 3),
            expected_population=FEEDBACK_EVALUATION_POPULATION,
        )


class _CoverageStreamlit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any, **_kwargs: Any) -> None:
        self.calls.append((name, args))

    def columns(self, count: int) -> list["_CoverageStreamlit"]:
        return [self] * count

    header = lambda self, *a, **kw: self._record("header", *a, **kw)
    code = lambda self, *a, **kw: self._record("code", *a, **kw)
    metric = lambda self, *a, **kw: self._record("metric", *a, **kw)
    caption = lambda self, *a, **kw: self._record("caption", *a, **kw)
    warning = lambda self, *a, **kw: self._record("warning", *a, **kw)
    success = lambda self, *a, **kw: self._record("success", *a, **kw)


def test_feedback_coverage_fields_and_warnings_are_rendered(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "result_feedback.csv"
    _write_feedback(
        path,
        [
            _feedback_row(
                result="unresolved",
                graded_result="unresolved",
                is_win=0,
                odds="",
                confidence="",
                edge="",
                player_id="",
                canonical_player_id="",
                game_id="",
                model_projection="",
                projection="",
            )
        ],
    )
    source = load_feedback_records(path)
    fake_streamlit = _CoverageStreamlit()
    monkeypatch.setattr(app, "st", fake_streamlit)

    app._render_source_and_coverage(source, as_of_date=date(2026, 8, 3))
    app._render_data_quality(source, as_of_date=date(2026, 8, 3))

    metric_labels = [args[0] for name, args in fake_streamlit.calls if name == "metric"]
    assert {
        "Unique grade keys",
        "Duplicate groups",
        "Duplicate source rows",
        "Duplicate rows excluded",
        "Conflicting grade keys",
        "Final graded",
        "Unresolved",
        "Unsupported",
        "Actual-value coverage",
        "Valid-odds coverage",
        "Prediction-value coverage",
        "Confidence coverage",
        "Edge coverage",
        "Participant-ID coverage",
        "Event-ID coverage",
    }.issubset(metric_labels)
    warning_messages = [args[0] for name, args in fake_streamlit.calls if name == "warning"]
    assert any("Unresolved rows excluded from decision metrics: 1" in message for message in warning_messages)
    assert any("Missing or invalid odds excluded without substitution: 1" in message for message in warning_messages)
    assert any("Missing confidence values appear in Unknown: 1" in message for message in warning_messages)
    assert any("Missing prediction values reduce coverage: 1" in message for message in warning_messages)
