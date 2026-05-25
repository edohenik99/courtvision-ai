from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision_ai import CourtVisionAI
from scripts.repair_pending_grades import COMBO_MARKET_COMPONENTS, MARKET_TYPE_ALIASES, SUPPORTED_MARKETS


BASE_STAT_COLUMNS = {
    "player_points": "pts",
    "player_rebounds": "reb",
    "player_assists": "ast",
    "player_blocks": "blk",
    "player_steals": "stl",
}
FINAL_RESULTS = {"win", "loss", "push"}


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>"}:
        return default
    return text


def _safe_float(value: Any) -> float | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _id_key(value: Any) -> str:
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = _safe_text(value)
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text.lower()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _runtime_outputs_root(runtime_root: Path) -> Path:
    return runtime_root.parent if runtime_root.name == "runtime" else runtime_root


def _normalize_market_type(value: Any) -> str:
    text = _safe_text(value).lower().replace(" ", "_")
    return MARKET_TYPE_ALIASES.get(text, text)


def _row_market_type(row: pd.Series) -> str:
    for column in ("market_type", "market", "prop_type", "raw_prop_type"):
        market = _normalize_market_type(row.get(column))
        if market:
            return market
    return ""


def _selection(row: pd.Series) -> str:
    return (_safe_text(row.get("selection")) or _safe_text(row.get("side"))).lower()


def _line_value(row: pd.Series) -> float | None:
    for column in ("line", "sportsbook_line", "line_value"):
        value = _safe_float(row.get(column))
        if value is not None:
            return value
    return None


def _line_result(selection: str, actual_value: float, line: float) -> str:
    if abs(actual_value - line) < 1e-9:
        return "push"
    if selection == "over":
        return "win" if actual_value > line else "loss"
    if selection == "under":
        return "win" if actual_value < line else "loss"
    return "unresolved"


def _team_abbr(row: pd.Series) -> str:
    return (_safe_text(row.get("team_abbr")) or _safe_text(row.get("team"))).upper()


def _entity_name(row: pd.Series) -> str:
    return _safe_text(row.get("entity_name")) or _safe_text(row.get("player_name"))


def _grade_key(row: pd.Series, *, prediction_date: str, market_type: str, selection: str, line: float) -> str:
    return "|".join([prediction_date, market_type, _entity_name(row), selection, str(line)])


def _is_final_game(row: pd.Series | dict[str, Any]) -> bool:
    status = _safe_text(row.get("status")).lower()
    if any(token in status for token in ("final", "complete", "closed")):
        return True
    home_score = _safe_float(row.get("home_team_score"))
    away_score = _safe_float(row.get("visitor_team_score"))
    has_scores = home_score is not None and away_score is not None
    return bool(has_scores and (home_score != 0.0 or away_score != 0.0) and status not in {"scheduled", "not started", "pre-game", "pregame"})


def _final_game_ids(games: pd.DataFrame) -> set[str]:
    if not isinstance(games, pd.DataFrame) or games.empty:
        return set()
    ids: set[str] = set()
    for _, row in games.iterrows():
        if _is_final_game(row):
            game_id = _id_key(row.get("game_id") or row.get("id"))
            if game_id:
                ids.add(game_id)
    return ids


def _stats_for_board_row(row: pd.Series, stats: pd.DataFrame) -> pd.DataFrame:
    if stats.empty:
        return pd.DataFrame()
    player_id = _id_key(row.get("player_id"))
    game_id = _id_key(row.get("game_id"))
    team = _team_abbr(row)
    player_name = _entity_name(row).lower()

    candidates = stats.copy()
    strict = candidates
    strict_has_player_id = bool(player_id and "player_id" in strict.columns)
    strict_has_game_id = bool(game_id and "game_id" in strict.columns)
    if player_id and "player_id" in strict.columns:
        strict = strict[strict["player_id"].map(_id_key).eq(player_id)].copy()
    if game_id and "game_id" in strict.columns:
        strict = strict[strict["game_id"].map(_id_key).eq(game_id)].copy()
    if team and "team_abbr" in strict.columns:
        team_strict = strict[strict["team_abbr"].map(lambda value: _safe_text(value).upper()).eq(team)].copy()
        if not team_strict.empty:
            strict = team_strict
    if strict_has_player_id and strict_has_game_id and not strict.empty:
        return strict

    fallback = candidates
    if player_name and "player_name" in fallback.columns:
        fallback = fallback[fallback["player_name"].map(lambda value: _safe_text(value).lower()).eq(player_name)].copy()
    if game_id and "game_id" in fallback.columns:
        fallback = fallback[fallback["game_id"].map(_id_key).eq(game_id)].copy()
    if team and "team_abbr" in fallback.columns:
        fallback = fallback[fallback["team_abbr"].map(lambda value: _safe_text(value).upper()).eq(team)].copy()
    return fallback


def _stat_value_for_market(row: pd.Series, stats: pd.DataFrame, market_type: str) -> tuple[float | None, str]:
    candidates = _stats_for_board_row(row, stats)
    if candidates.empty:
        return None, "player_stat_match_missing"
    stat_columns = COMBO_MARKET_COMPONENTS.get(market_type, (market_type,))
    values: list[float] = []
    missing_columns: list[str] = []
    for component in stat_columns:
        stat_column = BASE_STAT_COLUMNS.get(component)
        if not stat_column or stat_column not in candidates.columns:
            missing_columns.append(component)
            continue
        actual = _safe_float(candidates.iloc[0].get(stat_column))
        if actual is None:
            missing_columns.append(component)
        else:
            values.append(actual)
    if missing_columns:
        return None, "stat_column_missing:" + ",".join(missing_columns)
    return float(sum(values)), ""


def _existing_final_feedback_keys(feedback: pd.DataFrame) -> set[str]:
    if feedback.empty or "grade_key" not in feedback.columns:
        return set()
    result = feedback.get("result", pd.Series("", index=feedback.index)).map(lambda value: _safe_text(value).lower())
    if "graded_result" in feedback.columns:
        graded = feedback["graded_result"].map(lambda value: _safe_text(value).lower())
        result = result.mask(result.eq(""), graded)
    return set(feedback.loc[result.isin(FINAL_RESULTS), "grade_key"].map(_safe_text))


def _build_feedback_rows(
    *,
    board: pd.DataFrame,
    stats: pd.DataFrame,
    final_game_ids: set[str],
    prediction_date: str,
) -> tuple[pd.DataFrame, Counter[str]]:
    rows: list[dict[str, Any]] = []
    skip_reasons: Counter[str] = Counter()
    for _, row in board.iterrows():
        market_type = _row_market_type(row)
        selection = _selection(row)
        line = _line_value(row)
        if market_type not in SUPPORTED_MARKETS:
            skip_reasons["unsupported_market"] += 1
            continue
        if selection not in {"over", "under"}:
            skip_reasons["unsupported_selection"] += 1
            continue
        if line is None:
            skip_reasons["missing_line"] += 1
            continue
        game_id = _id_key(row.get("game_id"))
        if game_id and final_game_ids and game_id not in final_game_ids:
            skip_reasons["game_not_final"] += 1
            continue
        actual_value, reason = _stat_value_for_market(row, stats, market_type)
        if actual_value is None:
            skip_reasons[reason or "actual_stats_not_found"] += 1
            continue
        result = _line_result(selection, actual_value, float(line))
        if result not in FINAL_RESULTS:
            skip_reasons["unsupported_selection"] += 1
            continue
        player_name = _safe_text(row.get("player_name")) or _entity_name(row)
        team = _team_abbr(row)
        grade_key = _grade_key(row, prediction_date=prediction_date, market_type=market_type, selection=selection, line=float(line))
        rows.append(
            {
                "grade_key": grade_key,
                "prediction_date": prediction_date,
                "market_type": market_type,
                "entity_name": _entity_name(row),
                "player_name": player_name,
                "player_id": _safe_text(row.get("player_id")),
                "team": team,
                "team_abbr": team,
                "opponent": _safe_text(row.get("opponent")).upper(),
                "game_id": _safe_text(row.get("game_id")),
                "selection": selection,
                "sportsbook_line": float(line),
                "line": float(line),
                "actual_value": round(float(actual_value), 4),
                "result": result,
                "graded_result": result,
                "is_win": 1 if result == "win" else 0,
                "is_push": 1 if result == "push" else 0,
                "is_loss": 1 if result == "loss" else 0,
                "source": "closed_slate_actual_prefill",
            }
        )
    if not rows:
        return pd.DataFrame(), skip_reasons
    feedback_rows = pd.DataFrame(rows)
    return feedback_rows.drop_duplicates(subset=["grade_key"], keep="last").reset_index(drop=True), skip_reasons


def prefill_actual_feedback_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    dry_run: bool = False,
    ai_factory: Callable[[Path], CourtVisionAI] | None = None,
) -> dict[str, Any]:
    runtime_root_path = Path(runtime_root)
    board_path = runtime_root_path / "operator" / f"full_market_board_{prediction_date}.csv"
    feedback_path = runtime_root_path / "history" / "result_feedback.csv"
    board = _read_csv(board_path)
    if board.empty:
        return {
            "status": "no_full_market_board_rows",
            "prediction_date": prediction_date,
            "board_path": str(board_path),
            "feedback_path": str(feedback_path),
            "board_rows": 0,
            "prefilled_rows": 0,
            "dry_run": bool(dry_run),
        }
    if "prediction_date" in board.columns:
        board = board[board["prediction_date"].astype(str).eq(str(prediction_date))].copy()

    ai = (ai_factory or (lambda out_dir: CourtVisionAI(out_dir=str(out_dir))))(_runtime_outputs_root(runtime_root_path))
    if hasattr(ai, "runtime_dir"):
        ai.runtime_dir = runtime_root_path
    if hasattr(ai, "runtime_history_dir"):
        ai.runtime_history_dir = runtime_root_path / "history"
    if hasattr(ai, "feedback_path"):
        ai.feedback_path = feedback_path

    try:
        client = ai._get_client()
        raw_games = client.get_games(str(prediction_date))
        raw_stats = client.get_stats(str(prediction_date), str(prediction_date))
        stats = ai._normalize_stats(raw_stats)
    except Exception as exc:
        return {
            "status": "provider_fetch_failed",
            "prediction_date": prediction_date,
            "board_path": str(board_path),
            "feedback_path": str(feedback_path),
            "board_rows": int(len(board)),
            "prefilled_rows": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "dry_run": bool(dry_run),
        }

    games = raw_games.copy() if isinstance(raw_games, pd.DataFrame) else pd.DataFrame()
    stats = stats.copy() if isinstance(stats, pd.DataFrame) else pd.DataFrame()
    final_game_ids = _final_game_ids(games)
    if stats.empty:
        return {
            "status": "provider_stats_empty",
            "prediction_date": prediction_date,
            "board_path": str(board_path),
            "feedback_path": str(feedback_path),
            "board_rows": int(len(board)),
            "provider_stat_rows": 0,
            "final_game_ids": sorted(final_game_ids),
            "prefilled_rows": 0,
            "dry_run": bool(dry_run),
        }
    if not final_game_ids:
        return {
            "status": "provider_final_games_missing",
            "prediction_date": prediction_date,
            "board_path": str(board_path),
            "feedback_path": str(feedback_path),
            "board_rows": int(len(board)),
            "provider_stat_rows": int(len(stats)),
            "prefilled_rows": 0,
            "dry_run": bool(dry_run),
        }

    feedback_rows, skip_reasons = _build_feedback_rows(
        board=board,
        stats=stats,
        final_game_ids=final_game_ids,
        prediction_date=prediction_date,
    )
    existing_feedback = _read_csv(feedback_path)
    existing_final_keys = _existing_final_feedback_keys(existing_feedback)
    if not feedback_rows.empty and existing_final_keys:
        feedback_rows = feedback_rows[~feedback_rows["grade_key"].map(_safe_text).isin(existing_final_keys)].copy()

    prefilled_rows = int(len(feedback_rows))
    if prefilled_rows and not dry_run:
        feedback_path.parent.mkdir(parents=True, exist_ok=True)
        ai._append_history(feedback_path, feedback_rows)

    return {
        "status": "ok" if prefilled_rows else "already_prefilled_or_no_gradeable_rows",
        "prediction_date": prediction_date,
        "board_path": str(board_path),
        "feedback_path": str(feedback_path),
        "board_rows": int(len(board)),
        "provider_stat_rows": int(len(stats)),
        "final_game_ids": sorted(final_game_ids),
        "prefilled_rows": prefilled_rows,
        "existing_final_rows_skipped": int(len(existing_final_keys)),
        "skip_reasons": dict(sorted(skip_reasons.items())),
        "dry_run": bool(dry_run),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely prefill result_feedback.csv actuals for an existing closed-slate full-market board."
    )
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = prefill_actual_feedback_for_date(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        dry_run=args.dry_run,
    )
    print(f"prefill_status={result.get('status')}")
    print(f"prediction_date={result.get('prediction_date')}")
    print(f"board_rows={result.get('board_rows', 0)}")
    print(f"provider_stat_rows={result.get('provider_stat_rows', 0)}")
    print(f"prefilled_rows={result.get('prefilled_rows', 0)}")
    for reason, count in result.get("skip_reasons", {}).items():
        print(f"skip_reason={reason},{count}")
    if result.get("error"):
        print(f"error={result['error']}")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
