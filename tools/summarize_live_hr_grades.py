"""Build an offline Markdown performance summary from MLB live HR grades."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
REPORTS_DIR = DATA_DIR / "reports"

COLUMN_ALIASES = {
    "bookmaker": ("bookmaker", "bookmaker_name", "sportsbook", "book"),
    "player": ("player", "player_name", "batter", "batter_name"),
    "price": ("price", "american_odds", "american_price", "odds"),
    "result": ("result", "grade_result", "win_loss", "outcome", "grade"),
    "profit": ("profit_1u", "net_profit_1u", "profit", "net_profit"),
}
OPTIONAL_COLUMN_ALIASES = {
    "event_id": ("event_id", "event", "game_id"),
    "grade_status": ("grade_status", "status", "grading_status"),
    "game_status": ("game_status",),
    "result_reason": ("result_reason", "void_reason", "grading_skip_reason", "reason"),
    "home_team": ("home_team", "home"),
    "away_team": ("away_team", "away"),
    "team": ("team", "player_team", "batter_team"),
}
ODDS_BUCKETS = (
    "< +300",
    "+300 to +499",
    "+500 to +799",
    "+800 to +1199",
    "+1200+",
)
WIN_VALUES = {"win", "won", "w", "hit"}
LOSS_VALUES = {"loss", "lost", "l", "miss"}
VOID_VALUES = {"void", "excluded", "push", "cancelled", "canceled"}
VOID_CANDIDATE_VALUES = {"void_candidate"}
MANUAL_REVIEW_VALUES = {"manual_review_required"}


class SummaryInputError(ValueError):
    """Raised when a grade summary input is missing or invalid."""


@dataclass(frozen=True)
class GradeRow:
    bookmaker: str
    player: str
    price: Decimal
    result: str
    profit: Decimal
    game: str | None = None
    team: str | None = None


@dataclass(frozen=True)
class NonGradeableRow:
    status: str
    player: str
    event_id: str
    game: str
    team: str
    reason: str


@dataclass
class Performance:
    rows: int = 0
    wins: int = 0
    losses: int = 0
    profit: Decimal = Decimal("0")

    @property
    def hit_rate(self) -> Decimal:
        return Decimal(self.wins) / self.rows if self.rows else Decimal("0")

    @property
    def roi(self) -> Decimal:
        return self.profit / self.rows if self.rows else Decimal("0")

    def add(self, row: GradeRow) -> None:
        self.rows += 1
        self.wins += int(row.result == "win")
        self.losses += int(row.result == "loss")
        self.profit += row.profit


@dataclass(frozen=True)
class GradeSummary:
    target_date: str
    grade_csv: Path
    report_path: Path
    total_rows: int
    void_rows: int
    void_candidate_rows: int
    manual_review_rows: int
    non_gradeable_rows: tuple[NonGradeableRow, ...]
    overall: Performance
    bookmaker: dict[str, Performance]
    odds_bucket: dict[str, Performance]
    players: dict[str, Performance]
    games: dict[str, Performance]
    teams: dict[str, Performance]


def _normalize_column(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _infer_columns(
    fieldnames: list[str], aliases: dict[str, tuple[str, ...]], required: bool
) -> dict[str, str]:
    normalized = {_normalize_column(column): column for column in fieldnames}
    inferred: dict[str, str] = {}
    missing: list[str] = []

    for purpose, candidates in aliases.items():
        match = next(
            (normalized[candidate] for candidate in candidates if candidate in normalized),
            None,
        )
        if match is None:
            if required:
                missing.append(f"{purpose} ({', '.join(candidates)})")
        else:
            inferred[purpose] = match

    if missing:
        raise SummaryInputError(
            "grade CSV missing required columns for: " + "; ".join(missing)
        )
    return inferred


def _validate_target_date(target_date: str) -> str:
    try:
        parsed = date.fromisoformat(target_date)
    except ValueError as exc:
        raise SummaryInputError(
            f"invalid date {target_date!r}; expected YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != target_date:
        raise SummaryInputError(
            f"invalid date {target_date!r}; expected YYYY-MM-DD"
        )
    return target_date


def default_grade_path(target_date: str) -> Path:
    return DATA_DIR / f"live_hr_grades_{target_date.replace('-', '')}.csv"


def default_report_path(target_date: str) -> Path:
    return REPORTS_DIR / f"live_hr_grade_summary_{target_date.replace('-', '')}.md"


def _required_text(
    row: dict[str, str | None], column: str, purpose: str, row_number: int
) -> str:
    value = str(row.get(column) or "").strip()
    if not value:
        raise SummaryInputError(
            f"grade CSV row {row_number} has blank required {purpose} field "
            f"{column!r}"
        )
    return value


def _decimal(value: str, purpose: str, row_number: int) -> Decimal:
    cleaned = value.strip().replace(",", "")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise SummaryInputError(
            f"grade CSV row {row_number} has invalid {purpose}: {value!r}"
        ) from exc
    if not number.is_finite():
        raise SummaryInputError(
            f"grade CSV row {row_number} has invalid {purpose}: {value!r}"
        )
    return number


def _canonical_result(value: str) -> str | None:
    normalized = value.strip().casefold()
    if normalized in WIN_VALUES:
        return "win"
    if normalized in LOSS_VALUES:
        return "loss"
    if normalized in VOID_VALUES:
        return "void"
    return None


def _canonical_non_gradeable_status(value: str) -> str | None:
    normalized = value.strip().casefold()
    if normalized in VOID_VALUES:
        return "void"
    if normalized in VOID_CANDIDATE_VALUES:
        return "void_candidate"
    if normalized in MANUAL_REVIEW_VALUES:
        return "manual_review_required"
    return None


def odds_bucket(price: Decimal) -> str:
    if price < 300:
        return "< +300"
    if price < 500:
        return "+300 to +499"
    if price < 800:
        return "+500 to +799"
    if price < 1200:
        return "+800 to +1199"
    return "+1200+"


def _group_add(groups: dict[str, Performance], key: str, row: GradeRow) -> None:
    groups.setdefault(key, Performance()).add(row)


def _read_grade_rows(
    grade_csv: Path,
) -> tuple[
    list[GradeRow],
    int,
    int,
    int,
    int,
    tuple[NonGradeableRow, ...],
    bool,
    bool,
]:
    if not grade_csv.is_file():
        raise SummaryInputError(f"grade CSV does not exist: {grade_csv}")

    try:
        with grade_csv.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or ())
            if not fieldnames:
                raise SummaryInputError(f"grade CSV has no header row: {grade_csv}")
            columns = _infer_columns(fieldnames, COLUMN_ALIASES, required=True)
            optional = _infer_columns(
                fieldnames, OPTIONAL_COLUMN_ALIASES, required=False
            )

            grade_rows: list[GradeRow] = []
            non_gradeable_rows: list[NonGradeableRow] = []
            total_rows = 0
            void_rows = 0
            void_candidate_rows = 0
            manual_review_rows = 0
            for row_number, row in enumerate(reader, start=2):
                total_rows += 1
                if None in row:
                    raise SummaryInputError(
                        f"grade CSV row {row_number} has more values than its schema"
                    )

                raw_result = str(row.get(columns["result"]) or "").strip()
                result = _canonical_result(raw_result)
                raw_status = (
                    str(row.get(optional["grade_status"]) or "").strip()
                    if "grade_status" in optional
                    else ""
                )
                raw_game_status = (
                    str(row.get(optional["game_status"]) or "").strip()
                    if "game_status" in optional
                    else ""
                )
                non_gradeable_status = (
                    _canonical_non_gradeable_status(raw_status)
                    or _canonical_non_gradeable_status(raw_result)
                    or _canonical_non_gradeable_status(raw_game_status)
                )
                if non_gradeable_status:
                    if non_gradeable_status == "void":
                        void_rows += 1
                    elif non_gradeable_status == "void_candidate":
                        void_candidate_rows += 1
                    elif non_gradeable_status == "manual_review_required":
                        manual_review_rows += 1
                    home = str(row.get(optional.get("home_team", "")) or "").strip()
                    away = str(row.get(optional.get("away_team", "")) or "").strip()
                    game = f"{away} @ {home}" if home and away else ""
                    non_gradeable_rows.append(
                        NonGradeableRow(
                            status=non_gradeable_status,
                            player=_required_text(
                                row, columns["player"], "player", row_number
                            ),
                            event_id=str(
                                row.get(optional.get("event_id", "")) or ""
                            ).strip(),
                            game=game,
                            team=str(
                                row.get(optional.get("team", "")) or ""
                            ).strip(),
                            reason=(
                                str(row.get(optional["result_reason"]) or "").strip()
                                if "result_reason" in optional
                                else ""
                            ),
                        )
                    )
                    continue
                if result not in {"win", "loss"}:
                    continue

                bookmaker = _required_text(
                    row, columns["bookmaker"], "bookmaker", row_number
                )
                player = _required_text(
                    row, columns["player"], "player", row_number
                )
                price = _decimal(
                    _required_text(row, columns["price"], "price", row_number),
                    "price",
                    row_number,
                )
                profit = _decimal(
                    _required_text(row, columns["profit"], "profit", row_number),
                    "profit",
                    row_number,
                )

                home = str(row.get(optional.get("home_team", "")) or "").strip()
                away = str(row.get(optional.get("away_team", "")) or "").strip()
                team = str(row.get(optional.get("team", "")) or "").strip()
                game = f"{away} @ {home}" if home and away else None
                grade_rows.append(
                    GradeRow(
                        bookmaker=bookmaker,
                        player=player,
                        price=price,
                        result=result,
                        profit=profit,
                        game=game,
                        team=team or None,
                    )
                )
    except OSError as exc:
        raise SummaryInputError(f"could not read grade CSV: {exc}") from exc

    return (
        grade_rows,
        total_rows,
        void_rows,
        void_candidate_rows,
        manual_review_rows,
        tuple(non_gradeable_rows),
        "home_team" in optional and "away_team" in optional,
        "team" in optional,
    )


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _number(value: Decimal) -> str:
    return f"{value:.2f}"


def _percent(value: Decimal) -> str:
    return f"{value:.2%}"


def _performance_table(
    heading: str, label: str, groups: dict[str, Performance]
) -> list[str]:
    lines = [
        f"## {heading}",
        "",
        f"| {label} | Rows | Wins | Losses | Hit rate | Profit_1u | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    if not groups:
        lines.append("| No available data | 0 | 0 | 0 | 0.00% | 0.00 | 0.00% |")
        return lines

    for name, performance in groups.items():
        lines.append(
            f"| {_escape(name)} | {performance.rows} | {performance.wins} | "
            f"{performance.losses} | {_percent(performance.hit_rate)} | "
            f"{_number(performance.profit)} | {_percent(performance.roi)} |"
        )
    return lines


def _player_table(
    heading: str, players: list[tuple[str, Performance]]
) -> list[str]:
    lines = [
        f"## {heading}",
        "",
        "| Player | Rows | Wins | Losses | Profit_1u | ROI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for player, performance in players:
        lines.append(
            f"| {_escape(player)} | {performance.rows} | {performance.wins} | "
            f"{performance.losses} | {_number(performance.profit)} | "
            f"{_percent(performance.roi)} |"
        )
    if not players:
        lines.append("| No available data | 0 | 0 | 0 | 0.00 | 0.00% |")
    return lines


def _non_gradeable_table(rows: tuple[NonGradeableRow, ...]) -> list[str]:
    lines = [
        "## Void/manual review rows",
        "",
        "| Status | Player | Event ID | Team | Game | Reason |",
        "|---|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| No available data |  |  |  |  |  |")
        return lines
    for row in rows:
        lines.append(
            f"| {_escape(row.status)} | {_escape(row.player)} | "
            f"{_escape(row.event_id or 'n/a')} | {_escape(row.team or 'n/a')} | "
            f"{_escape(row.game or 'n/a')} | {_escape(row.reason or 'n/a')} |"
        )
    return lines


def _render_report(summary: GradeSummary, generated_at: str) -> str:
    overall = summary.overall
    lines = [
        "# MLB Live HR Grade Performance Summary",
        "",
        f"- Date: {summary.target_date}",
        f"- Generated: {generated_at}",
        f"- Grade CSV: `{summary.grade_csv}`",
        "",
        "## Overall performance",
        "",
        f"- Total source rows: {summary.total_rows}",
        f"- Graded rows: {overall.rows}",
        f"- Wins: {overall.wins}",
        f"- Losses: {overall.losses}",
        f"- Hit rate: {_percent(overall.hit_rate)}",
        f"- Total profit_1u: {_number(overall.profit)}",
        f"- ROI: {_percent(overall.roi)}",
        f"- Void candidate rows: {summary.void_candidate_rows}",
        f"- Manual review rows: {summary.manual_review_rows}",
    ]
    if summary.void_rows:
        lines.append(f"- Void/excluded rows present in source: {summary.void_rows}")

    lines.extend(["", *_non_gradeable_table(summary.non_gradeable_rows)])
    lines.extend(["", *_performance_table("Bookmaker performance", "Bookmaker", summary.bookmaker)])
    lines.extend(["", *_performance_table("Odds bucket performance", "Odds bucket", summary.odds_bucket)])

    profitable = sorted(
        summary.players.items(), key=lambda item: (-item[1].profit, item[0].casefold())
    )[:10]
    worst = sorted(
        summary.players.items(), key=lambda item: (item[1].profit, item[0].casefold())
    )[:10]
    lines.extend(["", *_player_table("Top profitable players", profitable)])
    lines.extend(["", *_player_table("Worst players", worst)])

    lines.extend(["", "## Game/team summary", ""])
    if summary.games:
        lines.extend(_performance_table("Game performance", "Game", summary.games)[2:])
    elif summary.teams:
        lines.extend(_performance_table("Team performance", "Team", summary.teams)[2:])
    else:
        lines.append("Game/team columns were not available in the grade CSV.")

    if overall.profit > 0:
        profitability = "The graded result is profitable on a one-unit-per-row basis."
    elif overall.profit < 0:
        profitability = "The graded result is not profitable on a one-unit-per-row basis."
    else:
        profitability = "The graded result is break-even on a one-unit-per-row basis."
    sample_note = (
        "The sample size is small; interpret the rates cautiously."
        if overall.rows < 100
        else "The sample is larger than the small-sample flag, but remains one historical slice."
    )
    lines.extend(
        [
            "",
            "## Action notes",
            "",
            f"- {profitability}",
            f"- {sample_note}",
            "- This is historical grading, not betting advice.",
            "",
        ]
    )
    return "\n".join(lines)


def summarize_live_hr_grades(
    target_date: str,
    grade_csv: str | Path | None = None,
    output_path: str | Path | None = None,
) -> GradeSummary:
    """Read one grade CSV, write its Markdown summary, and return its metrics."""

    target_date = _validate_target_date(target_date)
    input_path = Path(grade_csv) if grade_csv else default_grade_path(target_date)
    report_path = Path(output_path) if output_path else default_report_path(target_date)
    (
        rows,
        total_rows,
        void_rows,
        void_candidate_rows,
        manual_review_rows,
        non_gradeable_rows,
        has_games,
        has_teams,
    ) = _read_grade_rows(input_path)

    overall = Performance()
    bookmaker: dict[str, Performance] = {}
    buckets = {name: Performance() for name in ODDS_BUCKETS}
    players: dict[str, Performance] = {}
    games: dict[str, Performance] = {}
    teams: dict[str, Performance] = {}
    for row in rows:
        overall.add(row)
        _group_add(bookmaker, row.bookmaker, row)
        buckets[odds_bucket(row.price)].add(row)
        _group_add(players, row.player, row)
        if has_games and row.game:
            _group_add(games, row.game, row)
        if has_teams and row.team:
            _group_add(teams, row.team, row)

    bookmaker = dict(sorted(bookmaker.items(), key=lambda item: item[0].casefold()))
    games = dict(sorted(games.items(), key=lambda item: item[0].casefold()))
    teams = dict(sorted(teams.items(), key=lambda item: item[0].casefold()))
    summary = GradeSummary(
        target_date=target_date,
        grade_csv=input_path,
        report_path=report_path,
        total_rows=total_rows,
        void_rows=void_rows,
        void_candidate_rows=void_candidate_rows,
        manual_review_rows=manual_review_rows,
        non_gradeable_rows=non_gradeable_rows,
        overall=overall,
        bookmaker=bookmaker,
        odds_bucket=buckets,
        players=players,
        games=games,
        teams=teams,
    )

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            _render_report(
                summary, datetime.now().astimezone().isoformat(timespec="seconds")
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise SummaryInputError(f"could not write summary report: {exc}") from exc
    return summary


def print_summary(summary: GradeSummary) -> None:
    print(f"MLB live HR grade summary: {summary.target_date}")
    print(
        f"Rows: {summary.overall.rows} graded "
        f"({summary.overall.wins} wins, {summary.overall.losses} losses)"
    )
    print(f"Profit_1u: {_number(summary.overall.profit)}")
    print(f"ROI: {_percent(summary.overall.roi)}")
    if summary.void_rows:
        print(f"Void/excluded source rows: {summary.void_rows}")
    print(f"Void candidate rows: {summary.void_candidate_rows}")
    print(f"Manual review rows: {summary.manual_review_rows}")
    print(f"Report: {summary.report_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize a date-scoped MLB live HR grade CSV offline."
    )
    parser.add_argument("--date", dest="target_date", required=True)
    parser.add_argument("--grade-csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        summary = summarize_live_hr_grades(
            target_date=args.target_date,
            grade_csv=args.grade_csv,
            output_path=args.output,
        )
    except SummaryInputError as exc:
        print(f"ERROR for date {args.target_date}: {exc}", file=sys.stderr)
        return 1

    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
