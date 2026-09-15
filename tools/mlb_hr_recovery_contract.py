"""Explicit, offline contracts for successor MLB HR research automation.

No collector, task mutation, control discovery, or mutable current pointer lives
here. Configuration and every consumed source are named by path and digest.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
import re
import stat
import subprocess
import sys
from zoneinfo import ZoneInfo


class RecoveryContractError(ValueError):
    pass


RESEARCH_FLAGS = {
    "research_only": True,
    "approval_status": "not_approved",
    "eligible_for_betting": False,
    "eligible_for_official_pick": False,
}
MATCH_TOLERANCE_SECONDS = 120
PARENTS = {
    "predictor": "CourtVision MLB HR Prospective Predictor",
    "closing": "CourtVision MLB HR Closing Scheduler",
    "nightly": "CourtVision MLB HR Nightly Controller",
}


def assert_no_reparse_path(path: str | Path) -> None:
    """Reject aliases in every existing component, including Windows junctions."""
    source = Path(path)
    if ".." in source.parts:
        raise RecoveryContractError("parent traversal is not an explicit bound path")
    absolute = source.absolute()
    for component in (absolute, *absolute.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RecoveryContractError("symlink/reparse path component is forbidden")


def read_bound(path: str | Path, digest: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RecoveryContractError("explicit SHA256 required")
    source = Path(path)
    assert_no_reparse_path(source)
    if source.is_symlink() or not source.is_file():
        raise RecoveryContractError("bound input must be a regular existing file")
    payload = source.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise RecoveryContractError("bound input digest mismatch")
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryContractError("duplicate JSON key")
        result[key] = value
    return result


def read_json(payload: bytes):
    return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object)


def load_configuration(path: str | Path, digest: str) -> dict:
    config = read_json(read_bound(path, digest))
    required = {
        "schema_version", "repository_root", "python_executable", "python_version",
        "trial_root", "control_id", "control_manifest_sha256", "expected_commit",
        "cutover_operating_date", "timezone", "evidence_root", "odds_directory",
        "results_csv", "supplemental_shadow_enabled", "allow_task_registration",
        "allow_provider_collection", "evidence_kind", *RESEARCH_FLAGS,
    }
    if set(config) != required or config["schema_version"] != "mlb-hr-recovery-v1":
        raise RecoveryContractError("explicit recovery configuration schema mismatch")
    for key, value in RESEARCH_FLAGS.items():
        if type(config[key]) is not type(value) or config[key] != value:
            raise RecoveryContractError("research boundary mismatch")
    if config["timezone"] != "America/Toronto":
        raise RecoveryContractError("operating timezone must be America/Toronto")
    if not re.fullmatch(r"mlb-hr-control-v1-[0-9a-f]{20}", config["control_id"]):
        raise RecoveryContractError("explicit immutable control identity required")
    if not re.fullmatch(r"[0-9a-f]{40}", config["expected_commit"]):
        raise RecoveryContractError("exact runtime commit required")
    if not re.fullmatch(r"[0-9a-f]{64}", config["control_manifest_sha256"]):
        raise RecoveryContractError("exact control manifest digest required")
    date.fromisoformat(config["cutover_operating_date"])
    for key in ("supplemental_shadow_enabled", "allow_task_registration", "allow_provider_collection"):
        if type(config[key]) is not bool:
            raise RecoveryContractError("switches must be JSON booleans")
    # Supplemental recovery is a separate architecture and is never an implicit hook.
    if config["supplemental_shadow_enabled"]:
        raise RecoveryContractError("supplemental shadow is outside the minimal recovery")
    if config["evidence_kind"] not in ("prospective_research", "disposable_test"):
        raise RecoveryContractError("historical replay cannot use prospective automation")
    if config["evidence_kind"] == "disposable_test" and (
        config["allow_task_registration"] or config["allow_provider_collection"]
    ):
        raise RecoveryContractError("disposable rehearsal cannot register tasks or collect")
    for key in ("repository_root", "python_executable", "trial_root", "evidence_root", "odds_directory", "results_csv"):
        value = Path(config[key])
        assert_no_reparse_path(value)
        if not value.is_absolute() or value.is_symlink():
            raise RecoveryContractError(f"explicit absolute non-symlink path required: {key}")
    trial = Path(config["trial_root"]).resolve()
    evidence = Path(config["evidence_root"]).resolve()
    if trial == evidence or trial in evidence.parents or evidence in trial.parents:
        raise RecoveryContractError("external evidence and trial roots must be separate")
    if config["evidence_kind"] == "disposable_test" and "disposable" not in trial.name.lower():
        raise RecoveryContractError("disposable trial must have an explicit disposable namespace")
    return config


def validate_runtime(path: str | Path, digest: str) -> dict:
    config = load_configuration(path, digest)
    repository = Path(config["repository_root"]).resolve(strict=True)
    if repository != Path(__file__).resolve().parents[1]:
        raise RecoveryContractError("executing code root differs from configured repository")
    if Path(sys.executable).resolve() != Path(config["python_executable"]).resolve():
        raise RecoveryContractError("executing Python differs from configured interpreter")
    if ".".join(map(str, sys.version_info[:3])) != config["python_version"]:
        raise RecoveryContractError("executing Python version mismatch")
    def git(*args):
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-C", str(repository), *args], text=True
        ).strip()
    if git("rev-parse", "HEAD") != config["expected_commit"]:
        raise RecoveryContractError("runtime provenance mismatch")
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise RecoveryContractError("runtime must have clean provenance")
    from courtvision.sports.mlb.training import hr_prospective_trial as trial
    control_dir = Path(config["trial_root"]) / "controls" / config["control_id"]
    read_bound(control_dir / "control_manifest_v1.json", config["control_manifest_sha256"])
    control, _, _ = trial._read_control(
        control_dir, trial_root=Path(config["trial_root"]),
        repository_root=repository, revalidate_model=True,
    )
    activation = control["identity_material"]["activation_configuration"]
    immutable_kind = activation.get("evidence_kind", "prospective_research")
    immutable_excluded = activation.get("promotion_excluded", False)
    if config["evidence_kind"] == "disposable_test":
        if immutable_kind != "disposable_test" or immutable_excluded is not True:
            raise RecoveryContractError(
                "disposable configuration requires immutable disposable_test promotion exclusion"
            )
    elif immutable_kind != "prospective_research" or immutable_excluded is not False:
        raise RecoveryContractError(
            "prospective configuration requires an ordinary promotion-eligible control"
        )
    frozen = trial._git_from_mapping(control["identity_material"]["activation_git_provenance"])
    trial._validate_current_git(frozen, repository)
    created_date = trial._parse_utc(control["created_at_utc"], "control creation").astimezone(ZoneInfo("America/Toronto")).date()
    if date.fromisoformat(config["cutover_operating_date"]) < created_date:
        raise RecoveryContractError("cutover precedes successor control creation")
    config["control_dir"] = str(control_dir)
    return config


def validate_publication(configuration: Path, configuration_sha256: str,
                         predictions_csv: Path, operating_date: str) -> dict:
    config = validate_runtime(configuration, configuration_sha256)
    from courtvision.sports.mlb.training import hr_prospective_trial as trial
    control_dir = Path(config["control_dir"])
    if predictions_csv.resolve().parent.parent != control_dir / "dates" / operating_date:
        raise RecoveryContractError("prediction path does not bind intended date/control")
    control, digest, _ = trial._read_control(control_dir, trial_root=Path(config["trial_root"]))
    predictions, _, _, manifest_digest, _ = trial._validate_prediction_artifact(
        predictions_csv=predictions_csv, control_manifest=control,
        control_manifest_digest=digest, control_dir=control_dir,
    )
    trial._verify_ledger_linkage(
        ledger_path=control_dir / "prospective_ledger.csv", predictions=predictions,
        prediction_manifest_digest=manifest_digest,
        predictions_csv_sha256=trial._file_sha256(predictions_csv),
    )
    if not predictions or any(row["operating_date"] != operating_date for row in predictions):
        raise RecoveryContractError("publication is empty or has a different operating date")
    return {"success": True, "control_id": config["control_id"],
            "operating_date": operating_date, "prediction_count": len(predictions)}


def verify_task_identity(tasks: list[dict], expected: dict) -> str:
    """Read-only identity check. Missing and disabled never imply activation."""
    required = {"TaskName", "TaskPath", "Execute", "Arguments", "WorkingDirectory"}
    if set(expected) != required or expected["TaskPath"] != "\\":
        raise RecoveryContractError("exact root task identity required")
    matches = [task for task in tasks if task.get("TaskName") == expected["TaskName"]
               and task.get("TaskPath") == expected["TaskPath"]]
    if not matches:
        return "MISSING_NO_ACTION"
    if len(matches) != 1:
        raise RecoveryContractError("ambiguous task identity")
    task = matches[0]
    actions = task.get("Actions")
    if not isinstance(actions, list) or len(actions) != 1:
        raise RecoveryContractError("ambiguous task action")
    if any(actions[0].get(key) != expected[key] for key in ("Execute", "Arguments", "WorkingDirectory")):
        raise RecoveryContractError("task action identity mismatch")
    if task.get("Enabled") is not False or task.get("State") != "Disabled":
        raise RecoveryContractError("task is not contained")
    return "EXACT_DISABLED_NO_ACTION"


def enrich_odds(*, odds_path: Path, odds_sha256: str, schedule_path: Path,
                schedule_sha256: str, operating_date: str, output: Path,
                require_complete_slate: bool = False) -> dict:
    """Bind unique official identities without rewriting provider timestamps.

    Identity-only callers may validate a subset. Prediction requires the complete
    eligible official slate, using this same matcher before any revision is written.
    """
    date.fromisoformat(operating_date)
    source = read_bound(odds_path, odds_sha256)
    schedule = read_json(read_bound(schedule_path, schedule_sha256))
    games = []
    for entry in schedule.get("dates", []):
        if entry.get("date") == operating_date:
            games.extend(entry.get("games", []))
    if not games:
        raise RecoveryContractError("explicit official operating-date schedule required")
    reader = csv.DictReader(io.StringIO(source.decode("utf-8-sig")))
    required_columns = {"event_id", "commence_time", "home_team", "away_team", "market", "side", "point"}
    if (
        not reader.fieldnames
        or len(reader.fieldnames) != len(set(reader.fieldnames))
        or not required_columns.issubset(reader.fieldnames)
    ):
        raise RecoveryContractError("odds source headers are missing or ambiguous")
    rows = list(reader)
    if not rows or not reader.fieldnames:
        raise RecoveryContractError("odds source is empty")
    def timestamp(value):
        if not isinstance(value, str):
            raise RecoveryContractError("explicit schedule timestamp required")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RecoveryContractError("invalid schedule timestamp") from exc
        if parsed.tzinfo is None:
            raise RecoveryContractError("timezone-aware schedule identity required")
        return parsed.astimezone(timezone.utc)

    operating_timezone = ZoneInfo("America/Toronto")
    official_games = []
    for game in games:
        if game.get("gameType") != "R" or game.get("status", {}).get("detailedState") in (
            "Cancelled", "Postponed",
        ):
            continue
        official_start = timestamp(game.get("gameDate"))
        if official_start.astimezone(operating_timezone).date().isoformat() != operating_date:
            continue
        if type(game.get("gamePk")) is not int or game["gamePk"] <= 0:
            if require_complete_slate:
                raise RecoveryContractError("missing authoritative regular-season gamePk")
            continue
        official_games.append((game, official_start))
    official_ids = {str(game["gamePk"]) for game, _ in official_games}
    if len(official_ids) != len(official_games):
        raise RecoveryContractError("ambiguous authoritative regular-season gamePk")

    seen = {}
    provider_identities = {}
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise RecoveryContractError("odds source row width does not match its headers")
        if row.get("market") not in ("batter_home_runs", "batter_home_runs_alternate") or row.get("side", "").lower() != "over" or row.get("point") not in ("0.5", ".5"):
            raise RecoveryContractError("unsupported market/side/line")
        start = timestamp(row["commence_time"])
        if start.astimezone(operating_timezone).date().isoformat() != operating_date:
            raise RecoveryContractError("odds operating date mismatch")
        if not row["home_team"] or not row["away_team"]:
            raise RecoveryContractError("explicit canonical home and away teams required")
        identity = (row["commence_time"], row["home_team"], row["away_team"])
        if (not row["event_id"]
                or provider_identities.setdefault(row["event_id"], identity) != identity):
            raise RecoveryContractError("provider event identity conflict")
        matches = [(game, official_start) for game, official_start in official_games
                   if abs((start - official_start).total_seconds()) <= MATCH_TOLERANCE_SECONDS
                   and game["teams"]["home"]["team"]["name"] == row["home_team"]
                   and game["teams"]["away"]["team"]["name"] == row["away_team"]]
        if len(matches) != 1:
            raise RecoveryContractError("missing/ambiguous authoritative regular-season identity")
        game, official_start = matches[0]
        game_id = str(game["gamePk"])
        if not row.get("event_id") or seen.setdefault(row["event_id"], game_id) != game_id:
            raise RecoveryContractError("provider event identity conflict")
        row.update(event_type="regular_season", game_type="R", game_pk=game_id,
                   official_commence_time_utc=official_start.isoformat().replace("+00:00", "Z"),
                   schedule_start_drift_seconds=(start - official_start).total_seconds(),
                   schedule_sha256=schedule_sha256, source_odds_sha256=odds_sha256)
    if len(set(seen.values())) != len(seen):
        raise RecoveryContractError("multiple provider events resolve to one official game")
    if require_complete_slate and set(seen.values()) != official_ids:
        raise RecoveryContractError("provider slate does not match complete official game identities")
    columns = list(reader.fieldnames)
    for key in ("event_type", "game_type", "game_pk", "official_commence_time_utc",
                "schedule_start_drift_seconds", "schedule_sha256", "source_odds_sha256"):
        if key not in columns:
            columns.append(key)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = stream.getvalue().encode("utf-8")
    assert_no_reparse_path(output)
    with output.open("xb") as handle:
        handle.write(payload)
    return {"success": True, "path": str(output), "sha256": hashlib.sha256(payload).hexdigest(),
            "rows": len(rows), "source_odds_sha256": odds_sha256,
            "operating_date": operating_date,
            "match_tolerance_seconds": MATCH_TOLERANCE_SECONDS,
            "complete_slate_required": require_complete_slate,
            "provider_events": len(seen), "canonical_game_bindings": len(set(seen.values())),
            "eligible_official_games": len(official_games),
            "ambiguous_bindings": 0, "unmatched_bindings": 0,
            "schedule_sha256": schedule_sha256, **RESEARCH_FLAGS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-runtime")
    validate.add_argument("--configuration", type=Path, required=True)
    validate.add_argument("--configuration-sha256", required=True)
    publication = commands.add_parser("validate-publication")
    publication.add_argument("--configuration", type=Path, required=True)
    publication.add_argument("--configuration-sha256", required=True)
    publication.add_argument("--predictions-csv", type=Path, required=True)
    publication.add_argument("--operating-date", required=True)
    enrich = commands.add_parser("enrich-odds")
    enrich.add_argument("--require-complete-slate", action="store_true")
    for name in ("odds-path", "schedule-path", "output"):
        enrich.add_argument("--" + name, type=Path, required=True)
    for name in ("odds-sha256", "schedule-sha256", "operating-date"):
        enrich.add_argument("--" + name, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "validate-runtime":
            result = validate_runtime(args["configuration"], args["configuration_sha256"])
        elif command == "validate-publication":
            result = validate_publication(**args)
        else:
            result = enrich_odds(**args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
