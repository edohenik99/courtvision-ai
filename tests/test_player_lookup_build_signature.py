"""Regression test: keep `_build_player_prop_identity_lookup` signature
aligned with the call site inside `BallDontLieClient.get_odds`.

Background: a kwarg mismatch (caller used `requested_game_ids=`, callee
expected `game_ids=`) silently swallowed the TypeError and produced an
empty player_lookup, which collapsed every downstream stage:

    odds_rows -> 0 -> raw_candidates -> 0 -> elite picks -> 0
"""
from __future__ import annotations

import inspect

import courtvision_ai


def test_build_player_prop_identity_lookup_accepts_game_ids_kwarg() -> None:
    sig = inspect.signature(
        courtvision_ai.BallDontLieClient._build_player_prop_identity_lookup
    )
    assert "game_ids" in sig.parameters, (
        "Caller in BallDontLieClient.get_odds invokes "
        "_build_player_prop_identity_lookup(game_date, game_ids=...). "
        "Renaming this parameter silently breaks the player lookup and "
        "collapses the downstream pipeline to zero candidates."
    )


def test_get_odds_calls_lookup_builder_with_supported_kwargs(monkeypatch) -> None:
    """The production call must use a kwarg the method actually accepts."""
    import pandas as pd

    client = courtvision_ai.BallDontLieClient(api_key="test-key")

    captured: dict[str, object] = {}

    def fake_lookup(self, game_date, game_ids=None):  # noqa: ARG001
        captured["game_date"] = game_date
        captured["game_ids"] = list(game_ids or [])
        return {}

    monkeypatch.setattr(
        courtvision_ai.BallDontLieClient,
        "_build_player_prop_identity_lookup",
        fake_lookup,
    )
    monkeypatch.setattr(client, "_get", lambda *a, **k: {"data": []})

    df = client.get_odds("2026-04-26", game_ids=[1, 2, 3])

    assert isinstance(df, pd.DataFrame)
    assert captured["game_date"] == "2026-04-26"
    assert captured["game_ids"] == [1, 2, 3]
