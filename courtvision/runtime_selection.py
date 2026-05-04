from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from courtvision.calibration.buckets import injury_influence_value, player_points_line_band, player_profile_bucket


@dataclass(frozen=True, slots=True)
class PlayerSelectionConfig:
    hard_min_minutes: float = 14.0
    soft_minutes_buffer: float = 4.0
    max_soft_edge_multiplier_penalty: float = 0.10
    min_soft_confidence_penalty: float = 0.01
    max_soft_confidence_penalty: float = 0.03


@dataclass(frozen=True, slots=True)
class QualificationGateConfig:
    strong_edge_multiplier: float = 1.25
    strong_edge_confidence_buffer: float = 0.04
    strong_confidence_bonus: float = 0.05
    strong_confidence_edge_multiplier: float = 0.82


@dataclass(frozen=True, slots=True)
class BoardVolumeConfig:
    quality_rescue_min_quality_score: float = 74.0
    quality_rescue_confidence_buffer: float = 0.05
    quality_rescue_edge_ratio: float = 0.88
    quality_rescue_min_minutes: float = 28.0
    quality_rescue_min_market_trust: float = 0.90
    elite_min_size: int = 6
    elite_target_size: int = 8
    elite_backfill_min_quality_score: float = 82.0
    elite_backfill_min_confidence: float = 0.60
    elite_backfill_min_edge_abs: float = 1.8
    elite_backfill_min_minutes: float = 30.0
    full_market_min_size: int = 20
    full_market_target_size: int = 24
    full_market_backfill_min_quality_score: float = 68.0
    full_market_backfill_min_confidence: float = 0.57
    full_market_backfill_min_edge_abs: float = 1.0
    full_market_backfill_min_minutes: float = 24.0
    elite_backfill_allowed_gate_modes: tuple[str, ...] = ("live_quality_rescue", "core_pass")
    full_market_backfill_allowed_gate_modes: tuple[str, ...] = ("live_quality_rescue", "core_pass")
    allowed_quality_rescue_markets: tuple[str, ...] = (
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_3pt_made",
    )


class PlayerSelectionPolicy:
    def __init__(self, config: PlayerSelectionConfig | None = None) -> None:
        self.config = config or PlayerSelectionConfig()

    def evaluate_minutes_gate(
        self,
        minutes_avg: Any,
        minutes_recent: Any,
        min_threshold: Any,
        threshold_overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        avg_value = self.to_float(minutes_avg) or 0.0
        recent_value = self.to_float(minutes_recent)
        if recent_value is None:
            recent_value = avg_value

        threshold_value = self.to_float(min_threshold) or 0.0
        effective_minutes = max(avg_value, recent_value * 0.92)
        hard_floor = max(self.config.hard_min_minutes, threshold_value - self.config.soft_minutes_buffer)
        shortfall = max(0.0, threshold_value - effective_minutes)

        overrides = {
            "edge_multiplier": 1.0,
            "edge_delta": 0.0,
            "confidence_delta": 0.0,
        }
        if threshold_overrides:
            overrides.update(
                {
                    "edge_multiplier": self.to_float(threshold_overrides.get("edge_multiplier")) or 1.0,
                    "edge_delta": self.to_float(threshold_overrides.get("edge_delta")) or 0.0,
                    "confidence_delta": self.to_float(threshold_overrides.get("confidence_delta")) or 0.0,
                }
            )

        result = {
            "mode": "clear",
            "effective_minutes": round(float(effective_minutes), 4),
            "minutes_threshold": round(float(threshold_value), 4),
            "hard_reject_floor": round(float(hard_floor), 4),
            "minutes_shortfall": round(float(shortfall), 4),
            "threshold_overrides": overrides,
        }

        if effective_minutes >= threshold_value:
            return result

        if effective_minutes < hard_floor:
            result["mode"] = "hard_reject"
            return result

        result["mode"] = "soft_penalty"
        penalty_window = max(threshold_value - hard_floor, 0.001)
        severity = min(1.0, shortfall / penalty_window)

        overrides = dict(overrides)
        overrides["edge_multiplier"] *= 1.0 + (self.config.max_soft_edge_multiplier_penalty * severity)
        confidence_penalty = self.config.min_soft_confidence_penalty + (
            (self.config.max_soft_confidence_penalty - self.config.min_soft_confidence_penalty) * severity
        )
        overrides["confidence_delta"] += confidence_penalty
        result["threshold_overrides"] = overrides
        return result

    @staticmethod
    def to_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None


class QualificationGatePolicy:
    def __init__(self, config: QualificationGateConfig | None = None) -> None:
        self.config = config or QualificationGateConfig()

    def evaluate(
        self,
        edge_abs: Any,
        confidence: Any,
        edge_threshold: Any,
        confidence_threshold: Any,
    ) -> dict[str, Any]:
        edge_value = max(0.0, PlayerSelectionPolicy.to_float(edge_abs) or 0.0)
        confidence_value = max(0.0, PlayerSelectionPolicy.to_float(confidence) or 0.0)
        edge_floor = max(0.05, PlayerSelectionPolicy.to_float(edge_threshold) or 0.05)
        confidence_floor = min(max(PlayerSelectionPolicy.to_float(confidence_threshold) or 0.35, 0.35), 0.95)

        edge_pass = edge_value >= edge_floor
        confidence_pass = confidence_value >= confidence_floor

        strong_edge_pass = (
            edge_value >= (edge_floor * self.config.strong_edge_multiplier)
            and confidence_value >= max(0.35, confidence_floor - self.config.strong_edge_confidence_buffer)
        )
        strong_confidence_pass = (
            confidence_value >= min(0.95, confidence_floor + self.config.strong_confidence_bonus)
            and edge_value >= max(0.05, edge_floor * self.config.strong_confidence_edge_multiplier)
        )

        qualification_mode = "rejected"
        qualified = False
        if edge_pass and confidence_pass:
            qualified = True
            qualification_mode = "core_pass"
        elif strong_edge_pass:
            qualified = True
            qualification_mode = "strong_edge_override"
        elif strong_confidence_pass:
            qualified = True
            qualification_mode = "strong_confidence_override"

        return {
            "qualified": qualified,
            "qualification_mode": qualification_mode,
            "edge_pass": edge_pass,
            "confidence_pass": confidence_pass,
            "strong_edge_pass": strong_edge_pass,
            "strong_confidence_pass": strong_confidence_pass,
            "edge_threshold": round(float(edge_floor), 4),
            "confidence_threshold": round(float(confidence_floor), 4),
        }


class BoardVolumePolicy:
    def __init__(self, config: BoardVolumeConfig | None = None) -> None:
        self.config = config or BoardVolumeConfig()

    def is_live_quality_rescue_candidate(
        self,
        row: Mapping[str, Any],
        *,
        edge_threshold: Any,
        confidence_threshold: Any,
        market_trust_weight: Any,
    ) -> bool:
        market_type = str(row.get("market_type", "")).strip().lower()
        if market_type not in self.config.allowed_quality_rescue_markets:
            return False
        if self._is_synthetic(row) or not self._is_live_market(row):
            return False
        if (PlayerSelectionPolicy.to_float(market_trust_weight) or 0.0) < self.config.quality_rescue_min_market_trust:
            return False
        if self._projected_minutes(row) < self.config.quality_rescue_min_minutes:
            return False

        quality_score = PlayerSelectionPolicy.to_float(row.get("quality_score")) or 0.0
        confidence = PlayerSelectionPolicy.to_float(row.get("confidence")) or 0.0
        edge_abs = self._edge_abs(row)
        edge_floor = PlayerSelectionPolicy.to_float(edge_threshold) or 0.0
        confidence_floor = PlayerSelectionPolicy.to_float(confidence_threshold) or 0.0

        if quality_score < self.config.quality_rescue_min_quality_score:
            return False
        if edge_abs < max(0.5, edge_floor * self.config.quality_rescue_edge_ratio):
            return False
        if confidence < max(0.35, confidence_floor - self.config.quality_rescue_confidence_buffer):
            return False
        return True

    def is_elite_backfill_candidate(self, row: Mapping[str, Any]) -> bool:
        if not self._is_live_board_candidate(row):
            return False
        market_type = str(row.get("market_type", "")).strip().lower()
        if market_type == "moneyline":
            return False
        if not market_type.startswith("player_"):
            return False
        if not passes_elite_points_risk_guard(row):
            return False
        if not passes_player_points_strong_over_calibration(row):
            return False
        qualification_gate_mode = str(row.get("qualification_gate_mode", "")).strip()
        if qualification_gate_mode not in self.config.elite_backfill_allowed_gate_modes:
            return False
        if self._projected_minutes(row) < self.config.elite_backfill_min_minutes:
            return False
        if self._edge_abs(row) < self.config.elite_backfill_min_edge_abs:
            return False
        if (PlayerSelectionPolicy.to_float(row.get("confidence")) or 0.0) < self.config.elite_backfill_min_confidence:
            return False
        if (PlayerSelectionPolicy.to_float(row.get("quality_score")) or 0.0) < self.config.elite_backfill_min_quality_score:
            return False
        return True

    def is_full_market_backfill_candidate(self, row: Mapping[str, Any]) -> bool:
        if not self._is_live_board_candidate(row):
            return False
        market_type = str(row.get("market_type", "")).strip().lower()
        if market_type == "moneyline":
            return False
        if not market_type.startswith("player_"):
            return False
        qualification_gate_mode = str(row.get("qualification_gate_mode", "")).strip()
        if qualification_gate_mode not in self.config.full_market_backfill_allowed_gate_modes:
            return False
        if self._projected_minutes(row) < self.config.full_market_backfill_min_minutes:
            return False
        if self._edge_abs(row) < self.config.full_market_backfill_min_edge_abs:
            return False
        if (PlayerSelectionPolicy.to_float(row.get("confidence")) or 0.0) < self.config.full_market_backfill_min_confidence:
            return False
        if (PlayerSelectionPolicy.to_float(row.get("quality_score")) or 0.0) < self.config.full_market_backfill_min_quality_score:
            return False
        return True

    def backfill_priority(self, row: Mapping[str, Any]) -> int:
        qualification_gate_mode = str(row.get("qualification_gate_mode", "")).strip()
        if qualification_gate_mode == "live_quality_rescue":
            return 2
        if qualification_gate_mode == "core_pass":
            return 1
        return 0

    @staticmethod
    def _edge_abs(row: Mapping[str, Any]) -> float:
        edge_abs = PlayerSelectionPolicy.to_float(row.get("edge_abs"))
        if edge_abs is not None:
            return max(0.0, edge_abs)
        return abs(PlayerSelectionPolicy.to_float(row.get("edge")) or 0.0)

    @staticmethod
    def _projected_minutes(row: Mapping[str, Any]) -> float:
        avg_value = PlayerSelectionPolicy.to_float(row.get("minutes_avg")) or 0.0
        recent_value = PlayerSelectionPolicy.to_float(row.get("minutes_recent")) or avg_value
        return max(avg_value, recent_value)

    def _is_live_board_candidate(self, row: Mapping[str, Any]) -> bool:
        if self._is_synthetic(row) or not self._is_live_market(row):
            return False
        line_source = str(row.get("line_source", "")).strip().lower()
        if line_source not in {"", "live_market"}:
            return False
        return True

    @staticmethod
    def _is_live_market(row: Mapping[str, Any]) -> bool:
        value = row.get("is_live_market")
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _is_synthetic(row: Mapping[str, Any]) -> bool:
        value = row.get("synthetic_line")
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y"}


def elite_points_risk_guard_reason(row: Mapping[str, Any]) -> str:
    market_type = str(row.get("market_type", "")).strip().lower()
    if market_type != "player_points":
        return ""

    selection = str(row.get("selection", "")).strip().lower()
    confidence = PlayerSelectionPolicy.to_float(row.get("confidence")) or 0.0
    sportsbook_line = PlayerSelectionPolicy.to_float(row.get("sportsbook_line"))
    if sportsbook_line is None:
        sportsbook_line = PlayerSelectionPolicy.to_float(row.get("line_value")) or 0.0
    quality_score = PlayerSelectionPolicy.to_float(row.get("quality_score")) or 0.0
    profile_bucket = player_profile_bucket(row)
    team_injury_impact = max(
        PlayerSelectionPolicy.to_float(row.get("selection_team_injury_impact")) or 0.0,
        PlayerSelectionPolicy.to_float(row.get("team_injury_impact")) or 0.0,
        0.0,
    )

    if selection == "over":
        if profile_bucket == "role_low_usage" and sportsbook_line <= 16.5 and team_injury_impact >= 0.25:
            if confidence < 0.70 or quality_score < 90.0:
                return "injury_driven_low_line_over"
        return ""

    if selection == "under":
        if profile_bucket == "role_low_usage":
            if confidence < 0.74:
                return "weak_role_under"
        if profile_bucket == "starter_secondary" and sportsbook_line <= 26.5:
            if confidence < 0.71:
                return "weak_secondary_under"
    return ""


def passes_elite_points_risk_guard(row: Mapping[str, Any]) -> bool:
    return elite_points_risk_guard_reason(row) == ""


# Calibration guard: historical player_points OVER picks at edge >= +3 hit only
# 41.6% (n=127); at edge >= +6, only 35.7% (n=15). Until projection model is
# recalibrated, block strong OVERs from elite/Kelly. Diagnostic-only otherwise:
# rows remain in Full Market.
PLAYER_POINTS_STRONG_OVER_CALIBRATION_EDGE_THRESHOLD: float = 3.0
PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON: str = (
    "player_points_strong_over_calibration_guard"
)


def player_points_strong_over_calibration_reason(row: Mapping[str, Any]) -> str:
    """Return the strong-OVER calibration block reason, or '' if not blocked.

    Triggers only on player_points OVER picks where edge >= +3.0. UNDERs and
    non-player_points markets always return ''.

    Defers to the high-caution-conflicted context safety gate when that gate
    would already block this row, so existing telemetry counters stay stable.
    The row is still blocked from elite/Kelly — just attributed to the
    context gate's rejection reason instead of this guard's.
    """
    market_type = str(row.get("market_type", "")).strip().lower()
    if market_type != "player_points":
        return ""
    selection = str(row.get("selection", "")).strip().lower()
    if selection != "over":
        return ""
    edge = PlayerSelectionPolicy.to_float(row.get("edge"))
    if edge is None:
        edge_abs = PlayerSelectionPolicy.to_float(row.get("edge_abs"))
        if edge_abs is None:
            return ""
        edge = float(edge_abs)
    if edge < PLAYER_POINTS_STRONG_OVER_CALIBRATION_EDGE_THRESHOLD:
        return ""
    caution = str(row.get("context_caution_level", "")).strip().lower()
    alignment = str(row.get("context_pick_alignment", "")).strip().lower()
    if caution == "high" and alignment == "conflicted":
        return ""
    return PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON


def passes_player_points_strong_over_calibration(row: Mapping[str, Any]) -> bool:
    return player_points_strong_over_calibration_reason(row) == ""


def elite_points_recent_form_ratio(row: Mapping[str, Any]) -> float:
    direct_ratio = PlayerSelectionPolicy.to_float(row.get("recent_form_ratio"))
    if direct_ratio is not None:
        return max(0.0, float(direct_ratio))

    recent_vs_line = PlayerSelectionPolicy.to_float(row.get("recent_vs_line"))
    if recent_vs_line is not None:
        sportsbook_line = PlayerSelectionPolicy.to_float(row.get("sportsbook_line"))
        if sportsbook_line is None:
            sportsbook_line = PlayerSelectionPolicy.to_float(row.get("line_value")) or 0.0
        baseline = max(abs(float(sportsbook_line)), 12.0)
        return max(0.0, min(1.5, 1.0 + (float(recent_vs_line) / baseline)))

    recent_avg = PlayerSelectionPolicy.to_float(row.get("recent_avg"))
    sportsbook_line = PlayerSelectionPolicy.to_float(row.get("sportsbook_line"))
    if sportsbook_line is None:
        sportsbook_line = PlayerSelectionPolicy.to_float(row.get("line_value"))
    if recent_avg is not None and sportsbook_line is not None:
        return max(0.0, min(1.5, float(recent_avg) / max(abs(float(sportsbook_line)), 12.0)))

    season_avg = PlayerSelectionPolicy.to_float(row.get("season_avg"))
    if season_avg is not None and sportsbook_line is not None:
        return max(0.0, min(1.5, float(season_avg) / max(abs(float(sportsbook_line)), 12.0)))

    return 1.0


def elite_points_ranking_pressure(row: Mapping[str, Any]) -> dict[str, Any]:
    market_type = str(row.get("market_type", "")).strip().lower()
    if market_type != "player_points":
        return {"penalty": 0.0, "reason": "", "recent_form_ratio": elite_points_recent_form_ratio(row)}

    selection = str(row.get("selection", "")).strip().lower()
    confidence = PlayerSelectionPolicy.to_float(row.get("confidence")) or 0.0
    quality_score = PlayerSelectionPolicy.to_float(row.get("quality_score")) or 0.0
    sportsbook_line = PlayerSelectionPolicy.to_float(row.get("sportsbook_line"))
    if sportsbook_line is None:
        sportsbook_line = PlayerSelectionPolicy.to_float(row.get("line_value")) or 0.0
    profile_bucket = player_profile_bucket(row)
    line_band = player_points_line_band(row)
    injury_influence = injury_influence_value(row)
    recent_form_ratio = elite_points_recent_form_ratio(row)

    candidates: list[tuple[str, float]] = []
    if selection == "under":
        if profile_bucket == "starter_secondary" and line_band == "gte_27":
            if confidence < 0.76 or quality_score < 95.0:
                candidates.append(("borderline_secondary_under", 6.0))
        if profile_bucket == "role_low_usage" and confidence < 0.80:
            candidates.append(("borderline_role_under", 4.5))
    elif selection == "over":
        if profile_bucket == "role_low_usage" and line_band in {"lte_14_5", "15_to_19_5"}:
            if injury_influence >= 0.15:
                candidates.append(("injury_influenced_points_over", 6.0 if injury_influence >= 0.25 else 3.5))
            if recent_form_ratio < 0.95:
                candidates.append(("weak_role_scoring_form", 5.0 if recent_form_ratio < 0.80 else 2.5))

    if not candidates:
        return {"penalty": 0.0, "reason": "", "recent_form_ratio": round(float(recent_form_ratio), 4)}

    total_penalty = round(float(sum(penalty for _, penalty in candidates)), 4)
    dominant_reason = max(candidates, key=lambda item: item[1])[0]
    return {
        "penalty": total_penalty,
        "reason": dominant_reason,
        "recent_form_ratio": round(float(recent_form_ratio), 4),
    }


# =============================================================================
# ELITE CANDIDATE FILTERING
# =============================================================================

def get_elite_rejection_reason(row: dict[str, Any]) -> str | None:
    """Return rejection reason if row fails elite criteria, else None."""
    from courtvision.runtime_audit import (
        REJECT_INJURY_FLAG,
        REJECT_MINUTES_VOLATILITY,
        REJECT_CONFIDENCE_BELOW_THRESHOLD,
        REJECT_PROJECTION_REALISM,
        REJECT_EXPOSURE_LIMIT,
        REJECT_NEGATIVE_EDGE_DIRECTION,
    )

    market = str(row.get("market", "")).lower()
    selection = str(row.get("selection", "")).lower()
    edge = float(row.get("edge_pct", row.get("edge", 0.0)) or 0.0)

    # Check flags in priority order - mirror your actual gate sequence
    if bool(row.get("injury_flag", False)):
        return REJECT_INJURY_FLAG

    if bool(row.get("minutes_volatility_fail", False)):
        return REJECT_MINUTES_VOLATILITY

    if bool(row.get("confidence_fail", False)):
        return REJECT_CONFIDENCE_BELOW_THRESHOLD

    if bool(row.get("projection_realism_fail", False)):
        return REJECT_PROJECTION_REALISM

    if bool(row.get("exposure_limit_fail", False)):
        return REJECT_EXPOSURE_LIMIT

    # Only validate player_points edge direction
    if market == "player_points":
        if selection == "over" and edge <= 0:
            return REJECT_NEGATIVE_EDGE_DIRECTION
        if selection == "under" and edge >= 0:
            return REJECT_NEGATIVE_EDGE_DIRECTION

    return None


def filter_elite_candidates(
    candidates: list[dict[str, Any]],
    slate_date: str,
) -> tuple[list[dict[str, Any]], Any]:
    """Filter candidates and collect telemetry."""
    from courtvision.runtime_audit import EliteTelemetry
    telemetry = EliteTelemetry(slate_date=slate_date)
    passed: list[dict[str, Any]] = []

    for row in candidates:
        market = str(row.get("market", "unknown"))
        selection = str(row.get("selection", "unknown")).lower()

        telemetry.record_candidate_seen(
            market=market,
            selection_side=selection,
        )

        rejection_reason = get_elite_rejection_reason(row)

        if rejection_reason is None:
            telemetry.record_pass(
                market=market,
                selection_side=selection,
            )
            passed.append(row)
            continue

        telemetry.record(
            market=market,
            selection_side=selection,
            rejection_reason=rejection_reason,
        )

    return passed, telemetry


# ---- Game status / slate-lock gate ----------------------------------------
# Prevent completed or already-started games from producing actionable picks.

#: Default lock buffer minutes before game start
DEFAULT_GAME_LOCK_BUFFER_MINUTES: int = 10

#: Game statuses that indicate game is not yet started/bettable
GAME_STATUS_SCHEDULED: set[str] = {
    "scheduled", "pregame", "pre", "not_started", "pending", "upcoming", "open",
    # Note: "unknown" is NOT included - requires datetime validation in betting mode
}

#: Game statuses that indicate game is in progress (not bettable)
GAME_STATUS_IN_PROGRESS: set[str] = {
    "in_progress", "live", "1st", "2nd", "3rd", "4th", "ot", "halftime",
    "q1", "q2", "q3", "q4", "quarter_1", "quarter_2", "quarter_3", "quarter_4",
}

#: Game statuses that indicate game is complete (not bettable)
GAME_STATUS_FINAL: set[str] = {
    "final", "completed", "done", "finished", "ft", "over", "ended"
}

#: Game statuses that indicate game is postponed/cancelled (not bettable)
GAME_STATUS_CANCELLED: set[str] = {
    "postponed", "cancelled", "canceled", "suspended", "delayed", "abandoned"
}


def _parse_game_datetime(dt_raw: Any) -> datetime | None:
    """Parse game datetime from various formats."""
    from datetime import datetime
    if dt_raw is None:
        return None
    if isinstance(dt_raw, datetime):
        return dt_raw
    dt_str = str(dt_raw).strip()
    if not dt_str or dt_str.lower() in ("none", "null", "", "na", "n/a"):
        return None
    # Remove 'Z' and handle timezone
    dt_str = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        # Try without timezone
        try:
            return datetime.fromisoformat(dt_str.split("+")[0].split(".")[0])
        except ValueError:
            return None


def _is_before_lock_buffer(
    game_dt: datetime,
    now: Any,
    lock_buffer_minutes: int,
) -> bool:
    """Check if current time is before game start minus lock buffer."""
    from datetime import datetime, timedelta
    if now is None:
        return True  # Assume bettable if no reference time
    if isinstance(now, datetime):
        now_dt = now
    else:
        now_dt = datetime.now(game_dt.tzinfo) if game_dt.tzinfo else datetime.now()
    lock_time = game_dt - timedelta(minutes=lock_buffer_minutes)
    return now_dt < lock_time


def game_status_ineligibility_reason(
    row: Mapping[str, Any],
    now: Any | None = None,
    lock_buffer_minutes: int = DEFAULT_GAME_LOCK_BUFFER_MINUTES,
) -> str:
    """Return the game status ineligibility reason, or '' if bettable.

    A game is actionable only if:
    - status is scheduled/pregame (not started) OR unknown with valid future datetime
    - current time is before scheduled start minus lock buffer
    - game is not Final/completed
    - game is not in progress
    - game is not postponed/cancelled

    Betting mode (COURTVISION_MODE=betting):
    - scheduled/pregame: allowed only if before game start minus lock buffer
    - unknown: allowed only if valid future datetime outside lock buffer
    - unknown + missing datetime: blocked with 'game_status_unknown'
    - unknown + past datetime: blocked with 'game_locked'
    - final/live/postponed: always blocked

    Research mode (COURTVISION_MODE=research):
    - All checks bypassed

    Args:
        row: Candidate row with game status fields
        now: Current datetime (defaults to datetime.now())
        lock_buffer_minutes: Minutes before game start to lock betting

    Returns:
        Empty string if game is bettable, otherwise a reason code:
        - "game_final": Game is complete
        - "game_in_progress": Game is live/in progress
        - "game_postponed": Game is postponed or cancelled
        - "game_locked": Current time is within lock buffer of game start
        - "game_status_unknown": Cannot determine game status
    """
    import os

    # Check for COURTVISION_MODE - research mode bypasses game status checks
    mode = os.environ.get("COURTVISION_MODE", "betting").strip().lower()
    if mode == "research":
        return ""

    # Get game status from row
    status = str(row.get("game_status", row.get("status", ""))).strip().lower()

    # Check if game is already complete
    if status in GAME_STATUS_FINAL:
        return "game_final"

    # Check if game is in progress
    if status in GAME_STATUS_IN_PROGRESS:
        return "game_in_progress"

    # Check if game is postponed/cancelled
    if status in GAME_STATUS_CANCELLED:
        return "game_postponed"

    # Parse game datetime for lock buffer checks
    game_datetime_raw = row.get("game_datetime") or row.get("game_date") or row.get("datetime")
    game_dt = _parse_game_datetime(game_datetime_raw)

    # Handle "unknown" status (from normalize_games_schema default)
    # In betting mode, unknown requires datetime validation
    if status == "unknown" or not status:
        if game_dt is None:
            # Unknown status and no datetime - cannot determine if bettable
            return "game_status_unknown"
        # Have datetime - check if game is in the future outside lock buffer
        if not _is_before_lock_buffer(game_dt, now, lock_buffer_minutes):
            # Game has started or is within lock buffer
            return "game_locked"
        # Game is in the future outside lock buffer - treat as bettable
        return ""

    # For known scheduled/pregame statuses, also check datetime
    if status in GAME_STATUS_SCHEDULED:
        if game_dt is not None:
            # Have datetime - verify game hasn't started
            if not _is_before_lock_buffer(game_dt, now, lock_buffer_minutes):
                return "game_locked"
        # No datetime but status is scheduled - allow (trust the status)
        return ""

    # Truly unrecognized status - be conservative and block
    # Check if it's numeric (quarter/period indicator)
    if status.isdigit() or status in ("q1", "q2", "q3", "q4", "ot", "halftime"):
        return "game_in_progress"

    return "game_status_unknown"


def is_game_bettable(
    row: Mapping[str, Any],
    now: Any | None = None,
    lock_buffer_minutes: int = DEFAULT_GAME_LOCK_BUFFER_MINUTES,
) -> bool:
    """Return True if the game's candidates can be bet on.

    Convenience wrapper around game_status_ineligibility_reason.
    """
    return game_status_ineligibility_reason(row, now, lock_buffer_minutes) == ""


__all__ = [
    "BoardVolumeConfig",
    "BoardVolumePolicy",
    "PlayerSelectionConfig",
    "PlayerSelectionPolicy",
    "QualificationGateConfig",
    "QualificationGatePolicy",
    "elite_points_ranking_pressure",
    "elite_points_risk_guard_reason",
    "elite_points_recent_form_ratio",
    "passes_elite_points_risk_guard",
    "PLAYER_POINTS_STRONG_OVER_CALIBRATION_EDGE_THRESHOLD",
    "PLAYER_POINTS_STRONG_OVER_CALIBRATION_GUARD_REASON",
    "player_points_strong_over_calibration_reason",
    "passes_player_points_strong_over_calibration",
    "get_elite_rejection_reason",
    "filter_elite_candidates",
    # Game status gate exports
    "DEFAULT_GAME_LOCK_BUFFER_MINUTES",
    "GAME_STATUS_SCHEDULED",
    "GAME_STATUS_IN_PROGRESS",
    "GAME_STATUS_FINAL",
    "GAME_STATUS_CANCELLED",
    "game_status_ineligibility_reason",
    "is_game_bettable",
]
