"""Compatibility CLI shim for daily prediction runs.

`courtvision_ai.py` stays canonical for now. This wrapper remains intentionally
thin and exists only for operator convenience.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.balldontlie_auth import BALLDONTLIE_API_KEY_ENV_VAR, resolve_api_key
from courtvision.pipeline.runner import build_prediction_manifest, write_manifest, save_prediction_boards


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical CourtVision daily prediction flow.")
    parser.add_argument("--prediction-date", required=True, help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--out-dir", default="outputs", help="Output directory. Defaults to ./outputs")
    parser.add_argument("--send-telegram", action="store_true", help="Send Telegram alert for top qualified plays after prediction.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _, key_details = resolve_api_key(
        entrypoint="scripts/run_daily.py",
        env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
        logger=logging.getLogger("courtvision_run_daily"),
    )
    print(
        f"[run_daily_auth] env_var={key_details.get('env_var_name', BALLDONTLIE_API_KEY_ENV_VAR)} "
        f"source={key_details.get('source', 'unknown')} "
        f"key={key_details.get('masked_preview', '<empty>')}"
    )

    from courtvision_ai import CourtVisionAI

    args = parse_args()
    ai = CourtVisionAI(out_dir=args.out_dir)
    prediction_outputs = ai.predict(args.prediction_date)

    save_prediction_boards(args.prediction_date, prediction_outputs, args.out_dir)

    if args.send_telegram:
        summary = dict(prediction_outputs.get("summary", {}))
        sent = ai.send_telegram_top_plays(
            prediction_date=args.prediction_date,
            selected_df=prediction_outputs.get("selected_props"),
            summary=summary,
        )
        print(f"[telegram] {'sent' if sent else 'skipped_or_failed'}")

    manifest_path = write_manifest(
        build_prediction_manifest(args.prediction_date, prediction_outputs),
        out_dir=args.out_dir,
    )
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
