"""
Generate a human-friendly MLB live HR results workbook CSV.

Offline-only:
- No API calls
- Reads live_hr_props_master.csv
- Dedupes by event_id + player
- Adds game/team/time context
- Adds bookmaker price summary
- Leaves actual_home_runs and game_status blank for manual filling

This file is for humans.
The strict grader still uses live_hr_results.csv.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_INPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv")
DEFAULT_OUTPUT = Path("data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv")


OUTPUT_COLUMNS = [
    "commence_time",
    "home_team",
    "away_team",
    "event_id",
    "player",
    "books_available",
    "best_bookmaker",
    "best_price",
    "all_prices",
    "actual_home_runs",
    "game_status",
]


REQUIRED_INPUT_COLUMNS = [
    "event_id",
    "player",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker",
    "price",
]


def is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def parse_price(value: str | None) -> int | None:
    if is_blank(value):
        return None

    try:
        return int(str(value).strip())
    except ValueError:
        return None


def validate_columns(fieldnames: list[str] | None) -> None:
    if not fieldnames:
        raise ValueError("Input CSV has no header row.")

    missing = [column for column in REQUIRED_INPUT_COLUMNS if column not in fieldnames]

    if missing:
        raise ValueError(
            f"Input CSV is missing required columns: {missing}. "
            f"Available columns: {fieldnames}"
        )


def generate_workbook(input_path: Path, output_path: Path, overwrite: bool) -> int:
    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}\n"
            "Use --overwrite only if you intentionally want to replace it."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    grouped: dict[tuple[str, str], dict[str, object]] = {}

    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        validate_columns(reader.fieldnames)

        for row in reader:
            event_id = (row.get("event_id") or "").strip()
            player = (row.get("player") or "").strip()

            if not event_id or not player:
                continue

            key = (event_id, player)

            if key not in grouped:
                grouped[key] = {
                    "commence_time": (row.get("commence_time") or "").strip(),
                    "home_team": (row.get("home_team") or "").strip(),
                    "away_team": (row.get("away_team") or "").strip(),
                    "event_id": event_id,
                    "player": player,
                    "prices": [],
                }

            bookmaker = (row.get("bookmaker") or row.get("bookmaker_key") or "").strip()
            price_raw = (row.get("price") or "").strip()
            side = (row.get("side") or "").strip()
            point = (row.get("point") or "").strip()

            if bookmaker and price_raw:
                price_label_parts = [bookmaker]

                if side:
                    price_label_parts.append(side)

                if point:
                    price_label_parts.append(point)

                price_label = " ".join(price_label_parts)

                grouped[key]["prices"].append(
                    {
                        "bookmaker": bookmaker,
                        "price_raw": price_raw,
                        "price": parse_price(price_raw),
                        "label": f"{price_label}: {price_raw}",
                    }
                )

    output_rows: list[dict[str, str]] = []

    for item in grouped.values():
        prices = list(item["prices"])

        valid_prices = [price for price in prices if price["price"] is not None]

        if valid_prices:
            best = max(valid_prices, key=lambda price: int(price["price"]))
            best_bookmaker = str(best["bookmaker"])
            best_price = str(best["price_raw"])
        else:
            best_bookmaker = ""
            best_price = ""

        books_available = sorted(
            {str(price["bookmaker"]) for price in prices if price["bookmaker"]}
        )

        all_prices = " | ".join(str(price["label"]) for price in prices)

        output_rows.append(
            {
                "commence_time": str(item["commence_time"]),
                "home_team": str(item["home_team"]),
                "away_team": str(item["away_team"]),
                "event_id": str(item["event_id"]),
                "player": str(item["player"]),
                "books_available": "; ".join(books_available),
                "best_bookmaker": best_bookmaker,
                "best_price": best_price,
                "all_prices": all_prices,
                "actual_home_runs": "",
                "game_status": "",
            }
        )

    output_rows.sort(
        key=lambda row: (
            row["commence_time"],
            row["away_team"],
            row["home_team"],
            row["player"],
        )
    )

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(output_rows)

    return len(output_rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a human-friendly live HR results workbook CSV."
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help=f"Input master odds CSV. Default: {DEFAULT_INPUT}",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output workbook CSV. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists.",
    )

    args = parser.parse_args()

    count = generate_workbook(
        input_path=Path(args.input),
        output_path=Path(args.output),
        overwrite=args.overwrite,
    )

    print(f"Generated results workbook: {args.output}")
    print(f"Rows: {count}")
    print("Use this workbook for manual result entry.")
    print("The strict grader still uses live_hr_results.csv.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())