from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from courtvision.context.game_context import is_identity_quarantined
from courtvision.context.player_identity import SOURCE_IDENTITY_CONFLICT_COLUMNS

REPORT_FILE_PREFIX = "paper_kelly_simulation"
ROW_STAKE_CAP = 0.0025
TOTAL_PAPER_EXPOSURE_CAP = 0.02
PLAYER_EXPOSURE_CAP = 0.005
PLAYER_SELECTION_EXPOSURE_CAP = 0.0035
TEAM_EXPOSURE_CAP = 0.0075
GAME_EXPOSURE_CAP = 0.01
SIDE_EXPOSURE_CAP = 0.008
PAPER_BUCKET_EXPOSURE_CAP = 0.01
SIMULATION_WARNING = "Simulation only; no real stake and no real Kelly eligibility."
PAPER_BUCKET_PRIORITY = {
    "combo_under_watchlist": 0,
    "high_caution_over_watchlist": 1,
}

REPORT_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "paper_bucket",
    "player_id",
    "player_name",
    "team_abbr",
    "opponent",
    "game_id",
    "market_type",
    "selection",
    "line",
    "odds",
    "edge",
    "directional_edge",
    "confidence",
    "quality_score",
    "context_pick_alignment",
    "context_caution_level",
    "pre_cap_simulated_stake",
    "simulated_fraction",
    "simulated_stake",
    "simulated_ev",
    "cap_adjustment_reason",
    "player_exposure_after_cap",
    "team_exposure_after_cap",
    "game_exposure_after_cap",
    "side_exposure_after_cap",
    "bucket_exposure_after_cap",
    "real_kelly_eligible",
    "simulation_only",
    "reason_not_real_kelly",
    *SOURCE_IDENTITY_CONFLICT_COLUMNS,
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


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _prepared_source_frame(
    df: pd.DataFrame,
    *,
    paper_bucket: str,
    reason_not_real_kelly: str,
) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    prepared = df.copy()
    quarantine_mask = prepared.apply(lambda row: is_identity_quarantined(row) is not None, axis=1)
    if quarantine_mask.any():
        prepared = prepared[~quarantine_mask].copy()
    if prepared.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)
    fallback_columns = {
        "team_abbr": "team",
        "line": "sportsbook_line",
        "odds": "american_odds",
    }
    for column, fallback in fallback_columns.items():
        if column not in prepared.columns and fallback in prepared.columns:
            prepared[column] = prepared[fallback]
    for column in REPORT_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared["paper_bucket"] = paper_bucket
    prepared["reason_not_real_kelly"] = reason_not_real_kelly
    return prepared[list(REPORT_COLUMNS)].copy()


def _game_key(row: pd.Series) -> str:
    if _safe_text(row.get("game_id")):
        return f"game:{_safe_text(row.get('game_id')).lower()}"
    team = _safe_text(row.get("team_abbr")).upper()
    opponent = _safe_text(row.get("opponent")).upper()
    return "|".join(sorted(value for value in (team, opponent) if value)) or "unknown_game"


def _team_key(row: pd.Series) -> str:
    return _safe_text(row.get("team_abbr"), default="unknown_team").upper()


def _side_key(row: pd.Series) -> str:
    return _safe_text(row.get("selection"), default="unknown_side").lower()


def _bucket_key(row: pd.Series) -> str:
    return _safe_text(row.get("paper_bucket"), default="unknown_bucket").lower()


def _player_key(row: pd.Series) -> str:
    return _safe_text(row.get("player_name"), default="unknown_player").lower()


def _player_side_key(row: pd.Series) -> str:
    return f"{_player_key(row)}|{_side_key(row)}"


def _directional_edge(row: pd.Series) -> float:
    edge = _safe_float(row.get("edge"))
    selection = _safe_text(row.get("selection")).lower()
    if selection == "over":
        return edge
    if selection == "under":
        return -edge
    return abs(edge)


def _bucket_priority(row: pd.Series) -> int:
    bucket = _safe_text(row.get("paper_bucket"))
    return PAPER_BUCKET_PRIORITY.get(bucket, 99)


def _paper_fraction(row: pd.Series) -> float:
    edge = _directional_edge(row)
    confidence = _safe_float(row.get("confidence"), default=0.0)
    if confidence > 1.0:
        confidence = confidence / 100.0
    if edge <= 0 or confidence <= 0:
        return 0.0
    return min(ROW_STAKE_CAP, edge * confidence / 1000.0)


def _paper_ev(row: pd.Series, stake: float) -> float:
    if stake <= 0:
        return 0.0
    edge = _directional_edge(row)
    if edge <= 0:
        return 0.0
    line = max(abs(_safe_float(row.get("line"), default=1.0)), 1.0)
    confidence = _safe_float(row.get("confidence"), default=0.0)
    if confidence > 1.0:
        confidence = confidence / 100.0
    edge_ratio = min(edge / line, 0.25)
    return stake * edge_ratio * max(confidence, 0.0)


def _cap_reason(requested: float, stake: float, remaining_by_reason: dict[str, float]) -> str:
    if requested <= 0:
        return "no_positive_directional_edge"
    if stake >= requested - 1e-12:
        return "none"
    limiting = [
        reason
        for reason, remaining in remaining_by_reason.items()
        if remaining <= stake + 1e-12
    ]
    return ";".join(limiting) if limiting else "correlation_cap"


def _exposure_lines(report_df: pd.DataFrame, group_column: str, *, limit: int = 10) -> list[str]:
    if report_df.empty or group_column not in report_df.columns:
        return ["- none"]
    grouped = (
        report_df.assign(_stake=pd.to_numeric(report_df["simulated_stake"], errors="coerce").fillna(0.0))
        .groupby(group_column, sort=True)["_stake"]
        .sum()
        .sort_values(ascending=False, kind="mergesort")
    )
    if grouped.empty:
        return ["- none"]
    return [f"- {key}: {float(value):.6f}" for key, value in grouped.head(limit).items()]


def _apply_paper_caps(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    working = df.copy()
    working["directional_edge"] = working.apply(_directional_edge, axis=1)
    working["_bucket_priority"] = working.apply(_bucket_priority, axis=1)
    working["_requested_fraction"] = working.apply(_paper_fraction, axis=1)
    working["_pre_cap_ev"] = working.apply(
        lambda row: _paper_ev(row, float(row["_requested_fraction"])),
        axis=1,
    )
    working = working.sort_values(
        ["_bucket_priority", "_pre_cap_ev", "_requested_fraction"],
        ascending=[True, False, False],
        kind="mergesort",
    )
    player_exposure: dict[str, float] = {}
    player_side_exposure: dict[str, float] = {}
    team_exposure: dict[str, float] = {}
    game_exposure: dict[str, float] = {}
    side_exposure: dict[str, float] = {}
    bucket_exposure: dict[str, float] = {}
    total_exposure = 0.0
    pre_cap_stakes: list[float] = []
    simulated_fractions: list[float] = []
    simulated_evs: list[float] = []
    cap_reasons: list[str] = []
    player_after: list[float] = []
    team_after: list[float] = []
    game_after: list[float] = []
    side_after: list[float] = []
    bucket_after: list[float] = []
    for _, row in working.iterrows():
        player_key = _player_key(row)
        player_side_key = _player_side_key(row)
        team_key = _team_key(row)
        game_key = _game_key(row)
        side_key = _side_key(row)
        bucket_key = _bucket_key(row)
        requested = float(row["_requested_fraction"])
        remaining_by_reason = {
            "total_paper_exposure_cap": max(TOTAL_PAPER_EXPOSURE_CAP - total_exposure, 0.0),
            "player_exposure_cap": max(PLAYER_EXPOSURE_CAP - player_exposure.get(player_key, 0.0), 0.0),
            "player_selection_exposure_cap": max(
                PLAYER_SELECTION_EXPOSURE_CAP - player_side_exposure.get(player_side_key, 0.0),
                0.0,
            ),
            "team_exposure_cap": max(TEAM_EXPOSURE_CAP - team_exposure.get(team_key, 0.0), 0.0),
            "game_exposure_cap": max(GAME_EXPOSURE_CAP - game_exposure.get(game_key, 0.0), 0.0),
            "side_exposure_cap": max(SIDE_EXPOSURE_CAP - side_exposure.get(side_key, 0.0), 0.0),
            "paper_bucket_exposure_cap": max(
                PAPER_BUCKET_EXPOSURE_CAP - bucket_exposure.get(bucket_key, 0.0),
                0.0,
            ),
        }
        stake = min(requested, *remaining_by_reason.values())
        stake = max(stake, 0.0)
        total_exposure += stake
        player_exposure[player_key] = player_exposure.get(player_key, 0.0) + stake
        player_side_exposure[player_side_key] = player_side_exposure.get(player_side_key, 0.0) + stake
        team_exposure[team_key] = team_exposure.get(team_key, 0.0) + stake
        game_exposure[game_key] = game_exposure.get(game_key, 0.0) + stake
        side_exposure[side_key] = side_exposure.get(side_key, 0.0) + stake
        bucket_exposure[bucket_key] = bucket_exposure.get(bucket_key, 0.0) + stake
        pre_cap_stakes.append(round(requested, 6))
        simulated_fractions.append(round(stake, 6))
        simulated_evs.append(round(_paper_ev(row, stake), 6))
        cap_reasons.append(_cap_reason(requested, stake, remaining_by_reason))
        player_after.append(round(player_exposure[player_key], 6))
        team_after.append(round(team_exposure[team_key], 6))
        game_after.append(round(game_exposure[game_key], 6))
        side_after.append(round(side_exposure[side_key], 6))
        bucket_after.append(round(bucket_exposure[bucket_key], 6))

    working["pre_cap_simulated_stake"] = pre_cap_stakes
    working["simulated_fraction"] = simulated_fractions
    working["simulated_stake"] = simulated_fractions
    working["simulated_ev"] = simulated_evs
    working["cap_adjustment_reason"] = cap_reasons
    working["player_exposure_after_cap"] = player_after
    working["team_exposure_after_cap"] = team_after
    working["game_exposure_after_cap"] = game_after
    working["side_exposure_after_cap"] = side_after
    working["bucket_exposure_after_cap"] = bucket_after
    working["real_kelly_eligible"] = False
    working["simulation_only"] = True
    working = working.sort_values(
        ["simulated_ev", "simulated_fraction"],
        ascending=[False, False],
        kind="mergesort",
    )
    return working[list(REPORT_COLUMNS)].reset_index(drop=True)


def build_paper_kelly_simulation(
    *,
    prediction_date: str,
    combo_under_watchlist: pd.DataFrame | None = None,
    high_caution_over_watchlist: pd.DataFrame | None = None,
) -> pd.DataFrame:
    frames = [
        _prepared_source_frame(
            combo_under_watchlist if isinstance(combo_under_watchlist, pd.DataFrame) else pd.DataFrame(),
            paper_bucket="combo_under_watchlist",
            reason_not_real_kelly="combo_market_not_real_kelly_eligible",
        ),
        _prepared_source_frame(
            high_caution_over_watchlist if isinstance(high_caution_over_watchlist, pd.DataFrame) else pd.DataFrame(),
            paper_bucket="high_caution_over_watchlist",
            reason_not_real_kelly="high_caution_over_context_blocked",
        ),
    ]
    non_empty_frames = [frame for frame in frames if not frame.empty]
    if not non_empty_frames:
        return pd.DataFrame(columns=REPORT_COLUMNS)
    combined = pd.concat(non_empty_frames, ignore_index=True)
    combined["prediction_date"] = combined["prediction_date"].replace("", str(prediction_date))
    return _apply_paper_caps(combined)


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    operator_dir = Path(runtime_root) / "operator"
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return operator_dir / f"{stem}.txt", operator_dir / f"{stem}.csv"


def _bucket_exposure_lines(report_df: pd.DataFrame) -> list[str]:
    if report_df.empty:
        return ["- none"]
    grouped = report_df.groupby("paper_bucket", sort=True)["simulated_stake"].sum()
    return [f"- {bucket}: {value:.6f}" for bucket, value in grouped.items()]


def _game_exposure_lines(report_df: pd.DataFrame) -> list[str]:
    if report_df.empty:
        return ["- none"]
    working = report_df.copy()
    working["_game_key"] = working.apply(_game_key, axis=1)
    return _exposure_lines(working, "_game_key")


def _cap_adjustment_reason_lines(report_df: pd.DataFrame) -> list[str]:
    if report_df.empty or "cap_adjustment_reason" not in report_df.columns:
        return ["- none"]
    reasons: Counter[str] = Counter()
    for raw_reason in report_df["cap_adjustment_reason"].fillna("").astype(str):
        for reason in raw_reason.split(";"):
            reason = reason.strip() or "none"
            reasons[reason] += 1
    return [f"- {reason}: {count}" for reason, count in reasons.most_common(10)]


def _row_line(row: pd.Series) -> str:
    return (
        f"{_safe_text(row.get('player_name'), default='Unknown')}: "
        f"{_safe_text(row.get('market_type'), default='unknown')} "
        f"{_safe_text(row.get('selection'), default='unknown')} "
        f"{_safe_text(row.get('line'), default='n/a')} "
        f"bucket={_safe_text(row.get('paper_bucket'), default='unknown')} "
        f"dir_edge={_safe_float(row.get('directional_edge')):.3f} "
        f"pre_cap={_safe_float(row.get('pre_cap_simulated_stake')):.6f} "
        f"stake={_safe_float(row.get('simulated_stake')):.6f} "
        f"ev={_safe_float(row.get('simulated_ev')):.6f} "
        f"cap={_safe_text(row.get('cap_adjustment_reason'), default='none')}"
    )


def render_paper_kelly_text(
    *,
    prediction_date: str,
    report_df: pd.DataFrame,
    csv_path: Path,
) -> str:
    total_pre_cap_exposure = float(
        pd.to_numeric(report_df.get("pre_cap_simulated_stake", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    )
    total_exposure = float(
        pd.to_numeric(report_df.get("simulated_stake", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    )
    exposure_reduced = max(total_pre_cap_exposure - total_exposure, 0.0)
    lines = [
        f"Paper Kelly Simulation - {prediction_date}",
        "=" * 72,
        SIMULATION_WARNING,
        "Stake/exposure values are bankroll fractions, not dollars.",
        f"CSV artifact: {csv_path}",
        "",
        "Summary",
        "-" * 72,
        f"- total paper rows: {int(len(report_df))}",
        f"- total pre-cap exposure: {total_pre_cap_exposure:.6f}",
        f"- total post-cap exposure: {total_exposure:.6f}",
        f"- exposure reduced by caps: {exposure_reduced:.6f}",
        "- top cap adjustment reasons:",
        *_cap_adjustment_reason_lines(report_df),
        "",
        "Exposure After Caps",
        "-" * 72,
        "- by player:",
        *_exposure_lines(report_df, "player_name"),
        "- by team:",
        *_exposure_lines(report_df, "team_abbr"),
        "- by game:",
        *_game_exposure_lines(report_df),
        "- by side:",
        *_exposure_lines(report_df, "selection"),
        "- by bucket:",
        *_bucket_exposure_lines(report_df),
        "",
        "Top 10 Simulated EV Rows",
        "-" * 72,
    ]
    if report_df.empty:
        lines.append("- None")
    else:
        top = report_df.sort_values("simulated_ev", ascending=False, kind="mergesort").head(10)
        for _, row in top.iterrows():
            lines.append(f"- {_row_line(row)}")
    return "\n".join(lines) + "\n"


def write_paper_kelly_simulation(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    combo_under_watchlist: pd.DataFrame | None = None,
    high_caution_over_watchlist: pd.DataFrame | None = None,
) -> tuple[Path, Path, pd.DataFrame]:
    runtime_root = Path(runtime_root)
    text_path, csv_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    operator_dir = runtime_root / "operator"
    combo_df = (
        combo_under_watchlist
        if isinstance(combo_under_watchlist, pd.DataFrame)
        else _read_csv(operator_dir / f"combo_under_watchlist_{prediction_date}.csv")
    )
    high_df = (
        high_caution_over_watchlist
        if isinstance(high_caution_over_watchlist, pd.DataFrame)
        else _read_csv(operator_dir / f"high_caution_over_watchlist_{prediction_date}.csv")
    )
    report_df = build_paper_kelly_simulation(
        prediction_date=prediction_date,
        combo_under_watchlist=combo_df,
        high_caution_over_watchlist=high_df,
    )
    text_path.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(csv_path, index=False)
    text_path.write_text(
        render_paper_kelly_text(
            prediction_date=prediction_date,
            report_df=report_df,
            csv_path=csv_path,
        ),
        encoding="utf-8",
    )
    return text_path, csv_path, report_df


__all__ = [
    "GAME_EXPOSURE_CAP",
    "PAPER_BUCKET_EXPOSURE_CAP",
    "PLAYER_EXPOSURE_CAP",
    "PLAYER_SELECTION_EXPOSURE_CAP",
    "REPORT_COLUMNS",
    "ROW_STAKE_CAP",
    "SIDE_EXPOSURE_CAP",
    "SIMULATION_WARNING",
    "TEAM_EXPOSURE_CAP",
    "TOTAL_PAPER_EXPOSURE_CAP",
    "build_paper_kelly_simulation",
    "render_paper_kelly_text",
    "report_paths_for_date",
    "write_paper_kelly_simulation",
]
