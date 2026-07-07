"""Create the offline Day 0 preregistration manifest for an NBA paper trial."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "history" / "evidence" / "day0"
ALLOWED_INVESTOR_AUDIT = "docs/CODEX_INVESTOR_AUDIT_2026_07_07.md"
TRIAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

POLICY_DEFAULTS = {
    "source_book_rule": "best_available_canonical_operator_price",
    "line_capture_cutoff": "pregame_operator_run",
    "closing_line_source": "same_provider_when_available",
    "closing_line_cutoff": "market_close",
    "timezone": "America/Toronto",
    "settlement_policy": "official_final_result_flat_1u",
}

ENV_DEFAULTS = {
    "COURTVISION_MODE": "betting",
    "COURTVISION_BANKROLL": "1000",
    "COURTVISION_MAX_DAILY_EXPOSURE": "0.08",
    "BALLDONTLIE_REQUEST_TIMEOUT": "30",
    "BALLDONTLIE_V1_BASE_URL": "https://api.balldontlie.io/v1",
    "BALLDONTLIE_V2_BASE_URL": "https://api.balldontlie.io/v2",
    "BALLDONTLIE_VENDORS": "fanduel,draftkings,fanatics,caesars,betrivers",
    "COURTVISION_HTTP_RETRIES": "3",
    "COURTVISION_HTTP_BACKOFF": "1.5",
    "ELITE_MARKET_MODE": "points_only",
    "ELITE_ALLOWED_MARKETS": "",
    "COURTVISION_ENABLE_LEGACY_PIPELINE": "",
    "COURTVISION_PLAYER_POINTS_RECALIBRATION": "off",
}


class Day0ManifestError(RuntimeError):
    """Raised when a Day 0 manifest cannot be created safely."""


class DirtyWorkingTreeError(Day0ManifestError):
    """Raised when git status contains a non-approved working-tree change."""


def _clean_env_value(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read the simple KEY=VALUE format used by the canonical auth bootstrap."""

    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Day0ManifestError(f"could not read {path}: {exc}") from exc

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _clean_env_value(value)
    return values


def resolve_environment(
    repo_root: Path = PROJECT_ROOT,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve repository .env values with process environment precedence."""

    resolved = _read_dotenv(Path(repo_root) / ".env")
    source = os.environ if process_env is None else process_env
    resolved.update({str(key): str(value) for key, value in source.items()})
    return resolved


def _env_or_default(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None:
        return ENV_DEFAULTS[key]
    return str(value)


def _number(env: Mapping[str, str], key: str, number_type: type[int] | type[float]) -> int | float:
    raw_value = _env_or_default(env, key)
    if raw_value == "":
        raw_value = ENV_DEFAULTS[key]
    try:
        return number_type(raw_value)
    except ValueError as exc:
        raise Day0ManifestError(f"{key} does not resolve to a valid {number_type.__name__}") from exc


def _csv_values(raw_value: str, *, sort_values: bool = False) -> list[str]:
    values = [part.strip().lower() for part in raw_value.split(",") if part.strip()]
    if sort_values:
        return sorted(set(values))
    return values


def resolve_config_object(
    env: Mapping[str, str],
    *,
    source_book_rule: str = POLICY_DEFAULTS["source_book_rule"],
    line_capture_cutoff: str = POLICY_DEFAULTS["line_capture_cutoff"],
    closing_line_source: str = POLICY_DEFAULTS["closing_line_source"],
    closing_line_cutoff: str = POLICY_DEFAULTS["closing_line_cutoff"],
    timezone_name: str = POLICY_DEFAULTS["timezone"],
    settlement_policy: str = POLICY_DEFAULTS["settlement_policy"],
) -> dict[str, Any]:
    """Return the resolved, secret-safe canonical NBA operator configuration."""

    mode = _env_or_default(env, "COURTVISION_MODE").strip().lower()
    bankroll = _number(env, "COURTVISION_BANKROLL", float)
    max_daily_exposure = _number(env, "COURTVISION_MAX_DAILY_EXPOSURE", float)
    request_timeout = _number(env, "BALLDONTLIE_REQUEST_TIMEOUT", int)
    retries = _number(env, "COURTVISION_HTTP_RETRIES", int)
    backoff = _number(env, "COURTVISION_HTTP_BACKOFF", float)

    recalibration = (
        _env_or_default(env, "COURTVISION_PLAYER_POINTS_RECALIBRATION").strip().lower()
    )
    if recalibration not in {"off", "shadow", "enabled"}:
        recalibration = "off"

    return {
        "COURTVISION_MODE": mode,
        "COURTVISION_BANKROLL": bankroll,
        "COURTVISION_MAX_DAILY_EXPOSURE": max_daily_exposure,
        "BALLDONTLIE_REQUEST_TIMEOUT": request_timeout,
        "BALLDONTLIE_V1_BASE_URL": _env_or_default(
            env, "BALLDONTLIE_V1_BASE_URL"
        ).rstrip("/"),
        "BALLDONTLIE_V2_BASE_URL": _env_or_default(
            env, "BALLDONTLIE_V2_BASE_URL"
        ).rstrip("/"),
        "BALLDONTLIE_VENDORS": _csv_values(
            _env_or_default(env, "BALLDONTLIE_VENDORS"), sort_values=True
        ),
        "COURTVISION_HTTP_RETRIES": retries,
        "COURTVISION_HTTP_BACKOFF": backoff,
        "ELITE_MARKET_MODE": (
            _env_or_default(env, "ELITE_MARKET_MODE").strip().lower()
            or ENV_DEFAULTS["ELITE_MARKET_MODE"]
        ),
        "ELITE_ALLOWED_MARKETS": _csv_values(_env_or_default(env, "ELITE_ALLOWED_MARKETS")),
        "COURTVISION_ENABLE_LEGACY_PIPELINE": (
            _env_or_default(env, "COURTVISION_ENABLE_LEGACY_PIPELINE").strip().lower()
            in {"1", "true", "yes", "on"}
        ),
        "COURTVISION_PLAYER_POINTS_RECALIBRATION": recalibration,
        "BALLDONTLIE_API_KEY": (
            "configured" if _clean_env_value(env.get("BALLDONTLIE_API_KEY")) else "missing"
        ),
        "source_book_rule": source_book_rule,
        "line_capture_cutoff": line_capture_cutoff,
        "closing_line_source": closing_line_source,
        "closing_line_cutoff": closing_line_cutoff,
        "timezone": timezone_name,
        "settlement_policy": settlement_policy,
    }


def canonical_config_json(config_object: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(config_object), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def compute_config_hash(config_object: Mapping[str, Any]) -> str:
    canonical = canonical_config_json(config_object).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _run_git(repo_root: Path, args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
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
        raise Day0ManifestError(f"git {' '.join(args)} failed{suffix}") from exc
    return completed.stdout.rstrip("\r\n")


def capture_git_state(
    repo_root: Path = PROJECT_ROOT,
    *,
    allow_untracked_investor_audit: bool = False,
) -> dict[str, str]:
    """Capture git identity and reject all non-approved working-tree changes."""

    repo_root = Path(repo_root).resolve()
    code_sha = _run_git(repo_root, ["rev-parse", "HEAD"])
    branch = _run_git(repo_root, ["branch", "--show-current"])
    status_short = _run_git(repo_root, ["status", "--short", "--untracked-files=all"])
    status_lines = status_short.splitlines() if status_short else []
    allowed_line = f"?? {ALLOWED_INVESTOR_AUDIT}"
    disallowed = [
        line
        for line in status_lines
        if not (allow_untracked_investor_audit and line == allowed_line)
    ]
    if disallowed:
        raise DirtyWorkingTreeError(
            "working tree is not clean; refusing Day 0 manifest creation:\n"
            + "\n".join(disallowed)
        )

    tree_status = "clean"
    if status_lines:
        tree_status = "clean_with_allowed_untracked_investor_audit"

    return {
        "code_sha": code_sha,
        "git_branch": branch,
        "git_status_short": status_short,
        "working_tree_status": tree_status,
    }


def parse_trial_date(value: str, field_name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise Day0ManifestError(f"{field_name} must be a valid date in YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise Day0ManifestError(f"{field_name} must be a valid date in YYYY-MM-DD format")
    return parsed


def create_evidence_day0_manifest(
    *,
    trial_id: str,
    start_date: str,
    end_date: str,
    repo_root: Path = PROJECT_ROOT,
    output_dir: Path | None = None,
    process_env: Mapping[str, str] | None = None,
    allow_untracked_investor_audit: bool = False,
    force: bool = False,
    source_book_rule: str = POLICY_DEFAULTS["source_book_rule"],
    line_capture_cutoff: str = POLICY_DEFAULTS["line_capture_cutoff"],
    closing_line_source: str = POLICY_DEFAULTS["closing_line_source"],
    closing_line_cutoff: str = POLICY_DEFAULTS["closing_line_cutoff"],
    timezone_name: str = POLICY_DEFAULTS["timezone"],
    settlement_policy: str = POLICY_DEFAULTS["settlement_policy"],
) -> tuple[Path, dict[str, Any], str]:
    """Create and return ``(path, manifest, write_status)``."""

    if not TRIAL_ID_PATTERN.fullmatch(trial_id):
        raise Day0ManifestError(
            "trial_id must start with an alphanumeric character and contain only "
            "letters, numbers, dots, underscores, or hyphens"
        )

    parsed_start = parse_trial_date(start_date, "start_date")
    parsed_end = parse_trial_date(end_date, "end_date")
    if parsed_end < parsed_start:
        raise Day0ManifestError("end_date must be on or after start_date")

    repo_root = Path(repo_root).resolve()
    destination_dir = Path(output_dir) if output_dir is not None else (
        repo_root / "data" / "history" / "evidence" / "day0"
    )
    manifest_path = destination_dir / f"day0_manifest_{trial_id}.json"
    if manifest_path.exists() and not force:
        raise Day0ManifestError(
            f"manifest already exists: {manifest_path}; pass --force to overwrite it"
        )

    git_state = capture_git_state(
        repo_root, allow_untracked_investor_audit=allow_untracked_investor_audit
    )
    resolved_env = resolve_environment(repo_root, process_env)
    config_object = resolve_config_object(
        resolved_env,
        source_book_rule=source_book_rule,
        line_capture_cutoff=line_capture_cutoff,
        closing_line_source=closing_line_source,
        closing_line_cutoff=closing_line_cutoff,
        timezone_name=timezone_name,
        settlement_policy=settlement_policy,
    )
    config_hash = compute_config_hash(config_object)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_type": "courtvision_nba_forward_paper_trial_day0",
        "trial_id": trial_id,
        "start_date": parsed_start.isoformat(),
        "end_date": parsed_end.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        **git_state,
        "config_hash": config_hash,
        "config_object": config_object,
    }

    destination_dir.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    try:
        with manifest_path.open(mode, encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise Day0ManifestError(
            f"manifest already exists: {manifest_path}; pass --force to overwrite it"
        ) from exc
    except OSError as exc:
        raise Day0ManifestError(f"could not write manifest {manifest_path}: {exc}") from exc

    return manifest_path.resolve(), manifest, "overwritten" if force else "created"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an offline Day 0 manifest for the frozen NBA forward paper trial."
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--start-date", required=True, help="Trial start date in YYYY-MM-DD format.")
    parser.add_argument("--end-date", required=True, help="Trial end date in YYYY-MM-DD format.")
    parser.add_argument("--source-book-rule", default=POLICY_DEFAULTS["source_book_rule"])
    parser.add_argument("--line-capture-cutoff", default=POLICY_DEFAULTS["line_capture_cutoff"])
    parser.add_argument("--closing-line-source", default=POLICY_DEFAULTS["closing_line_source"])
    parser.add_argument("--closing-line-cutoff", default=POLICY_DEFAULTS["closing_line_cutoff"])
    parser.add_argument("--timezone", dest="timezone_name", default=POLICY_DEFAULTS["timezone"])
    parser.add_argument("--settlement-policy", default=POLICY_DEFAULTS["settlement_policy"])
    parser.add_argument(
        "--allow-untracked-investor-audit",
        action="store_true",
        help=f"Allow only the untracked {ALLOWED_INVESTOR_AUDIT} file.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing manifest.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path, manifest, write_status = create_evidence_day0_manifest(
            trial_id=args.trial_id,
            start_date=args.start_date,
            end_date=args.end_date,
            allow_untracked_investor_audit=args.allow_untracked_investor_audit,
            force=args.force,
            source_book_rule=args.source_book_rule,
            line_capture_cutoff=args.line_capture_cutoff,
            closing_line_source=args.closing_line_source,
            closing_line_cutoff=args.closing_line_cutoff,
            timezone_name=args.timezone_name,
            settlement_policy=args.settlement_policy,
        )
    except Day0ManifestError as exc:
        print(f"Status: failed: {exc}")
        return 1

    print(f"Manifest path: {path}")
    print(f"Code SHA: {manifest['code_sha']}")
    print(f"Config hash: {manifest['config_hash']}")
    print(f"Status: {write_status} ({manifest['working_tree_status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
