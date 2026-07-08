"""Export existing dated run artifacts into the offline evidence records.

This module is intentionally a read-and-append adapter.  It does not import the
prediction runtime, provider clients, grading code, or Kelly calculation code.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from scripts.init_evidence_daily_manifest import MANIFEST_COLUMNS
    from scripts.init_evidence_ledger import LEDGER_COLUMNS
except ModuleNotFoundError:  # Support ``python scripts/export_...py``.
    from init_evidence_daily_manifest import MANIFEST_COLUMNS
    from init_evidence_ledger import LEDGER_COLUMNS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / "outputs" / "runtime"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "history" / "evidence_daily_manifest.csv"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "history" / "evidence_ledger.csv"

VALID_RUN_STATUSES = frozenset(
    {
        "complete",
        "no_slate",
        "no_picks",
        "provider_failure",
        "failed_validation",
        "failed_grading",
        "failed_other",
    }
)

ARTIFACT_FIELDS = (
    "source_board",
    "elite_board",
    "kelly_artifact",
    "operator_card",
    "completion_audit",
    "artifact_manifest",
    "run_log",
    "validation_log",
    "grading_log",
)

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "market": ("market", "market_type", "prop_type"),
    "player": ("player", "player_name", "entity_name"),
    "team": ("team", "team_abbr"),
    "opponent": ("opponent", "opponent_abbr"),
    "game_id": ("game_id",),
    "selection": ("selection", "side", "selection_side"),
    "line": ("line", "sportsbook_line"),
    "odds": ("odds", "american_odds"),
    "implied_probability": ("implied_probability", "implied_prob"),
    "model_probability": (
        "model_probability",
        "predicted_probability",
        "win_probability",
    ),
    "edge": ("edge", "side_edge_pct", "edge_pct"),
    "confidence": ("confidence",),
    "kelly_eligible": ("kelly_eligible", "eligible"),
    "recommended_units": ("recommended_units", "stake_units", "units"),
    "provider_used": ("provider_used", "odds_provider", "provider"),
}

KELLY_OWNED_FIELDS = frozenset({"kelly_eligible", "recommended_units"})
DUPLICATE_LEDGER_FIELDS = (
    "trial_id",
    "prediction_date",
    "player",
    "market",
    "selection",
    "line",
    "odds",
)


class EvidenceExportError(RuntimeError):
    """Raised when an artifact export cannot be completed safely."""


@dataclass(frozen=True)
class ExportResult:
    manifest_row: dict[str, str]
    ledger_rows: tuple[dict[str, str], ...]
    artifacts: dict[str, Path | None]
    dry_run: bool


def _parse_date(value: str, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise EvidenceExportError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        ) from exc
    if parsed.isoformat() != value:
        raise EvidenceExportError(
            f"{field_name} must be a valid date in YYYY-MM-DD format"
        )
    return parsed.isoformat()


def _require_text(value: str, field_name: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise EvidenceExportError(f"{field_name} is required")
    return cleaned


def _validate_csv_schema(path: Path, expected: tuple[str, ...], label: str) -> None:
    if not path.is_file():
        raise EvidenceExportError(f"{label} does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            actual = tuple(next(csv.reader(handle), ()))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceExportError(f"could not read {label} header: {exc}") from exc
    if actual != expected:
        raise EvidenceExportError(
            f"{label} has the wrong schema; expected {list(expected)!r}, "
            f"got {list(actual)!r}"
        )


def _capture_code_sha(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise EvidenceExportError(f"git rev-parse HEAD failed{suffix}") from exc
    return _require_text(completed.stdout, "code_sha")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceExportError(f"could not hash artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _artifact_candidates(runtime_root: Path, prediction_date: str) -> dict[str, tuple[Path, ...]]:
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    logs = runtime_root / "logs"
    return {
        "source_board": (
            operator / f"full_market_board_{prediction_date}.csv",
            operator / f"source_board_{prediction_date}.csv",
            runtime_root / f"full_market_board_{prediction_date}.csv",
            runtime_root / f"source_board_{prediction_date}.csv",
        ),
        "elite_board": (
            operator / f"elite_board_{prediction_date}.csv",
            runtime_root / f"elite_board_{prediction_date}.csv",
        ),
        "kelly_artifact": (
            operator / f"kelly_stakes_{prediction_date}.csv",
            operator / f"kelly_artifact_{prediction_date}.csv",
            runtime_root / f"kelly_stakes_{prediction_date}.csv",
        ),
        "operator_card": (
            operator / f"operator_card_{prediction_date}.txt",
            operator / f"operator_card_{prediction_date}.json",
        ),
        "completion_audit": (
            diagnostics / f"completion_state_audit_{prediction_date}.json",
            operator / f"completion_state_audit_{prediction_date}.txt",
        ),
        "artifact_manifest": (
            diagnostics / f"artifact_manifest_{prediction_date}.json",
            operator / f"artifact_manifest_{prediction_date}.txt",
        ),
        "run_log": (
            logs / f"run_today_{prediction_date}.log",
            logs / f"run_{prediction_date}.log",
        ),
        "validation_log": (logs / f"validation_{prediction_date}.log",),
        "grading_log": (logs / f"grading_{prediction_date}.log",),
    }


def discover_artifacts(
    *, runtime_root: Path, prediction_date: str
) -> dict[str, Path | None]:
    """Resolve one current, date-specific artifact for each evidence field."""

    candidates = _artifact_candidates(Path(runtime_root).resolve(), prediction_date)
    return {
        name: next((path.resolve() for path in paths if path.is_file()), None)
        for name, paths in candidates.items()
    }


def _stored_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise EvidenceExportError(
            f"artifact must resolve inside the repository: {path}"
        ) from exc


def _read_csv_rows(path: Path, label: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise EvidenceExportError(f"{label} has no CSV header: {path}")
            return [
                {str(key): "" if value is None else str(value) for key, value in row.items()}
                for row in reader
            ]
    except EvidenceExportError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceExportError(f"could not read {label} {path}: {exc}") from exc


def _first_value(row: Mapping[str, object], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = row.get(alias)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _identity_key(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        _first_value(row, FIELD_ALIASES[field]).strip().casefold()
        for field in ("player", "market", "selection", "line", "odds")
    )


def _merge_recommendations(
    elite_rows: list[dict[str, str]], kelly_rows: list[dict[str, str]]
) -> list[tuple[dict[str, str], dict[str, str] | None]]:
    if not elite_rows:
        return [(row, row) for row in kelly_rows]
    if not kelly_rows:
        return [(row, None) for row in elite_rows]

    kelly_by_key: defaultdict[tuple[str, ...], deque[dict[str, str]]] = defaultdict(deque)
    for row in kelly_rows:
        kelly_by_key[_identity_key(row)].append(row)
    return [
        (elite, kelly_by_key[_identity_key(elite)].popleft())
        if kelly_by_key[_identity_key(elite)]
        else (elite, None)
        for elite in elite_rows
    ]


def _field_value(
    field: str,
    elite_row: Mapping[str, object],
    kelly_row: Mapping[str, object] | None,
) -> str:
    aliases = FIELD_ALIASES[field]
    sources = (
        (kelly_row, elite_row)
        if field in KELLY_OWNED_FIELDS
        else (elite_row, kelly_row)
    )
    for source in sources:
        if source is not None:
            value = _first_value(source, aliases)
            if value:
                return value
    return ""


def _normalize_mapped_boolean(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return "true"
    if normalized in {"false", "0", "no"}:
        return "false"
    return value


def _recommendation_rows(
    *,
    elite_path: Path | None,
    kelly_path: Path | None,
    trial_id: str,
    run_date: str,
    prediction_date: str,
    code_sha: str,
    config_hash: str,
    notes: str,
    created_at: str,
) -> list[dict[str, str]]:
    elite_rows = _read_csv_rows(elite_path, "Elite board") if elite_path else []
    kelly_rows = _read_csv_rows(kelly_path, "Kelly artifact") if kelly_path else []
    merged = _merge_recommendations(elite_rows, kelly_rows)
    output: list[dict[str, str]] = []

    for elite_row, kelly_row in merged:
        mapped = {
            field: _field_value(field, elite_row, kelly_row)
            for field in FIELD_ALIASES
        }
        mapped["kelly_eligible"] = _normalize_mapped_boolean(
            mapped["kelly_eligible"]
        )
        unavailable = [
            field
            for field in (
                "market",
                "player",
                "team",
                "opponent",
                "game_id",
                "selection",
                "line",
                "odds",
                "implied_probability",
                "model_probability",
                "edge",
                "confidence",
                "kelly_eligible",
                "recommended_units",
                "provider_used",
            )
            if not mapped[field]
        ]
        row_notes = str(notes).strip()
        if unavailable:
            missing_note = "Unavailable artifact fields: " + ", ".join(unavailable)
            row_notes = f"{row_notes}; {missing_note}" if row_notes else missing_note
        output.append(
            {
                "trial_id": trial_id,
                "run_date": run_date,
                "prediction_date": prediction_date,
                "code_sha": code_sha,
                "config_hash": config_hash,
                "provider_used": mapped["provider_used"],
                "market": mapped["market"],
                "player": mapped["player"],
                "team": mapped["team"],
                "opponent": mapped["opponent"],
                "game_id": mapped["game_id"],
                "selection": mapped["selection"],
                "line": mapped["line"],
                "odds": mapped["odds"],
                "implied_probability": mapped["implied_probability"],
                "model_probability": mapped["model_probability"],
                "edge": mapped["edge"],
                "confidence": mapped["confidence"],
                "kelly_eligible": mapped["kelly_eligible"],
                "recommended_units": mapped["recommended_units"],
                "closing_line": "",
                "closing_odds": "",
                "result": "",
                "profit_1u": "",
                "void_reason": "",
                "notes": row_notes,
                "created_at": created_at,
            }
        )
    return output


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EvidenceExportError(f"could not inspect artifact {path}: {exc}") from exc


def _clearly_no_slate(artifacts: Mapping[str, Path | None]) -> bool:
    evidence = "\n".join(
        _read_text(artifacts.get(field))
        for field in ("operator_card", "completion_audit")
    ).casefold()
    return bool(
        re.search(r"\bno[ _-]?slate\b", evidence)
        or re.search(r"games\s+count\s*:\s*0\b", evidence)
        or re.search(r'"(?:games_count|game_count)"\s*:\s*0\b', evidence)
        or re.search(r'"no_slate"\s*:\s*true\b', evidence)
    )


def _artifacts_show_success(artifacts: Mapping[str, Path | None]) -> bool:
    evidence = "\n".join(
        _read_text(artifacts.get(field))
        for field in (
            "operator_card",
            "completion_audit",
            "artifact_manifest",
            "run_log",
            "validation_log",
            "grading_log",
        )
    ).casefold()
    failure_signal = bool(
        re.search(r"run_health\s*:\s*(?:fail|error)", evidence)
        or re.search(r'"status"\s*:\s*"(?:failed|fatal|fatal_missing|error)"', evidence)
        or re.search(r"\b(?:run|validation|grading)\s+(?:failed|error)\b", evidence)
    )
    if failure_signal:
        return False
    return bool(
        re.search(r"final_decision\s*:", evidence)
        or re.search(r'"report_agreement_status"\s*:\s*"complete"', evidence)
        or re.search(r'"status"\s*:\s*"(?:complete|completed|ok|warning_missing)"', evidence)
        or re.search(r"\b(?:run|validation|grading)\s+(?:complete|completed|passed)\b", evidence)
    )


def _infer_run_status(
    *,
    supplied: str | None,
    recommendation_count: int,
    artifacts: Mapping[str, Path | None],
    missing: Sequence[str],
) -> str:
    if supplied is not None:
        normalized = str(supplied).strip()
        if normalized not in VALID_RUN_STATUSES:
            raise EvidenceExportError(
                "run_status must be one of: " + ", ".join(sorted(VALID_RUN_STATUSES))
            )
        return normalized
    if missing:
        return "failed_other"
    if recommendation_count:
        return "complete"
    successful = _artifacts_show_success(artifacts)
    if successful and _clearly_no_slate(artifacts):
        return "no_slate"
    if successful:
        return "no_picks"
    return "failed_other"


def _normalized_duplicate_value(field: str, value: object) -> str:
    text = "" if value is None else str(value).strip()
    if field in {"line", "odds"} and text:
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            pass
    return text.casefold()


def _ledger_duplicate_key(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        _normalized_duplicate_value(field, row.get(field, ""))
        for field in DUPLICATE_LEDGER_FIELDS
    )


def _read_existing_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise EvidenceExportError(f"could not read existing evidence rows: {exc}") from exc


def _preflight_duplicates(
    *,
    manifest_path: Path,
    ledger_path: Path,
    manifest_row: Mapping[str, str],
    ledger_rows: Sequence[Mapping[str, str]],
    allow_duplicates: bool,
    allow_duplicate_manifest: bool,
) -> None:
    existing_manifest = _read_existing_rows(manifest_path)
    manifest_key = (manifest_row["trial_id"], manifest_row["prediction_date"])
    if not allow_duplicate_manifest and any(
        (row.get("trial_id", ""), row.get("prediction_date", "")) == manifest_key
        for row in existing_manifest
    ):
        raise EvidenceExportError(
            "duplicate daily manifest row for "
            f"trial_id={manifest_key[0]!r}, prediction_date={manifest_key[1]!r}; "
            "use --allow-duplicate-manifest only for an intentional additive record"
        )

    if allow_duplicates:
        return
    existing_keys = {_ledger_duplicate_key(row) for row in _read_existing_rows(ledger_path)}
    pending_keys: set[tuple[str, ...]] = set()
    for row in ledger_rows:
        key = _ledger_duplicate_key(row)
        if key in existing_keys or key in pending_keys:
            rendered = ", ".join(
                f"{field}={row.get(field, '')!r}" for field in DUPLICATE_LEDGER_FIELDS
            )
            raise EvidenceExportError(
                f"duplicate evidence ledger row ({rendered}); "
                "use --allow-duplicates only when intentional"
            )
        pending_keys.add(key)


def _append_rows(path: Path, columns: tuple[str, ...], rows: Sequence[Mapping[str, str]]) -> None:
    if not rows:
        return
    try:
        with path.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writerows(rows)
    except (OSError, csv.Error) as exc:
        raise EvidenceExportError(f"could not append evidence rows to {path}: {exc}") from exc


def export_run_to_evidence(
    *,
    trial_id: str,
    prediction_date: str,
    config_hash: str,
    repo_root: Path = PROJECT_ROOT,
    runtime_root: Path | None = None,
    manifest_path: Path | None = None,
    ledger_path: Path | None = None,
    run_date: str | None = None,
    code_sha: str | None = None,
    run_status: str | None = None,
    notes: str = "",
    allow_missing_artifacts: bool = False,
    allow_duplicates: bool = False,
    allow_duplicate_manifest: bool = False,
    dry_run: bool = False,
) -> ExportResult:
    """Preflight and append one run's manifest and recommendation evidence."""

    repo_root = Path(repo_root).resolve()
    runtime_root = (
        Path(runtime_root).resolve()
        if runtime_root is not None
        else repo_root / "outputs" / "runtime"
    )
    manifest_path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else repo_root / "data" / "history" / "evidence_daily_manifest.csv"
    )
    ledger_path = (
        Path(ledger_path).resolve()
        if ledger_path is not None
        else repo_root / "data" / "history" / "evidence_ledger.csv"
    )

    _validate_csv_schema(manifest_path, MANIFEST_COLUMNS, "daily evidence manifest")
    _validate_csv_schema(ledger_path, LEDGER_COLUMNS, "evidence ledger")
    trial_id = _require_text(trial_id, "trial_id")
    config_hash = _require_text(config_hash, "config_hash")
    prediction_date = _parse_date(prediction_date, "prediction_date")
    run_date = _parse_date(
        run_date or datetime.now().astimezone().date().isoformat(), "run_date"
    )
    resolved_code_sha = (
        _require_text(code_sha, "code_sha")
        if code_sha is not None
        else _capture_code_sha(repo_root)
    )

    artifacts = discover_artifacts(
        runtime_root=runtime_root, prediction_date=prediction_date
    )
    missing = [name for name in ARTIFACT_FIELDS if artifacts[name] is None]
    if missing and not allow_missing_artifacts:
        raise EvidenceExportError(
            "missing required dated artifacts: " + ", ".join(missing) + "; "
            "use --allow-missing-artifacts to record an incomplete export"
        )

    created_at = datetime.now().astimezone().isoformat()
    ledger_rows = _recommendation_rows(
        elite_path=artifacts["elite_board"],
        kelly_path=artifacts["kelly_artifact"],
        trial_id=trial_id,
        run_date=run_date,
        prediction_date=prediction_date,
        code_sha=resolved_code_sha,
        config_hash=config_hash,
        notes=notes,
        created_at=created_at,
    )
    inferred_status = _infer_run_status(
        supplied=run_status,
        recommendation_count=len(ledger_rows),
        artifacts=artifacts,
        missing=missing,
    )

    manifest_notes = str(notes).strip()
    if missing:
        missing_note = "MISSING ARTIFACTS ALLOWED: " + ", ".join(missing)
        manifest_notes = (
            f"{manifest_notes}; {missing_note}" if manifest_notes else missing_note
        )
    artifact_values: dict[str, str] = {}
    for name in ARTIFACT_FIELDS:
        path = artifacts[name]
        artifact_values[f"{name}_path"] = _stored_path(path, repo_root) if path else ""
        artifact_values[f"{name}_sha256"] = _sha256(path) if path else ""

    providers = list(
        dict.fromkeys(row["provider_used"] for row in ledger_rows if row["provider_used"])
    )
    failure_reason = (
        "Required dated artifacts were missing during export: " + ", ".join(missing)
        if inferred_status == "failed_other" and missing
        else (
            "Existing artifacts did not provide an unambiguous successful-run signal."
            if inferred_status == "failed_other" and run_status is None
            else ""
        )
    )
    manifest_row = {
        "trial_id": trial_id,
        "run_date": run_date,
        "prediction_date": prediction_date,
        "code_sha": resolved_code_sha,
        "config_hash": config_hash,
        "run_status": inferred_status,
        "provider_attempted": "",
        "provider_used": "|".join(providers),
        "fallback_used": "",
        "released_recommendation_count": str(len(ledger_rows)),
        **artifact_values,
        "failure_reason": failure_reason,
        "manual_intervention": "",
        "notes": manifest_notes,
        "created_at": created_at,
    }

    _preflight_duplicates(
        manifest_path=manifest_path,
        ledger_path=ledger_path,
        manifest_row=manifest_row,
        ledger_rows=ledger_rows,
        allow_duplicates=allow_duplicates,
        allow_duplicate_manifest=allow_duplicate_manifest,
    )
    if not dry_run:
        _append_rows(manifest_path, MANIFEST_COLUMNS, [manifest_row])
        _append_rows(ledger_path, LEDGER_COLUMNS, ledger_rows)

    return ExportResult(
        manifest_row=manifest_row,
        ledger_rows=tuple(ledger_rows),
        artifacts=artifacts,
        dry_run=dry_run,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export existing dated CourtVision artifacts into the offline evidence "
            "manifest and ledger."
        )
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--prediction-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--run-date", help="YYYY-MM-DD; defaults to today's local date")
    parser.add_argument("--code-sha")
    parser.add_argument("--run-status", choices=sorted(VALID_RUN_STATUSES))
    parser.add_argument("--notes", default="")
    parser.add_argument("--allow-missing-artifacts", action="store_true")
    parser.add_argument("--allow-duplicates", action="store_true")
    parser.add_argument("--allow-duplicate-manifest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--ledger-path", type=Path, default=DEFAULT_LEDGER_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_run_to_evidence(
            **vars(args),
            repo_root=PROJECT_ROOT,
        )
    except EvidenceExportError as exc:
        print(f"Status: failed: {exc}")
        return 1

    action = "would append" if result.dry_run else "appended"
    print(f"Status: {'dry-run' if result.dry_run else 'complete'}")
    print(f"Daily manifest row {action}:")
    print(json.dumps(result.manifest_row, indent=2, sort_keys=True))
    print(f"Evidence ledger rows {action}: {len(result.ledger_rows)}")
    for row in result.ledger_rows:
        print(json.dumps(row, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
