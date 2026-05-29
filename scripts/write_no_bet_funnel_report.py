from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.no_bet_funnel import (
    DEFAULT_LOOKBACK,
    write_no_bet_funnel_report_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the reporting-only NO_BET funnel report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--history-root", default="data/history")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    args = parser.parse_args(argv)

    text_path, json_path, csv_path, payload = write_no_bet_funnel_report_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        history_root=args.history_root,
        lookback=args.lookback,
    )
    aggregate = payload["aggregate"]
    status_counts = payload["status_counts"]
    print(f"no_bet_funnel_report_txt={text_path}")
    print(f"no_bet_funnel_report_json={json_path}")
    print(f"no_bet_funnel_report_csv={csv_path}")
    print(
        "no_bet_funnel_totals "
        f"operator_cards={payload['operator_card_count']} "
        f"no_bet={status_counts.get('NO BET', 0)} "
        f"review_required={status_counts.get('REVIEW REQUIRED', 0)} "
        f"bet_approved={status_counts.get('BET APPROVED', 0)} "
        f"full_market={aggregate['total_full_market_candidates']} "
        f"high_caution_over={aggregate['total_high_caution_over_blocks']} "
        f"high_caution_over_rate={aggregate['high_caution_over_block_rate']:.4f} "
        f"near_elite={aggregate['total_near_elite_rows']} "
        f"incubator={aggregate['total_incubator_rows']} "
        f"elite={aggregate['total_elite_rows']} "
        f"kelly_eligible={aggregate['total_kelly_eligible_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
