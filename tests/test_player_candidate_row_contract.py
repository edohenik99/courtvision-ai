"""Regression test: the player-market `build_candidate_row` MUST emit
the scoring fields (`edge`, `confidence`, `projection`, `line`, `odds`,
`market_type`) that `score_candidate_fn` reads downstream.

Background
----------
The team-market candidate dict at `predict_pipeline.py` correctly emits
all scoring fields. The *player*-market candidate dict was missing
`edge`, `confidence`, etc. — so `score_candidate_fn` always read 0.0 and
threshold-rejected every player candidate as
`market_supported_but_failed_quality`. That collapsed the elite board
to zero picks even when 275 player candidates had been built.

This test ensures both candidate shapes stay aligned with the threshold
fields the scorer expects.
"""
from __future__ import annotations

import inspect

from courtvision.pipeline import predict_pipeline


REQUIRED_SCORING_FIELDS = {
    "edge",
    "edge_pct",
    "confidence",
    "projection",
    "line",
    "odds",
    "market_type",
}


def test_player_candidate_row_emits_scoring_fields() -> None:
    """Lint the source of `build_candidate_row` in PredictPipeline.

    We can't easily call it without a full pipeline, but we can assert
    that the *return statement* of the player-market candidate builder
    includes the scoring fields the threshold gate reads.
    """
    source = inspect.getsource(predict_pipeline)
    # Locate the player-market `build_candidate_row` block. It appears
    # before the team-market candidate block (which uses `team_abbr`
    # set to `team`). We grep for the function header.
    marker = "def build_candidate_row("
    assert marker in source, "Could not locate build_candidate_row in predict_pipeline.py"

    start = source.index(marker)
    # Find the matching `def reject_candidate(` to bound the function.
    end = source.index("def reject_candidate(", start)
    body = source[start:end]

    missing = sorted(field for field in REQUIRED_SCORING_FIELDS if f'"{field}"' not in body)
    assert not missing, (
        f"build_candidate_row is missing scoring fields {missing}. "
        "score_candidate_fn reads these on the returned dict; if absent, "
        "every player candidate is rejected as market_supported_but_failed_quality."
    )
