"""Read-only validation for proposed MLB HR batter-game crosswalks.

The validator checks local CSV bytes only.  It does not fetch authoritative
records, repair mappings, write reports, or promote data into CourtVision's
historical-pack or runtime directories.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from types import MappingProxyType
from typing import Final, Mapping

from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    normalize_player_name,
)


MLB_HR_CROSSWALK_VERSION: Final = "mlb-hr-batter-game-crosswalk-v2"

REQUIRED_CROSSWALK_COLUMNS: Final = frozenset(
    {
        "game_date",
        "retrosheet_game_id",
        "mlbam_game_id",
        "game_number",
        "retrosheet_batter_id",
        "mlbam_batter_id",
        "batter_name",
        "retrosheet_home_team_id",
        "home_team",
        "retrosheet_away_team_id",
        "away_team",
        "retrosheet_batting_team_id",
        "batting_team",
        "retrosheet_fielding_team_id",
        "fielding_team",
        "player_mapping_source",
        "game_mapping_source",
        "team_mapping_source",
        "verified_at",
    }
)

# Canonical abbreviations match the team codes expected by the existing local
# MLB HR pack.  ATH is included for post-Oakland Athletics source exports.
MLB_TEAM_ABBREVIATIONS: Final = frozenset(
    {
        "ARI",
        "ATL",
        "ATH",
        "BAL",
        "BOS",
        "CHC",
        "CIN",
        "CLE",
        "COL",
        "CWS",
        "DET",
        "HOU",
        "KC",
        "LAA",
        "LAD",
        "MIA",
        "MIL",
        "MIN",
        "NYM",
        "NYY",
        "OAK",
        "PHI",
        "PIT",
        "SD",
        "SEA",
        "SF",
        "STL",
        "TB",
        "TEX",
        "TOR",
        "WSH",
    }
)

# Retrosheet uses its own stable three-letter identifiers.  This table is an
# explicit crosswalk, not a fuzzy name matcher.  Legacy franchise codes are
# retained so older local research exports can be checked without rewriting
# their source-native identity.
RETROSHEET_TO_MLB_TEAM: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ANA": "LAA",
        "ARI": "ARI",
        "ATH": "ATH",
        "ATL": "ATL",
        "BAL": "BAL",
        "BOS": "BOS",
        "CHA": "CWS",
        "CHN": "CHC",
        "CIN": "CIN",
        "CLE": "CLE",
        "COL": "COL",
        "DET": "DET",
        "FLO": "MIA",
        "HOU": "HOU",
        "KCA": "KC",
        "LAA": "LAA",
        "LAN": "LAD",
        "MIA": "MIA",
        "MIL": "MIL",
        "MIN": "MIN",
        "MON": "WSH",
        "NYA": "NYY",
        "NYN": "NYM",
        "OAK": "OAK",
        "PHI": "PHI",
        "PIT": "PIT",
        "SDN": "SD",
        "SEA": "SEA",
        "SFN": "SF",
        "SLN": "STL",
        "TBA": "TB",
        "TEX": "TEX",
        "TOR": "TOR",
        "WAS": "WSH",
    }
)

_MLBAM_ID = re.compile(r"^[1-9]\d{5,9}$")
_RETROSHEET_PLAYER_ID = re.compile(r"^[a-z]{2,6}\d{3}$")
_RETROSHEET_GAME_ID = re.compile(
    r"^(?P<home_team>[A-Z]{3})(?P<date>\d{8})(?P<game_number>[0-3])$"
)
_PROHIBITED_IDENTITY_TOKEN = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:sample|fixture|mock|test|synthetic|dummy|fake|example|placeholder)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


class MLBHRCrosswalkValidationError(ValueError):
    """Raised when a proposed crosswalk fails read-only validation."""


@dataclass(frozen=True, slots=True)
class MLBHRCrosswalkValidationResult:
    """Stable pass/fail diagnostics for one proposed crosswalk CSV."""

    source_path: Path
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    row_count: int
    valid_row_count: int
    duplicate_mapping_count: int
    conflicting_mapping_count: int
    missing_required_id_count: int
    sample_identity_count: int
    mlbam_batter_count: int
    retrosheet_batter_count: int
    mlbam_game_count: int
    retrosheet_game_count: int

    @property
    def invalid_row_count(self) -> int:
        return self.row_count - self.valid_row_count

    def raise_for_errors(self) -> None:
        if self.errors:
            raise MLBHRCrosswalkValidationError("; ".join(self.errors))


def _text(row: Mapping[str, object], field_name: str) -> str:
    value = row.get(field_name)
    return "" if value is None else str(value).strip()


def _parse_game_date(value: str, row_number: int, errors: list[str]) -> date | None:
    if not value:
        errors.append(f"row {row_number}: game_date is required")
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        errors.append(
            f"row {row_number}: game_date must be an ISO date (YYYY-MM-DD): {value!r}"
        )
        return None
    if parsed.isoformat() != value:
        errors.append(
            f"row {row_number}: game_date must use exact YYYY-MM-DD form: {value!r}"
        )
        return None
    return parsed


def _parse_verified_at(value: str, row_number: int, errors: list[str]) -> None:
    if not value:
        errors.append(f"row {row_number}: verified_at is required")
        return
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"row {row_number}: verified_at must be an ISO datetime")
        return
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"row {row_number}: verified_at must include a UTC offset")


def _validate_mlbam_id(
    value: str,
    *,
    field_name: str,
    row_number: int,
    errors: list[str],
) -> bool:
    if not value:
        errors.append(f"row {row_number}: {field_name} is required")
        return False
    if not _MLBAM_ID.fullmatch(value):
        errors.append(
            f"row {row_number}: {field_name} must be a positive 6-10 digit "
            f"canonical MLBAM id: {value!r}"
        )
        return False
    return True


def _validate_retrosheet_player_id(
    value: str,
    *,
    row_number: int,
    errors: list[str],
) -> bool:
    if not value:
        return False
    if not _RETROSHEET_PLAYER_ID.fullmatch(value):
        errors.append(
            f"row {row_number}: retrosheet_batter_id must be a lowercase "
            f"Retrosheet player id ending in three digits: {value!r}"
        )
        return False
    return True


def _parse_game_number(
    value: str,
    *,
    row_number: int,
    errors: list[str],
) -> int | None:
    if value not in {"1", "2", "3"}:
        errors.append(f"row {row_number}: game_number must be 1, 2, or 3: {value!r}")
        return None
    return int(value)


def _validate_team_pair(
    *,
    retrosheet_value: str,
    mlb_value: str,
    retrosheet_field: str,
    mlb_field: str,
    row_number: int,
    errors: list[str],
) -> bool:
    expected = RETROSHEET_TO_MLB_TEAM.get(retrosheet_value)
    valid = True
    if expected is None:
        errors.append(
            f"row {row_number}: {retrosheet_field} is not a supported "
            f"Retrosheet team id: {retrosheet_value!r}"
        )
        valid = False
    if mlb_value not in MLB_TEAM_ABBREVIATIONS:
        errors.append(
            f"row {row_number}: {mlb_field} is not a canonical MLB team "
            f"abbreviation: {mlb_value!r}"
        )
        valid = False
    if expected is not None and mlb_value in MLB_TEAM_ABBREVIATIONS and expected != mlb_value:
        errors.append(
            f"row {row_number}: team mapping mismatch for {retrosheet_value}: "
            f"expected {expected}, found {mlb_value}"
        )
        valid = False
    return valid


def _set_mapping(
    mapping: dict[str, object],
    *,
    key: str,
    value: object,
    label: str,
    row_number: int,
    errors: list[str],
) -> bool:
    previous = mapping.setdefault(key, value)
    if previous == value:
        return False
    errors.append(
        f"row {row_number}: conflicting {label} mapping for {key!r}: "
        f"previous={previous!r}, current={value!r}"
    )
    return True


def validate_mlb_hr_crosswalk_csv(
    crosswalk_csv: str | Path,
) -> MLBHRCrosswalkValidationResult:
    """Validate one local proposed crosswalk without writing or fetching data."""

    source_path = Path(crosswalk_csv).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    raw_rows: list[tuple[int, dict[str, object]]] = []
    data_row_count = 0

    if not source_path.is_file():
        errors.append(f"crosswalk CSV does not exist: {source_path}")
    else:
        try:
            with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = reader.fieldnames or []
                if not headers:
                    errors.append("crosswalk CSV is missing a header row")
                elif len(headers) != len(set(headers)):
                    errors.append("crosswalk CSV contains duplicate column names")
                missing_columns = sorted(REQUIRED_CROSSWALK_COLUMNS - set(headers))
                if missing_columns:
                    errors.append(
                        "crosswalk CSV is missing required columns: "
                        + ", ".join(missing_columns)
                    )
                if headers:
                    for row_number, row in enumerate(reader, start=2):
                        data_row_count += 1
                        if missing_columns:
                            continue
                        if None in row:
                            errors.append(
                                f"row {row_number}: crosswalk CSV has extra values"
                            )
                            continue
                        raw_rows.append((row_number, dict(row)))
        except (OSError, UnicodeError, csv.Error) as exc:
            errors.append(f"could not read crosswalk CSV {source_path}: {exc}")

    if source_path.is_file() and data_row_count == 0 and not any(
        "missing required columns" in error for error in errors
    ):
        errors.append("crosswalk CSV contains no data rows")

    valid_row_count = 0
    duplicate_mapping_count = 0
    conflicting_mapping_count = 0
    missing_required_id_count = 0
    sample_identity_count = 0
    mlbam_batters: set[str] = set()
    retrosheet_batters: set[str] = set()
    mlbam_games: set[str] = set()
    retrosheet_games: set[str] = set()

    seen_mlbam_batter_games: dict[tuple[str, str], int] = {}
    seen_retrosheet_batter_games: dict[tuple[str, str], int] = {}
    retrosheet_player_to_mlbam: dict[str, object] = {}
    mlbam_player_to_retrosheet: dict[str, object] = {}
    mlbam_player_names: dict[str, object] = {}
    retrosheet_game_to_mlbam: dict[str, object] = {}
    mlbam_game_to_retrosheet: dict[str, object] = {}
    retrosheet_team_to_mlb: dict[str, object] = {}

    for row_number, row in raw_rows:
        error_count_before = len(errors)
        game_date_text = _text(row, "game_date")
        game_date = _parse_game_date(game_date_text, row_number, errors)
        game_number = _parse_game_number(
            _text(row, "game_number"),
            row_number=row_number,
            errors=errors,
        )

        mlbam_game_id = _text(row, "mlbam_game_id")
        mlbam_batter_id = _text(row, "mlbam_batter_id")
        retrosheet_game_id = _text(row, "retrosheet_game_id")
        retrosheet_batter_id = _text(row, "retrosheet_batter_id")

        required_ids = (
            (mlbam_game_id, "mlbam_game_id"),
            (mlbam_batter_id, "mlbam_batter_id"),
            (_text(row, "retrosheet_home_team_id"), "retrosheet_home_team_id"),
            (_text(row, "retrosheet_away_team_id"), "retrosheet_away_team_id"),
            (_text(row, "retrosheet_batting_team_id"), "retrosheet_batting_team_id"),
            (_text(row, "retrosheet_fielding_team_id"), "retrosheet_fielding_team_id"),
        )
        for required_id, field_name in required_ids:
            if not required_id:
                missing_required_id_count += 1

        mlbam_game_valid = _validate_mlbam_id(
            mlbam_game_id,
            field_name="mlbam_game_id",
            row_number=row_number,
            errors=errors,
        )
        mlbam_batter_valid = _validate_mlbam_id(
            mlbam_batter_id,
            field_name="mlbam_batter_id",
            row_number=row_number,
            errors=errors,
        )
        retrosheet_batter_valid = _validate_retrosheet_player_id(
            retrosheet_batter_id,
            row_number=row_number,
            errors=errors,
        )
        if not retrosheet_batter_id:
            warnings.append(
                f"row {row_number}: retrosheet_batter_id is absent; only the "
                "MLBAM side of the player identity can be validated"
            )
        if not retrosheet_game_id:
            warnings.append(
                f"row {row_number}: retrosheet_game_id is absent; MLB StatsAPI "
                "gamePk remains the canonical prospective event identity"
            )

        batter_name = _text(row, "batter_name")
        normalized_name = normalize_player_name(batter_name)
        if normalized_name is None:
            errors.append(f"row {row_number}: batter_name is required")
        row_uses_sample_identity = bool(
            _PROHIBITED_IDENTITY_TOKEN.search(batter_name)
        )
        if row_uses_sample_identity:
            sample_identity_count += 1
            errors.append(
                f"row {row_number}: batter_name uses a sample/fixture/synthetic "
                f"identity: {batter_name!r}"
            )

        for source_field in (
            "player_mapping_source",
            "game_mapping_source",
            "team_mapping_source",
        ):
            source_value = _text(row, source_field)
            if not source_value:
                errors.append(f"row {row_number}: {source_field} is required")
            elif _PROHIBITED_IDENTITY_TOKEN.search(source_value):
                errors.append(
                    f"row {row_number}: {source_field} uses sample/fixture/synthetic "
                    "provenance"
                )
        _parse_verified_at(_text(row, "verified_at"), row_number, errors)

        team_values: dict[str, str] = {}
        all_team_pairs_valid = True
        for role in ("home", "away", "batting", "fielding"):
            retrosheet_field = f"retrosheet_{role}_team_id"
            mlb_field = f"{role}_team"
            retrosheet_value = _text(row, retrosheet_field)
            mlb_value = _text(row, mlb_field)
            team_values[retrosheet_field] = retrosheet_value
            team_values[mlb_field] = mlb_value
            if not _validate_team_pair(
                retrosheet_value=retrosheet_value,
                mlb_value=mlb_value,
                retrosheet_field=retrosheet_field,
                mlb_field=mlb_field,
                row_number=row_number,
                errors=errors,
            ):
                all_team_pairs_valid = False
            if retrosheet_value in RETROSHEET_TO_MLB_TEAM:
                if _set_mapping(
                    retrosheet_team_to_mlb,
                    key=retrosheet_value,
                    value=mlb_value,
                    label="Retrosheet-to-MLB team",
                    row_number=row_number,
                    errors=errors,
                ):
                    conflicting_mapping_count += 1

        home_team = team_values["home_team"]
        away_team = team_values["away_team"]
        batting_team = team_values["batting_team"]
        fielding_team = team_values["fielding_team"]
        if home_team and home_team == away_team:
            errors.append(f"row {row_number}: home_team and away_team must differ")
        if batting_team and fielding_team and batting_team == fielding_team:
            errors.append(f"row {row_number}: batting_team and fielding_team must differ")
        if {batting_team, fielding_team} != {home_team, away_team}:
            errors.append(
                f"row {row_number}: batting/fielding teams must equal the "
                "home/away team pair"
            )
        if {
            team_values["retrosheet_batting_team_id"],
            team_values["retrosheet_fielding_team_id"],
        } != {
            team_values["retrosheet_home_team_id"],
            team_values["retrosheet_away_team_id"],
        }:
            errors.append(
                f"row {row_number}: Retrosheet batting/fielding teams must equal "
                "the Retrosheet home/away team pair"
            )

        game_match = _RETROSHEET_GAME_ID.fullmatch(retrosheet_game_id)
        retrosheet_game_valid = game_match is not None
        if retrosheet_game_id and game_match is None:
            errors.append(
                f"row {row_number}: retrosheet_game_id must use "
                "TTTYYYYMMDDN form: "
                f"{retrosheet_game_id!r}"
            )
        elif game_match is not None:
            encoded_home = game_match.group("home_team")
            encoded_date_text = game_match.group("date")
            encoded_game_number = int(game_match.group("game_number"))
            try:
                encoded_date = datetime.strptime(encoded_date_text, "%Y%m%d").date()
            except ValueError:
                errors.append(
                    f"row {row_number}: retrosheet_game_id contains an invalid date: "
                    f"{retrosheet_game_id!r}"
                )
                retrosheet_game_valid = False
            else:
                if game_date is not None and encoded_date != game_date:
                    errors.append(
                        f"row {row_number}: retrosheet_game_id date {encoded_date} "
                        f"does not match game_date {game_date}"
                    )
                if encoded_home != team_values["retrosheet_home_team_id"]:
                    errors.append(
                        f"row {row_number}: retrosheet_game_id home team "
                        f"{encoded_home} does not match retrosheet_home_team_id "
                        f"{team_values['retrosheet_home_team_id']}"
                    )
                expected_game_number = 1 if encoded_game_number == 0 else encoded_game_number
                if game_number is not None and game_number != expected_game_number:
                    errors.append(
                        f"row {row_number}: game_number {game_number} does not match "
                        f"retrosheet_game_id marker {encoded_game_number}"
                    )

        if mlbam_game_valid:
            mlbam_games.add(mlbam_game_id)
        if mlbam_batter_valid:
            mlbam_batters.add(mlbam_batter_id)
        if retrosheet_game_valid:
            retrosheet_games.add(retrosheet_game_id)
        if retrosheet_batter_valid:
            retrosheet_batters.add(retrosheet_batter_id)

        if mlbam_game_valid and mlbam_batter_valid:
            mlbam_batter_game = (mlbam_game_id, mlbam_batter_id)
            previous_row = seen_mlbam_batter_games.setdefault(
                mlbam_batter_game, row_number
            )
            if previous_row != row_number:
                duplicate_mapping_count += 1
                errors.append(
                    f"row {row_number}: duplicate MLBAM batter-game mapping "
                    f"{mlbam_batter_game}; first seen on row {previous_row}"
                )

        if retrosheet_game_valid and retrosheet_batter_valid:
            retrosheet_batter_game = (retrosheet_game_id, retrosheet_batter_id)
            previous_row = seen_retrosheet_batter_games.setdefault(
                retrosheet_batter_game, row_number
            )
            if previous_row != row_number:
                duplicate_mapping_count += 1
                errors.append(
                    f"row {row_number}: duplicate Retrosheet batter-game mapping "
                    f"{retrosheet_batter_game}; first seen on row {previous_row}"
                )

        if retrosheet_batter_valid and mlbam_batter_valid:
            if _set_mapping(
                retrosheet_player_to_mlbam,
                key=retrosheet_batter_id,
                value=mlbam_batter_id,
                label="Retrosheet-to-MLBAM player",
                row_number=row_number,
                errors=errors,
            ):
                conflicting_mapping_count += 1
            if _set_mapping(
                mlbam_player_to_retrosheet,
                key=mlbam_batter_id,
                value=retrosheet_batter_id,
                label="MLBAM-to-Retrosheet player",
                row_number=row_number,
                errors=errors,
            ):
                conflicting_mapping_count += 1
        if mlbam_batter_valid and normalized_name is not None:
            if _set_mapping(
                mlbam_player_names,
                key=mlbam_batter_id,
                value=normalized_name,
                label="MLBAM player-name",
                row_number=row_number,
                errors=errors,
            ):
                conflicting_mapping_count += 1

        if (
            retrosheet_game_valid
            and mlbam_game_valid
            and game_date is not None
            and all_team_pairs_valid
        ):
            game_context = (
                mlbam_game_id,
                game_date.isoformat(),
                home_team,
                away_team,
                game_number,
            )
            if _set_mapping(
                retrosheet_game_to_mlbam,
                key=retrosheet_game_id,
                value=game_context,
                label="Retrosheet-to-MLBAM game",
                row_number=row_number,
                errors=errors,
            ):
                conflicting_mapping_count += 1
            reverse_game_context = (
                retrosheet_game_id,
                game_date.isoformat(),
                team_values["retrosheet_home_team_id"],
                team_values["retrosheet_away_team_id"],
                game_number,
            )
            if _set_mapping(
                mlbam_game_to_retrosheet,
                key=mlbam_game_id,
                value=reverse_game_context,
                label="MLBAM-to-Retrosheet game",
                row_number=row_number,
                errors=errors,
            ):
                conflicting_mapping_count += 1

        if len(errors) == error_count_before:
            valid_row_count += 1

    is_valid = not errors
    return MLBHRCrosswalkValidationResult(
        source_path=source_path,
        is_valid=is_valid,
        errors=tuple(errors),
        warnings=tuple(warnings),
        row_count=data_row_count,
        valid_row_count=valid_row_count,
        duplicate_mapping_count=duplicate_mapping_count,
        conflicting_mapping_count=conflicting_mapping_count,
        missing_required_id_count=missing_required_id_count,
        sample_identity_count=sample_identity_count,
        mlbam_batter_count=len(mlbam_batters),
        retrosheet_batter_count=len(retrosheet_batters),
        mlbam_game_count=len(mlbam_games),
        retrosheet_game_count=len(retrosheet_games),
    )


__all__ = [
    "MLB_HR_CROSSWALK_VERSION",
    "MLB_TEAM_ABBREVIATIONS",
    "RETROSHEET_TO_MLB_TEAM",
    "REQUIRED_CROSSWALK_COLUMNS",
    "MLBHRCrosswalkValidationError",
    "MLBHRCrosswalkValidationResult",
    "validate_mlb_hr_crosswalk_csv",
]
