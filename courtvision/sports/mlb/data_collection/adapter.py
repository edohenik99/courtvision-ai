"""Approved MLB source contracts and v1 raw collection planning."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping

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
    source_url_provider="https://github.com/chadwickbureau/register",
    license_terms_note=(
        "Chadwick Bureau Register only; preserve its published license and attribution."
    ),
    acquisition_method=AcquisitionMethod.SUPPLIED_ARCHIVE,
    allowed_extensions=(".zip", ".csv"),
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
    def materialize(destination: Path) -> tuple[Path, ...]:
        try:
            from pybaseball import statcast
        except ImportError as exc:
            raise CollectionError(
                "--fetch-statcast requires the optional pybaseball package"
            ) from exc
        frame = statcast(
            start_dt=request.start_date.isoformat(),
            end_dt=request.end_date.isoformat(),
        )
        output = destination / (
            f"statcast_{request.start_date.isoformat()}_{request.end_date.isoformat()}.csv"
        )
        frame.to_csv(output, index=False)
        return (output,)

    return materialize


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

        optional_paths = (
            (RETROSHEET, _path_option(options, "retrosheet_path")),
            (
                CHADWICK_REGISTER,
                _path_option(options, "chadwick_register_path", "chadwick_path"),
            ),
        )
        for contract, path in optional_paths:
            if path is None:
                warnings.append(f"{contract.source_name} was not supplied.")
            else:
                planned.append(PlannedSource(contract, input_path=path))

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


__all__ = ["MLB_SOURCE_CONTRACTS", "MLBCollectionAdapter"]
