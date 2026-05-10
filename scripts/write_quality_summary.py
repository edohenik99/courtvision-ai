from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.reporting.quality_summary import (
    QUALITY_HISTORY_CSV_NAME,
    QUALITY_HISTORY_JSONL_NAME,
    write_quality_summary_outputs,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the operator quality diagnostics summary.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    parser.add_argument("--out-dir", default="outputs")
    parser.add_argument("--history-root", default="data/history")
    args = parser.parse_args(argv)

    text_path, json_path, payload = write_quality_summary_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
        history_root=args.history_root,
    )
    kelly = payload["kelly_safety_summary"]
    funnel = payload["candidate_funnel"]
    print(f"quality_summary_txt={text_path}")
    print(f"quality_summary_json={json_path}")
    watchlist = payload.get("high_caution_over_watchlist", {})
    if isinstance(watchlist, dict) and watchlist.get("path"):
        print(f"high_caution_over_watchlist_csv={watchlist['path']}")
    history_csv = Path(args.runtime_root) / "operator" / QUALITY_HISTORY_CSV_NAME
    history_jsonl = Path(args.runtime_root) / "operator" / QUALITY_HISTORY_JSONL_NAME
    if history_csv.exists():
        print(f"quality_history_csv={history_csv}")
    if history_jsonl.exists():
        print(f"quality_history_jsonl={history_jsonl}")
    print(
        "quality_summary_totals "
        f"elite={funnel['elite_board_count']} "
        f"full_market={funnel['full_market_board_count']} "
        f"kelly_rows={kelly['total_rows']} "
        f"kelly_eligible={kelly['kelly_eligible_count']} "
        f"high_caution_over_skips={kelly['context_high_caution_over_skip_count']} "
        f"medium_neutral_over_dampeners={kelly['medium_neutral_over_dampened_count']} "
        f"kelly_manual_review_required={kelly.get('manual_review_required_count', 0)} "
        f"review_before_bet={kelly.get('review_before_bet_count', 0)} "
        f"same_opponent_under_warnings={payload.get('same_opponent_under_warning_count', 0)} "
        f"manual_review_required={payload.get('manual_review_required_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
