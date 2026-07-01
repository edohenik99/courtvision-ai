"""Meteostat acquisition keyed by Retrosheet game-log parks and dates."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from itertools import chain
import math
from pathlib import Path
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from courtvision.data_collection.core import CollectionError


DAY_REFERENCE_TIME = time(13, 0)
NIGHT_REFERENCE_TIME = time(19, 0)
WEATHER_WINDOW_HOURS = 2
WEATHER_FILENAME = "mlb_meteostat_hourly.csv"

WEATHER_COLUMNS = (
    "game_id",
    "game_date",
    "park_id",
    "stadium_name",
    "latitude",
    "longitude",
    "timezone",
    "game_time_local",
    "game_time_basis",
    "observation_time_local",
    "observation_time_utc",
    "meteostat_station_id",
    "temperature",
    "humidity",
    "wind_speed",
    "wind_direction",
    "precipitation",
    "pressure",
)

_MAP_ALIASES = {
    "park_id": ("park_id", "retro_id", "site", "park"),
    "stadium_name": ("stadium_name", "venue_name", "park_name", "name"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "timezone": ("timezone", "time_zone", "tz"),
    "elevation": ("elevation", "elevation_m", "altitude"),
}


@dataclass(frozen=True, slots=True)
class RetrosheetGame:
    game_id: str
    game_date: date
    park_id: str
    day_night: str
    start_time: time | None


@dataclass(frozen=True, slots=True)
class StadiumLocation:
    park_id: str
    stadium_name: str
    latitude: float
    longitude: float
    timezone_name: str
    elevation_m: int | None = None


def _first(row: Mapping[str, object], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_game_date(value: str, *, row_number: int) -> date:
    text = value.strip()
    try:
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text.replace("/", "-")[:10])
    except ValueError as exc:
        raise CollectionError(
            f"Retrosheet weather blocker: row {row_number} has invalid game date {text!r}"
        ) from exc


def _parse_start_time(value: str) -> time | None:
    text = value.strip().upper().replace(" ", "")
    if not text or text in {"0:00", "UNKNOWN"}:
        return None
    for pattern in ("%I:%M%p", "%H:%M", "%H%M"):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue
    raise CollectionError(
        f"Retrosheet weather blocker: invalid starttime value {value!r}"
    )


def _headered_game(row: Mapping[str, object], row_number: int) -> RetrosheetGame:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    game_date = _parse_game_date(
        _first(normalized, ("game_date", "date")), row_number=row_number
    )
    park_id = _first(normalized, ("park_id", "site", "park"))
    if not park_id:
        raise CollectionError(
            f"Retrosheet weather blocker: row {row_number} is missing park ID/site"
        )
    home = _first(normalized, ("home_team", "hometeam"))
    number = _first(normalized, ("game_number", "number")) or "0"
    game_id = _first(normalized, ("game_id", "gid")) or (
        f"{home}{game_date:%Y%m%d}{number}"
    )
    return RetrosheetGame(
        game_id=game_id,
        game_date=game_date,
        park_id=park_id.upper(),
        day_night=_first(normalized, ("day_night", "daynight")) or "unknown",
        start_time=_parse_start_time(_first(normalized, ("start_time", "starttime"))),
    )


def _legacy_game(row: list[str], row_number: int) -> RetrosheetGame:
    if len(row) < 17:
        raise CollectionError(
            f"Retrosheet weather blocker: row {row_number} has {len(row)} fields; expected at least 17"
        )
    game_date = _parse_game_date(row[0], row_number=row_number)
    home = row[6].strip().upper()
    number = row[1].strip() or "0"
    park_id = row[16].strip().upper()
    if not park_id:
        raise CollectionError(
            f"Retrosheet weather blocker: row {row_number} is missing park ID"
        )
    return RetrosheetGame(
        game_id=f"{home}{game_date:%Y%m%d}{number}",
        game_date=game_date,
        park_id=park_id,
        day_night=row[12].strip() or "unknown",
        start_time=None,
    )


def load_retrosheet_games(
    path: str | Path, start_date: date, end_date: date
) -> tuple[RetrosheetGame, ...]:
    """Load headerless ``glYYYY`` or headered Retrosheet game-info CSV rows."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise CollectionError(
            f"Retrosheet weather blocker: game log must be a file: {source}"
        )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            first_row = next(reader)
        except StopIteration as exc:
            raise CollectionError(
                f"Retrosheet weather blocker: game log is empty: {source}"
            ) from exc

        first_keys = {cell.strip().lower() for cell in first_row}
        headered = bool(first_keys & {"game_date", "date"}) and bool(
            first_keys & {"park_id", "site", "park"}
        )
        games: list[RetrosheetGame] = []
        if headered:
            dict_reader = csv.DictReader(handle, fieldnames=first_row)
            candidates = (
                _headered_game(row, row_number)
                for row_number, row in enumerate(dict_reader, start=2)
            )
        else:
            candidates = (
                _legacy_game(row, row_number)
                for row_number, row in enumerate(
                    chain((first_row,), reader), start=1
                )
            )
        for game in candidates:
            if start_date <= game.game_date <= end_date:
                games.append(game)
    if not games:
        raise CollectionError(
            "Retrosheet weather blocker: no games fall within the requested date range"
        )
    return tuple(games)


def load_stadium_map(path: str | Path) -> dict[str, StadiumLocation]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise CollectionError(
            f"Meteostat weather blocker: stadium map must be a CSV file: {source}"
        )
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CollectionError("Meteostat weather blocker: stadium map has no header")
        locations: dict[str, StadiumLocation] = {}
        for row_number, raw_row in enumerate(reader, start=2):
            row = {str(key).strip().lower(): value for key, value in raw_row.items()}
            values = {name: _first(row, aliases) for name, aliases in _MAP_ALIASES.items()}
            missing = [
                name
                for name in ("park_id", "latitude", "longitude", "timezone")
                if not values[name]
            ]
            if missing:
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} is missing "
                    + ", ".join(missing)
                )
            park_id = values["park_id"].upper()
            if park_id in locations:
                raise CollectionError(
                    f"Meteostat weather blocker: duplicate stadium mapping for {park_id}"
                )
            try:
                latitude = float(values["latitude"])
                longitude = float(values["longitude"])
                elevation = (
                    int(round(float(values["elevation"])))
                    if values["elevation"]
                    else None
                )
            except ValueError as exc:
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} has invalid coordinates/elevation"
                ) from exc
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} coordinates are out of range"
                )
            try:
                ZoneInfo(values["timezone"])
            except ZoneInfoNotFoundError as exc:
                raise CollectionError(
                    f"Meteostat weather blocker: unknown timezone {values['timezone']!r} for {park_id}"
                ) from exc
            locations[park_id] = StadiumLocation(
                park_id=park_id,
                stadium_name=values["stadium_name"] or park_id,
                latitude=latitude,
                longitude=longitude,
                timezone_name=values["timezone"],
                elevation_m=elevation,
            )
    if not locations:
        raise CollectionError("Meteostat weather blocker: stadium map has no data rows")
    return locations


def missing_stadium_park_ids(
    games: Iterable[RetrosheetGame], locations: Mapping[str, StadiumLocation]
) -> tuple[str, ...]:
    return tuple(sorted({game.park_id for game in games if game.park_id not in locations}))


def _reference_time(game: RetrosheetGame, location: StadiumLocation) -> tuple[datetime, str]:
    if game.start_time is not None:
        reference = game.start_time
        basis = "retrosheet_starttime"
    elif game.day_night.strip().lower() in {"d", "day"}:
        reference = DAY_REFERENCE_TIME
        basis = "retrosheet_day_default_13:00"
    else:
        reference = NIGHT_REFERENCE_TIME
        basis = "retrosheet_night_or_unknown_default_19:00"
    return (
        datetime.combine(game.game_date, reference, ZoneInfo(location.timezone_name)),
        basis,
    )


def _fetch_meteostat_hourly(
    location: StadiumLocation, start: datetime, end: datetime
):
    try:
        import meteostat as ms
    except ImportError as exc:
        raise CollectionError(
            "Meteostat weather blocker: install the collector-weather dependency group"
        ) from exc
    point = ms.Point(location.latitude, location.longitude, location.elevation_m)
    hourly = getattr(ms, "hourly", None) or getattr(ms, "Hourly", None)
    if hourly is None:
        raise CollectionError(
            "Meteostat weather blocker: installed Meteostat package has no hourly API"
        )
    return hourly(
        point,
        start.replace(tzinfo=None),
        end.replace(tzinfo=None),
        timezone=location.timezone_name,
    ).fetch()


def _observation_datetime(index: object, row: Mapping[str, object]) -> datetime | None:
    value = row.get("time")
    candidates = (value, *(index if isinstance(index, tuple) else (index,)))
    for candidate in reversed(candidates):
        if isinstance(candidate, datetime):
            converter = getattr(candidate, "to_pydatetime", None)
            return converter() if callable(converter) else candidate
    return None


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if str(value) in {"<NA>", "NaT", "nan"}:
        return ""
    item = getattr(value, "item", None)
    return item() if callable(item) else value


@dataclass(slots=True)
class MeteostatWeatherCollector:
    games: tuple[RetrosheetGame, ...]
    locations: Mapping[str, StadiumLocation]
    warnings: list[str] = field(default_factory=list)

    def materialize(self, destination: Path) -> tuple[Path, ...]:
        missing = missing_stadium_park_ids(self.games, self.locations)
        if missing:
            raise CollectionError(
                "Meteostat weather blocker: missing stadium mapping for Retrosheet park ID(s): "
                + ", ".join(missing)
            )
        output = destination / WEATHER_FILENAME
        try:
            with output.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=WEATHER_COLUMNS, lineterminator="\n")
                writer.writeheader()
                used_reference_defaults = False
                for game in self.games:
                    location = self.locations[game.park_id]
                    game_time, basis = _reference_time(game, location)
                    used_reference_defaults |= game.start_time is None
                    try:
                        frame = _fetch_meteostat_hourly(
                            location,
                            game_time - timedelta(hours=WEATHER_WINDOW_HOURS),
                            game_time + timedelta(hours=WEATHER_WINDOW_HOURS),
                        )
                    except CollectionError:
                        raise
                    except Exception as exc:
                        raise CollectionError(
                            f"Meteostat weather blocker: fetch failed for {game.game_id} at {game.park_id}: {exc}"
                        ) from exc
                    if frame is None or getattr(frame, "empty", True):
                        self.warnings.append(
                            f"Meteostat returned no hourly rows for {game.game_id} ({game.park_id})."
                        )
                        continue
                    columns = set(getattr(frame, "columns", ()))
                    parameter_map = {
                        "temperature": "temp",
                        "humidity": "rhum",
                        "wind_speed": "wspd",
                        "wind_direction": "wdir",
                        "precipitation": "prcp",
                        "pressure": "pres",
                    }
                    unavailable = [name for name, code in parameter_map.items() if code not in columns]
                    if unavailable:
                        self.warnings.append(
                            f"Meteostat fields unavailable for {game.game_id}: {', '.join(unavailable)}."
                        )
                    for index, series in frame.iterrows():
                        row = series.to_dict()
                        observed = _observation_datetime(index, row)
                        if observed is None:
                            raise CollectionError(
                                f"Meteostat weather blocker: hourly row for {game.game_id} has no observation time"
                            )
                        local_zone = ZoneInfo(location.timezone_name)
                        local_observed = (
                            observed.replace(tzinfo=local_zone)
                            if observed.tzinfo is None
                            else observed.astimezone(local_zone)
                        )
                        station_id = index[0] if isinstance(index, tuple) and index else ""
                        writer.writerow(
                            {
                                "game_id": game.game_id,
                                "game_date": game.game_date.isoformat(),
                                "park_id": game.park_id,
                                "stadium_name": location.stadium_name,
                                "latitude": location.latitude,
                                "longitude": location.longitude,
                                "timezone": location.timezone_name,
                                "game_time_local": game_time.isoformat(),
                                "game_time_basis": basis,
                                "observation_time_local": local_observed.isoformat(),
                                "observation_time_utc": local_observed.astimezone(timezone.utc).isoformat(),
                                "meteostat_station_id": station_id,
                                **{
                                    name: _csv_value(row.get(code))
                                    for name, code in parameter_map.items()
                                },
                            }
                        )
                if used_reference_defaults:
                    self.warnings.append(
                        "Retrosheet legacy game logs lack exact start times; day games used 13:00 local and night/unknown games used 19:00 local as weather-window references."
                    )
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return (output,)

    def manifest_warnings(self, source_dir: Path) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.warnings))


__all__ = [
    "DAY_REFERENCE_TIME",
    "MeteostatWeatherCollector",
    "NIGHT_REFERENCE_TIME",
    "RetrosheetGame",
    "StadiumLocation",
    "WEATHER_FILENAME",
    "WEATHER_WINDOW_HOURS",
    "load_retrosheet_games",
    "load_stadium_map",
    "missing_stadium_park_ids",
]
