"""Phase 4 Research Mode runtime entrypoint.

Research Mode is stats-only. It writes isolated research artifacts and does
not create betting candidates, MarketProp rows, Elite rows, Kelly inputs, or
operator betting boards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.clients.api_nba_client import ApiNbaClient
from courtvision.sports.nba.artifact_domains import (
    NBA_OUTCOME_EVIDENCE, NBA_STAT_ARTIFACT_SCHEMA, require_artifact_path, stat_artifact_path,
)
from courtvision.sports.nba.outcome_publication import qualify_completed_outcomes
from courtvision.providers.research_schedule_resolver import (
    DEFAULT_MANUAL_SCHEDULE_DIR,
    SOURCE_API_NBA,
    SOURCE_MANUAL_SCHEDULE,
    resolve_research_schedule,
)


RESEARCH_OK = "RESEARCH_OK"
RESEARCH_NO_GAMES = "RESEARCH_NO_GAMES"
RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING = "RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING"
RESEARCH_PROVIDER_UNAVAILABLE = "RESEARCH_PROVIDER_UNAVAILABLE"
RESEARCH_NO_PLAYER_STATS = "RESEARCH_NO_PLAYER_STATS"
RESEARCH_OUTCOME_UNQUALIFIED = "RESEARCH_OUTCOME_UNQUALIFIED"

DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
RESEARCH_MODE = "research"
ELIGIBLE_FOR_BETTING = False

STAT_PROJECTION_COLUMNS = [
    "artifact_domain",
    "artifact_schema_version",
    "game_date",
    "game_id",
    "player_id",
    "player_name",
    "team_id",
    "team_abbreviation",
    "minutes",
    "points",
    "rebounds",
    "assists",
    "threes",
    "steals",
    "blocks",
    "source",
    "mode",
    "eligible_for_betting",
]


@dataclass(slots=True)
class ResearchModeResult:
    status: str
    stat_projection_path: Path | None
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_research_mode(
    *,
    target_date: str,
    season: int,
    stats_provider: str = SOURCE_API_NBA,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    manual_schedule_dir: str | Path = DEFAULT_MANUAL_SCHEDULE_DIR,
    client_factory: Callable[..., Any] = ApiNbaClient,
) -> ResearchModeResult:
    """Run stats-only Research Mode for one date."""
    target_date_text = _validate_date(target_date)
    if stats_provider != SOURCE_API_NBA:
        raise ValueError("Research Mode currently supports only --stats-provider api_nba")

    output_dir_path = Path(output_dir)
    runtime_root = output_dir_path.parent
    diagnostics_dir = runtime_root / "diagnostics" / "nba" / "outcomes"
    stat_projection_path = stat_artifact_path(output_dir_path, NBA_OUTCOME_EVIDENCE, target_date_text)
    if stat_projection_path.exists():
        raise FileExistsError("outcome artifact already exists; overwrite is prohibited")
    attempt_id = uuid4().hex
    attempt_dir = diagnostics_dir / target_date_text / attempt_id
    summary_path = attempt_dir / "summary.txt"
    diagnostics_path = attempt_dir / "diagnostics.json"

    output_dir_path.mkdir(parents=True, exist_ok=True)
    attempt_dir.mkdir(parents=True, exist_ok=False)

    client = client_factory(
        runtime_root=runtime_root,
        manual_schedule_dir=manual_schedule_dir,
    )

    api_games_body = client._request("games", {"date": target_date_text})
    games_provider_status = _provider_status(client)
    schedule_result = resolve_research_schedule(
        target_date_text,
        api_games_body,
        manual_schedule_dir=manual_schedule_dir,
        runtime_root=runtime_root,
        write_diagnostics=False,
    )

    schedule_rows = schedule_result.schedule.to_dict("records")
    numeric_game_ids, skipped_game_ids = _partition_numeric_game_ids(schedule_rows)
    player_stats: list[Any] = []
    stats_provider_statuses: list[dict[str, Any]] = []
    stats_bodies: dict[int, Any] = {}
    statuses_by_game: dict[int, dict[str, Any]] = {}

    status = _initial_status(
        schedule_rows=schedule_rows,
        selected_source=schedule_result.selected_source,
        numeric_game_ids=numeric_game_ids,
        games_provider_status=games_provider_status,
    )

    if status is None:
        for game_id in numeric_game_ids:
            # Retain the original envelope: mapped rows alone lose missing
            # values, response counts and the evidence of incomplete coverage.
            body = client._request("players/statistics", {"game": game_id})
            stats_bodies[game_id] = body
            provider_status = _provider_status(client)
            statuses_by_game[game_id] = provider_status
            stats_provider_statuses.append({"game_id": game_id, **provider_status})
            raw_rows = body.get("response", []) if isinstance(body, dict) else []
            rows = [ApiNbaClient._map_player_game_stats(
                row, fallback_game_id=game_id, fallback_game_date=target_date_text,
            ) for row in raw_rows if isinstance(row, dict)] if isinstance(raw_rows, list) else []
            player_stats.extend(rows)

        if player_stats:
            status = RESEARCH_OK
        elif any(_is_provider_unavailable(item) for item in stats_provider_statuses):
            status = RESEARCH_PROVIDER_UNAVAILABLE
        else:
            status = RESEARCH_NO_PLAYER_STATS

    stat_rows = [_stat_row(stat, fallback_date=target_date_text) for stat in player_stats]
    qualification = qualify_completed_outcomes(
        target_date=target_date_text, selected_source=schedule_result.selected_source,
        schedule_game_ids=numeric_game_ids, skipped_game_ids=skipped_game_ids,
        games_body=api_games_body, games_status=games_provider_status,
        stats_bodies=stats_bodies, stats_statuses=statuses_by_game,
    )
    publication_allowed = qualification["canonical_publication_allowed"]
    if status == RESEARCH_OK and not publication_allowed:
        status = RESEARCH_OUTCOME_UNQUALIFIED

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        season=season,
        stats_provider=stats_provider,
        status=status,
        schedule_result=schedule_result,
        games_provider_status=games_provider_status,
        stats_provider_statuses=stats_provider_statuses,
        numeric_game_ids=numeric_game_ids,
        skipped_game_ids=skipped_game_ids,
        stat_projection_path=stat_projection_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        stat_rows=stat_rows,
    )
    diagnostics.update(
        artifact_kind="NBA_OUTCOME_COLLECTION_ATTEMPT", attempt_id=attempt_id,
        outcome_qualification=qualification, canonical_outcome_published=False,
    )
    diagnostics["artifacts"]["stat_projection_source_csv"] = None
    # Write the attempt record first. A failed/partial write never claims the
    # completed CSV. Publication is atomic and create-once below.
    _write_summary(summary_path, diagnostics)
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")
    if publication_allowed:
        _write_stat_projection_csv(stat_projection_path, stat_rows)
        diagnostics["canonical_outcome_published"] = True
        diagnostics["canonical_outcome_sha256"] = hashlib.sha256(stat_projection_path.read_bytes()).hexdigest()
        diagnostics["artifacts"]["stat_projection_source_csv"] = str(stat_projection_path)
        diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")

    return ResearchModeResult(
        status=status,
        stat_projection_path=stat_projection_path if publication_allowed else None,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _initial_status(
    *,
    schedule_rows: list[dict[str, Any]],
    selected_source: str,
    numeric_game_ids: list[int],
    games_provider_status: dict[str, Any],
) -> str | None:
    if not schedule_rows:
        if _is_provider_unavailable(games_provider_status):
            return RESEARCH_PROVIDER_UNAVAILABLE
        return RESEARCH_NO_GAMES

    if selected_source == SOURCE_MANUAL_SCHEDULE and not numeric_game_ids:
        return RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING

    if not numeric_game_ids:
        return RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING

    return None


def _partition_numeric_game_ids(rows: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    numeric_game_ids: list[int] = []
    skipped_game_ids: list[str] = []
    seen: set[int] = set()

    for row in rows:
        value = str(row.get("game_id", "")).strip()
        game_id = _numeric_game_id(value)
        if game_id is None:
            if value:
                skipped_game_ids.append(value)
            continue
        if game_id not in seen:
            numeric_game_ids.append(game_id)
            seen.add(game_id)

    return numeric_game_ids, skipped_game_ids


def _numeric_game_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    game_id = int(text)
    return game_id if game_id > 0 else None


def _stat_row(stat: Any, *, fallback_date: str) -> dict[str, Any]:
    game_date = _clean_text(getattr(stat, "game_date", ""))[:10] or fallback_date
    return {
        "game_date": game_date,
        "artifact_domain": NBA_OUTCOME_EVIDENCE,
        "artifact_schema_version": NBA_STAT_ARTIFACT_SCHEMA,
        "game_id": getattr(stat, "game_id", ""),
        "player_id": getattr(stat, "player_id", ""),
        "player_name": getattr(stat, "player_name", ""),
        "team_id": getattr(stat, "team_id", ""),
        "team_abbreviation": getattr(stat, "team_abbreviation", ""),
        "minutes": getattr(stat, "minutes", ""),
        "points": getattr(stat, "points", ""),
        "rebounds": getattr(stat, "rebounds", ""),
        "assists": getattr(stat, "assists", ""),
        "threes": getattr(stat, "threes", ""),
        "steals": getattr(stat, "steals", ""),
        "blocks": getattr(stat, "blocks", ""),
        "source": SOURCE_API_NBA,
        "mode": RESEARCH_MODE,
        "eligible_for_betting": ELIGIBLE_FOR_BETTING,
    }


def _write_stat_projection_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    require_artifact_path(path, NBA_OUTCOME_EVIDENCE)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A hard link publishes fully flushed bytes without replacing an existing
    # destination. Never expose a partially written canonical date artifact.
    staged_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=".outcome-", suffix=".partial",
                                         delete=False) as stream:
            staged_path = Path(stream.name)
            pd.DataFrame(rows, columns=STAT_PROJECTION_COLUMNS).to_csv(stream, index=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged_path, path)
    finally:
        if staged_path is not None:
            staged_path.unlink()


def _diagnostics_payload(
    *,
    target_date: str,
    season: int,
    stats_provider: str,
    status: str,
    schedule_result: Any,
    games_provider_status: dict[str, Any],
    stats_provider_statuses: list[dict[str, Any]],
    numeric_game_ids: list[int],
    skipped_game_ids: list[str],
    stat_projection_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
    stat_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "season": int(season),
        "stats_provider": stats_provider,
        "status": status,
        "schedule_source": schedule_result.selected_source,
        "schedule_provider_status": schedule_result.provider_status,
        "schedule_game_count": int(len(schedule_result.schedule.index)),
        "api_nba_games_provider_status": games_provider_status,
        "api_nba_player_stats_provider_statuses": stats_provider_statuses,
        "numeric_api_game_ids": numeric_game_ids,
        "skipped_non_numeric_game_ids": skipped_game_ids,
        "player_stats_row_count": len(stat_rows),
        "eligible_for_betting": ELIGIBLE_FOR_BETTING,
        "eligible_for_betting_all_rows": all(
            row.get("eligible_for_betting") is False for row in stat_rows
        ),
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_artifacts_written": [],
        "artifacts": {
            "stat_projection_source_csv": str(stat_projection_path),
            "summary_txt": str(summary_path),
            "diagnostics_json": str(diagnostics_path),
        },
        "schedule_diagnostics": schedule_result.diagnostics,
    }


def _write_summary(path: Path, diagnostics: dict[str, Any]) -> None:
    lines = [
        f"Research Mode Summary - {diagnostics['target_date']}",
        f"status: {diagnostics['status']}",
        f"stats_provider: {diagnostics['stats_provider']}",
        f"schedule_source: {diagnostics['schedule_source']}",
        f"schedule_game_count: {diagnostics['schedule_game_count']}",
        f"numeric_api_game_ids: {len(diagnostics['numeric_api_game_ids'])}",
        f"skipped_non_numeric_game_ids: {len(diagnostics['skipped_non_numeric_game_ids'])}",
        f"player_stats_row_count: {diagnostics['player_stats_row_count']}",
        f"canonical_publication_allowed: {diagnostics['outcome_qualification']['canonical_publication_allowed']}",
        f"outcome_contract: {diagnostics['outcome_qualification']['contract']}",
        "eligible_for_betting: False",
        "market_prop_rows_created: 0",
        "elite_rows_created: 0",
        "kelly_called: False",
        "operator_artifacts_written: 0",
    ]
    if diagnostics["status"] == RESEARCH_SCHEDULE_ONLY_API_GAME_ID_MISSING:
        lines.append(
            "note: schedule resolved, but no real numeric API-NBA game IDs were available for stats lookup."
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _provider_status(client: Any) -> dict[str, Any]:
    try:
        status = client.get_provider_status()
    except Exception as exc:
        return {"provider_status": f"status_unavailable: {exc}"}
    return dict(status) if isinstance(status, dict) else {"provider_status": str(status)}


def _is_provider_unavailable(status: dict[str, Any]) -> bool:
    provider_status = str(status.get("provider_status") or "").strip()
    if not provider_status:
        return False
    if provider_status in {"ok", "unrequested"}:
        return False
    if provider_status.startswith("research_schedule_"):
        return False
    return True


def _clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CourtVision stats-only Research Mode.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--season", required=True, type=int, help="NBA season year, for example 2025.")
    parser.add_argument(
        "--stats-provider",
        default=SOURCE_API_NBA,
        choices=[SOURCE_API_NBA],
        help="Stats provider. Only api_nba is supported for Phase 4 Research Mode.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Research output directory. Defaults to outputs/runtime/research.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_research_mode(
            target_date=args.date,
            season=args.season,
            stats_provider=args.stats_provider,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"stat_projection_source: {result.stat_projection_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
