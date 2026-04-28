from __future__ import annotations

import warnings

import pandas as pd

from courtvision_ai import CourtVisionAI


def test_append_history_filters_empty_frames_before_concat(tmp_path) -> None:
    ai = CourtVisionAI.__new__(CourtVisionAI)
    path = tmp_path / "history.csv"
    pd.DataFrame({"player_name": [pd.NA], "edge": [pd.NA]}).to_csv(path, index=False)
    new_rows = pd.DataFrame({"player_name": ["Jalen Green"], "edge": [3.4]})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", FutureWarning)
        ai._append_history(path, new_rows)

    assert not any("DataFrame concatenation with empty or all-NA entries" in str(item.message) for item in caught)
    out = pd.read_csv(path)
    assert out.to_dict("records") == [{"player_name": "Jalen Green", "edge": 3.4}]


def test_append_history_filters_all_na_columns_before_concat(tmp_path) -> None:
    ai = CourtVisionAI.__new__(CourtVisionAI)
    path = tmp_path / "history.csv"
    pd.DataFrame({"player_name": ["Existing Player"], "edge": [1.2], "notes": [pd.NA]}).to_csv(path, index=False)
    new_rows = pd.DataFrame({"player_name": ["Jalen Green"], "edge": [3.4], "notes": [pd.NA]})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", FutureWarning)
        ai._append_history(path, new_rows)

    assert not any("DataFrame concatenation with empty or all-NA entries" in str(item.message) for item in caught)
    out = pd.read_csv(path)
    assert out["player_name"].tolist() == ["Existing Player", "Jalen Green"]
    assert out["edge"].tolist() == [1.2, 3.4]
    assert "notes" in out.columns
