from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


REPORT_FILE_PREFIX = "correlation_exposure_report"
REPORT_TITLE = "Correlation Exposure \u2014 Observation Only"
OBSERVATION_ONLY_NOTE = "Observation only; this report does not change eligibility, gates, scoring, or stakes."

SOURCE_ARTIFACTS: tuple[str, ...] = (
    "elite_board",
    "kelly_stakes",
    "paper_kelly_simulation",
    "high_caution_over_watchlist",
    "combo_under_watchlist",
    "full_market_board",
)

NORMALIZED_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "source_artifact",
    "exposure_bucket",
    "player_name",
    "team_abbr",
    "opponent",
    "game_key",
    "market_type",
    "selection",
    "paper_bucket",
    "context_pick_alignment",
    "context_caution_level",
)

REPORT_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "risk_type",
    "risk_label",
    "group_key",
    "player_name",
    "team_abbr",
    "opponent",
    "game_key",
    "market_type",
    "selection",
    "paper_bucket",
    "context_pick_alignment",
    "context_caution_level",
    "exposure_bucket",
    "source_artifacts",
    "source_buckets",
    "total_rows",
    "distinct_players",
    "distinct_teams",
    "distinct_games",
    "distinct_markets",
    "distinct_selections",
    "elite_rows",
    "real_kelly_rows",
    "paper_kelly_rows",
    "watchlist_rows",
    "full_market_rows",
    "repeated_player_count",
    "max_rows_per_player",
    "max_rows_per_game",
    "max_rows_per_team",
    "over_count",
    "under_count",
    "dominant_side",
    "dominant_side_share",
    "players_in_multiple_buckets",
    "players_multiple_markets_same_direction",
    "risk_reason",
)

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "extreme": 3}


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


def _truthy(value: Any) -> bool:
    return _safe_text(value).lower() in {"true", "1", "yes", "y"}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _source_path(
    *,
    runtime_root: Path,
    artifact: str,
    prediction_date: str,
) -> Path:
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


def _kelly_eligible_mask(df: pd.DataFrame) -> pd.Series:
    for column in ("eligible", "kelly_eligible", "real_kelly_eligible"):
        if column in df.columns:
            return df[column].map(_truthy)
    return pd.Series(True, index=df.index)


def _valid_kelly_rows(
    *,
    kelly_path: Path,
    elite_df: pd.DataFrame,
    prediction_date: str,
    warnings: list[str],
) -> pd.DataFrame:
    kelly = _same_date_rows(_read_csv(kelly_path), prediction_date)
    if kelly.empty:
        return pd.DataFrame()
    if elite_df.empty:
        warnings.append("Ignoring Kelly stakes artifact because elite board has 0 rows.")
        return pd.DataFrame()
    eligible = kelly[_kelly_eligible_mask(kelly)].copy()
    if eligible.empty:
        warnings.append("Kelly stakes artifact had no real Kelly eligible same-date rows.")
    return eligible


def _team_value(row: pd.Series) -> str:
    return (_safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))).upper()


def _opponent_value(row: pd.Series) -> str:
    opponent = (_safe_text(row.get("opponent")) or _safe_text(row.get("opponent_abbr"))).upper()
    if opponent == "OPP":
        return ""
    return opponent


def _game_key(row: pd.Series) -> str:
    game_id = _safe_text(row.get("game_id"))
    if game_id:
        return f"game:{game_id.lower()}"
    team = _team_value(row)
    opponent = _opponent_value(row)
    teams = sorted(value for value in (team, opponent) if value)
    return "-".join(teams) if teams else "unknown_game"


def _market_value(row: pd.Series) -> str:
    return (
        _safe_text(row.get("market_type"))
        or _safe_text(row.get("market"))
        or _safe_text(row.get("prop_type"))
        or "unknown"
    ).lower()


def _selection_value(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side")) or "unknown").lower()


def _exposure_bucket(row: pd.Series, source_artifact: str) -> str:
    if source_artifact == "paper_kelly_simulation":
        return _safe_text(row.get("paper_bucket"), default="paper_kelly").lower()
    if source_artifact == "kelly_stakes":
        return "real_kelly"
    if source_artifact == "elite_board":
        return "elite"
    if source_artifact == "full_market_board":
        return "full_market_candidate"
    return source_artifact


def _normalize_source_rows(
    df: pd.DataFrame,
    *,
    prediction_date: str,
    source_artifact: str,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=list(NORMALIZED_COLUMNS))
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        exposure_bucket = _exposure_bucket(row, source_artifact)
        rows.append(
            {
                "prediction_date": str(prediction_date),
                "source_artifact": source_artifact,
                "exposure_bucket": exposure_bucket,
                "player_name": _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "unknown",
                "team_abbr": _team_value(row) or "unknown",
                "opponent": _opponent_value(row) or "unknown",
                "game_key": _game_key(row),
                "market_type": _market_value(row),
                "selection": _selection_value(row),
                "paper_bucket": _safe_text(row.get("paper_bucket")).lower(),
                "context_pick_alignment": _safe_text(row.get("context_pick_alignment"), default="unknown").lower(),
                "context_caution_level": _safe_text(row.get("context_caution_level"), default="unknown").lower(),
            }
        )
    return pd.DataFrame(rows, columns=list(NORMALIZED_COLUMNS))


def load_correlation_exposure_rows(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[pd.DataFrame, list[str]]:
    runtime_root_path = Path(runtime_root)
    warnings: list[str] = []
    elite_path = _source_path(runtime_root=runtime_root_path, artifact="elite_board", prediction_date=prediction_date)
    elite_df = _same_date_rows(_read_csv(elite_path), prediction_date)
    frames: list[pd.DataFrame] = [
        _normalize_source_rows(elite_df, prediction_date=prediction_date, source_artifact="elite_board")
    ]

    kelly_path = _source_path(runtime_root=runtime_root_path, artifact="kelly_stakes", prediction_date=prediction_date)
    kelly_df = _valid_kelly_rows(
        kelly_path=kelly_path,
        elite_df=elite_df,
        prediction_date=prediction_date,
        warnings=warnings,
    )
    frames.append(_normalize_source_rows(kelly_df, prediction_date=prediction_date, source_artifact="kelly_stakes"))

    for artifact in (
        "paper_kelly_simulation",
        "high_caution_over_watchlist",
        "combo_under_watchlist",
        "full_market_board",
    ):
        path = _source_path(runtime_root=runtime_root_path, artifact=artifact, prediction_date=prediction_date)
        df = _same_date_rows(_read_csv(path), prediction_date)
        frames.append(_normalize_source_rows(df, prediction_date=prediction_date, source_artifact=artifact))

    non_empty = [frame for frame in frames if not frame.empty]
    if not non_empty:
        return pd.DataFrame(columns=list(NORMALIZED_COLUMNS)), warnings
    combined = pd.concat(non_empty, ignore_index=True)
    for column in NORMALIZED_COLUMNS:
        if column not in combined.columns:
            combined[column] = ""
    return combined[list(NORMALIZED_COLUMNS)].reset_index(drop=True), warnings


def _risk_from_count(count: int) -> str:
    if count >= 8:
        return "extreme"
    if count >= 5:
        return "high"
    if count >= 2:
        return "medium"
    return "low"


def _risk_from_side_share(share: float, total: int) -> str:
    if total < 4:
        return "low"
    if share >= 0.85 and total >= 10:
        return "extreme"
    if share >= 0.75:
        return "high"
    if share >= 0.60:
        return "medium"
    return "low"


def _max_risk(*labels: str) -> str:
    valid = [label for label in labels if label in RISK_ORDER]
    if not valid:
        return "low"
    return max(valid, key=lambda label: RISK_ORDER[label])


def _value_counts(df: pd.DataFrame, column: str) -> pd.Series:
    if df.empty or column not in df.columns:
        return pd.Series(dtype=int)
    values = df[column].fillna("unknown").astype(str).str.strip().replace("", "unknown")
    return values.value_counts()


def _daily_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "repeated_player_count": 0,
            "max_rows_per_player": 0,
            "max_rows_per_game": 0,
            "max_rows_per_team": 0,
            "over_count": 0,
            "under_count": 0,
            "dominant_side": "none",
            "dominant_side_share": 0.0,
            "players_in_multiple_buckets": 0,
            "players_multiple_markets_same_direction": 0,
        }

    player_counts = _value_counts(df, "player_name")
    game_counts = _value_counts(df, "game_key")
    team_counts = _value_counts(df, "team_abbr")
    selection = df["selection"].fillna("").astype(str).str.lower()
    over_count = int(selection.eq("over").sum())
    under_count = int(selection.eq("under").sum())
    side_total = over_count + under_count
    if over_count >= under_count and side_total:
        dominant_side = "over"
        dominant_side_count = over_count
    elif side_total:
        dominant_side = "under"
        dominant_side_count = under_count
    else:
        dominant_side = "none"
        dominant_side_count = 0
    player_bucket_counts = (
        df.groupby("player_name", dropna=False)["exposure_bucket"].nunique()
        if "exposure_bucket" in df.columns
        else pd.Series(dtype=int)
    )
    player_direction_market_counts = (
        df.groupby(["player_name", "selection"], dropna=False)["market_type"].nunique()
        if {"player_name", "selection", "market_type"}.issubset(df.columns)
        else pd.Series(dtype=int)
    )
    return {
        "repeated_player_count": int((player_counts > 1).sum()),
        "max_rows_per_player": int(player_counts.max()) if not player_counts.empty else 0,
        "max_rows_per_game": int(game_counts.max()) if not game_counts.empty else 0,
        "max_rows_per_team": int(team_counts.max()) if not team_counts.empty else 0,
        "over_count": over_count,
        "under_count": under_count,
        "dominant_side": dominant_side,
        "dominant_side_share": round(float(dominant_side_count / side_total), 4) if side_total else 0.0,
        "players_in_multiple_buckets": int((player_bucket_counts > 1).sum()),
        "players_multiple_markets_same_direction": int((player_direction_market_counts > 1).sum()),
    }


def _source_counts(segment: pd.DataFrame) -> dict[str, int]:
    source = segment.get("source_artifact", pd.Series("", index=segment.index)).astype(str)
    return {
        "elite_rows": int(source.eq("elite_board").sum()),
        "real_kelly_rows": int(source.eq("kelly_stakes").sum()),
        "paper_kelly_rows": int(source.eq("paper_kelly_simulation").sum()),
        "watchlist_rows": int(source.isin(["high_caution_over_watchlist", "combo_under_watchlist"]).sum()),
        "full_market_rows": int(source.eq("full_market_board").sum()),
    }


def _segment_summary(
    segment: pd.DataFrame,
    *,
    prediction_date: str,
    risk_type: str,
    group_key: str,
    risk_label: str,
    risk_reason: str,
    daily_metrics: dict[str, Any],
    dimensions: dict[str, str] | None = None,
) -> dict[str, Any]:
    dimensions = dimensions or {}
    source_counts = _source_counts(segment)
    def joined(column: str) -> str:
        if segment.empty or column not in segment.columns:
            return ""
        values = sorted({_safe_text(value).lower() for value in segment[column].tolist() if _safe_text(value)})
        return "|".join(values)

    row = {
        "prediction_date": str(prediction_date),
        "risk_type": risk_type,
        "risk_label": risk_label,
        "group_key": group_key,
        "player_name": dimensions.get("player_name", joined("player_name")),
        "team_abbr": dimensions.get("team_abbr", joined("team_abbr")),
        "opponent": dimensions.get("opponent", joined("opponent")),
        "game_key": dimensions.get("game_key", joined("game_key")),
        "market_type": dimensions.get("market_type", joined("market_type")),
        "selection": dimensions.get("selection", joined("selection")),
        "paper_bucket": dimensions.get("paper_bucket", joined("paper_bucket")),
        "context_pick_alignment": dimensions.get("context_pick_alignment", joined("context_pick_alignment")),
        "context_caution_level": dimensions.get("context_caution_level", joined("context_caution_level")),
        "exposure_bucket": joined("exposure_bucket"),
        "source_artifacts": joined("source_artifact"),
        "source_buckets": joined("exposure_bucket"),
        "total_rows": int(len(segment)),
        "distinct_players": int(segment["player_name"].nunique()) if "player_name" in segment.columns else 0,
        "distinct_teams": int(segment["team_abbr"].nunique()) if "team_abbr" in segment.columns else 0,
        "distinct_games": int(segment["game_key"].nunique()) if "game_key" in segment.columns else 0,
        "distinct_markets": int(segment["market_type"].nunique()) if "market_type" in segment.columns else 0,
        "distinct_selections": int(segment["selection"].nunique()) if "selection" in segment.columns else 0,
        **source_counts,
        **daily_metrics,
        "risk_reason": risk_reason,
    }
    return {column: row.get(column, "") for column in REPORT_COLUMNS}


def _overall_risk_label(metrics: dict[str, Any]) -> str:
    return _max_risk(
        _risk_from_count(int(metrics.get("max_rows_per_player", 0))),
        _risk_from_count(int(metrics.get("max_rows_per_game", 0))),
        _risk_from_count(int(metrics.get("max_rows_per_team", 0))),
        _risk_from_side_share(
            float(metrics.get("dominant_side_share", 0.0) or 0.0),
            int(metrics.get("over_count", 0) or 0) + int(metrics.get("under_count", 0) or 0),
        ),
    )


def _append_count_rows(
    rows: list[dict[str, Any]],
    df: pd.DataFrame,
    *,
    prediction_date: str,
    daily_metrics: dict[str, Any],
    column: str,
    risk_type: str,
    dimension_name: str,
) -> None:
    counts = _value_counts(df, column)
    for value, count in counts.items():
        count = int(count)
        if count <= 1:
            continue
        segment = df[df[column].astype(str).eq(str(value))].copy()
        label = _risk_from_count(count)
        rows.append(
            _segment_summary(
                segment,
                prediction_date=prediction_date,
                risk_type=risk_type,
                group_key=str(value),
                risk_label=label,
                risk_reason=f"{dimension_name} appears in {count} source row(s).",
                daily_metrics=daily_metrics,
                dimensions={column: str(value)},
            )
        )


def build_correlation_exposure_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    exposure_df, warnings = load_correlation_exposure_rows(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    daily_metrics = _daily_metrics(exposure_df)
    rows: list[dict[str, Any]] = []

    overall_label = _overall_risk_label(daily_metrics)
    rows.append(
        _segment_summary(
            exposure_df,
            prediction_date=prediction_date,
            risk_type="overall",
            group_key="all",
            risk_label=overall_label,
            risk_reason="Daily aggregate across available same-date source artifacts.",
            daily_metrics=daily_metrics,
        )
    )

    if not exposure_df.empty:
        _append_count_rows(
            rows,
            exposure_df,
            prediction_date=prediction_date,
            daily_metrics=daily_metrics,
            column="player_name",
            risk_type="player",
            dimension_name="player",
        )
        _append_count_rows(
            rows,
            exposure_df,
            prediction_date=prediction_date,
            daily_metrics=daily_metrics,
            column="game_key",
            risk_type="game",
            dimension_name="game",
        )
        _append_count_rows(
            rows,
            exposure_df,
            prediction_date=prediction_date,
            daily_metrics=daily_metrics,
            column="team_abbr",
            risk_type="team",
            dimension_name="team",
        )

        side_total = int(daily_metrics["over_count"]) + int(daily_metrics["under_count"])
        side_label = _risk_from_side_share(float(daily_metrics["dominant_side_share"]), side_total)
        rows.append(
            _segment_summary(
                exposure_df,
                prediction_date=prediction_date,
                risk_type="side_concentration",
                group_key=str(daily_metrics["dominant_side"]),
                risk_label=side_label,
                risk_reason=(
                    f"{daily_metrics['dominant_side']} side share is "
                    f"{float(daily_metrics['dominant_side_share']):.1%} across {side_total} over/under row(s)."
                ),
                daily_metrics=daily_metrics,
                dimensions={"selection": str(daily_metrics["dominant_side"])},
            )
        )

        for player, group in exposure_df.groupby("player_name", sort=True, dropna=False):
            bucket_count = int(group["exposure_bucket"].nunique())
            if bucket_count > 1:
                rows.append(
                    _segment_summary(
                        group,
                        prediction_date=prediction_date,
                        risk_type="multi_bucket_player",
                        group_key=str(player),
                        risk_label=_risk_from_count(bucket_count),
                        risk_reason=f"Player appears in {bucket_count} exposure bucket(s).",
                        daily_metrics=daily_metrics,
                        dimensions={"player_name": str(player)},
                    )
                )
            for selection, side_group in group.groupby("selection", sort=True, dropna=False):
                market_count = int(side_group["market_type"].nunique())
                if market_count > 1 and str(selection) in {"over", "under"}:
                    rows.append(
                        _segment_summary(
                            side_group,
                            prediction_date=prediction_date,
                            risk_type="multi_market_same_direction_player",
                            group_key=f"{player}|{selection}",
                            risk_label=_risk_from_count(market_count),
                            risk_reason=f"Player has {market_count} market(s) on the {selection} side.",
                            daily_metrics=daily_metrics,
                            dimensions={"player_name": str(player), "selection": str(selection)},
                        )
                    )

        group_columns = [
            "player_name",
            "team_abbr",
            "game_key",
            "market_type",
            "selection",
            "paper_bucket",
            "context_pick_alignment",
            "context_caution_level",
        ]
        for group_values, segment in exposure_df.groupby(group_columns, sort=True, dropna=False):
            count = int(len(segment))
            if count <= 1:
                continue
            dimensions = dict(zip(group_columns, [str(value) for value in group_values], strict=True))
            rows.append(
                _segment_summary(
                    segment,
                    prediction_date=prediction_date,
                    risk_type="dimension_group",
                    group_key="|".join(str(value) for value in group_values),
                    risk_label=_risk_from_count(count),
                    risk_reason="Repeated rows in requested exposure grouping.",
                    daily_metrics=daily_metrics,
                    dimensions=dimensions,
                )
            )

    report = pd.DataFrame(rows, columns=list(REPORT_COLUMNS))
    report["_risk_rank"] = report["risk_label"].map(RISK_ORDER).fillna(0).astype(int)
    report = report.sort_values(
        ["_risk_rank", "total_rows", "risk_type", "group_key"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).drop(columns=["_risk_rank"])
    summary = {
        "prediction_date": str(prediction_date),
        "total_rows": int(len(exposure_df)),
        "report_rows": int(len(report)),
        "risk_label": overall_label,
        "warnings": warnings,
        **daily_metrics,
        **_source_counts(exposure_df),
    }
    return report.reset_index(drop=True), summary


def report_row_line(row: pd.Series) -> str:
    return (
        f"{_safe_text(row.get('risk_type'), default='unknown')}:{_safe_text(row.get('group_key'), default='all')} "
        f"risk={_safe_text(row.get('risk_label'), default='low')} "
        f"rows={int(row.get('total_rows') or 0)} "
        f"players={int(row.get('distinct_players') or 0)} "
        f"games={int(row.get('distinct_games') or 0)} "
        f"markets={int(row.get('distinct_markets') or 0)} "
        f"sources={_safe_text(row.get('source_buckets'), default='n/a')}"
    )


def render_correlation_exposure_text(
    *,
    prediction_date: str,
    report_df: pd.DataFrame,
    summary: dict[str, Any],
    csv_path: Path,
) -> str:
    source_counts = {
        "elite": int(summary.get("elite_rows", 0) or 0),
        "real_kelly": int(summary.get("real_kelly_rows", 0) or 0),
        "paper_kelly": int(summary.get("paper_kelly_rows", 0) or 0),
        "watchlists": int(summary.get("watchlist_rows", 0) or 0),
        "full_market": int(summary.get("full_market_rows", 0) or 0),
    }
    lines = [
        f"{REPORT_TITLE} - {prediction_date}",
        "=" * 72,
        OBSERVATION_ONLY_NOTE,
        f"CSV artifact: {csv_path}",
        "",
        "Summary",
        "-" * 72,
        f"- total source rows: {int(summary.get('total_rows', 0) or 0)}",
        f"- risk label: {_safe_text(summary.get('risk_label'), default='low')}",
        f"- repeated player count: {int(summary.get('repeated_player_count', 0) or 0)}",
        f"- max rows per player: {int(summary.get('max_rows_per_player', 0) or 0)}",
        f"- max rows per game: {int(summary.get('max_rows_per_game', 0) or 0)}",
        f"- max rows per team: {int(summary.get('max_rows_per_team', 0) or 0)}",
        (
            f"- side concentration: over={int(summary.get('over_count', 0) or 0)}, "
            f"under={int(summary.get('under_count', 0) or 0)}, "
            f"dominant={_safe_text(summary.get('dominant_side'), default='none')} "
            f"({float(summary.get('dominant_side_share', 0.0) or 0.0):.1%})"
        ),
        f"- players appearing in multiple buckets: {int(summary.get('players_in_multiple_buckets', 0) or 0)}",
        (
            "- players with multiple markets in same direction: "
            f"{int(summary.get('players_multiple_markets_same_direction', 0) or 0)}"
        ),
        "- source rows:",
    ]
    for label, count in source_counts.items():
        lines.append(f"  - {label}: {count}")
    warnings = summary.get("warnings", [])
    if warnings:
        lines.append("- warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
    lines.extend(["", "Top Risk Groups", "-" * 72])
    if report_df.empty:
        lines.append("- None")
    else:
        for _, row in report_df.head(20).iterrows():
            lines.append(f"- {report_row_line(row)}")
    return "\n".join(lines) + "\n"


def write_correlation_exposure_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, pd.DataFrame, dict[str, Any]]:
    runtime_root_path = Path(runtime_root)
    text_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
    )
    report_df, summary = build_correlation_exposure_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(csv_path, index=False)
    text_path.write_text(
        render_correlation_exposure_text(
            prediction_date=prediction_date,
            report_df=report_df,
            summary=summary,
            csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return text_path, csv_path, report_df, summary


__all__ = [
    "NORMALIZED_COLUMNS",
    "OBSERVATION_ONLY_NOTE",
    "REPORT_COLUMNS",
    "REPORT_FILE_PREFIX",
    "REPORT_TITLE",
    "SOURCE_ARTIFACTS",
    "build_correlation_exposure_report",
    "load_correlation_exposure_rows",
    "render_correlation_exposure_text",
    "report_paths_for_date",
    "report_row_line",
    "write_correlation_exposure_report",
]
