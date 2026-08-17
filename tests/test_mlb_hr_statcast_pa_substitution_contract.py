from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from courtvision.sports.mlb.data import context_source_pack as sources
from courtvision.sports.mlb.training import hr_context_features as features


RAW_COLUMNS = (
    "game_pk",
    "game_date",
    "at_bat_number",
    "pitch_number",
    "batter",
    "pitcher",
    "stand",
    "p_throws",
    "home_team",
    "away_team",
    "inning",
    "inning_topbot",
    "events",
    "description",
    "pitch_type",
    "release_speed",
    "launch_speed",
    "launch_angle",
    "bb_type",
)

CLOCK_COLUMNS = (
    "game_id",
    "game_completed_at_utc",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
)


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: list[dict[str, object]],
) -> Path:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
            lineterminator="\n",
        )

        writer.writeheader()
        writer.writerows(rows)

    return path


def _clock(path: Path) -> Path:

    return _write_csv(
        path,
        CLOCK_COLUMNS,
        [
            {
                "game_id": "765400",
                "game_completed_at_utc":
                    "2026-06-01T22:00:00Z",
                "provider_published_at_utc":
                    "2026-06-01T22:05:00Z",
                "first_observed_at_utc":
                    "2026-06-01T22:06:00Z",
                "captured_at_utc":
                    "2026-06-01T22:07:00Z",
            }
        ],
    )


def _raw_pitch(
    *,
    pitch_number: int,
    batter: str = "100001",
    pitcher: str = "200001",
    stand: str = "R",
    p_throws: str = "R",
    inning_half: str = "Top",
    events: str = "",
) -> dict[str, object]:

    return {
        "game_pk": "765400",
        "game_date": "2026-06-01",
        "at_bat_number": "46",
        "pitch_number": pitch_number,
        "batter": batter,
        "pitcher": pitcher,
        "stand": stand,
        "p_throws": p_throws,
        "home_team": "TOR",
        "away_team": "BOS",
        "inning": "6",
        "inning_topbot": inning_half,
        "events": events,
        "description": (
            "swinging_strike"
            if events
            else "foul"
        ),
        "pitch_type": "FF",
        "release_speed": "95.0",
        "launch_speed": "",
        "launch_angle": "",
        "bb_type": "",
    }


def _normalize(
    tmp_path: Path,
    rows: list[dict[str, object]],
):

    statcast = _write_csv(
        tmp_path / "statcast.csv",
        RAW_COLUMNS,
        rows,
    )

    clocks = _clock(
        tmp_path / "clocks.csv"
    )

    return sources.normalize_statcast_pitch_csv(
        statcast,
        clocks,
        captured_at_utc="2026-06-01T22:10:00Z",
    )


def _feature_pitch(
    *,
    pitch_number: int,
    batter_id: str = "100001",
    pitcher_id: str = "200001",
    batter_hand: str = "R",
    pitcher_hand: str = "R",
    batter_team: str = "BOS",
    pitcher_team: str = "TOR",
    event_type: str = "",
) -> dict[str, object]:

    return {
        "game_id": "765400",
        "game_date": "2026-06-01",
        "game_completed_at_utc":
            "2026-06-01T22:00:00Z",
        "completion_evidence_type":
            "legacy_exact_completion_clock",
        "completion_witnessed_at_utc": "",
        "provider_published_at_utc":
            "2026-06-01T22:05:00Z",
        "first_observed_at_utc":
            "2026-06-01T22:06:00Z",
        "captured_at_utc":
            "2026-06-01T22:07:00Z",
        "plate_appearance_id":
            "765400:46",
        "pitch_number": pitch_number,
        "pitch_id":
            f"765400:46:{pitch_number}",
        "is_terminal_pa":
            bool(event_type),
        "batter_id": batter_id,
        "pitcher_id": pitcher_id,
        "batter_hand": batter_hand,
        "pitcher_hand": pitcher_hand,
        "home_team": "TOR",
        "away_team": "BOS",
        "batter_team": batter_team,
        "pitcher_team": pitcher_team,
        "inning": 6,
        "inning_half": "top",
        "event_type": event_type,
        "description": (
            "swinging_strike"
            if event_type
            else "foul"
        ),
        "is_home_run": False,
        "pitch_type": "FF",
        "release_speed": 95.0,
        "launch_speed": None,
        "launch_angle": None,
        "is_barrel": None,
        "estimated_woba": None,
        "estimated_slg": None,
        "batted_ball_type": None,
        "is_pull": None,
    }


def test_source_pack_accepts_mid_pa_batter_substitution(
    tmp_path: Path,
) -> None:

    rows = _normalize(
        tmp_path,
        [
            _raw_pitch(
                pitch_number=1,
                batter="100001",
                stand="R",
            ),
            _raw_pitch(
                pitch_number=2,
                batter="100002",
                stand="L",
                events="strikeout",
            ),
        ],
    )

    ordered = sorted(
        rows,
        key=lambda row: int(
            row["pitch_number"]
        ),
    )

    assert [
        row["batter_id"]
        for row in ordered
    ] == [
        "100001",
        "100002",
    ]

    assert [
        row["batter_hand"]
        for row in ordered
    ] == [
        "R",
        "L",
    ]

    assert len(
        {
            row["plate_appearance_id"]
            for row in ordered
        }
    ) == 1


def test_source_pack_accepts_mid_pa_pitcher_substitution(
    tmp_path: Path,
) -> None:

    rows = _normalize(
        tmp_path,
        [
            _raw_pitch(
                pitch_number=1,
                pitcher="200001",
                p_throws="R",
            ),
            _raw_pitch(
                pitch_number=2,
                pitcher="200002",
                p_throws="L",
                events="strikeout",
            ),
        ],
    )

    ordered = sorted(
        rows,
        key=lambda row: int(
            row["pitch_number"]
        ),
    )

    assert [
        row["pitcher_id"]
        for row in ordered
    ] == [
        "200001",
        "200002",
    ]

    assert [
        row["pitcher_hand"]
        for row in ordered
    ] == [
        "R",
        "L",
    ]


def test_source_pack_still_rejects_structural_pa_mutation(
    tmp_path: Path,
) -> None:

    with pytest.raises(
        sources.ContextSourceError,
        match="inconsistent Statcast PA identity",
    ):
        _normalize(
            tmp_path,
            [
                _raw_pitch(
                    pitch_number=1,
                    inning_half="Top",
                ),
                _raw_pitch(
                    pitch_number=2,
                    inning_half="Bot",
                    events="strikeout",
                ),
            ],
        )


def test_feature_parser_accepts_mid_pa_batter_substitution() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
                batter_id="100001",
                batter_hand="R",
            ),
            _feature_pitch(
                pitch_number=2,
                batter_id="100002",
                batter_hand="L",
                event_type="strikeout",
            ),
        )
    )

    parsed = features._parse_statcast(
        snapshot
    )

    assert [
        row.batter_id
        for row in parsed
    ] == [
        "100001",
        "100002",
    ]

    assert [
        row.batter_hand
        for row in parsed
    ] == [
        "R",
        "L",
    ]


def test_feature_parser_accepts_mid_pa_pitcher_substitution() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
                pitcher_id="200001",
                pitcher_hand="R",
            ),
            _feature_pitch(
                pitch_number=2,
                pitcher_id="200002",
                pitcher_hand="L",
                event_type="strikeout",
            ),
        )
    )

    parsed = features._parse_statcast(
        snapshot
    )

    assert [
        row.pitcher_id
        for row in parsed
    ] == [
        "200001",
        "200002",
    ]

    assert [
        row.pitcher_hand
        for row in parsed
    ] == [
        "R",
        "L",
    ]


def test_feature_parser_still_rejects_structural_pa_mutation() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
                batter_team="BOS",
                pitcher_team="TOR",
            ),
            _feature_pitch(
                pitch_number=2,
                batter_team="TOR",
                pitcher_team="BOS",
                event_type="strikeout",
            ),
        )
    )

    with pytest.raises(
        features.ContextFeatureError,
        match="inconsistent Statcast PA identity",
    ):
        features._parse_statcast(
            snapshot
        )
def test_source_pack_accepts_zero_terminal_fragment_without_synthesis(
    tmp_path: Path,
) -> None:

    rows = _normalize(
        tmp_path,
        [
            _raw_pitch(
                pitch_number=1,
            ),
            _raw_pitch(
                pitch_number=2,
            ),
        ],
    )

    assert len(rows) == 2

    assert all(
        row["is_terminal_pa"] == "false"
        for row in rows
    )

    assert all(
        row["event_type"] == ""
        for row in rows
    )


def test_source_pack_rejects_multiple_terminal_rows(
    tmp_path: Path,
) -> None:

    with pytest.raises(
        sources.ContextSourceError,
        match="at most one terminal row",
    ):

        _normalize(
            tmp_path,
            [
                _raw_pitch(
                    pitch_number=1,
                    events="strikeout",
                ),
                _raw_pitch(
                    pitch_number=2,
                    events="field_out",
                ),
            ],
        )


def test_source_pack_terminal_row_if_present_must_be_final(
    tmp_path: Path,
) -> None:

    with pytest.raises(
        sources.ContextSourceError,
        match="terminal Statcast row is not the final pitch",
    ):

        _normalize(
            tmp_path,
            [
                _raw_pitch(
                    pitch_number=1,
                    events="strikeout",
                ),
                _raw_pitch(
                    pitch_number=2,
                ),
            ],
        )


def test_feature_parser_accepts_zero_terminal_fragment_without_synthesis() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
            ),
            _feature_pitch(
                pitch_number=2,
            ),
        )
    )

    parsed = features._parse_statcast(
        snapshot
    )

    assert len(parsed) == 2

    assert all(
        row.event_type is None
        for row in parsed
    )

    assert all(
        not row.is_terminal
        for row in parsed
    )


def test_feature_parser_rejects_multiple_terminal_rows() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
                event_type="strikeout",
            ),
            _feature_pitch(
                pitch_number=2,
                event_type="field_out",
            ),
        )
    )

    with pytest.raises(
        features.ContextFeatureError,
        match="at most one terminal event",
    ):

        features._parse_statcast(
            snapshot
        )


def test_feature_parser_terminal_if_present_must_be_final() -> None:

    snapshot = SimpleNamespace(
        rows=(
            _feature_pitch(
                pitch_number=1,
                event_type="strikeout",
            ),
            _feature_pitch(
                pitch_number=2,
            ),
        )
    )

    with pytest.raises(
        features.ContextFeatureError,
        match="terminal Statcast event is not the final pitch",
    ):

        features._parse_statcast(
            snapshot
        )
