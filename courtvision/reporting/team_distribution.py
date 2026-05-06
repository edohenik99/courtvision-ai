from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


REPORT_FILE_PREFIX = "team_distribution_report"
REPORT_TITLE = "Team Distribution — Observation Only"
OBSERVATION_ONLY_NOTE = (
    "Observation only; this report does not change prediction logic, "
    "eligibility, gates, scoring, or stakes."
)

REPORT_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "team_abbr",
    "full_market_rows",
    "elite_rows",
    "real_kelly_rows",
    "high_caution_over_rows",
    "combo_under_rows",
    "paper_rows",
    "pre_cap_paper_exposure",
    "post_cap_paper_exposure",
    "exposure_reduced_by_caps",
    "over_rows",
    "under_rows",
    "unique_players",
    "unique_markets",
    "avg_edge",
    "avg_abs_edge",
    "avg_confidence",
    "avg_quality_score",
    "context_aligned_rows",
    "context_conflicted_rows",
    "context_neutral_rows",
    "diagnosis",
)

# Diagnosis labels in priority order (first match wins)
DIAGNOSIS_LABELS: tuple[str, ...] = (
    "insufficient_data",
    "paper_cap_limited",
    "context_rejection_driven",
    "market_availability_driven",
    "edge_concentration_driven",
    "balanced",
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return default
    return text


def _safe_float(value: Any, default: float = 0.0) -> float:
    text = _safe_text(value)
    if not text:
        return default
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _truthy(value: Any) -> bool:
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _source_path(*, runtime_root: Path, artifact: str, prediction_date: str) -> Path:
    return runtime_root / "operator" / f"{artifact}_{prediction_date}.csv"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    operator_dir = Path(runtime_root) / "operator"
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return operator_dir / f"{stem}.txt", operator_dir / f"{stem}.csv"


def _same_date_rows(df: pd.DataFrame, prediction_date: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    scoped = df.copy()
    if "prediction_date" in scoped.columns:
        scoped = scoped[scoped["prediction_date"].astype(str).eq(str(prediction_date))].copy()
    return scoped


def _normalize_team_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    for col in ("team_abbr", "team"):
        if col in df.columns:
            return df[col].fillna("").astype(str).str.strip().str.upper().replace("", "UNKNOWN")
    return pd.Series("UNKNOWN", index=df.index)


def _kelly_eligible_mask(df: pd.DataFrame) -> pd.Series:
    for col in ("eligible", "kelly_eligible", "real_kelly_eligible"):
        if col in df.columns:
            return df[col].map(_truthy)
    return pd.Series(True, index=df.index)


def _numeric_mean(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    series = pd.to_numeric(df[col], errors="coerce").dropna()
    return round(float(series.mean()), 4) if len(series) > 0 else 0.0


def _diagnose(
    *,
    full_market_rows: int,
    pre_cap_paper_exposure: float,
    exposure_reduced_by_caps: float,
    context_conflicted_rows: int,
    context_aligned_rows: int,
    context_neutral_rows: int,
    elite_rows: int,
    real_kelly_rows: int,
    paper_rows: int,
    high_caution_over_rows: int,
    combo_under_rows: int,
    avg_abs_edge: float,
) -> str:
    if full_market_rows == 0:
        return "insufficient_data"
    if pre_cap_paper_exposure > 0 and (exposure_reduced_by_caps / pre_cap_paper_exposure) >= 0.25:
        return "paper_cap_limited"
    if context_conflicted_rows >= 2 and context_conflicted_rows > context_aligned_rows + context_neutral_rows:
        return "context_rejection_driven"
    promoted = elite_rows + real_kelly_rows + paper_rows + high_caution_over_rows + combo_under_rows
    if full_market_rows >= 3 and promoted == 0:
        return "market_availability_driven"
    if avg_abs_edge >= 3.0 and full_market_rows >= 2:
        return "edge_concentration_driven"
    return "balanced"


def _team_paper_exposure(paper_df: pd.DataFrame, team_mask: pd.Series) -> tuple[float, float]:
    if paper_df.empty or not team_mask.any():
        return 0.0, 0.0
    team_paper = paper_df[team_mask].copy()
    pre_cap = float(
        pd.to_numeric(
            team_paper.get("pre_cap_simulated_stake", pd.Series(dtype=float)),
            errors="coerce",
        ).fillna(0.0).sum()
    )
    post_cap = float(
        pd.to_numeric(
            team_paper.get("simulated_stake", pd.Series(dtype=float)),
            errors="coerce",
        ).fillna(0.0).sum()
    )
    # When pre_cap column is absent/zero, assume no cap reduction.
    if pre_cap == 0.0 and post_cap > 0.0:
        pre_cap = post_cap
    return round(pre_cap, 6), round(post_cap, 6)


def _build_team_row(
    *,
    prediction_date: str,
    team: str,
    full_market_df: pd.DataFrame,
    fm_teams: pd.Series,
    elite_teams: pd.Series,
    kelly_teams: pd.Series,
    hco_teams: pd.Series,
    combo_teams: pd.Series,
    paper_df: pd.DataFrame,
    paper_teams: pd.Series,
) -> dict[str, Any]:
    def count(teams: pd.Series) -> int:
        return int(teams.eq(team).sum()) if not teams.empty else 0

    full_market_rows = count(fm_teams)
    elite_rows = count(elite_teams)
    real_kelly_rows = count(kelly_teams)
    hco_rows = count(hco_teams)
    combo_rows = count(combo_teams)
    paper_rows_count = count(paper_teams)

    pre_cap, post_cap = _team_paper_exposure(
        paper_df, paper_teams.eq(team) if not paper_teams.empty else pd.Series(dtype=bool)
    )
    exposure_reduced = round(max(pre_cap - post_cap, 0.0), 6)

    over_rows = 0
    under_rows = 0
    unique_players = 0
    unique_markets = 0
    avg_edge = 0.0
    avg_abs_edge = 0.0
    avg_confidence = 0.0
    avg_quality_score = 0.0
    context_aligned_rows = 0
    context_conflicted_rows = 0
    context_neutral_rows = 0

    if not full_market_df.empty and not fm_teams.empty:
        team_fm = full_market_df[fm_teams.eq(team)].copy()
        if not team_fm.empty:
            if "selection" in team_fm.columns:
                sel = team_fm["selection"].fillna("").astype(str).str.lower()
                over_rows = int(sel.eq("over").sum())
                under_rows = int(sel.eq("under").sum())
            unique_players = int(team_fm["player_name"].nunique()) if "player_name" in team_fm.columns else 0
            unique_markets = int(team_fm["market_type"].nunique()) if "market_type" in team_fm.columns else 0
            avg_edge = _numeric_mean(team_fm, "edge")
            if "edge" in team_fm.columns:
                edge_series = pd.to_numeric(team_fm["edge"], errors="coerce").dropna()
                avg_abs_edge = round(float(edge_series.abs().mean()), 4) if len(edge_series) > 0 else 0.0
            avg_confidence = _numeric_mean(team_fm, "confidence")
            avg_quality_score = _numeric_mean(team_fm, "quality_score")
            if "context_pick_alignment" in team_fm.columns:
                alignment = team_fm["context_pick_alignment"].fillna("").astype(str).str.lower()
                context_aligned_rows = int(alignment.eq("aligned").sum())
                context_conflicted_rows = int(alignment.eq("conflicted").sum())
                context_neutral_rows = int(alignment.eq("neutral").sum())

    diagnosis = _diagnose(
        full_market_rows=full_market_rows,
        pre_cap_paper_exposure=pre_cap,
        exposure_reduced_by_caps=exposure_reduced,
        context_conflicted_rows=context_conflicted_rows,
        context_aligned_rows=context_aligned_rows,
        context_neutral_rows=context_neutral_rows,
        elite_rows=elite_rows,
        real_kelly_rows=real_kelly_rows,
        paper_rows=paper_rows_count,
        high_caution_over_rows=hco_rows,
        combo_under_rows=combo_rows,
        avg_abs_edge=avg_abs_edge,
    )

    return {
        "prediction_date": str(prediction_date),
        "team_abbr": team,
        "full_market_rows": full_market_rows,
        "elite_rows": elite_rows,
        "real_kelly_rows": real_kelly_rows,
        "high_caution_over_rows": hco_rows,
        "combo_under_rows": combo_rows,
        "paper_rows": paper_rows_count,
        "pre_cap_paper_exposure": pre_cap,
        "post_cap_paper_exposure": post_cap,
        "exposure_reduced_by_caps": exposure_reduced,
        "over_rows": over_rows,
        "under_rows": under_rows,
        "unique_players": unique_players,
        "unique_markets": unique_markets,
        "avg_edge": avg_edge,
        "avg_abs_edge": avg_abs_edge,
        "avg_confidence": avg_confidence,
        "avg_quality_score": avg_quality_score,
        "context_aligned_rows": context_aligned_rows,
        "context_conflicted_rows": context_conflicted_rows,
        "context_neutral_rows": context_neutral_rows,
        "diagnosis": diagnosis,
    }


def build_team_distribution_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    warnings: list[str] = []

    full_market_df = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="full_market_board", prediction_date=prediction_date)),
        prediction_date,
    )
    elite_df = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="elite_board", prediction_date=prediction_date)),
        prediction_date,
    )

    kelly_raw = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="kelly_stakes", prediction_date=prediction_date)),
        prediction_date,
    )
    if not kelly_raw.empty and elite_df.empty:
        warnings.append("Ignoring Kelly stakes artifact because elite board has 0 rows.")
        kelly_df = pd.DataFrame()
    elif not kelly_raw.empty:
        kelly_df = kelly_raw[_kelly_eligible_mask(kelly_raw)].copy()
    else:
        kelly_df = pd.DataFrame()

    hco_df = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="high_caution_over_watchlist", prediction_date=prediction_date)),
        prediction_date,
    )
    combo_df = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="combo_under_watchlist", prediction_date=prediction_date)),
        prediction_date,
    )
    paper_df = _same_date_rows(
        _read_csv(_source_path(runtime_root=runtime_root_path, artifact="paper_kelly_simulation", prediction_date=prediction_date)),
        prediction_date,
    )

    fm_teams = _normalize_team_series(full_market_df)
    el_teams = _normalize_team_series(elite_df)
    kl_teams = _normalize_team_series(kelly_df)
    hco_teams = _normalize_team_series(hco_df)
    combo_teams = _normalize_team_series(combo_df)
    paper_teams = _normalize_team_series(paper_df)

    all_teams: set[str] = set()
    for teams_series in (fm_teams, el_teams, kl_teams, hco_teams, combo_teams, paper_teams):
        if not teams_series.empty:
            all_teams.update(v for v in teams_series.tolist() if v and v != "UNKNOWN")

    if not all_teams:
        return pd.DataFrame(columns=list(REPORT_COLUMNS)), {
            "prediction_date": str(prediction_date),
            "total_teams": 0,
            "teams_with_elite": 0,
            "teams_with_paper": 0,
            "teams_cap_limited": 0,
            "teams_context_rejected": 0,
            "most_represented_team": "",
            "most_represented_count": 0,
            "diagnosis_counts": {},
            "warnings": warnings,
        }

    rows: list[dict[str, Any]] = []
    for team in sorted(all_teams):
        rows.append(
            _build_team_row(
                prediction_date=prediction_date,
                team=team,
                full_market_df=full_market_df,
                fm_teams=fm_teams,
                elite_teams=el_teams,
                kelly_teams=kl_teams,
                hco_teams=hco_teams,
                combo_teams=combo_teams,
                paper_df=paper_df,
                paper_teams=paper_teams,
            )
        )

    report = pd.DataFrame(rows, columns=list(REPORT_COLUMNS))
    report = report.sort_values(
        ["full_market_rows", "paper_rows", "team_abbr"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)

    diagnosis_counts: dict[str, int] = {label: 0 for label in DIAGNOSIS_LABELS}
    if not report.empty:
        for diag, cnt in report["diagnosis"].value_counts().items():
            diagnosis_counts[str(diag)] = int(cnt)

    most_rep_row = report.iloc[0] if not report.empty else None
    summary: dict[str, Any] = {
        "prediction_date": str(prediction_date),
        "total_teams": int(len(report)),
        "teams_with_elite": int((report["elite_rows"] > 0).sum()) if not report.empty else 0,
        "teams_with_paper": int((report["paper_rows"] > 0).sum()) if not report.empty else 0,
        "teams_cap_limited": int((report["diagnosis"] == "paper_cap_limited").sum()) if not report.empty else 0,
        "teams_context_rejected": int((report["diagnosis"] == "context_rejection_driven").sum()) if not report.empty else 0,
        "most_represented_team": _safe_text(most_rep_row["team_abbr"]) if most_rep_row is not None else "",
        "most_represented_count": int(most_rep_row["full_market_rows"]) if most_rep_row is not None else 0,
        "diagnosis_counts": diagnosis_counts,
        "warnings": warnings,
    }
    return report, summary


def report_row_line(row: pd.Series) -> str:
    return (
        f"{_safe_text(row.get('team_abbr'), default='unknown')}: "
        f"full_market={int(row.get('full_market_rows') or 0)}, "
        f"elite={int(row.get('elite_rows') or 0)}, "
        f"kelly={int(row.get('real_kelly_rows') or 0)}, "
        f"paper={int(row.get('paper_rows') or 0)}, "
        f"hco={int(row.get('high_caution_over_rows') or 0)}, "
        f"combo={int(row.get('combo_under_rows') or 0)}, "
        f"post_cap_exp={float(row.get('post_cap_paper_exposure') or 0.0):.6f}, "
        f"reduced={float(row.get('exposure_reduced_by_caps') or 0.0):.6f}, "
        f"players={int(row.get('unique_players') or 0)}, "
        f"avg_abs_edge={float(row.get('avg_abs_edge') or 0.0):.3f}, "
        f"conflicted={int(row.get('context_conflicted_rows') or 0)}, "
        f"diagnosis={_safe_text(row.get('diagnosis'), default='unknown')}"
    )


def render_team_distribution_text(
    *,
    prediction_date: str,
    report_df: pd.DataFrame,
    summary: dict[str, Any],
    csv_path: Path,
) -> str:
    diag_counts = summary.get("diagnosis_counts", {})
    lines = [
        f"{REPORT_TITLE} - {prediction_date}",
        "=" * 72,
        OBSERVATION_ONLY_NOTE,
        f"CSV artifact: {csv_path}",
        "",
        "Summary",
        "-" * 72,
        f"- total teams: {int(summary.get('total_teams') or 0)}",
        f"- teams with elite rows: {int(summary.get('teams_with_elite') or 0)}",
        f"- teams with paper rows: {int(summary.get('teams_with_paper') or 0)}",
        f"- teams cap-limited: {int(summary.get('teams_cap_limited') or 0)}",
        f"- teams context-rejected: {int(summary.get('teams_context_rejected') or 0)}",
        (
            f"- most represented: "
            f"{_safe_text(summary.get('most_represented_team'), default='none')} "
            f"({int(summary.get('most_represented_count') or 0)} full-market rows)"
        ),
        "- diagnosis breakdown:",
    ]
    for label in DIAGNOSIS_LABELS:
        lines.append(f"  - {label}: {int(diag_counts.get(label, 0))}")
    warnings_list = summary.get("warnings", [])
    if warnings_list:
        lines.append("- warnings:")
        for warning in warnings_list:
            lines.append(f"  - {warning}")
    lines.extend(["", "Team Rows", "-" * 72])
    if report_df.empty:
        lines.append("- None")
    else:
        for _, row in report_df.head(20).iterrows():
            lines.append(f"- {report_row_line(row)}")
    return "\n".join(lines) + "\n"


def write_team_distribution_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, pd.DataFrame, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    text_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
    )
    report_df, summary = build_team_distribution_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(csv_path, index=False)
    text_path.write_text(
        render_team_distribution_text(
            prediction_date=prediction_date,
            report_df=report_df,
            summary=summary,
            csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return text_path, csv_path, report_df, summary


__all__ = [
    "DIAGNOSIS_LABELS",
    "OBSERVATION_ONLY_NOTE",
    "REPORT_COLUMNS",
    "REPORT_FILE_PREFIX",
    "REPORT_TITLE",
    "build_team_distribution_report",
    "render_team_distribution_text",
    "report_paths_for_date",
    "report_row_line",
    "write_team_distribution_report",
]
