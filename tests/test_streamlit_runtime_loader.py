import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import courtvision_streamlit_app as app


PREDICTION_DATE = "2026-06-01"


def _runtime_loader():
    return getattr(
        app.load_runtime_prediction_cached,
        "__wrapped__",
        app.load_runtime_prediction_cached,
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _normal_pick(player: str, *, market: str = "player_points") -> dict[str, object]:
    return {
        "prediction_date": PREDICTION_DATE,
        "market_type": market,
        "entity_name": player,
        "team": "BOS",
        "opponent": "NYK",
        "selection": "OVER",
        "sportsbook_line": 21.5,
        "model_projection": 24.2,
        "edge": 2.7,
        "edge_abs": 2.7,
        "confidence": 0.62,
        "quality_score": 88.0,
        "odds": -110,
        "kelly_eligible": True,
        "final_decision": "BET_READY",
    }


@pytest.mark.parametrize(
    ("status", "lifecycle_status", "expected_level", "message_fragment"),
    [
        ("SUCCESS", "PASS", "success", "publication completed"),
        ("SUCCESS", "DISABLED", "info", "lifecycle disabled"),
        ("SUCCESS", "DEGRADED", "warning", "lifecycle status is DEGRADED"),
        ("FAILED", "NOT_STARTED", "error", "run failed"),
        (
            "PROTECTED_NO_OP",
            "PROTECTED_NO_OP",
            "info",
            "no files were changed",
        ),
        (
            "NO_ELIGIBLE_PREDICTIONS",
            "PASS",
            "info",
            "eligibility rules",
        ),
    ],
)
def test_prediction_application_outcome_classification(
    status: str,
    lifecycle_status: str,
    expected_level: str,
    message_fragment: str,
) -> None:
    level, message = app.classify_prediction_application_outcome(
        status=status,
        lifecycle_status=lifecycle_status,
        run_id="run-123",
    )

    assert level == expected_level
    assert message_fragment in message
    assert "run-123" in message


class _FakeStreamlit:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def markdown(self, *args: Any, **kwargs: Any) -> None:
        self._record("markdown", *args, **kwargs)

    def json(self, *args: Any, **kwargs: Any) -> None:
        self._record("json", *args, **kwargs)

    def code(self, *args: Any, **kwargs: Any) -> None:
        self._record("code", *args, **kwargs)

    def caption(self, *args: Any, **kwargs: Any) -> None:
        self._record("caption", *args, **kwargs)


def test_streamlit_runtime_loader_loads_normal_boards(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    runtime_root = out_dir / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    _write_csv(operator / f"elite_board_{PREDICTION_DATE}.csv", [_normal_pick("Elite Player")])
    _write_csv(
        operator / f"full_market_board_{PREDICTION_DATE}.csv",
        [_normal_pick("Full Player"), _normal_pick("Assist Player", market="player_assists")],
    )
    _write_csv(operator / f"sgp_board_{PREDICTION_DATE}.csv", [_normal_pick("SGP Player")])
    _write_json(
        diagnostics / f"board_diagnostics_{PREDICTION_DATE}.json",
        {"board_counts": {"rejected": 4, "qualified_pool": 2}},
    )
    _write_json(diagnostics / f"market_coverage_{PREDICTION_DATE}.json", {"markets": 2})
    _write_text(operator / f"daily_summary_{PREDICTION_DATE}.txt", "Pending grading count: 0")

    payload = _runtime_loader()(str(out_dir), PREDICTION_DATE, 0)

    assert len(payload["elite_props"]) == 1
    assert len(payload["full_market_props"]) == 2
    assert len(payload["sgp_props"]) == 1
    assert payload["summary"]["elite_count"] == 1
    assert payload["summary"]["full_market_count"] == 2
    assert payload["summary"]["rejected_count"] == 4
    assert payload["summary"]["data_status"] == (
        f"Runtime artifacts loaded for {PREDICTION_DATE}."
    )
    assert str(runtime_root) not in payload["summary"]["data_status"]
    assert app.runtime_status_message(payload["summary"]) == (
        f"Runtime artifacts loaded for {PREDICTION_DATE}."
    )
    assert app.payload_has_board_rows(payload) is True


def test_runtime_status_message_removes_local_artifact_paths(tmp_path: Path) -> None:
    runtime_root = tmp_path / "outputs" / "runtime"
    summary = {
        "prediction_date": PREDICTION_DATE,
        "data_status": f"Loaded runtime artifacts for {PREDICTION_DATE} from {runtime_root}",
    }

    message = app.runtime_status_message(summary)

    assert message == f"Runtime artifacts loaded for {PREDICTION_DATE}."
    assert str(runtime_root) not in message
    assert "Loaded runtime artifacts" not in message


def test_raw_debug_ui_helpers_require_explicit_env_flag(monkeypatch) -> None:
    monkeypatch.delenv("COURTVISION_SHOW_RAW_UI_DEBUG", raising=False)
    assert app.raw_ui_debug_enabled() is False

    monkeypatch.setenv("COURTVISION_SHOW_RAW_UI_DEBUG", "1")
    assert app.raw_ui_debug_enabled() is True


def test_completion_raw_audit_text_not_rendered_by_default(monkeypatch) -> None:
    fake_st = _FakeStreamlit()
    monkeypatch.delenv("COURTVISION_SHOW_RAW_UI_DEBUG", raising=False)
    monkeypatch.setattr(app, "st", fake_st)

    app._render_completion_state_raw(
        {
            "completion_state_audit_json": {"status": "COMPLETE"},
            "completion_state_audit_text": "raw completion audit",
        }
    )

    assert fake_st.calls == []


def test_completion_raw_audit_text_renders_only_in_debug_mode(monkeypatch) -> None:
    fake_st = _FakeStreamlit()
    monkeypatch.setenv("COURTVISION_SHOW_RAW_UI_DEBUG", "1")
    monkeypatch.setattr(app, "st", fake_st)

    app._render_completion_state_raw(
        {
            "completion_state_audit_json": {"status": "COMPLETE"},
            "completion_state_audit_text": "raw completion audit",
        }
    )

    rendered_text = "\n".join(
        str(arg)
        for _name, args, _kwargs in fake_st.calls
        for arg in args
    )
    assert "Completion audit raw JSON/text" in rendered_text
    assert any(name == "json" for name, _args, _kwargs in fake_st.calls)
    assert any(name == "code" for name, _args, _kwargs in fake_st.calls)


def test_streamlit_runtime_loader_missing_and_empty_files_are_nonfatal(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    empty_csv = (
        out_dir
        / "runtime"
        / "operator"
        / f"under_visibility_board_{PREDICTION_DATE}.csv"
    )
    empty_csv.parent.mkdir(parents=True, exist_ok=True)
    empty_csv.write_text("", encoding="utf-8")

    payload = _runtime_loader()(str(out_dir), PREDICTION_DATE, 1)

    assert payload["elite_props"].empty
    assert payload["full_market_props"].empty
    assert payload["under_visibility_board"].empty
    under_record = payload["under_visibility_records"]["under_visibility_board"]
    assert under_record["status"] == "empty"
    assert under_record["error"] == "CSV has no header or data rows."
    assert app.payload_has_board_rows(payload) is False


def test_under_visibility_artifacts_load_shadow_only_read_only(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    runtime_root = out_dir / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    _write_csv(
        operator / f"under_visibility_board_{PREDICTION_DATE}.csv",
        [
            {
                "prediction_date": PREDICTION_DATE,
                "entity_name": "Under Candidate",
                "market_type": "player_points",
                "selection": "UNDER",
                "under_visibility_lane": "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY",
                "kelly_eligible": True,
                "final_decision": "BET_READY",
            }
        ],
    )
    _write_text(
        operator / f"under_visibility_report_{PREDICTION_DATE}.txt",
        "UNDER Visibility Board - Shadow Only",
    )
    _write_json(
        diagnostics / f"under_visibility_board_{PREDICTION_DATE}.json",
        {
            "prediction_date": PREDICTION_DATE,
            "shadow_only": True,
            "affects_betability": False,
            "elite_promotion": False,
            "kelly_promotion": False,
        },
    )

    payload = _runtime_loader()(str(out_dir), PREDICTION_DATE, 2)
    contract = app.under_visibility_betting_contract(payload)

    assert len(payload["under_visibility_board"]) == 1
    assert "Shadow Only" in payload["under_visibility_report_text"]
    assert payload["under_visibility_diagnostics"]["shadow_only"] is True
    assert payload["under_visibility_shadow_only"] is True
    assert payload["under_visibility_affects_betting"] is False
    assert payload["under_visibility_warning"] == app.UNDER_VISIBILITY_WARNING
    assert contract["shadow_only"] is True
    assert contract["read_only"] is True
    assert contract["affects_betability"] is False
    assert contract["affects_elite"] is False
    assert contract["affects_kelly"] is False
    assert app.under_visibility_contract_is_safe(contract) is True
    assert app.payload_has_board_rows(payload) is False


def test_under_visibility_malformed_json_is_nonfatal(tmp_path: Path) -> None:
    out_dir = tmp_path / "outputs"
    diagnostics = out_dir / "runtime" / "diagnostics"
    diagnostics.mkdir(parents=True)
    (diagnostics / f"under_visibility_board_{PREDICTION_DATE}.json").write_text(
        "{not valid json",
        encoding="utf-8",
    )

    payload = _runtime_loader()(str(out_dir), PREDICTION_DATE, 3)

    assert payload["under_visibility_diagnostics"] == {}
    record = payload["under_visibility_records"]["under_visibility_diagnostics"]
    assert record["status"] == "error"
    assert "JSONDecodeError" in record["error"]


def test_under_visibility_does_not_affect_betting_status_elite_kelly_or_final_decision(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "outputs"
    runtime_root = out_dir / "runtime"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"

    _write_csv(operator / f"elite_board_{PREDICTION_DATE}.csv", [_normal_pick("Elite Player")])
    _write_csv(
        operator / f"under_visibility_board_{PREDICTION_DATE}.csv",
        [
            {
                "prediction_date": PREDICTION_DATE,
                "entity_name": "Shadow Under",
                "market_type": "player_points",
                "selection": "UNDER",
                "source_lane": "Lane A",
                "under_visibility_lane": "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY",
                "kelly_eligible": True,
                "final_decision": "BET_READY",
            }
        ],
    )
    _write_json(
        diagnostics / f"under_visibility_board_{PREDICTION_DATE}.json",
        {
            "shadow_only": False,
            "affects_betability": True,
            "elite_promotion": True,
            "kelly_promotion": True,
            "pick_history_written": True,
        },
    )

    payload = _runtime_loader()(str(out_dir), PREDICTION_DATE, 4)
    contract = app.under_visibility_betting_contract(payload)

    assert len(payload["elite_props"]) == 1
    assert payload["summary"]["elite_count"] == 1
    assert payload["summary"].get("kelly_eligible_count") is None
    assert payload["summary"].get("final_decision") is None
    assert contract["affects_lane_a"] is False
    assert contract["affects_betability"] is False
    assert contract["affects_elite"] is False
    assert contract["affects_kelly"] is False
    assert contract["affects_final_decision"] is False
    assert contract["affects_staking"] is False
