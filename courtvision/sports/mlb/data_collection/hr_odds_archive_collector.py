"""Strict, local-only MLB historical home-run odds archive collection.

The collector validates caller-supplied CSV bytes and local Retrosheet/
Chadwick identity evidence only.  It has no network, scraping, prediction,
expected-value, staking, or bankroll behavior.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import io
import json
from pathlib import Path, PurePosixPath
import re
from typing import Iterable, Mapping, Sequence
import zipfile

from courtvision.data_collection.core import CollectionError
from courtvision.data_collection.manifest import COLLECTOR_VERSION, sha256_file
from courtvision.data_collection.source_contracts import reject_disallowed_source


HR_ODDS_SCHEMA_VERSION = "mlb-historical-hr-odds-v1"
NORMALIZED_HR_ODDS_FILENAME = "normalized_hr_odds.csv"
ODDS_VALIDATION_REPORT_FILENAME = "odds_validation_report.json"
REQUIRED_ODDS_ARCHIVE_COLUMNS = (
    "season",
    "game_id",
    "game_date",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "sportsbook",
    "market",
    "line",
    "odds_american",
    "odds_decimal",
    "over_under",
    "collected_at",
    "event_start_time",
    "source_name",
    "source_license",
)
_DUPLICATE_FIELDS = (
    "sportsbook",
    "player_id",
    "game_id",
    "market",
    "line",
    "over_under",
    "collected_at",
)
_HR_MARKET_ALIASES = frozenset(
    {
        "home run",
        "home runs",
        "home_run",
        "home_runs",
        "hr",
        "player home run",
        "player home runs",
        "player_home_run",
        "player_home_runs",
        "player to hit a home run",
        "to hit a home run",
    }
)
_SPORTSBOOKS = {
    "bet365": "bet365",
    "betmgm": "BetMGM",
    "betrivers": "BetRivers",
    "caesars": "Caesars",
    "caesarssportsbook": "Caesars",
    "draftkings": "DraftKings",
    "espnbet": "ESPN BET",
    "fanatics": "Fanatics Sportsbook",
    "fanaticssportsbook": "Fanatics Sportsbook",
    "fanduel": "FanDuel",
    "hardrockbet": "Hard Rock Bet",
    "pinnacle": "Pinnacle",
    "pointsbet": "PointsBet",
}
APPROVED_SPORTSBOOKS = frozenset(_SPORTSBOOKS.values())
_IDENTITY_COLUMNS = frozenset(
    {
        "key_person",
        "key_mlbam",
        "key_retro",
        "mlbam_batter_id",
        "player_id",
        "retrosheet_batter_id",
    }
)


class HROddsArchiveCollectionError(CollectionError):
    """Raised when an approved supplied archive fails closed validation."""


@dataclass(frozen=True, slots=True)
class RetrosheetGameIdentity:
    game_id: str
    game_date: date


@dataclass(frozen=True, slots=True)
class NormalizedHROddsRow:
    season: int
    game_id: str
    game_date: date
    player_id: str
    player_name: str
    team: str
    opponent: str
    sportsbook: str
    market: str
    line: Decimal
    odds_american: int
    odds_decimal: Decimal
    over_under: str
    collected_at: datetime
    event_start_time: datetime
    source_name: str
    source_license: str

    def as_csv_row(self) -> dict[str, object]:
        return {
            "season": self.season,
            "game_id": self.game_id,
            "game_date": self.game_date.isoformat(),
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team": self.team,
            "opponent": self.opponent,
            "sportsbook": self.sportsbook,
            "market": self.market,
            "line": _decimal_text(self.line),
            "odds_american": self.odds_american,
            "odds_decimal": _decimal_text(self.odds_decimal),
            "over_under": self.over_under,
            "collected_at": self.collected_at.isoformat(),
            "event_start_time": self.event_start_time.isoformat(),
            "source_name": self.source_name,
            "source_license": self.source_license,
        }


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if "." in text else f"{text}.0"


def _headers(fieldnames: Sequence[str] | None, *, label: str) -> tuple[str, ...]:
    if fieldnames is None:
        raise HROddsArchiveCollectionError(f"{label} has no header")
    normalized = tuple(str(name).strip().lower() for name in fieldnames)
    duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
    if duplicates:
        raise HROddsArchiveCollectionError(
            f"{label} has duplicate columns: " + ", ".join(duplicates)
        )
    return normalized


def _required(row: Mapping[str, str], field: str, row_number: int) -> str:
    value = row.get(field, "").strip()
    if not value:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: {field} must not be blank"
        )
    return value


def _parse_decimal(value: str, *, field: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: {field} must be numeric"
        ) from exc
    if not parsed.is_finite():
        raise HROddsArchiveCollectionError(
            f"row {row_number}: {field} must be finite"
        )
    return parsed


def _parse_datetime(value: str, *, field: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: {field} must be an ISO datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: {field} must include a UTC offset"
        )
    return parsed


def _market(value: str, *, row_number: int) -> str:
    normalized = re.sub(r"[\s_\-/]+", " ", value.casefold()).strip()
    if normalized not in {alias.replace("_", " ") for alias in _HR_MARKET_ALIASES}:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: market must be an approved HR market: {value!r}"
        )
    return "player_home_runs"


def _sportsbook(value: str, *, row_number: int) -> str:
    key = re.sub(r"[^a-z0-9]+", "", value.casefold())
    canonical = _SPORTSBOOKS.get(key)
    if canonical is None:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: unknown sportsbook {value!r}; not in the "
            "approved allowlist"
        )
    return canonical


def _normalize_row(
    row: Mapping[str, str],
    *,
    row_number: int,
    requested_season: int,
    games: Mapping[str, RetrosheetGameIdentity],
) -> NormalizedHROddsRow:
    season_text = _required(row, "season", row_number)
    try:
        season = int(season_text)
    except ValueError as exc:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: season must be an integer"
        ) from exc
    if str(season) != season_text or season != requested_season:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: season {season_text!r} does not match requested season "
            f"{requested_season}"
        )

    date_text = _required(row, "game_date", row_number)
    try:
        game_date = date.fromisoformat(date_text)
    except ValueError as exc:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: game_date must use YYYY-MM-DD"
        ) from exc
    if game_date.year != requested_season:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: game_date {game_date} does not match requested season "
            f"{requested_season}"
        )

    game_id = _required(row, "game_id", row_number)
    game = games.get(game_id)
    if game is None:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: unknown game_id {game_id!r}; not present in Retrosheet"
        )
    if game.game_date != game_date:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: game_date {game_date} does not match Retrosheet date "
            f"{game.game_date} for {game_id}"
        )

    line = _parse_decimal(
        _required(row, "line", row_number), field="line", row_number=row_number
    )
    american_text = _required(row, "odds_american", row_number)
    try:
        american_decimal = Decimal(american_text)
    except InvalidOperation as exc:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: odds_american must be numeric"
        ) from exc
    if (
        not american_decimal.is_finite()
        or american_decimal != american_decimal.to_integral_value()
    ):
        raise HROddsArchiveCollectionError(
            f"row {row_number}: odds_american must be a finite integer"
        )
    american = int(american_decimal)
    if -100 < american < 100:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: odds_american must be at least +100 or at most -100"
        )
    decimal_odds = _parse_decimal(
        _required(row, "odds_decimal", row_number),
        field="odds_decimal",
        row_number=row_number,
    )
    if decimal_odds <= 1:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: odds_decimal must be greater than 1"
        )

    over_under = _required(row, "over_under", row_number).upper()
    if over_under not in {"OVER", "UNDER"}:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: over_under must be OVER or UNDER"
        )
    collected_at = _parse_datetime(
        _required(row, "collected_at", row_number),
        field="collected_at",
        row_number=row_number,
    )
    event_start = _parse_datetime(
        _required(row, "event_start_time", row_number),
        field="event_start_time",
        row_number=row_number,
    )
    if collected_at >= event_start:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: collected_at must be before event_start_time"
        )

    team = _required(row, "team", row_number).upper()
    opponent = _required(row, "opponent", row_number).upper()
    if team == opponent:
        raise HROddsArchiveCollectionError(
            f"row {row_number}: team and opponent must differ"
        )
    source_name = _required(row, "source_name", row_number)
    reject_disallowed_source(source_name)
    return NormalizedHROddsRow(
        season=season,
        game_id=game_id,
        game_date=game_date,
        player_id=_required(row, "player_id", row_number),
        player_name=" ".join(_required(row, "player_name", row_number).split()),
        team=team,
        opponent=opponent,
        sportsbook=_sportsbook(
            _required(row, "sportsbook", row_number), row_number=row_number
        ),
        market=_market(_required(row, "market", row_number), row_number=row_number),
        line=line,
        odds_american=american,
        odds_decimal=decimal_odds,
        over_under=over_under,
        collected_at=collected_at,
        event_start_time=event_start,
        source_name=source_name,
        source_license=_required(row, "source_license", row_number),
    )


def _reader_rows(text: io.TextIOBase) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    reader = csv.DictReader(text)
    headers = _headers(reader.fieldnames, label="CSV")
    rows: list[dict[str, str]] = []
    for raw in reader:
        if None in raw:
            raise HROddsArchiveCollectionError("CSV contains extra values")
        row = {
            str(key).strip().lower(): "" if value is None else value.strip()
            for key, value in raw.items()
        }
        if any(row.values()):
            rows.append(row)
    return headers, rows


def _retrosheet_rows_from_text(
    text: str, *, label: str
) -> list[RetrosheetGameIdentity]:
    handle = io.StringIO(text, newline="")
    reader = csv.reader(handle)
    try:
        first = next(reader)
    except StopIteration:
        return []
    normalized = [cell.strip().lower() for cell in first]
    games: list[RetrosheetGameIdentity] = []
    if "game_id" in normalized and "game_date" in normalized:
        dict_reader = csv.DictReader(handle, fieldnames=first)
        for row_number, row in enumerate(dict_reader, start=2):
            mapped = {str(key).strip().lower(): value for key, value in row.items()}
            game_id = str(mapped.get("game_id") or "").strip()
            date_text = str(mapped.get("game_date") or "").strip()
            if not game_id or not date_text:
                continue
            try:
                games.append(
                    RetrosheetGameIdentity(game_id, date.fromisoformat(date_text))
                )
            except ValueError as exc:
                raise HROddsArchiveCollectionError(
                    f"Retrosheet {label} row {row_number}: invalid game_date "
                    f"{date_text!r}"
                ) from exc
        return games

    for row_number, row in enumerate([first, *reader], start=1):
        if not row:
            continue
        if len(row) < 7:
            return []
        try:
            game_date = datetime.strptime(row[0].strip(), "%Y%m%d").date()
        except ValueError:
            return []
        marker = row[1].strip() or "0"
        home_team = row[6].strip().upper()
        games.append(
            RetrosheetGameIdentity(f"{home_team}{game_date:%Y%m%d}{marker}", game_date)
        )
    return games


def _local_text_members(path: Path) -> Iterable[tuple[str, str]]:
    if path.is_file() and path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                for name in sorted(archive.namelist()):
                    if PurePosixPath(name).suffix.lower() not in {".csv", ".txt"}:
                        continue
                    yield name, archive.read(name).decode("utf-8-sig")
        except (zipfile.BadZipFile, UnicodeError, OSError) as exc:
            raise HROddsArchiveCollectionError(
                f"could not read local archive {path}: {exc}"
            ) from exc
        return
    candidates = [path] if path.is_file() else sorted(path.rglob("*"))
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in {".csv", ".txt"}:
            try:
                yield str(candidate), candidate.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise HROddsArchiveCollectionError(
                    f"could not read local reference file {candidate}: {exc}"
                ) from exc


def _load_retrosheet_games(
    path: Path, requested_season: int
) -> dict[str, RetrosheetGameIdentity]:
    if not path.exists() or not (path.is_file() or path.is_dir()):
        raise HROddsArchiveCollectionError(
            f"Retrosheet collection does not exist: {path}"
        )
    games: dict[str, RetrosheetGameIdentity] = {}
    for label, text in _local_text_members(path):
        for game in _retrosheet_rows_from_text(text, label=label):
            if game.game_date.year != requested_season:
                continue
            previous = games.setdefault(game.game_id, game)
            if previous.game_date != game.game_date:
                raise HROddsArchiveCollectionError(
                    f"Retrosheet collection has conflicting dates for {game.game_id}"
                )
    if not games:
        raise HROddsArchiveCollectionError(
            f"Retrosheet collection has no games for season {requested_season}: {path}"
        )
    return games


def _load_player_ids(path: Path | None) -> frozenset[str]:
    if path is None:
        return frozenset()
    if not path.exists() or not (path.is_file() or path.is_dir()):
        raise HROddsArchiveCollectionError(
            f"Chadwick/crosswalk source does not exist: {path}"
        )
    identifiers: set[str] = set()
    for _, text in _local_text_members(path):
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            continue
        relevant = {
            name.strip().lower()
            for name in reader.fieldnames
            if name.strip().lower() in _IDENTITY_COLUMNS
        }
        if not relevant:
            continue
        for raw in reader:
            row = {str(key).strip().lower(): value for key, value in raw.items()}
            identifiers.update(
                str(row.get(column) or "").strip()
                for column in relevant
                if str(row.get(column) or "").strip()
            )
    if not identifiers:
        raise HROddsArchiveCollectionError(
            "Chadwick/crosswalk source contains no supported player identifiers: "
            f"{path}"
        )
    return frozenset(identifiers)


@dataclass(frozen=True, slots=True)
class HROddsArchiveCollector:
    """Validated local archive ready to produce two immutable artifacts."""

    source_path: Path
    retrosheet_path: Path
    crosswalk_path: Path | None
    requested_season: int
    rows: tuple[NormalizedHROddsRow, ...]
    source_sha256: str
    coverage_summary: Mapping[str, int | float]
    resolved_player_count: int
    unresolved_player_count: int

    @classmethod
    def validate(
        cls,
        source_path: str | Path,
        retrosheet_path: str | Path,
        *,
        requested_season: int,
        crosswalk_path: str | Path | None = None,
    ) -> HROddsArchiveCollector:
        source = Path(source_path).expanduser().resolve()
        retrosheet = Path(retrosheet_path).expanduser().resolve()
        crosswalk = (
            None
            if crosswalk_path is None
            else Path(crosswalk_path).expanduser().resolve()
        )
        reject_disallowed_source(str(source))
        if not source.is_file() or source.suffix.lower() != ".csv":
            raise HROddsArchiveCollectionError(
                f"odds archive must be one supplied local CSV file: {source}"
            )

        games = _load_retrosheet_games(retrosheet, requested_season)
        player_ids = _load_player_ids(crosswalk)
        try:
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                headers, raw_rows = _reader_rows(handle)
        except HROddsArchiveCollectionError:
            raise
        except (OSError, UnicodeError, csv.Error) as exc:
            raise HROddsArchiveCollectionError(
                f"could not read odds archive {source}: {exc}"
            ) from exc
        missing = [
            name for name in REQUIRED_ODDS_ARCHIVE_COLUMNS if name not in headers
        ]
        if missing:
            raise HROddsArchiveCollectionError(
                "odds archive is missing required columns: " + ", ".join(missing)
            )
        if not raw_rows:
            raise HROddsArchiveCollectionError("odds archive has no data rows")

        rows: list[NormalizedHROddsRow] = []
        seen: dict[tuple[object, ...], int] = {}
        resolved_players: set[str] = set()
        unresolved_players: set[str] = set()
        for row_number, raw in enumerate(raw_rows, start=2):
            row = _normalize_row(
                raw,
                row_number=row_number,
                requested_season=requested_season,
                games=games,
            )
            duplicate_key = tuple(
                row.as_csv_row()[field] for field in _DUPLICATE_FIELDS
            )
            previous = seen.setdefault(duplicate_key, row_number)
            if previous != row_number:
                raise HROddsArchiveCollectionError(
                    f"row {row_number}: duplicate odds row; first seen at row "
                    f"{previous}"
                )
            if player_ids:
                if row.player_id not in player_ids:
                    raise HROddsArchiveCollectionError(
                        f"row {row_number}: player_id {row.player_id!r} does not "
                        "resolve "
                        "through the supplied Chadwick/crosswalk source"
                    )
                resolved_players.add(row.player_id)
            else:
                unresolved_players.add(row.player_id)
            rows.append(row)

        sorted_rows = tuple(
            sorted(
                rows,
                key=lambda item: (
                    item.game_date,
                    item.game_id,
                    item.player_id,
                    item.sportsbook,
                    item.market,
                    item.line,
                    item.over_under,
                    item.collected_at,
                ),
            )
        )
        games_with_odds = len({row.game_id for row in sorted_rows})
        total_games = len(games)
        coverage = {
            "games_with_odds": games_with_odds,
            "player_props_count": len(sorted_rows),
            "sportsbooks_count": len({row.sportsbook for row in sorted_rows}),
            "missing_games_count": total_games - games_with_odds,
            "coverage_rate": round(games_with_odds / total_games, 8),
        }
        return cls(
            source_path=source,
            retrosheet_path=retrosheet,
            crosswalk_path=crosswalk,
            requested_season=requested_season,
            rows=sorted_rows,
            source_sha256=sha256_file(source),
            coverage_summary=coverage,
            resolved_player_count=len(resolved_players),
            unresolved_player_count=len(unresolved_players),
        )

    def _report(self, normalized_sha256: str) -> dict[str, object]:
        return {
            "artifact_type": "mlb_historical_hr_odds_validation_report",
            "collector_version": COLLECTOR_VERSION,
            "coverage_summary": dict(self.coverage_summary),
            "errors": [],
            "normalized_output": {
                "filename": NORMALIZED_HR_ODDS_FILENAME,
                "row_count": len(self.rows),
                "sha256": normalized_sha256,
            },
            "player_resolution": {
                "crosswalk_filename": (
                    None if self.crosswalk_path is None else self.crosswalk_path.name
                ),
                "mode": (
                    "enforced"
                    if self.crosswalk_path is not None
                    else "not_available"
                ),
                "resolved_player_count": self.resolved_player_count,
                "unresolved_player_count": self.unresolved_player_count,
            },
            "provenance": {
                "acquisition_method": "approved_supplied_csv",
                "network_accessed": False,
                "scraping_performed": False,
                "source_filename": self.source_path.name,
                "source_sha256": self.source_sha256,
                "retrosheet_source": self.retrosheet_path.name,
            },
            "requested_season": self.requested_season,
            "row_counts": {
                "normalized": len(self.rows),
                "rejected": 0,
                "source": len(self.rows),
            },
            "schema_version": HR_ODDS_SCHEMA_VERSION,
            "status": "valid",
            "validations": {
                "capture_before_event": "passed",
                "duplicate_rows": "passed",
                "game_ids_against_retrosheet": "passed",
                "hr_markets_only": "passed",
                "numeric_lines_and_odds": "passed",
                "player_ids": (
                    "passed" if self.crosswalk_path is not None else "not_available"
                ),
                "required_columns": "passed",
                "season_consistency": "passed",
                "sportsbook_allowlist": "passed",
            },
            "warnings": (
                []
                if self.crosswalk_path is not None
                else [
                    "Player IDs were not crosswalk-validated because no supplied "
                    "Chadwick/crosswalk source was available."
                ]
            ),
        }

    def materialize(self, destination: Path) -> tuple[Path, ...]:
        if sha256_file(self.source_path) != self.source_sha256:
            raise HROddsArchiveCollectionError(
                "odds archive changed after validation"
            )
        normalized = destination / NORMALIZED_HR_ODDS_FILENAME
        report_path = destination / ODDS_VALIDATION_REPORT_FILENAME
        with normalized.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=REQUIRED_ODDS_ARCHIVE_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(row.as_csv_row() for row in self.rows)
        try:
            report = self._report(sha256_file(normalized))
            with report_path.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(report, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except Exception:
            normalized.unlink(missing_ok=True)
            raise
        return normalized, report_path

    def manifest_metadata(self, source_dir: Path) -> Mapping[str, object]:
        normalized = source_dir / NORMALIZED_HR_ODDS_FILENAME
        report = source_dir / ODDS_VALIDATION_REPORT_FILENAME
        return {
            "collector_version": COLLECTOR_VERSION,
            "coverage_summary": dict(self.coverage_summary),
            "normalized_filename": normalized.name,
            "normalized_file_hash": sha256_file(normalized),
            "normalized_sha256": sha256_file(normalized),
            "row_counts": {
                "normalized": len(self.rows),
                "rejected": 0,
                "source": len(self.rows),
            },
            "schema_version": HR_ODDS_SCHEMA_VERSION,
            "source_filename": self.source_path.name,
            "source_hash": self.source_sha256,
            "source_sha256": self.source_sha256,
            "validation_report_filename": report.name,
            "validation_report_hash": sha256_file(report),
            "validation_report_sha256": sha256_file(report),
        }


__all__ = [
    "APPROVED_SPORTSBOOKS",
    "HR_ODDS_SCHEMA_VERSION",
    "HROddsArchiveCollectionError",
    "HROddsArchiveCollector",
    "NORMALIZED_HR_ODDS_FILENAME",
    "ODDS_VALIDATION_REPORT_FILENAME",
    "REQUIRED_ODDS_ARCHIVE_COLUMNS",
]
