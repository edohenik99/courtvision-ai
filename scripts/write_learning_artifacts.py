from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

FINAL_STATUS_READY = "LEARNING_ARTIFACTS_READY"
FINAL_STATUS_READY_WITH_WARNINGS = "LEARNING_ARTIFACTS_READY_WITH_WARNINGS"
FINAL_STATUS_DRY_RUN = "LEARNING_ARTIFACTS_DRY_RUN"
FINAL_STATUS_FAILED = "LEARNING_ARTIFACTS_FAILED"
FINAL_STATUS_BLOCKED_BY_UNSAFE_PROPOSALS = "LEARNING_ARTIFACTS_BLOCKED_BY_UNSAFE_PROPOSALS"

STEP_STATUS_DRY_RUN = "DRY_RUN"
STEP_STATUS_SUCCEEDED = "SUCCEEDED"
STEP_STATUS_FAILED = "FAILED"

SUMMARY_FILE_PREFIX = "learning_artifacts_summary"

SAFETY_FLAGS: dict[str, bool] = {
    "applied_changes": False,
    "live_rules_modified": False,
    "elite_logic_modified": False,
    "kelly_logic_modified": False,
    "final_decision_modified": False,
    "pick_history_modified": False,
    "history_files_modified": False,
    "generated_real_money_recommendations": False,
}

Runner = Callable[..., subprocess.CompletedProcess]
Printer = Callable[[str], None]


def _format_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _artifact_paths(
    *,
    runtime_root: Path,
    prediction_date: str,
) -> dict[str, dict[str, Path]]:
    return {
        "learning_brain": {
            "txt": runtime_root / "operator" / f"learning_brain_report_{prediction_date}.txt",
            "json": runtime_root / "diagnostics" / f"learning_brain_report_{prediction_date}.json",
        },
        "shadow_rule_proposals": {
            "txt": runtime_root / "operator" / f"shadow_rule_proposals_{prediction_date}.txt",
            "json": runtime_root / "diagnostics" / f"shadow_rule_proposals_{prediction_date}.json",
        },
        "learning_integration_snapshot": {
            "txt": runtime_root / "operator" / f"learning_integration_snapshot_{prediction_date}.txt",
            "json": runtime_root / "diagnostics" / f"learning_integration_snapshot_{prediction_date}.json",
        },
    }


def _summary_paths(
    *,
    runtime_root: Path,
    prediction_date: str,
) -> tuple[Path, Path]:
    stem = f"{SUMMARY_FILE_PREFIX}_{prediction_date}"
    return (
        runtime_root / "operator" / f"{stem}.txt",
        runtime_root / "diagnostics" / f"{stem}.json",
    )


def _assert_paths_under_runtime_root(paths: Sequence[Path], runtime_root: Path) -> None:
    root = runtime_root.resolve()
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"refusing to write outside runtime-root: {path}")


def _read_json_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    if path.stat().st_size == 0:
        return None, f"empty JSON artifact: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON artifact: {path}: {exc}"
    except OSError as exc:
        return None, f"unreadable JSON artifact: {path}: {type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, f"invalid JSON artifact root: {path}"
    return payload, None


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _payload_warnings(payload: dict[str, Any] | None, *, label: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("data_quality_warnings", [])
    if not isinstance(warnings, list):
        return []
    return [f"{label}: {warning}" for warning in warnings if str(warning).strip()]


def _build_steps(
    *,
    prediction_date: str,
    runtime_root: Path,
    history_root: Path,
    min_sample: int,
    confidence_z: float,
) -> list[dict[str, Any]]:
    return [
        {
            "name": "Learning Brain",
            "key": "learning_brain",
            "script": "scripts/write_learning_brain_report.py",
            "args": [
                "--prediction-date",
                prediction_date,
                "--runtime-root",
                str(runtime_root),
                "--history-root",
                str(history_root),
                "--min-sample",
                str(int(min_sample)),
            ],
        },
        {
            "name": "Shadow Rule Proposals",
            "key": "shadow_rule_proposals",
            "script": "scripts/write_shadow_rule_proposals.py",
            "args": [
                "--prediction-date",
                prediction_date,
                "--runtime-root",
                str(runtime_root),
                "--min-sample",
                str(int(min_sample)),
                "--confidence-z",
                str(float(confidence_z)),
            ],
        },
        {
            "name": "Learning Integration Snapshot",
            "key": "learning_integration_snapshot",
            "script": "scripts/write_learning_integration_snapshot.py",
            "args": [
                "--prediction-date",
                prediction_date,
                "--runtime-root",
                str(runtime_root),
            ],
        },
    ]


def _step_summary(
    *,
    step: dict[str, Any],
    command: list[str],
    artifact_paths: dict[str, Path],
    status: str,
    exit_code: int | None,
    stdout: str = "",
    stderr: str = "",
    error_message: str = "",
    payload_status: str = "",
) -> dict[str, Any]:
    generated_paths = {
        name: {"path": str(path), "exists": path.exists()}
        for name, path in artifact_paths.items()
    }
    return {
        "name": step["name"],
        "key": step["key"],
        "script": step["script"],
        "command": _format_command(command),
        "status": status,
        "exit_code": exit_code,
        "payload_status": payload_status,
        "generated_artifact_paths": generated_paths,
        "stdout": stdout,
        "stderr": stderr,
        "error_message": error_message,
    }


def _render_summary_text(summary: dict[str, Any]) -> str:
    lines: list[str] = [
        f"CourtVision Learning Artifact Orchestrator - {summary['prediction_date']}",
        "=" * 76,
        f"Final status: {summary['final_status']}",
        f"Strict mode: {str(summary['strict']).lower()}",
        f"Dry run: {str(summary['dry_run']).lower()}",
        "",
        "Steps",
        "-" * 76,
    ]
    for step in summary["steps"]:
        lines.extend(
            [
                f"- {step['name']}: {step['status']}",
                f"  exit_code: {step['exit_code']}",
                f"  command: {step['command']}",
            ]
        )
        if step.get("payload_status"):
            lines.append(f"  payload_status: {step['payload_status']}")
        for artifact in step["generated_artifact_paths"].values():
            exists = str(artifact["exists"]).lower()
            lines.append(f"  artifact: {artifact['path']} exists={exists}")
        if step.get("error_message"):
            lines.append(f"  error: {step['error_message']}")

    lines.extend(["", "Warnings", "-" * 76])
    if summary["warnings"]:
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    else:
        lines.append("- none")

    lines.extend(["", "Safety", "-" * 76])
    for key in (
        "applied_changes",
        "live_rules_modified",
        "elite_logic_modified",
        "kelly_logic_modified",
        "final_decision_modified",
        "pick_history_modified",
        "history_files_modified",
        "generated_real_money_recommendations",
    ):
        lines.append(f"- {key}: {str(summary[key]).lower()}")
    lines.extend(
        [
            f"- active_proposal_count: {summary['active_proposal_count']}",
            f"- production_effect_count: {summary['production_effect_count']}",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_summary_outputs(summary: dict[str, Any], *, runtime_root: Path, prediction_date: str) -> tuple[Path, Path]:
    txt_path, json_path = _summary_paths(runtime_root=runtime_root, prediction_date=prediction_date)
    _assert_paths_under_runtime_root((txt_path, json_path), runtime_root)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(_render_summary_text(summary), encoding="utf-8")
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return txt_path, json_path


def orchestrate_learning_artifacts(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    min_sample: int = 20,
    confidence_z: float = 1.96,
    dry_run: bool = False,
    strict: bool = False,
    runner: Runner | None = None,
    printer: Printer = print,
) -> tuple[int, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    history_root_path = Path(history_root)
    runner = runner or subprocess.run

    expected_artifacts = _artifact_paths(runtime_root=runtime_root_path, prediction_date=prediction_date)
    steps = _build_steps(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        history_root=history_root_path,
        min_sample=min_sample,
        confidence_z=confidence_z,
    )

    step_summaries: list[dict[str, Any]] = []
    warnings: list[str] = []
    failed_count = 0

    for step in steps:
        command = [sys.executable, step["script"], *step["args"]]
        printer(f"[START] {step['name']}")
        if dry_run:
            printer(f"[DRY-RUN] Would run command: {_format_command(command)}")
            step_summaries.append(
                _step_summary(
                    step=step,
                    command=command,
                    artifact_paths=expected_artifacts[step["key"]],
                    status=STEP_STATUS_DRY_RUN,
                    exit_code=None,
                )
            )
            continue

        printer(f"[RUN] {_format_command(command)}")
        stdout = ""
        stderr = ""
        error_message = ""
        exit_code: int | None
        status = STEP_STATUS_SUCCEEDED
        try:
            result = runner(command, cwd=ROOT_DIR, capture_output=True, text=True)
            exit_code = int(result.returncode)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
        except Exception as exc:  # pragma: no cover - defensive subprocess boundary
            exit_code = None
            status = STEP_STATUS_FAILED
            error_message = str(exc)
            failed_count += 1
            warnings.append(f"{step['name']} failed: {error_message}")
        else:
            if stdout.strip():
                printer(stdout.rstrip())
            if stderr.strip():
                printer(stderr.rstrip())
            if exit_code != 0:
                status = STEP_STATUS_FAILED
                failed_count += 1
                error_message = stderr.strip() or stdout.strip() or f"exit code {exit_code}"
                warnings.append(f"{step['name']} failed: {error_message}")

        payload_status = ""
        json_path = expected_artifacts[step["key"]]["json"]
        payload, read_warning = _read_json_payload(json_path)
        if read_warning:
            warnings.append(f"{step['name']}: {read_warning}")
        if isinstance(payload, dict):
            payload_status = str(payload.get("status", "")).strip()
            warnings.extend(_payload_warnings(payload, label=step["name"]))

        step_summaries.append(
            _step_summary(
                step=step,
                command=command,
                artifact_paths=expected_artifacts[step["key"]],
                status=status,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                error_message=error_message,
                payload_status=payload_status,
            )
        )
        if status == STEP_STATUS_SUCCEEDED:
            printer(f"[OK] {step['name']}")
        else:
            printer(f"[WARN] {step['name']} failed nonfatally")

    active_proposal_count = 0
    production_effect_count = 0
    integration_json = expected_artifacts["learning_integration_snapshot"]["json"]
    integration_payload, integration_warning = _read_json_payload(integration_json)
    if integration_warning:
        warnings.append(f"Learning Integration Snapshot: {integration_warning}")
    if isinstance(integration_payload, dict):
        active_proposal_count = _safe_int(integration_payload.get("active_proposal_count"))
        production_effect_count = _safe_int(integration_payload.get("production_effect_count"))

    unsafe_proposals = active_proposal_count > 0 or production_effect_count > 0
    if dry_run:
        final_status = FINAL_STATUS_DRY_RUN
        exit_code = 0
    elif unsafe_proposals:
        final_status = FINAL_STATUS_BLOCKED_BY_UNSAFE_PROPOSALS
        exit_code = 1
        warnings.append(
            "unsafe proposals detected: "
            f"active_proposal_count={active_proposal_count}, "
            f"production_effect_count={production_effect_count}"
        )
    elif strict and failed_count > 0:
        final_status = FINAL_STATUS_FAILED
        exit_code = 1
    elif failed_count > 0 or warnings:
        final_status = FINAL_STATUS_READY_WITH_WARNINGS
        exit_code = 0
    else:
        final_status = FINAL_STATUS_READY
        exit_code = 0

    summary: dict[str, Any] = {
        "report_name": SUMMARY_FILE_PREFIX,
        "report_version": "1.0",
        "prediction_date": prediction_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(runtime_root_path),
        "history_root": str(history_root_path),
        "min_sample": int(min_sample),
        "confidence_z": float(confidence_z),
        "strict": bool(strict),
        "dry_run": bool(dry_run),
        "steps_attempted": [step["name"] for step in step_summaries],
        "steps": step_summaries,
        "failed_step_count": int(failed_count),
        "warnings": list(dict.fromkeys(warnings)),
        "active_proposal_count": int(active_proposal_count),
        "production_effect_count": int(production_effect_count),
        "final_status": final_status,
        **SAFETY_FLAGS,
    }

    txt_path, json_path = _write_summary_outputs(
        summary,
        runtime_root=runtime_root_path,
        prediction_date=prediction_date,
    )
    summary["summary_artifact_paths"] = {
        "txt": str(txt_path),
        "json": str(json_path),
    }
    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    printer(f"learning_artifacts_summary_txt={txt_path}")
    printer(f"learning_artifacts_summary_json={json_path}")
    printer(f"learning_artifacts_status={final_status}")
    printer(f"active_proposal_count={active_proposal_count}")
    printer(f"production_effect_count={production_effect_count}")
    printer(f"applied_changes={str(summary['applied_changes']).lower()}")
    return exit_code, summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run reporting-only learning artifacts in safe order.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--confidence-z", type=float, default=1.96)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code, _summary = orchestrate_learning_artifacts(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        min_sample=args.min_sample,
        confidence_z=args.confidence_z,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
