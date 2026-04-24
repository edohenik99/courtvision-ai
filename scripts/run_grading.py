from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.config import Settings
from courtvision.grading import PickGrader
from courtvision.pipeline.runner import build_grading_manifest, write_manifest
from courtvision.reporting.text_report import TextReportWriter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade CourtVision pick history for a given date.")
    parser.add_argument("--prediction-date", required=True, help="Date of the picks to grade in YYYY-MM-DD format.")
    parser.add_argument("--out-dir", default="outputs", help="Output directory. Defaults to ./outputs")
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def main() -> None:
    load_dotenv()
    configure_logging()
    args = parse_args()

    settings = Settings.from_env()
    grader = PickGrader(settings=settings, output_dir=args.out_dir)
    writer = TextReportWriter(output_dir=args.out_dir)

    try:
        graded, summary = grader.grade_date(args.prediction_date)
    except FileNotFoundError as exc:
        print(f"\nUnable to grade picks for {args.prediction_date}.\n")
        print(str(exc))
        print("Run python courtvision_ai.py --prediction-date <date> --out-dir outputs first so the operator board or pick history exists.")
        raise SystemExit(1) from exc

    graded_txt, graded_csv = writer.write_graded_picks(args.prediction_date, graded)
    summary_csv = writer.write_grading_summary(args.prediction_date, summary)
    manifest_path = write_manifest(
        build_grading_manifest(args.prediction_date, graded, summary),
        out_dir=args.out_dir,
    )

    print("\nGrading complete.\n")
    print(f"graded_picks_txt: {graded_txt}")
    print(f"graded_picks_csv: {graded_csv}")
    print(f"grading_summary_csv: {summary_csv}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
