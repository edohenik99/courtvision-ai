"""Grade saved live 1+ HR odds snapshots against local results."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
DEFAULT_ODDS_CSV = DATA_DIR / "live_hr_props_master.csv"
DEFAULT_RESULTS_CSV = DATA_DIR / "live_hr_results.csv"
DEFAULT_OUTPUT_CSV = DATA_DIR / "live_hr_props_graded.csv"

ODDS_REQUIRED_COLUMNS = ("event_id", "player", "side", "price", "point")
ODDS_DATE_COLUMNS = ("commence_time",)
RESULTS_REQUIRED_COLUMNS = (
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
)
RESULT_REASON_COLUMN = "result_reason"
VOID_STATUS = "void"
VOID_CANDIDATE_STATUS = "void_candidate"
MANUAL_REVIEW_STATUS = "manual_review_required"
NON_GRADEABLE_STATUSES = {
    VOID_STATUS,
    VOID_CANDIDATE_STATUS,
    MANUAL_REVIEW_STATUS,
}
GRADE_COLUMNS = (
    "actual_home_runs",
    "game_status",
    "result_reason",
    "result",
    "implied_probability",
    "stake_1u",
    "profit_1u",
    "grade_status",
)


class GradingInputError(ValueError):
    """Raised when a grading input is missing or invalid."""


@dataclass(frozen=True)
class ResultRecord:
    actual_home_runs: int | None
    game_status: str
    result_reason: str


@dataclass(frozen=True)
class GradeSummary:
    total_rows: int
    graded_rows: int
    missing_result_rows: int
    excluded_void_rows: int
    void_candidate_rows: int
    manual_review_rows: int
    wins: int
    losses: int
    total_profit_1u: float

    @property
    def roi(self) -> float:
        return self.total_profit_1u / self.graded_rows if self.graded_rows else 0.0


def implied_probability(american_odds: int | float | Decimal) -> float:
    """Convert non-zero American odds to implied probability."""

    odds = Decimal(str(american_odds))
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return float(Decimal(100) / (odds + Decimal(100)))
    absolute_odds = abs(odds)
    return float(absolute_odds / (absolute_odds + Decimal(100)))


def win_profit_1u(american_odds: int | float | Decimal) -> float:
    """Return profit, excluding stake, for a winning one-unit wager."""

    odds = Decimal(str(american_odds))
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return float(odds / Decimal(100))
    return float(Decimal(100) / abs(odds))


def _read_csv(
    path: Path, required_columns: tuple[str, ...], label: str
) -> tuple[list[str], list[dict[str, str | None]]]:
    if not path.is_file():
        raise GradingInputError(f"{label} file does not exist: {path}")

    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or ())
            missing = [column for column in required_columns if column not in columns]
            if missing:
                raise GradingInputError(
                    f"{label} CSV missing required columns: {', '.join(missing)}"
                )

            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise GradingInputError(
                        f"{label} CSV row {row_number} has more values than its schema"
                    )
                rows.append({column: row.get(column, "") for column in columns})
    except OSError as exc:
        raise GradingInputError(f"could not read {label} CSV: {exc}") from exc

    return columns, rows


def _required_text(
    row: dict[str, str | None], column: str, label: str, row_number: int
) -> str:
    raw_value = row.get(column)
    if raw_value is None or not str(raw_value).strip():
        raise GradingInputError(
            f"{label} CSV row {row_number} has blank required field '{column}'"
        )
    return str(raw_value).strip()


def _decimal(value: str, column: str, label: str, row_number: int) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise GradingInputError(
            f"{label} CSV row {row_number} has invalid {column}: {value!r}"
        ) from exc
    if not number.is_finite():
        raise GradingInputError(
            f"{label} CSV row {row_number} has invalid {column}: {value!r}"
        )
    return number


def _validate_target_date(target_date: str) -> str:
    try:
        parsed_date = date.fromisoformat(target_date)
    except ValueError as exc:
        raise GradingInputError(
            f"invalid date {target_date!r}; expected YYYY-MM-DD"
        ) from exc
    if parsed_date.isoformat() != target_date:
        raise GradingInputError(
            f"invalid date {target_date!r}; expected YYYY-MM-DD"
        )
    return target_date


def _commence_date(value: str, row_number: int) -> str:
    try:
        parsed_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed_time.date().isoformat()
    except ValueError as exc:
        raise GradingInputError(
            f"odds CSV row {row_number} has invalid commence_time: {value!r}"
        ) from exc


def _load_results(
    path: Path,
    event_ids: set[str] | None = None,
    require_game_status: bool = False,
) -> dict[tuple[str, str], ResultRecord]:
    _, rows = _read_csv(path, RESULTS_REQUIRED_COLUMNS, "results")
    results: dict[tuple[str, str], ResultRecord] = {}

    for row_number, row in enumerate(rows, start=2):
        raw_event_id = str(row.get("event_id") or "").strip()
        if event_ids is not None and raw_event_id not in event_ids:
            continue

        event_id = _required_text(row, "event_id", "results", row_number)
        player = _required_text(row, "player", "results", row_number)
        game_status = (
            _required_text(row, "game_status", "results", row_number)
            if require_game_status
            else str(row.get("game_status") or "").strip()
        )
        result_reason = str(row.get(RESULT_REASON_COLUMN) or "").strip()
        if game_status.casefold() in NON_GRADEABLE_STATUSES:
            parsed_home_runs: int | None = None
        else:
            home_runs_value = _required_text(
                row, "actual_home_runs", "results", row_number
            )
            home_runs_decimal = _decimal(
                home_runs_value, "actual_home_runs", "results", row_number
            )
            if (
                home_runs_decimal < 0
                or home_runs_decimal != home_runs_decimal.to_integral_value()
            ):
                raise GradingInputError(
                    f"results CSV row {row_number} has invalid actual_home_runs: "
                    f"{home_runs_value!r}"
                )
            parsed_home_runs = int(home_runs_decimal)

        key = (event_id, player)
        if key in results:
            raise GradingInputError(
                "results CSV contains duplicate event_id + player key: "
                f"{event_id!r} + {player!r}"
            )
        results[key] = ResultRecord(
            actual_home_runs=parsed_home_runs,
            game_status=game_status,
            result_reason=result_reason,
        )

    return results


def grade_live_hr_results(
    odds_path: str | Path = DEFAULT_ODDS_CSV,
    results_path: str | Path = DEFAULT_RESULTS_CSV,
    output_path: str | Path | None = None,
    target_date: str | None = None,
) -> GradeSummary:
    """Grade local odds, write a separate CSV, and return aggregate results."""

    odds_csv = Path(odds_path)
    results_csv = Path(results_path)
    if target_date:
        target_date = _validate_target_date(target_date)
    output_csv = (
        Path(output_path) if output_path else default_output_path(target_date)
    )
    required_odds_columns = (
        ODDS_REQUIRED_COLUMNS + ODDS_DATE_COLUMNS
        if target_date
        else ODDS_REQUIRED_COLUMNS
    )
    odds_columns, odds_rows = _read_csv(
        odds_csv, required_odds_columns, "odds"
    )

    if target_date:
        filtered_odds_rows: list[dict[str, str | None]] = []
        for row_number, row in enumerate(odds_rows, start=2):
            commence_time = _required_text(
                row, "commence_time", "odds", row_number
            )
            if _commence_date(commence_time, row_number) == target_date:
                filtered_odds_rows.append(row)
        odds_rows = filtered_odds_rows
        if not odds_rows:
            raise GradingInputError(f"odds CSV has no rows for date {target_date}")

    target_event_ids = (
        {
            str(row.get("event_id") or "").strip()
            for row in odds_rows
            if str(row.get("event_id") or "").strip()
        }
        if target_date
        else None
    )
    results = _load_results(
        results_csv,
        event_ids=target_event_ids,
        require_game_status=target_date is not None,
    )

    output_columns = [column for column in odds_columns if column not in GRADE_COLUMNS]
    output_columns.extend(GRADE_COLUMNS)
    graded_output: list[dict[str, object]] = []
    wins = 0
    losses = 0
    missing = 0
    excluded_void = 0
    void_candidates = 0
    manual_reviews = 0
    total_profit = 0.0

    for row_number, row in enumerate(odds_rows, start=2):
        event_id = _required_text(row, "event_id", "odds", row_number)
        player = _required_text(row, "player", "odds", row_number)
        matched_result = results.get((event_id, player))
        if matched_result is not None:
            non_gradeable_status = matched_result.game_status.casefold()
            if non_gradeable_status in NON_GRADEABLE_STATUSES:
                if non_gradeable_status == VOID_STATUS:
                    excluded_void += 1
                elif non_gradeable_status == VOID_CANDIDATE_STATUS:
                    void_candidates += 1
                elif non_gradeable_status == MANUAL_REVIEW_STATUS:
                    manual_reviews += 1
                output_row: dict[str, object] = dict(row)
                output_row.update(
                    {
                        "actual_home_runs": "",
                        "game_status": matched_result.game_status,
                        "result_reason": matched_result.result_reason,
                        "result": "",
                        "implied_probability": "",
                        "stake_1u": "",
                        "profit_1u": "",
                        "grade_status": non_gradeable_status,
                    }
                )
                graded_output.append(output_row)
                continue

        side = _required_text(row, "side", "odds", row_number)
        point_value = _required_text(row, "point", "odds", row_number)
        price_value = _required_text(row, "price", "odds", row_number)
        point = _decimal(point_value, "point", "odds", row_number)
        price = _decimal(price_value, "price", "odds", row_number)

        if side != "Over" or point != Decimal("0.5"):
            raise GradingInputError(
                f"odds CSV row {row_number} is not an Over 0.5 HR market"
            )
        if price == 0:
            raise GradingInputError(
                f"odds CSV row {row_number} has invalid price: {price_value!r}"
            )

        output_row: dict[str, object] = dict(row)
        output_row.update(
            {
                "actual_home_runs": "",
                "game_status": "",
                "result_reason": "",
                "result": "",
                "implied_probability": implied_probability(price),
                "stake_1u": 1.0,
                "profit_1u": "",
                "grade_status": "missing_result",
            }
        )

        if matched_result is None:
            missing += 1
        else:
            actual_home_runs = matched_result.actual_home_runs
            game_status = matched_result.game_status
            if actual_home_runs is None:
                raise GradingInputError(
                    "non-void result unexpectedly has blank actual_home_runs: "
                    f"{event_id!r} + {player!r}"
                )
            won = actual_home_runs >= 1
            profit = win_profit_1u(price) if won else -1.0
            output_row.update(
                {
                    "actual_home_runs": actual_home_runs,
                    "game_status": game_status,
                    "result_reason": matched_result.result_reason,
                    "result": "win" if won else "loss",
                    "profit_1u": profit,
                    "grade_status": "graded",
                }
            )
            wins += int(won)
            losses += int(not won)
            total_profit += profit

        graded_output.append(output_row)

    try:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=output_columns)
            writer.writeheader()
            writer.writerows(graded_output)
    except OSError as exc:
        raise GradingInputError(f"could not write graded CSV: {exc}") from exc

    return GradeSummary(
        total_rows=len(odds_rows),
        graded_rows=wins + losses,
        missing_result_rows=missing,
        excluded_void_rows=excluded_void,
        void_candidate_rows=void_candidates,
        manual_review_rows=manual_reviews,
        wins=wins,
        losses=losses,
        total_profit_1u=total_profit,
    )


def default_output_path(target_date: str | None) -> Path:
    if not target_date:
        return DEFAULT_OUTPUT_CSV
    return DATA_DIR / f"live_hr_grades_{target_date.replace('-', '')}.csv"


def print_summary(summary: GradeSummary, target_date: str | None = None) -> None:
    if target_date:
        print(f"Target date: {target_date}")
    print(f"Total rows: {summary.total_rows}")
    print(f"Graded rows: {summary.graded_rows}")
    print(f"Missing result rows: {summary.missing_result_rows}")
    print(f"Excluded void rows: {summary.excluded_void_rows}")
    print(f"Void candidate rows: {summary.void_candidate_rows}")
    print(f"Manual review rows: {summary.manual_review_rows}")
    print(f"Wins: {summary.wins}")
    print(f"Losses: {summary.losses}")
    print(f"Total profit_1u: {summary.total_profit_1u:.2f}")
    print(f"ROI: {summary.roi:.2%}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grade saved live 1+ HR odds against a local results CSV."
    )
    parser.add_argument("--odds-csv", type=Path, default=DEFAULT_ODDS_CSV)
    parser.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    parser.add_argument("--output-csv", type=Path)
    parser.add_argument("--date", dest="target_date")
    args = parser.parse_args(argv)

    try:
        target_date = (
            _validate_target_date(args.target_date) if args.target_date else None
        )
        summary = grade_live_hr_results(
            odds_path=args.odds_csv,
            results_path=args.results_csv,
            output_path=args.output_csv,
            target_date=target_date,
        )
    except GradingInputError as exc:
        date_context = f" for date {args.target_date}" if args.target_date else ""
        print(f"ERROR{date_context}: {exc}", file=sys.stderr)
        return 1

    print_summary(summary, target_date=target_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
