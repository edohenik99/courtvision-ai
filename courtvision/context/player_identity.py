from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import pandas as pd

from courtvision.context.game_context import (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
)


PLAYER_IDENTITY_REJECTION_REASON = "player_identity_validation"
PLAYER_ID_TEAM_CONFLICT_REASON = "player_id_team_conflict"
BASELINE_PROVIDER_TEAM_CONFLICT_REASON = "baseline_provider_team_conflict"
PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON = "player_team_not_in_active_game"

PLAYER_IDENTITY_COLUMNS: tuple[str, ...] = (
    "canonical_player_id",
    "canonical_player_name",
    "canonical_team_abbr",
    "player_identity_valid",
    "player_identity_status",
    "player_identity_conflict_reason",
    "player_identity_conflict_details",
    "identity_roster_date",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>", "nat"} else text


def _team(value: Any) -> str:
    return _text(value).upper()


def _player_id_key(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() and number > 0 else text


def _game_id_key(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    return str(int(number)) if number.is_integer() else text


def _first_team(row: pd.Series | dict[str, Any], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = _team(row.get(column))
        if value:
            return value
    return ""


def _candidate_team(row: pd.Series | dict[str, Any]) -> str:
    return _first_team(row, ("team_abbr", "team", "team_abbreviation"))


def _baseline_team(row: pd.Series | dict[str, Any]) -> str:
    return _first_team(row, ("baseline_team_abbr", "team_abbr", "team", "team_abbreviation"))


def _provider_team(row: pd.Series | dict[str, Any]) -> str:
    return _first_team(
        row,
        (
            "provider_team_abbr",
            "odds_team_abbr",
            "_team_abbr",
            "team_abbr",
            "team",
            "team.abbreviation",
            "team.abbr",
        ),
    )


def _source_team(row: pd.Series | dict[str, Any]) -> str:
    return _first_team(
        row,
        (
            "identity_source_team_abbr",
            "resolved_team_abbr",
            "provider_team_abbr",
            "odds_team_abbr",
            "_team_abbr",
        ),
    )


def _player_name(row: pd.Series | dict[str, Any]) -> str:
    explicit = _text(row.get("player_name") or row.get("entity_name"))
    if explicit:
        return explicit
    first = _text(row.get("first_name") or row.get("player.first_name"))
    last = _text(row.get("last_name") or row.get("player.last_name"))
    return f"{first} {last}".strip()


def _row_game_teams(row: pd.Series | dict[str, Any]) -> set[str]:
    home = _first_team(
        row,
        (
            "game_home_team_abbr",
            "home_team_abbr",
            "home_team",
            "home",
        ),
    )
    away = _first_team(
        row,
        (
            "game_away_team_abbr",
            "game_visitor_team_abbr",
            "visitor_team_abbr",
            "away_team_abbr",
            "away_team",
            "visitor_team",
            "away",
        ),
    )
    return {team for team in (home, away) if team}


def _game_team_maps(games: pd.DataFrame) -> tuple[set[str], dict[str, set[str]]]:
    active_teams: set[str] = set()
    game_teams_by_id: dict[str, set[str]] = {}
    if not isinstance(games, pd.DataFrame) or games.empty:
        return active_teams, game_teams_by_id

    for _, row in games.iterrows():
        home = _first_team(row, ("home_team_abbr", "home.abbreviation", "home_team.abbreviation"))
        away = _first_team(row, ("visitor_team_abbr", "away_team_abbr", "visitor.abbreviation", "visitor_team.abbreviation"))
        teams = {team for team in (home, away) if team}
        active_teams.update(teams)
        game_id = _game_id_key(row.get("game_id") or row.get("id"))
        if game_id and teams:
            game_teams_by_id[game_id] = teams
    return active_teams, game_teams_by_id


def _records_by_player_id(df: pd.DataFrame, *, source: str) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(df, pd.DataFrame) or df.empty or "player_id" not in df.columns:
        return records

    for _, row in df.iterrows():
        player_id = _player_id_key(row.get("player_id"))
        if not player_id:
            continue
        team = _provider_team(row) if source == "provider" else _baseline_team(row)
        game_id = _game_id_key(row.get("game_id"))
        records[player_id].append(
            {
                "player_id": player_id,
                "player_name": _player_name(row),
                "team_abbr": team,
                "game_id": game_id,
                "source": source,
            }
        )
    return records


@dataclass(frozen=True)
class CanonicalPlayerIdentity:
    player_id: str
    player_name: str
    team_abbr: str
    source: str
    baseline_team_abbrs: tuple[str, ...]
    provider_team_abbrs: tuple[str, ...]


class CanonicalPlayerIdentityResolver:
    """Resolve player identity for one prediction date and expose diagnostics."""

    def __init__(
        self,
        *,
        prediction_date: str,
        player_baselines: pd.DataFrame,
        odds: pd.DataFrame,
        games: pd.DataFrame,
    ) -> None:
        self.prediction_date = str(prediction_date)
        self.active_teams, self.game_teams_by_id = _game_team_maps(games)
        self.baseline_records = _records_by_player_id(player_baselines, source="baseline")
        self.provider_records = _records_by_player_id(odds, source="provider")
        self.identities = self._build_identities()
        self._diagnostic_rows = self._build_source_diagnostics()

    def _build_identities(self) -> dict[str, CanonicalPlayerIdentity]:
        identities: dict[str, CanonicalPlayerIdentity] = {}
        for player_id in sorted(set(self.baseline_records) | set(self.provider_records)):
            baseline_rows = self.baseline_records.get(player_id, [])
            provider_rows = self.provider_records.get(player_id, [])
            baseline_teams = sorted({row["team_abbr"] for row in baseline_rows if row.get("team_abbr")})
            provider_teams = sorted({row["team_abbr"] for row in provider_rows if row.get("team_abbr")})
            names = [_text(row.get("player_name")) for row in baseline_rows + provider_rows if _text(row.get("player_name"))]
            active_baseline = [team for team in baseline_teams if team in self.active_teams]
            active_provider = [team for team in provider_teams if team in self.active_teams]
            if len(active_baseline) == 1:
                canonical_team = active_baseline[0]
                source = "baseline_active_game"
            elif len(active_provider) == 1:
                canonical_team = active_provider[0]
                source = "provider_active_game"
            elif len(baseline_teams) == 1:
                canonical_team = baseline_teams[0]
                source = "baseline"
            elif len(provider_teams) == 1:
                canonical_team = provider_teams[0]
                source = "provider"
            else:
                canonical_team = baseline_teams[0] if baseline_teams else provider_teams[0] if provider_teams else ""
                source = "ambiguous"
            identities[player_id] = CanonicalPlayerIdentity(
                player_id=player_id,
                player_name=names[0] if names else "",
                team_abbr=canonical_team,
                source=source,
                baseline_team_abbrs=tuple(baseline_teams),
                provider_team_abbrs=tuple(provider_teams),
            )
        return identities

    def _build_source_diagnostics(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for player_id, identity in sorted(self.identities.items()):
            if len(identity.baseline_team_abbrs) > 1 or len(identity.provider_team_abbrs) > 1:
                rows.append(
                    self._diagnostic_row(
                        identity=identity,
                        reason=PLAYER_ID_TEAM_CONFLICT_REASON,
                        team=identity.team_abbr,
                        details={
                            "baseline_team_abbrs": list(identity.baseline_team_abbrs),
                            "provider_team_abbrs": list(identity.provider_team_abbrs),
                            "canonical_team_abbr": identity.team_abbr,
                            "canonical_source": identity.source,
                        },
                    )
                )

            for provider_team in identity.provider_team_abbrs:
                baseline_teams = set(identity.baseline_team_abbrs)
                if baseline_teams and provider_team not in baseline_teams:
                    rows.append(
                        self._diagnostic_row(
                            identity=identity,
                            reason=BASELINE_PROVIDER_TEAM_CONFLICT_REASON,
                            team=identity.team_abbr or provider_team,
                            details={
                                "baseline_team_abbrs": list(identity.baseline_team_abbrs),
                                "provider_team_abbr": provider_team,
                                "canonical_team_abbr": identity.team_abbr,
                            },
                        )
                    )

        for player_id, provider_rows in sorted(self.provider_records.items()):
            identity = self.identities.get(player_id)
            for row in provider_rows:
                provider_team = _team(row.get("team_abbr"))
                game_id = _game_id_key(row.get("game_id"))
                game_teams = self.game_teams_by_id.get(game_id, set())
                if provider_team and game_teams and provider_team not in game_teams:
                    rows.append(
                        self._diagnostic_row(
                            identity=identity,
                            reason=PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON,
                            team=provider_team,
                            details={
                                "provider_team_abbr": provider_team,
                                "game_id": game_id,
                                "game_team_abbrs": sorted(game_teams),
                            },
                        )
                    )
        return rows

    def _diagnostic_row(
        self,
        *,
        identity: CanonicalPlayerIdentity | None,
        reason: str,
        team: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        player_id = identity.player_id if identity else ""
        player_name = identity.player_name if identity else ""
        canonical_team = identity.team_abbr if identity else ""
        return {
            "prediction_date": self.prediction_date,
            "player_id": player_id,
            "player_name": player_name,
            "team": team,
            "team_abbr": team,
            "canonical_player_id": player_id,
            "canonical_player_name": player_name,
            "canonical_team_abbr": canonical_team,
            "identity_roster_date": self.prediction_date,
            "player_identity_valid": False,
            "player_identity_status": "invalid",
            "player_identity_conflict_reason": reason,
            "player_identity_conflict_details": json.dumps(details, sort_keys=True),
            "rejection_reason": PLAYER_IDENTITY_REJECTION_REASON,
            "selection_rejection_reason": PLAYER_IDENTITY_REJECTION_REASON,
        }

    def annotate_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.annotate_record(record) for record in records]

    def annotate_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame) or df.empty:
            out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
            for column in PLAYER_IDENTITY_COLUMNS:
                if column not in out.columns:
                    out[column] = "" if column != "player_identity_valid" else True
            return out
        return pd.DataFrame([self.annotate_record(row) for _, row in df.iterrows()], index=df.index)

    def annotate_record(self, record: pd.Series | dict[str, Any]) -> dict[str, Any]:
        out = dict(record)
        player_id = _player_id_key(out.get("player_id"))
        identity = self.identities.get(player_id)
        candidate_team = _candidate_team(out)
        baseline_team = _team(out.get("baseline_team_abbr")) or (identity.team_abbr if identity else "")
        provider_team = _team(out.get("provider_team_abbr") or out.get("odds_team_abbr"))
        source_team = _source_team(out)
        canonical_team = identity.team_abbr if identity else candidate_team
        game_teams = _row_game_teams(out)
        if not game_teams:
            game_teams = self.game_teams_by_id.get(_game_id_key(out.get("game_id")), set())
        if not game_teams and self.active_teams and _text(out.get("is_live_market")).lower() in {"true", "1", "yes"}:
            game_teams = self.active_teams

        details: dict[str, Any] = {
            "candidate_team_abbr": candidate_team,
            "baseline_team_abbr": baseline_team,
            "provider_team_abbr": provider_team,
            "identity_source_team_abbr": source_team,
            "canonical_team_abbr": canonical_team,
            "game_team_abbrs": sorted(game_teams),
        }
        if identity:
            details["baseline_team_abbrs"] = list(identity.baseline_team_abbrs)
            details["provider_team_abbrs"] = list(identity.provider_team_abbrs)
            details["canonical_source"] = identity.source

        reason = ""
        if (
            identity
            and (len(identity.baseline_team_abbrs) > 1 or len(identity.provider_team_abbrs) > 1)
            and canonical_team
            and candidate_team
            and candidate_team != canonical_team
        ):
            reason = PLAYER_ID_TEAM_CONFLICT_REASON
        elif baseline_team and provider_team and baseline_team != provider_team:
            reason = BASELINE_PROVIDER_TEAM_CONFLICT_REASON
        elif baseline_team and source_team and source_team != baseline_team:
            reason = BASELINE_PROVIDER_TEAM_CONFLICT_REASON
        elif candidate_team and game_teams and candidate_team not in game_teams:
            reason = PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON

        out["canonical_player_id"] = player_id
        out["canonical_player_name"] = identity.player_name if identity else _player_name(out)
        out["canonical_team_abbr"] = canonical_team
        out["identity_roster_date"] = self.prediction_date

        if not reason:
            out["player_identity_valid"] = True
            out["player_identity_status"] = "valid"
            out["player_identity_conflict_reason"] = ""
            out["player_identity_conflict_details"] = ""
            return out

        out["player_identity_valid"] = False
        out["player_identity_status"] = "invalid"
        out["player_identity_conflict_reason"] = reason
        out["player_identity_conflict_details"] = json.dumps(details, sort_keys=True)
        if candidate_team and game_teams and candidate_team not in game_teams:
            out["candidate_team_not_in_game"] = True
            out["context_conflict_cause"] = "stale_team_not_in_game"
            out["identity_quarantine_reason"] = IDENTITY_OUTSIDE_TEAM_REASON
        else:
            out["identity_quarantine_reason"] = IDENTITY_STALE_TEAM_REASON
        return out

    def diagnostic_rows(self) -> list[dict[str, Any]]:
        return list(self._diagnostic_rows)

    def summary(self) -> dict[str, Any]:
        counts = Counter(
            str(row.get("player_identity_conflict_reason") or "").strip()
            for row in self._diagnostic_rows
            if str(row.get("player_identity_conflict_reason") or "").strip()
        )
        return {
            "status": "conflicts_detected" if counts else "ok",
            "rejection_reason": PLAYER_IDENTITY_REJECTION_REASON,
            "prediction_date": self.prediction_date,
            "conflict_count": int(sum(counts.values())),
            "counts_by_reason": dict(sorted(counts.items())),
            "diagnostic_rows": self.diagnostic_rows(),
        }


def build_canonical_player_identity_resolver(
    *,
    prediction_date: str,
    player_baselines: pd.DataFrame,
    odds: pd.DataFrame,
    games: pd.DataFrame,
) -> CanonicalPlayerIdentityResolver:
    return CanonicalPlayerIdentityResolver(
        prediction_date=prediction_date,
        player_baselines=player_baselines,
        odds=odds,
        games=games,
    )


def player_identity_reason_counts(*frames: pd.DataFrame) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for df in frames:
        if not isinstance(df, pd.DataFrame) or df.empty or "player_identity_conflict_reason" not in df.columns:
            continue
        reasons = df["player_identity_conflict_reason"].fillna("").astype(str).str.strip()
        counts.update(reason for reason in reasons if reason)
    return dict(sorted(counts.items()))
