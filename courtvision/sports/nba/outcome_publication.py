"""Fail-closed qualification of API-NBA completed player-stat collections.

The normalized schedule alone discards finality. Inspect the original games
envelope and each original statistics envelope before normalizing any values.
This is a completed, played-game contract: postponed/cancelled dates remain
diagnostic, and no manual schedule can assert provider finality.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONTRACT = "nba-completed-outcome-v1"
STAT_FIELDS = ("points", "totReb", "assists", "tpm", "steals", "blocks")


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _envelope_rows(body: Any) -> list[dict] | None:
    if not isinstance(body, dict) or body.get("errors") not in ([], {}):
        return None
    rows = body.get("response")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    if not _integer(body.get("results")) or body["results"] != len(rows):
        return None
    # This collector does not acquire additional pages. Never bless one page.
    if "paging" in body and body["paging"] != {"current": 1, "total": 1}:
        return None
    return rows


def _seconds(value: Any) -> int | None:
    # Require explicit minutes and seconds; rounded/unknown minutes cannot
    # establish full team playing-time coverage.
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isascii() and part.isdigit() for part in parts):
        return None
    minutes, seconds = map(int, parts)
    return minutes * 60 + seconds if seconds < 60 else None


def qualify_completed_outcomes(
    *,
    target_date: str,
    selected_source: str,
    schedule_game_ids: list[int],
    skipped_game_ids: list[str],
    games_body: Any,
    games_status: Mapping[str, Any],
    stats_bodies: Mapping[int, Any],
    stats_statuses: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return explicit finality/coverage reasons; absence is never success.

    Per-team player minutes must reconcile exactly to the final period count,
    and player points must reconcile to the provider's final team scores. This
    rejects truncated nonempty pulls as well as missing games/teams. Zero-minute
    nonparticipants do not establish coverage. Missing evidence fails closed.
    """
    reasons: list[str] = []
    game_evidence: list[dict[str, Any]] = []
    if selected_source != "api_nba":
        reasons.append("UNTRUSTED_SCHEDULE_FINALITY")
    if games_status.get("provider_status") != "ok":
        reasons.append("PROVIDER_UNAVAILABLE")
    games = _envelope_rows(games_body)
    if games is None:
        reasons.append("SCHEDULE_ENVELOPE_UNQUALIFIED")
        games = []
    if not games:
        reasons.append("NO_GAMES")
    raw_ids = [game.get("id") for game in games]
    if (skipped_game_ids or not all(_integer(gid, minimum=1) for gid in raw_ids)
            or len(set(str(gid) for gid in raw_ids)) != len(raw_ids)
            or set(str(gid) for gid in raw_ids) != set(map(str, schedule_game_ids))):
        reasons.append("SCHEDULE_COVERAGE_UNQUALIFIED")

    for game in games:
        gid = game.get("id")
        if not _integer(gid, minimum=1):
            continue
        game_reasons: list[str] = []
        date = game.get("date")
        if not isinstance(date, dict) or str(date.get("start", ""))[:10] != target_date:
            game_reasons.append("GAME_DATE_MISMATCH")
        status = game.get("status")
        status = status if isinstance(status, dict) else {}
        if status.get("long") != "Finished" or status.get("short") not in (3, "3", "FT", "AOT"):
            game_reasons.append("NOT_FINAL")
        periods = game.get("periods")
        total = periods.get("total") if isinstance(periods, dict) else None
        if not _integer(total, minimum=4):
            game_reasons.append("FINAL_PERIOD_COUNT_UNQUALIFIED")
        teams = game.get("teams")
        scores = game.get("scores")
        teams = teams if isinstance(teams, dict) else {}
        scores = scores if isinstance(scores, dict) else {}
        expected: dict[int, int] = {}
        for side in ("home", "visitors"):
            team, score = teams.get(side), scores.get(side)
            tid = team.get("id") if isinstance(team, dict) else None
            points = score.get("points") if isinstance(score, dict) else None
            if not _integer(tid, minimum=1) or not _integer(points):
                game_reasons.append("FINAL_TEAM_IDENTITY_OR_SCORE_MISSING")
            else:
                expected[tid] = points
        if len(expected) != 2:
            game_reasons.append("FINAL_TEAM_COVERAGE_UNQUALIFIED")
        if stats_statuses.get(gid, {}).get("provider_status") != "ok":
            game_reasons.append("STATS_PROVIDER_UNAVAILABLE")
        stats = _envelope_rows(stats_bodies.get(gid))
        if stats is None:
            game_reasons.append("STATS_ENVELOPE_UNQUALIFIED")
            stats = []
        if not stats:
            game_reasons.append("EMPTY_STATS")
        players: set[int] = set()
        totals = {tid: {"seconds": 0, "points": 0, "participants": 0} for tid in expected}
        for row in stats:
            player, team, stat_game = row.get("player"), row.get("team"), row.get("game")
            pid = player.get("id") if isinstance(player, dict) else None
            tid = team.get("id") if isinstance(team, dict) else None
            row_gid = stat_game.get("id") if isinstance(stat_game, dict) else None
            row_date = stat_game.get("date") if isinstance(stat_game, dict) else None
            seconds = _seconds(row.get("min"))
            if (not _integer(pid, minimum=1) or pid in players
                    or not _integer(tid, minimum=1) or tid not in expected
                    or not _integer(row_gid, minimum=1) or row_gid != gid
                    or (row_date is not None and str(row_date)[:10] != target_date)
                    or seconds is None or any(not _integer(row.get(key)) for key in STAT_FIELDS)):
                game_reasons.append("PLAYER_STATS_IDENTITY_OR_VALUES_UNQUALIFIED")
                continue
            if _integer(total, minimum=4) and seconds > (48 + (total - 4) * 5) * 60:
                game_reasons.append("PLAYER_MINUTES_EXCEED_GAME_DURATION")
            players.add(pid)
            totals[tid]["seconds"] += seconds
            totals[tid]["points"] += row["points"]
            totals[tid]["participants"] += int(seconds > 0)
        if _integer(total, minimum=4):
            expected_seconds = 5 * (48 + (total - 4) * 5) * 60
            if any(values["seconds"] != expected_seconds or values["points"] != expected[tid]
                   or values["participants"] < 5 for tid, values in totals.items()):
                game_reasons.append("PARTIAL_PLAYER_STATS")
        game_evidence.append({"game_id": gid, "provider_status": status,
                              "game_date": date, "final_team_scores": expected,
                              "periods_total": total, "team_totals": totals,
                              "row_count": len(stats), "reasons": sorted(set(game_reasons))})
        reasons.extend(f"game:{gid}:{reason}" for reason in sorted(set(game_reasons)))
    return {"contract": CONTRACT, "canonical_publication_allowed": not reasons,
            "classification": "FINAL_COMPLETE" if not reasons else "UNQUALIFIED",
            "reasons": sorted(set(reasons)), "games": game_evidence}
