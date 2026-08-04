"""Research-only Streamlit dashboard for legacy NBA model evaluation.

Launch with::

    streamlit run model_evaluation_streamlit_app.py

The UI reads one explicitly selected historical CSV through a strict adapter.
It has no operational actions and delegates every metric to ``model_metrics.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import streamlit as st

from courtvision.evaluation.model_metrics import (
    BucketMetrics,
    EvaluationMetrics,
    SlateWindow,
    calculate_evaluation_metrics,
    select_recent_slates,
    source_is_stale,
)
from courtvision.evaluation.model_records import (
    ELITE_EVALUATION_POPULATION,
    FEEDBACK_EVALUATION_POPULATION,
    ModelEvaluationRecord,
    Outcome,
)
from courtvision.evaluation.model_sources import (
    FEEDBACK_SOURCE_PATH,
    ModelSourceError,
    PHASE1_SOURCE_PATH,
    SourceLoadResult,
    SourceState,
    load_feedback_records,
    load_phase1_records,
)


RESEARCH_ONLY_BANNER = (
    "Research-only legacy observational evaluation — not betting guidance"
)
ELITE_POPULATION_LABEL = "Legacy elite picks"
FEEDBACK_POPULATION_LABEL = "All graded feedback"
POPULATION_OPTIONS = (ELITE_POPULATION_LABEL, FEEDBACK_POPULATION_LABEL)
POPULATION_OVERLAP_WARNING = (
    "These populations may overlap, but they are evaluated separately and are "
    "never combined."
)


@dataclass(frozen=True, slots=True)
class DashboardLoadState:
    """Non-throwing boundary between strict source validation and the UI."""

    source: SourceLoadResult | None
    error_kind: str | None = None
    error_message: str | None = None


def load_dashboard_data(
    population_label: str,
    source_path: str | Path | None = None,
) -> DashboardLoadState:
    """Load exactly one selected source and convert typed errors to UI state."""

    try:
        if population_label == ELITE_POPULATION_LABEL:
            selected_path = source_path or PHASE1_SOURCE_PATH
            result = load_phase1_records(selected_path)
        elif population_label == FEEDBACK_POPULATION_LABEL:
            selected_path = source_path or FEEDBACK_SOURCE_PATH
            result = load_feedback_records(selected_path)
        else:
            raise ValueError(f"Unsupported evaluation population: {population_label!r}")
        return DashboardLoadState(source=result)
    except ModelSourceError as exc:
        return DashboardLoadState(
            source=None,
            error_kind=type(exc).__name__,
            error_message=str(exc),
        )


def _format_date(value: date | None) -> str:
    return value.isoformat() if value is not None else "N/A"


def _format_percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def _format_units(value: float) -> str:
    return f"{value:+.2f}"


def _format_coverage(count: int, total: int) -> str:
    percentage = (count / total) if total else None
    return f"{count}/{total} ({_format_percentage(percentage)})"


def _metric_columns(items: Iterable[tuple[str, str]]) -> None:
    values = tuple(items)
    for column, (label, value) in zip(st.columns(len(values)), values):
        column.metric(label, value)


def _render_source_and_coverage(
    source: SourceLoadResult, *, as_of_date: date
) -> None:
    coverage = source.coverage
    st.header("Source and coverage")
    st.code(source.source_path, language=None)
    _metric_columns(
        (
            ("Source rows", str(coverage.source_row_count)),
            ("Normalized records", str(coverage.unique_record_count)),
            ("Earliest date", _format_date(coverage.earliest_date)),
            ("Latest date", _format_date(coverage.latest_date)),
            ("Unique slates", str(coverage.unique_slates)),
            ("Duplicates", coverage.duplicate_status),
        )
    )
    st.caption(
        f"Data as of {_format_date(coverage.latest_date)}. Dashboard as-of date: "
        f"{as_of_date.isoformat()}. Fully excluded records: "
        f"{coverage.fully_excluded_count}."
    )
    feedback = source.feedback_coverage
    if feedback is None:
        st.caption(
            "Identical duplicate rows excluded: "
            f"{coverage.duplicate_identical_count}."
        )
    else:
        total = coverage.unique_record_count
        _metric_columns(
            (
                ("Unique grade keys", str(feedback.unique_grade_key_count)),
                ("Duplicate groups", str(feedback.duplicate_grade_key_group_count)),
                ("Duplicate source rows", str(feedback.duplicate_source_row_count)),
                (
                    "Duplicate rows excluded",
                    str(feedback.duplicate_rows_excluded_count),
                ),
                ("Conflicting grade keys", str(feedback.conflicting_grade_key_count)),
            )
        )
        _metric_columns(
            (
                ("Final graded", str(feedback.final_graded_count)),
                ("Unresolved", str(feedback.unresolved_count)),
                ("Unsupported", str(feedback.unsupported_count)),
                (
                    "Actual-value coverage",
                    _format_coverage(feedback.actual_value_present_count, total),
                ),
                (
                    "Valid-odds coverage",
                    _format_coverage(feedback.valid_odds_count, total),
                ),
            )
        )
        _metric_columns(
            (
                (
                    "Prediction-value coverage",
                    _format_coverage(feedback.prediction_value_present_count, total),
                ),
                (
                    "Confidence coverage",
                    _format_coverage(feedback.confidence_present_count, total),
                ),
                ("Edge coverage", _format_coverage(feedback.edge_present_count, total)),
                (
                    "Participant-ID coverage",
                    _format_coverage(feedback.participant_id_present_count, total),
                ),
                (
                    "Event-ID coverage",
                    _format_coverage(feedback.event_id_present_count, total),
                ),
            )
        )
    if source_is_stale(coverage.latest_date, as_of_date=as_of_date):
        st.warning(
            "Stale source coverage: the latest prediction slate is materially "
            "behind the dashboard as-of date."
        )


def _apply_display_filters(
    records: tuple[ModelEvaluationRecord, ...],
) -> tuple[ModelEvaluationRecord, ...]:
    markets = sorted({record.market for record in records})
    outcomes = [outcome.value for outcome in Outcome]
    selections = sorted({record.selection for record in records})

    selected_market = st.sidebar.selectbox("Market", ["All markets", *markets])
    selected_outcome = st.sidebar.selectbox("Outcome", ["All outcomes", *outcomes])
    selected_selection = st.sidebar.selectbox(
        "Selection", ["All selections", *selections]
    )
    return tuple(
        record
        for record in records
        if (selected_market == "All markets" or record.market == selected_market)
        and (
            selected_outcome == "All outcomes"
            or record.outcome.value == selected_outcome
        )
        and (
            selected_selection == "All selections"
            or record.selection == selected_selection
        )
    )


def _render_overview(metrics: EvaluationMetrics) -> None:
    st.header("Overview")
    counts = dict(metrics.outcome_counts)
    _metric_columns(
        tuple((outcome.value, str(counts[outcome])) for outcome in Outcome)
    )
    hit_rate = metrics.hit_rate
    flat_roi = metrics.flat_unit_roi
    _metric_columns(
        (
            ("Hit rate", _format_percentage(hit_rate.hit_rate)),
            ("Decisive sample", str(hit_rate.decisive_sample)),
            ("Flat-unit entry-price ROI", _format_percentage(flat_roi.roi)),
            ("Net flat units", _format_units(flat_roi.net_flat_units)),
            (
                "Eligible priced decisions",
                str(flat_roi.eligible_priced_decisions),
            ),
            (
                "Odds coverage",
                f"{flat_roi.odds_coverage_count} "
                f"({_format_percentage(flat_roi.odds_coverage_percentage)})",
            ),
        )
    )
    st.caption(
        f"Flat-unit entry-price ROI excludes {flat_roi.excluded_count} record(s). "
        "Odds coverage is measured over WIN/LOSS/PUSH decisions; no odds are inferred."
    )


def _bucket_rows(buckets: tuple[BucketMetrics, ...]) -> list[dict[str, object]]:
    return [
        {
            "Bucket": bucket.bucket,
            "Sample size": bucket.sample_size,
            "Wins": bucket.wins,
            "Losses": bucket.losses,
            "Decisive sample": bucket.decisive_sample,
            "Hit rate": _format_percentage(bucket.hit_rate),
            "Flat-unit entry-price ROI": _format_percentage(bucket.roi),
            "Net flat units": round(bucket.net_flat_units, 3),
            "Eligible priced decisions": bucket.eligible_priced_decisions,
            "Odds coverage": (
                f"{bucket.odds_coverage_count} "
                f"({_format_percentage(bucket.odds_coverage_percentage)})"
            ),
        }
        for bucket in buckets
    ]


def _render_bucket_analysis(metrics: EvaluationMetrics) -> None:
    st.header("Confidence analysis")
    st.caption(
        "Confidence is a descriptive legacy score, not a calibrated probability. "
        "No probability-calibration or expected-value metric is calculated."
    )
    st.dataframe(
        _bucket_rows(metrics.confidence_buckets),
        width="stretch",
        hide_index=True,
    )

    st.header("Edge analysis")
    st.caption(
        "Edge buckets use absolute raw stat-unit difference only; percentage and "
        "probability edge semantics are not combined."
    )
    st.dataframe(
        _bucket_rows(metrics.edge_buckets),
        width="stretch",
        hide_index=True,
    )


def _recent_rows(
    records: tuple[ModelEvaluationRecord, ...], *, limit: int = 100
) -> list[dict[str, object]]:
    ordered = sorted(
        records,
        key=lambda record: (record.prediction_date, record.source_row_number),
        reverse=True,
    )
    return [
        {
            "Prediction date": record.prediction_date.isoformat(),
            "Participant": record.participant_name,
            "Market": record.market,
            "Selection": record.selection,
            "Line": record.line,
            "Edge (stat units)": record.edge_value,
            "Confidence (legacy score)": record.confidence,
            "American odds": record.odds_american,
            "Outcome": record.outcome.value,
            "Actual value": record.actual_value,
        }
        for record in ordered[:limit]
    ]


def _render_recent_records(records: tuple[ModelEvaluationRecord, ...]) -> None:
    st.header("Recent records")
    rows = _recent_rows(records)
    if not rows:
        st.info("No records match the current observational filters.")
        return
    st.dataframe(rows, width="stretch", hide_index=True)
    if len(records) > len(rows):
        st.caption(f"Showing the most recent {len(rows)} of {len(records)} records.")


def _reason_count(source: SourceLoadResult, reason: str) -> int:
    return dict(source.coverage.exclusion_reason_counts).get(reason, 0)


def _render_data_quality(
    source: SourceLoadResult,
    *,
    as_of_date: date,
) -> None:
    st.header("Data-quality warnings")
    feedback = source.feedback_coverage
    if feedback is not None:
        total = source.coverage.unique_record_count
        warnings = (
            (
                "Unresolved rows excluded from decision metrics",
                feedback.unresolved_count,
            ),
            (
                "Unsupported rows excluded from decision metrics",
                feedback.unsupported_count,
            ),
            (
                "Missing or invalid odds excluded without substitution",
                total - feedback.valid_odds_count,
            ),
            (
                "Missing confidence values appear in Unknown",
                total - feedback.confidence_present_count,
            ),
            (
                "Missing edge values appear in Unknown",
                total - feedback.edge_present_count,
            ),
            (
                "Missing participant IDs reduce coverage",
                total - feedback.participant_id_present_count,
            ),
            (
                "Missing event IDs reduce coverage",
                total - feedback.event_id_present_count,
            ),
            (
                "Missing prediction values reduce coverage",
                total - feedback.prediction_value_present_count,
            ),
            (
                "Compatible duplicate rows canonicalized",
                feedback.duplicate_rows_excluded_count,
            ),
            (
                "Conflicting final outcomes block loading",
                feedback.conflicting_grade_key_count,
            ),
        )
        for label, count in warnings:
            message = f"{label}: {count}"
            if count:
                st.warning(message)
            else:
                st.success(message)
        if source_is_stale(source.coverage.latest_date, as_of_date=as_of_date):
            st.warning("Stale coverage warning: source freshness exceeds 30 days.")
        else:
            st.success("Stale coverage warning: none.")
        return

    warnings = (
        ("Missing participant IDs", _reason_count(source, "missing_participant_id")),
        ("Missing event IDs", _reason_count(source, "missing_event_id")),
        ("Missing odds", _reason_count(source, "missing_odds")),
        ("Invalid odds", _reason_count(source, "invalid_odds")),
        ("Unsupported outcomes", _reason_count(source, "unsupported_outcome")),
        ("Identical duplicate rows excluded", source.coverage.duplicate_identical_count),
        ("Duplicate conflicts", source.coverage.duplicate_conflict_count),
    )
    for label, count in warnings:
        message = f"{label}: {count}"
        if count:
            st.warning(message)
        else:
            st.success(message)
    if source_is_stale(source.coverage.latest_date, as_of_date=as_of_date):
        st.warning("Stale coverage warning: source freshness exceeds 30 days.")
    else:
        st.success("Stale coverage warning: none.")


def render_dashboard(
    source: SourceLoadResult,
    *,
    as_of_date: date,
    expected_population: str = ELITE_EVALUATION_POPULATION,
) -> None:
    """Render loaded observational records without operational dependencies."""

    actual_populations = {record.evaluation_population for record in source.records}
    if actual_populations != {expected_population}:
        raise ValueError(
            "Source records must contain exactly the selected evaluation population"
        )
    _render_source_and_coverage(source, as_of_date=as_of_date)
    window_label = st.sidebar.selectbox(
        "Prediction-slate window",
        [window.value for window in SlateWindow],
        index=2,
        help="Windows count unique prediction dates, not calendar days.",
    )
    windowed = select_recent_slates(source.records, SlateWindow(window_label))
    st.sidebar.caption(
        f"{windowed.unique_slates} unique slates: "
        f"{_format_date(windowed.earliest_date)} to "
        f"{_format_date(windowed.latest_date)}"
    )
    filtered_records = _apply_display_filters(windowed.records)
    metrics = calculate_evaluation_metrics(filtered_records)
    st.caption(
        f"Current view: {len(filtered_records)} records across "
        f"{windowed.unique_slates} selected prediction slates "
        f"({_format_date(windowed.earliest_date)} to "
        f"{_format_date(windowed.latest_date)})."
    )
    _render_overview(metrics)
    _render_bucket_analysis(metrics)
    _render_recent_records(filtered_records)
    _render_data_quality(source, as_of_date=as_of_date)


def main(
    *,
    as_of_date: date | None = None,
    elite_source_path: str | Path = PHASE1_SOURCE_PATH,
    feedback_source_path: str | Path = FEEDBACK_SOURCE_PATH,
) -> None:
    """Render one explicitly selected observational evaluation population."""

    effective_as_of_date = as_of_date or date.today()
    st.set_page_config(page_title="CourtVision Model Evaluation", layout="wide")
    st.warning(RESEARCH_ONLY_BANNER)
    st.title("CourtVision model evaluation")
    selected_label = st.sidebar.selectbox(
        "Evaluation population",
        POPULATION_OPTIONS,
        index=None,
        placeholder="Select an evaluation population",
    )
    if selected_label is None:
        st.info("Select one evaluation population to load observational evidence.")
        return

    st.warning(POPULATION_OVERLAP_WARNING)
    if selected_label == ELITE_POPULATION_LABEL:
        selected_path = elite_source_path
        expected_population = ELITE_EVALUATION_POPULATION
    else:
        selected_path = feedback_source_path
        expected_population = FEEDBACK_EVALUATION_POPULATION
    st.caption(f"Phase 2A · {selected_label} · observational evidence")

    load_state = load_dashboard_data(selected_label, selected_path)
    if load_state.error_message is not None:
        st.error(
            f"Source validation failed ({load_state.error_kind}): "
            f"{load_state.error_message}"
        )
        return

    source = load_state.source
    assert source is not None
    if source.state is SourceState.MISSING:
        st.error(source.message)
        st.code(source.source_path, language=None)
        return
    if source.state is SourceState.EMPTY:
        st.info(source.message)
        st.code(source.source_path, language=None)
        return
    render_dashboard(
        source,
        as_of_date=effective_as_of_date,
        expected_population=expected_population,
    )


if __name__ == "__main__":
    main()
