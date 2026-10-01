"""CV-MLB-HITS-LIVE-01: authoritative evidence, immutable prediction, then market.

Run explicitly from clean canonical main. No scheduler, settlement, or betting
entrypoint is installed. A zero-row run remains immutable and can be followed
by a new run ID on the selected date when its pregame window/lineups are ready.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta
import json
import os
import re
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    AcquisitionPolicy, EvidenceRequest, parse_mlb_schedule,
)
from courtvision.sports.mlb.fact_backfill_evidence import (
    BASE, BackfillError, EvidenceJournal, StatsAPIProvider, digest,
    publish_document, read_document, source_ref, utc_now,
)
from courtvision.sports.mlb.fact_ledger import MLBFactStore, _plain_path
from courtvision.sports.mlb.hits_acquisition import SovereignBatterHitsEvidence
from courtvision.sports.mlb.hits_features import extract_batter_lineup_evidence
from courtvision.sports.mlb.hits_identity import bind_statsapi_event, bind_statsapi_roster_players
from courtvision.sports.mlb.hits_season_ledger import HitsLedgerError, load_ledger_season
from courtvision.sports.mlb.live01_evidence import (
    CATCHUP_START, batter_coverage, catch_up, compose_coverage, historical_inventory,
    inventory_from_capture, schedule_request,
)
from courtvision.sports.mlb.live01_freeze import freeze_predictions, prediction_row, verify_freeze
from courtvision.sports.mlb.live01_market import capture_market
from courtvision.sports.mlb.providers.the_odds_api_live import MLBOddsIngestionConfig

NOT_BEFORE = date(2026, 10, 1)
TARGET_RULE = "first_date_with_unstarted_official_games_from_max_2026-10-01_Toronto_today_within_7_calendar_days"


def canonical_main(repository: Path) -> str:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()
    sha = git("rev-parse", "HEAD")
    if (git("branch", "--show-current") != "main" or sha != git("rev-parse", "origin/main")
            or git("status", "--porcelain=v2")):
        raise BackfillError("live execution requires clean canonical main=origin/main")
    return sha


def unstarted(status: dict, *, start: datetime, observed: datetime) -> bool:
    """Require positive pregame evidence; NON_FINAL alone also includes live games."""
    return (observed < start and status.get("abstractGameState") == "Preview"
            and status.get("detailedState") in {"Scheduled", "Pre-Game", "Warmup", "Preview"}
            and status.get("codedGameState") in {"S", "P"}
            and status.get("statusCode", status["codedGameState"]) in {"S", "P", "PW"}
            and status.get("abstractGameCode", "P") == "P")


def select_target(journal: EvidenceJournal, provider, *, now: datetime, clock=utc_now):
    candidate = max(NOT_BEFORE, now.astimezone(ZoneInfo("America/Toronto")).date())
    for offset in range(7):
        target = candidate + timedelta(days=offset)
        request = schedule_request(target, target, "target-schedule-" + target.isoformat())
        record = journal.capture(request, provider)
        inventory = inventory_from_capture(journal, record, target, target)
        observed = datetime.fromisoformat(record["response"]["responded_at"])
        remaining = [row for row in inventory["games"]
                     if unstarted(row["status"],
                         start=datetime.fromisoformat(row["source_game"]["gameDate"].replace("Z", "+00:00")),
                         observed=max(observed, clock()))]
        if remaining:
            return target, inventory, record, {
                "target_date_selection_rule": TARGET_RULE, "target_operating_date": target.isoformat(),
                "target_date_advanced": offset != 0,
                "target_date_advance_reason": "NO_REMAINING_UNSTARTED_OFFICIAL_GAMES" if offset else "NOT_ADVANCED",
                "target_games_total": len(inventory["games"]), "target_games_unstarted": len(remaining),
                "target_games_started": sum(
                    datetime.fromisoformat(r["source_game"]["gameDate"].replace("Z", "+00:00")) <= observed
                    or r["status"].get("abstractGameState") in {"Live", "Final"} for r in inventory["games"]),
                "observed_target_game_types": inventory["observed_game_types"]}
    return None, None, None, {
        "target_date_selection_rule": TARGET_RULE, "target_operating_date": None,
        "target_date_advanced": False, "target_date_advance_reason": "NO_UNSTARTED_OFFICIAL_GAMES_IN_7_DAY_SEARCH"}


def generate_cohort(inventory: dict, schedule_record: dict, journal: EvidenceJournal, provider,
                    *, index: dict, store: MLBFactStore, run_id: str,
                    repository_sha: str, clock=utc_now, policy=AcquisitionPolicy()):
    target = date.fromisoformat(inventory["window_start"])
    journal.payload(schedule_record)  # Recheck preserved bytes before interpretation.
    events = {e.event_id: e for e in parse_mlb_schedule(
        (journal.root / f'{schedule_record["claim"]["sequence"]:06d}' / "body.bin").read_bytes(),
        operating_date=target)}
    predictions, sources, exclusions = [], {}, []
    counts = {"target_game_feeds_captured": 0, "games_with_confirmed_lineups": 0,
              "games_without_confirmed_lineups": 0, "confirmed_batters": 0,
              "ledger_qualified_batters": 0, "excluded_batters": 0}
    for row in inventory["games"]:
        event = events[row["gamePk"]]
        now = clock()
        if not unstarted(row["status"], start=event.scheduled_start_utc, observed=now):
            exclusions.append({"gamePk": event.event_id, "reason": "GAME_STARTED_OR_NOT_PREGAME"})
            continue
        lead = (event.scheduled_start_utc - now).total_seconds() / 60
        if not policy.minimum_lead_minutes <= lead <= policy.maximum_lead_minutes:
            exclusions.append({"gamePk": event.event_id,
                "reason": "PREGAME_WINDOW_NOT_OPEN" if lead > policy.maximum_lead_minutes else "PREGAME_WINDOW_MISSED"})
            counts["games_without_confirmed_lineups"] += 1
            continue
        request = EvidenceRequest(request_id="pregame-feed-" + event.event_id,
            evidence_class="volatile_pregame", source_name="mlb_live01_lineup",
            provider="mlb_statsapi", event_id=event.event_id,
            url=f"{BASE}/api/v1.1/game/{event.event_id}/feed/live")
        record = journal.capture(request, provider)
        counts["target_game_feeds_captured"] += 1
        observed = datetime.fromisoformat(record["response"]["responded_at"])
        feed = journal.payload(record)
        data = feed["gameData"]
        captured_lead = (event.scheduled_start_utc - observed).total_seconds() / 60
        if (not unstarted(data["status"], start=event.scheduled_start_utc, observed=max(observed, clock()))
                or feed.get("liveData", {}).get("plays", {}).get("allPlays")
                or data["datetime"].get("firstPitch")):
            exclusions.append({"gamePk": event.event_id, "reason": "GAME_STARTED"})
            continue
        if not policy.minimum_lead_minutes <= captured_lead <= policy.maximum_lead_minutes:
            exclusions.append({"gamePk": event.event_id, "reason": "PREGAME_WINDOW_MISSED"})
            continue
        if (data["datetime"]["officialDate"] != event.operating_date.isoformat()
                or data["game"]["type"] != row["source_game"]["gameType"]
                or str(data["game"]["season"]) != str(target.year)):
            raise BackfillError("target feed conflicts with official date/gameType/season")
        schedule_observed = datetime.fromisoformat(schedule_record["response"]["responded_at"])
        if observed < schedule_observed:
            raise BackfillError("target feed predates schedule evidence")
        refs = (source_ref(schedule_record), source_ref(record))
        binding = bind_statsapi_event(event, observed_at=schedule_observed,
                                       evidence_cutoff=observed, source_refs=refs[:1])
        players = bind_statsapi_roster_players(feed, binding, observed_at=observed,
                                               evidence_cutoff=observed, source_refs=refs)
        roster = [asdict(p.roster_player) for p in players]
        confirmed = 0
        for player in players:
            lineup = extract_batter_lineup_evidence(feed, binding, player, observed_at=observed,
                                                    evidence_cutoff=observed, source_refs=refs)
            if lineup.lineup_status != "statsapi_batting_order_present":
                exclusions.append({"gamePk": event.event_id, "player_id": player.mlbam_player_id,
                    "reason": "LINEUP_NOT_CONFIRMED" if lineup.lineup_status == "unavailable"
                              else "NOT_IN_CONFIRMED_BATTING_ORDER"})
                counts["excluded_batters"] += 1
                continue
            confirmed += 1
            counts["confirmed_batters"] += 1
            generated = clock()
            if generated >= event.scheduled_start_utc:
                raise BackfillError("game started during model generation")
            coverage = batter_coverage(index, player_id=player.mlbam_player_id,
                game_id=event.event_id, target=target, observed_at=generated)
            try:
                season = load_ledger_season(store, coverage, player_name=player.roster_player.player_name)
            except HitsLedgerError as exc:
                if exc.state != "COURTVISION_LEDGER_INCOMPLETE":
                    raise
                exclusions.append({"gamePk": event.event_id, "player_id": player.mlbam_player_id,
                                    "reason": "LEDGER_COVERAGE_INCOMPLETE", "detail": str(exc)})
                counts["excluded_batters"] += 1
                continue
            acquired = SovereignBatterHitsEvidence(player, season, lineup, generated)
            prediction, source = prediction_row(acquired, schedule_row=row, run_id=run_id,
                repository_sha=repository_sha, coverage_policy=index["coverage_policy"],
                model_version=index["model_version"], generated_at=generated)
            source["roster"] = roster
            predictions.append(prediction)
            sources[prediction["prediction_id"]] = source
            counts["ledger_qualified_batters"] += 1
        counts["games_with_confirmed_lineups" if confirmed else "games_without_confirmed_lineups"] += 1
        if not confirmed:
            exclusions.append({"gamePk": event.event_id, "reason": "LINEUP_NOT_CONFIRMED"})
    return predictions, sources, exclusions, counts


def execute_live01(repository: Path, *, run_id: str, provider, clock=utc_now,
                   odds_config_path: Path | None = None,
                   odds_credential_file: Path | None = None) -> dict:
    repository = repository.resolve()
    sha = canonical_main(repository)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id) is None:
        raise BackfillError("invalid LIVE-01 run ID")
    base = repository / "data/mlb/prospective/hits"
    run_root = base / "runs" / run_id
    _plain_path(run_root)
    if run_root.exists():
        raise BackfillError("run ID already exists; preserve it and use a new explicit run ID")
    run_root.mkdir(parents=True)
    publish_document(run_root / "plan.json", {"run_id": run_id, "repository_commit_sha": sha,
        "not_before_date": NOT_BEFORE.isoformat(), "target_selection_rule": TARGET_RULE,
        "maximum_search_days": 7, "maximum_target_requests": 67, "maximum_catchup_requests": 225,
        "started_at": clock().isoformat(), "research_only": True})
    try:
        journal = EvidenceJournal(run_root / "raw", 67)
        target, inventory, record, selection = select_target(journal, provider, now=clock(), clock=clock)
        publish_document(run_root / "selection.json", selection)
        if target is None:
            result = {**selection, "status": "YELLOW", "target_day_hits_qualification": "NO_ELIGIBLE_PREDICTIONS",
                "frozen_prediction_rows": 0, "odds_provider_calls": 0,
                "reason": "NO_UNSTARTED_OFFICIAL_GAMES_IN_SEARCH_WINDOW"}
            publish_document(run_root / "manifest.json", {
                "schema_version": "cv_mlb_hits_live01_no_target_v1", "row_count": 0,
                "repository_commit_sha": sha, "prediction_run_id": run_id, **selection})
            publish_document(run_root / "exclusions.json", {
                "rows": [{"reason": "NO_UNSTARTED_OFFICIAL_GAMES_IN_SEARCH_WINDOW"}]})
            publish_document(run_root / "disposition.json", result)
            return result
        publish_document(run_root / "target-inventory.json", inventory)
        store = MLBFactStore(repository / "data/mlb/facts")
        # Each component is independently derived from preserved provider evidence.
        history = historical_inventory(store.root, "cv-mlb-fact-backfill-02-20260925")
        publish_document(run_root / "historical-coverage.json", history)
        catchup, stats = catch_up(base / "catchup" / target.isoformat(), store, target=target, provider=provider)
        index = compose_coverage(history, catchup, target=target, store=store)
        publish_document(run_root / "composed-coverage.json", index)
        if canonical_main(repository) != sha:
            raise BackfillError("canonical execution commit changed during evidence acquisition")
        rows, sources, exclusions, counts = generate_cohort(inventory, record, journal, provider,
            index=index, store=store, run_id=run_id, repository_sha=sha, clock=clock)
        frozen = freeze_predictions(base / target.isoformat(), run_id=run_id, operating_date=target,
            repository_sha=sha, rows=rows, sources=sources, exclusions=exclusions,
            stage_counts=counts, clock=clock)
        verified, persisted_rows = verify_freeze(frozen)
        config = None
        key = None
        # Credential/config reading is itself after the durable freeze/read-back gate.
        if persisted_rows and odds_config_path is not None:
            config_payload = json.loads(odds_config_path.read_bytes())
            config_payload["operating_date"] = target
            config_payload["markets"] = tuple(config_payload["markets"])
            config = MLBOddsIngestionConfig(**config_payload)
            key = configured_odds_key(odds_credential_file)
        if canonical_main(repository) != sha:
            raise BackfillError("canonical execution commit changed before market gate")
        market = capture_market(frozen, config=config, api_key=key, clock=clock)
        qualification = ("PASS" if rows else "LINEUPS_NOT_YET_AVAILABLE"
            if any(e["reason"] in {"LINEUP_NOT_CONFIRMED", "PREGAME_WINDOW_NOT_OPEN"} for e in exclusions)
            else "NO_ELIGIBLE_PREDICTIONS")
        result = {**selection, **counts, "status": "GREEN" if rows and market["market_quotes_captured"]
                  and market["status"] == "COMPLETE" else "YELLOW",
            "live_execution_main_sha": sha, "historical_hits_ledger_ready": True,
            "catchup_window_start": CATCHUP_START.isoformat(), "catchup_window_end": index["through"],
            "catchup": stats, "observed_catchup_game_types": catchup["observed_game_types"],
            "prior_date_coverage_complete": True, "coverage_through": index["through"],
            "coverage_universe_game_count": len(index["games"]), "coverage_missing_game_count": 0,
            "coverage_conflict_count": 0, "hits_formula_changed": False,
            "hits_coverage_policy": index["coverage_policy"], "live_model_version": index["model_version"],
            "model_version_reused": index["model_version"] == "research-v1",
            "frozen_prediction_rows": len(persisted_rows), "freeze_directory": str(frozen),
            "prediction_file_sha256": verified["artifacts"]["predictions"]["sha256"],
            "prediction_row_hash_verification": "PASS", "prediction_freeze_verified": True,
            "prediction_artifact_durable_at": verified["prediction_artifact_durable_at"],
            "target_day_hits_qualification": qualification,
            "market": {k: v for k, v in market.items() if k not in {"quotes", "research_board"}},
            "outcome_status": "PENDING", "closing_status": "PENDING",
            "betting_enabled": False, "kelly_enabled": False, "official_pick_enabled": False}
        publish_document(run_root / "disposition.json", result)
        return result
    except Exception as exc:
        publish_document(run_root / "failure.json", {"status": "RED", "error_type": type(exc).__name__,
            "detail": str(exc), "observed_at": clock().isoformat(), "research_only": True})
        raise


def configured_odds_key(path: Path | None) -> str | None:
    """Read only the explicitly configured source, without logging or fallback."""
    if path is None:
        return os.environ.get("THE_ODDS_API_KEY")
    if not path.is_file():
        return None
    matches = [line.partition("=")[2].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
               if line.partition("=")[0].strip() == "THE_ODDS_API_KEY"]
    if len(matches) != 1:
        return None
    value = matches[0]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '\"'}:
        value = value[1:-1]
    return value if value and not any(c.isspace() for c in value) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--allow-statsapi", action="store_true")
    parser.add_argument("--odds-config", type=Path)
    parser.add_argument("--odds-credential-file", type=Path)
    args = parser.parse_args(argv)
    if not args.allow_statsapi:
        parser.error("explicit --allow-statsapi authorization is required")
    result = execute_live01(args.repository, run_id=args.run_id, provider=StatsAPIProvider(),
                            odds_config_path=args.odds_config,
                            odds_credential_file=args.odds_credential_file)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
