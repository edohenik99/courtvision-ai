"""Grade saved live 1+ HR odds snapshots against local results."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
DEFAULT_ODDS_CSV = DATA_DIR / "live_hr_props_master.csv"
DEFAULT_RESULTS_CSV = DATA_DIR / "live_hr_results.csv"
DEFAULT_OUTPUT_CSV = DATA_DIR / "live_hr_props_graded.csv"

ODDS_REQUIRED_COLUMNS = ("event_id", "player", "side", "price", "point")
RESULTS_REQUIRED_COLUMNS = (
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
)
GRADE_COLUMNS = (
    "actual_home_runs",
    "game_status",
    "result",
    "implied_probability",
    "stake_1u",
    "profit_1u",
    "grade_status",
)


class GradingInputError(ValueError):
    """Raised when a grading input is missing or invalid."""


@dataclass(frozen=True)
class GradeSummary:
    total_rows: int
    graded_rows: int
    missing_result_rows: int
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


def _load_results(path: Path) -> dict[tuple[str, str], tuple[int, str]]:
    _, rows = _read_csv(path, RESULTS_REQUIRED_COLUMNS, "results")
    results: dict[tuple[str, str], tuple[int, str]] = {}

    for row_number, row in enumerate(rows, start=2):
        event_id = _required_text(row, "event_id", "results", row_number)
        player = _required_text(row, "player", "results", row_number)
        home_runs_value = _required_text(
            row, "actual_home_runs", "results", row_number
        )
        game_status = str(row.get("game_status") or "").strip()
        home_runs_decimal = _decimal(
            home_runs_value, "actual_home_runs", "results", row_number
        )
        if home_runs_decimal < 0 or home_runs_decimal != home_runs_decimal.to_integral_value():
            raise GradingInputError(
                f"results CSV row {row_number} has invalid actual_home_runs: "
                f"{home_runs_value!r}"
            )

        key = (event_id, player)
        if key in results:
            raise GradingInputError(
                "results CSV contains duplicate event_id + player key: "
                f"{event_id!r} + {player!r}"
            )
        results[key] = (int(home_runs_decimal), game_status)

    return results


def grade_live_hr_results(
    odds_path: str | Path = DEFAULT_ODDS_CSV,
    results_path: str | Path = DEFAULT_RESULTS_CSV,
    output_path: str | Path = DEFAULT_OUTPUT_CSV,
) -> GradeSummary:
    """Grade local odds, write a separate CSV, and return aggregate results."""

    odds_csv = Path(odds_path)
    results_csv = Path(results_path)
    output_csv = Path(output_path)
    odds_columns, odds_rows = _read_csv(
        odds_csv, ODDS_REQUIRED_COLUMNS, "odds"
    )
    results = _load_results(results_csv)

    output_columns = [column for column in odds_columns if column not in GRADE_COLUMNS]
    output_columns.extend(GRADE_COLUMNS)
    graded_output: list[dict[str, object]] = []
    wins = 0
    losses = 0
    missing = 0
    total_profit = 0.0

    for row_number, row in enumerate(odds_rows, start=2):
        event_id = _required_text(row, "event_id", "odds", row_number)
        player = _required_text(row, "player", "odds", row_number)
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
                "result": "",
                "implied_probability": implied_probability(price),
                "stake_1u": 1.0,
                "profit_1u": "",
                "grade_status": "missing_result",
            }
        )

        matched_result = results.get((event_id, player))
        if matched_result is None:
            missing += 1
        else:
            actual_home_runs, game_status = matched_result
            won = actual_home_runs >= 1
            profit = win_profit_1u(price) if won else -1.0
            output_row.update(
                {
                    "actual_home_runs": actual_home_runs,
                    "game_status": game_status,
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
        wins=wins,
        losses=losses,
        total_profit_1u=total_profit,
    )


def print_summary(summary: GradeSummary) -> None:
    print(f"Total rows: {summary.total_rows}")
    print(f"Graded rows: {summary.graded_rows}")
    print(f"Missing result rows: {summary.missing_result_rows}")
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
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    args = parser.parse_args(argv)

    try:
        summary = grade_live_hr_results(
            odds_path=args.odds_csv,
            results_path=args.results_csv,
            output_path=args.output_csv,
        )
    except GradingInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
