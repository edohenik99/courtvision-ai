"""Read-only MLB HR prospective market-movement evidence reporting.

The report consumes only immutable committed prediction evidence, prospective
closing-v2 evidence, and frozen control metadata.  It never captures evidence,
reads outcomes for calculations, or mutates the prospective trial store.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Final

from courtvision.sports.mlb.training import hr_prospective_trial as trial
from courtvision.sports.mlb.training import hr_research_baseline as baseline


REPORT_SCHEMA_VERSION: Final = "mlb-hr-prospective-market-movement-v1"
REPORT_COMMAND: Final = "report-prospective-market-movement"
REPORT_COMMANDS: Final = frozenset({REPORT_COMMAND})

_CAPTURED_STATUS_METHODS: Final = {
    "captured_same_book": "same_book_latest_prestart",
    "captured_consensus": "consensus_latest_prestart",
}
_MISSING_STATUS_METHODS: Final = {
    "missing": "missing",
    "missing_prestart": "missing_prestart_snapshot",
}


def _decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise trial.MLBHRProspectiveTrialError(
            f"{field_name} must be a finite decimal"
        ) from exc
    if not parsed.is_finite():
        raise trial.MLBHRProspectiveTrialError(
            f"{field_name} must be a finite decimal"
        )
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise trial.MLBHRProspectiveTrialError(
            "market movement produced a non-finite decimal"
        )
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _canonical_price_values(american_odds: int) -> tuple[Decimal, Decimal]:
    return (
        _decimal(
            trial._format_float(baseline.american_to_decimal(american_odds)),
            "canonical decimal odds",
        ),
        _decimal(
            trial._format_float(
                baseline.american_to_implied_probability(american_odds)
            ),
            "canonical implied probability",
        ),
    )


def _validate_american_price(
    *,
    american_text: str,
    decimal_text: str,
    implied_text: str,
    description: str,
) -> tuple[int, Decimal, Decimal]:
    try:
        american = int(american_text)
    except ValueError as exc:
        raise trial.MLBHRProspectiveTrialError(
            f"{description} American odds are invalid"
        ) from exc
    if american == 0 or str(american) != american_text:
        raise trial.MLBHRProspectiveTrialError(
            f"{description} American odds are invalid"
        )
    decimal_odds = _decimal(decimal_text, f"{description} decimal odds")
    implied = _decimal(implied_text, f"{description} implied probability")
    expected_decimal, expected_implied = _canonical_price_values(american)
    if (
        decimal_odds != expected_decimal
        or implied != expected_implied
        or decimal_odds <= 1
        or not 0 < implied < 1
    ):
        raise trial.MLBHRProspectiveTrialError(
            f"{description} odds and implied probability are inconsistent"
        )
    return american, decimal_odds, implied


def _stable_file_entry(path: Path, description: str) -> dict[str, object]:
    data = trial._read_stable_bytes(path, description)
    return {
        "name": path.name,
        "sha256": trial._sha256_bytes(data),
        "size_bytes": len(data),
    }


def _ledger_prediction_fingerprint(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False, "prediction_rows_sha256": ""}
    if path.is_symlink() or not path.is_file():
        raise trial.MLBHRProspectiveTrialError(
            "prospective ledger is not a regular file"
        )
    rows = trial._parse_csv_bytes(
        trial._read_stable_bytes(path, "prospective ledger"),
        trial.LEDGER_COLUMNS,
        "prospective ledger",
    )
    prediction_rows = [
        dict(row) for row in rows if row.get("record_type") == "prediction"
    ]
    return {
        "exists": True,
        "prediction_rows_sha256": trial._canonical_sha256(prediction_rows),
    }


def _date_run_membership(control_dir: Path) -> list[dict[str, object]]:
    dates_root = control_dir / "dates"
    if not dates_root.exists():
        return []
    if dates_root.is_symlink() or not dates_root.is_dir():
        raise trial.MLBHRProspectiveTrialError(
            "prospective dates store is not a real directory"
        )
    try:
        date_entries = sorted(
            (entry for entry in dates_root.iterdir() if not entry.name.startswith(".")),
            key=lambda entry: entry.name,
        )
    except OSError as exc:
        raise trial.MLBHRProspectiveTrialError(
            "prospective dates store is inaccessible"
        ) from exc
    output: list[dict[str, object]] = []
    for date_dir in date_entries:
        if date_dir.is_symlink() or not date_dir.is_dir():
            raise trial.MLBHRProspectiveTrialError(
                "prospective dates store contains an invalid entry"
            )
        try:
            run_entries = sorted(
                (
                    entry
                    for entry in date_dir.iterdir()
                    if not entry.name.startswith(".")
                ),
                key=lambda entry: entry.name,
            )
        except OSError as exc:
            raise trial.MLBHRProspectiveTrialError(
                "prospective operating-date store is inaccessible"
            ) from exc
        runs: list[dict[str, object]] = []
        for run_dir in run_entries:
            if run_dir.is_symlink() or not run_dir.is_dir():
                raise trial.MLBHRProspectiveTrialError(
                    "prospective operating-date store contains an invalid entry"
                )
            try:
                artifacts = sorted(run_dir.iterdir(), key=lambda entry: entry.name)
            except OSError as exc:
                raise trial.MLBHRProspectiveTrialError(
                    "prospective prediction run is inaccessible"
                ) from exc
            files: list[dict[str, object]] = []
            for artifact in artifacts:
                if artifact.is_symlink() or not artifact.is_file():
                    raise trial.MLBHRProspectiveTrialError(
                        "prospective prediction run contains an invalid artifact"
                    )
                files.append(
                    _stable_file_entry(
                        artifact,
                        f"prospective run artifact {artifact.name}",
                    )
                )
            runs.append({"prediction_run_id": run_dir.name, "files": files})
        output.append({"operating_date": date_dir.name, "runs": runs})
    return output


def _capture_evidence_snapshot(control_dir: Path) -> dict[str, object]:
    if control_dir.is_symlink() or not control_dir.is_dir():
        raise trial.MLBHRProspectiveTrialError(
            "explicit control directory does not exist"
        )
    try:
        control_membership = sorted(
            (
                {
                    "name": entry.name,
                    "is_symlink": entry.is_symlink(),
                    "kind": (
                        "directory"
                        if entry.is_dir()
                        else "file"
                        if entry.is_file()
                        else "other"
                    ),
                }
                for entry in control_dir.iterdir()
            ),
            key=lambda item: str(item["name"]),
        )
    except OSError as exc:
        raise trial.MLBHRProspectiveTrialError(
            "control directory is inaccessible"
        ) from exc
    manifest_path = control_dir / trial.CONTROL_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise trial.MLBHRProspectiveTrialError("control manifest is missing")
    closing_path = control_dir / "closing_lines.csv"
    closing: dict[str, object]
    if closing_path.exists():
        if closing_path.is_symlink() or not closing_path.is_file():
            raise trial.MLBHRProspectiveTrialError(
                "closing-line evidence is not a regular file"
            )
        closing = {
            "exists": True,
            **_stable_file_entry(closing_path, "closing-line evidence"),
        }
    else:
        closing = {"exists": False}
    return {
        "control_membership": control_membership,
        "control_manifest": _stable_file_entry(
            manifest_path, "control manifest"
        ),
        "prospective_ledger": _ledger_prediction_fingerprint(
            control_dir / "prospective_ledger.csv"
        ),
        "closing_evidence": closing,
        "date_run_membership": _date_run_membership(control_dir),
    }


def _empty_accounting() -> dict[str, Any]:
    return {
        "committed_predictions": 0,
        "comparable_same_book": 0,
        "comparable_consensus": 0,
        "non_comparable_temporal": {
            "total": 0,
            "same_book": 0,
            "consensus": 0,
        },
        "explicit_missing": {
            "total": 0,
            "missing": 0,
            "missing_prestart": 0,
        },
        "predictions_without_closing_record": 0,
        "accounting_invariant_holds": True,
    }


def _increment_accounting(accounting: dict[str, Any], bucket: str) -> None:
    if bucket == "comparable_same_book":
        accounting["comparable_same_book"] += 1
    elif bucket == "comparable_consensus":
        accounting["comparable_consensus"] += 1
    elif bucket == "non_comparable_temporal_same_book":
        accounting["non_comparable_temporal"]["same_book"] += 1
        accounting["non_comparable_temporal"]["total"] += 1
    elif bucket == "non_comparable_temporal_consensus":
        accounting["non_comparable_temporal"]["consensus"] += 1
        accounting["non_comparable_temporal"]["total"] += 1
    elif bucket in _MISSING_STATUS_METHODS:
        accounting["explicit_missing"][bucket] += 1
        accounting["explicit_missing"]["total"] += 1
    elif bucket == "predictions_without_closing_record":
        accounting["predictions_without_closing_record"] += 1
    else:
        raise trial.MLBHRProspectiveTrialError(
            "market movement accounting received an unknown bucket"
        )


def _assert_accounting(accounting: dict[str, Any]) -> None:
    observed = (
        accounting["comparable_same_book"]
        + accounting["comparable_consensus"]
        + accounting["non_comparable_temporal"]["same_book"]
        + accounting["non_comparable_temporal"]["consensus"]
        + accounting["explicit_missing"]["total"]
        + accounting["predictions_without_closing_record"]
    )
    holds = accounting["committed_predictions"] == observed
    accounting["accounting_invariant_holds"] = holds
    if not holds:
        raise trial.MLBHRProspectiveTrialError(
            "prospective market-movement accounting does not reconcile"
        )


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    with localcontext() as context:
        context.prec = 28
        return sum(values, Decimal(0)) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    with localcontext() as context:
        context.prec = 28
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _ratio(numerator: int, denominator: int) -> str | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 28
        return _decimal_text(Decimal(numerator) / Decimal(denominator))


def _stat_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _movement_summary(
    observations: Sequence[Mapping[str, object]],
    *,
    include_decimal: bool,
) -> dict[str, object]:
    directions = [str(row["direction"]) for row in observations]
    implied_deltas = [
        row["implied_probability_delta"]
        for row in observations
        if isinstance(row["implied_probability_delta"], Decimal)
    ]
    count = len(observations)
    output: dict[str, object] = {
        "count": count,
        "shortened_count": directions.count("shortened"),
        "shortened_rate": _ratio(directions.count("shortened"), count),
        "unchanged_count": directions.count("unchanged"),
        "unchanged_rate": _ratio(directions.count("unchanged"), count),
        "lengthened_count": directions.count("lengthened"),
        "lengthened_rate": _ratio(directions.count("lengthened"), count),
        "mean_implied_probability_delta": _stat_text(_mean(implied_deltas)),
        "median_implied_probability_delta": _stat_text(_median(implied_deltas)),
    }
    if include_decimal:
        decimal_deltas = [
            row["decimal_odds_change"]
            for row in observations
            if isinstance(row.get("decimal_odds_change"), Decimal)
        ]
        output.update(
            {
                "mean_decimal_odds_delta": _stat_text(_mean(decimal_deltas)),
                "median_decimal_odds_delta": _stat_text(
                    _median(decimal_deltas)
                ),
            }
        )
    return output


def _validate_closing_record_id(row: Mapping[str, str]) -> None:
    expected = "mlb-hr-closing-v2-" + trial._canonical_sha256(
        {
            "prediction_id": row["prediction_id"],
            "closing_status": row["closing_status"],
            "closing_method": row["closing_method"],
            "closing_snapshot_time": row["closing_snapshot_time_utc"],
            "closing_sportsbook": row["closing_sportsbook"],
            "closing_american_odds": row["closing_american_odds"],
        }
    )[:24]
    if row["closing_record_id"] != expected:
        raise trial.MLBHRProspectiveTrialError(
            "closing_record_id does not match immutable closing content"
        )


def _validate_closing_linkage(
    row: Mapping[str, str],
    prediction: Mapping[str, str],
    *,
    control_id: str,
    control_digest: str,
) -> None:
    required = (
        "closing_record_id",
        "prediction_id",
        "prediction_run_id",
        "control_id",
        "control_manifest_digest",
        "closing_status",
        "closing_method",
        "original_american_odds",
        "original_implied_probability",
        "source_odds_sha256",
        "captured_at_utc",
        "integrity_status",
    )
    if any(not row[field] for field in required):
        raise trial.MLBHRProspectiveTrialError(
            "closing-line evidence is missing a required integrity field"
        )
    if (
        row["prediction_run_id"] != prediction["prediction_run_id"]
        or row["control_id"] != control_id
        or row["control_manifest_digest"] != control_digest
    ):
        raise trial.MLBHRProspectiveTrialError(
            "closing-line evidence has invalid control or run linkage"
        )
    if (
        row["original_american_odds"] != prediction["original_american_odds"]
        or row["original_implied_probability"]
        != prediction["original_implied_probability"]
    ):
        raise trial.MLBHRProspectiveTrialError(
            "closing-line evidence has invalid original-price linkage"
        )
    if not trial._is_sha256(row["source_odds_sha256"]):
        raise trial.MLBHRProspectiveTrialError(
            "closing-line source digest is invalid"
        )
    trial._parse_utc(row["captured_at_utc"], "closing captured_at_utc")
    _validate_closing_record_id(row)


def _validate_missing_row(row: Mapping[str, str]) -> None:
    status = row["closing_status"]
    if row["closing_method"] != _MISSING_STATUS_METHODS[status]:
        raise trial.MLBHRProspectiveTrialError(
            "closing status and method pair is invalid"
        )
    blank_fields = (
        "closing_snapshot_time_utc",
        "closing_sportsbook",
        "closing_sportsbook_name",
        "closing_american_odds",
        "closing_decimal_odds",
        "closing_implied_probability",
        "consensus_implied_probability",
        "closing_line_movement",
        "closing_probability_movement",
    )
    if (
        any(row[field] for field in blank_fields)
        or row["consensus_bookmaker_count"] != "0"
        or row["integrity_status"] != "no_valid_prestart_snapshot"
    ):
        raise trial.MLBHRProspectiveTrialError(
            "explicit-missing closing evidence is malformed"
        )


def _direction(delta: Decimal) -> str:
    if delta > 0:
        return "shortened"
    if delta < 0:
        return "lengthened"
    return "unchanged"


def _validate_captured_row(
    row: Mapping[str, str], prediction: Mapping[str, str]
) -> tuple[str, dict[str, object] | None]:
    status = row["closing_status"]
    if row["closing_method"] != _CAPTURED_STATUS_METHODS[status]:
        raise trial.MLBHRProspectiveTrialError(
            "closing status and method pair is invalid"
        )
    required = (
        "closing_snapshot_time_utc",
        "closing_sportsbook",
        "closing_american_odds",
        "closing_decimal_odds",
        "closing_implied_probability",
        "consensus_bookmaker_count",
        "consensus_implied_probability",
        "closing_line_movement",
        "closing_probability_movement",
    )
    if any(not row[field] for field in required):
        raise trial.MLBHRProspectiveTrialError(
            "captured closing evidence is incomplete"
        )
    if row["integrity_status"] != "no_post_start_evidence_used":
        raise trial.MLBHRProspectiveTrialError(
            "captured closing integrity status is invalid"
        )
    closing_american, closing_decimal, closing_implied = (
        _validate_american_price(
            american_text=row["closing_american_odds"],
            decimal_text=row["closing_decimal_odds"],
            implied_text=row["closing_implied_probability"],
            description="closing",
        )
    )
    try:
        consensus_count = int(row["consensus_bookmaker_count"])
    except ValueError as exc:
        raise trial.MLBHRProspectiveTrialError(
            "consensus bookmaker count is invalid"
        ) from exc
    consensus_implied = _decimal(
        row["consensus_implied_probability"],
        "consensus implied probability",
    )
    if consensus_count <= 0 or not 0 < consensus_implied < 1:
        raise trial.MLBHRProspectiveTrialError(
            "consensus closing evidence is invalid"
        )
    try:
        original_american = int(prediction["original_american_odds"])
    except ValueError as exc:
        raise trial.MLBHRProspectiveTrialError(
            "committed original American odds are invalid"
        ) from exc
    original_decimal = _decimal(
        prediction["original_decimal_odds"], "original decimal odds"
    )
    original_implied = _decimal(
        prediction["original_implied_probability"],
        "original implied probability",
    )
    expected_line_movement = _decimal(
        trial._format_float(closing_american - original_american),
        "expected closing line movement",
    )
    expected_probability_movement = _decimal(
        trial._format_float(float(closing_implied) - float(original_implied)),
        "expected closing probability movement",
    )
    if (
        _decimal(row["closing_line_movement"], "closing line movement")
        != expected_line_movement
        or _decimal(
            row["closing_probability_movement"],
            "closing probability movement",
        )
        != expected_probability_movement
    ):
        raise trial.MLBHRProspectiveTrialError(
            "closing derived movement fields are inconsistent"
        )
    if status == "captured_same_book" and (
        row["closing_sportsbook"] != prediction["sportsbook"]
    ):
        raise trial.MLBHRProspectiveTrialError(
            "same-book closing evidence does not match prediction sportsbook"
        )
    closing_snapshot = trial._parse_utc(
        row["closing_snapshot_time_utc"], "closing snapshot time"
    )
    captured_at = trial._parse_utc(row["captured_at_utc"], "closing captured_at_utc")
    selected_snapshot = trial._parse_utc(
        prediction["selected_snapshot_timestamp_utc"],
        "selected prediction snapshot time",
    )
    commence_time = trial._parse_utc(
        prediction["commence_time_utc"], "prediction commence time"
    )
    if closing_snapshot >= commence_time:
        raise trial.MLBHRProspectiveTrialError(
            "closing snapshot must be strictly pregame"
        )
    if closing_snapshot > captured_at:
        raise trial.MLBHRProspectiveTrialError(
            "closing snapshot cannot be later than captured_at_utc"
        )
    category = "same_book" if status == "captured_same_book" else "consensus"
    if closing_snapshot <= selected_snapshot:
        return f"non_comparable_temporal_{category}", None
    comparison_implied = (
        closing_implied if category == "same_book" else consensus_implied
    )
    implied_delta = comparison_implied - original_implied
    observation: dict[str, object] = {
        "operating_date": prediction["operating_date"],
        "sportsbook": prediction["sportsbook"],
        "direction": _direction(implied_delta),
        "implied_probability_delta": implied_delta,
    }
    if category == "same_book":
        observation.update(
            {
                "american_odds_change": Decimal(
                    closing_american - original_american
                ),
                "decimal_odds_change": closing_decimal - original_decimal,
            }
        )
    return f"comparable_{category}", observation


def _validate_and_classify_closing(
    *,
    rows: Sequence[Mapping[str, str]],
    predictions: Sequence[Mapping[str, str]],
    control_id: str,
    control_digest: str,
) -> tuple[dict[str, str], dict[str, dict[str, object]]]:
    predictions_by_id = {row["prediction_id"]: row for row in predictions}
    closing_by_id: dict[str, str] = {}
    observations: dict[str, dict[str, object]] = {}
    for row in rows:
        prediction_id = row["prediction_id"]
        prediction = predictions_by_id.get(prediction_id)
        if prediction is None:
            raise trial.MLBHRProspectiveTrialError(
                "closing-line evidence contains an orphan prediction_id"
            )
        _validate_closing_linkage(
            row,
            prediction,
            control_id=control_id,
            control_digest=control_digest,
        )
        status = row["closing_status"]
        if status in _MISSING_STATUS_METHODS:
            _validate_missing_row(row)
            closing_by_id[prediction_id] = status
            continue
        if status not in _CAPTURED_STATUS_METHODS:
            raise trial.MLBHRProspectiveTrialError(
                "closing status and method pair is invalid"
            )
        bucket, observation = _validate_captured_row(row, prediction)
        closing_by_id[prediction_id] = bucket
        if observation is not None:
            observations[prediction_id] = observation
    return closing_by_id, observations


def _date_report(
    operating_date: str,
    predictions: Sequence[Mapping[str, str]],
    closing_buckets: Mapping[str, str],
    observations: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    date_predictions = [
        row for row in predictions if row["operating_date"] == operating_date
    ]
    accounting = _empty_accounting()
    accounting["committed_predictions"] = len(date_predictions)
    same_book: list[Mapping[str, object]] = []
    consensus: list[Mapping[str, object]] = []
    for prediction in date_predictions:
        prediction_id = prediction["prediction_id"]
        bucket = closing_buckets.get(
            prediction_id, "predictions_without_closing_record"
        )
        _increment_accounting(accounting, bucket)
        observation = observations.get(prediction_id)
        if bucket == "comparable_same_book" and observation is not None:
            same_book.append(observation)
        if bucket == "comparable_consensus" and observation is not None:
            consensus.append(observation)
    _assert_accounting(accounting)
    return {
        "operating_date": operating_date,
        "evidence": accounting,
        "movement": {
            "same_book": _movement_summary(same_book, include_decimal=True),
            "consensus": _movement_summary(consensus, include_decimal=False),
        },
    }


def _sportsbook_reports(
    predictions: Sequence[Mapping[str, str]],
    closing_buckets: Mapping[str, str],
    observations: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    sportsbooks = sorted(
        {
            prediction["sportsbook"]
            for prediction in predictions
            if closing_buckets.get(prediction["prediction_id"])
            in {
                "comparable_same_book",
                "non_comparable_temporal_same_book",
            }
        }
    )
    output: list[dict[str, object]] = []
    for sportsbook in sportsbooks:
        relevant_ids = {
            prediction["prediction_id"]
            for prediction in predictions
            if prediction["sportsbook"] == sportsbook
        }
        comparable = [
            observations[prediction_id]
            for prediction_id in sorted(relevant_ids)
            if closing_buckets.get(prediction_id) == "comparable_same_book"
        ]
        non_comparable = sum(
            closing_buckets.get(prediction_id)
            == "non_comparable_temporal_same_book"
            for prediction_id in relevant_ids
        )
        output.append(
            {
                "sportsbook": sportsbook,
                "comparable_same_book": len(comparable),
                "non_comparable_temporal_same_book": non_comparable,
                "movement": _movement_summary(
                    comparable, include_decimal=True
                ),
            }
        )
    return output


def report_prospective_market_movement(
    *,
    control_dir: str | Path,
    trial_root: str | Path,
) -> dict[str, object]:
    """Validate and describe committed pregame market movement without writes."""

    supplied_control = Path(control_dir).expanduser()
    if supplied_control.is_symlink():
        raise trial.MLBHRProspectiveTrialError(
            "control directory may not be a symlink"
        )
    resolved_control = supplied_control.resolve(strict=False)
    before = _capture_evidence_snapshot(resolved_control)
    control, control_digest, frozen_control_dir = trial._read_control(
        resolved_control,
        trial_root=trial_root,
    )
    ledger_rows = trial._strict_committed_prediction_rows(
        control_manifest=control,
        control_manifest_digest=control_digest,
        control_dir=frozen_control_dir,
        ledger_path=frozen_control_dir / "prospective_ledger.csv",
    )
    predictions = tuple(
        row for row in ledger_rows if row["record_type"] == "prediction"
    )
    closing_rows = trial._read_closing_rows(
        frozen_control_dir / "closing_lines.csv"
    )
    closing_buckets, observations = _validate_and_classify_closing(
        rows=closing_rows,
        predictions=predictions,
        control_id=str(control["control_id"]),
        control_digest=control_digest,
    )

    evidence = _empty_accounting()
    evidence["committed_predictions"] = len(predictions)
    same_book: list[Mapping[str, object]] = []
    consensus: list[Mapping[str, object]] = []
    for prediction in predictions:
        prediction_id = prediction["prediction_id"]
        bucket = closing_buckets.get(
            prediction_id, "predictions_without_closing_record"
        )
        _increment_accounting(evidence, bucket)
        observation = observations.get(prediction_id)
        if bucket == "comparable_same_book" and observation is not None:
            same_book.append(observation)
        if bucket == "comparable_consensus" and observation is not None:
            consensus.append(observation)
    _assert_accounting(evidence)

    dates = sorted({row["operating_date"] for row in predictions})
    material = control.get("identity_material")
    if not isinstance(material, Mapping):
        raise trial.MLBHRProspectiveTrialError(
            "control identity material is invalid"
        )
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "control": {
            "control_id": control["control_id"],
            "control_manifest_digest": control_digest,
            "model_id": material["model_id"],
            "model_version": material["model_version"],
            "validation_status": "valid",
            **trial.RESEARCH_BOUNDARY,
        },
        "evidence": evidence,
        "movement": {
            "same_book": _movement_summary(same_book, include_decimal=True),
            "consensus": _movement_summary(consensus, include_decimal=False),
        },
        "by_operating_date": [
            _date_report(
                operating_date,
                predictions,
                closing_buckets,
                observations,
            )
            for operating_date in dates
        ],
        "by_sportsbook": _sportsbook_reports(
            predictions, closing_buckets, observations
        ),
        "integrity": {
            "status": "valid",
            "input_fingerprint": trial._canonical_sha256(before),
        },
    }
    after = _capture_evidence_snapshot(frozen_control_dir)
    if after != before:
        raise trial.MLBHRProspectiveTrialError(
            "prospective market-movement evidence changed during read-only reporting"
        )
    trial._canonical_json_bytes(report)
    return report


def configure_market_movement_cli(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    report = subparsers.add_parser(REPORT_COMMAND)
    report.add_argument("--control-dir", type=Path, required=True)
    report.add_argument("--trial-root", type=Path, required=True)


def execute_market_movement_cli(args: argparse.Namespace) -> dict[str, object]:
    if args.command != REPORT_COMMAND:
        raise trial.MLBHRProspectiveTrialError(
            "unsupported prospective market-movement command"
        )
    return report_prospective_market_movement(
        control_dir=args.control_dir,
        trial_root=args.trial_root,
    )


__all__ = [
    "REPORT_COMMAND",
    "REPORT_COMMANDS",
    "REPORT_SCHEMA_VERSION",
    "configure_market_movement_cli",
    "execute_market_movement_cli",
    "report_prospective_market_movement",
]
