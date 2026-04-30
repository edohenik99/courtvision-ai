from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.artifact_guard import log_prediction_artifact_write
from .contracts import PipelineManifest
from .stages import stage


def _safe_len(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        return int(len(value))
    if isinstance(value, (list, tuple, set, dict)):
        return int(len(value))
    return None


def _log_and_guard_board_write(prediction_date: str, output_path: Path, caller: str, board_name: str) -> None:
    log_prediction_artifact_write(
        requested_prediction_date=prediction_date,
        output_path=output_path,
        caller=caller,
        artifact_label=board_name,
    )


def build_prediction_manifest(prediction_date: str, prediction_outputs: dict[str, Any]) -> PipelineManifest:
    summary = dict(prediction_outputs.get("summary", {}))
    selected_df = prediction_outputs.get("selected_props")
    elite_df = prediction_outputs.get("elite_props")
    market_df = prediction_outputs.get("market_lines")
    merged_df = prediction_outputs.get("merged_market_props")
    report_paths = prediction_outputs.get("output_paths", {})

    manifest = PipelineManifest(
        run_type="prediction",
        run_date=prediction_date,
        quality_status=str(summary.get("data_quality_status", summary.get("market_quality_status", "live"))),
        summary=summary,
    )
    manifest.add_stage(stage("load_inputs", input_count=summary.get("games_count"), output_count=summary.get("games_count")))
    manifest.add_stage(stage("build_candidate_universe", output_count=summary.get("candidate_count_before_final_board_build")))
    manifest.add_stage(stage("market_ingestion", output_count=_safe_len(market_df), warnings=[summary.get("market_status_message")] if summary.get("market_status_message") else []))
    manifest.add_stage(stage("market_merge", output_count=_safe_len(merged_df)))
    manifest.add_stage(stage("selection", output_count=_safe_len(selected_df), notes={"elite_rows": _safe_len(elite_df)}))
    manifest.add_stage(stage("reporting", critical=False, artifacts=[str(v) for v in report_paths.values() if v]))
    return manifest


def build_grading_manifest(prediction_date: str, graded_df: pd.DataFrame, summary: dict[str, Any] | None = None) -> PipelineManifest:
    if summary is None:
        summary_payload = {}
    elif isinstance(summary, dict):
        summary_payload = summary
    elif hasattr(summary, "to_dict"):
        summary_payload = summary.to_dict()
    elif isinstance(summary, (list, tuple)) and len(summary) > 0 and isinstance(summary[0], (list, tuple)) and len(summary[0]) == 2:
        summary_payload = dict(summary)
    else:
        summary_payload = {"summary": str(summary)}
    manifest = PipelineManifest(
        run_type="grading",
        run_date=prediction_date,
        quality_status="graded",
        summary=summary_payload,
    )
    manifest.add_stage(stage("load_pick_history", output_count=summary_payload.get("history_rows")))
    manifest.add_stage(stage("grade_results", output_count=_safe_len(graded_df)))
    manifest.add_stage(stage("write_grading_reports", critical=False, output_count=_safe_len(graded_df)))
    return manifest


def write_manifest(manifest: PipelineManifest, out_dir: str | Path) -> Path:
    output_path = manifest.default_path(Path(out_dir))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return output_path


def save_prediction_boards(prediction_date: str, prediction_outputs: dict[str, Any], out_dir: str | Path) -> None:
    """Save prediction boards to disk and print summary."""
    import json
    from pathlib import Path

    out_dir = Path(out_dir)
    boards_dir = out_dir / "boards" / prediction_date
    boards_dir.mkdir(parents=True, exist_ok=True)

    # Map of possible keys to board names
    board_keys = {
        "elite": ["elite_props", "elite_board", "elite"],
        "full_market": ["full_market_props", "full_market_board", "full_market"],
        "stat_only": ["stat_only_props", "stat_only_board", "stat_only"],
    }

    saved_boards = []

    for board_name, possible_keys in board_keys.items():
        board_data = None
        for key in possible_keys:
            if key in prediction_outputs:
                board_data = prediction_outputs[key]
                break

        if board_data is None or (hasattr(board_data, 'empty') and board_data.empty) or (isinstance(board_data, (list, tuple)) and not board_data):
            continue

        if isinstance(board_data, pd.DataFrame):
            if board_data.empty:
                continue
            csv_path = boards_dir / f"{board_name}.csv"
            _log_and_guard_board_write(
                prediction_date,
                csv_path,
                "courtvision.pipeline.runner:save_prediction_boards",
                board_name,
            )
            board_data.to_csv(csv_path, index=False)

            raw_txt_path = boards_dir / f"{board_name}.txt"
            _log_and_guard_board_write(
                prediction_date,
                raw_txt_path,
                "courtvision.pipeline.runner:save_prediction_boards",
                f"{board_name}_raw_preview",
            )
            with open(raw_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{board_name.upper()} BOARD ({len(board_data)} rows)\n")
                f.write("=" * 50 + "\n")
                f.write(board_data.head(10).to_string(index=False))
                if len(board_data) > 10:
                    f.write(f"\n... and {len(board_data) - 10} more rows\n")

            desired_columns = ['prediction_date', 'selection', 'team', 'opponent', 'sportsbook_line', 'odds', 'model_projection', 'edge', 'confidence']
            if 'letter_grade' in board_data.columns:
                desired_columns.append('letter_grade')
            available_columns = [col for col in desired_columns if col in board_data.columns]

            clean_txt_path = boards_dir / f"{board_name}_clean.txt"
            _log_and_guard_board_write(
                prediction_date,
                clean_txt_path,
                "courtvision.pipeline.runner:save_prediction_boards",
                f"{board_name}_clean_preview",
            )
            with open(clean_txt_path, 'w', encoding='utf-8') as f:
                f.write(f"{board_name.upper()} CLEAN BOARD ({len(board_data)} rows)\n")
                f.write("=" * 80 + "\n")
                if available_columns:
                    clean_df = board_data[available_columns].copy()
                    for col in ['sportsbook_line', 'odds', 'model_projection', 'edge', 'confidence']:
                        if col in clean_df.columns:
                            clean_df[col] = clean_df[col].astype(float).round(2)
                    f.write(clean_df.to_string(index=False, justify='left'))
                else:
                    f.write("No data available\n")

            saved_boards.append((board_name, len(board_data), str(csv_path), str(raw_txt_path), str(clean_txt_path)))
        elif isinstance(board_data, (list, tuple)):
            if not board_data:
                continue
            json_path = boards_dir / f"{board_name}.json"
            _log_and_guard_board_write(
                prediction_date,
                json_path,
                "courtvision.pipeline.runner:save_prediction_boards",
                board_name,
            )
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(board_data, f, indent=2, default=str)
            saved_boards.append((board_name, len(board_data), str(json_path), None))

    if saved_boards:
        print(f"[boards_saved] {len(saved_boards)} boards saved to {boards_dir}")
        for row in saved_boards:
            board_name, count, path1, path2, path3 = row if len(row) == 5 else (*row, None)
            print(f"  {board_name}: {count} rows -> {path1}")
            if path2:
                print(f"    raw preview: {path2}")
            if path3:
                print(f"    clean preview: {path3}")
    else:
        print("[boards_saved] no boards to save")
