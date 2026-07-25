from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".claude",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "outputs",
    "test_outputs",
    "tests",
    "fixtures",
    "archive",
    "archived",
}


def _production_python_files() -> dict[str, str]:
    files: dict[str, str] = {}
    roots = [
        ROOT / "courtvision_ai.py",
        ROOT / "courtvision_streamlit_app.py",
        ROOT / "courtvision",
        ROOT / "scripts",
    ]
    for source_root in roots:
        candidates = (
            [source_root]
            if source_root.is_file()
            else source_root.rglob("*.py")
        )
        for path in candidates:
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            files[relative.as_posix()] = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
    return files


def test_public_live_entrypoints_delegate_to_prediction_application() -> None:
    sources = _production_python_files()
    assert "run_nba_prediction_application(" in sources["courtvision_ai.py"]
    assert "run_nba_prediction_application(" in sources[
        "courtvision_streamlit_app.py"
    ]
    assert "canonical_main(canonical_args)" in sources["scripts/run_daily.py"]
    assert "PredictionApplicationService(" in sources[
        "courtvision/sports/mlb/training/hr_research_baseline.py"
    ]


def test_no_unapproved_script_calls_courtvision_predict_directly() -> None:
    sources = _production_python_files()
    offenders = {
        path
        for path, source in sources.items()
        if path.startswith("scripts/")
        and (
            "CourtVisionAI.predict(" in source
            or ".predict(args.prediction_date)" in source
            or ".predict(prediction_date_text)" in source
        )
    }
    assert offenders == set()


def test_downstream_workflows_cannot_call_prediction_application() -> None:
    sources = _production_python_files()
    downstream = {
        "scripts/backfill_grading.py",
        "scripts/grade_market_shadow_history.py",
        "scripts/history_tracking.py",
        "scripts/prefill_actual_feedback.py",
        "scripts/update_game_results_history.py",
    }
    offenders = {
        path
        for path in downstream
        if "PredictionApplicationService" in sources[path]
        or "run_nba_prediction_application" in sources[path]
        or "generate_daily_research_predictions" in sources[path]
    }
    assert offenders == set()
    assert all(
        "courtvision.operations" in sources[path] for path in downstream
    )


def test_canonical_prediction_writers_are_explicitly_allowlisted() -> None:
    sources = _production_python_files()
    artifact_tokens = (
        'f"elite_board_',
        'f"full_market_board_',
        '"predictions.csv"',
    )
    # Approved implementation/publication adapters. Other modules may read
    # these names, but cannot pair them with direct file writes.
    approved_writers = {
        "courtvision_ai.py",
        "courtvision/prediction/publication.py",
        "courtvision/pipeline/runner.py",  # deprecated compatibility adapter
        "courtvision/sports/mlb/training/hr_research_baseline.py",
    }
    offenders: set[str] = set()
    for path, source in sources.items():
        if path in approved_writers:
            continue
        for line in source.splitlines():
            if not any(token in line for token in artifact_tokens):
                continue
            if any(
                write_call in line
                for write_call in (
                    ".to_csv(",
                    ".write_text(",
                    "open(",
                    "_write_csv_create_once(",
                )
            ):
                offenders.add(path)
    assert offenders == set()


def test_lifecycle_identity_is_application_scoped_and_sport_structured() -> None:
    publication = _production_python_files()[
        "courtvision/lifecycle/publication.py"
    ]
    assert 'actor_id="courtvision_ai.py"' not in publication
    assert "context.actor_id" in publication
    assert "context.sport" in publication
    assert "context.mode" in publication
    assert "context.entrypoint" in publication


def test_mlb_settlement_and_finalization_do_not_generate_predictions() -> None:
    sources = _production_python_files()
    candidates = {
        path: source
        for path, source in sources.items()
        if "mlb" in path.lower()
        and any(
            token in path.lower()
            for token in ("settle", "settlement", "final", "grading")
        )
    }
    offenders = {
        path
        for path, source in candidates.items()
        if "PredictionApplicationService(" in source
        or "generate_daily_research_predictions(" in source
    }
    assert offenders == set()
