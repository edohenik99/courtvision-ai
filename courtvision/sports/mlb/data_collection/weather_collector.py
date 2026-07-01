"""Meteostat acquisition keyed by Retrosheet game-log parks and dates."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from itertools import chain
import json
import math
from pathlib import Path
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from courtvision.data_collection.core import CollectionError


DAY_REFERENCE_TIME = time(13, 0)
NIGHT_REFERENCE_TIME = time(19, 0)
WEATHER_WINDOW_HOURS = 2
DEFAULT_MAX_STATION_ATTEMPTS = 5
WEATHER_FILENAME = "mlb_meteostat_hourly.csv"
WEATHER_DIAGNOSTICS_FILENAME = "weather_diagnostics.csv"
WEATHER_MISSING_REPORT_FILENAME = "weather_missing_report.csv"

WEATHER_STATUSES = (
    "weather_found",
    "no_nearby_station",
    "no_hourly_rows",
    "indoor_or_roofed",
    "invalid_coordinates",
    "timezone_error",
    "provider_error",
)
ROOF_TYPES = frozenset({"open", "retractable", "fixed_roof", "dome", "unknown"})
INDOOR_ROOF_TYPES = frozenset({"fixed_roof", "dome"})

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

WEATHER_DIAGNOSTIC_COLUMNS = (
    "game_id",
    "game_date",
    "park_id",
    "stadium_name",
    "latitude",
    "longitude",
    "timezone",
    "local_lookup_time",
    "utc_lookup_time",
    "query_window_start",
    "query_window_end",
    "nearest_station_id",
    "nearest_station_name",
    "station_distance_km",
    "stations_found_count",
    "stations_attempted_count",
    "attempted_station_ids",
    "selected_station_id",
    "selected_station_name",
    "selected_station_distance_km",
    "hourly_rows_found_count",
    "status",
)

_MAP_ALIASES = {
    "park_id": ("park_id", "retro_id", "site", "park"),
    "stadium_name": ("stadium_name", "venue_name", "park_name", "name"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "timezone": ("timezone", "time_zone", "tz"),
    "elevation": ("elevation", "elevation_m", "altitude"),
    "roof_type": ("roof_type", "roof"),
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
    latitude: float | str | None
    longitude: float | str | None
    timezone_name: str
    elevation_m: int | None = None
    roof_type: str = "unknown"


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
            if not values["park_id"]:
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} is missing park_id"
                )
            park_id = values["park_id"].upper()
            if park_id in locations:
                raise CollectionError(
                    f"Meteostat weather blocker: duplicate stadium mapping for {park_id}"
                )
            try:
                elevation = (
                    int(round(float(values["elevation"])))
                    if values["elevation"]
                    else None
                )
            except ValueError as exc:
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} has invalid elevation"
                ) from exc
            try:
                latitude: float | str | None = (
                    float(values["latitude"]) if values["latitude"] else None
                )
            except ValueError:
                latitude = values["latitude"]
            try:
                longitude: float | str | None = (
                    float(values["longitude"]) if values["longitude"] else None
                )
            except ValueError:
                longitude = values["longitude"]
            roof_type = (values["roof_type"] or "unknown").lower()
            if roof_type not in ROOF_TYPES:
                raise CollectionError(
                    f"Meteostat weather blocker: stadium map row {row_number} has invalid "
                    f"roof_type {roof_type!r}; expected one of {', '.join(sorted(ROOF_TYPES))}"
                )
            locations[park_id] = StadiumLocation(
                park_id=park_id,
                stadium_name=values["stadium_name"] or park_id,
                latitude=latitude,
                longitude=longitude,
                timezone_name=values["timezone"],
                elevation_m=elevation,
                roof_type=roof_type,
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


def _meteostat_module():
    try:
        import meteostat as ms
    except ImportError as exc:
        raise CollectionError(
            "Meteostat weather blocker: install the collector-weather dependency group"
        ) from exc
    return ms


def _fetch_nearby_stations(location: StadiumLocation):
    ms = _meteostat_module()
    point = ms.Point(location.latitude, location.longitude, location.elevation_m)
    stations = getattr(ms, "stations", None)
    if stations is not None:
        result = stations.nearby(point)
    else:
        stations_type = getattr(ms, "Stations", None)
        if stations_type is None:
            raise CollectionError(
                "Meteostat weather blocker: installed Meteostat package has no station API"
            )
        result = stations_type().nearby(location.latitude, location.longitude)
    fetch = getattr(result, "fetch", None)
    return fetch() if callable(fetch) else result


def _fetch_meteostat_hourly(
    station_id: str, location: StadiumLocation, start: datetime, end: datetime
):
    ms = _meteostat_module()
    hourly = getattr(ms, "hourly", None) or getattr(ms, "Hourly", None)
    if hourly is None:
        raise CollectionError(
            "Meteostat weather blocker: installed Meteostat package has no hourly API"
        )
    result = hourly(
        station_id,
        start.replace(tzinfo=None),
        end.replace(tzinfo=None),
        timezone=location.timezone_name,
    )
    fetch = getattr(result, "fetch", None)
    return fetch() if callable(fetch) else result


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


def _valid_coordinates(location: StadiumLocation) -> bool:
    return (
        isinstance(location.latitude, (int, float))
        and not isinstance(location.latitude, bool)
        and math.isfinite(float(location.latitude))
        and -90 <= float(location.latitude) <= 90
        and isinstance(location.longitude, (int, float))
        and not isinstance(location.longitude, bool)
        and math.isfinite(float(location.longitude))
        and -180 <= float(location.longitude) <= 180
    )


def _station_candidates(frame: object) -> tuple[tuple[str, str, object], ...]:
    if frame is None or getattr(frame, "empty", True):
        return ()
    candidates: list[tuple[str, str, object, float, int]] = []
    for position, (index, series) in enumerate(frame.iterrows()):  # type: ignore[union-attr]
        row = series.to_dict()
        index_id = index[0] if isinstance(index, tuple) else index
        station_id = str(_csv_value(row.get("id") or index_id))
        distance_km: object = ""
        if _csv_value(row.get("distance_km")) != "":
            distance_km = _csv_value(row.get("distance_km"))
        elif _csv_value(row.get("distance")) != "":
            distance_km = round(float(row["distance"]) / 1000, 3)
        sort_distance = (
            float(distance_km) if distance_km != "" else math.inf
        )
        candidates.append(
            (
                station_id,
                str(_csv_value(row.get("name"))),
                distance_km,
                sort_distance,
                position,
            )
        )
    candidates.sort(key=lambda item: (item[3], item[4]))
    return tuple((item[0], item[1], item[2]) for item in candidates)


def _diagnostic_base(
    game: RetrosheetGame, location: StadiumLocation
) -> dict[str, object]:
    return {
        "game_id": game.game_id,
        "game_date": game.game_date.isoformat(),
        "park_id": game.park_id,
        "stadium_name": location.stadium_name,
        "latitude": _csv_value(location.latitude),
        "longitude": _csv_value(location.longitude),
        "timezone": location.timezone_name,
        "local_lookup_time": "",
        "utc_lookup_time": "",
        "query_window_start": "",
        "query_window_end": "",
        "nearest_station_id": "",
        "nearest_station_name": "",
        "station_distance_km": "",
        "stations_found_count": 0,
        "stations_attempted_count": 0,
        "attempted_station_ids": "[]",
        "selected_station_id": "",
        "selected_station_name": "",
        "selected_station_distance_km": "",
        "hourly_rows_found_count": 0,
        "status": "provider_error",
    }


@dataclass(slots=True)
class MeteostatWeatherCollector:
    games: tuple[RetrosheetGame, ...]
    locations: Mapping[str, StadiumLocation]
    max_station_attempts: int = DEFAULT_MAX_STATION_ATTEMPTS
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, object]] = field(default_factory=list)
    unavailable_field_counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_station_attempts, bool)
            or not isinstance(self.max_station_attempts, int)
            or self.max_station_attempts < 1
        ):
            raise ValueError("max_station_attempts must be a positive integer")

    def materialize(self, destination: Path) -> tuple[Path, ...]:
        missing = missing_stadium_park_ids(self.games, self.locations)
        if missing:
            raise CollectionError(
                "Meteostat weather blocker: missing stadium mapping for Retrosheet park ID(s): "
                + ", ".join(missing)
            )
        output = destination / WEATHER_FILENAME
        diagnostics_output = destination / WEATHER_DIAGNOSTICS_FILENAME
        missing_output = destination / WEATHER_MISSING_REPORT_FILENAME
        created = (output, diagnostics_output, missing_output)
        self.warnings.clear()
        self.diagnostics.clear()
        self.unavailable_field_counts.clear()
        try:
            with (
                output.open("x", encoding="utf-8", newline="") as weather_handle,
                diagnostics_output.open(
                    "x", encoding="utf-8", newline=""
                ) as diagnostics_handle,
                missing_output.open("x", encoding="utf-8", newline="") as missing_handle,
            ):
                writer = csv.DictWriter(
                    weather_handle, fieldnames=WEATHER_COLUMNS, lineterminator="\n"
                )
                diagnostics_writer = csv.DictWriter(
                    diagnostics_handle,
                    fieldnames=WEATHER_DIAGNOSTIC_COLUMNS,
                    lineterminator="\n",
                )
                missing_writer = csv.DictWriter(
                    missing_handle,
                    fieldnames=WEATHER_DIAGNOSTIC_COLUMNS,
                    lineterminator="\n",
                )
                writer.writeheader()
                diagnostics_writer.writeheader()
                missing_writer.writeheader()
                used_reference_defaults = False
                for game in self.games:
                    location = self.locations[game.park_id]
                    diagnostic = _diagnostic_base(game, location)
                    if not _valid_coordinates(location):
                        diagnostic["status"] = "invalid_coordinates"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    try:
                        game_time, basis = _reference_time(game, location)
                    except (ZoneInfoNotFoundError, ValueError):
                        diagnostic["status"] = "timezone_error"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    used_reference_defaults |= game.start_time is None
                    window_start = game_time - timedelta(hours=WEATHER_WINDOW_HOURS)
                    window_end = game_time + timedelta(hours=WEATHER_WINDOW_HOURS)
                    diagnostic.update(
                        {
                            "local_lookup_time": game_time.isoformat(),
                            "utc_lookup_time": game_time.astimezone(
                                timezone.utc
                            ).isoformat(),
                            "query_window_start": window_start.isoformat(),
                            "query_window_end": window_end.isoformat(),
                        }
                    )
                    if location.roof_type in INDOOR_ROOF_TYPES:
                        diagnostic["status"] = "indoor_or_roofed"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    try:
                        stations = _fetch_nearby_stations(location)
                        station_candidates = _station_candidates(stations)
                        station_count = len(station_candidates)
                        nearest = (
                            station_candidates[0]
                            if station_candidates
                            else ("", "", "")
                        )
                        diagnostic.update(
                            {
                                "nearest_station_id": nearest[0],
                                "nearest_station_name": nearest[1],
                                "station_distance_km": nearest[2],
                                "stations_found_count": station_count,
                            }
                        )
                        if not station_count:
                            diagnostic["status"] = "no_nearby_station"
                            self._record_diagnostic(
                                diagnostic, diagnostics_writer, missing_writer
                            )
                            continue
                        attempted_station_ids: list[str] = []
                        frame = None
                        station_id = ""
                        for candidate_id, candidate_name, distance_km in station_candidates[
                            : self.max_station_attempts
                        ]:
                            attempted_station_ids.append(candidate_id)
                            diagnostic.update(
                                {
                                    "stations_attempted_count": len(
                                        attempted_station_ids
                                    ),
                                    "attempted_station_ids": json.dumps(
                                        attempted_station_ids, separators=(",", ":")
                                    ),
                                }
                            )
                            candidate_frame = _fetch_meteostat_hourly(
                                candidate_id, location, window_start, window_end
                            )
                            if candidate_frame is None or getattr(
                                candidate_frame, "empty", True
                            ):
                                continue
                            frame = candidate_frame
                            station_id = candidate_id
                            diagnostic.update(
                                {
                                    "selected_station_id": candidate_id,
                                    "selected_station_name": candidate_name,
                                    "selected_station_distance_km": distance_km,
                                }
                            )
                            break
                    except Exception:
                        diagnostic["status"] = "provider_error"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    if frame is None or getattr(frame, "empty", True):
                        diagnostic["status"] = "no_hourly_rows"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    diagnostic["hourly_rows_found_count"] = len(frame)
                    columns = set(getattr(frame, "columns", ()))
                    parameter_map = {
                        "temperature": "temp",
                        "humidity": "rhum",
                        "wind_speed": "wspd",
                        "wind_direction": "wdir",
                        "precipitation": "prcp",
                        "pressure": "pres",
                    }
                    unavailable = [
                        name
                        for name, code in parameter_map.items()
                        if code not in columns
                    ]
                    self.unavailable_field_counts.update(unavailable)
                    weather_rows: list[dict[str, object]] = []
                    malformed = False
                    for index, series in frame.iterrows():
                        row = series.to_dict()
                        observed = _observation_datetime(index, row)
                        if observed is None:
                            malformed = True
                            break
                        local_zone = ZoneInfo(location.timezone_name)
                        local_observed = (
                            observed.replace(tzinfo=local_zone)
                            if observed.tzinfo is None
                            else observed.astimezone(local_zone)
                        )
                        observed_station_id = (
                            index[0] if isinstance(index, tuple) and index else station_id
                        )
                        weather_rows.append(
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
                                "observation_time_utc": local_observed.astimezone(
                                    timezone.utc
                                ).isoformat(),
                                "meteostat_station_id": observed_station_id,
                                **{
                                    name: _csv_value(row.get(code))
                                    for name, code in parameter_map.items()
                                },
                            }
                        )
                    if malformed:
                        diagnostic["status"] = "provider_error"
                        self._record_diagnostic(
                            diagnostic, diagnostics_writer, missing_writer
                        )
                        continue
                    writer.writerows(weather_rows)
                    diagnostic["status"] = "weather_found"
                    self._record_diagnostic(diagnostic, diagnostics_writer, missing_writer)
                if used_reference_defaults:
                    self.warnings.append(
                        "Retrosheet legacy game logs lack exact start times; day games used 13:00 local and night/unknown games used 19:00 local as weather-window references."
                    )
                self.warnings.append(self.summary_warning())
                if self.unavailable_field_counts:
                    unavailable_summary = ", ".join(
                        f"{name}={count}"
                        for name, count in sorted(self.unavailable_field_counts.items())
                    )
                    self.warnings.append(
                        "Meteostat field availability summary: " + unavailable_summary + "."
                    )
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return created

    def _record_diagnostic(
        self,
        diagnostic: dict[str, object],
        diagnostics_writer: csv.DictWriter,
        missing_writer: csv.DictWriter,
    ) -> None:
        if diagnostic["status"] not in WEATHER_STATUSES:
            raise CollectionError(
                f"invalid weather diagnostic status: {diagnostic['status']}"
            )
        row = dict(diagnostic)
        self.diagnostics.append(row)
        diagnostics_writer.writerow(row)
        if row["status"] != "weather_found":
            missing_writer.writerow(row)

    def summary(self) -> dict[str, object]:
        status_counts = Counter(str(row["status"]) for row in self.diagnostics)
        missing = [
            row for row in self.diagnostics if row["status"] != "weather_found"
        ]
        park_counts = Counter(str(row["park_id"]) for row in missing)
        processed = len(self.diagnostics)
        station_attempts_total = sum(
            int(row.get("stations_attempted_count") or 0)
            for row in self.diagnostics
        )
        fallback_station_selected_count = sum(
            1
            for row in self.diagnostics
            if row["status"] == "weather_found"
            and int(row.get("stations_attempted_count") or 0) > 1
        )
        return {
            "games_processed": processed,
            "max_station_attempts": self.max_station_attempts,
            "station_attempts_total": station_attempts_total,
            "fallback_station_selected_count": fallback_station_selected_count,
            "weather_found": status_counts["weather_found"],
            "missing_weather": len(missing),
            "missing_weather_rate": (
                round(len(missing) / processed, 6) if processed else 0.0
            ),
            "missing_by_reason": {
                status: status_counts[status]
                for status in WEATHER_STATUSES
                if status != "weather_found" and status_counts[status]
            },
            "top_missing_park_ids": dict(
                sorted(park_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
            ),
            "indoor_or_roofed_skipped_count": status_counts["indoor_or_roofed"],
        }

    def summary_warning(self) -> str:
        summary = self.summary()
        reasons = summary["missing_by_reason"]
        parks = summary["top_missing_park_ids"]
        reason_text = (
            ", ".join(f"{key}={value}" for key, value in reasons.items()) or "none"
        )
        park_text = (
            ", ".join(f"{key}={value}" for key, value in parks.items()) or "none"
        )
        return (
            "Weather summary: "
            f"games processed={summary['games_processed']}; "
            f"weather found={summary['weather_found']}; "
            f"missing weather={summary['missing_weather']}; "
            f"missing by reason={reason_text}; "
            f"top missing park IDs={park_text}; "
            f"indoor/roofed skipped={summary['indoor_or_roofed_skipped_count']}."
        )

    def _load_existing_diagnostics(self, source_dir: Path) -> None:
        if self.diagnostics:
            return
        path = source_dir / WEATHER_DIAGNOSTICS_FILENAME
        if not path.is_file():
            return
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            self.diagnostics.extend(dict(row) for row in csv.DictReader(handle))

    def manifest_warnings(self, source_dir: Path) -> tuple[str, ...]:
        self._load_existing_diagnostics(source_dir)
        if not self.warnings and self.diagnostics:
            diagnostics_by_game = {
                str(row["game_id"]): row for row in self.diagnostics
            }
            if any(
                game.start_time is None
                and diagnostics_by_game.get(game.game_id, {}).get("local_lookup_time")
                for game in self.games
            ):
                self.warnings.append(
                    "Retrosheet legacy game logs lack exact start times; day games used "
                    "13:00 local and night/unknown games used 19:00 local as "
                    "weather-window references."
                )
            self.warnings.append(self.summary_warning())
        return tuple(dict.fromkeys(self.warnings))

    def manifest_metadata(self, source_dir: Path) -> Mapping[str, object]:
        from courtvision.data_collection.manifest import sha256_file

        self._load_existing_diagnostics(source_dir)
        summary = self.summary()
        return {
            "weather_diagnostics_file": WEATHER_DIAGNOSTICS_FILENAME,
            "weather_diagnostics_sha256": sha256_file(
                source_dir / WEATHER_DIAGNOSTICS_FILENAME
            ),
            "weather_missing_report_file": WEATHER_MISSING_REPORT_FILENAME,
            "missing_weather_count": summary["missing_weather"],
            "missing_weather_rate": summary["missing_weather_rate"],
            "reason_counts": summary["missing_by_reason"],
            "weather_summary": summary,
        }


__all__ = [
    "DAY_REFERENCE_TIME",
    "DEFAULT_MAX_STATION_ATTEMPTS",
    "MeteostatWeatherCollector",
    "NIGHT_REFERENCE_TIME",
    "RetrosheetGame",
    "StadiumLocation",
    "WEATHER_FILENAME",
    "WEATHER_DIAGNOSTICS_FILENAME",
    "WEATHER_MISSING_REPORT_FILENAME",
    "WEATHER_STATUSES",
    "WEATHER_WINDOW_HOURS",
    "load_retrosheet_games",
    "load_stadium_map",
    "missing_stadium_park_ids",
]
