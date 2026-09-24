"""Local artifact loading and publication for the MLB research preview.

No collector is called. A sources index references existing capture manifests;
it contains no features, probabilities, opportunity defaults, or credentials.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import fields, replace
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from courtvision.core.candidates import IdentityStatus
from courtvision.sports.mlb.data.prospective_context_acquisition import AcquisitionResult, parse_mlb_schedule
from courtvision.sports.mlb.hits_acquisition import (
    captured_hits_source, materialize_acquired_hits_evidence, resolve_hits_player_from_capture,
)
from courtvision.sports.mlb.hits_identity import bind_mlb_events
from courtvision.sports.mlb.providers.the_odds_api_market_adapter import normalize_mlb_event_odds
from courtvision.sports.mlb.research_preview import (
    HITS_LIMITATION, MODEL_ID, MODEL_VERSION, OPERATING_TIMEZONE,
    MLBResearchPreviewRow, hits_failure_reason, hits_source_row, preview_hits_evidence,
    preview_availability, preview_hr_prediction, preview_summary, sort_preview_rows, timestamp, unavailable_row,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "outputs" / "runtime" / "mlb" / "research"
DEFAULT_QUALIFICATION_ROOT = REPOSITORY_ROOT.parent / "CourtVisionRuntime" / "qualification"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("source must be a JSON object")
    return value


def _local_path(value: str, root: Path) -> Path:
    path = Path(value)
    if path.drive.startswith("\\\\") or str(path).startswith(("//", "\\\\")) or "://" in value:
        raise ValueError("only local filesystem sources are supported")
    return (path if path.is_absolute() else root / path).resolve()


def _capture(path: Path) -> AcquisitionResult:
    manifest = _json(path)
    return AcquisitionResult(manifest["capture_id"], path.parent, path, manifest.get("capture_state", ""), True, 0)


def load_hits_sources(path: Path, day: str, *, generated_at: datetime) -> list[MLBResearchPreviewRow]:
    """Consume an index of preserved odds and existing acquisition manifests.

    Index: schema_version, operating_date, odds {path, collected_at}, schedule
    {manifest, request_id}, game_feeds {gamePk: manifest}, seasons {playerId:
    manifest}. All paths are local, relative to the index or absolute. The clock
    used for new probabilities is the actual run clock, never a replay override.
    """
    index = _json(path)
    if index.get("schema_version") != "mlb-hits-preview-sources-v1" or index.get("operating_date") != day:
        raise ValueError("Hits sources index schema/date mismatch")
    odds = index["odds"]
    odds_path = _local_path(odds["path"], path.parent)
    batch = normalize_mlb_event_odds(
        _json(odds_path), collected_at=timestamp(odds["collected_at"]), source_refs=(str(odds_path),),
    )
    sources = [r for r in batch.records if r.canonical_market_type == "batter_hits"
               and r.provider_market_key == "batter_hits" and r.side.value == "OVER" and r.line == 0.5]
    if not sources:
        return [unavailable_row(day, "batter_hits", "MARKET_UNAVAILABLE", refs=(str(path),))]
    if any(s.to_normalized_quote().event_date.isoformat() != day for s in sources):
        raise ValueError("Hits odds operating date mismatch")
    try:
        schedule = index["schedule"]
        captured = captured_hits_source(_capture(_local_path(schedule["manifest"], path.parent)),
                                        request_id=schedule["request_id"])
        events = parse_mlb_schedule(captured.body, operating_date=date.fromisoformat(day))
        bindings = bind_mlb_events(sources, events, observed_at=captured.first_observed_at_utc,
                                  evidence_cutoff=captured.evidence_cutoff, source_refs=captured.source_refs)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        return [replace(hits_source_row(s), block_reason=hits_failure_reason(str(exc), "EVENT_IDENTITY_UNRESOLVED"),
                        block_detail=str(exc)) for s in sources]
    rows = []
    for source, event in zip(sources, bindings):
        base = hits_source_row(source)
        stage = "EVENT_IDENTITY_UNRESOLVED"
        try:
            if event.identity_status is not IdentityStatus.RESOLVED:
                rows.append(replace(base, event_status=event.identity_status.value))
                continue
            base = replace(base, canonical_event_id=event.mlbam_game_id, event_status="resolved")
            stage = "LINEUP_UNAVAILABLE"
            feed = _capture(_local_path(index["game_feeds"][event.mlbam_game_id], path.parent))
            stage = "PLAYER_IDENTITY_UNRESOLVED"
            player = resolve_hits_player_from_capture(source, event, feed)
            if player.participant_identity.identity_status is not IdentityStatus.RESOLVED:
                rows.append(replace(base, block_reason=stage, identity_status=player.participant_identity.identity_status.value))
                continue
            base = replace(base, player_id=player.mlbam_player_id, identity_status="resolved")
            stage = "SEASON_EVIDENCE_UNAVAILABLE"
            season = _capture(_local_path(index["seasons"][player.mlbam_player_id], path.parent))
            acquired = materialize_acquired_hits_evidence(player, season=date.fromisoformat(day).year,
                                                          game_feed_capture=feed, season_capture=season)
            rows.append(preview_hits_evidence(source, acquired, generated_at=generated_at))
        except (ValueError, KeyError, OSError, TypeError) as exc:
            rows.append(replace(base, block_reason=hits_failure_reason(str(exc), stage), block_detail=str(exc)))
    return rows


def load_preserved_hits_rejection(path: Path, day: str) -> MLBResearchPreviewRow:
    """Display a preserved canonical rejection; this path can never qualify a row.

    This pre-existing qualification artifact contains no market quote. Its
    canonical StatsAPI game ID is retained separately from a provider odds ID.
    """
    manifest = _json(path)
    if (manifest.get("schema_version") != "cv-oct3a-r4-qualification-manifest-v1"
            or manifest.get("operating_date") != day or manifest.get("research_only") is not True
            or manifest.get("eligible_for_betting") is not False or manifest.get("wagering_enabled") is not False
            or manifest.get("complete_live_bundle_ready") is not False):
        raise ValueError("unsupported preserved Hits rejection")
    diagnosis = manifest["season_evidence"]["diagnosis"]
    if manifest["season_evidence"]["qualified"] is not False or not diagnosis.get("canonical_validator_exception"):
        raise ValueError("preserved Hits source has no canonical rejection")
    detail = diagnosis["canonical_validator_exception"]
    # Identity/lineup were established by the preserved checkpoint. Do not
    # choose a season split or run models using that rejected evidence.
    lineup = manifest["batting_order_evidence"]
    return MLBResearchPreviewRow(
        operating_date=day, market_type="batter_hits", market_display="1+ Hits",
        prediction_status="BLOCKED", block_reason=hits_failure_reason(detail, "SEASON_EVIDENCE_REJECTED"),
        block_detail=detail + "; market quote unavailable in this preserved qualification bundle",
        canonical_event_id=str(manifest["gamePk"]), commence_time_utc=manifest["authoritative_scheduled_start"],
        player_id=str(manifest["player_mlbam_id"]), player_name=manifest["player_canonical_name"], team=manifest.get("team"),
        model_id=MODEL_ID, model_version=MODEL_VERSION, evidence_cutoff=manifest["evidence_cutoff"],
        source_refs=(str(path),), identity_status="resolved", event_status="resolved",
        lineup_status=lineup["lineup_status"], limitation_status=HITS_LIMITATION,
    )


def load_hr_predictions(path: Path, day: str) -> list[MLBResearchPreviewRow]:
    raw = path.read_bytes()
    manifest_path = path.with_name("prediction_manifest_v1.json")
    if not manifest_path.is_file():
        manifest_path = path.with_name("manifest.json")
    manifest = _json(manifest_path)
    digest = manifest.get("predictions_csv_sha256") or manifest.get("artifact_hashes", {}).get("mlb_predictions")
    if digest != hashlib.sha256(raw).hexdigest():
        raise ValueError("HR prediction CSV does not match its manifest")
    source_day = manifest.get("operating_date") or manifest.get("target_date") or manifest.get("feature_manifest", {}).get("target_date")
    if source_day != day:
        raise ValueError("HR manifest operating date mismatch")
    data = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    expected = manifest.get("prediction_count", manifest.get("row_count"))
    if expected is not None and len(data) != expected:
        raise ValueError("HR manifest row count mismatch")
    for row in data:
        for key in ("model_id", "model_version", "prediction_run_id"):
            if manifest.get(key) and row.get(key) != manifest[key]:
                raise ValueError(f"HR row/manifest {key} mismatch")
        if (row.get("operating_date") or row.get("game_date")) != day:
            raise ValueError("HR row operating date mismatch")
    return [preview_hr_prediction(row, day=day, source_ref=str(path)) for row in data] or [
        unavailable_row(day, "batter_home_runs", "HR_PREDICTIONS_EMPTY", refs=(str(path),))]


def discover_hr_predictions(root: Path, day: str) -> list[Path]:
    # Only supported current layouts. Rehearsal directories are never searched.
    return sorted(set(
        list(root.glob(f"mlb_hr*/controls/*/dates/{day}/*/predictions.csv"))
        + list(root.glob(f"mlb_hr_baseline/daily_runs/{day}/*/predictions/predictions.csv"))
        + list(root.glob(f"mlb_hr_baseline/daily_runs/{day}/predictions.csv"))
    ))


def build_local_preview(
    day: str, *, repository_root: Path = REPOSITORY_ROOT, qualification_root: Path = DEFAULT_QUALIFICATION_ROOT,
    hits_sources: Path | None = None, hr_predictions: Path | None = None, generated_at: datetime | None = None,
) -> list[MLBResearchPreviewRow]:
    date.fromisoformat(day)
    now = generated_at or datetime.now(timezone.utc)
    rows = []
    hits_path = hits_sources or repository_root / "outputs" / "research" / "mlb_hits" / day / "sources.json"
    try:
        if hits_path.is_file():
            payload = _json(hits_path)
            if payload.get("schema_version") == "cv-oct3a-r4-qualification-manifest-v1":
                rows.append(load_preserved_hits_rejection(hits_path, day))
            else:
                rows.extend(load_hits_sources(hits_path, day, generated_at=now))
        elif hits_sources is not None:
            rows.append(unavailable_row(day, "batter_hits", "HITS_SOURCE_NOT_FOUND", refs=(str(hits_path),)))
        else:
            matches = []
            for path in sorted(qualification_root.glob("CV-OCT.3A-R4/*/manifests/02_qualification_manifest.json")):
                if _json(path).get("operating_date") == day:
                    matches.append(path)
            rows.extend(load_preserved_hits_rejection(p, day) for p in matches)
            if not matches:
                rows.append(unavailable_row(day, "batter_hits", "HITS_SOURCES_UNAVAILABLE", refs=(str(hits_path),)))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        rows.append(replace(unavailable_row(day, "batter_hits", "HITS_SOURCE_INVALID", refs=(str(hits_path),)),
                            block_detail=str(exc)))
    paths = [hr_predictions] if hr_predictions is not None else discover_hr_predictions(repository_root / "outputs" / "research", day)
    if len(paths) != 1:
        rows.append(unavailable_row(day, "batter_home_runs", "HR_SOURCES_AMBIGUOUS" if paths else "HR_SOURCE_UNAVAILABLE",
                                    refs=tuple(str(p) for p in paths)))
    else:
        try:
            rows.extend(load_hr_predictions(paths[0], day))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rows.append(replace(unavailable_row(day, "batter_home_runs", "HR_SOURCE_INVALID", refs=(str(paths[0]),)),
                                block_detail=str(exc)))
    return sort_preview_rows(rows)


def write_preview(rows: list[MLBResearchPreviewRow], day: str, output_root: Path) -> tuple[Path, Path]:
    """Publish a new run. The summary is the completion marker; no overwrite."""
    summary = preview_summary(rows, day)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    root = output_root.resolve() / day / run_id
    root.mkdir(parents=True, exist_ok=False)
    board = root / f"mlb_research_board_{day}.csv"
    summary_path = root / f"mlb_research_summary_{day}.json"
    with board.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[f.name for f in fields(MLBResearchPreviewRow)])
        writer.writeheader()
        for row in sort_preview_rows(rows):
            data = row.to_dict()
            data["source_refs"] = json.dumps(data["source_refs"], ensure_ascii=False)
            writer.writerow(data)
    summary.update(board_filename=board.name, board_sha256=hashlib.sha256(board.read_bytes()).hexdigest(),
                   generated_at=datetime.now(timezone.utc).isoformat(), run_id=run_id)
    with summary_path.open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    return board, summary_path


def load_preview_board(output_root: Path, day: str) -> tuple[list[MLBResearchPreviewRow], dict]:
    """Read the latest completed run for this exact date; never a prior date."""
    date.fromisoformat(day)
    summaries = sorted(output_root.glob(f"{day}/*/mlb_research_summary_{day}.json"))
    if not summaries:
        rows = [unavailable_row(day, m, "PREVIEW_BOARD_UNAVAILABLE") for m in ("batter_hits", "batter_home_runs")]
        return rows, preview_summary(rows, day)
    summary_path = summaries[-1]
    summary = _json(summary_path)
    expected_name = f"mlb_research_board_{day}.csv"
    if summary.get("board_filename") != expected_name or summary.get("operating_date") != day:
        raise ValueError("preview summary date/path mismatch")
    board = summary_path.with_name(expected_name)
    raw = board.read_bytes()
    if hashlib.sha256(raw).hexdigest() != summary.get("board_sha256"):
        raise ValueError("preview board integrity mismatch")
    rows = []
    for raw_row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
        data = {key: value if value != "" else None for key, value in raw_row.items()}
        for key in ("research_only", "eligible_for_betting", "kelly_eligible"):
            if data[key] not in {"True", "False"}:
                raise ValueError("invalid preview safety flag")
            data[key] = data[key] == "True"
        for key in ("line", "market_implied_probability", "model_probability", "projected_at_bats"):
            if data[key] is not None:
                data[key] = float(data[key])
        for key in ("american_odds", "season_hits", "season_at_bats"):
            if data[key] is not None:
                data[key] = int(data[key])
        data["source_refs"] = tuple(json.loads(data["source_refs"]))
        rows.append(MLBResearchPreviewRow(**data))
    recomputed = preview_summary(rows, day)
    expected = recomputed
    if "availability_schema_version" not in summary:
        # Verify the original summary semantics before presenting the new view.
        # Existing immutable artifacts are never rewritten or trusted unchecked.
        expected = {key: value for key, value in recomputed.items() if key not in preview_availability(rows)}
        expected["status"] = "MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE" if any(
            row.prediction_status == "UNAVAILABLE" for row in rows) else "MLB_PREVIEW_RESEARCH_ONLY"
    if any(summary.get(key) != value for key, value in expected.items()):
        raise ValueError("preview summary content mismatch")
    return rows, {**summary, **recomputed, "board_path": str(board), "summary_path": str(summary_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CourtVision MLB Research Preview (local sources only)")
    parser.add_argument("--date", default=datetime.now(OPERATING_TIMEZONE).date().isoformat())
    parser.add_argument("--hits-sources", type=Path)
    parser.add_argument("--hr-predictions", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        day = date.fromisoformat(args.date).isoformat()
        rows = build_local_preview(day, hits_sources=args.hits_sources, hr_predictions=args.hr_predictions)
        summary = preview_summary(rows, day)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"MLB_PREVIEW_SOURCE_DATA_UNAVAILABLE: {exc}")
        return 2
    try:
        board, summary_path = write_preview(rows, day, args.output_root)
    except (ValueError, OSError) as exc:
        print(f"MLB_PREVIEW_OUTPUT_UNAVAILABLE: {exc}")
        return 2
    print("CourtVision MLB Research Preview")
    print(f"Date: {day}")
    if day != datetime.now(OPERATING_TIMEZONE).date().isoformat():
        print("HISTORICAL/PRESERVED DATA - not today's predictions")
    for label, key in (("Games", "games_seen"), ("Players", "players_seen"), ("Hits qualified", "hits_qualified"),
                       ("Hits blocked", "hits_blocked"), ("Hits unavailable", "hits_unavailable"),
                       ("HR research rows", "hr_market_contaminated"), ("HR market-contaminated rows", "hr_market_contaminated"),
                       ("HR unavailable", "hr_unavailable")):
        print(f"{label}: {summary[key]}")
    print(summary["status"])
    for market in summary["market_status"].values():
        print(f"{market['label']}: {market['status']}")
    for missing in summary["missing_sources"]:
        print(f"Missing source: {missing}")
    print("Unavailable source markers are not player predictions.")
    print(f"Board: {board}\nSummary: {summary_path}")
    print("Research only; unapproved. HR: LEGACY_MARKET_CONTAMINATED. Hits: NAIVE_UNCALIBRATED_BASELINE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
