"""Phase 5B The Odds API market validation board.

This entrypoint is research-only. It validates normalized provider rows and
writes isolated research diagnostics. It does not create MarketProp rows, call
Kelly, create Elite rows, or write operator betting boards.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.providers import the_odds_api_provider


MARKET_VALIDATION_OK = "MARKET_VALIDATION_OK"
MARKET_VALIDATION_NO_EVENTS = "MARKET_VALIDATION_NO_EVENTS"
MARKET_VALIDATION_NO_PROPS = "MARKET_VALIDATION_NO_PROPS"
MARKET_VALIDATION_PROVIDER_UNAVAILABLE = "MARKET_VALIDATION_PROVIDER_UNAVAILABLE"
MARKET_VALIDATION_SCHEMA_INVALID = "MARKET_VALIDATION_SCHEMA_INVALID"
MARKET_VALIDATION_PARTIAL_MARKET_COVERAGE = "MARKET_VALIDATION_PARTIAL_MARKET_COVERAGE"

PROVIDER_THE_ODDS_API = "the_odds_api"
DEFAULT_MARKETS = "player_points,player_rebounds,player_assists"
DEFAULT_OUTPUT_DIR = Path("outputs/runtime/research")
DEFAULT_TIMEZONE = "America/Toronto"

BOARD_COLUMNS = [
    "provider",
    "provider_event_id",
    "home_team",
    "away_team",
    "game_date",
    "commence_time_utc",
    "commence_time_local",
    "player_name",
    "market_type",
    "side",
    "line",
    "american_odds",
    "sportsbook",
    "updated_at",
    "source",
    "eligible_for_betting",
]

MISSING_FIELD_COLUMNS = [
    "player_name",
    "market_type",
    "side",
    "line",
    "american_odds",
    "sportsbook",
    "updated_at",
]

DUPLICATE_KEY_COLUMNS = [
    "provider_event_id",
    "player_name",
    "market_type",
    "side",
    "line",
    "sportsbook",
]

PAIR_KEY_COLUMNS = [
    "provider_event_id",
    "player_name",
    "market_type",
    "line",
    "sportsbook",
]


@dataclass(slots=True)
class MarketValidationResult:
    status: str
    board_path: Path
    summary_path: Path
    diagnostics_path: Path
    diagnostics: dict[str, Any]


def run_market_validation(
    *,
    target_date: str,
    provider: str = PROVIDER_THE_ODDS_API,
    markets: str | list[str] | tuple[str, ...] = DEFAULT_MARKETS,
    max_events: int = 1,
    timezone_name: str = DEFAULT_TIMEZONE,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    props_loader: Callable[..., Any] | None = None,
) -> MarketValidationResult:
    """Run the research-only market validation board for one date."""
    target_date_text = _validate_date(target_date)
    provider_name = _validate_provider(provider)
    requested_markets = _csv_items(markets)
    output_dir_path = Path(output_dir)
    runtime_root = output_dir_path.parent
    diagnostics_dir = runtime_root / "diagnostics"

    board_path = output_dir_path / f"market_validation_board_{target_date_text}.csv"
    summary_path = output_dir_path / f"market_validation_summary_{target_date_text}.txt"
    diagnostics_path = diagnostics_dir / f"market_validation_{target_date_text}.json"
    provider_diagnostics_path = (
        diagnostics_dir / f"the_odds_api_provider_{target_date_text}.json"
    )

    output_dir_path.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    provider_diag_mtime_before = _path_mtime_ns(provider_diagnostics_path)
    provider_error = ""
    raw_payload: Any = None
    loader = props_loader or the_odds_api_provider.get_player_props_for_date

    try:
        raw_payload = loader(
            target_date_text,
            requested_markets,
            max_events=max(0, int(max_events)),
            timezone=timezone_name,
            runtime_root=runtime_root,
        )
    except Exception as exc:
        provider_error = f"{type(exc).__name__}: {exc}"
        raw_payload = pd.DataFrame(columns=BOARD_COLUMNS)

    provider_diagnostics = _read_provider_diagnostics_if_fresh(
        provider_diagnostics_path,
        provider_diag_mtime_before,
    )
    source_is_dataframe = isinstance(raw_payload, pd.DataFrame)
    source_df = raw_payload.copy() if source_is_dataframe else pd.DataFrame()
    source_columns = list(source_df.columns)
    schema_missing_columns = [
        column for column in BOARD_COLUMNS if column not in source_columns
    ]
    eligible_for_betting_any_true = _eligible_any_true(source_df)

    board = _board_dataframe(source_df)
    board.to_csv(board_path, index=False)

    metrics = _validation_metrics(
        board=board,
        requested_markets=requested_markets,
        eligible_for_betting_any_true=eligible_for_betting_any_true,
    )
    status = _validation_status(
        row_count=metrics["row_count"],
        missing_requested_markets=metrics["missing_requested_markets"],
        schema_missing_columns=schema_missing_columns,
        source_is_dataframe=source_is_dataframe,
        eligible_for_betting_any_true=eligible_for_betting_any_true,
        provider_diagnostics=provider_diagnostics,
        provider_error=provider_error,
    )

    diagnostics = _diagnostics_payload(
        target_date=target_date_text,
        provider=provider_name,
        timezone_name=timezone_name,
        max_events=max(0, int(max_events)),
        status=status,
        metrics=metrics,
        source_is_dataframe=source_is_dataframe,
        source_columns=source_columns,
        schema_missing_columns=schema_missing_columns,
        provider_error=provider_error,
        provider_diagnostics=provider_diagnostics,
        board_path=board_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
    )
    _write_summary(summary_path, diagnostics)
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")

    return MarketValidationResult(
        status=status,
        board_path=board_path,
        summary_path=summary_path,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def _board_dataframe(source_df: pd.DataFrame) -> pd.DataFrame:
    row_count = len(source_df.index)
    data: dict[str, Any] = {}
    for column in BOARD_COLUMNS:
        if column in source_df.columns:
            data[column] = source_df[column]
        else:
            data[column] = [pd.NA] * row_count

    board = pd.DataFrame(data, columns=BOARD_COLUMNS)
    board["eligible_for_betting"] = False
    return board


def _validation_metrics(
    *,
    board: pd.DataFrame,
    requested_markets: list[str],
    eligible_for_betting_any_true: bool,
) -> dict[str, Any]:
    row_count = int(len(board.index))
    returned_markets = _ordered_non_missing_values(board, "market_type")
    missing_requested_markets = [
        market for market in requested_markets if market not in set(returned_markets)
    ]
    over_under = _over_under_health(board)
    updated_at_min, updated_at_max = _updated_at_bounds(board)
    missing_field_counts = {
        f"rows_missing_{column}": _missing_count(board, column)
        for column in MISSING_FIELD_COLUMNS
    }

    metrics: dict[str, Any] = {
        "row_count": row_count,
        "unique_player_count": len(_ordered_non_missing_values(board, "player_name")),
        "unique_market_count": len(returned_markets),
        "unique_sportsbook_count": len(_ordered_non_missing_values(board, "sportsbook")),
        "requested_markets": requested_markets,
        "returned_markets": returned_markets,
        "missing_requested_markets": missing_requested_markets,
        "sportsbook_counts": _value_counts(board, "sportsbook"),
        "market_counts": _value_counts(board, "market_type"),
        "sportsbook_market_counts": _sportsbook_market_counts(board),
        "duplicate_key_count": _duplicate_key_count(board),
        "updated_at_min": updated_at_min,
        "updated_at_max": updated_at_max,
        "eligible_for_betting_any_true": bool(eligible_for_betting_any_true),
    }
    metrics.update(missing_field_counts)
    metrics.update(over_under)
    return metrics


def _validation_status(
    *,
    row_count: int,
    missing_requested_markets: list[str],
    schema_missing_columns: list[str],
    source_is_dataframe: bool,
    eligible_for_betting_any_true: bool,
    provider_diagnostics: dict[str, Any],
    provider_error: str,
) -> str:
    provider_status = _clean_text(provider_diagnostics.get("provider_status"))
    if provider_error or _provider_unavailable(provider_status):
        return MARKET_VALIDATION_PROVIDER_UNAVAILABLE
    if (not source_is_dataframe) or schema_missing_columns or eligible_for_betting_any_true:
        return MARKET_VALIDATION_SCHEMA_INVALID
    if row_count == 0 and _as_int(provider_diagnostics.get("target_date_events_count")) == 0:
        return MARKET_VALIDATION_NO_EVENTS
    if row_count == 0:
        return MARKET_VALIDATION_NO_PROPS
    if missing_requested_markets:
        return MARKET_VALIDATION_PARTIAL_MARKET_COVERAGE
    return MARKET_VALIDATION_OK


def _diagnostics_payload(
    *,
    target_date: str,
    provider: str,
    timezone_name: str,
    max_events: int,
    status: str,
    metrics: dict[str, Any],
    source_is_dataframe: bool,
    source_columns: list[str],
    schema_missing_columns: list[str],
    provider_error: str,
    provider_diagnostics: dict[str, Any],
    board_path: Path,
    summary_path: Path,
    diagnostics_path: Path,
) -> dict[str, Any]:
    provider_status = _clean_text(provider_diagnostics.get("provider_status"))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "date": target_date,
        "target_date": target_date,
        "provider": provider,
        "provider_status": provider_status,
        "provider_error": provider_error,
        "timezone": timezone_name,
        "max_events": int(max_events),
        "source_is_dataframe": bool(source_is_dataframe),
        "source_columns": source_columns,
        "schema_missing_required_columns": schema_missing_columns,
        "eligible_for_betting_all_board_rows_false": True,
        "betting_mode_integrated": False,
        "market_prop_rows_created": 0,
        "elite_rows_created": 0,
        "kelly_called": False,
        "operator_artifacts_written": [],
        "operator_betting_boards_written": [],
        "artifacts": {
            "market_validation_board_csv": str(board_path),
            "market_validation_summary_txt": str(summary_path),
            "market_validation_diagnostics_json": str(diagnostics_path),
        },
        "provider_diagnostics": provider_diagnostics,
    }
    payload.update(metrics)
    return payload


def _write_summary(path: Path, diagnostics: dict[str, Any]) -> None:
    missing_lines = [
        f"  {key}: {diagnostics[key]}"
        for key in [
            "rows_missing_player_name",
            "rows_missing_market_type",
            "rows_missing_side",
            "rows_missing_line",
            "rows_missing_american_odds",
            "rows_missing_sportsbook",
            "rows_missing_updated_at",
        ]
    ]
    coverage_lines = _coverage_summary_lines(diagnostics["sportsbook_market_counts"])
    if not coverage_lines:
        coverage_lines = ["  none"]

    lines = [
        f"Market Validation Board - {diagnostics['date']}",
        f"status: {diagnostics['status']}",
        f"date: {diagnostics['date']}",
        f"provider: {diagnostics['provider']}",
        f"provider_status: {diagnostics['provider_status'] or 'unknown'}",
        f"row_count: {diagnostics['row_count']}",
        f"unique_player_count: {diagnostics['unique_player_count']}",
        f"markets_returned: {_counts_inline(diagnostics['market_counts'])}",
        f"missing_markets: {_list_inline(diagnostics['missing_requested_markets'])}",
        f"sportsbooks: {_counts_inline(diagnostics['sportsbook_counts'])}",
        "sportsbook_market_coverage:",
        *coverage_lines,
        f"duplicate_key_count: {diagnostics['duplicate_key_count']}",
        "missing_field_counts:",
        *missing_lines,
        "over_under_pairing_health:",
        f"  over_count: {diagnostics['over_count']}",
        f"  under_count: {diagnostics['under_count']}",
        f"  over_under_pair_count: {diagnostics['over_under_pair_count']}",
        f"  orphan_over_count: {diagnostics['orphan_over_count']}",
        f"  orphan_under_count: {diagnostics['orphan_under_count']}",
        f"updated_at_min: {diagnostics['updated_at_min'] or 'none'}",
        f"updated_at_max: {diagnostics['updated_at_max'] or 'none'}",
        f"eligible_for_betting_any_true: {diagnostics['eligible_for_betting_any_true']}",
        "",
        "WARNING: Betting Mode is not integrated by this validation board.",
        "Research-only output; no MarketProp, Elite, Kelly, or operator betting boards were created.",
    ]
    if diagnostics["schema_missing_required_columns"]:
        lines.append(
            "schema_missing_required_columns: "
            + ", ".join(diagnostics["schema_missing_required_columns"])
        )
    if diagnostics["provider_error"]:
        lines.append(f"provider_error: {diagnostics['provider_error']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _coverage_summary_lines(payload: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    for sportsbook in sorted(payload):
        market_counts = payload[sportsbook]
        counts = ", ".join(
            f"{market}={market_counts[market]}" for market in sorted(market_counts)
        )
        lines.append(f"  {sportsbook}: {counts}")
    return lines


def _counts_inline(payload: dict[str, int]) -> str:
    if not payload:
        return "none"
    return ", ".join(f"{key} ({payload[key]})" for key in sorted(payload))


def _list_inline(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _sportsbook_market_counts(board: pd.DataFrame) -> dict[str, dict[str, int]]:
    if board.empty:
        return {}
    frame = board.copy()
    frame["sportsbook"] = frame["sportsbook"].map(_clean_text)
    frame["market_type"] = frame["market_type"].map(_clean_text)
    frame = frame[(frame["sportsbook"] != "") & (frame["market_type"] != "")]
    if frame.empty:
        return {}

    grouped = frame.groupby(["sportsbook", "market_type"], dropna=False).size()
    payload: dict[str, dict[str, int]] = {}
    for (sportsbook, market), count in grouped.items():
        payload.setdefault(str(sportsbook), {})[str(market)] = int(count)
    return {
        sportsbook: dict(sorted(markets.items()))
        for sportsbook, markets in sorted(payload.items())
    }


def _value_counts(board: pd.DataFrame, column: str) -> dict[str, int]:
    if column not in board.columns or board.empty:
        return {}
    values = [_clean_text(value) for value in board[column].tolist()]
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _ordered_non_missing_values(board: pd.DataFrame, column: str) -> list[str]:
    if column not in board.columns:
        return []
    values: list[str] = []
    seen: set[str] = set()
    for raw_value in board[column].tolist():
        value = _clean_text(raw_value)
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _duplicate_key_count(board: pd.DataFrame) -> int:
    if board.empty:
        return 0
    subset = [column for column in DUPLICATE_KEY_COLUMNS if column in board.columns]
    if len(subset) != len(DUPLICATE_KEY_COLUMNS):
        return 0
    return int(board.duplicated(subset=subset, keep="first").sum())


def _over_under_health(board: pd.DataFrame) -> dict[str, int]:
    sides_by_pair: dict[tuple[str, ...], set[str]] = {}
    over_count = 0
    under_count = 0

    for _, row in board.iterrows():
        side = _clean_text(row.get("side")).lower()
        if side not in {"over", "under"}:
            continue
        if side == "over":
            over_count += 1
        else:
            under_count += 1

        pair_key = tuple(_clean_text(row.get(column)) for column in PAIR_KEY_COLUMNS)
        sides_by_pair.setdefault(pair_key, set()).add(side)

    over_under_pair_count = 0
    orphan_over_count = 0
    orphan_under_count = 0
    for sides in sides_by_pair.values():
        if sides == {"over", "under"}:
            over_under_pair_count += 1
        elif sides == {"over"}:
            orphan_over_count += 1
        elif sides == {"under"}:
            orphan_under_count += 1

    return {
        "over_count": int(over_count),
        "under_count": int(under_count),
        "over_under_pair_count": int(over_under_pair_count),
        "orphan_over_count": int(orphan_over_count),
        "orphan_under_count": int(orphan_under_count),
    }


def _updated_at_bounds(board: pd.DataFrame) -> tuple[str, str]:
    if "updated_at" not in board.columns or board.empty:
        return "", ""
    values = [_clean_text(value) for value in board["updated_at"].tolist()]
    values = [value for value in values if value]
    if not values:
        return "", ""

    parsed = pd.to_datetime(pd.Series(values), errors="coerce", utc=True)
    valid = parsed.dropna()
    if valid.empty:
        return min(values), max(values)
    return _format_timestamp(valid.min()), _format_timestamp(valid.max())


def _format_timestamp(value: Any) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return _clean_text(value)


def _missing_count(board: pd.DataFrame, column: str) -> int:
    if column not in board.columns:
        return int(len(board.index))
    return int(sum(_is_missing(value) for value in board[column].tolist()))


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _eligible_any_true(df: pd.DataFrame) -> bool:
    if "eligible_for_betting" not in df.columns or df.empty:
        return False
    return any(_truthy(value) for value in df["eligible_for_betting"].tolist())


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return bool(value) and not pd.isna(value)
        except TypeError:
            return bool(value)
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


def _provider_unavailable(provider_status: str) -> bool:
    if not provider_status:
        return False
    return provider_status not in {"ok", "cached_fallback"}


def _read_provider_diagnostics_if_fresh(
    path: Path,
    previous_mtime_ns: int | None,
) -> dict[str, Any]:
    current_mtime_ns = _path_mtime_ns(path)
    if current_mtime_ns is None:
        return {}
    if previous_mtime_ns is not None and current_mtime_ns == previous_mtime_ns:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _path_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _csv_items(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _validate_date(value: str) -> str:
    text = str(value).strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--date must be in YYYY-MM-DD format") from exc
    return text


def _validate_provider(value: str) -> str:
    provider = str(value).strip()
    if provider != PROVIDER_THE_ODDS_API:
        raise ValueError("Market validation currently supports only --provider the_odds_api")
    return provider


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run The Odds API research-only market validation board."
    )
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--provider",
        default=PROVIDER_THE_ODDS_API,
        choices=[PROVIDER_THE_ODDS_API],
        help="Odds provider to validate.",
    )
    parser.add_argument(
        "--markets",
        default=DEFAULT_MARKETS,
        help="Comma-separated player-prop market keys.",
    )
    parser.add_argument("--max-events", type=int, default=1, help="Maximum events to probe.")
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone used for local game-date matching.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Research output directory. Defaults to outputs/runtime/research.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = run_market_validation(
            target_date=args.date,
            provider=args.provider,
            markets=args.markets,
            max_events=args.max_events,
            timezone_name=args.timezone,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"status: {result.status}")
    print(f"board: {result.board_path}")
    print(f"summary: {result.summary_path}")
    print(f"diagnostics: {result.diagnostics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
