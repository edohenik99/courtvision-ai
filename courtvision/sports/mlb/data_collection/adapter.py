"""Approved MLB source contracts and v1.2 raw collection planning."""

from __future__ import annotations

import csv
from dataclasses import replace
import io
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Mapping
import zipfile

from courtvision.data_collection.core import (
    CollectionError,
    CollectionPlan,
    CollectionRequest,
    PlannedSource,
)
from courtvision.data_collection.source_contracts import (
    AcquisitionMethod,
    SourceContract,
    reject_disallowed_source,
)
from courtvision.sports.mlb.data_collection.statcast_chunked_collector import (
    STATCAST_DEFAULT_CHUNK_SIZE,
    chunk_size_from_str,
    run_chunked_statcast,
)


STATCAST = SourceContract(
    source_name="statcast_pybaseball",
    source_type="public_api",
    source_url_provider="pybaseball / MLB Statcast",
    license_terms_note=(
        "Use through pybaseball for permitted research; review MLB and pybaseball "
        "terms before redistribution."
    ),
    acquisition_method=AcquisitionMethod.PYBASEBALL,
    allowed_extensions=(".csv",),
)
RETROSHEET = SourceContract(
    source_name="retrosheet_official",
    source_type="official_archive",
    source_url_provider="https://www.retrosheet.org/",
    license_terms_note=(
        "Retrosheet official files only; preserve the Retrosheet attribution and "
        "comply with its use notice."
    ),
    acquisition_method=AcquisitionMethod.SUPPLIED_ARCHIVE,
    allowed_extensions=(".zip", ".csv", ".txt", ".eve", ".evn", ".eva", ".ros"),
)
CHADWICK_REGISTER = SourceContract(
    source_name="chadwick_bureau_register",
    source_type="open_data_archive",
    source_url_provider=(
        "https://github.com/chadwickbureau/register/archive/refs/heads/master.zip"
    ),
    license_terms_note=(
        "Chadwick Bureau Register; Open Data Commons Attribution License 1.0. "
        "Preserve Chadwick Bureau attribution."
    ),
    acquisition_method=AcquisitionMethod.OFFICIAL_DOWNLOAD,
    allowed_extensions=(".zip", ".csv"),
)
CHADWICK_REGISTER_URL = CHADWICK_REGISTER.source_url_provider
CHADWICK_REGISTER_FILENAME = "chadwick-register-master.zip"
_CHADWICK_PEOPLE_FILES = frozenset(
    f"people-{suffix}.csv" for suffix in "0123456789abcdef"
)
METEOSTAT_WEATHER = SourceContract(
    source_name="weather_meteostat",
    source_type="weather_api_or_archive",
    source_url_provider="https://meteostat.net/",
    license_terms_note="Meteostat data only; retain attribution and applicable license metadata.",
    acquisition_method=AcquisitionMethod.SUPPLIED_ARCHIVE,
    allowed_extensions=(".csv", ".json", ".jsonl", ".zip"),
)
NOAA_WEATHER = SourceContract(
    source_name="weather_noaa",
    source_type="government_api_or_archive",
    source_url_provider="https://www.noaa.gov/",
    license_terms_note=(
        "NOAA source only; retain dataset provenance and any dataset-specific terms."
    ),
    acquisition_method=AcquisitionMethod.SUPPLIED_ARCHIVE,
    allowed_extensions=(".csv", ".json", ".jsonl", ".zip"),
)
BALLPARK_FACTORS = SourceContract(
    source_name="approved_supplied_ballpark_factors",
    source_type="manual_csv",
    source_url_provider="caller-approved supplied CSV",
    license_terms_note="Permission and license must be documented by the CSV supplier.",
    acquisition_method=AcquisitionMethod.SUPPLIED_FILE,
    required=True,
    allowed_extensions=(".csv",),
)
ODDS_ARCHIVE = SourceContract(
    source_name="approved_supplied_odds",
    source_type="paid_api_or_archive",
    source_url_provider="caller-approved supplied provider/API/archive",
    license_terms_note="Use only under the supplying provider's licensed archive/API terms.",
    acquisition_method=AcquisitionMethod.SUPPLIED_ARCHIVE,
    required=True,
    allowed_extensions=(".csv", ".json", ".jsonl", ".zip"),
)

MLB_SOURCE_CONTRACTS = (
    STATCAST,
    RETROSHEET,
    CHADWICK_REGISTER,
    METEOSTAT_WEATHER,
    NOAA_WEATHER,
    BALLPARK_FACTORS,
    ODDS_ARCHIVE,
)


def _path_option(options: Mapping[str, object], *names: str) -> Path | None:
    for name in names:
        value = options.get(name)
        if value is not None and str(value).strip():
            return Path(str(value))
    return None


def _statcast_materializer(request: CollectionRequest):
    chunk_size_option = request.source_options.get("statcast_chunk_size")
    chunk_size = (
        STATCAST_DEFAULT_CHUNK_SIZE
        if chunk_size_option is None
        else chunk_size_from_str(str(chunk_size_option))
    )

    def materialize(destination: Path) -> tuple[Path, ...]:
        output = destination / (
            f"statcast_{request.start_date.isoformat()}_{request.end_date.isoformat()}.csv"
        )
        merged = run_chunked_statcast(
            request,
            staging_dir=destination,
            output_path=output,
            chunk_size=chunk_size,
            resume=request.resume,
            allow_network=True,
        )
        assert merged is not None
        return (merged,)

    return materialize


def _chadwick_register_row_count(path: Path) -> int:
    """Validate and count the official 16-shard Chadwick player register."""

    try:
        with zipfile.ZipFile(path) as archive:
            members = {
                PurePosixPath(name).name: name
                for name in archive.namelist()
                if re.fullmatch(r"people-[0-9a-f]\.csv", PurePosixPath(name).name)
            }
            missing = sorted(_CHADWICK_PEOPLE_FILES - members.keys())
            if missing:
                raise CollectionError(
                    "Chadwick Bureau register download blocker: archive is missing "
                    + ", ".join(missing)
                )

            rows = 0
            for basename in sorted(_CHADWICK_PEOPLE_FILES):
                with archive.open(members[basename]) as raw_handle:
                    with io.TextIOWrapper(
                        raw_handle, encoding="utf-8-sig", newline=""
                    ) as text_handle:
                        reader = csv.reader(text_handle)
                        try:
                            next(reader)
                        except StopIteration:
                            continue
                        rows += sum(1 for row in reader if row)
            return rows
    except zipfile.BadZipFile as exc:
        raise CollectionError(
            "Chadwick Bureau register download blocker: response is not a valid ZIP archive"
        ) from exc


def _chadwick_register_materializer(destination: Path) -> tuple[Path, ...]:
    """Download the allowlisted official archive without scraping."""

    try:
        import requests
    except ImportError as exc:
        raise CollectionError(
            "Chadwick Bureau register download blocker: the optional requests package "
            "is required"
        ) from exc

    output = destination / CHADWICK_REGISTER_FILENAME
    response = None
    try:
        response = requests.get(
            CHADWICK_REGISTER_URL,
            stream=True,
            timeout=(10, 120),
        )
        response.raise_for_status()
        with output.open("xb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        _chadwick_register_row_count(output)
    except CollectionError:
        output.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise CollectionError(
            f"Chadwick Bureau register download blocker: {exc}"
        ) from exc
    finally:
        if response is not None:
            response.close()
    return (output,)


class MLBCollectionAdapter:
    """MLB v1 adapter: approved raw sources only, with no website scraping."""

    sport = "mlb"
    required_sources = (
        "approved supplied ballpark factors CSV",
        "approved supplied odds provider/API/archive",
    )

    def source_contracts(self) -> tuple[SourceContract, ...]:
        return MLB_SOURCE_CONTRACTS

    def build_plan(self, request: CollectionRequest) -> CollectionPlan:
        options = request.source_options
        planned: list[PlannedSource] = []
        blockers: list[str] = []
        warnings: list[str] = []

        statcast_path = _path_option(options, "statcast_csv", "statcast_path")
        fetch_statcast = bool(options.get("fetch_statcast", False))
        if statcast_path is not None and fetch_statcast:
            raise CollectionError("choose either supplied Statcast CSV or --fetch-statcast")
        if statcast_path is not None:
            planned.append(
                PlannedSource(
                    STATCAST,
                    input_path=statcast_path,
                    warnings=("Supplied file must be an unmodified pybaseball export.",),
                )
            )
        elif fetch_statcast:
            planned.append(
                PlannedSource(
                    STATCAST,
                    materializer=_statcast_materializer(request),
                )
            )
        else:
            warnings.append("Statcast was not requested; no Statcast file will be collected.")

        retrosheet_path = _path_option(options, "retrosheet_path")
        if retrosheet_path is None:
            warnings.append(f"{RETROSHEET.source_name} was not supplied.")
        else:
            planned.append(PlannedSource(RETROSHEET, input_path=retrosheet_path))

        chadwick_path = _path_option(
            options, "chadwick_register_path", "chadwick_path"
        )
        fetch_chadwick = bool(options.get("fetch_chadwick_register", False))
        if chadwick_path is not None and fetch_chadwick:
            raise CollectionError(
                "choose either supplied --chadwick-register-path or "
                "--fetch-chadwick-register"
            )
        if chadwick_path is not None:
            planned.append(PlannedSource(CHADWICK_REGISTER, input_path=chadwick_path))
        elif fetch_chadwick:
            planned.append(
                PlannedSource(
                    CHADWICK_REGISTER,
                    materializer=_chadwick_register_materializer,
                    row_counter=_chadwick_register_row_count,
                )
            )
        else:
            warnings.append(
                f"{CHADWICK_REGISTER.source_name} was neither supplied nor requested."
            )

        weather_path = _path_option(options, "weather_path")
        if weather_path is None:
            warnings.append("Meteostat/NOAA weather data was not supplied.")
        else:
            provider = str(options.get("weather_provider") or "").strip().lower()
            if provider not in {"meteostat", "noaa"}:
                raise CollectionError(
                    "weather_provider must be 'meteostat' or 'noaa' when weather is supplied"
                )
            contract = METEOSTAT_WEATHER if provider == "meteostat" else NOAA_WEATHER
            planned.append(PlannedSource(contract, input_path=weather_path))

        ballpark_path = _path_option(
            options, "ballpark_factors_path", "ballpark_path"
        )
        if ballpark_path is None:
            blockers.append(
                "Missing required approved supplied ballpark factors CSV."
            )
        else:
            planned.append(PlannedSource(BALLPARK_FACTORS, input_path=ballpark_path))

        odds_path = _path_option(options, "odds_archive_path", "odds_path")
        if odds_path is None:
            blockers.append(
                "Missing required approved odds provider/API/archive source."
            )
        else:
            provider = str(
                options.get("odds_provider") or "caller-approved supplied odds archive"
            ).strip()
            reject_disallowed_source(provider)
            odds_contract = replace(
                ODDS_ARCHIVE,
                source_url_provider=provider,
            )
            planned.append(PlannedSource(odds_contract, input_path=odds_path))

        return CollectionPlan(
            sources=tuple(planned),
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )


__all__ = [
    "CHADWICK_REGISTER_FILENAME",
    "CHADWICK_REGISTER_URL",
    "MLB_SOURCE_CONTRACTS",
    "MLBCollectionAdapter",
]
