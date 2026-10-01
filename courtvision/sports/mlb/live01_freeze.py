"""Create-once, row-hash-bound prospective Hits test articles for LIVE-01."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import re

from courtvision.sports.mlb.ab_projection import (
    assemble_sovereign_batter_hits_features, project_batter_at_bats_v2,
)
from courtvision.sports.mlb.batter_hits import compute_batter_hits_probability
from courtvision.sports.mlb.fact_backfill_evidence import (
    BackfillError, digest, publish_bytes, publish_document, read_document, utc_now,
)
from courtvision.sports.mlb.fact_ledger import _plain_path
from courtvision.sports.mlb.game_facts import canonical_json
from courtvision.sports.mlb.hits_season_ledger import coverage_from_payload
from courtvision.sports.mlb.live01_evidence import OFFICIAL_TYPES

SCHEMA = "cv_mlb_hits_live01_prediction_v1"
SAFETY = {"research_only": True, "eligible_for_betting": False, "kelly_eligible": False,
          "eligible_for_official_pick": False, "approval_status": "not_approved"}
FIELDS = tuple("""prediction_schema_version prediction_run_id prediction_id operating_date
mlbam_game_id official_game_date official_game_type scheduled_start_utc
home_team_id home_team_name away_team_id away_team_name mlbam_player_id player_name
team_id team_side batting_order_position lineup_status season season_hits season_at_bats
distinct_batting_games projected_at_bats projection_method projection_model_version
model_id model_version feature_schema_version model_probability evidence_cutoff generated_at
coverage_manifest_hash season_aggregate_hash repository_commit_sha hits_coverage_policy
source_refs research_only eligible_for_betting kelly_eligible eligible_for_official_pick
approval_status prediction_payload_sha256""".split())
INTEGER_FIELDS = frozenset({"batting_order_position", "season", "season_hits", "season_at_bats",
                            "distinct_batting_games"})
FLOAT_FIELDS = frozenset({"projected_at_bats", "model_probability"})
BOOL_FIELDS = frozenset(k for k, v in SAFETY.items() if type(v) is bool)


def _clock(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise BackfillError("prediction timestamps must be explicit and timezone-aware")
    return result


def row_hash(row: dict) -> str:
    return digest({key: value for key, value in row.items() if key != "prediction_payload_sha256"})


def validate_row(row: dict) -> None:
    if set(row) != set(FIELDS) or row["prediction_schema_version"] != SCHEMA:
        raise BackfillError("prediction fields/schema differ from the dedicated model payload")
    if row_hash(row) != row["prediction_payload_sha256"]:
        raise BackfillError("prediction row hash mismatch")
    for key, value in SAFETY.items():
        if type(row[key]) is not type(value) or row[key] != value:
            raise BackfillError("prediction safety flags differ")
    for key in INTEGER_FIELDS:
        if type(row[key]) is not int or row[key] < 0:
            raise BackfillError("prediction integer evidence is invalid")
    for key in FLOAT_FIELDS:
        if type(row[key]) not in (int, float) or not math.isfinite(row[key]):
            raise BackfillError("prediction numeric evidence is invalid")
    for key in ("mlbam_game_id", "mlbam_player_id", "home_team_id", "away_team_id", "team_id"):
        if not isinstance(row[key], str) or re.fullmatch(r"[1-9][0-9]*", row[key]) is None:
            raise BackfillError("prediction canonical identity is invalid")
    if (row["official_game_type"] not in OFFICIAL_TYPES
            or row["official_game_date"] != row["operating_date"]
            or date.fromisoformat(row["official_game_date"]).year != row["season"]
            or row["team_side"] not in {"away", "home"}
            or row["team_id"] != row[row["team_side"] + "_team_id"]
            or row["home_team_id"] == row["away_team_id"]):
        raise BackfillError("prediction game/team identity conflicts")
    if not _clock(row["evidence_cutoff"]) <= _clock(row["generated_at"]) < _clock(row["scheduled_start_utc"]):
        raise BackfillError("prediction clocks are not prospective")
    if (row["lineup_status"] != "statsapi_batting_order_present"
            or not 1 <= row["batting_order_position"] <= 9
            or row["season_at_bats"] <= 0 or row["season_hits"] > row["season_at_bats"]
            or row["distinct_batting_games"] <= 0
            or row["projection_model_version"] != "cv_ab_projection_v2"
            or row["model_id"] != "mlb-batter-hits-independent-at-bat-baseline"
            or row["feature_schema_version"] != "mlb-batter-hits-baseline-features-v1"):
        raise BackfillError("prediction does not identify the qualified baseline")
    policy_versions = {"regular-season-prior-date-v1": "research-v1",
        "official-regular-and-observed-postseason-prior-date-v1": "research-v1-live01-official-history"}
    if policy_versions.get(row["hits_coverage_policy"]) != row["model_version"]:
        raise BackfillError("model version does not bind coverage semantics")
    projected = row["season_at_bats"] / row["distinct_batting_games"]
    probability = 1 - (1 - row["season_hits"] / row["season_at_bats"]) ** projected
    if row["projected_at_bats"] != projected or row["model_probability"] != probability:
        raise BackfillError("frozen baseline arithmetic mismatch")
    for name, size in (("coverage_manifest_hash", 64), ("season_aggregate_hash", 64),
                       ("repository_commit_sha", 40), ("prediction_payload_sha256", 64)):
        if re.fullmatch(r"[0-9a-f]{" + str(size) + "}", row[name]) is None:
            raise BackfillError("invalid prediction provenance hash")
    refs = row["source_refs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or not r for r in refs):
        raise BackfillError("missing model source references")
    required = {f'cv-ledger-coverage:sha256:{row["coverage_manifest_hash"]}',
                f'cv-ledger-season:sha256:{row["season_aggregate_hash"]}',
                "COURTVISION_GAME_FACT_LEDGER", "cv_ab_projection_v2",
                "repository-commit:" + row["repository_commit_sha"]}
    if not required <= set(refs):
        raise BackfillError("prediction source identities are not bound")
    if any("the_odds_api" in ref or "sportsbook" in ref for ref in refs):
        raise BackfillError("market provenance cannot enter a frozen model payload")
    expected_id = digest({key: row[key] for key in
        ("prediction_schema_version", "operating_date", "mlbam_game_id", "mlbam_player_id")})
    if row["prediction_id"] != expected_id:
        raise BackfillError("prediction identity mismatch")


def prediction_row(acquired, *, schedule_row: dict, run_id: str, repository_sha: str,
                   coverage_policy: str, model_version: str, generated_at: datetime) -> tuple[dict, dict]:
    """The only probability path: canonical ledger -> existing v2 -> existing formula."""
    features = assemble_sovereign_batter_hits_features(acquired, generated_at=generated_at)
    projection = project_batter_at_bats_v2(acquired, generated_at=generated_at)
    output = compute_batter_hits_probability(features, generated_at=generated_at)
    player, season, lineup = acquired.player_binding, acquired.season_evidence, acquired.lineup_evidence
    event = player.event_binding.scheduled_event
    raw = schedule_row["source_game"]
    row = dict(prediction_schema_version=SCHEMA, prediction_run_id=run_id,
        operating_date=event.operating_date.isoformat(), mlbam_game_id=event.event_id,
        official_game_date=raw["officialDate"], official_game_type=raw["gameType"],
        scheduled_start_utc=event.scheduled_start_utc.isoformat(),
        home_team_id=event.home_team_id, home_team_name=event.home_team,
        away_team_id=event.away_team_id, away_team_name=event.away_team,
        mlbam_player_id=player.mlbam_player_id, player_name=player.roster_player.player_name,
        team_id=player.team_id, team_side=player.team_side,
        batting_order_position=lineup.batting_order_position, lineup_status=lineup.lineup_status,
        season=season.season, season_hits=season.hits, season_at_bats=season.at_bats,
        distinct_batting_games=season.distinct_batting_games,
        projected_at_bats=projection.projected_at_bats, projection_method=projection.projection_method,
        projection_model_version=projection.model_version, model_id=output.model_id,
        model_version=model_version, feature_schema_version=output.feature_schema_version,
        model_probability=output.model_probability, evidence_cutoff=features.evidence_cutoff.isoformat(),
        generated_at=generated_at.isoformat(), coverage_manifest_hash=season.coverage.manifest_hash,
        season_aggregate_hash=season.season_aggregate_hash, repository_commit_sha=repository_sha,
        hits_coverage_policy=coverage_policy,
        source_refs=list(dict.fromkeys((*features.source_refs, "COURTVISION_GAME_FACT_LEDGER",
            "cv_ab_projection_v2", "repository-commit:" + repository_sha))),
        **SAFETY)
    row["prediction_id"] = digest({key: row[key] for key in
        ("prediction_schema_version", "operating_date", "mlbam_game_id", "mlbam_player_id")})
    row["prediction_payload_sha256"] = row_hash(row)
    validate_row(row)
    return row, {"coverage": season.coverage.to_envelope(), "aggregate": season.aggregate_manifest()}


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        validate_row(row)
        values = dict(row)
        values["source_refs"] = canonical_json(values["source_refs"]).decode("utf-8")
        for field in BOOL_FIELDS:
            values[field] = "true" if values[field] else "false"
        writer.writerow(values)
    return stream.getvalue().encode("utf-8")


def read_rows(raw: bytes) -> list[dict]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
    if reader.fieldnames != list(FIELDS):
        raise BackfillError("frozen CSV column identity mismatch")
    rows = []
    for row in reader:
        for field in INTEGER_FIELDS:
            row[field] = int(row[field])
        for field in FLOAT_FIELDS:
            row[field] = float(row[field])
        for field in BOOL_FIELDS:
            if row[field] not in {"true", "false"}:
                raise BackfillError("invalid CSV research flag")
            row[field] = row[field] == "true"
        row["source_refs"] = json.loads(row["source_refs"])
        validate_row(row)
        rows.append(row)
    if csv_bytes(rows) != raw:
        raise BackfillError("prediction CSV is not canonical")
    return rows


def _verify_artifacts(root: Path) -> tuple[dict, list[dict]]:
    _plain_path(root)
    manifest = read_document(root / "manifest.json")
    if (manifest["schema_version"] != SCHEMA
            or set(manifest["artifacts"]) != {"predictions", "exclusions", "sources"}
            or type(manifest["row_count"]) is not int or manifest["row_count"] < 0
            or any(manifest[key] != value for key, value in SAFETY.items())):
        raise BackfillError("invalid dedicated frozen run manifest")
    for label, record in manifest["artifacts"].items():
        path = root / (label + (".csv" if label == "predictions" else ".json"))
        _plain_path(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise BackfillError("frozen artifact file hash mismatch")
    rows = read_rows((root / "predictions.csv").read_bytes())
    sources = read_document(root / "sources.json")
    if len(rows) != manifest["row_count"]:
        raise BackfillError("frozen row count mismatch")
    ids = set()
    rosters = {}
    for row in rows:
        key = (row["mlbam_game_id"], row["mlbam_player_id"])
        if key in ids:
            raise BackfillError("duplicate frozen game/player")
        ids.add(key)
        if (row["repository_commit_sha"] != manifest["repository_commit_sha"]
                or row["prediction_run_id"] != manifest["prediction_run_id"]
                or row["operating_date"] != manifest["operating_date"]):
            raise BackfillError("row/run/repository binding mismatch")
        supplied = sources[row["prediction_id"]]
        roster = supplied["roster"]
        player_ids = [p["mlbam_player_id"] for p in roster]
        matching = [p for p in roster if p["mlbam_player_id"] == row["mlbam_player_id"]]
        if (len(player_ids) != len(set(player_ids)) or len(matching) != 1
                or any(p["mlbam_game_id"] != row["mlbam_game_id"] for p in roster)
                or matching[0]["player_name"] != row["player_name"]
                or matching[0]["team_id"] != row["team_id"]
                or matching[0]["team_side"] != row["team_side"]):
            raise BackfillError("frozen roster does not bind the canonical player")
        previous = rosters.setdefault(row["mlbam_game_id"], roster)
        if previous != roster:
            raise BackfillError("conflicting frozen roster for one game")
        coverage = coverage_from_payload(supplied["coverage"])
        aggregate = supplied["aggregate"]
        if (coverage.manifest_hash != row["coverage_manifest_hash"]
                or coverage.target_game_id != row["mlbam_game_id"]
                or coverage.mlbam_player_id != row["mlbam_player_id"]
                or coverage.target_game_date.isoformat() != row["official_game_date"]
                or not coverage.complete
                or coverage.coverage_through != coverage.target_game_date - timedelta(days=1)
                or not coverage.expected_records
                or coverage.aggregation_cutoff > _clock(row["generated_at"])
                or digest(aggregate) != row["season_aggregate_hash"]
                or aggregate["coverage"] != coverage.to_envelope()
                or aggregate["source"] != "COURTVISION_GAME_FACT_LEDGER"
                or aggregate["season"] != row["season"]
                or aggregate["mlbam_player_id"] != row["mlbam_player_id"]
                or aggregate["at_bats"] != row["season_at_bats"]
                or aggregate["hits"] != row["season_hits"]
                or aggregate["distinct_batting_games"] != row["distinct_batting_games"]):
            raise BackfillError("frozen season source binding mismatch")
        expected = {(ref.mlbam_game_id, row["mlbam_player_id"], "BATTER"): ref.factual_record_hash
                    for ref in coverage.expected_records}
        actual = {tuple(ref["logical_identity"]): ref["factual_record_hash"]
                  for ref in aggregate["records"]}
        if actual != expected or len(actual) != len(aggregate["records"]):
            raise BackfillError("frozen aggregate differs from independent expected facts")
    if set(sources) != {row["prediction_id"] for row in rows}:
        raise BackfillError("source population differs from frozen population")
    return manifest, rows


def verify_freeze(root: Path) -> tuple[dict, list[dict]]:
    manifest, rows = _verify_artifacts(root)
    receipt = read_document(root / "freeze.json")
    if receipt["manifest_hash"] != digest(manifest):
        raise BackfillError("freeze receipt does not bind the manifest")
    durable = _clock(receipt["prediction_artifact_durable_at"])
    if any(not _clock(row["generated_at"]) <= durable < _clock(row["scheduled_start_utc"]) for row in rows):
        raise BackfillError("prediction was not durably frozen before game start")
    if rows:
        claim = read_document(root.parent / "frozen-cohort.json")
        if claim != {"prediction_run_id": manifest["prediction_run_id"], "manifest_hash": digest(manifest)}:
            raise BackfillError("same-date frozen cohort claim mismatch")
    return {**manifest, **receipt}, rows


def freeze_predictions(date_root: Path, *, run_id: str, operating_date: date,
                       repository_sha: str, rows: list[dict], sources: dict,
                       exclusions: list[dict], stage_counts: dict, clock=utc_now) -> Path:
    """Use CourtVision's atomic hard-link primitive; no overwrite/replace fallback.

    PredictionPublicationTransaction commits using os.replace. Its pre-stage
    existence check is insufficient for immutable scientific articles, so use
    the existing stronger fact-evidence publisher and a final commit receipt.
    Interrupted artifacts/claims are preserved, never rolled back or deleted.
    """
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id) is None:
        raise BackfillError("invalid prediction run ID")
    if date_root.name != operating_date.isoformat():
        raise BackfillError("prediction directory must bind the operating date")
    if re.fullmatch(r"[0-9a-f]{40}", repository_sha) is None:
        raise BackfillError("explicit repository commit SHA required")
    root = date_root / run_id
    _plain_path(root)
    rows = sorted(rows, key=lambda r: (int(r["mlbam_game_id"]), int(r["mlbam_player_id"])))
    artifacts = {"predictions": csv_bytes(rows),
                 "exclusions": canonical_json({"payload": {"rows": exclusions, "stage_counts": stage_counts},
                     "sha256": digest({"rows": exclusions, "stage_counts": stage_counts})}) + b"\n",
                 "sources": canonical_json({"payload": sources, "sha256": digest(sources)}) + b"\n"}
    manifest = {"schema_version": SCHEMA, "prediction_run_id": run_id,
        "operating_date": operating_date.isoformat(), "repository_commit_sha": repository_sha,
        "row_count": len(rows), "stage_counts": stage_counts,
        "artifacts": {key: {"sha256": hashlib.sha256(value).hexdigest()} for key, value in artifacts.items()},
        **SAFETY}
    if (root / "freeze.json").exists():
        saved, _ = verify_freeze(root)
        if saved["manifest_hash"] != digest(manifest):
            raise BackfillError("conflicting same-run prediction rewrite")
        return root
    if rows and any(clock() >= _clock(row["scheduled_start_utc"]) for row in rows):
        raise BackfillError("game started before prediction publication")
    if rows:
        publish_document(date_root / "frozen-cohort.json",
            {"prediction_run_id": run_id, "manifest_hash": digest(manifest)})
    for label, raw in artifacts.items():
        publish_bytes(root / (label + (".csv" if label == "predictions" else ".json")), raw)
    publish_document(root / "manifest.json", manifest)
    _verify_artifacts(root)
    durable = clock()
    if rows and any(durable >= _clock(row["scheduled_start_utc"]) for row in rows):
        raise BackfillError("game started before durable prediction freeze")
    publish_document(root / "freeze.json",
        {"manifest_hash": digest(manifest), "prediction_artifact_durable_at": durable.isoformat(),
         "prediction_row_hash_verification": "PASS", "prediction_rows_mutable": False})
    verify_freeze(root)
    return root
