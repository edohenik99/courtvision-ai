import os
import csv
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv


# ============================================================
# Project / Environment
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("THE_ODDS_API_KEY")
BASE_URL = os.getenv("THE_ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")

if not API_KEY:
    raise RuntimeError("Missing THE_ODDS_API_KEY in .env")


# ============================================================
# Config
# ============================================================

SPORT = "baseball_mlb"

BOOKMAKERS = "draftkings,fanduel,betmgm,bet365,fanatics,espnbet,betrivers"

# Current free-plan strategy:
# Use alternate market, then filter point = 0.5 and side = Over.
# This gives 1+ HR / to hit a home run.
MARKETS = "batter_home_runs_alternate"

ODDS_FORMAT = "american"

# Free-plan protection.
MAX_EVENTS_PER_RUN = 12

# Skip games too close to start/live.
MIN_MINUTES_BEFORE_GAME = 30

# Console output control.
PRINT_SAMPLE_ROWS = 3

DATA_DIR = PROJECT_ROOT / "data" / "theoddsapi" / "live_hr_snapshots"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MASTER_CSV = DATA_DIR / "live_hr_props_master.csv"
RUN_LOG = DATA_DIR / "run_log.csv"

ROW_FIELDS = [
    "snapshot_time",
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "bookmaker_key",
    "bookmaker",
    "bookmaker_last_update",
    "market",
    "market_last_update",
    "player",
    "side",
    "price",
    "point",
    "hr_label",
]


# ============================================================
# Helpers
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_iso_z(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_z(dt):
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def header_int(headers, key, default=0):
    value = headers.get(key)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


def safe_request(path, params=None):
    params = params or {}
    params["apiKey"] = API_KEY

    response = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
    safe_url = response.url.replace(API_KEY, "API_KEY_HIDDEN")

    print("\nURL:", safe_url)
    print("Status:", response.status_code)
    print("Used:", response.headers.get("x-requests-used"))
    print("Remaining:", response.headers.get("x-requests-remaining"))
    print("Last cost:", response.headers.get("x-requests-last"))

    if response.status_code != 200:
        print("ERROR BODY:")
        print(response.text)
        raise RuntimeError(f"The Odds API request failed with status {response.status_code}")

    return response.json(), response.headers


def already_ran_today(today_key):
    if not RUN_LOG.exists():
        return False

    with open(RUN_LOG, "r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row.get("run_date") == today_key and row.get("status") == "success":
                return True

    return False


def write_run_log(row):
    log_exists = RUN_LOG.exists()

    fieldnames = [
        "run_date",
        "snapshot_time",
        "status",
        "events_found",
        "events_scanned",
        "rows_saved",
        "credits_used_this_run",
        "credits_remaining",
        "snapshot_csv",
        "master_csv",
        "error",
    ]

    with open(RUN_LOG, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)

        if not log_exists:
            writer.writeheader()

        writer.writerow(row)

    print(f"Run logged: {RUN_LOG}")


# ============================================================
# API Calls
# ============================================================

def get_events():
    events, headers = safe_request(
        f"/sports/{SPORT}/events",
        {
            "dateFormat": "iso",
        },
    )

    return events, headers


def get_event_hr_odds(event_id):
    odds, headers = safe_request(
        f"/sports/{SPORT}/events/{event_id}/odds",
        {
            "bookmakers": BOOKMAKERS,
            "markets": MARKETS,
            "oddsFormat": ODDS_FORMAT,
            "dateFormat": "iso",
        },
    )

    return odds, headers


# ============================================================
# Data Processing
# ============================================================

def flatten_1_plus_hr(event_odds, snapshot_time):
    rows = []

    event_id = event_odds.get("id")
    commence_time = event_odds.get("commence_time")
    home_team = event_odds.get("home_team")
    away_team = event_odds.get("away_team")

    for bookmaker in event_odds.get("bookmakers", []):
        bookmaker_key = bookmaker.get("key")
        bookmaker_title = bookmaker.get("title")
        bookmaker_last_update = bookmaker.get("last_update")

        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            market_last_update = market.get("last_update")

            for outcome in market.get("outcomes", []):
                player = outcome.get("description")
                side = outcome.get("name")
                price = outcome.get("price")
                point = outcome.get("point")

                # Main target: To hit 1+ HR.
                if side != "Over":
                    continue

                if point != 0.5:
                    continue

                if not player:
                    continue

                rows.append({
                    "snapshot_time": snapshot_time,
                    "event_id": event_id,
                    "commence_time": commence_time,
                    "home_team": home_team,
                    "away_team": away_team,
                    "bookmaker_key": bookmaker_key,
                    "bookmaker": bookmaker_title,
                    "bookmaker_last_update": bookmaker_last_update,
                    "market": market_key,
                    "market_last_update": market_last_update,
                    "player": player,
                    "side": side,
                    "price": price,
                    "point": point,
                    "hr_label": "1+ HR",
                })

    return rows


def write_csv(path, rows):
    if not rows:
        print(f"No rows to write: {path}")
        return

    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ROW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved snapshot CSV: {path}")


def append_master_csv(path, rows):
    if not rows:
        print("No rows to append to master CSV.")
        return

    file_exists = path.exists()

    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=ROW_FIELDS)

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)

    print(f"Appended to master CSV: {path}")


def dedupe_master_csv(path):
    """
    Removes accidental duplicate runs from the same day.

    Keeps one latest row per:
    snapshot_date + event + bookmaker + market + player + side + point

    This keeps different days separate, so your dataset can still grow over time.
    """

    if not path.exists():
        print(f"No master CSV found to dedupe: {path}")
        return

    try:
        import pandas as pd
    except ImportError:
        print("pandas is required for dedupe. Install with: pip install pandas")
        return

    df = pd.read_csv(path)

    if df.empty:
        print("Master CSV is empty. Nothing to dedupe.")
        return

    before = df.shape

    df["snapshot_time_sort"] = pd.to_datetime(df["snapshot_time"], errors="coerce")
    df["snapshot_date"] = df["snapshot_time_sort"].dt.strftime("%Y-%m-%d")

    df = df.sort_values("snapshot_time_sort")

    subset = [
        "snapshot_date",
        "event_id",
        "bookmaker_key",
        "market",
        "player",
        "side",
        "point",
    ]

    df = df.drop_duplicates(subset=subset, keep="last")
    df = df.drop(columns=["snapshot_time_sort", "snapshot_date"])

    after = df.shape

    df.to_csv(path, index=False)

    print(f"Deduped master CSV: before {before}, after {after}")


# ============================================================
# Main Collector
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Collect live MLB 1+ HR prop odds from The Odds API."
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass same-day run guard. Use carefully because this can create extra snapshots and burn credits.",
    )

    parser.add_argument(
        "--max-events",
        type=int,
        default=MAX_EVENTS_PER_RUN,
        help=f"Maximum events to scan. Default: {MAX_EVENTS_PER_RUN}",
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print sample rows.",
    )

    parser.add_argument(
        "--dedupe-only",
        action="store_true",
        help="Only dedupe the master CSV. Does not call The Odds API.",
    )

    args = parser.parse_args()

    if args.dedupe_only:
        dedupe_master_csv(MASTER_CSV)
        return

    snapshot_dt = now_utc()
    snapshot_time = iso_z(snapshot_dt)
    today_key = snapshot_dt.strftime("%Y-%m-%d")

    timestamp_for_file = snapshot_dt.strftime("%Y%m%d_%H%M%SZ")
    snapshot_csv = DATA_DIR / f"live_hr_props_{timestamp_for_file}.csv"

    if already_ran_today(today_key) and not args.force:
        print(f"Collector already ran today: {today_key}")
        print("Stopping to protect credits and prevent duplicate snapshots.")
        print("Use --force only if you intentionally want another snapshot today.")
        return

    total_credit_cost = 0
    credits_remaining = None
    events_found = 0
    events_scanned = 0
    all_rows = []

    try:
        print("Getting upcoming MLB events...")

        events, event_headers = get_events()
        total_credit_cost += header_int(event_headers, "x-requests-last")
        credits_remaining = event_headers.get("x-requests-remaining")

        events_found = len(events)

        print(f"\nEvents found: {events_found}")

        usable_events = []

        for event in events:
            commence_raw = event.get("commence_time")

            if not commence_raw:
                continue

            commence_time = parse_iso_z(commence_raw)
            minutes_until_game = (commence_time - snapshot_dt).total_seconds() / 60

            if minutes_until_game >= MIN_MINUTES_BEFORE_GAME:
                usable_events.append(event)

        usable_events = usable_events[:args.max_events]

        print(f"Usable future events: {len(usable_events)}")
        print(f"Max events this run: {args.max_events}")

        for event in usable_events:
            print("\n==================================================")
            print(
                event.get("away_team"),
                "@",
                event.get("home_team"),
                "|",
                event.get("commence_time"),
            )

            event_odds, odds_headers = get_event_hr_odds(event["id"])
            total_credit_cost += header_int(odds_headers, "x-requests-last")
            credits_remaining = odds_headers.get("x-requests-remaining")

            rows = flatten_1_plus_hr(event_odds, snapshot_time)

            print(f"1+ HR rows found: {len(rows)}")

            if not args.quiet and PRINT_SAMPLE_ROWS > 0:
                for row in rows[:PRINT_SAMPLE_ROWS]:
                    print(row)

            all_rows.extend(rows)
            events_scanned += 1

            # Small pause so we do not hammer the API.
            time.sleep(0.5)

        write_csv(snapshot_csv, all_rows)
        append_master_csv(MASTER_CSV, all_rows)
        dedupe_master_csv(MASTER_CSV)

        print("\n==================================================")
        print(f"TOTAL 1+ HR ROWS SAVED THIS RUN: {len(all_rows)}")
        print(f"Credits used this run: {total_credit_cost}")
        print(f"Credits remaining: {credits_remaining}")
        print(f"Snapshot folder: {DATA_DIR}")
        print(f"Snapshot CSV: {snapshot_csv}")
        print(f"Master CSV: {MASTER_CSV}")

        write_run_log({
            "run_date": today_key,
            "snapshot_time": snapshot_time,
            "status": "success",
            "events_found": events_found,
            "events_scanned": events_scanned,
            "rows_saved": len(all_rows),
            "credits_used_this_run": total_credit_cost,
            "credits_remaining": credits_remaining,
            "snapshot_csv": str(snapshot_csv),
            "master_csv": str(MASTER_CSV),
            "error": "",
        })

    except Exception as exc:
        write_run_log({
            "run_date": today_key,
            "snapshot_time": snapshot_time,
            "status": "failed",
            "events_found": events_found,
            "events_scanned": events_scanned,
            "rows_saved": len(all_rows),
            "credits_used_this_run": total_credit_cost,
            "credits_remaining": credits_remaining,
            "snapshot_csv": str(snapshot_csv),
            "master_csv": str(MASTER_CSV),
            "error": str(exc),
        })

        raise


if __name__ == "__main__":
    main()