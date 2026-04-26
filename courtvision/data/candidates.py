from __future__ import annotations

from collections import Counter, defaultdict
import logging
from typing import Any, Callable

import pandas as pd
from courtvision.market import normalize_market_alias

logger = logging.getLogger(__name__)

# Keep these local for now so the candidate module does not depend on
# unstable legacy imports from courtvision.market.value_engine.
PRIMARY_PLAYER_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
    "player_steals",
    "player_blocks",
}

CORE_PARTIAL_PLAYER_MARKETS = {
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
    "player_steals",
    "player_blocks",
}


def partial_fill_markets(
    player_market_rows: pd.DataFrame,
    available_markets: set[str] | None = None,
) -> list[str]:
    """
    Local fallback for partial-market filling.

    Returns the subset of core player markets that are missing for the
    current player but are eligible for partial-fill handling.

    This keeps the candidate-building module stable while the market
    layer is still being extracted/refactored.
    """
    if available_markets is None:
        available_markets = set()

    missing = [
        market
        for market in CORE_PARTIAL_PLAYER_MARKETS
        if market not in available_markets
    ]
    return missing


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_player_name(name: str | None) -> str:
    """Normalize player name for matching.

    Handles various formats:
        "LeBron James" -> "lebron james"
        "L. James" -> "l james"
        "James, LeBron" -> "lebron james"
        "  LeBron  James  " -> "lebron james"
    """
    if name is None:
        return ""

    # Convert to string and lowercase
    normalized = str(name).lower().strip()

    # Handle "Last, First" format -> "First Last"
    if "," in normalized:
        parts = [p.strip() for p in normalized.split(",")]
        normalized = " ".join(reversed(parts))

    # Remove periods (from initials like "L." or "J.")
    normalized = normalized.replace(".", " ")

    # Remove extra whitespace and normalize
    normalized = " ".join(normalized.split())

    return normalized


def _normalize_market_rows(player_market_rows: pd.DataFrame) -> pd.DataFrame:
    if player_market_rows is None or player_market_rows.empty:
        return pd.DataFrame()

    rows = player_market_rows.copy()

    raw_market_series = None
    if "market" in rows.columns:
        raw_market_series = rows["market"]
    elif "market_type" in rows.columns:
        raw_market_series = rows["market_type"]
    elif "raw_market_name" in rows.columns:
        raw_market_series = rows["raw_market_name"]

    if raw_market_series is not None:
        rows["_raw_market"] = raw_market_series
        rows["_normalized_market"] = rows["_raw_market"].apply(
            lambda value: normalize_market_alias(value) or str(value).strip()
        )
        rows["market"] = rows["_normalized_market"]

    team_series = None
    if "team_abbr" in rows.columns:
        team_series = rows["team_abbr"]
    elif "team" in rows.columns:
        team_series = rows["team"]
    if team_series is not None:
        rows["_team_abbr"] = team_series.fillna("").astype(str).str.strip().str.upper()

    if "line" in rows.columns:
        rows["line"] = rows["line"].apply(_safe_float)

    if "odds" in rows.columns:
        rows["odds"] = rows["odds"].apply(_safe_float)

    if "quality_score" in rows.columns:
        rows["quality_score"] = rows["quality_score"].apply(_safe_float)

    if "confidence" in rows.columns:
        rows["confidence"] = rows["confidence"].apply(_safe_float)

    return rows


def _is_milestone_market_row(row: pd.Series) -> bool:
    raw_market_type = str(row.get("raw_market_type", row.get("market.type", "")) or "").strip().lower()
    selection = str(row.get("selection", "") or "").strip().lower()
    return raw_market_type == "milestone" or selection == "milestone"


def _summarize_market_rows(rows: pd.DataFrame, limit: int = 5) -> list[dict[str, Any]]:
    if rows is None or rows.empty:
        return []
    sample = rows.head(limit).copy()
    out: list[dict[str, Any]] = []
    for _, row in sample.iterrows():
        out.append(
            {
                "player_name": str(row.get("player_name", "") or "").strip(),
                "team_abbr": str(row.get("_team_abbr", row.get("team_abbr", row.get("team", ""))) or "").strip().upper(),
                "raw_market": str(row.get("_raw_market", row.get("market", "")) or "").strip(),
                "normalized_market": str(row.get("market", "") or "").strip(),
            }
        )
    return out


def score_player_markets(
    *,
    players_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    is_player_inactive: Callable[[str], bool],
    build_candidate_row: Callable[..., dict[str, Any]],
    score_candidate_fn: Callable[..., dict[str, Any] | None],
    reject_candidate_fn: Callable[..., dict[str, Any]],
    allow_partial_fill: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Build and score player-market candidates.

    Returns:
        accepted_candidates, rejected_candidates

    Expected call pattern:
        accepted, rejected = score_player_markets(
            players_df=...,
            odds_df=...,
            is_player_inactive=self._is_player_inactive,
            build_candidate_row=self._build_candidate_row,
            score_candidate_fn=self._score_candidate,
            reject_candidate_fn=self._build_rejected_candidate,
        )
    """
    accepted_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    if players_df is None or players_df.empty:
        return accepted_candidates, rejected_candidates

    if odds_df is None:
        odds_df = pd.DataFrame()

    working_odds = odds_df.copy()
    if not working_odds.empty:
        if "player_name" not in working_odds.columns and "player" in working_odds.columns:
            working_odds["player_name"] = working_odds["player"]

        # Normalize player names for matching
        if "player_name" in working_odds.columns:
            working_odds["_normalized_name"] = working_odds["player_name"].apply(
                _normalize_player_name
            )
        if "team_abbr" in working_odds.columns:
            working_odds["_team_abbr"] = working_odds["team_abbr"].fillna("").astype(str).str.strip().str.upper()
        elif "team" in working_odds.columns:
            working_odds["_team_abbr"] = working_odds["team"].fillna("").astype(str).str.strip().str.upper()

    working_odds = _normalize_market_rows(working_odds)
    unsupported_milestone_count = 0
    if not working_odds.empty:
        milestone_mask = working_odds.apply(_is_milestone_market_row, axis=1)
        unsupported_milestone_count = int(milestone_mask.sum())
        if unsupported_milestone_count:
            working_odds = working_odds[~milestone_mask].copy()
    print(f"[COUNT] unsupported_milestone_count={unsupported_milestone_count}", flush=True)

    # Prefer player_id matching - create lookup if player_id available
    player_id_lookup: dict[int, pd.DataFrame] = {}
    if not working_odds.empty and "player_id" in working_odds.columns:
        # Group by player_id for fast lookup
        player_id_lookup = {
            int(pid): group for pid, group in working_odds.groupby("player_id")
            if pd.notna(pid) and int(pid) > 0
        }
    sample_odds_pids = list(player_id_lookup.keys())[:5] if player_id_lookup else []
    print(
        f"[COUNT] odds_player_id_lookup size={len(player_id_lookup)} "
        f"sample_ids={sample_odds_pids}",
        flush=True,
    )

    coverage_by_market: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidate_count": 0, "matched_count": 0, "missing_market_lines_count": 0, "unsupported_count": 0}
    )
    coverage_by_team: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidate_count": 0, "matched_count": 0, "missing_market_lines_count": 0, "unsupported_count": 0}
    )
    projection_support_breakdown: dict[str, int] = defaultdict(int)
    top_unmatched_players: list[dict[str, Any]] = []
    unmatched_samples: list[dict[str, Any]] = []
    missing_coverage_causes: Counter[str] = Counter()
    market_alias_audit: Counter[tuple[str, str]] = Counter()
    skipped_none_candidates: int = 0
    # Market match rate tracking
    market_match_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"attempted": 0, "matched": 0})

    if not working_odds.empty:
        for _, row in working_odds.iterrows():
            raw_market = str(row.get("_raw_market", row.get("market", "")) or "").strip()
            normalized_market = str(row.get("market", "") or "").strip()
            market_alias_audit[(raw_market, normalized_market)] += 1

    # Track match diagnostics
    total_players = 0
    matched_players = 0
    baseline_player_ids: list[int] = []
    
    # Collect baseline player_ids for intersection analysis
    for _, player_row in players_df.iterrows():
        pid = int(player_row.get("player_id", 0) or 0)
        if pid > 0:
            baseline_player_ids.append(pid)
    
    # Calculate intersection between odds player_ids and baseline player_ids.
    odds_player_ids = set(player_id_lookup.keys())
    baseline_ids_set = set(baseline_player_ids)
    intersection = odds_player_ids & baseline_ids_set
    print(
        f"[COUNT] odds_baseline_intersection "
        f"odds_ids={len(odds_player_ids)} baseline_ids={len(baseline_ids_set)} "
        f"intersection={len(intersection)} sample_intersection={list(intersection)[:5]}",
        flush=True,
    )

    for _, player_row in players_df.iterrows():
        player_name = str(player_row.get("player_name", "")).strip()
        team = str(player_row.get("team_abbr") or player_row.get("team") or "").strip().upper()

        if not player_name:
            continue

        total_players += 1

        if is_player_inactive(player_name):
            rejected_candidates.append(
                reject_candidate_fn(
                    player_row=player_row,
                    market=None,
                    reason="player_ruled_out_or_doubtful",
                    team=team,
                )
            )
            continue

        # Prefer player_id matching, fall back to normalized name
        player_id = int(player_row.get("player_id", 0) or 0)
        normalized_player_name = _normalize_player_name(player_name)
        exact_name_rows = pd.DataFrame()
        player_market_rows = pd.DataFrame()
        matched_by_id = False

        # Try player_id matching first (more reliable).
        if player_id > 0 and player_id in player_id_lookup:
            player_market_rows = player_id_lookup[player_id].copy()
            exact_name_rows = player_market_rows.copy()
            matched_players += 1
            matched_by_id = True
        # Fall back to normalized name matching
        elif not working_odds.empty and "_normalized_name" in working_odds.columns:
            exact_name_rows = working_odds[
                working_odds["_normalized_name"] == normalized_player_name
            ].copy()
            player_market_rows = exact_name_rows.copy()
            if not player_market_rows.empty:
                matched_players += 1

        same_team_rows = pd.DataFrame()
        if not working_odds.empty and "_team_abbr" in working_odds.columns and team:
            same_team_rows = working_odds[working_odds["_team_abbr"] == team].copy()

        available_markets = set()
        if not player_market_rows.empty and "market" in player_market_rows.columns:
            available_markets = {
                str(m).strip()
                for m in player_market_rows["market"].dropna().tolist()
                if str(m).strip()
            }
        matched_core_markets = sorted(m for m in available_markets if m in PRIMARY_PLAYER_MARKETS)

        if not matched_core_markets:
            exact_name_same_team_rows = pd.DataFrame()
            if not exact_name_rows.empty and "_team_abbr" in exact_name_rows.columns and team:
                exact_name_same_team_rows = exact_name_rows[exact_name_rows["_team_abbr"] == team].copy()

            if exact_name_rows.empty:
                cause = "player_mismatch" if not same_team_rows.empty else "no_team_market_coverage"
            elif exact_name_same_team_rows.empty:
                cause = "team_mismatch"
            else:
                normalized_markets = {
                    str(m).strip()
                    for m in exact_name_same_team_rows.get("market", pd.Series(dtype=object)).dropna().tolist()
                    if str(m).strip()
                }
                if not normalized_markets:
                    cause = "market_alias_mismatch"
                else:
                    cause = "missing_prop_coverage"

            missing_coverage_causes[cause] += 1
            candidate_markets = sorted(CORE_PARTIAL_PLAYER_MARKETS)
            unmatched_detail = {
                "player_name": player_name,
                "team_abbr": team,
                "candidate_market_types": candidate_markets,
                "same_player_market_rows": _summarize_market_rows(exact_name_rows),
                "same_team_market_rows": _summarize_market_rows(same_team_rows),
                "coverage_cause": cause,
            }
            if len(top_unmatched_players) < 20:
                top_unmatched_players.append(unmatched_detail)
            if len(unmatched_samples) < 10:
                unmatched_samples.append(unmatched_detail)

        # Score all live player markets first.
        for market in sorted(available_markets):
            market_match_counts[market]["attempted"] += 1
            if market not in PRIMARY_PLAYER_MARKETS:
                market_match_counts[market]["matched"] += 0  # Track attempted but filtered
                continue

            market_match_counts[market]["matched"] += 1
            coverage_by_market[market]["candidate_count"] += 1
            coverage_by_market[market]["matched_count"] += 1
            coverage_by_team[team]["candidate_count"] += 1
            coverage_by_team[team]["matched_count"] += 1

            market_rows = player_market_rows[
                player_market_rows["market"].astype(str).str.strip() == market
            ].copy()

            candidate_row = build_candidate_row(
                player_row=player_row,
                market=market,
                market_rows=market_rows,
                partial_fill=False,
            )

            # Handle unsupported markets (build_candidate_row returned None)
            if candidate_row is None:
                # Determine rejection reason based on market type
                if market in ("player_rebounds", "player_assists", "player_3pt_made", "player_steals", "player_blocks"):
                    rejection_reason = "unsupported_projection_market"
                    support_status = "unsupported_market"
                elif market == "player_points":
                    rejection_reason = "invalid_projection_output"
                    support_status = "projection_missing"
                else:
                    rejection_reason = "unsupported_market_type"
                    support_status = "unsupported_market"

                # Track unsupported counts
                coverage_by_market[market]["unsupported_count"] += 1
                coverage_by_team[team]["unsupported_count"] += 1
                projection_support_breakdown[support_status] += 1

                rejected_candidates.append(
                    reject_candidate_fn(
                        player_row=player_row,
                        market=market,
                        reason=rejection_reason,
                        team=team,
                        projection_support_status=support_status,
                    )
                )
                continue

            scored = score_candidate_fn(
                candidate_row=candidate_row,
                player_row=player_row,
                market=market,
                market_rows=market_rows,
                partial_fill=False,
            )

            if scored is None:
                reason = str(candidate_row.get("pre_rejection_reason", "") or "").strip()
                if not reason:
                    edge = _safe_float(candidate_row.get("edge"), 0.0)
                    confidence = _safe_float(candidate_row.get("confidence"), 0.0)
                    if abs(edge) < 0.5:
                        reason = "market_supported_but_failed_quality"
                    elif confidence < 0.35:
                        reason = "market_supported_but_failed_confidence"
                    else:
                        reason = "edge_and_confidence_below_threshold"
                rejected_candidates.append(
                    reject_candidate_fn(
                        player_row=player_row,
                        market=market,
                        reason=reason,
                        team=team,
                        projection_support_status=candidate_row.get("projection_support_status", ""),
                    )
                )
            else:
                accepted_candidates.append(scored)

        # Handle partial-fill candidates for missing markets.
        if allow_partial_fill:
            missing_markets = partial_fill_markets(
                player_market_rows=player_market_rows,
                available_markets=available_markets,
            )

            for market in missing_markets:
                if market not in CORE_PARTIAL_PLAYER_MARKETS:
                    continue

                coverage_by_market[market]["candidate_count"] += 1
                coverage_by_team[team]["candidate_count"] += 1

                candidate_row = build_candidate_row(
                    player_row=player_row,
                    market=market,
                    market_rows=pd.DataFrame(),
                    partial_fill=True,
                )

                # Skip unsupported markets (None from build_candidate_row)
                if candidate_row is None:
                    skipped_none_candidates += 1
                    coverage_by_market[market]["unsupported_count"] += 1
                    coverage_by_team[team]["unsupported_count"] += 1
                    rejected_candidates.append(
                        reject_candidate_fn(
                            player_row=player_row,
                            market=market,
                            reason="unsupported_projection_market",
                            team=team,
                            projection_support_status="unsupported_market",
                        )
                    )
                    continue

                scored = score_candidate_fn(
                    candidate_row=candidate_row,
                    player_row=player_row,
                    market=market,
                    market_rows=pd.DataFrame(),
                    partial_fill=True,
                )

                if scored is None:
                    reason = str(candidate_row.get("pre_rejection_reason", "") or "").strip()
                    if not reason:
                        edge = _safe_float(candidate_row.get("edge"), 0.0)
                        confidence = _safe_float(candidate_row.get("confidence"), 0.0)
                        if abs(edge) < 0.5:
                            reason = "market_supported_but_failed_quality"
                        elif confidence < 0.35:
                            reason = "market_supported_but_failed_confidence"
                        else:
                            reason = "edge_and_confidence_below_threshold"
                    coverage_by_market[market]["missing_market_lines_count"] += 1
                    coverage_by_team[team]["missing_market_lines_count"] += 1
                    rejected_candidates.append(
                        reject_candidate_fn(
                            player_row=player_row,
                            market=market,
                            reason=reason,
                            team=team,
                            projection_support_status=candidate_row.get("projection_support_status", ""),
                        )
                    )
                else:
                    accepted_candidates.append(scored)

    # Match rate diagnostics
    match_rate = (matched_players / total_players * 100) if total_players > 0 else 0
    logger.info(
            "player_odds_match_rate total_players=%d matched_players=%d match_rate=%.1f%%",
            total_players,
            matched_players,
            match_rate,
        )
    logger.info(
        "skipped_none_candidates count=%d",
        skipped_none_candidates,
    )
    logger.info(
        "market_type_alias_audit %s",
        [
            {"raw_market": raw_market, "normalized_market": normalized_market, "count": count}
            for (raw_market, normalized_market), count in sorted(
                market_alias_audit.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:20]
        ],
    )
    logger.info(
        "market_coverage_by_type %s",
        [
            {"market_type": market_type, **stats}
            for market_type, stats in sorted(coverage_by_market.items())
        ],
    )
    # Market match rate by market_type - tracks coverage efficiency
    logger.info(
        "market_match_rate %s",
        [
            {
                "market_type": market,
                "attempted": counts["attempted"],
                "matched": counts["matched"],
                "rate_pct": round(100 * counts["matched"] / counts["attempted"], 1) if counts["attempted"] > 0 else 0
            }
            for market, counts in sorted(market_match_counts.items())
        ],
    )
    logger.info(
        "market_coverage_by_team %s",
        [
            {"team_abbr": team_abbr, **stats}
            for team_abbr, stats in sorted(
                coverage_by_team.items(),
                key=lambda item: (item[1]["missing_market_lines_count"], item[1]["candidate_count"]),
                reverse=True,
            )[:30]
        ],
    )
    logger.info(
        "top_missing_coverage_causes %s",
        [{"cause": cause, "count": count} for cause, count in missing_coverage_causes.most_common(10)],
    )
    # Summary stats only - avoid large debug dumps
    logger.debug(f"[provider_fetch] unmatched_count={len(top_unmatched_players)}")

    # Projection support status breakdown
    logger.info(
        "projection_support_breakdown %s",
        [{"status": status, "count": count} for status, count in sorted(projection_support_breakdown.items())],
    )

    # Rejection reason breakdown
    rejection_reasons: dict[str, int] = defaultdict(int)
    for rc in rejected_candidates:
        reason = rc.get("rejection_reason", "unknown")
        rejection_reasons[reason] += 1
    rejection_breakdown = [
        {"reason": reason, "count": count}
        for reason, count in sorted(rejection_reasons.items(), key=lambda x: -x[1])
    ]
    logger.info("rejection_breakdown_by_reason %s", rejection_breakdown)
    print(f"[COUNT] rejection_breakdown={rejection_breakdown}", flush=True)

    # Diagnostics for low baseline-coverage runs.
    #
    # Note: a low `match_rate` (baseline players whose props the books posted
    # tonight) is *not* a data-quality signal. Many baseline players simply do
    # not play, or no vendor lists props for them. Discarding all already-
    # accepted candidates in that case is a stale kill-switch from when the
    # upstream odds normalizer was returning unresolved rows. Keep the
    # diagnostics so coverage drops are still visible, but do not nuke the
    # candidates that scored cleanly. Downstream elite admission/qualification
    # remains the authoritative safety layer.
    if match_rate < 50 and total_players > 0:
        sample_players = players_df["player_name"].head(5).tolist() if "player_name" in players_df.columns else []
        odds_with_names = working_odds["player_name"].notna().sum() if "player_name" in working_odds.columns else 0

        print(f"[COUNT] total_players={total_players}", flush=True)
        print(f"[COUNT] matched_players={matched_players}", flush=True)
        print(f"[COUNT] match_rate={match_rate:.1f}%", flush=True)
        print(f"[COUNT] odds_rows_with_player_name={odds_with_names}", flush=True)
        print(f"[COUNT] odds_rows_total={len(working_odds)}", flush=True)
        print(f"[COUNT] accepted_candidates_pre_return={len(accepted_candidates)}", flush=True)
        print(f"[COUNT] rejected_candidates_pre_return={len(rejected_candidates)}", flush=True)
        print(f"[DIAGNOSIS] Low baseline coverage (informational, not fatal)", flush=True)
        print(f"  Sample players from baselines: {sample_players}", flush=True)
        print(
            f"[WARNING] Low baseline coverage: {matched_players}/{total_players} players matched, "
            f"odds rows={len(working_odds)} — keeping {len(accepted_candidates)} accepted candidates",
            flush=True,
        )

    return accepted_candidates, rejected_candidates
