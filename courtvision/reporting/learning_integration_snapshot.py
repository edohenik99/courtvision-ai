from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_VERSION = "1.0"
REPORT_FILE_PREFIX = "learning_integration_snapshot"
LEARNING_REPORT_FILE_PREFIX = "learning_brain_report"
SHADOW_RULE_PROPOSALS_FILE_PREFIX = "shadow_rule_proposals"

STATUS_READY = "LEARNING_INTEGRATION_READY"
STATUS_READY_WITH_WARNINGS = "LEARNING_INTEGRATION_READY_WITH_WARNINGS"
STATUS_MISSING_LEARNING_REPORT = "LEARNING_INTEGRATION_MISSING_LEARNING_REPORT"
STATUS_MISSING_RULE_PROPOSALS = "LEARNING_INTEGRATION_MISSING_RULE_PROPOSALS"
STATUS_BLOCKED_BY_UNSAFE_PROPOSALS = "LEARNING_INTEGRATION_BLOCKED_BY_UNSAFE_PROPOSALS"

MISSING_STATUS = "MISSING"

WATCHLIST_RULE_TYPES = {
    "UNDER_VISIBILITY_WATCHLIST",
    "NEAR_ELITE_WATCHLIST",
    "COMBO_SHADOW_WATCHLIST",
    "HIGH_CAUTION_COMBO_SHADOW_WATCHLIST",
    "DATA_COLLECTION_RULE",
}
DO_NOT_PROMOTE_RULE_TYPE = "DO_NOT_PROMOTE_BLOCK"
MANUAL_REVIEW_RULE_TYPE = "MANUAL_REVIEW_CANDIDATE_RULE"

SAFETY_DECLARATIONS: tuple[str, ...] = (
    "No rules activated.",
    "No production effect.",
    "No live betting effect.",
    "No Kelly effect.",
    "No Elite effect.",
    "No final_decision effect.",
    "No pick_history mutation.",
    "No history mutation.",
    "Human approval is required for future changes.",
)

RECOMMENDED_NEXT_ACTION: tuple[str, ...] = (
    "Continue collecting shadow samples.",
    "Do not change core gates.",
    "Do not activate rules.",
    "Review UNDER/near-elite/combo watchlists only as research.",
)

OPTIONAL_TEXT_REPORTS: tuple[tuple[str, str, str], ...] = (
    ("learning_brain_operator_text", "operator", "learning_brain_report_{date}.txt"),
    ("shadow_rule_proposals_operator_text", "operator", "shadow_rule_proposals_{date}.txt"),
    ("operator_card_text", "operator", "operator_card_{date}.txt"),
    ("daily_summary_text", "operator", "daily_summary_{date}.txt"),
)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _safe_text(value).lower() in {"true", "t", "1", "1.0", "yes", "y"}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if _safe_text(value)))


def _read_json(path: Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"missing {label}: {path}"]
    if path.stat().st_size == 0:
        return None, [f"empty {label}: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"invalid {label}: {path}: {exc}"]
    except OSError as exc:
        return None, [f"unreadable {label}: {path}: {type(exc).__name__}"]
    if not isinstance(payload, dict):
        return None, [f"invalid {label} root: {path}"]
    return payload, []


def _read_optional_text(path: Path) -> tuple[bool, int, str | None]:
    if not path.exists():
        return False, 0, "missing"
    if path.stat().st_size == 0:
        return True, 0, "empty"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
        return True, len(text), "decode_replaced"
    except OSError as exc:
        return True, 0, f"read_error:{type(exc).__name__}"
    return True, len(text), None


def _optional_text_report_summary(runtime_root: Path, prediction_date: str) -> tuple[dict[str, Any], list[str]]:
    reports: dict[str, Any] = {}
    warnings: list[str] = []
    for name, directory, template in OPTIONAL_TEXT_REPORTS:
        path = runtime_root / directory / template.format(date=prediction_date)
        found, char_count, issue = _read_optional_text(path)
        reports[name] = {
            "path": str(path),
            "found": found,
            "char_count": int(char_count),
            "read_issue": issue,
        }
        if not found:
            warnings.append(f"missing optional text report: {name} ({path})")
        elif issue:
            warnings.append(f"optional text report issue: {name} ({path}): {issue}")
    return reports, warnings


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _count_from_payload_or_proposals(
    payload: dict[str, Any] | None,
    key: str,
    proposals: list[dict[str, Any]],
    fallback_key: str,
    fallback_value: Any,
) -> int:
    if isinstance(payload, dict) and key in payload:
        return _safe_int(payload.get(key))
    return sum(1 for proposal in proposals if proposal.get(fallback_key) == fallback_value)


def _proposal_counts_by_type(payload: dict[str, Any] | None, proposals: list[dict[str, Any]]) -> dict[str, int]:
    raw = payload.get("proposal_counts_by_type") if isinstance(payload, dict) else None
    if isinstance(raw, dict):
        return {str(rule_type): _safe_int(count) for rule_type, count in sorted(raw.items())}
    counts = Counter(_safe_text(proposal.get("rule_type"), default="UNKNOWN") for proposal in proposals)
    return {rule_type: int(count) for rule_type, count in sorted(counts.items())}


def _proposal_digest(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "rule_id": _safe_text(proposal.get("rule_id")),
        "rule_type": _safe_text(proposal.get("rule_type"), default="UNKNOWN"),
        "rule_name": _safe_text(proposal.get("rule_name") or proposal.get("source_bucket"), default="unknown"),
        "source_bucket": _safe_text(proposal.get("source_bucket") or proposal.get("rule_name"), default="unknown"),
        "source_recommendation": _safe_text(proposal.get("source_recommendation")),
        "activation": _safe_text(proposal.get("activation"), default="UNKNOWN"),
        "production_effect": _truthy(proposal.get("production_effect")),
        "requires_human_approval": _truthy(proposal.get("requires_human_approval")),
        "reason": _safe_text(proposal.get("reason")),
    }


def _proposal_summary(proposals: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    if kind == "watchlist":
        selected = [
            proposal
            for proposal in proposals
            if _safe_text(proposal.get("rule_type")) in WATCHLIST_RULE_TYPES
            or "WATCHLIST" in _safe_text(proposal.get("rule_type"))
        ]
    elif kind == "do_not_promote":
        selected = [
            proposal
            for proposal in proposals
            if _safe_text(proposal.get("rule_type")) == DO_NOT_PROMOTE_RULE_TYPE
            or _safe_text(proposal.get("source_recommendation")) == "DO_NOT_PROMOTE"
        ]
    elif kind == "manual_review":
        selected = [
            proposal
            for proposal in proposals
            if _safe_text(proposal.get("rule_type")) == MANUAL_REVIEW_RULE_TYPE
            or _safe_text(proposal.get("source_recommendation"))
            in {"MANUAL_REVIEW_CANDIDATE", "PROPOSED_ONLY_REQUIRES_APPROVAL"}
        ]
    else:
        selected = []
    return {
        "count": int(len(selected)),
        "items": [_proposal_digest(proposal) for proposal in selected[:12]],
    }


def _bucket_digest(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "dimension": _safe_text(item.get("dimension")),
        "bucket": _safe_text(item.get("bucket") or item.get("label") or item.get("source_bucket"), default="unknown"),
        "recommendation": _safe_text(item.get("recommendation")),
        "total_rows": _safe_int(item.get("total_rows")),
        "graded_rows": _safe_int(item.get("graded_rows")),
        "hit_rate": item.get("hit_rate"),
        "roi": item.get("roi"),
        "sample_quality_flag": _safe_text(item.get("sample_quality_flag")),
    }


def _learning_summary(learning_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(learning_payload, dict):
        return {
            "learning_status": MISSING_STATUS,
            "no_bet_blockers": [],
            "top_losing_buckets": [],
            "top_profitable_buckets": [],
            "watchlist_buckets": [],
            "promotion_candidates": [],
            "keep_blocked_buckets": [],
            "recommended_core_changes": [],
            "data_quality_warnings": [],
        }

    return {
        "learning_status": _safe_text(learning_payload.get("status"), default=MISSING_STATUS),
        "no_bet_blockers": [
            _safe_text(item)
            for item in learning_payload.get("no_bet_blockers", [])
            if _safe_text(item)
        ],
        "top_losing_buckets": [_bucket_digest(item) for item in _list_payload(learning_payload.get("top_losing_buckets"))[:12]],
        "top_profitable_buckets": [
            _bucket_digest(item) for item in _list_payload(learning_payload.get("top_profitable_buckets"))[:12]
        ],
        "watchlist_buckets": [
            _bucket_digest(item) for item in _list_payload(learning_payload.get("shadow_tracking_candidates"))[:12]
        ],
        "promotion_candidates": [
            _bucket_digest(item) for item in _list_payload(learning_payload.get("promotion_candidates"))[:12]
        ],
        "keep_blocked_buckets": [
            _bucket_digest(item) for item in _list_payload(learning_payload.get("keep_blocked_buckets"))[:12]
        ],
        "recommended_core_changes": [
            _safe_text(item)
            for item in learning_payload.get("recommended_core_changes", [])
            if _safe_text(item)
        ],
        "data_quality_warnings": [
            _safe_text(item)
            for item in learning_payload.get("data_quality_warnings", [])
            if _safe_text(item)
        ],
    }


def _recommended_watch_text(watchlist_summary: dict[str, Any]) -> str:
    items = watchlist_summary.get("items") if isinstance(watchlist_summary, dict) else []
    if isinstance(items, list) and items:
        buckets = [
            _safe_text(item.get("source_bucket") if isinstance(item, dict) else item)
            for item in items[:5]
            if _safe_text(item.get("source_bucket") if isinstance(item, dict) else item)
        ]
        if buckets:
            return "; ".join(buckets)
    return "UNDER/near-elite/combo watchlists as research only."


def _stay_blocked_text(do_not_promote_summary: dict[str, Any], learning_summary: dict[str, Any]) -> str:
    items = do_not_promote_summary.get("items") if isinstance(do_not_promote_summary, dict) else []
    buckets: list[str] = []
    if isinstance(items, list):
        buckets.extend(
            _safe_text(item.get("source_bucket") if isinstance(item, dict) else item)
            for item in items[:5]
            if _safe_text(item.get("source_bucket") if isinstance(item, dict) else item)
        )
    keep_blocked = learning_summary.get("keep_blocked_buckets") if isinstance(learning_summary, dict) else []
    if isinstance(keep_blocked, list):
        buckets.extend(
            _safe_text(item.get("bucket") if isinstance(item, dict) else item)
            for item in keep_blocked[:5]
            if _safe_text(item.get("bucket") if isinstance(item, dict) else item)
        )
    if buckets:
        return "; ".join(_dedupe(buckets)[:8])
    return "Core gates, do-not-promote buckets, and unsafe/low-sample lanes."


def _operator_summary(
    *,
    learning_payload: dict[str, Any] | None,
    learning_summary: dict[str, Any],
    watchlist_summary: dict[str, Any],
    do_not_promote_summary: dict[str, Any],
) -> dict[str, Any]:
    learning_status = _safe_text(learning_summary.get("learning_status"), default=MISSING_STATUS)
    is_learning = bool(learning_payload) and learning_status != MISSING_STATUS
    return {
        "is_system_learning": "yes" if is_learning else "no",
        "anything_bettable": "no",
        "anything_production_approved": "no",
        "production_approval": "no",
        "what_to_watch": _recommended_watch_text(watchlist_summary),
        "what_should_stay_blocked": _stay_blocked_text(do_not_promote_summary, learning_summary),
        "recommended_next_action": list(RECOMMENDED_NEXT_ACTION),
    }


def _status_for(
    *,
    learning_payload: dict[str, Any] | None,
    proposals_payload: dict[str, Any] | None,
    active_proposal_count: int,
    production_effect_count: int,
    status_warnings: list[str],
) -> str:
    if proposals_payload is not None and (active_proposal_count > 0 or production_effect_count > 0):
        return STATUS_BLOCKED_BY_UNSAFE_PROPOSALS
    if learning_payload is None:
        return STATUS_MISSING_LEARNING_REPORT
    if proposals_payload is None:
        return STATUS_MISSING_RULE_PROPOSALS
    return STATUS_READY_WITH_WARNINGS if status_warnings else STATUS_READY


def _status_warning_candidates(
    *,
    learning_payload: dict[str, Any] | None,
    proposals_payload: dict[str, Any] | None,
    learning_status: str,
    proposal_status: str,
    data_quality_warnings: list[str],
) -> list[str]:
    candidates: list[str] = []
    if learning_payload is not None and learning_status in {
        "LEARNING_BLOCKED_BY_MISSING_HISTORY",
        "LEARNING_NEEDS_MORE_DATA",
    }:
        candidates.append(f"learning report status requires attention: {learning_status}")
    if proposals_payload is not None and proposal_status not in {
        "SHADOW_RULE_PROPOSALS_READY",
        "SHADOW_RULE_PROPOSALS_READY_LOW_SAMPLE",
    }:
        candidates.append(f"shadow rule proposal status requires attention: {proposal_status}")
    for warning in data_quality_warnings:
        if not warning.startswith("missing optional text report:"):
            candidates.append(warning)
    return _dedupe(candidates)


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def artifact_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> dict[str, Path]:
    root = Path(runtime_root)
    return {
        "learning_brain_json": root / "diagnostics" / f"{LEARNING_REPORT_FILE_PREFIX}_{prediction_date}.json",
        "shadow_rule_proposals_json": root / "diagnostics" / f"{SHADOW_RULE_PROPOSALS_FILE_PREFIX}_{prediction_date}.json",
    }


def report_paths_for_date(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path]:
    root = Path(runtime_root)
    stem = f"{REPORT_FILE_PREFIX}_{prediction_date}"
    return (
        root / "operator" / f"{stem}.txt",
        root / "diagnostics" / f"{stem}.json",
    )


def build_learning_integration_snapshot(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    root = Path(runtime_root)
    paths = artifact_paths_for_date(prediction_date=prediction_date, runtime_root=root)

    learning_payload, learning_read_warnings = _read_json(paths["learning_brain_json"], "Learning Brain JSON")
    proposals_payload, proposals_read_warnings = _read_json(
        paths["shadow_rule_proposals_json"],
        "Shadow Rule Proposal JSON",
    )
    optional_text_reports, optional_text_warnings = _optional_text_report_summary(root, prediction_date)

    proposals = _list_payload(proposals_payload.get("proposals") if isinstance(proposals_payload, dict) else [])
    proposal_counts_by_type = _proposal_counts_by_type(proposals_payload, proposals)
    if proposals:
        total_proposals = len(proposals)
    elif isinstance(proposals_payload, dict) and "total_proposals" in proposals_payload:
        total_proposals = _safe_int(proposals_payload.get("total_proposals"))
    else:
        total_proposals = sum(proposal_counts_by_type.values())
    disabled_proposal_count = _count_from_payload_or_proposals(
        proposals_payload,
        "disabled_proposal_count",
        proposals,
        "activation",
        "DISABLED",
    )
    active_proposal_count = (
        _safe_int(proposals_payload.get("active_proposal_count"))
        if isinstance(proposals_payload, dict) and "active_proposal_count" in proposals_payload
        else sum(1 for proposal in proposals if proposal.get("activation") != "DISABLED")
    )
    production_effect_count = (
        _safe_int(proposals_payload.get("production_effect_count"))
        if isinstance(proposals_payload, dict) and "production_effect_count" in proposals_payload
        else sum(1 for proposal in proposals if proposal.get("production_effect") is True)
    )
    human_approval_required_count = _count_from_payload_or_proposals(
        proposals_payload,
        "human_approval_required_count",
        proposals,
        "requires_human_approval",
        True,
    )

    learning = _learning_summary(learning_payload)
    learning_status = _safe_text(learning.get("learning_status"), default=MISSING_STATUS)
    shadow_rule_proposal_status = (
        _safe_text(proposals_payload.get("status"), default=MISSING_STATUS)
        if isinstance(proposals_payload, dict)
        else MISSING_STATUS
    )
    watchlist_summary = _proposal_summary(proposals, kind="watchlist")
    do_not_promote_summary = _proposal_summary(proposals, kind="do_not_promote")
    manual_review_summary = _proposal_summary(proposals, kind="manual_review")

    data_quality_warnings = []
    data_quality_warnings.extend(learning_read_warnings)
    data_quality_warnings.extend(proposals_read_warnings)
    data_quality_warnings.extend(optional_text_warnings)
    data_quality_warnings.extend(
        f"Learning Brain warning: {warning}" for warning in learning.get("data_quality_warnings", [])
    )
    if isinstance(proposals_payload, dict):
        data_quality_warnings.extend(
            f"Shadow Rule Proposal warning: {warning}"
            for warning in proposals_payload.get("data_quality_warnings", [])
            if _safe_text(warning)
        )
    if active_proposal_count > 0:
        data_quality_warnings.append(f"unsafe proposals: active_proposal_count={active_proposal_count}")
    if production_effect_count > 0:
        data_quality_warnings.append(f"unsafe proposals: production_effect_count={production_effect_count}")
    data_quality_warnings = _dedupe(data_quality_warnings)

    status_warnings = _status_warning_candidates(
        learning_payload=learning_payload,
        proposals_payload=proposals_payload,
        learning_status=learning_status,
        proposal_status=shadow_rule_proposal_status,
        data_quality_warnings=data_quality_warnings,
    )
    status = _status_for(
        learning_payload=learning_payload,
        proposals_payload=proposals_payload,
        active_proposal_count=active_proposal_count,
        production_effect_count=production_effect_count,
        status_warnings=status_warnings,
    )
    operator_summary = _operator_summary(
        learning_payload=learning_payload,
        learning_summary=learning,
        watchlist_summary=watchlist_summary,
        do_not_promote_summary=do_not_promote_summary,
    )

    payload = {
        "report_name": REPORT_FILE_PREFIX,
        "report_version": REPORT_VERSION,
        "status": status,
        "prediction_date": prediction_date,
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(),
        "runtime_root": str(root),
        "source_paths": {name: str(path) for name, path in paths.items()},
        "optional_text_reports": optional_text_reports,
        "learning_brain_status": learning_status,
        "shadow_rule_proposal_status": shadow_rule_proposal_status,
        "total_proposals": int(total_proposals),
        "proposal_counts_by_type": proposal_counts_by_type,
        "disabled_proposal_count": int(disabled_proposal_count),
        "active_proposal_count": int(active_proposal_count),
        "production_effect_count": int(production_effect_count),
        "human_approval_required_count": int(human_approval_required_count),
        "learning_brain_summary": learning,
        "watchlist_summary": watchlist_summary,
        "do_not_promote_summary": do_not_promote_summary,
        "manual_review_summary": manual_review_summary,
        "operator_summary": operator_summary,
        "recommended_next_action": list(RECOMMENDED_NEXT_ACTION),
        "data_quality_warnings": data_quality_warnings,
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
    return _json_ready(payload)


def _format_bucket_line(item: dict[str, Any]) -> str:
    label = _safe_text(item.get("bucket"), default="unknown")
    dimension = _safe_text(item.get("dimension"))
    prefix = f"{dimension}={label}" if dimension else label
    return (
        f"- {prefix}: rec={_safe_text(item.get('recommendation'), default='n/a')}, "
        f"total={_safe_int(item.get('total_rows'))}, graded={_safe_int(item.get('graded_rows'))}, "
        f"hit={item.get('hit_rate') if item.get('hit_rate') is not None else 'n/a'}, "
        f"roi={item.get('roi') if item.get('roi') is not None else 'n/a'}, "
        f"sample={_safe_text(item.get('sample_quality_flag'), default='n/a')}"
    )


def _format_proposal_line(item: dict[str, Any]) -> str:
    return (
        f"- {_safe_text(item.get('source_bucket'), default='unknown')} "
        f"[{_safe_text(item.get('rule_type'), default='UNKNOWN')}]: "
        f"activation={_safe_text(item.get('activation'), default='UNKNOWN')}, "
        f"production_effect={str(_truthy(item.get('production_effect'))).lower()}, "
        f"human_approval={str(_truthy(item.get('requires_human_approval'))).lower()}, "
        f"rec={_safe_text(item.get('source_recommendation'), default='n/a')}"
    )


def _append_bucket_section(lines: list[str], title: str, items: list[dict[str, Any]]) -> None:
    lines.append(title)
    if not items:
        lines.append("- none")
        return
    for item in items:
        lines.append(_format_bucket_line(item))


def _append_proposal_section(lines: list[str], title: str, summary: dict[str, Any]) -> None:
    lines.append(title)
    lines.append(f"- count: {_safe_int(summary.get('count'))}")
    items = summary.get("items") if isinstance(summary, dict) else []
    if not isinstance(items, list) or not items:
        lines.append("- none")
        return
    for item in items:
        if isinstance(item, dict):
            lines.append(_format_proposal_line(item))


def render_operator_card_learning_integration_block(payload: dict[str, Any]) -> str:
    """Return a short read-only block for possible future operator-card inclusion."""

    summary = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    lines = [
        "Learning Integration Snapshot - Research Only",
        "-" * 40,
        f"- status: {_safe_text(payload.get('status'), default=MISSING_STATUS)}",
        f"- learning status: {_safe_text(payload.get('learning_brain_status'), default=MISSING_STATUS)}",
        f"- proposal status: {_safe_text(payload.get('shadow_rule_proposal_status'), default=MISSING_STATUS)}",
        f"- total proposals: {_safe_int(payload.get('total_proposals'))}",
        f"- active proposals: {_safe_int(payload.get('active_proposal_count'))}",
        f"- production effect proposals: {_safe_int(payload.get('production_effect_count'))}",
        f"- system learning: {_safe_text(summary.get('is_system_learning'), default='no')}",
        "- production approval: no",
        "- betting effect: no",
        "- next action: continue collecting shadow samples; do not activate rules.",
    ]
    return "\n".join(lines)


def render_learning_integration_snapshot_text(payload: dict[str, Any]) -> str:
    learning = payload.get("learning_brain_summary") if isinstance(payload.get("learning_brain_summary"), dict) else {}
    operator_summary = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    lines: list[str] = [
        f"CourtVision Learning Integration Snapshot - {payload['prediction_date']}",
        "=" * 76,
        "Reporting-only diagnostic. No rules, Elite logic, Kelly logic, final_decision, bankroll, staking, histories, or boards changed.",
        "",
        "1. Executive Verdict",
        "-" * 76,
        f"Status: {payload['status']}",
        f"Learning Brain status: {payload['learning_brain_status']}",
        f"Shadow Rule Proposal status: {payload['shadow_rule_proposal_status']}",
        f"Total proposals: {payload['total_proposals']}",
        f"Active proposals: {payload['active_proposal_count']}",
        f"Production effect proposals: {payload['production_effect_count']}",
        "Learning visibility can be automated. Rule activation cannot be automated.",
        "",
        "2. Learning Brain Summary",
        "-" * 76,
        f"- learning status: {payload['learning_brain_status']}",
        "- no-bet blockers:",
    ]
    blockers = learning.get("no_bet_blockers") if isinstance(learning.get("no_bet_blockers"), list) else []
    lines.extend(f"  - {blocker}" for blocker in blockers) if blockers else lines.append("  - none")
    _append_bucket_section(lines, "- top losing buckets:", learning.get("top_losing_buckets", []))
    _append_bucket_section(lines, "- top profitable buckets:", learning.get("top_profitable_buckets", []))
    _append_bucket_section(lines, "- watchlist buckets:", learning.get("watchlist_buckets", []))
    _append_bucket_section(lines, "- promotion candidates:", learning.get("promotion_candidates", []))
    _append_bucket_section(lines, "- keep-blocked buckets:", learning.get("keep_blocked_buckets", []))
    lines.append("- recommended core changes:")
    core_changes = learning.get("recommended_core_changes") if isinstance(learning.get("recommended_core_changes"), list) else []
    lines.extend(f"  - {item}" for item in core_changes) if core_changes else lines.append("  - none")
    lines.append("- Learning Brain data quality warnings:")
    learning_warnings = learning.get("data_quality_warnings") if isinstance(learning.get("data_quality_warnings"), list) else []
    lines.extend(f"  - {item}" for item in learning_warnings) if learning_warnings else lines.append("  - none")

    lines.extend(
        [
            "",
            "3. Shadow Rule Proposal Summary",
            "-" * 76,
            f"- proposal status: {payload['shadow_rule_proposal_status']}",
            f"- total proposals: {payload['total_proposals']}",
            "- proposal counts by type:",
        ]
    )
    counts = payload.get("proposal_counts_by_type") if isinstance(payload.get("proposal_counts_by_type"), dict) else {}
    if counts:
        for rule_type, count in counts.items():
            lines.append(f"  - {rule_type}: {count}")
    else:
        lines.append("  - none")
    lines.extend(
        [
            f"- disabled proposal count: {payload['disabled_proposal_count']}",
            f"- active proposal count: {payload['active_proposal_count']}",
            f"- production effect count: {payload['production_effect_count']}",
            f"- human approval required count: {payload['human_approval_required_count']}",
        ]
    )
    _append_proposal_section(lines, "- watchlist proposals:", payload.get("watchlist_summary", {}))
    _append_proposal_section(lines, "- do-not-promote blocks:", payload.get("do_not_promote_summary", {}))
    _append_proposal_section(lines, "- manual-review candidates:", payload.get("manual_review_summary", {}))

    lines.extend(["", "4. Safety Declaration", "-" * 76])
    for declaration in payload["safety_declarations"]:
        lines.append(f"- {declaration}")

    lines.extend(
        [
            "",
            "5. Operator Summary",
            "-" * 76,
            f"- Is the system learning? {_safe_text(operator_summary.get('is_system_learning'), default='no')}",
            f"- Did anything become bettable? {_safe_text(operator_summary.get('anything_bettable'), default='no')}",
            "- Did anything receive production approval? no",
            f"- Did anything become production-approved? {_safe_text(operator_summary.get('anything_production_approved'), default='no')}",
            f"- What should the operator watch? {_safe_text(operator_summary.get('what_to_watch'), default='research watchlists only')}",
            f"- What should stay blocked? {_safe_text(operator_summary.get('what_should_stay_blocked'), default='core gates')}",
            "- Recommended next action:",
        ]
    )
    for item in payload["recommended_next_action"]:
        lines.append(f"  - {item}")

    lines.extend(["", "6. Data Quality Warnings", "-" * 76])
    if payload["data_quality_warnings"]:
        for warning in payload["data_quality_warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- none")

    lines.extend(["", "Operator-Card Helper Preview", "-" * 76])
    lines.append(render_operator_card_learning_integration_block(payload))
    return "\n".join(lines) + "\n"


def _assert_output_paths_under_runtime_root(paths: tuple[Path, Path], runtime_root: Path) -> None:
    root = runtime_root.resolve()
    for path in paths:
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"refusing to write outside runtime-root: {path}")


def write_learning_integration_snapshot_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, Path, dict[str, Any]]:
    text_path, json_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    _assert_output_paths_under_runtime_root((text_path, json_path), Path(runtime_root))
    payload = build_learning_integration_snapshot(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    text = render_learning_integration_snapshot_text(payload)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return text_path, json_path, payload


__all__ = [
    "REPORT_FILE_PREFIX",
    "STATUS_BLOCKED_BY_UNSAFE_PROPOSALS",
    "STATUS_MISSING_LEARNING_REPORT",
    "STATUS_MISSING_RULE_PROPOSALS",
    "STATUS_READY",
    "STATUS_READY_WITH_WARNINGS",
    "artifact_paths_for_date",
    "build_learning_integration_snapshot",
    "render_learning_integration_snapshot_text",
    "render_operator_card_learning_integration_block",
    "report_paths_for_date",
    "write_learning_integration_snapshot_outputs",
]
