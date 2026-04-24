from __future__ import annotations

from collections import defaultdict

from courtvision.models import Game, PlayerGameStats


def build_player_pool(games: list[Game], stats: list[PlayerGameStats], minimum_games_played: int) -> dict[int, dict]:
    team_ids = {g.home_team.id for g in games} | {g.visitor_team.id for g in games}
    by_player: dict[int, list[PlayerGameStats]] = defaultdict(list)
    for row in stats:
        if row.team_id in team_ids:
            by_player[row.player_id].append(row)

    pool: dict[int, dict] = {}
    for player_id, rows in by_player.items():
        rows = sorted(rows, key=lambda item: item.game_id, reverse=True)
        if len(rows) < minimum_games_played:
            continue
        sample = rows[0]
        pool[player_id] = {
            "player_id": player_id,
            "player_name": sample.player_name,
            "team_id": sample.team_id,
            "games_played": len(rows),
            "rows": rows,
        }
    return pool
