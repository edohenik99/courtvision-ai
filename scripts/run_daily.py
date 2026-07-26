"""Deprecated compatibility shim for the canonical prediction application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical CourtVision daily prediction flow.")
    parser.add_argument("--prediction-date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--out-dir", default="outputs", help="Output directory. Defaults to ./outputs")
    parser.add_argument("--send-telegram", action="store_true", help="Send Telegram alert for top qualified plays after prediction.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from courtvision_ai import main as canonical_main

    print(
        "[compatibility] scripts/run_daily.py is deprecated; "
        "delegating to PredictionApplicationService via courtvision_ai.py."
    )
    canonical_args = [
        "predict",
        "--sport",
        "nba",
        "--mode",
        "production",
        "--prediction-date",
        args.prediction_date,
        "--out-dir",
        args.out_dir,
    ]
    if args.send_telegram:
        canonical_args.append("--send-telegram")
    return canonical_main(canonical_args)


if __name__ == "__main__":
    raise SystemExit(main())
