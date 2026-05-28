from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from courtvision.context.game_context import (
    IDENTITY_OUTSIDE_TEAM_REASON,
    IDENTITY_STALE_TEAM_REASON,
    is_identity_quarantined,
)


PLAYER_IDENTITY_REJECTION_REASON = "player_identity_validation"
PLAYER_ID_TEAM_CONFLICT_REASON = "player_id_team_conflict"
BASELINE_PROVIDER_TEAM_CONFLICT_REASON = "baseline_provider_team_conflict"
PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON = "player_team_not_in_active_game"
SOURCE_IDENTITY_CONFLICT_POLICY_ROW_VALID = "row_valid_but_source_conflicted"
SOURCE_IDENTITY_CONFLICT_POLICY_ROW_INVALID = "row_invalid_source_conflicted"
SOURCE_IDENTITY_CONFLICT_POLICY_ROW_QUARANTINED = "row_quarantined_source_conflicted"

PLAYER_IDENTITY_COLUMNS: tuple[str, ...] = (
    "canonical_player_id",
    "canonical_player_name",
    "canonical_team_abbr",
    "player_identity_valid",
    "player_identity_status",
    "player_identity_conflict_reason",
    "player_identity_conflict_details",
    "identity_roster_date",
    "identity_resolution_category",
)
SOURCE_IDENTITY_CONFLICT_COLUMNS: tuple[str, ...] = (
    "row_identity_valid",
    "row_identity_quarantined",
    "row_identity_quarantine_reason",
    "source_identity_conflicted",
    "source_identity_conflict_reason",
    "source_identity_conflict_details",
    "source_identity_conflict_policy",
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


def _truthy(value: Any) -> bool | None:
    text = _text(value).lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return None


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

    def canonical_team(self, player_id: Any) -> str:
        key = _player_id_key(player_id)
        if not key:
            return ""
        identity = self.identities.get(key)
        return identity.team_abbr if identity else ""

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
        category = ""

        if not player_id or not candidate_team or not baseline_team:
            category = "missing_team"
            reason = PLAYER_IDENTITY_REJECTION_REASON
        else:
            is_multi_stint = identity and (len(identity.baseline_team_abbrs) > 1 or len(identity.provider_team_abbrs) > 1)
            
            if is_multi_stint:
                if candidate_team == canonical_team:
                    category = "valid_current_team_override"
                else:
                    category = "historical_stint_mismatch"
                    reason = PLAYER_ID_TEAM_CONFLICT_REASON
            elif (baseline_team and provider_team and baseline_team != provider_team) or (baseline_team and source_team and source_team != baseline_team):
                if provider_team == canonical_team and provider_team in game_teams:
                    category = "valid_current_team_override"
                else:
                    category = "stale_baseline_team"
                    reason = BASELINE_PROVIDER_TEAM_CONFLICT_REASON
            elif candidate_team and game_teams and candidate_team not in game_teams:
                category = "true_identity_conflict"
                reason = PLAYER_TEAM_NOT_IN_ACTIVE_GAME_REASON
            else:
                category = ""

        out["canonical_player_id"] = player_id
        out["canonical_player_name"] = identity.player_name if identity else _player_name(out)
        out["canonical_team_abbr"] = canonical_team
        out["identity_roster_date"] = self.prediction_date
        out["identity_resolution_category"] = category

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


def _source_identity_diagnostic_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    direct_rows = payload.get("diagnostic_rows")
    if isinstance(direct_rows, list):
        return [row for row in direct_rows if isinstance(row, Mapping)]
    player_identity = payload.get("player_identity")
    if isinstance(player_identity, Mapping):
        rows = player_identity.get("diagnostic_rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        return _source_identity_diagnostic_rows(summary)
    return []


def source_identity_conflict_lookup(payload: Any) -> dict[str, dict[str, str]]:
    """Return source-conflict metadata keyed by canonical player id.

    This lookup is diagnostic-only. It does not imply row-level identity failure
    or quarantine; it exposes resolver source conflicts on operator artifacts.
    """

    grouped: dict[str, dict[str, Any]] = {}
    for row in _source_identity_diagnostic_rows(payload):
        player_id = _player_id_key(row.get("player_id") or row.get("canonical_player_id"))
        if not player_id:
            continue
        bucket = grouped.setdefault(
            player_id,
            {
                "player_id": player_id,
                "reasons": [],
                "details": [],
            },
        )
        reason = _text(row.get("player_identity_conflict_reason"))
        if reason and reason not in bucket["reasons"]:
            bucket["reasons"].append(reason)
        details = _text(row.get("player_identity_conflict_details"))
        if details and details not in bucket["details"]:
            bucket["details"].append(details)

    lookup: dict[str, dict[str, str]] = {}
    for player_id, bucket in grouped.items():
        detail_values: list[Any] = []
        for raw_detail in bucket["details"]:
            try:
                detail_values.append(json.loads(raw_detail))
            except (TypeError, ValueError, json.JSONDecodeError):
                detail_values.append(raw_detail)
        if not detail_values:
            detail_text = ""
        elif len(detail_values) == 1:
            detail_text = json.dumps(detail_values[0], sort_keys=True)
        else:
            detail_text = json.dumps(detail_values, sort_keys=True)
        lookup[player_id] = {
            "source_identity_conflict_reason": ";".join(bucket["reasons"]),
            "source_identity_conflict_details": detail_text,
        }
    return lookup


def source_identity_conflict_diagnostic_count(payload: Any) -> int:
    if isinstance(payload, Mapping):
        if "conflict_count" in payload:
            try:
                return int(payload.get("conflict_count") or 0)
            except (TypeError, ValueError):
                pass
        player_identity = payload.get("player_identity")
        if isinstance(player_identity, Mapping) and "conflict_count" in player_identity:
            try:
                return int(player_identity.get("conflict_count") or 0)
            except (TypeError, ValueError):
                pass
        summary = payload.get("summary")
        if isinstance(summary, Mapping):
            return source_identity_conflict_diagnostic_count(summary)
    return len(_source_identity_diagnostic_rows(payload))


def annotate_source_identity_conflicts(
    df: pd.DataFrame,
    source_identity_payload: Any,
) -> pd.DataFrame:
    """Annotate rows whose player id exists in source identity diagnostics."""

    out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    lookup = source_identity_conflict_lookup(source_identity_payload)
    if out.empty:
        for column in SOURCE_IDENTITY_CONFLICT_COLUMNS:
            if column not in out.columns:
                out[column] = "" if column not in {"row_identity_valid", "row_identity_quarantined", "source_identity_conflicted"} else False
        return out

    if "player_id" not in out.columns and "canonical_player_id" not in out.columns:
        for column in SOURCE_IDENTITY_CONFLICT_COLUMNS:
            if column not in out.columns:
                out[column] = "" if column not in {"row_identity_valid", "row_identity_quarantined", "source_identity_conflicted"} else False
        return out

    row_valid_values: list[bool] = []
    row_quarantined_values: list[bool] = []
    row_quarantine_reasons: list[str] = []
    source_conflicted_values: list[bool] = []
    source_reasons: list[str] = []
    source_details: list[str] = []
    source_policies: list[str] = []

    for _, row in out.iterrows():
        player_id = _player_id_key(row.get("player_id") or row.get("canonical_player_id"))
        source_meta = lookup.get(player_id)
        quarantine_reason = is_identity_quarantined(row) or ""
        row_quarantined = bool(quarantine_reason)
        explicit_valid = _truthy(row.get("player_identity_valid"))
        row_conflict_reason = _text(row.get("player_identity_conflict_reason"))
        row_valid = explicit_valid if explicit_valid is not None else not row_quarantined and not bool(row_conflict_reason)
        
        resolution_cat = _text(row.get("identity_resolution_category"))
        if resolution_cat == "valid_current_team_override":
            source_conflicted = False
        else:
            source_conflicted = source_meta is not None

        if source_conflicted:
            if row_quarantined:
                policy = SOURCE_IDENTITY_CONFLICT_POLICY_ROW_QUARANTINED
            elif row_valid:
                policy = SOURCE_IDENTITY_CONFLICT_POLICY_ROW_VALID
            else:
                policy = SOURCE_IDENTITY_CONFLICT_POLICY_ROW_INVALID
        else:
            policy = ""

        row_valid_values.append(bool(row_valid))
        row_quarantined_values.append(row_quarantined)
        row_quarantine_reasons.append(quarantine_reason)
        source_conflicted_values.append(source_conflicted)
        source_reasons.append(source_meta.get("source_identity_conflict_reason", "") if source_meta and source_conflicted else "")
        source_details.append(source_meta.get("source_identity_conflict_details", "") if source_meta and source_conflicted else "")
        source_policies.append(policy)

    out["row_identity_valid"] = row_valid_values
    out["row_identity_quarantined"] = row_quarantined_values
    out["row_identity_quarantine_reason"] = row_quarantine_reasons
    out["source_identity_conflicted"] = source_conflicted_values
    out["source_identity_conflict_reason"] = source_reasons
    out["source_identity_conflict_details"] = source_details
    out["source_identity_conflict_policy"] = source_policies
    return out


def source_identity_conflicted_row_count(df: pd.DataFrame) -> int:
    if not isinstance(df, pd.DataFrame) or df.empty or "source_identity_conflicted" not in df.columns:
        return 0
    values = df["source_identity_conflicted"].map(lambda value: _truthy(value) is True)
    return int(values.sum())


def _source_identity_player_key(row: pd.Series | Mapping[str, Any]) -> str:
    for column in ("player_id", "canonical_player_id"):
        player_id = _player_id_key(row.get(column))
        if player_id:
            return player_id
    for column in ("player_name", "canonical_player_name", "entity_name"):
        name = " ".join(_text(row.get(column)).lower().split())
        if name:
            return f"name:{name}"
    return ""


def source_identity_conflict_exposure_summary(
    *,
    source_identity_payload: Any,
    full_market_df: pd.DataFrame | None = None,
    elite_df: pd.DataFrame | None = None,
    kelly_df: pd.DataFrame | None = None,
    high_caution_watchlist_df: pd.DataFrame | None = None,
    combo_under_watchlist_df: pd.DataFrame | None = None,
    paper_kelly_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    lookup = source_identity_conflict_lookup(source_identity_payload)

    def _annotated_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame()
        if "source_identity_conflicted" in frame.columns:
            return frame
        return annotate_source_identity_conflicts(frame, source_identity_payload)

    def _conflicted_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
        annotated = _annotated_frame(frame)
        if annotated.empty or "source_identity_conflicted" not in annotated.columns:
            return annotated.iloc[0:0].copy()
        mask = annotated["source_identity_conflicted"].map(lambda value: _truthy(value) is True)
        return annotated.loc[mask].copy()

    def _annotated_count(frame: pd.DataFrame | None) -> int:
        return int(len(_conflicted_frame(frame)))

    def _player_keys(frame: pd.DataFrame | None) -> set[str]:
        conflicted = _conflicted_frame(frame)
        if conflicted.empty:
            return set()
        keys = {
            key
            for _, row in conflicted.iterrows()
            if (key := _source_identity_player_key(row))
        }
        return keys

    def _example_rows(frame: pd.DataFrame | None, *, lane: str, artifact: str) -> list[dict[str, str]]:
        conflicted = _conflicted_frame(frame)
        examples: list[dict[str, str]] = []
        for _, row in conflicted.iterrows():
            player_id = _player_id_key(row.get("player_id") or row.get("canonical_player_id"))
            lookup_meta = lookup.get(player_id, {})
            examples.append(
                {
                    "player_id": player_id,
                    "player_name": (
                        _text(row.get("player_name"))
                        or _text(row.get("canonical_player_name"))
                        or _text(row.get("entity_name"))
                    ),
                    "market_type": _text(row.get("market_type") or row.get("raw_market_type") or row.get("market.type")),
                    "artifact": artifact,
                    "lane": lane,
                    "policy": _text(row.get("source_identity_conflict_policy")),
                    "conflict_reason": (
                        _text(row.get("source_identity_conflict_reason"))
                        or _text(lookup_meta.get("source_identity_conflict_reason"))
                        or _text(row.get("player_identity_conflict_reason"))
                    ),
                }
            )
        return examples

    full_market_count = _annotated_count(full_market_df)
    elite_count = _annotated_count(elite_df)
    kelly_count = _annotated_count(kelly_df)
    high_caution_count = _annotated_count(high_caution_watchlist_df)
    combo_under_count = _annotated_count(combo_under_watchlist_df)
    paper_count = _annotated_count(paper_kelly_df)
    full_market_players = _player_keys(full_market_df)
    elite_players = _player_keys(elite_df)
    kelly_players = _player_keys(kelly_df)
    high_caution_players = _player_keys(high_caution_watchlist_df)
    combo_under_players = _player_keys(combo_under_watchlist_df)
    watchlist_players = high_caution_players | combo_under_players
    paper_players = _player_keys(paper_kelly_df)
    all_lane_players = full_market_players | elite_players | kelly_players | watchlist_players | paper_players
    watchlist_count = high_caution_count + combo_under_count
    blocking_count = elite_count + kelly_count
    operator_visible_count = full_market_count + watchlist_count + paper_count
    example_candidates: list[dict[str, str]] = []
    example_candidates.extend(
        _example_rows(full_market_df, lane="full_market", artifact="full_market_board")
    )
    example_candidates.extend(_example_rows(elite_df, lane="elite", artifact="elite_board"))
    example_candidates.extend(_example_rows(kelly_df, lane="kelly", artifact="kelly_stakes"))
    example_candidates.extend(
        _example_rows(high_caution_watchlist_df, lane="watchlist", artifact="high_caution_over_watchlist")
    )
    example_candidates.extend(
        _example_rows(combo_under_watchlist_df, lane="watchlist", artifact="combo_under_watchlist")
    )
    example_candidates.extend(_example_rows(paper_kelly_df, lane="paper", artifact="paper_kelly_simulation"))
    examples: list[dict[str, str]] = []
    seen_examples: set[tuple[str, str, str]] = set()
    for example in example_candidates:
        player_key = (
            _player_id_key(example.get("player_id"))
            or f"name:{' '.join(_text(example.get('player_name')).lower().split())}"
        )
        dedupe_key = (player_key, example["artifact"], example["lane"])
        if not player_key or dedupe_key in seen_examples:
            continue
        seen_examples.add(dedupe_key)
        examples.append(example)
        if len(examples) >= 5:
            break
    if blocking_count > 0:
        safety_state = "blocking_manual_review_required"
    elif operator_visible_count > 0:
        safety_state = "non_blocking_diagnostic_warning"
    else:
        safety_state = "clear"
    return {
        "source_identity_conflict_count": int(source_identity_conflict_diagnostic_count(source_identity_payload)),
        "source_identity_conflicted_player_count": int(max(len(lookup), len(all_lane_players))),
        "source_identity_conflicted_operator_rows": full_market_count,
        "source_identity_conflicted_full_market_rows": full_market_count,
        "source_identity_conflicted_elite_rows": elite_count,
        "source_identity_conflicted_kelly_rows": kelly_count,
        "source_identity_conflicted_watchlist_rows": watchlist_count,
        "source_identity_conflicted_high_caution_watchlist_rows": high_caution_count,
        "source_identity_conflicted_combo_under_watchlist_rows": combo_under_count,
        "source_identity_conflicted_paper_rows": paper_count,
        "source_identity_conflicted_full_market_players": int(len(full_market_players)),
        "source_identity_conflicted_elite_players": int(len(elite_players)),
        "source_identity_conflicted_kelly_players": int(len(kelly_players)),
        "source_identity_conflicted_watchlist_players": int(len(watchlist_players)),
        "source_identity_conflicted_paper_players": int(len(paper_players)),
        "source_identity_conflict_examples": examples,
        "source_identity_conflicted_operator_visible_rows": operator_visible_count,
        "source_identity_conflict_blocking_rows": blocking_count,
        "source_identity_conflict_safety_state": safety_state,
        "source_identity_conflict_policy": (
            "blocking_manual_review_required_for_elite_or_kelly"
            if blocking_count > 0
            else "row_valid_but_source_conflicted_non_blocking_diagnostic"
            if operator_visible_count > 0
            else "no_operator_visible_source_conflict"
        ),
    }
