from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

BOARD_FILE_PREFIX = "shadow_candidate_lane"
REPORT_FILE_PREFIX = "shadow_candidate_lane_report"
REPORT_VERSION = "1.0"

UNDER_ALIGNED_RESEARCH = "UNDER_ALIGNED_RESEARCH"
COMBO_OVER_WEAK_POSITIVE_RESEARCH = "COMBO_OVER_WEAK_POSITIVE_RESEARCH"
NEAR_ELITE_RESEARCH = "NEAR_ELITE_RESEARCH"
INCUBATOR_RESEARCH = "INCUBATOR_RESEARCH"
HIGH_CAUTION_OVER_DO_NOT_PROMOTE = "HIGH_CAUTION_OVER_DO_NOT_PROMOTE"

PROMOTION_STATUS = "SHADOW_ONLY_DO_NOT_PROMOTE"
EXCLUDED_UNSAFE_BUCKETS = {
    ("player_points", "over", "high", "conflicted"),
    ("player_rebounds", "over", "high", "conflicted"),
    ("player_assists", "over", "high", "conflicted"),
}
COMBO_OVER_MARKETS = {
    "player_points_rebounds",
    "player_points_assists",
    "player_rebounds_assists",
    "player_points_rebounds_assists",
}
POSITIVE_RECOMMENDATIONS = {"SHADOW_ONLY", "FUTURE_THRESHOLD_REVIEW"}

LANE_PRIORITY = {
    UNDER_ALIGNED_RESEARCH: 500,
    COMBO_OVER_WEAK_POSITIVE_RESEARCH: 400,
    NEAR_ELITE_RESEARCH: 300,
    INCUBATOR_RESEARCH: 250,
    HIGH_CAUTION_OVER_DO_NOT_PROMOTE: 100,
}

SOURCE_PRIORITY = {
    "incubator_board": 5,
    "near_elite_review": 4,
    "full_market_board": 1,
}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "prediction_date",
    "source_artifact_date",
    "source_board",
    "research_lane",
    "rank_score",
    "player_id",
    "player_name",
    "team_abbr",
    "opponent",
    "game_id",
    "market_type",
    "selection",
    "line",
    "odds",
    "model_projection",
    "edge",
    "confidence",
    "quality_score",
    "selection_score",
    "context_pick_alignment",
    "context_caution_level",
    "source_rejection_reason",
    "historical_bucket_key",
    "historical_recommendation",
    "historical_graded_rows",
    "historical_hit_rate",
    "historical_roi",
    "historical_clv_coverage_rate",
    "lane_reason",
    "safety_warning",
    "sample_warning",
    "clv_warning",
    "promotion_status",
    "real_money_eligible",
    "kelly_eligible",
    "elite_eligible",
    "shadow_only",
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return default
    return text


def _safe_lower(value: Any, default: str = "unknown") -> str:
    text = _safe_text(value, default=default).lower()
    return text if text else default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, keep_default_na=False, low_memory=False)
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _date_from_name(path: Path) -> str:
    match = re.search(r"_(\d{4}-\d{2}-\d{2})\.(?:csv|json|txt)$", path.name)
    return match.group(1) if match else ""


def _latest_date_for_prefix(operator_dir: Path, prefix: str, prediction_date: str) -> str:
    dates = []
    for path in operator_dir.glob(f"{prefix}_*.csv"):
        date_text = _date_from_name(path)
        if date_text and date_text <= prediction_date:
            dates.append(date_text)
    return sorted(dates)[-1] if dates else prediction_date


def resolve_source_artifact_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> str:
    operator_dir = Path(runtime_root) / "operator"
    exact_full = operator_dir / f"full_market_board_{prediction_date}.csv"
    if exact_full.exists():
        return prediction_date
    return _latest_date_for_prefix(operator_dir, "full_market_board", prediction_date)


def _latest_json_payload(
    *,
    runtime_root: Path,
    prediction_date: str,
    stem: str,
) -> tuple[str, Path | None, dict[str, Any]]:
    diagnostics_dir = runtime_root / "diagnostics"
    candidates: list[tuple[str, Path]] = []
    for path in diagnostics_dir.glob(f"{stem}_*.json"):
        date_text = _date_from_name(path)
        if date_text and date_text <= prediction_date:
            candidates.append((date_text, path))
    if not candidates:
        return "", None, {}
    candidates.sort(key=lambda item: item[0])
    date_text, path = candidates[-1]
    return date_text, path, _read_json(path)


def _first_text(row: pd.Series, columns: tuple[str, ...], default: str = "") -> str:
    for column in columns:
        value = _safe_text(row.get(column))
        if value:
            return value
    return default


def _source_rejection_reason(row: pd.Series, source_board: str) -> str:
    reason = _first_text(
        row,
        (
            "source_rejection_reason",
            "final_elite_rejection_reason",
            "elite_rejection_reason",
            "selection_rejection_reason",
            "kelly_projected_skip_reason",
            "rejection_reason",
            "review_reason",
            "incubator_reason",
        ),
    )
    return reason or source_board


def _context_label(row: pd.Series) -> str:
    return _first_text(row, ("context_pick_alignment", "context_alignment", "context_edge_label"), default="unknown")


def _player_name(row: pd.Series) -> str:
    return _first_text(row, ("player_name", "player", "entity_name"), default="Unknown")


def _team(row: pd.Series) -> str:
    return _first_text(row, ("team_abbr", "team", "resolved_team_abbr"), default="")


def _line_token(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return _safe_text(value).lower()
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    identity = _safe_text(row.get("player_id")).lower() or _safe_text(row.get("player_name")).lower()
    return (
        identity,
        _safe_text(row.get("market_type")).lower(),
        _safe_text(row.get("selection")).lower(),
        _line_token(row.get("line")),
        _safe_text(row.get("game_id")).lower(),
    )


def _normalized_candidate(row: pd.Series, *, source_board: str, source_artifact_date: str, prediction_date: str) -> dict[str, Any]:
    market_type = _safe_lower(row.get("market_type") or row.get("market"), default="unknown")
    selection = _safe_lower(row.get("selection"), default="unknown")
    context = _safe_lower(row.get("context_caution_level"), default="unknown")
    alignment = _safe_lower(_context_label(row), default="unknown")
    line = _safe_float(row.get("line") if _safe_text(row.get("line")) else row.get("sportsbook_line"))
    odds = _safe_float(row.get("odds") if _safe_text(row.get("odds")) else row.get("entry_odds"))
    edge = _safe_float(row.get("edge") if _safe_text(row.get("edge")) else row.get("side_edge"))
    return {
        "prediction_date": prediction_date,
        "source_artifact_date": source_artifact_date,
        "source_board": source_board,
        "player_id": _safe_text(row.get("player_id")),
        "player_name": _player_name(row),
        "team_abbr": _team(row),
        "opponent": _safe_text(row.get("opponent")),
        "game_id": _safe_text(row.get("game_id")),
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "odds": odds,
        "model_projection": _safe_float(
            row.get("model_projection") if _safe_text(row.get("model_projection")) else row.get("projection")
        ),
        "edge": edge,
        "confidence": _safe_float(row.get("confidence")),
        "quality_score": _safe_float(row.get("quality_score")),
        "selection_score": _safe_float(row.get("selection_score")),
        "context_pick_alignment": alignment,
        "context_caution_level": context,
        "source_rejection_reason": _safe_lower(_source_rejection_reason(row, source_board), default=source_board),
    }


def _load_current_candidates(
    *,
    prediction_date: str,
    runtime_root: Path,
    source_artifact_date: str,
) -> list[dict[str, Any]]:
    operator_dir = runtime_root / "operator"
    sources = (
        ("full_market_board", operator_dir / f"full_market_board_{source_artifact_date}.csv"),
        ("near_elite_review", operator_dir / f"near_elite_review_{source_artifact_date}.csv"),
        ("incubator_board", operator_dir / f"incubator_board_{source_artifact_date}.csv"),
    )
    candidates: list[dict[str, Any]] = []
    for source_board, path in sources:
        df = _read_csv(path)
        if df.empty:
            continue
        for _, row in df.iterrows():
            candidates.append(
                _normalized_candidate(
                    row,
                    source_board=source_board,
                    source_artifact_date=source_artifact_date,
                    prediction_date=prediction_date,
                )
            )
    return candidates


def _is_aligned(candidate: dict[str, Any]) -> bool:
    selection = _safe_lower(candidate.get("selection"), default="")
    label = _safe_lower(candidate.get("context_pick_alignment"), default="")
    if label == "aligned":
        return True
    if selection == "under" and label in {"supports_under", "under_aligned"}:
        return True
    if selection == "over" and label in {"supports_over", "over_aligned"}:
        return True
    return False


def _is_obvious_unsafe_bucket(candidate: dict[str, Any]) -> bool:
    return (
        _safe_lower(candidate.get("market_type"), default=""),
        _safe_lower(candidate.get("selection"), default=""),
        _safe_lower(candidate.get("context_caution_level"), default=""),
        _safe_lower(candidate.get("context_pick_alignment"), default=""),
    ) in EXCLUDED_UNSAFE_BUCKETS


def _is_combo_over(candidate: dict[str, Any]) -> bool:
    return (
        _safe_lower(candidate.get("selection"), default="") == "over"
        and _safe_lower(candidate.get("market_type"), default="") in COMBO_OVER_MARKETS
    )


def _is_high_caution_over(candidate: dict[str, Any]) -> bool:
    return (
        _safe_lower(candidate.get("selection"), default="") == "over"
        and _safe_lower(candidate.get("context_caution_level"), default="") == "high"
    )


def _safe_action_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("recommendation_matrix")
    return rows if isinstance(rows, list) else []


def _row_score(candidate: dict[str, Any], evidence: dict[str, Any]) -> int:
    score = 0
    for column in ("market_type", "selection", "context_caution_level", "context_edge_label"):
        candidate_value = candidate.get("context_pick_alignment") if column == "context_edge_label" else candidate.get(column)
        if _safe_lower(evidence.get(column), default="") == _safe_lower(candidate_value, default=""):
            score += 10
    if _safe_lower(evidence.get("source_rejection_reason"), default="") == _safe_lower(
        candidate.get("source_rejection_reason"),
        default="",
    ):
        score += 6
    if _safe_text(evidence.get("recommendation")) in POSITIVE_RECOMMENDATIONS:
        score += 3
    return score


def _selection_evidence(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    selection = _safe_lower(candidate.get("selection"), default="")
    matches = [
        row
        for row in rows
        if _safe_text(row.get("bucket_scope")) == "selection"
        and _safe_lower(row.get("selection"), default="") == selection
    ]
    if not matches:
        return {}
    return sorted(matches, key=lambda row: int(row.get("graded_rows") or 0), reverse=True)[0]


def _best_evidence(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    market = _safe_lower(candidate.get("market_type"), default="")
    selection = _safe_lower(candidate.get("selection"), default="")
    caution = _safe_lower(candidate.get("context_caution_level"), default="")
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        if _safe_lower(row.get("market_type"), default="") not in {market, "all"}:
            continue
        if _safe_lower(row.get("selection"), default="") not in {selection, "all"}:
            continue
        if _safe_lower(row.get("context_caution_level"), default="") not in {caution, "all"}:
            continue
        score = _row_score(candidate, row)
        if score <= 0:
            continue
        scored.append((score, int(row.get("graded_rows") or 0), row))
    if scored:
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return scored[0][2]
    return _selection_evidence(candidate, rows)


def _evidence_positive_or_neutral(evidence: dict[str, Any]) -> bool:
    if not evidence:
        return False
    if _safe_text(evidence.get("recommendation")) == "KEEP_BLOCKED":
        return False
    roi = _safe_float(evidence.get("roi"))
    hit_rate = _safe_float(evidence.get("hit_rate"))
    if roi is not None and roi < 0:
        return False
    if hit_rate is not None and hit_rate < 0.50:
        return False
    return True


def _evidence_positive_shadow(evidence: dict[str, Any]) -> bool:
    if not evidence:
        return False
    if _safe_text(evidence.get("recommendation")) not in POSITIVE_RECOMMENDATIONS:
        return False
    roi = _safe_float(evidence.get("roi"))
    hit_rate = _safe_float(evidence.get("hit_rate"))
    return bool((roi is not None and roi > 0) and (hit_rate is not None and hit_rate >= 0.53))


def _sample_warning(evidence: dict[str, Any]) -> str:
    graded = int(evidence.get("graded_rows") or 0) if evidence else 0
    if graded < 20:
        return "Fewer than 20 graded rows; no conclusion."
    if graded < 50:
        return "20-49 graded rows; weak directional signal only."
    if graded < 100:
        return "50-99 graded rows; moderate evidence only."
    return "100+ graded rows, but this lane remains research-only."


def _clv_warning(evidence: dict[str, Any]) -> str:
    coverage = _safe_float(evidence.get("clv_coverage_rate")) if evidence else None
    if coverage is None:
        return "CLV coverage unavailable; no promotion review."
    if coverage < 0.50:
        return "CLV coverage below 50%; no promotion review."
    return "CLV coverage present, but this board is still not a promotion signal."


def _lane_reason(lane: str, evidence: dict[str, Any]) -> str:
    graded = int(evidence.get("graded_rows") or 0) if evidence else 0
    hit_rate = _safe_float(evidence.get("hit_rate")) if evidence else None
    roi = _safe_float(evidence.get("roi")) if evidence else None
    metric = f"historical n={graded}, hit={hit_rate:.1%}, ROI={roi:.1%}" if hit_rate is not None and roi is not None else f"historical n={graded}"
    if lane == UNDER_ALIGNED_RESEARCH:
        return f"Low-caution/context-aligned UNDER candidate with positive or neutral shadow evidence ({metric})."
    if lane == COMBO_OVER_WEAK_POSITIVE_RESEARCH:
        return f"Combo OVER candidate has weak positive shadow evidence ({metric}); remains paper-only."
    if lane == NEAR_ELITE_RESEARCH:
        return "Near-Elite review artifact candidate; manual research only, not Elite."
    if lane == INCUBATOR_RESEARCH:
        return "Incubator artifact candidate; paper-only evidence collection."
    return "High-caution OVER candidate retained only as do-not-promote shadow context."


def _safety_warning(lane: str) -> str:
    if lane == COMBO_OVER_WEAK_POSITIVE_RESEARCH:
        return "Combo OVER positive signal is weak, high-caution/conflicted, and not promotion-ready."
    if lane == HIGH_CAUTION_OVER_DO_NOT_PROMOTE:
        return "High-caution OVER gate remains strict; do not promote."
    if lane == NEAR_ELITE_RESEARCH:
        return "Near-Elite rows are not Elite picks and are not Kelly/staking inputs."
    if lane == INCUBATOR_RESEARCH:
        return "Incubator rows are paper-only and are not staking inputs."
    return "Shadow-only research row; not a betting board, Elite board, or Kelly input."


def _classify_lane(candidate: dict[str, Any], evidence: dict[str, Any]) -> str:
    source = _safe_text(candidate.get("source_board"))
    if source == "incubator_board":
        return INCUBATOR_RESEARCH
    if source == "near_elite_review":
        return NEAR_ELITE_RESEARCH
    if _is_obvious_unsafe_bucket(candidate):
        return ""
    if (
        _safe_lower(candidate.get("selection"), default="") == "under"
        and (_safe_lower(candidate.get("context_caution_level"), default="") == "low" or _is_aligned(candidate))
        and _evidence_positive_or_neutral(evidence)
    ):
        return UNDER_ALIGNED_RESEARCH
    if _is_combo_over(candidate) and _evidence_positive_shadow(evidence):
        return COMBO_OVER_WEAK_POSITIVE_RESEARCH
    if _is_high_caution_over(candidate):
        return HIGH_CAUTION_OVER_DO_NOT_PROMOTE
    return ""


def _rank_score(candidate: dict[str, Any], evidence: dict[str, Any], lane: str) -> float:
    edge = abs(_safe_float(candidate.get("edge")) or 0.0)
    confidence = _safe_float(candidate.get("confidence")) or 0.0
    quality = _safe_float(candidate.get("quality_score")) or 0.0
    roi = _safe_float(evidence.get("roi")) if evidence else None
    hit_rate = _safe_float(evidence.get("hit_rate")) if evidence else None
    return round(
        LANE_PRIORITY.get(lane, 0)
        + confidence * 100.0
        + quality * 0.5
        + edge * 4.0
        + ((roi or 0.0) * 50.0)
        + ((hit_rate or 0.0) * 30.0),
        4,
    )


def _shadow_row(candidate: dict[str, Any], evidence: dict[str, Any], lane: str) -> dict[str, Any]:
    return {
        **candidate,
        "research_lane": lane,
        "rank_score": _rank_score(candidate, evidence, lane),
        "historical_bucket_key": _safe_text(evidence.get("bucket_key")) if evidence else "",
        "historical_recommendation": _safe_text(evidence.get("recommendation")) if evidence else "",
        "historical_graded_rows": int(evidence.get("graded_rows") or 0) if evidence else 0,
        "historical_hit_rate": _safe_float(evidence.get("hit_rate")) if evidence else None,
        "historical_roi": _safe_float(evidence.get("roi")) if evidence else None,
        "historical_clv_coverage_rate": _safe_float(evidence.get("clv_coverage_rate")) if evidence else None,
        "lane_reason": _lane_reason(lane, evidence),
        "safety_warning": _safety_warning(lane),
        "sample_warning": _sample_warning(evidence),
        "clv_warning": _clv_warning(evidence),
        "promotion_status": PROMOTION_STATUS,
        "real_money_eligible": False,
        "kelly_eligible": False,
        "elite_eligible": False,
        "shadow_only": True,
    }


def build_shadow_candidate_lane(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    source_artifact_date: str | None = None,
    generated_at_utc: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    del history_root  # Reserved for future read-only history context; never written by this report.
    runtime_root_path = Path(runtime_root)
    source_date = source_artifact_date or resolve_source_artifact_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
    )
    safe_action_date, safe_action_path, safe_action_payload = _latest_json_payload(
        runtime_root=runtime_root_path,
        prediction_date=prediction_date,
        stem="safe_action_discovery_report",
    )
    evidence_rows = _safe_action_rows(safe_action_payload)
    current_candidates = _load_current_candidates(
        prediction_date=prediction_date,
        runtime_root=runtime_root_path,
        source_artifact_date=source_date,
    )

    lane_rows: list[dict[str, Any]] = []
    excluded_unsafe = 0
    for candidate in current_candidates:
        evidence = _best_evidence(candidate, evidence_rows)
        if candidate["source_board"] == "full_market_board" and _is_obvious_unsafe_bucket(candidate):
            excluded_unsafe += 1
            continue
        lane = _classify_lane(candidate, evidence)
        if not lane:
            continue
        lane_rows.append(_shadow_row(candidate, evidence, lane))

    deduped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in lane_rows:
        key = _candidate_key(row)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = row
            continue
        existing_priority = SOURCE_PRIORITY.get(_safe_text(existing.get("source_board")), 0)
        row_priority = SOURCE_PRIORITY.get(_safe_text(row.get("source_board")), 0)
        if (row_priority, float(row.get("rank_score") or 0.0)) > (
            existing_priority,
            float(existing.get("rank_score") or 0.0),
        ):
            deduped[key] = row

    board_rows = sorted(deduped.values(), key=lambda row: (-float(row["rank_score"]), row["player_name"]))
    for rank, row in enumerate(board_rows, start=1):
        row["rank"] = rank
    board_df = pd.DataFrame(board_rows)
    if board_df.empty:
        board_df = pd.DataFrame(columns=("rank", *OUTPUT_COLUMNS))
    else:
        board_df = board_df.reindex(columns=("rank", *OUTPUT_COLUMNS))

    lane_counts = Counter(row["research_lane"] for row in board_rows)
    all_flags_safe = bool(
        board_df.empty
        or (
            (~board_df["real_money_eligible"].astype(bool))
            & (~board_df["kelly_eligible"].astype(bool))
            & (~board_df["elite_eligible"].astype(bool))
            & board_df["shadow_only"].astype(bool)
        ).all()
    )
    payload = {
        "report_name": BOARD_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "prediction_date": prediction_date,
        "source_artifact_date": source_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "betting_logic_changed": False,
        "real_money_promotion_recommended": False,
        "all_rows_real_money_eligible_false": bool(
            board_df.empty or (~board_df["real_money_eligible"].astype(bool)).all()
        ),
        "all_rows_kelly_eligible_false": bool(board_df.empty or (~board_df["kelly_eligible"].astype(bool)).all()),
        "all_rows_elite_eligible_false": bool(board_df.empty or (~board_df["elite_eligible"].astype(bool)).all()),
        "all_rows_shadow_only": bool(board_df.empty or board_df["shadow_only"].astype(bool).all()),
        "all_flags_safe": all_flags_safe,
        "summary": {
            "source_candidate_rows_loaded": int(len(current_candidates)),
            "shadow_candidate_count": int(len(board_df)),
            "excluded_obvious_unsafe_full_market_rows": int(excluded_unsafe),
            "deduplicated_rows_removed": int(len(lane_rows) - len(board_rows)),
            "lane_counts": dict(sorted(lane_counts.items())),
        },
        "top_under_research_candidates": [
            row for row in board_rows if row["research_lane"] == UNDER_ALIGNED_RESEARCH
        ][:10],
        "top_combo_over_research_candidates": [
            row for row in board_rows if row["research_lane"] == COMBO_OVER_WEAK_POSITIVE_RESEARCH
        ][:10],
        "guardrails": [
            "This is not a betting board.",
            "This is not an Elite board.",
            "This is not a Kelly input.",
            "This is for future evidence collection only.",
            "Every row is shadow_only=True.",
            "Every row is real_money_eligible=False, kelly_eligible=False, and elite_eligible=False.",
            "High-caution OVER gates remain strict.",
            "Incubator and shadow rows are not promoted.",
            "pick_history is not written.",
            "Closed-slate boards are not regenerated.",
        ],
        "source_paths": {
            "runtime_root": str(runtime_root_path),
            "safe_action_discovery_report": str(safe_action_path or ""),
            "safe_action_discovery_report_date": safe_action_date,
            "full_market_board": str(runtime_root_path / "operator" / f"full_market_board_{source_date}.csv"),
            "near_elite_review": str(runtime_root_path / "operator" / f"near_elite_review_{source_date}.csv"),
            "incubator_board": str(runtime_root_path / "operator" / f"incubator_board_{source_date}.csv"),
        },
    }
    return _json_ready(payload), board_df


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, pd.DataFrame):
        return [_json_ready(row) for row in value.to_dict("records")]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def _fmt_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _candidate_line(row: dict[str, Any]) -> str:
    return (
        f"- #{row.get('rank')} {row.get('player_name')} {row.get('market_type')} "
        f"{row.get('selection')} line={row.get('line')} edge={row.get('edge')} "
        f"hist_n={row.get('historical_graded_rows')} "
        f"hit={_fmt_pct(row.get('historical_hit_rate'))} "
        f"roi={_fmt_pct(row.get('historical_roi'))}"
    )


def render_shadow_candidate_lane_text(payload: dict[str, Any], board_path: Path) -> str:
    summary = payload["summary"]
    lines = [
        f"CourtVision Shadow Candidate Lane Board - {payload['prediction_date']}",
        "=" * 78,
        "This is not a betting board. This is not an Elite board. This is not a Kelly input.",
        "This is for future evidence collection only.",
        f"CSV board: {board_path}",
        "",
        "Executive Summary",
        "-" * 78,
        f"- source artifact date: {payload['source_artifact_date']}",
        f"- source candidate rows loaded: {summary['source_candidate_rows_loaded']}",
        f"- shadow candidates: {summary['shadow_candidate_count']}",
        f"- lane counts: {summary['lane_counts']}",
        f"- obvious unsafe full-market rows excluded: {summary['excluded_obvious_unsafe_full_market_rows']}",
        f"- all rows real_money_eligible=False: {payload['all_rows_real_money_eligible_false']}",
        f"- all rows kelly_eligible=False: {payload['all_rows_kelly_eligible_false']}",
        f"- all rows elite_eligible=False: {payload['all_rows_elite_eligible_false']}",
        f"- all rows shadow_only=True: {payload['all_rows_shadow_only']}",
        "",
        "Count By Research Lane",
        "-" * 78,
    ]
    if summary["lane_counts"]:
        for lane, count in summary["lane_counts"].items():
            lines.append(f"- {lane}: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "Top UNDER Research Candidates", "-" * 78])
    under = payload.get("top_under_research_candidates", [])
    if under:
        lines.extend(_candidate_line(row) for row in under[:10])
    else:
        lines.append("- none")

    lines.extend(["", "Top Combo OVER Research Candidates", "-" * 78])
    combo = payload.get("top_combo_over_research_candidates", [])
    if combo:
        lines.extend(_candidate_line(row) for row in combo[:10])
    else:
        lines.append("- none")

    lines.extend(["", "Guardrails", "-" * 78])
    lines.extend(f"- {item}" for item in payload["guardrails"])
    return "\n".join(lines) + "\n"


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, Path]:
    runtime_root_path = Path(runtime_root)
    return (
        runtime_root_path / "operator" / f"{BOARD_FILE_PREFIX}_{prediction_date}.csv",
        runtime_root_path / "operator" / f"{REPORT_FILE_PREFIX}_{prediction_date}.txt",
        runtime_root_path / "diagnostics" / f"{BOARD_FILE_PREFIX}_{prediction_date}.json",
    )


def write_shadow_candidate_lane_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    history_root: str | Path = "data/history",
    source_artifact_date: str | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    board_path, text_path, json_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    payload, board_df = build_shadow_candidate_lane(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        source_artifact_date=source_artifact_date,
    )
    text = render_shadow_candidate_lane_text(payload, board_path)

    board_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    board_df.to_csv(board_path, index=False)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return board_path, text_path, json_path, payload


__all__ = [
    "BOARD_FILE_PREFIX",
    "COMBO_OVER_WEAK_POSITIVE_RESEARCH",
    "HIGH_CAUTION_OVER_DO_NOT_PROMOTE",
    "INCUBATOR_RESEARCH",
    "NEAR_ELITE_RESEARCH",
    "UNDER_ALIGNED_RESEARCH",
    "build_shadow_candidate_lane",
    "render_shadow_candidate_lane_text",
    "report_paths_for_date",
    "resolve_source_artifact_date",
    "write_shadow_candidate_lane_outputs",
]
