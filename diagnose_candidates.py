#!/usr/bin/env python3
"""Diagnostic script to investigate why candidates are being filtered out.

Run this to see detailed stage-by-stage diagnostics:
    python diagnose_candidates.py

Expected output format:
    candidate_universe_input games=2 odds=46 player_baselines=30 injury_teams=1
    candidates_raw=3828
    after_edge=1200
    after_confidence=400
    after_final=0
    rejection_breakdown {'low_edge': 2628, 'low_confidence': 1200}
    top_rejection_causes [('low_edge', 2628), ('low_confidence', 1200)]
    sample_low_edge_values [0.1, 0.05, 0.0, 0.15, 0.08] (threshold=0.5)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Set up logging to see diagnostics
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from courtvision.pipeline import PredictionConfig, PredictionPipeline


def main():
    """Run pipeline with diagnostics enabled."""
    print("=" * 60)
    print("Candidate Pipeline Diagnostics")
    print("=" * 60)

    config = PredictionConfig(
        prediction_date="2026-04-17",
        min_edge=0.5,
        min_confidence=0.35,
        enable_partial_fill=True,
    )

    pipeline = PredictionPipeline(config)

    try:
        result = pipeline.run()
        print("\n" + "=" * 60)
        print("Pipeline completed successfully")
        print("=" * 60)
        print(f"\nCandidates accepted: {len(result.merged_market_props)}")
        print(f"Elite props: {len(result.elite_props)}")
        print(f"Full market: {len(result.full_market_props)}")
    except Exception as e:
        print(f"\nPipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
