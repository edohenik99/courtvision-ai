from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


AUTH_SCHEMA = "courtvision-finalizer-authorization-v1"
EXECUTION_SCHEMA = "courtvision-finalizer-execution-v1"
AUTH_ID_PATTERN = re.compile(r"^cvfa-v1-[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONTROL_PATTERN = re.compile(r"^mlb-hr-control-v1-[0-9a-f]{20}$")
RUN_PATTERN = re.compile(r"^hrv1-[0-9a-f]{16}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT_LIFETIME = timedelta(minutes=30)


class PinnedFinalizerContractError(RuntimeError):
    """Raised before finalization when provenance cannot be proven."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PinnedFinalizerContractError(f"{field} must be UTC text ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PinnedFinalizerContractError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PinnedFinalizerContractError(f"{field} must be UTC")
    return parsed


def _utc_text(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_file(path: str | Path, field: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise PinnedFinalizerContractError(f"{field} does not exist: {resolved}")
    return resolved


def _resolve_directory(path: str | Path, field: str) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise PinnedFinalizerContractError(f"{field} does not exist: {resolved}")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_text(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise PinnedFinalizerContractError(
            "unable to establish Git identity: " + completed.stderr.strip()
        )
    return completed.stdout.strip()


def verify_executing_repository(
    repo_root: str | Path,
    expected_commit: str,
    *,
    require_clean: bool = True,
) -> dict[str, str]:
    if not COMMIT_PATTERN.fullmatch(expected_commit):
        raise PinnedFinalizerContractError("expected commit is missing or invalid")
    root = _resolve_directory(repo_root, "repository root")
    top_level = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != root:
        raise PinnedFinalizerContractError(
            f"executing source tree mismatch: expected {root}, Git resolved {top_level}"
        )
    head = _git_text(root, "rev-parse", "HEAD").lower()
    if head != expected_commit:
        raise PinnedFinalizerContractError(
            f"executing commit mismatch: expected {expected_commit}, actual {head}"
        )
    dirty = _git_text(root, "status", "--porcelain")
    if require_clean and dirty:
        raise PinnedFinalizerContractError("executing repository is dirty")
    return {"repo_root": str(root), "executing_commit": head, "dirty": dirty}


def _validate_identity_fields(payload: Mapping[str, Any]) -> None:
    required_text = {
        "operating_date",
        "control_id",
        "pinned_commit",
        "prediction_run_id",
        "prediction_manifest_sha256",
        "predictions_csv_sha256",
        "control_manifest_sha256",
        "controller_run_id",
        "source_root",
        "prediction_manifest_path",
        "predictions_csv_path",
        "control_manifest_path",
        "controller_script_path",
        "controller_script_sha256",
        "issued_at_utc",
        "expires_at_utc",
    }
    missing = sorted(name for name in required_text if not isinstance(payload.get(name), str))
    if missing:
        raise PinnedFinalizerContractError(
            "authorization fields missing or invalid: " + ", ".join(missing)
        )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(payload["operating_date"])):
        raise PinnedFinalizerContractError("authorization operating date is invalid")
    if not CONTROL_PATTERN.fullmatch(str(payload["control_id"])):
        raise PinnedFinalizerContractError("authorization control identity is invalid")
    if not COMMIT_PATTERN.fullmatch(str(payload["pinned_commit"])):
        raise PinnedFinalizerContractError("authorization pinned commit is invalid")
    if not RUN_PATTERN.fullmatch(str(payload["prediction_run_id"])):
        raise PinnedFinalizerContractError("authorization prediction run is invalid")
    for field in (
        "prediction_manifest_sha256",
        "predictions_csv_sha256",
        "control_manifest_sha256",
        "controller_script_sha256",
    ):
        if not SHA256_PATTERN.fullmatch(str(payload[field])):
            raise PinnedFinalizerContractError(f"authorization {field} is invalid")


def _authorization_id(payload: Mapping[str, Any]) -> str:
    return "cvfa-v1-" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{uuid4().hex}")
    try:
        temporary.write_bytes(_canonical_bytes(value))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def issue_authorization(
    *,
    authorization_root: str | Path,
    repo_root: str | Path,
    expected_commit: str,
    operating_date: str,
    control_id: str,
    prediction_run_id: str,
    prediction_manifest: str | Path,
    predictions_csv: str | Path,
    control_manifest: str | Path,
    controller_run_id: str,
    controller_script: str | Path,
    now: datetime | None = None,
    lifetime: timedelta = MAX_RECEIPT_LIFETIME,
) -> dict[str, Any]:
    if lifetime <= timedelta(0) or lifetime > MAX_RECEIPT_LIFETIME:
        raise PinnedFinalizerContractError("authorization lifetime is invalid")
    identity = verify_executing_repository(repo_root, expected_commit)
    manifest_path = _resolve_file(prediction_manifest, "prediction manifest")
    predictions_path = _resolve_file(predictions_csv, "predictions CSV")
    control_path = _resolve_file(control_manifest, "control manifest")
    controller_path = _resolve_file(controller_script, "controller script")
    issued = (now or _utc_now()).astimezone(timezone.utc)
    payload: dict[str, Any] = {
        "operating_date": operating_date,
        "control_id": control_id,
        "pinned_commit": expected_commit,
        "prediction_run_id": prediction_run_id,
        "prediction_manifest_sha256": _sha256_file(manifest_path),
        "predictions_csv_sha256": _sha256_file(predictions_path),
        "control_manifest_sha256": _sha256_file(control_path),
        "controller_run_id": controller_run_id,
        "source_root": identity["repo_root"],
        "prediction_manifest_path": str(manifest_path),
        "predictions_csv_path": str(predictions_path),
        "control_manifest_path": str(control_path),
        "controller_script_path": str(controller_path),
        "controller_script_sha256": _sha256_file(controller_path),
        "issued_at_utc": _utc_text(issued),
        "expires_at_utc": _utc_text(issued + lifetime),
    }
    _validate_identity_fields(payload)
    authorization_id = _authorization_id(payload)
    document = {
        "schema_version": AUTH_SCHEMA,
        "authorization_id": authorization_id,
        "payload": payload,
    }
    root = Path(authorization_root).expanduser().resolve(strict=False)
    receipt_name = f"cvfa-{authorization_id.removeprefix('cvfa-v1-')[:16]}.json"
    pending = root / "pending" / receipt_name
    if pending.exists() or (root / "claimed" / receipt_name).exists():
        raise PinnedFinalizerContractError("authorization identity already exists")
    _write_json_atomic(pending, document)
    return {**document, "receipt_path": str(pending)}


def _load_authorization(
    receipt_path: str | Path,
    *,
    now: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    path = _resolve_file(receipt_path, "authorization receipt")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PinnedFinalizerContractError("authorization receipt is malformed") from exc
    if not isinstance(document, dict) or document.get("schema_version") != AUTH_SCHEMA:
        raise PinnedFinalizerContractError("authorization schema mismatch")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise PinnedFinalizerContractError("authorization payload is malformed")
    _validate_identity_fields(payload)
    observed_id = document.get("authorization_id")
    expected_id = _authorization_id(payload)
    if not isinstance(observed_id, str) or not AUTH_ID_PATTERN.fullmatch(observed_id):
        raise PinnedFinalizerContractError("authorization identity is invalid")
    if observed_id != expected_id:
        raise PinnedFinalizerContractError("authorization content hash mismatch")
    issued = _parse_utc(payload["issued_at_utc"], "issued_at_utc")
    expires = _parse_utc(payload["expires_at_utc"], "expires_at_utc")
    current = (now or _utc_now()).astimezone(timezone.utc)
    if expires <= issued or expires - issued > MAX_RECEIPT_LIFETIME:
        raise PinnedFinalizerContractError("authorization validity window is invalid")
    if current < issued - timedelta(minutes=1):
        raise PinnedFinalizerContractError("authorization is not yet valid")
    if current > expires:
        raise PinnedFinalizerContractError("authorization is stale")
    return path, document


def validate_authorization(
    receipt_path: str | Path,
    *,
    expected_state: str,
    expected_commit: str,
    operating_date: str,
    control_id: str,
    prediction_run_id: str | None = None,
    prediction_manifest_sha256: str | None = None,
    predictions_csv_sha256: str | None = None,
    authorization_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    path, document = _load_authorization(receipt_path, now=now)
    if path.parent.name != expected_state:
        raise PinnedFinalizerContractError(
            f"authorization must be in {expected_state} state"
        )
    payload = document["payload"]
    expected = {
        "pinned_commit": expected_commit,
        "operating_date": operating_date,
        "control_id": control_id,
    }
    optional = {
        "prediction_run_id": prediction_run_id,
        "prediction_manifest_sha256": prediction_manifest_sha256,
        "predictions_csv_sha256": predictions_csv_sha256,
    }
    expected.update({key: value for key, value in optional.items() if value is not None})
    for field, value in expected.items():
        if payload.get(field) != value:
            raise PinnedFinalizerContractError(f"authorization {field} mismatch")
    if authorization_id is not None and document["authorization_id"] != authorization_id:
        raise PinnedFinalizerContractError("authorization identity mismatch")
    repo_identity = verify_executing_repository(payload["source_root"], expected_commit)
    evidence_files = {
        "prediction_manifest_sha256": _resolve_file(
            payload["prediction_manifest_path"], "prediction manifest"
        ),
        "predictions_csv_sha256": _resolve_file(
            payload["predictions_csv_path"], "predictions CSV"
        ),
        "control_manifest_sha256": _resolve_file(
            payload["control_manifest_path"], "control manifest"
        ),
    }
    for field, evidence_path in evidence_files.items():
        if _sha256_file(evidence_path) != payload[field]:
            raise PinnedFinalizerContractError(f"authorization {field} no longer matches")
    controller_path = _resolve_file(payload["controller_script_path"], "controller script")
    if _sha256_file(controller_path) != payload["controller_script_sha256"]:
        raise PinnedFinalizerContractError("controller script changed after authorization")
    return {
        **document,
        "receipt_path": str(path),
        "executing_commit": repo_identity["executing_commit"],
    }


def claim_authorization(
    receipt_path: str | Path,
    *,
    expected_commit: str,
    operating_date: str,
    control_id: str,
    authorization_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validated = validate_authorization(
        receipt_path,
        expected_state="pending",
        expected_commit=expected_commit,
        operating_date=operating_date,
        control_id=control_id,
        authorization_id=authorization_id,
        now=now,
    )
    pending = Path(validated["receipt_path"])
    if pending.parent.name != "pending":
        raise PinnedFinalizerContractError("authorization is not pending")
    claimed = pending.parent.parent / "claimed" / pending.name
    claimed.parent.mkdir(parents=True, exist_ok=True)
    if claimed.exists():
        raise PinnedFinalizerContractError("authorization has already been claimed")
    try:
        pending.replace(claimed)
    except OSError as exc:
        raise PinnedFinalizerContractError("authorization claim was not atomic") from exc
    return {**validated, "receipt_path": str(claimed)}


def write_execution_receipt(
    *,
    authorization_receipt: str | Path,
    output_path: str | Path,
    expected_commit: str,
    operating_date: str,
    control_id: str,
    authorization_id: str,
    results_csv: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    authorization = validate_authorization(
        authorization_receipt,
        expected_state="claimed",
        expected_commit=expected_commit,
        operating_date=operating_date,
        control_id=control_id,
        authorization_id=authorization_id,
        now=now,
    )
    payload = authorization["payload"]
    results_path = _resolve_file(results_csv, "strict results CSV")
    execution = {
        "schema_version": EXECUTION_SCHEMA,
        "authorization_id": authorization_id,
        "controller_run_id": payload["controller_run_id"],
        "operating_date": operating_date,
        "control_id": control_id,
        "prediction_run_id": payload["prediction_run_id"],
        "prediction_manifest_sha256": payload["prediction_manifest_sha256"],
        "predictions_csv_sha256": payload["predictions_csv_sha256"],
        "control_manifest_sha256": payload["control_manifest_sha256"],
        "expected_commit": expected_commit,
        "executing_commit": authorization["executing_commit"],
        "source_root": payload["source_root"],
        "results_csv_path": str(results_path),
        "results_csv_sha256": _sha256_file(results_path),
        "completed_at_utc": _utc_text(now or _utc_now()),
    }
    output = Path(output_path).expanduser().resolve(strict=False)
    if output.exists():
        raise PinnedFinalizerContractError("execution receipt already exists")
    _write_json_atomic(output, execution)
    return {**execution, "receipt_path": str(output)}


def validate_execution_receipt(
    execution_receipt: str | Path,
    *,
    authorization_receipt: str | Path,
    expected_commit: str,
    operating_date: str,
    control_id: str,
    authorization_id: str,
    results_csv: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    authorization = validate_authorization(
        authorization_receipt,
        expected_state="claimed",
        expected_commit=expected_commit,
        operating_date=operating_date,
        control_id=control_id,
        authorization_id=authorization_id,
        now=now,
    )
    path = _resolve_file(execution_receipt, "execution receipt")
    try:
        execution = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PinnedFinalizerContractError("execution receipt is malformed") from exc
    if not isinstance(execution, dict) or execution.get("schema_version") != EXECUTION_SCHEMA:
        raise PinnedFinalizerContractError("execution receipt schema mismatch")
    payload = authorization["payload"]
    expected = {
        "authorization_id": authorization_id,
        "controller_run_id": payload["controller_run_id"],
        "operating_date": operating_date,
        "control_id": control_id,
        "prediction_run_id": payload["prediction_run_id"],
        "prediction_manifest_sha256": payload["prediction_manifest_sha256"],
        "predictions_csv_sha256": payload["predictions_csv_sha256"],
        "control_manifest_sha256": payload["control_manifest_sha256"],
        "expected_commit": expected_commit,
        "executing_commit": expected_commit,
        "source_root": payload["source_root"],
    }
    for field, value in expected.items():
        if execution.get(field) != value:
            raise PinnedFinalizerContractError(f"execution receipt {field} mismatch")
    results_path = _resolve_file(
        str(execution.get("results_csv_path", "")), "strict results CSV"
    )
    if results_path != _resolve_file(results_csv, "expected strict results CSV"):
        raise PinnedFinalizerContractError("execution receipt strict results path mismatch")
    results_digest = execution.get("results_csv_sha256")
    if not isinstance(results_digest, str) or not SHA256_PATTERN.fullmatch(results_digest):
        raise PinnedFinalizerContractError("execution receipt strict results digest is invalid")
    if _sha256_file(results_path) != results_digest:
        raise PinnedFinalizerContractError("strict results CSV changed after finalization")
    _parse_utc(execution.get("completed_at_utc"), "completed_at_utc")
    return {**execution, "receipt_path": str(path)}


def _add_common_identity(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--operating-date", required=True)
    parser.add_argument("--control-id", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned CourtVision finalizer contract")
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue = subparsers.add_parser("issue")
    _add_common_identity(issue)
    issue.add_argument("--authorization-root", type=Path, required=True)
    issue.add_argument("--repo-root", type=Path, required=True)
    issue.add_argument("--prediction-run-id", required=True)
    issue.add_argument("--prediction-manifest", type=Path, required=True)
    issue.add_argument("--predictions-csv", type=Path, required=True)
    issue.add_argument("--control-manifest", type=Path, required=True)
    issue.add_argument("--controller-run-id", required=True)
    issue.add_argument("--controller-script", type=Path, required=True)

    claim = subparsers.add_parser("claim")
    _add_common_identity(claim)
    claim.add_argument("--authorization-receipt", type=Path, required=True)
    claim.add_argument("--authorization-id", required=True)

    validate = subparsers.add_parser("validate-claimed")
    _add_common_identity(validate)
    validate.add_argument("--authorization-receipt", type=Path, required=True)
    validate.add_argument("--authorization-id", required=True)

    write = subparsers.add_parser("write-execution")
    _add_common_identity(write)
    write.add_argument("--authorization-receipt", type=Path, required=True)
    write.add_argument("--authorization-id", required=True)
    write.add_argument("--output-path", type=Path, required=True)
    write.add_argument("--results-csv", type=Path, required=True)

    validate_execution = subparsers.add_parser("validate-execution")
    _add_common_identity(validate_execution)
    validate_execution.add_argument("--authorization-receipt", type=Path, required=True)
    validate_execution.add_argument("--authorization-id", required=True)
    validate_execution.add_argument("--execution-receipt", type=Path, required=True)
    validate_execution.add_argument("--results-csv", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        common = {
            "expected_commit": args.expected_commit,
            "operating_date": args.operating_date,
            "control_id": args.control_id,
        }
        if args.command == "issue":
            result = issue_authorization(
                authorization_root=args.authorization_root,
                repo_root=args.repo_root,
                prediction_run_id=args.prediction_run_id,
                prediction_manifest=args.prediction_manifest,
                predictions_csv=args.predictions_csv,
                control_manifest=args.control_manifest,
                controller_run_id=args.controller_run_id,
                controller_script=args.controller_script,
                **common,
            )
        elif args.command == "claim":
            result = claim_authorization(
                args.authorization_receipt,
                authorization_id=args.authorization_id,
                **common,
            )
        elif args.command == "validate-claimed":
            result = validate_authorization(
                args.authorization_receipt,
                expected_state="claimed",
                authorization_id=args.authorization_id,
                **common,
            )
        elif args.command == "write-execution":
            result = write_execution_receipt(
                authorization_receipt=args.authorization_receipt,
                authorization_id=args.authorization_id,
                output_path=args.output_path,
                results_csv=args.results_csv,
                **common,
            )
        else:
            result = validate_execution_receipt(
                args.execution_receipt,
                authorization_receipt=args.authorization_receipt,
                authorization_id=args.authorization_id,
                results_csv=args.results_csv,
                **common,
            )
    except PinnedFinalizerContractError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"success": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
