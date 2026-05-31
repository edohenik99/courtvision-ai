from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_VERSION = "1.0"
REPORT_FILE_PREFIX = "shadow_rule_proposals"
LEARNING_REPORT_FILE_PREFIX = "learning_brain_report"

STATUS_READY = "SHADOW_RULE_PROPOSALS_READY"
STATUS_READY_LOW_SAMPLE = "SHADOW_RULE_PROPOSALS_READY_LOW_SAMPLE"
STATUS_BLOCKED_MISSING_LEARNING_REPORT = "SHADOW_RULE_PROPOSALS_BLOCKED_BY_MISSING_LEARNING_REPORT"
STATUS_BLOCKED_DIRTY_DATA = "SHADOW_RULE_PROPOSALS_BLOCKED_BY_DIRTY_DATA"

RULE_UNDER_VISIBILITY_WATCHLIST = "UNDER_VISIBILITY_WATCHLIST"
RULE_NEAR_ELITE_WATCHLIST = "NEAR_ELITE_WATCHLIST"
RULE_COMBO_SHADOW_WATCHLIST = "COMBO_SHADOW_WATCHLIST"
RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST = "HIGH_CAUTION_COMBO_SHADOW_WATCHLIST"
RULE_DO_NOT_PROMOTE_BLOCK = "DO_NOT_PROMOTE_BLOCK"
RULE_DATA_COLLECTION = "DATA_COLLECTION_RULE"
RULE_MANUAL_REVIEW = "MANUAL_REVIEW_CANDIDATE_RULE"

RULE_TYPES: tuple[str, ...] = (
    RULE_UNDER_VISIBILITY_WATCHLIST,
    RULE_NEAR_ELITE_WATCHLIST,
    RULE_COMBO_SHADOW_WATCHLIST,
    RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST,
    RULE_DO_NOT_PROMOTE_BLOCK,
    RULE_DATA_COLLECTION,
    RULE_MANUAL_REVIEW,
)

RECOMMEND_KEEP_BLOCKED = "KEEP_BLOCKED"
RECOMMEND_KEEP_SHADOW = "KEEP_SHADOW"
RECOMMEND_WATCHLIST = "WATCHLIST"
RECOMMEND_MANUAL_REVIEW = "MANUAL_REVIEW_CANDIDATE"
RECOMMEND_PROPOSED_REQUIRES_APPROVAL = "PROPOSED_ONLY_REQUIRES_APPROVAL"
RECOMMEND_DO_NOT_PROMOTE = "DO_NOT_PROMOTE"

ALLOWED_OUTPUT_RECOMMENDATIONS: set[str] = {
    RECOMMEND_KEEP_BLOCKED,
    RECOMMEND_KEEP_SHADOW,
    RECOMMEND_WATCHLIST,
    RECOMMEND_MANUAL_REVIEW,
    RECOMMEND_PROPOSED_REQUIRES_APPROVAL,
    RECOMMEND_DO_NOT_PROMOTE,
}

SAFE_PROPOSAL_VALUES: dict[str, Any] = {
    "status": "PROPOSED_ONLY",
    "activation": "DISABLED",
    "requires_human_approval": True,
    "production_effect": False,
    "eligible_for_live_betting": False,
    "eligible_for_kelly": False,
    "eligible_for_elite": False,
    "shadow_only": True,
}

SAFETY_DECLARATIONS: tuple[str, ...] = (
    "No production rules activated.",
    "No live betting effect.",
    "No Kelly effect.",
    "No Elite effect.",
    "No final_decision effect.",
    "No history mutation.",
    "No pick_history mutation.",
    "Human approval is required before any future rule activation.",
    "Approval is not implemented in this phase.",
    "Production activation is not implemented in this phase.",
    "Any future activation requires a separate reviewed PR/commit.",
)

OPTIONAL_RUNTIME_ARTIFACTS: tuple[tuple[str, tuple[str, str]], ...] = (
    ("operator_learning_brain_txt", ("operator", "learning_brain_report_{date}.txt")),
    ("diagnostics_bet_readiness_json", ("diagnostics", "bet_readiness_report_{date}.json")),
    ("operator_full_market_board_csv", ("operator", "full_market_board_{date}.csv")),
    ("operator_near_elite_review_csv", ("operator", "near_elite_review_{date}.csv")),
    ("operator_shadow_candidate_lane_csv", ("operator", "shadow_candidate_lane_{date}.csv")),
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_lower(value: Any) -> str:
    return _safe_text(value).lower()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    number = _safe_float(value)
    if number is None:
        return 0
    return int(number)


def _slug(value: Any) -> str:
    text = _safe_lower(value)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"


def _item_bucket(item: dict[str, Any]) -> str:
    return _safe_text(item.get("bucket") or item.get("label") or item.get("source_bucket"), default="unknown")


def _source_bucket(item: dict[str, Any]) -> str:
    dimension = _safe_text(item.get("dimension"))
    bucket = _item_bucket(item)
    return f"{dimension}={bucket}" if dimension else bucket


def _item_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("bucket"),
        item.get("label"),
        item.get("dimension"),
        item.get("research_lane"),
        item.get("recommendation"),
        item.get("sample_quality_flag"),
    ]
    return " ".join(_safe_text(part) for part in parts if _safe_text(part)).lower()


def _normalize_output_recommendation(value: Any) -> str:
    text = _safe_text(value).upper()
    if text == "PROMOTION_CANDIDATE_REQUIRES_APPROVAL":
        return RECOMMEND_PROPOSED_REQUIRES_APPROVAL
    if text == "DEMOTE_OR_BLOCK":
        return RECOMMEND_DO_NOT_PROMOTE
    if text in ALLOWED_OUTPUT_RECOMMENDATIONS:
        return text
    return RECOMMEND_KEEP_SHADOW


def _is_low_sample(item: dict[str, Any], min_sample: int) -> bool:
    sample_quality = _safe_text(item.get("sample_quality_flag")).upper()
    return sample_quality in {"LOW_SAMPLE", "PENDING_HEAVY"} or _safe_int(item.get("graded_rows")) < int(min_sample)


def _is_pending_heavy(item: dict[str, Any]) -> bool:
    return _safe_text(item.get("sample_quality_flag")).upper() == "PENDING_HEAVY"


def _has_dirty_data(item: dict[str, Any]) -> bool:
    return _safe_text(item.get("sample_quality_flag")).upper() == "DIRTY_DATA" or _safe_int(item.get("dirty_data_rows")) > 0


def _has_negative_roi(item: dict[str, Any]) -> bool:
    roi = _safe_float(item.get("roi"))
    return roi is not None and roi < 0


def _is_same_opponent_bucket(item: dict[str, Any]) -> bool:
    text = _item_text(item)
    if _safe_text(item.get("dimension")) == "same_opponent_warning":
        return _safe_lower(item.get("bucket")) == "true"
    return "same-opponent warning" in text or "same opponent warning" in text


def _is_identity_conflict_bucket(item: dict[str, Any]) -> bool:
    text = _item_text(item)
    return "identity conflict" in text or "identity_conflict" in text or "source_identity_conflict" in text


def _is_unsupported_market_bucket(item: dict[str, Any]) -> bool:
    text = _item_text(item)
    return "unsupported market" in text or "unsupported_market" in text or _safe_int(item.get("unsupported_rows")) > 0


def _must_not_promote(item: dict[str, Any], min_sample: int) -> bool:
    return (
        _has_negative_roi(item)
        or _is_low_sample(item, min_sample)
        or _is_pending_heavy(item)
        or _has_dirty_data(item)
        or _is_same_opponent_bucket(item)
        or _is_identity_conflict_bucket(item)
        or _is_unsupported_market_bucket(item)
    )


def _research_lane(item: dict[str, Any]) -> str | None:
    direct = _safe_text(item.get("research_lane"))
    if direct:
        return direct
    dimension = _safe_text(item.get("dimension"))
    bucket = _item_bucket(item)
    text = _item_text(item)
    if dimension == "research_lane":
        return bucket
    if "under_aligned_research" in text:
        return "UNDER_ALIGNED_RESEARCH"
    if "combo_over_weak_positive_research" in text or "combo over weak positive" in text:
        return "COMBO_OVER_WEAK_POSITIVE_RESEARCH"
    if "near_elite_research" in text or "near-elite" in text or "near elite" in text:
        return "NEAR_ELITE_RESEARCH"
    if "incubator" in text:
        return "INCUBATOR_RESEARCH"
    return None


def _market_type(item: dict[str, Any]) -> str | None:
    direct = _safe_text(item.get("market_type"))
    if direct:
        return direct
    if _safe_text(item.get("dimension")) == "market_type":
        return _item_bucket(item)
    if _is_combo_item(item):
        return "combo"
    return None


def _selection(item: dict[str, Any]) -> str | None:
    direct = _safe_text(item.get("selection")).lower()
    if direct in {"under", "over"}:
        return direct
    if _safe_text(item.get("dimension")) == "selection":
        bucket = _safe_lower(_item_bucket(item))
        if bucket in {"under", "over"}:
            return bucket
    text = _item_text(item)
    if "under" in text:
        return "under"
    if "over" in text:
        return "over"
    return None


def _context_caution_level(item: dict[str, Any]) -> str | None:
    direct = _safe_lower(item.get("context_caution_level"))
    if direct:
        return direct
    if _safe_text(item.get("dimension")) == "context_caution_level":
        return _safe_lower(_item_bucket(item))
    text = _item_text(item)
    if "high-caution" in text or "high caution" in text:
        return "high"
    if "low-caution" in text or "low caution" in text:
        return "low"
    return None


def _context_pick_alignment(item: dict[str, Any]) -> str | None:
    direct = _safe_text(item.get("context_pick_alignment"))
    if direct:
        return direct
    if _safe_text(item.get("dimension")) == "context_pick_alignment":
        return _item_bucket(item)
    text = _item_text(item)
    if "conflicted" in text or "conflict" in text:
        return "conflicted"
    if "context-aligned" in text or "aligned" in text:
        return "aligned"
    return None


def _is_under_item(item: dict[str, Any]) -> bool:
    return _selection(item) == "under" or "under" in _item_text(item)


def _is_near_elite_item(item: dict[str, Any]) -> bool:
    lane = _research_lane(item)
    return lane == "NEAR_ELITE_RESEARCH" or "near-elite" in _item_text(item) or "near elite" in _item_text(item)


def _is_combo_item(item: dict[str, Any]) -> bool:
    text = _item_text(item)
    market = _safe_lower(item.get("market_type"))
    return "combo" in text or market in {
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }


def _is_high_caution_combo_over_item(item: dict[str, Any]) -> bool:
    return _is_combo_item(item) and _selection(item) == "over" and _context_caution_level(item) == "high"


def _is_interesting_data_collection_lane(item: dict[str, Any]) -> bool:
    lane = _research_lane(item)
    return lane in {"UNDER_ALIGNED_RESEARCH", "NEAR_ELITE_RESEARCH", "COMBO_OVER_WEAK_POSITIVE_RESEARCH"}


def _is_combo_market_type(value: Any) -> bool:
    return _safe_lower(value) in {
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }


def _is_explicit_block_matrix_item(item: dict[str, Any]) -> bool:
    dimension = _safe_text(item.get("dimension"))
    bucket = _safe_lower(_item_bucket(item))
    if _is_same_opponent_bucket(item) or _is_identity_conflict_bucket(item) or _is_unsupported_market_bucket(item):
        return True
    if dimension == "selection" and bucket == "over":
        return True
    if dimension == "context_caution_level" and "high" in bucket:
        return True
    if dimension == "context_pick_alignment" and "conflict" in bucket:
        return True
    if dimension == "research_lane" and "incubator" in bucket:
        return True
    if dimension == "market_type" and _is_combo_market_type(bucket):
        return True
    if dimension == "quality_bucket" and bucket in {"<60", "0-60", "low"}:
        return True
    if dimension == "confidence_bucket" and bucket in {"<0.65", "low"}:
        return True
    return False


def _rule_type_for_watchlist(item: dict[str, Any]) -> str:
    if _is_high_caution_combo_over_item(item):
        return RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST
    if _is_combo_item(item):
        return RULE_COMBO_SHADOW_WATCHLIST
    if _is_near_elite_item(item):
        return RULE_NEAR_ELITE_WATCHLIST
    if _is_under_item(item):
        return RULE_UNDER_VISIBILITY_WATCHLIST
    return RULE_MANUAL_REVIEW


def _risk_level(rule_type: str, item: dict[str, Any], min_sample: int) -> str:
    if rule_type == RULE_DO_NOT_PROMOTE_BLOCK or _has_dirty_data(item) or _has_negative_roi(item):
        return "HIGH"
    if _is_low_sample(item, min_sample) or rule_type in {RULE_MANUAL_REVIEW, RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST}:
        return "MEDIUM"
    return "LOW"


def _reason(rule_type: str, item: dict[str, Any], min_sample: int) -> str:
    source_bucket = _source_bucket(item)
    recommendation = _normalize_output_recommendation(item.get("recommendation"))
    if rule_type == RULE_DO_NOT_PROMOTE_BLOCK:
        if _has_negative_roi(item):
            return f"{source_bucket} remains blocked because graded ROI is negative."
        if _is_same_opponent_bucket(item):
            return f"{source_bucket} remains blocked because same-opponent warning rows cannot be promoted."
        if _is_identity_conflict_bucket(item):
            return f"{source_bucket} remains blocked because identity-conflict rows cannot be promoted."
        if _is_unsupported_market_bucket(item):
            return f"{source_bucket} remains blocked because unsupported markets cannot be promoted."
        if _is_low_sample(item, min_sample):
            return f"{source_bucket} remains blocked because evidence is below the minimum sample requirement."
        return f"{source_bucket} remains blocked by the Learning Brain recommendation."
    if rule_type == RULE_DATA_COLLECTION:
        return f"{source_bucket} is interesting but needs more shadow samples before any reviewed rule discussion."
    if recommendation == RECOMMEND_PROPOSED_REQUIRES_APPROVAL:
        return f"{source_bucket} is translated from a Learning Brain promotion candidate into a disabled proposal only."
    protective = _safe_text(item.get("protective_or_overblocking"))
    if protective:
        return protective
    return f"{source_bucket} qualifies only for disabled shadow tracking."


def _proposal(
    *,
    rule_type: str,
    item: dict[str, Any],
    source_report: Path,
    prediction_date: str,
    min_sample: int,
    confidence_z: float,
    source_recommendation: str | None = None,
) -> dict[str, Any]:
    source_bucket = _source_bucket(item)
    output_recommendation = source_recommendation or _normalize_output_recommendation(item.get("recommendation"))
    if output_recommendation not in ALLOWED_OUTPUT_RECOMMENDATIONS:
        output_recommendation = RECOMMEND_KEEP_SHADOW
    rule_id = f"{_slug(rule_type)}__{_slug(source_bucket)}__{_slug(prediction_date)}"
    proposal = {
        "rule_id": rule_id,
        "rule_name": source_bucket,
        "rule_type": rule_type,
        "source_report": str(source_report),
        "source_bucket": source_bucket,
        "source_recommendation": output_recommendation,
        **SAFE_PROPOSAL_VALUES,
        "market_type": _market_type(item),
        "selection": _selection(item),
        "context_caution_level": _context_caution_level(item),
        "context_pick_alignment": _context_pick_alignment(item),
        "research_lane": _research_lane(item),
        "min_sample_required": int(min_sample),
        "observed_sample_size": _safe_int(item.get("total_rows")),
        "graded_sample_size": _safe_int(item.get("graded_rows")),
        "hit_rate": _safe_float(item.get("hit_rate")),
        "roi": _safe_float(item.get("roi")),
        "average_odds": _safe_float(item.get("average_odds")),
        "break_even_hit_rate": _safe_float(item.get("break_even_hit_rate")),
        "wilson_lower_bound": _safe_float(item.get("wilson_lower_bound")),
        "confidence_z": float(confidence_z),
        "risk_level": _risk_level(rule_type, item, min_sample),
        "reason": _reason(rule_type, item, min_sample),
        "safety_notes": [
            "Disabled shadow proposal only.",
            "No live betting, Kelly, Elite, final_decision, bankroll, staking, board, or history effect.",
            "Human approval and a separate reviewed PR/commit are required before any activation.",
        ],
        "created_for_prediction_date": prediction_date,
    }
    return proposal


def _dedupe_append(proposals: list[dict[str, Any]], seen: set[str], proposal: dict[str, Any]) -> None:
    key = proposal["rule_id"]
    if key in seen:
        return
    seen.add(key)
    proposals.append(proposal)


def _shadow_candidate_proposals(
    item: dict[str, Any],
    *,
    source_report: Path,
    prediction_date: str,
    min_sample: int,
    confidence_z: float,
) -> list[dict[str, Any]]:
    text = _item_text(item)
    if "incubator" in text:
        return []
    recommendation = _normalize_output_recommendation(item.get("recommendation"))
    if _is_high_caution_combo_over_item(item) and recommendation not in {RECOMMEND_WATCHLIST, RECOMMEND_MANUAL_REVIEW}:
        return []
    if _is_interesting_data_collection_lane(item) and _is_low_sample(item, min_sample):
        return [
            _proposal(
                rule_type=RULE_DATA_COLLECTION,
                item=item,
                source_report=source_report,
                prediction_date=prediction_date,
                min_sample=min_sample,
                confidence_z=confidence_z,
                source_recommendation=RECOMMEND_WATCHLIST,
            )
        ]
    if not (_is_under_item(item) or _is_near_elite_item(item) or _is_combo_item(item)):
        return []
    return [
        _proposal(
            rule_type=_rule_type_for_watchlist(item),
            item=item,
            source_report=source_report,
            prediction_date=prediction_date,
            min_sample=min_sample,
            confidence_z=confidence_z,
            source_recommendation=recommendation,
        )
    ]


def _promotion_candidate_proposal(
    item: dict[str, Any],
    *,
    source_report: Path,
    prediction_date: str,
    min_sample: int,
    confidence_z: float,
) -> dict[str, Any]:
    if _must_not_promote(item, min_sample):
        rule_type = RULE_DATA_COLLECTION if _is_interesting_data_collection_lane(item) and _is_low_sample(item, min_sample) else RULE_DO_NOT_PROMOTE_BLOCK
        recommendation = RECOMMEND_WATCHLIST if rule_type == RULE_DATA_COLLECTION else RECOMMEND_DO_NOT_PROMOTE
    else:
        rule_type = _rule_type_for_watchlist(item)
        if rule_type in {RULE_UNDER_VISIBILITY_WATCHLIST, RULE_NEAR_ELITE_WATCHLIST, RULE_COMBO_SHADOW_WATCHLIST}:
            rule_type = RULE_MANUAL_REVIEW
        recommendation = RECOMMEND_PROPOSED_REQUIRES_APPROVAL
    return _proposal(
        rule_type=rule_type,
        item=item,
        source_report=source_report,
        prediction_date=prediction_date,
        min_sample=min_sample,
        confidence_z=confidence_z,
        source_recommendation=recommendation,
    )


def _block_proposal(
    item: dict[str, Any],
    *,
    source_report: Path,
    prediction_date: str,
    min_sample: int,
    confidence_z: float,
) -> dict[str, Any]:
    recommendation = _normalize_output_recommendation(item.get("recommendation"))
    if recommendation not in {RECOMMEND_KEEP_BLOCKED, RECOMMEND_KEEP_SHADOW}:
        recommendation = RECOMMEND_DO_NOT_PROMOTE
    return _proposal(
        rule_type=RULE_DO_NOT_PROMOTE_BLOCK,
        item=item,
        source_report=source_report,
        prediction_date=prediction_date,
        min_sample=min_sample,
        confidence_z=confidence_z,
        source_recommendation=recommendation,
    )


def generate_proposals_from_learning_report(
    learning_payload: dict[str, Any],
    *,
    source_report: Path,
    prediction_date: str,
    min_sample: int = 20,
    confidence_z: float = 1.96,
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in learning_payload.get("shadow_tracking_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        for proposal in _shadow_candidate_proposals(
            item,
            source_report=source_report,
            prediction_date=prediction_date,
            min_sample=min_sample,
            confidence_z=confidence_z,
        ):
            _dedupe_append(proposals, seen, proposal)

    for item in learning_payload.get("what_the_system_has_learned", []) or []:
        if not isinstance(item, dict) or not _is_interesting_data_collection_lane(item) or not _is_low_sample(item, min_sample):
            continue
        _dedupe_append(
            proposals,
            seen,
            _proposal(
                rule_type=RULE_DATA_COLLECTION,
                item=item,
                source_report=source_report,
                prediction_date=prediction_date,
                min_sample=min_sample,
                confidence_z=confidence_z,
                source_recommendation=RECOMMEND_WATCHLIST,
            ),
        )

    for item in learning_payload.get("promotion_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        _dedupe_append(
            proposals,
            seen,
            _promotion_candidate_proposal(
                item,
                source_report=source_report,
                prediction_date=prediction_date,
                min_sample=min_sample,
                confidence_z=confidence_z,
            ),
        )

    for item in learning_payload.get("keep_blocked_buckets", []) or []:
        if not isinstance(item, dict):
            continue
        _dedupe_append(
            proposals,
            seen,
            _block_proposal(
                item,
                source_report=source_report,
                prediction_date=prediction_date,
                min_sample=min_sample,
                confidence_z=confidence_z,
            ),
        )

    for item in learning_payload.get("bucket_performance_matrix", []) or []:
        if not isinstance(item, dict):
            continue
        recommendation = _normalize_output_recommendation(item.get("recommendation"))
        if _is_explicit_block_matrix_item(item) and (
            recommendation in {RECOMMEND_KEEP_BLOCKED, RECOMMEND_DO_NOT_PROMOTE} or _has_negative_roi(item)
        ):
            _dedupe_append(
                proposals,
                seen,
                _block_proposal(
                    item,
                    source_report=source_report,
                    prediction_date=prediction_date,
                    min_sample=min_sample,
                    confidence_z=confidence_z,
                ),
            )
        elif recommendation == RECOMMEND_PROPOSED_REQUIRES_APPROVAL:
            _dedupe_append(
                proposals,
                seen,
                _promotion_candidate_proposal(
                    item,
                    source_report=source_report,
                    prediction_date=prediction_date,
                    min_sample=min_sample,
                    confidence_z=confidence_z,
                ),
            )
        elif recommendation in {RECOMMEND_WATCHLIST, RECOMMEND_MANUAL_REVIEW} and _is_interesting_data_collection_lane(item):
            source_recommendation = RECOMMEND_MANUAL_REVIEW if recommendation == RECOMMEND_MANUAL_REVIEW else RECOMMEND_WATCHLIST
            rule_type = RULE_DATA_COLLECTION if _is_low_sample(item, min_sample) else _rule_type_for_watchlist(item)
            _dedupe_append(
                proposals,
                seen,
                _proposal(
                    rule_type=rule_type,
                    item=item,
                    source_report=source_report,
                    prediction_date=prediction_date,
                    min_sample=min_sample,
                    confidence_z=confidence_z,
                    source_recommendation=source_recommendation,
                ),
            )

    proposals.sort(key=lambda proposal: (proposal["rule_type"], proposal["source_bucket"]))
    return proposals


def validate_proposal_safety(proposals: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for proposal in proposals:
        rule_id = _safe_text(proposal.get("rule_id"), default="<missing-rule-id>")
        for key, expected in SAFE_PROPOSAL_VALUES.items():
            if proposal.get(key) != expected:
                errors.append(f"{rule_id}: {key} must be {expected!r}")
        if proposal.get("source_recommendation") not in ALLOWED_OUTPUT_RECOMMENDATIONS:
            errors.append(f"{rule_id}: source_recommendation is not allowed")
    return errors


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    runtime_root_path = Path(runtime_root)
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return (
        runtime_root_path / "operator" / f"{stem}.txt",
        runtime_root_path / "diagnostics" / f"{stem}.json",
    )


def learning_report_path_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"{LEARNING_REPORT_FILE_PREFIX}_{prediction_date}.json"


def _optional_artifact_warnings(runtime_root: Path, prediction_date: str) -> list[str]:
    warnings: list[str] = []
    for name, (directory, template) in OPTIONAL_RUNTIME_ARTIFACTS:
        path = runtime_root / directory / template.format(date=prediction_date)
        if not path.exists():
            warnings.append(f"missing optional artifact: {name}")
    return warnings


def _read_learning_payload(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing Learning Brain JSON: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid Learning Brain JSON: {path}: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"invalid Learning Brain JSON root: {path}"]
    return payload, []


def _proposal_counts_by_type(proposals: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(_safe_text(proposal.get("rule_type")) for proposal in proposals)
    return {rule_type: int(counts.get(rule_type, 0)) for rule_type in RULE_TYPES}


def _has_low_sample_proposal(proposals: list[dict[str, Any]], min_sample: int) -> bool:
    return any(_safe_int(proposal.get("graded_sample_size")) < int(min_sample) for proposal in proposals)


def _has_dirty_data_warning(warnings: list[str], proposals: list[dict[str, Any]]) -> bool:
    warning_text = "\n".join(warnings).lower()
    return "dirty data" in warning_text or "dirty_data" in warning_text or any(
        _safe_text(proposal.get("risk_level")) == "DIRTY_DATA" for proposal in proposals
    )


def build_shadow_rule_proposals_report(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    min_sample: int = 20,
    confidence_z: float = 1.96,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(runtime_root)
    source_report = learning_report_path_for_date(prediction_date=prediction_date, runtime_root=root)
    learning_payload, read_warnings = _read_learning_payload(source_report)
    optional_warnings = _optional_artifact_warnings(root, prediction_date)
    warnings = [*read_warnings, *optional_warnings]

    if learning_payload is None:
        status = STATUS_BLOCKED_MISSING_LEARNING_REPORT
        proposals: list[dict[str, Any]] = []
    else:
        proposals = generate_proposals_from_learning_report(
            learning_payload,
            source_report=source_report,
            prediction_date=prediction_date,
            min_sample=min_sample,
            confidence_z=confidence_z,
        )
        warnings.extend(_safe_text(warning) for warning in learning_payload.get("data_quality_warnings", []) if _safe_text(warning))
        safety_errors = validate_proposal_safety(proposals)
        if safety_errors:
            warnings.extend(f"safety validation error: {error}" for error in safety_errors)
            status = STATUS_BLOCKED_DIRTY_DATA
        elif _has_dirty_data_warning(warnings, proposals):
            status = STATUS_BLOCKED_DIRTY_DATA
        elif _has_low_sample_proposal(proposals, min_sample):
            status = STATUS_READY_LOW_SAMPLE
        else:
            status = STATUS_READY

    active_count = sum(1 for proposal in proposals if proposal.get("activation") != "DISABLED")
    production_effect_count = sum(1 for proposal in proposals if proposal.get("production_effect") is True)
    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "status": status,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "source_learning_brain_report": str(source_report),
        "min_sample": int(min_sample),
        "confidence_z": float(confidence_z),
        "proposals": proposals,
        "proposal_counts_by_type": _proposal_counts_by_type(proposals),
        "disabled_proposal_count": sum(1 for proposal in proposals if proposal.get("activation") == "DISABLED"),
        "active_proposal_count": active_count,
        "production_effect_count": production_effect_count,
        "human_approval_required_count": sum(
            1 for proposal in proposals if proposal.get("requires_human_approval") is True
        ),
        "data_quality_warnings": sorted(set(warnings)),
        "safety_declarations": list(SAFETY_DECLARATIONS),
        "applied_changes": False,
        "live_rules_modified": False,
        "elite_logic_modified": False,
        "kelly_logic_modified": False,
        "final_decision_modified": False,
        "pick_history_modified": False,
        "history_files_modified": False,
        "generated_real_money_recommendations": False,
    }
    return payload


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def _format_num(value: Any) -> str:
    number = _safe_float(value)
    if number is None:
        return "n/a"
    return f"{number:.3f}"


def _proposal_line(proposal: dict[str, Any]) -> str:
    return (
        f"- {proposal['rule_name']} [{proposal['rule_type']}]: rec={proposal['source_recommendation']}, "
        f"activation={proposal['activation']}, shadow_only={str(proposal['shadow_only']).lower()}, "
        f"graded={proposal['graded_sample_size']}, hit={_format_pct(proposal.get('hit_rate'))}, "
        f"ROI={_format_pct(proposal.get('roi'))}, BE={_format_pct(proposal.get('break_even_hit_rate'))}, "
        f"WL={_format_pct(proposal.get('wilson_lower_bound'))}, risk={proposal['risk_level']}"
    )


def render_shadow_rule_proposals_text(payload: dict[str, Any]) -> str:
    proposals = payload.get("proposals", [])
    watchlist_types = {
        RULE_UNDER_VISIBILITY_WATCHLIST,
        RULE_NEAR_ELITE_WATCHLIST,
        RULE_COMBO_SHADOW_WATCHLIST,
        RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST,
        RULE_DATA_COLLECTION,
    }
    watchlists = [proposal for proposal in proposals if proposal.get("rule_type") in watchlist_types]
    blocks = [proposal for proposal in proposals if proposal.get("rule_type") == RULE_DO_NOT_PROMOTE_BLOCK]
    manual_reviews = [
        proposal
        for proposal in proposals
        if proposal.get("rule_type") == RULE_MANUAL_REVIEW
        or proposal.get("source_recommendation") in {RECOMMEND_MANUAL_REVIEW, RECOMMEND_PROPOSED_REQUIRES_APPROVAL}
    ]

    lines: list[str] = [
        f"CourtVision Shadow Adaptive Rule Proposal Report - {payload['prediction_date']}",
        "=" * 76,
        "Reporting-only diagnostic. No Elite, Kelly, final_decision, bankroll, staking, thresholds, histories, or boards changed.",
        "",
        "1. Executive Verdict",
        "-" * 76,
        f"Status: {payload['status']}",
        f"Source Learning Brain JSON: {payload['source_learning_brain_report']}",
        f"Proposals generated: {len(proposals)}",
        "These are proposed shadow rules only. None are active.",
        "A human must approve any future rule before activation.",
        "Approval and production activation are not implemented in this phase.",
        "Future activation would require a separate reviewed PR/commit.",
        "",
        "2. Safety Declaration",
        "-" * 76,
    ]
    for declaration in payload["safety_declarations"]:
        lines.append(f"- {declaration}")

    lines.extend(["", "3. Proposed Shadow Watchlist Rules", "-" * 76])
    if watchlists:
        for proposal in watchlists:
            lines.append(_proposal_line(proposal))
            lines.append(f"  reason: {proposal['reason']}")
    else:
        lines.append("- none")

    lines.extend(["", "4. Do-Not-Promote Blocks", "-" * 76])
    if blocks:
        for proposal in blocks:
            lines.append(_proposal_line(proposal))
            lines.append(f"  reason: {proposal['reason']}")
    else:
        lines.append("- none")

    lines.extend(["", "5. Manual Review Candidates", "-" * 76])
    if manual_reviews:
        for proposal in manual_reviews:
            lines.append(_proposal_line(proposal))
            lines.append("  note: disabled and human-review-only; not eligible for live betting, Kelly, or Elite.")
    else:
        lines.append("- none")

    lines.extend(["", "6. Data Quality Warnings", "-" * 76])
    if payload["data_quality_warnings"]:
        for warning in payload["data_quality_warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    lines.extend(["", "7. Next Recommended Action", "-" * 76])
    lines.extend(
        [
            "- Continue collecting shadow samples.",
            "- Do not change core gates.",
            "- Do not activate rules.",
            "- Use these proposals only for future review.",
        ]
    )
    return "\n".join(lines) + "\n"


def _assert_output_paths_under_runtime_root(paths: tuple[Path, Path], runtime_root: Path) -> None:
    root = runtime_root.resolve()
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"refusing to write outside runtime-root: {path}")


def write_shadow_rule_proposals_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    min_sample: int = 20,
    confidence_z: float = 1.96,
) -> tuple[Path, Path, dict[str, Any]]:
    text_path, json_path = report_paths_for_date(prediction_date=prediction_date, runtime_root=runtime_root)
    _assert_output_paths_under_runtime_root((text_path, json_path), Path(runtime_root))
    payload = build_shadow_rule_proposals_report(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        min_sample=min_sample,
        confidence_z=confidence_z,
    )
    text = render_shadow_rule_proposals_text(payload)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return text_path, json_path, payload


__all__ = [
    "REPORT_FILE_PREFIX",
    "STATUS_BLOCKED_DIRTY_DATA",
    "STATUS_BLOCKED_MISSING_LEARNING_REPORT",
    "STATUS_READY",
    "STATUS_READY_LOW_SAMPLE",
    "RULE_COMBO_SHADOW_WATCHLIST",
    "RULE_DATA_COLLECTION",
    "RULE_DO_NOT_PROMOTE_BLOCK",
    "RULE_HIGH_CAUTION_COMBO_SHADOW_WATCHLIST",
    "RULE_MANUAL_REVIEW",
    "RULE_NEAR_ELITE_WATCHLIST",
    "RULE_UNDER_VISIBILITY_WATCHLIST",
    "build_shadow_rule_proposals_report",
    "generate_proposals_from_learning_report",
    "learning_report_path_for_date",
    "render_shadow_rule_proposals_text",
    "report_paths_for_date",
    "validate_proposal_safety",
    "write_shadow_rule_proposals_outputs",
]
