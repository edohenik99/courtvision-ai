"""Tests for scripts/record_manual_review_decision.py."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.record_manual_review_decision import (
    MANUAL_REVIEW_HISTORY_COLUMNS,
    VALID_DECISIONS,
    main,
    record_decision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kelly_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "kelly_stakes_2026-05-07.csv"
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _kelly_row(
    *,
    player_name: str = "Ajay Mitchell",
    market_type: str = "player_points",
    selection: str = "under",
    line: str = "16.5",
    stake_amount: str = "20.0",
    manual_review_required: str = "True",
) -> dict[str, str]:
    review = manual_review_required == "True"
    return {
        "prediction_date": "2026-05-07",
        "player_name": player_name,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "stake_amount": stake_amount,
        "team_abbr": "OKC",
        "opponent": "LAL",
        "recommended_action": "REVIEW_BEFORE_BET" if review else "OK_TO_CONSIDER",
        "review_status": "REVIEW_REQUIRED" if review else "CLEAR",
        "stake_policy": "HOLD" if review else "NORMAL",
        "operator_action": "DO_NOT_BET_UNTIL_REVIEWED" if review else "OK_TO_CONSIDER",
        "operator_note": "manual_review_reason=same_opponent_under_warning" if review else "",
        "manual_review_required": manual_review_required,
        "manual_review_reason": "same_opponent_under_warning" if review else "",
        "same_opponent_under_warning": "True" if review else "False",
        "same_opponent_warning_reason": (
            "last_same_opponent_actual_exceeded_current_under_line" if review else ""
        ),
        "eligible": "True",
        "skip_reason": "",
    }


def _read_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Decision recording tests
# ---------------------------------------------------------------------------

class TestRecordManualReviewDecision:

    def test_record_skip_creates_history_row(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        result = record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="skip",
            decision_reason="same_opponent_under_warning",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        rows = _read_history(history_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["decision"] == "skip"
        assert row["decision_reason"] == "same_opponent_under_warning"
        assert row["player_name"] == "Ajay Mitchell"
        assert row["prediction_date"] == "2026-05-07"
        assert row["stake_policy"] == "HOLD"
        assert row["operator_action"] == "DO_NOT_BET_UNTIL_REVIEWED"
        assert row["stake_amount"] == "20.0"
        assert row["created_at"] != ""
        assert row["updated_at"] != ""
        assert row["created_at"] == row["updated_at"]

    def test_record_play_creates_history_row(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="play",
            decision_reason="accepted_risk",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        rows = _read_history(history_path)
        assert len(rows) == 1
        assert rows[0]["decision"] == "play"
        assert rows[0]["decision_reason"] == "accepted_risk"

    def test_record_reduce_stake(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="reduce_stake",
            decision_reason="partial_confidence",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        rows = _read_history(history_path)
        assert rows[0]["decision"] == "reduce_stake"

    # ------------------------------------------------------------------
    # Update existing decision
    # ------------------------------------------------------------------

    def test_updating_existing_decision_changes_values(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="undecided",
            decision_reason="",
            kelly_path=kelly_path,
            history_path=history_path,
            _now="2026-05-07T10:00:00+00:00",
        )

        first_rows = _read_history(history_path)
        assert first_rows[0]["decision"] == "undecided"
        original_created_at = first_rows[0]["created_at"]
        assert original_created_at == "2026-05-07T10:00:00+00:00"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="skip",
            decision_reason="reconsidered",
            kelly_path=kelly_path,
            history_path=history_path,
            _now="2026-05-07T10:05:00+00:00",
        )

        updated_rows = _read_history(history_path)
        assert len(updated_rows) == 1, "must not duplicate the row"
        u = updated_rows[0]
        assert u["decision"] == "skip"
        assert u["decision_reason"] == "reconsidered"
        assert u["created_at"] == original_created_at, "created_at must be preserved"
        assert u["updated_at"] == "2026-05-07T10:05:00+00:00", "updated_at must reflect second write"

    # ------------------------------------------------------------------
    # No duplicate rows
    # ------------------------------------------------------------------

    def test_no_duplicate_rows_for_same_pick(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        for decision in ("undecided", "skip", "play"):
            record_decision(
                prediction_date="2026-05-07",
                player_name="Ajay Mitchell",
                market_type="player_points",
                selection="under",
                decision=decision,
                kelly_path=kelly_path,
                history_path=history_path,
            )

        rows = _read_history(history_path)
        assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
        assert rows[0]["decision"] == "play"

    def test_different_picks_create_separate_rows(self, tmp_path):
        kelly_path = _kelly_csv(
            tmp_path,
            [
                _kelly_row(player_name="Ajay Mitchell"),
                _kelly_row(player_name="Cade Cunningham", manual_review_required="False"),
            ],
        )
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="skip",
            kelly_path=kelly_path,
            history_path=history_path,
        )
        record_decision(
            prediction_date="2026-05-07",
            player_name="Cade Cunningham",
            market_type="player_points",
            selection="under",
            decision="play",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        rows = _read_history(history_path)
        assert len(rows) == 2
        decisions = {r["player_name"]: r["decision"] for r in rows}
        assert decisions["Ajay Mitchell"] == "skip"
        assert decisions["Cade Cunningham"] == "play"

    # ------------------------------------------------------------------
    # CSV column schema
    # ------------------------------------------------------------------

    def test_history_csv_contains_all_required_columns(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="skip",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        with history_path.open("r", encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh))

        missing = [col for col in MANUAL_REVIEW_HISTORY_COLUMNS if col not in header]
        assert missing == [], f"missing columns: {missing}"

    # ------------------------------------------------------------------
    # Missing Kelly file gives clear error
    # ------------------------------------------------------------------

    def test_missing_kelly_file_raises_system_exit(self, tmp_path):
        missing_path = tmp_path / "kelly_stakes_9999-01-01.csv"
        history_path = tmp_path / "manual_review_history.csv"

        with pytest.raises(SystemExit) as exc_info:
            record_decision(
                prediction_date="9999-01-01",
                player_name="Ghost Player",
                market_type="player_points",
                selection="under",
                decision="skip",
                kelly_path=missing_path,
                history_path=history_path,
            )

        assert "FATAL" in str(exc_info.value)
        assert "kelly_stakes" in str(exc_info.value).lower() or "9999-01-01" in str(exc_info.value)

    # ------------------------------------------------------------------
    # CLI entry point
    # ------------------------------------------------------------------

    def test_cli_main_records_decision(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        rc = main([
            "--prediction-date", "2026-05-07",
            "--player-name", "Ajay Mitchell",
            "--market-type", "player_points",
            "--selection", "under",
            "--decision", "skip",
            "--reason", "same_opponent_under_warning",
            "--kelly-csv", str(kelly_path),
            "--history-csv", str(history_path),
        ])

        assert rc == 0
        rows = _read_history(history_path)
        assert len(rows) == 1
        assert rows[0]["decision"] == "skip"
        assert rows[0]["decision_reason"] == "same_opponent_under_warning"

    def test_cli_invalid_decision_exits_nonzero(self, tmp_path, capsys):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        with pytest.raises(SystemExit) as exc_info:
            main([
                "--prediction-date", "2026-05-07",
                "--player-name", "Ajay Mitchell",
                "--market-type", "player_points",
                "--selection", "under",
                "--decision", "NOT_A_VALID_DECISION",
                "--kelly-csv", str(kelly_path),
                "--history-csv", str(history_path),
            ])

        assert exc_info.value.code != 0

    # ------------------------------------------------------------------
    # Kelly fields are copied into history row
    # ------------------------------------------------------------------

    def test_kelly_fields_propagated_to_history(self, tmp_path):
        kelly_path = _kelly_csv(tmp_path, [_kelly_row()])
        history_path = tmp_path / "manual_review_history.csv"

        record_decision(
            prediction_date="2026-05-07",
            player_name="Ajay Mitchell",
            market_type="player_points",
            selection="under",
            decision="skip",
            kelly_path=kelly_path,
            history_path=history_path,
        )

        row = _read_history(history_path)[0]
        assert row["team_abbr"] == "OKC"
        assert row["opponent"] == "LAL"
        assert row["line"] == "16.5"
        assert row["stake_amount"] == "20.0"
        assert row["review_status"] == "REVIEW_REQUIRED"
        assert row["stake_policy"] == "HOLD"
        assert row["operator_action"] == "DO_NOT_BET_UNTIL_REVIEWED"
        assert row["manual_review_required"] == "True"
        assert row["same_opponent_under_warning"] == "True"

    # ------------------------------------------------------------------
    # Appending across multiple dates keeps all rows
    # ------------------------------------------------------------------

    def test_decisions_accumulate_across_dates(self, tmp_path):
        history_path = tmp_path / "manual_review_history.csv"
        for date, player in [("2026-05-06", "Player A"), ("2026-05-07", "Player B")]:
            date_dir = tmp_path / date
            date_dir.mkdir(parents=True, exist_ok=True)
            kelly_path = _kelly_csv(date_dir, [_kelly_row(player_name=player)])
            record_decision(
                prediction_date=date,
                player_name=player,
                market_type="player_points",
                selection="under",
                decision="skip",
                kelly_path=kelly_path,
                history_path=history_path,
            )

        rows = _read_history(history_path)
        assert len(rows) == 2
        dates = {r["prediction_date"] for r in rows}
        assert dates == {"2026-05-06", "2026-05-07"}
