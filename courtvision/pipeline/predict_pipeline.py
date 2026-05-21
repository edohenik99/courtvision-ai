"""Package-owned prediction pipeline.

This module extracts the prediction orchestration flow from courtvision_ai.py
into a package-owned pipeline that delegates to specialized modules.
"""

from __future__ import annotations

import logging
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from courtvision.calibration.buckets import to_float
from courtvision.artifact_guard import log_prediction_artifact_write
from courtvision.context.game_context import (
    IDENTITY_QUARANTINE_REJECTION_REASON,
    identity_quarantine_reason_counts,
    mark_identity_quarantine_fields,
)
from courtvision.context.player_identity import (
    annotate_source_identity_conflicts,
    build_canonical_player_identity_resolver,
    player_identity_reason_counts,
    source_identity_conflict_exposure_summary,
)
from courtvision.data.candidates import score_player_markets
from courtvision.injuries import InjuryEngine
from courtvision.market import MarketEvaluator, normalize_market_alias
from courtvision.runtime_audit import (
    EliteTelemetry,
    assemble_elite_board,
    get_elite_rejection_reason,
)
from courtvision.runtime_gates import (
    _is_before_lock_buffer,
    _parse_game_datetime,
    game_status_ineligibility_reason,
    odds_stale_ineligibility_reason,
)
from courtvision.scoring import CandidateScoringPolicy
from courtvision.config import DEFAULT_BANKROLL, DEFAULT_ELITE_BOARD_SIZE, EliteThresholds
from courtvision.selection import (
    ACTIVE_OPERATOR_MARKETS,
    build_operator_boards,
    duplicate_betting_identity_drop_summary,
    unsupported_active_operator_market_drop_summary,
)
from courtvision.selection.pipeline_selectors import (
    apply_elite_exposure_caps,
    elite_market_policy_rejection_reason,
    finalize_elite_board,
    resolve_elite_allowed_markets,
    select_top_per_market as select_top_per_market_helper,
)
from courtvision.reason_codes import REJECT_NEGATIVE_EDGE_DIRECTION
from courtvision.betting.kelly import compute_kelly_fraction
from courtvision.selection.operator_boards import assign_candidate_lanes
from courtvision.projection.recalibration import (
    get_recalibration_mode,
    recalibrate_player_points,
    RecalibrationMode,
)


def _empty_injury_context() -> dict[str, Any]:
    """Return the canonical empty injury context shape."""
    return {
        "teams": {},
        "players": {},
        "metadata": {},
    }


def _nan_safe_to_float(value: Any, default: float | None = None) -> float | None:
    """Convert to float while treating None/blank/NaN as missing."""
    converted = to_float(value)
    if converted is None or pd.isna(converted):
        return default
    return float(converted)


def _normalize_game_key(row: Any) -> str:
    """Return stable game key - prefer int game_id, fallback to team@opponent.

    This helper ensures consistent game key normalization across cap enforcement
    and analytics calculations. Handles both Series (DataFrame row) and dict.
    """
    # Handle both Series and dict-like objects
    if hasattr(row, "get"):
        raw_game_id = row.get("game_id")
        team = str(row.get("team_abbr", row.get("team", ""))).strip().upper()
        opponent = str(row.get("opponent", "")).strip().upper()
    else:
        raw_game_id = row.game_id if hasattr(row, "game_id") else None
        team = str(getattr(row, "team_abbr", getattr(row, "team", ""))).strip().upper()
        opponent = str(getattr(row, "opponent", "")).strip().upper()

    # Prefer game_id, cast to int
    if raw_game_id is not None and str(raw_game_id).strip():
        try:
            return str(int(float(str(raw_game_id))))
        except (ValueError, TypeError):
            pass

    # Fallback: team@opponent composite key (sorted for stability)
    if team and opponent:
        teams_sorted = sorted([team, opponent])
        return f"{teams_sorted[0]}@{teams_sorted[1]}"

    # Last resort: team alone
    if team:
        return team

    return "unknown"


@dataclass(frozen=True, slots=True)
class PredictionConfig:
    """Configuration for prediction pipeline."""

    prediction_date: str
    out_dir: str = "outputs"
    player_baselines_path: Path | None = None
    team_baselines_path: Path | None = None
    injuries_path: Path | None = None
    verbose_outputs: bool = False

    # Scoring configuration
    min_edge: float = 0.5
    min_confidence: float = 0.35
    max_edge_cap: float = 15.0
    synthetic_odds_default: int = -110

    # Feature flags
    enable_injury_context: bool = True
    enable_market_quality: bool = True
    enable_partial_fill: bool = True
    elite_market_mode: str = "points_only"
    elite_allowed_markets: tuple[str, ...] = ()

    # Observability: active-game player count from provider lookup (Phase 11A)
    player_lookup_size: int = 0


@dataclass
class PredictionResult:
    """Result container for prediction pipeline."""

    prediction_date: str
    summary: dict[str, Any] = field(default_factory=dict)
    selected_props: pd.DataFrame = field(default_factory=pd.DataFrame)
    elite_props: pd.DataFrame = field(default_factory=pd.DataFrame)
    full_market_props: pd.DataFrame = field(default_factory=pd.DataFrame)
    stat_only_props: pd.DataFrame = field(default_factory=pd.DataFrame)
    market_lines: pd.DataFrame = field(default_factory=pd.DataFrame)
    merged_market_props: pd.DataFrame = field(default_factory=pd.DataFrame)
    rejected_candidate_diagnostics: pd.DataFrame = field(default_factory=pd.DataFrame)
    output_paths: dict[str, Path | None] = field(default_factory=dict)
    injury_context: dict[str, Any] = field(default_factory=_empty_injury_context)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for serialization."""
        return {
            "prediction_date": self.prediction_date,
            "summary": self.summary,
            "selected_props": self.selected_props.to_dict("records") if not self.selected_props.empty else [],
            "elite_props": self.elite_props.to_dict("records") if not self.elite_props.empty else [],
            "full_market_props": self.full_market_props.to_dict("records") if not self.full_market_props.empty else [],
            "stat_only_props": self.stat_only_props.to_dict("records") if not self.stat_only_props.empty else [],
            "market_lines": self.market_lines.to_dict("records") if not self.market_lines.empty else [],
            "merged_market_props": self.merged_market_props.to_dict("records") if not self.merged_market_props.empty else [],
            "rejected_candidate_diagnostics": self.rejected_candidate_diagnostics.to_dict("records") if not self.rejected_candidate_diagnostics.empty else [],
            "output_paths": {k: str(v) if v else None for k, v in self.output_paths.items()},
        }


class PredictionPipeline:
    """Package-owned prediction pipeline.

    Orchestrates the full prediction flow:
    1. Load slate / inputs
    2. Build candidate universe
    3. Evaluate injury context
    4. Evaluate market context
    5. Compute edge / confidence / penalties / selection score
    6. Assign lanes
    7. Build boards
    8. Export outputs
    """

    FULL_MARKET_READINESS_GATES: dict[str, dict[str, float]] = {
        "player_rebounds": {"min_minutes": 24.0, "min_confidence": 0.60},
        "player_assists": {"min_minutes": 24.0, "min_confidence": 0.60},
        "player_points_rebounds": {"min_minutes": 28.0, "min_confidence": 0.70},
        "player_points_assists": {"min_minutes": 28.0, "min_confidence": 0.70},
        "player_rebounds_assists": {"min_minutes": 28.0, "min_confidence": 0.70},
        "player_points_rebounds_assists": {"min_minutes": 28.0, "min_confidence": 0.70},
    }

    def __init__(
        self,
        config: PredictionConfig,
        scoring_policy: CandidateScoringPolicy | None = None,
        injury_engine: InjuryEngine | None = None,
        market_evaluator: MarketEvaluator | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.scoring_policy = scoring_policy or CandidateScoringPolicy()
        self.injury_engine = injury_engine or InjuryEngine()
        self.market_evaluator = market_evaluator or MarketEvaluator()
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self._elite_allowed_markets = self._resolve_elite_allowed_markets()

    def _resolve_elite_allowed_markets(self) -> set[str]:
        return resolve_elite_allowed_markets(
            elite_market_mode=getattr(self.config, "elite_market_mode", "points_only"),
            elite_allowed_markets=getattr(self.config, "elite_allowed_markets", ()),
        )

    def _normalize_injury_context(
        self,
        injury_context: dict[str, Any] | None,
        source: str,
    ) -> tuple[dict[str, Any], str]:
        """Normalize injury context to the canonical dict shape."""
        if not isinstance(injury_context, dict):
            normalized = _empty_injury_context()
            normalized_source = f"{source}_none"
        else:
            normalized = {
                "teams": injury_context.get("teams", {}) if isinstance(injury_context.get("teams", {}), dict) else {},
                "players": injury_context.get("players", {}) if isinstance(injury_context.get("players", {}), dict) else {},
                "metadata": injury_context.get("metadata", {}) if isinstance(injury_context.get("metadata", {}), dict) else {},
            }
            normalized_source = source

        self.logger.info(
            "injury_context_normalized teams=%d players=%d source=%s",
            len(normalized.get("teams", {})),
            len(normalized.get("players", {})),
            normalized_source,
        )
        return normalized, normalized_source

    def run(
        self,
        games: pd.DataFrame,
        odds: pd.DataFrame,
        player_baselines: pd.DataFrame,
        team_baselines: pd.DataFrame | None = None,
        injuries: pd.DataFrame | None = None,
        is_player_inactive_fn: Callable[[str], bool] | None = None,
    ) -> PredictionResult:
        """Run the complete prediction pipeline."""
        self.logger.info("prediction_start date=%s games=%d odds=%d",
                        self.config.prediction_date, len(games), len(odds))
        
        # [COUNT] Stage telemetry - initial inputs
        players_count = len(player_baselines) if player_baselines is not None else 0
        print(f"[COUNT] games_fetched={len(games)}")
        print(f"[COUNT] players_loaded={players_count}")
        print(f"[COUNT] odds_rows={len(odds)}")
        if isinstance(odds, pd.DataFrame) and not odds.empty:
            if "raw_prop_type" in odds.columns:
                odds_by_raw_prop_type = odds["raw_prop_type"].fillna("").astype(str).value_counts().to_dict()
                print(f"[COUNT] odds_by_raw_prop_type={odds_by_raw_prop_type}", flush=True)
            if "raw_market_type" in odds.columns:
                odds_by_market_type = odds["raw_market_type"].fillna("").astype(str).value_counts().to_dict()
            elif "market.type" in odds.columns:
                odds_by_market_type = odds["market.type"].fillna("").astype(str).value_counts().to_dict()
            else:
                odds_by_market_type = {}
            print(f"[COUNT] odds_by_market_type={odds_by_market_type}", flush=True)
            if "selection" in odds.columns:
                normalized_selection_counts = odds["selection"].fillna("").astype(str).str.strip().str.lower().value_counts().to_dict()
                print(f"[COUNT] normalized_over_rows={int(normalized_selection_counts.get('over', 0))}", flush=True)
                print(f"[COUNT] normalized_under_rows={int(normalized_selection_counts.get('under', 0))}", flush=True)

        # Initialize result
        result = PredictionResult(prediction_date=self.config.prediction_date)

        # Use games DataFrame directly; schema normalization handled downstream
        games_normalized = games if not games.empty else games
        if not games_normalized.empty:
            self.logger.info("games_normalized schema=%s cols=%s",
                            "canonical",
                            list(games_normalized.columns))

        # Build injury context if enabled and data available
        injury_context: dict[str, Any] | None = None
        injury_context_source = "disabled" if not self.config.enable_injury_context else "no_injuries"
        if self.config.enable_injury_context and injuries is not None and not injuries.empty:
            injury_context_source = "injuries_available"
            # Use normalized team columns: home_team_abbr, visitor_team_abbr
            # Falls back to team_id if abbr not available
            home_teams = games_normalized.get("home_team_abbr", games_normalized.get("home_team_id"))
            visitor_teams = games_normalized.get("visitor_team_abbr", games_normalized.get("visitor_team_id"))
            if home_teams is not None and visitor_teams is not None:
                injury_context_source = "injury_engine"
                active_teams = set(
                    home_teams.dropna().astype(str).tolist() +
                    visitor_teams.dropna().astype(str).tolist()
                )
                injury_context = self.injury_engine.build_context(
                    injuries=injuries,
                    player_baselines=player_baselines,
                    active_teams=active_teams,
                )
                built_teams = 0
                built_players = 0
                if isinstance(injury_context, dict):
                    built_teams = len(injury_context.get("teams", {}))
                    built_players = len(injury_context.get("players", {}))
                self.logger.info("injury_context_built teams=%d players=%d",
                                built_teams,
                                built_players)
            else:
                injury_context_source = "missing_active_teams"

        injury_context, injury_context_source = self._normalize_injury_context(
            injury_context=injury_context,
            source=injury_context_source,
        )

        # Build candidate universe using normalized games
        candidates, rejected, player_identity_summary = self._build_candidate_universe(
            games=games_normalized,
            odds=odds,
            player_baselines=player_baselines,
            team_baselines=team_baselines,
            injury_context=injury_context,
            is_player_inactive_fn=is_player_inactive_fn,
        )

        # [COUNT] Raw candidates from universe build
        print(f"[COUNT] raw_candidates={len(candidates)}")
        print(f"[COUNT] raw_rejected={len(rejected)}")
        print(
            f"[COUNT] player_identity_conflict_count="
            f"{int(player_identity_summary.get('conflict_count', 0) or 0)}",
            flush=True,
        )
        print(
            f"[COUNT] player_identity_conflict_reason_counts="
            f"{player_identity_summary.get('counts_by_reason', {})}",
            flush=True,
        )
        
        self.logger.info("candidates_built accepted=%d rejected=%d", len(candidates), len(rejected))

        # Stage-level diagnostics: rejection breakdown
        if rejected:
            rejection_counts: dict[str, int] = {}
            rejection_details: dict[str, list] = {"low_edge": [], "low_confidence": []}

            for r in rejected:
                reason = r.get("rejection_reason", "unknown")
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

                # Track edge/confidence values for debugging
                if reason == "low_edge":
                    rejection_details["low_edge"].append(r.get("edge", 0))
                elif reason == "low_confidence":
                    rejection_details["low_confidence"].append(r.get("confidence", 0))

            self.logger.info("rejection_breakdown %s", rejection_counts)

            # Identify top 3 rejection causes
            top_rejections = sorted(rejection_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            self.logger.info("top_rejection_causes %s", top_rejections)

            # Log sample values for debugging
            if rejection_details["low_edge"]:
                sample_edges = rejection_details["low_edge"][:5]
                self.logger.info("sample_low_edge_values %s (threshold=0.5)", sample_edges)
            if rejection_details["low_confidence"]:
                sample_conf = rejection_details["low_confidence"][:5]
                self.logger.info("sample_low_confidence_values %s (threshold=0.35)", sample_conf)

        if not candidates:
            print("[COUNT] pipeline_exit=no_candidates")
            print("[DIAGNOSIS] Zero candidates after universe build - check data sources and filters")
            result.injury_context = injury_context
            result.rejected_candidate_diagnostics = pd.DataFrame(rejected) if rejected else pd.DataFrame()
            result.summary = self._build_empty_summary(games, odds)
            self._attach_player_identity_summary(
                result.summary,
                player_identity_summary,
                pd.DataFrame(),
            )
            return result

        # Convert candidates to DataFrame and mark identity-invalid rows before any board ranking.
        candidates_df = mark_identity_quarantine_fields(pd.DataFrame(candidates))
        candidates_df = annotate_source_identity_conflicts(candidates_df, player_identity_summary)
        candidate_identity_counts = player_identity_reason_counts(candidates_df)
        identity_quarantine_counts = identity_quarantine_reason_counts(candidates_df)
        identity_quarantine_count = int(sum(identity_quarantine_counts.values()))
        identity_rejected_df = (
            candidates_df[
                candidates_df.get("selection_rejection_reason", pd.Series("", index=candidates_df.index))
                .fillna("")
                .astype(str)
                .str.strip()
                .eq(IDENTITY_QUARANTINE_REJECTION_REASON)
            ].copy()
            if identity_quarantine_count
            else pd.DataFrame()
        )
        rejected_source_df = pd.DataFrame(rejected) if rejected else pd.DataFrame()
        if not identity_rejected_df.empty:
            rejected_diagnostics_frames = [frame for frame in (rejected_source_df, identity_rejected_df) if not frame.empty]
            result.rejected_candidate_diagnostics = pd.concat(
                rejected_diagnostics_frames,
                ignore_index=True,
                sort=False,
            )
        else:
            result.rejected_candidate_diagnostics = rejected_source_df
        print(f"[COUNT] identity_quarantine_count={identity_quarantine_count}", flush=True)
        print(f"[COUNT] identity_quarantine_reason_counts={identity_quarantine_counts}", flush=True)

        # Define selection callables for board building
        selection_stage_trace: dict[str, dict[str, Any]] = {
            "elite": {},
            "full_market": {},
        }

        # Initialize elite telemetry collector and thresholds
        slate_date = str(getattr(self.config, 'prediction_date', None) or datetime.now().strftime("%Y-%m-%d"))
        elite_telemetry = EliteTelemetry(slate_date=slate_date)
        elite_thresholds = EliteThresholds.default()

        def select_elite_board(df: pd.DataFrame) -> pd.DataFrame:
            """Select elite candidates (strong conviction, high confidence)."""
            selection_stage_trace["elite"]["candidate_count_entering_elite_selection"] = len(df)
            if df.empty:
                selection_stage_trace["elite"]["candidate_count_after_elite_admission_filter"] = 0
                selection_stage_trace["elite"]["candidate_count_after_realism_filter"] = 0
                selection_stage_trace["elite"]["candidate_count_after_exposure_caps"] = 0
                selection_stage_trace["elite"]["candidate_count_after_backfill"] = 0
                return df
            
            # Log count by market_type BEFORE filtering
            if "market_type" in df.columns:
                count_before = df["market_type"].value_counts().to_dict()
                self.logger.info("count_by_market_type_before_filter %s", count_before)
            
            # Sort by selection_score to prioritize best bets
            df_sorted = df.sort_values("selection_score", ascending=False) if "selection_score" in df.columns else df
            
            # Track all candidates entering elite selection
            for _, row in df_sorted.iterrows():
                market = str(row.get("market_type", row.get("market", "unknown")))
                selection = str(row.get("selection", "unknown")).lower()
                elite_telemetry.record_candidate_seen(
                    market=market,
                    selection_side=selection,
                )
            
            # Filter for high quality and high confidence with telemetry
            passed_mask = []
            for _, row in df_sorted.iterrows():
                market = str(row.get("market_type", row.get("market", "unknown")))
                selection = str(row.get("selection", "unknown")).lower()
                market_rejection_reason = elite_market_policy_rejection_reason(
                    market,
                    self._elite_allowed_markets,
                )
                if market_rejection_reason is not None:
                    elite_telemetry.record(
                        market=market,
                        selection_side=selection,
                        rejection_reason=market_rejection_reason,
                    )
                    passed_mask.append(False)
                    continue
                
                # Check basic elite criteria using centralized thresholds
                is_elite_flag = row.get("is_elite", False) == True
                quality_ok = row.get("quality_score", 0) >= elite_thresholds.quality_score
                confidence_ok = row.get("confidence", 0) >= elite_thresholds.confidence
                
                if is_elite_flag or (quality_ok and confidence_ok):
                    # Check directional edge validity
                    rejection_reason = get_elite_rejection_reason(row.to_dict())
                    if rejection_reason is None:
                        elite_telemetry.record_pass(
                            market=market,
                            selection_side=selection,
                        )
                        passed_mask.append(True)
                    else:
                        elite_telemetry.record(
                            market=market,
                            selection_side=selection,
                            rejection_reason=rejection_reason,
                        )
                        passed_mask.append(False)
                else:
                    # Failed quality/confidence check
                    elite_telemetry.record(
                        market=market,
                        selection_side=selection,
                        rejection_reason="reject_quality_confidence_threshold",
                    )
                    passed_mask.append(False)
            
            admitted_df = df_sorted[pd.Series(passed_mask, index=df_sorted.index)].copy()
            selection_stage_trace["elite"]["candidate_count_after_elite_admission_filter"] = len(admitted_df)
            selection_stage_trace["elite"]["candidate_count_after_realism_filter"] = len(admitted_df)
            selection_stage_trace["elite"]["candidate_count_after_exposure_caps"] = len(admitted_df)
            
            # Apply concentration caps (team/game limits)
            # Use EliteThresholds as primary source, allow self.config override if explicitly set
            has_config_team_cap = hasattr(self.config, 'elite_team_cap') and self.config.elite_team_cap is not None
            has_config_game_cap = hasattr(self.config, 'elite_game_cap') and self.config.elite_game_cap is not None
            
            elite_team_cap = self.config.elite_team_cap if has_config_team_cap else elite_thresholds.team_cap
            elite_game_cap = self.config.elite_game_cap if has_config_game_cap else elite_thresholds.game_cap
            
            cap_source = "config" if (has_config_team_cap or has_config_game_cap) else "EliteThresholds"
            self.logger.info(
                "[CAP_DEBUG] Starting cap enforcement: source=%s, team_cap=%d, game_cap=%d, candidates=%d",
                cap_source, elite_team_cap, elite_game_cap, len(admitted_df)
            )
            
            cap_result = apply_elite_exposure_caps(
                admitted_df,
                elite_team_cap=elite_team_cap,
                elite_game_cap=elite_game_cap,
            )
            capped_df = cap_result.capped_df
            team_counts = cap_result.team_counts
            game_counts = cap_result.game_counts
            skipped_by_team_cap = cap_result.skipped_by_team_cap
            skipped_by_game_cap = cap_result.skipped_by_game_cap

            self.logger.info(
                "[CAP_DEBUG] Cap enforcement starting: admitted_df has %d rows, sample game_keys: %s",
                len(admitted_df), cap_result.sample_game_keys
            )

            for decision in cap_result.row_decisions:
                self.logger.info(
                    "[CAP_DEBUG] Row %d: player=%s, team=%s, game_key=%s, "
                    "current_team_count=%d, current_game_count=%d, "
                    "team_cap=%d, game_cap=%d, "
                    "team_would_skip=%s, game_would_skip=%s",
                    decision.index, decision.player, decision.team, str(decision.game_key),
                    decision.current_team_count, decision.current_game_count,
                    elite_team_cap, elite_game_cap,
                    decision.team_would_skip,
                    decision.game_would_skip
                )

                if decision.action == "team_cap":
                    self.logger.info(
                        "[CAP_DEBUG] Row %d: SKIPPED by team_cap (count=%d)",
                        decision.index,
                        decision.current_team_count,
                    )
                elif decision.action == "game_cap":
                    self.logger.info(
                        "[CAP_DEBUG] Row %d: SKIPPED by game_cap (count=%d)",
                        decision.index,
                        decision.current_game_count,
                    )
                else:
                    self.logger.info(
                        "[CAP_DEBUG] Row %d: SELECTED (team_count now=%d, game_count now=%d)",
                        decision.index,
                        decision.selected_team_count,
                        decision.selected_game_count,
                    )

            # Log first 10 row game_keys for verification
            self.logger.info("[CAP_DEBUG] First 10 row game_keys: %s", ",".join(cap_result.first_10_game_keys))
            
            # Log final game counts
            final_game_counts_str = ",".join([f"{k}:{v}" for k, v in game_counts.items()]) if game_counts else "none"
            self.logger.info(
                "[CAP_DEBUG] Cap enforcement complete: capped_df=%d, skipped_team=%d, skipped_game=%d, final_game_counts=%s",
                len(capped_df), skipped_by_team_cap, skipped_by_game_cap, final_game_counts_str
            )
            
            selection_stage_trace["elite"]["candidate_count_after_concentration_caps"] = len(capped_df)
            selection_stage_trace["elite"]["skipped_by_team_cap"] = skipped_by_team_cap
            selection_stage_trace["elite"]["skipped_by_game_cap"] = skipped_by_game_cap
            selection_stage_trace["elite"]["max_team_exposure"] = max(team_counts.values()) if team_counts else 0
            selection_stage_trace["elite"]["max_game_exposure"] = max(game_counts.values()) if game_counts else 0
            selection_stage_trace["elite"]["unique_teams"] = len(team_counts)
            selection_stage_trace["elite"]["unique_games"] = len(game_counts)
            
            # Final selection: take top N after caps applied
            elite_size = self.config.elite_size if hasattr(self.config, 'elite_size') else DEFAULT_ELITE_BOARD_SIZE
            selected_df = finalize_elite_board(capped_df, elite_size=elite_size)
            selection_stage_trace["elite"]["candidate_count_after_backfill"] = len(selected_df)
            
            # Recalculate max exposure from ACTUAL final selection (not full capped_df)
            final_game_counts = {}
            final_team_counts = {}
            for _, row in selected_df.iterrows():
                # Use same normalization as cap enforcement loop
                gkey = _normalize_game_key(row)
                if gkey and gkey != "unknown":
                    final_game_counts[gkey] = final_game_counts.get(gkey, 0) + 1
                team_abbr = str(row.get("team_abbr", row.get("team", ""))).strip().upper()
                if team_abbr:
                    final_team_counts[team_abbr] = final_team_counts.get(team_abbr, 0) + 1
            
            final_max_game = max(final_game_counts.values()) if final_game_counts else 0
            final_max_team = max(final_team_counts.values()) if final_team_counts else 0
            
            # [CAP_ENFORCE] trace
            print(f"[CAP_ENFORCE] before_rows={len(df)} after_rows={len(selected_df)} skipped_team_cap={skipped_by_team_cap} skipped_game_cap={skipped_by_game_cap} final_max_game={final_max_game}")
            
            # Clean rejection_reason in selected rows
            if "selection_rejection_reason" in selected_df.columns:
                selected_df["selection_rejection_reason"] = ""
            
            # Log concentration summary
            self.logger.info(
                "elite_concentration_summary team_cap=%d game_cap=%d selected=%d skipped_team=%d skipped_game=%d max_team=%d max_game=%d",
                elite_team_cap, elite_game_cap, len(selected_df), skipped_by_team_cap, skipped_by_game_cap,
                final_max_team, final_max_game
            )
            
            # Log count by market_type AFTER filtering
            if "market_type" in selected_df.columns:
                count_after = selected_df["market_type"].value_counts().to_dict()
                self.logger.info("count_by_market_type_after_filter %s", count_after)
            
            # Assertion: game cap must be enforced on FINAL selection
            if final_max_game > elite_game_cap:
                self.logger.error(
                    "[CAP_ASSERT] Game cap violated in final selection: max_game_exposure=%d > cap=%d",
                    final_max_game, elite_game_cap
                )
                raise RuntimeError(f"Game cap violated: {final_max_game} > {elite_game_cap}")
            
            return selected_df

        def select_top_per_market(df: pd.DataFrame, limit: int) -> pd.DataFrame:
            """Select top candidates per market type."""
            selection_stage_trace["full_market"]["candidate_count_entering_full_market_selection"] = len(df)
            selected_df = select_top_per_market_helper(df, per_market_limit=limit)
            selection_stage_trace["full_market"]["candidate_count_after_final_full_market_selection"] = len(selected_df)
            return selected_df

        # Build operator boards (returns elite_df, full_market_df, construction_trace)
        elite_df, full_market_df, selection_trace = build_operator_boards(
            candidates_df,
            per_market_limit=20,
            select_elite_board=select_elite_board,
            select_top_per_market=select_top_per_market,
        )
        elite_df = annotate_source_identity_conflicts(elite_df, player_identity_summary)
        full_market_df = annotate_source_identity_conflicts(full_market_df, player_identity_summary)
        if "selection" in candidates_df.columns:
            qualified_selection_counts = candidates_df["selection"].fillna("").astype(str).str.strip().str.lower().value_counts().to_dict()
            print(f"[COUNT] qualified_over_rows={int(qualified_selection_counts.get('over', 0))}", flush=True)
            print(f"[COUNT] qualified_under_rows={int(qualified_selection_counts.get('under', 0))}", flush=True)
        if "selection" in full_market_df.columns:
            full_market_selection_counts = full_market_df["selection"].fillna("").astype(str).str.strip().str.lower().value_counts().to_dict()
        else:
            full_market_selection_counts = {}
        print(f"[COUNT] full_market_over_rows={int(full_market_selection_counts.get('over', 0))}", flush=True)
        print(f"[COUNT] full_market_under_rows={int(full_market_selection_counts.get('under', 0))}", flush=True)
        if "selection" in elite_df.columns:
            elite_selection_counts = elite_df["selection"].fillna("").astype(str).str.strip().str.lower().value_counts().to_dict()
        else:
            elite_selection_counts = {}
        print(f"[COUNT] elite_over_rows={int(elite_selection_counts.get('over', 0))}", flush=True)
        print(f"[COUNT] elite_under_rows={int(elite_selection_counts.get('under', 0))}", flush=True)

        # ---- player_points strong-OVER calibration guard diagnostics ---------
        # Count how many player_points OVER picks with edge >= 3.0 exist in the
        # qualified pool (diagnostic only — these rows remain in Full Market but
        # are blocked from Elite/Kelly until projection is recalibrated).
        _pp_over_guard_count = 0
        _pp_over_guard_by_player: dict[str, int] = {}
        _pp_over_guard_by_bucket: dict[str, int] = {"edge_3_to_6": 0, "edge_6_plus": 0}
        if not candidates_df.empty and "market_type" in candidates_df.columns:
            for _, crow in candidates_df.iterrows():
                if str(crow.get("market_type", "")).strip().lower() != "player_points":
                    continue
                if str(crow.get("selection", "")).strip().lower() != "over":
                    continue
                cedge = to_float(crow.get("edge"))
                if cedge is None:
                    cedge = to_float(crow.get("edge_pct"))
                if cedge is None:
                    continue
                if cedge < 3.0:
                    continue
                _pp_over_guard_count += 1
                pname = str(crow.get("player_name", "unknown"))
                _pp_over_guard_by_player[pname] = _pp_over_guard_by_player.get(pname, 0) + 1
                if cedge >= 6.0:
                    _pp_over_guard_by_bucket["edge_6_plus"] += 1
                else:
                    _pp_over_guard_by_bucket["edge_3_to_6"] += 1
        print(f"[COUNT] player_points_strong_over_guard_count={_pp_over_guard_count}", flush=True)
        print(f"[COUNT] player_points_strong_over_guard_by_player={dict(sorted(_pp_over_guard_by_player.items()))}", flush=True)
        print(f"[COUNT] player_points_strong_over_guard_by_edge_bucket={_pp_over_guard_by_bucket}", flush=True)

        selection_trace.setdefault("elite", {}).update(selection_stage_trace["elite"])
        selection_trace.setdefault("full_market", {}).update(selection_stage_trace["full_market"])
        unsupported_active_market_count = int(
            selection_trace.get("full_market", {}).get("unsupported_active_operator_market_count", 0) or 0
        )
        unsupported_active_market_counts = (
            selection_trace.get("full_market", {}).get("unsupported_active_operator_market_counts", {}) or {}
        )
        unsupported_active_market_summary = unsupported_active_operator_market_drop_summary(selection_trace)
        duplicate_betting_identity_summary = duplicate_betting_identity_drop_summary(selection_trace)
        duplicate_betting_identity_drop_count = int(
            duplicate_betting_identity_summary.get("total_rows_dropped", 0) or 0
        )
        duplicate_betting_identity_drop_counts = (
            duplicate_betting_identity_summary.get("counts_by_market_type", {}) or {}
        )
        print(f"[COUNT] unsupported_active_operator_market_count={unsupported_active_market_count}", flush=True)
        print(f"[COUNT] unsupported_active_operator_market_counts={unsupported_active_market_counts}", flush=True)
        print(f"[COUNT] duplicate_betting_identity_drop_count={duplicate_betting_identity_drop_count}", flush=True)
        print(f"[COUNT] duplicate_betting_identity_drop_counts={duplicate_betting_identity_drop_counts}", flush=True)
        self.logger.info("board_selection_trace %s", selection_trace)
        if selection_trace.get("selection_rejection_reasons"):
            self.logger.info(
                "selection_rejection_reasons %s",
                selection_trace["selection_rejection_reasons"],
            )
        qualified_unselected = selection_trace.get("qualified_but_not_selected_rows") or []
        if qualified_unselected:
            # Emit as structured stdout [COUNT] lines instead of a stderr
            # warning. These rows are a normal informational summary of
            # candidates that passed quality filters but lost the final
            # board cap; surfacing them via logger.warning routed them to
            # stderr and PowerShell rendered them as a NativeCommandError.
            sample_keys = (
                "player_name",
                "market_type",
                "selection",
                "edge",
                "confidence",
                "selection_score",
                "selection_rejection_reason",
            )
            compact_sample = []
            for row in qualified_unselected[:3]:
                if isinstance(row, dict):
                    compact_sample.append({k: row.get(k) for k in sample_keys if k in row})
                else:
                    compact_sample.append(row)
            print(
                f"[COUNT] qualified_but_not_selected_rows={len(qualified_unselected)}",
                flush=True,
            )
            print(
                f"[COUNT] qualified_but_not_selected_sample={compact_sample}",
                flush=True,
            )
            # Keep the full payload available for offline debugging via
            # the standard info log (file/handler-bound, not stderr).
            self.logger.info(
                "qualified_but_not_selected_rows_full_sample count=%d rows=%s",
                len(qualified_unselected),
                qualified_unselected,
            )

        # Assign lanes for diagnostic purposes
        lane_summary = assign_candidate_lanes(candidates_df, elite_df, full_market_df)
        self.logger.info("lane_assignment %s", lane_summary)

        # Extract boards
        result.selected_props = elite_df  # Elite board is the selected props
        result.elite_props = elite_df
        result.full_market_props = full_market_df
        # Stat-only board would be non-live candidates (if needed)
        result.stat_only_props = candidates_df[
            ~candidates_df.index.isin(elite_df.index) &
            ~candidates_df.index.isin(full_market_df.index)
        ].copy() if not candidates_df.empty else pd.DataFrame()
        result.market_lines = odds
        result.merged_market_props = candidates_df
        result.injury_context = injury_context  # Pass through for downstream use

        # Build summary with board analytics
        result.summary = self._build_summary(
            games=games,
            odds=odds,
            candidates=candidates_df,
            elite_df=elite_df,
            selected_df=elite_df,  # Elite is the selected props
            injury_context=injury_context,
        )
        result.summary["active_operator_markets"] = sorted(ACTIVE_OPERATOR_MARKETS)
        result.summary["unsupported_active_operator_market_count"] = unsupported_active_market_count
        result.summary["unsupported_active_operator_market_counts"] = unsupported_active_market_counts
        result.summary["unsupported_active_operator_markets"] = unsupported_active_market_summary
        result.summary["duplicate_betting_identity_drop_count"] = duplicate_betting_identity_drop_count
        result.summary["duplicate_betting_identity_drop_counts_by_market_type"] = (
            dict(duplicate_betting_identity_drop_counts)
        )
        result.summary["duplicate_betting_identity_drop_groups"] = (
            duplicate_betting_identity_summary.get("groups", []) or []
        )
        result.summary["duplicate_betting_identity"] = duplicate_betting_identity_summary
        result.summary["identity_quarantine_count"] = identity_quarantine_count
        result.summary["identity_quarantine_reason_counts"] = identity_quarantine_counts
        result.summary["identity_quarantine"] = {
            "rejection_reason": IDENTITY_QUARANTINE_REJECTION_REASON,
            "total_rows_dropped": identity_quarantine_count,
            "counts_by_reason": identity_quarantine_counts,
        }
        source_identity_exposure = source_identity_conflict_exposure_summary(
            source_identity_payload=player_identity_summary,
            full_market_df=full_market_df,
            elite_df=elite_df,
        )
        result.summary["source_identity_conflict"] = source_identity_exposure
        result.summary.update(source_identity_exposure)
        self._attach_player_identity_summary(
            result.summary,
            player_identity_summary,
            candidates_df,
            candidate_identity_counts=candidate_identity_counts,
        )

        # Set summary on telemetry BEFORE writing audit files
        elite_telemetry.set_summary(result.summary)

        # Write elite telemetry audit files
        try:
            out_dir = Path(self.config.out_dir) / "runtime" / "operator"
            csv_path = elite_telemetry.write_csv(out_dir)
            json_path = elite_telemetry.write_summary_json(out_dir)
            self.logger.info("elite_telemetry_csv %s", csv_path)
            self.logger.info("elite_telemetry_json %s", json_path)
            self.logger.info("elite_telemetry_totals %s", dict(elite_telemetry.totals))
        except Exception as e:
            self.logger.warning("elite_telemetry_write_failed %s", str(e))
        try:
            self._write_market_availability_audit(
                prediction_date=self.config.prediction_date,
                odds=odds,
                candidates_df=candidates_df,
                elite_df=elite_df,
                rejected_rows=rejected if isinstance(rejected, list) else [],
            )
            self._write_market_performance_readiness(
                prediction_date=self.config.prediction_date,
                full_market_df=full_market_df,
                rejected_rows=rejected if isinstance(rejected, list) else [],
            )
        except Exception as e:
            self.logger.warning("market_availability_audit_write_failed %s", str(e))

        self.logger.info("prediction_complete summary_keys=%s", list(result.summary.keys()))
        return result

    def _build_candidate_universe(
        self,
        games: pd.DataFrame,
        odds: pd.DataFrame,
        player_baselines: pd.DataFrame,
        team_baselines: pd.DataFrame | None,
        injury_context: dict[str, Any],
        is_player_inactive_fn: Callable[[str], bool],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        """Build the universe of betting candidates."""
        # Input size diagnostics
        self.logger.info(
            "candidate_universe_input "
            "games=%d odds=%d player_baselines=%d injury_teams=%d",
            len(games),
            len(odds),
            len(player_baselines),
            len(injury_context.get("teams", {})),
        )

        # ---- Combo-projection feasibility audit (diagnostic only) ----------
        # These multi-stat markets (points+rebounds, points+assists,
        # rebounds+assists, points+rebounds+assists) are NOT yet wired into
        # candidate selection. Here we walk today's baseline universe once
        # and count how many players we *could* project for each combo
        # market under the current rules:
        #   supported       : every component stat present AND min_avg >= 24
        #   partial_support : every component stat present BUT min_avg < 24
        #   unsupported     : at least one component stat missing
        # The aggregate counts are emitted as [COUNT] lines so we can size
        # the multi-market expansion before turning it on.
        try:
            self._emit_combo_projection_audit(player_baselines)
        except Exception as exc:  # never let diagnostics break the pipeline
            self.logger.warning("combo_projection_audit_failed error=%s", exc)

        def is_inactive(player_name: str) -> bool:
            if is_player_inactive_fn:
                return is_player_inactive_fn(player_name)
            return False

        # ---- Game status / slate-lock gate: build lookup from games data ----
        # Map team abbreviation -> game status info for quick lookup per candidate
        _game_info_by_team: dict[str, dict[str, Any]] = {}
        _game_info_by_id: dict[str, dict[str, Any]] = {}
        _games_with_status_count = 0
        _games_with_datetime_count = 0
        if not games.empty and {"home_team_abbr", "visitor_team_abbr"}.issubset(games.columns):
            for _, g in games.iterrows():
                home_team_abbr = str(g.get("home_team_abbr", "")).strip().upper()
                visitor_team_abbr = str(g.get("visitor_team_abbr", "")).strip().upper()
                game_id_value = g.get("game_id") if g.get("game_id") is not None else g.get("id")
                # Support both normalized (status/date) and renamed (game_status/game_date) columns
                raw_status = g.get("game_status") or g.get("status") or ""
                gstatus = str(raw_status).strip().lower()
                if gstatus in ("", "nan", "none", "null"):
                    gstatus = "unknown"
                game_datetime_val = g.get("game_datetime") or g.get("datetime") or g.get("game_date") or g.get("date") or ""
                game_date_val = g.get("game_date") or g.get("date") or game_datetime_val
                game_status_bucket = str(g.get("game_status_bucket", "") or "").strip().lower()
                if gstatus and gstatus != "unknown":
                    _games_with_status_count += 1
                if game_datetime_val:
                    _games_with_datetime_count += 1
                game_payload = {
                    "game_id": game_id_value,
                    "game_home_team_abbr": home_team_abbr,
                    "game_away_team_abbr": visitor_team_abbr,
                    "game_status": gstatus,
                    "game_datetime": game_datetime_val,
                    "game_date": game_date_val,
                    "game_status_bucket": game_status_bucket,
                }
                _game_info_by_team[home_team_abbr] = game_payload
                _game_info_by_team[visitor_team_abbr] = game_payload
                if game_id_value is not None and str(game_id_value).strip():
                    try:
                        game_id_key = str(int(float(str(game_id_value).strip())))
                    except (TypeError, ValueError):
                        game_id_key = str(game_id_value).strip()
                    _game_info_by_id[game_id_key] = game_payload
        self.logger.info(
            "games_enrichment games=%s with_status=%s with_datetime=%s mapped_teams=%s",
            len(games),
            _games_with_status_count,
            _games_with_datetime_count,
            len(_game_info_by_team),
        )
        player_identity_resolver = build_canonical_player_identity_resolver(
            prediction_date=self.config.prediction_date,
            player_baselines=player_baselines,
            odds=odds,
            games=games,
        )

        # Fully modeled markets (real projections)
        FULLY_MODELED_MARKETS = {
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_points_rebounds",
            "player_points_assists",
            "player_rebounds_assists",
            "player_points_rebounds_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
        }
        # Markets with placeholder/incomplete projections
        PARTIALLY_MODELED_MARKETS = {
            "player_rebounds",
            "player_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
        }

        def _get_projection_support_status(
            market_type: str, projection: float | None
        ) -> tuple[str, str | None]:
            """
            Determine projection support status and rejection reason if invalid.
            Returns: (status, rejection_reason)
            """
            normalized = normalize_market_alias(market_type) or market_type

            # Check if market is fully modeled
            if normalized not in FULLY_MODELED_MARKETS:
                if normalized in PARTIALLY_MODELED_MARKETS:
                    return "unsupported_market", "market_not_supported_by_projection"
                if normalized in {"moneyline", "team_total"}:
                    return "unsupported_market", "market_not_supported_by_projection"
                return "unsupported_market", "unsupported_market_type"

            # For fully modeled markets, check projection validity
            if projection is None:
                return "projection_missing", "invalid_projection_output"
            if projection == 0.0:
                return "zero_projection_placeholder", "invalid_projection_output"

            return "modeled", None

        def build_candidate_row(
            player_row: pd.Series,
            market: str,
            market_rows: pd.DataFrame,
            partial_fill: bool = False,
        ) -> dict[str, Any] | None:
            """Build a candidate row with all scoring fields."""
            normalized_market = normalize_market_alias(market) or market

            # Get the first market row for line/odds
            market_row = market_rows.iloc[0] if not market_rows.empty else pd.Series()
            raw_prop_type = str(market_row.get("raw_prop_type", "") or "") if not market_row.empty else ""
            raw_market_type = str(market_row.get("raw_market_type", market_row.get("market.type", "")) or "") if not market_row.empty else ""

            # Live market / synthetic line flags for downstream lane tagging
            synthetic_line = bool(market_row.get("synthetic_line", False)) if not market_row.empty else True
            is_live_market = not market_row.empty and not synthetic_line

            # Get base projection
            if normalized_market in self.COMBO_PROJECTION_MARKETS:
                projection, combo_support_status = self._compute_combo_projection(
                    player_row,
                    normalized_market,
                )
            else:
                projection = self._compute_projection(
                    player_row=player_row,
                    market_type=normalized_market,
                )
                combo_support_status = ""

            # Get base confidence
            confidence = self._compute_confidence(
                player_row=player_row,
                market_type=normalized_market,
            )

            # Check projection support status
            support_status, reject_reason = _get_projection_support_status(
                normalized_market, projection
            )
            if normalized_market in self.COMBO_PROJECTION_MARKETS:
                if combo_support_status != "supported":
                    support_status = combo_support_status or "unsupported_market"
                    reject_reason = "unsupported_projection_market"
                else:
                    support_status = "modeled"

            # If unsupported market, return None to trigger rejection
            if reject_reason:
                return None  # Will be rejected by score_player_markets with explicit reason

            # Apply injury context if available
            injury_metadata: dict[str, Any] = {}
            if injury_context and self.config.enable_injury_context:
                team_abbr = str(player_row.get("team_abbr", ""))
                # Determine opponent from team context or use placeholder
                opp_abbr = "OPP"  # Simplified for package pipeline

                if team_abbr:
                    projection, confidence, injury_metadata = self.injury_engine.apply_context(
                        player_row=player_row,
                        team_abbr=team_abbr,
                        opp_abbr=opp_abbr,
                        market_type=normalized_market,
                        projection=projection,
                        confidence=confidence,
                        injury_context=injury_context,
                    )

            # Get line and odds
            line = _nan_safe_to_float(market_row.get("line"), projection)
            pre_rejection_reason = ""
            if market_rows.empty and normalized_market in {"moneyline", "team_total"}:
                pre_rejection_reason = "market_missing_line"
            if line is None:
                line = projection
                pre_rejection_reason = pre_rejection_reason or "market_missing_line"
            raw_odds = _nan_safe_to_float(market_row.get("odds"), float(self.config.synthetic_odds_default))
            if raw_odds in (None, 0.0):
                raw_odds = float(self.config.synthetic_odds_default)
                if not partial_fill:
                    pre_rejection_reason = pre_rejection_reason or "market_missing_odds"
            odds = int(raw_odds if raw_odds is not None else self.config.synthetic_odds_default)

            # Compute edge
            edge = projection - line
            edge_pct = (edge / line) if line else 0.0
            selection = str(market_row.get("selection", "")) if not market_row.empty else ""
            selection_normalized = selection.strip().lower()
            side_edge = -edge if selection_normalized == "under" else edge
            side_edge_pct = (side_edge / line) if line else 0.0

            # ---- Game status / slate-lock gate: attach game status to candidate ----
            player_team = str(player_row.get("team_abbr", "")).strip().upper()
            market_game_id = market_row.get("game_id") if not market_row.empty else ""
            try:
                market_game_id_key = str(int(float(str(market_game_id).strip()))) if str(market_game_id).strip() else ""
            except (TypeError, ValueError):
                market_game_id_key = str(market_game_id).strip()
            _game_info = _game_info_by_id.get(market_game_id_key, {}) or _game_info_by_team.get(player_team, {})

            # ---- Player Points Recalibration (feature-flagged) ----
            recal_mode = get_recalibration_mode()
            recal_fields: dict[str, Any] = {
                "recalibrated_projection": None,
                "recalibrated_edge": None,
                "recalibration_components_json": None,
                "recalibration_selected": None,
                "recalibration_rejection_reason": None,
                "recalibration_mode": recal_mode,
            }
            if normalized_market == "player_points" and recal_mode in {RecalibrationMode.SHADOW, RecalibrationMode.ENABLED}:
                # Build row for recalibration
                recal_row = {
                    "model_projection": projection,
                    "sportsbook_line": line,
                    "selection": selection,
                    "minutes_avg": _nan_safe_to_float(player_row.get("min_avg")),
                    "min_avg": _nan_safe_to_float(player_row.get("min_avg")),
                    "player_points_recent_form_ratio": _nan_safe_to_float(player_row.get("player_points_recent_form_ratio")),
                    "opponent_def_rating": _nan_safe_to_float(player_row.get("opponent_def_rating")),
                    "matchup_pace": _nan_safe_to_float(player_row.get("matchup_pace")),
                    "postseason": _game_info.get("postseason", ""),
                    "player_profile_bucket": str(player_row.get("player_profile_bucket", "")),
                }
                recal_result = recalibrate_player_points(recal_row)
                recal_fields = {
                    "recalibrated_projection": recal_result["recalibrated_projection"],
                    "recalibrated_edge": recal_result["recalibrated_edge"],
                    "recalibration_components_json": recal_result["recalibration_components_json"],
                    "recalibration_selected": recal_result["recalibration_selected"],
                    "recalibration_rejection_reason": recal_result["recalibration_rejection_reason"],
                    "recalibration_mode": recal_mode,
                }
                # In enabled mode, override projection/edge if recalibration selects the pick
                if recal_mode == RecalibrationMode.ENABLED and recal_result["recalibration_selected"]:
                    projection = recal_result["recalibrated_projection"]
                    edge = recal_result["recalibrated_edge"]
                    edge_pct = (edge / line) if line else 0.0
                    side_edge = -edge if selection_normalized == "under" else edge
                    side_edge_pct = (side_edge / line) if line else 0.0

            # Score the candidate
            scoring_input = {
                "market_type": normalized_market,
                "projection": projection,
                "line": line,
                "edge": edge,
                "edge_pct": edge_pct,
                "side_edge": side_edge,
                "side_edge_pct": side_edge_pct,
                "edge_abs": max(float(side_edge), 0.0),
                "confidence": confidence,
                "player_name": str(player_row.get("player_name", "")),
                "team": str(player_row.get("team_abbr", "")),
            }
            scoring_result = self.scoring_policy.apply_scoring_metadata(scoring_input)

            # Compute Kelly stake fraction for bet sizing
            stake_fraction = compute_kelly_fraction(
                edge=edge_pct,
                odds=float(odds) if odds else 1.91,
                confidence=float(confidence) if confidence else 0.0,
            )
            recommended_bet = round(DEFAULT_BANKROLL * stake_fraction, 2)

            # Determine qualification_reason based on market source
            # For live markets, set qualification_reason to pass the live gate filter
            if is_live_market and not synthetic_line:
                qualification_reason = "live_market_qualified"
            elif is_live_market and synthetic_line:
                qualification_reason = "live_market_fill"
            else:
                qualification_reason = "stat_only_qualified"

            # Compute Kelly stake fraction for bet sizing
            stake_fraction = compute_kelly_fraction(
                edge=edge_pct,
                odds=float(odds) if odds else 1.91,
                confidence=float(confidence) if confidence else 0.0,
            )
            recommended_bet = round(DEFAULT_BANKROLL * stake_fraction, 2)

            return {
                "prediction_date": self.config.prediction_date,
                "player_name": str(player_row.get("player_name", "")),
                "entity_name": str(player_row.get("player_name", "")),
                "player_id": player_row.get("player_id"),
                "team": str(player_row.get("team_abbr", "")),
                "team_abbr": str(player_row.get("team_abbr", "")),
                "baseline_team_abbr": str(player_row.get("baseline_team_abbr", player_row.get("team_abbr", "")) or "").strip().upper(),
                "provider_team_abbr": str(market_row.get("provider_team_abbr", market_row.get("_team_abbr", "")) or "").strip().upper() if not market_row.empty else "",
                "odds_team_abbr": str(market_row.get("odds_team_abbr", market_row.get("_team_abbr", "")) or "").strip().upper() if not market_row.empty else "",
                "resolved_team_abbr": str(market_row.get("resolved_team_abbr", "") or "").strip().upper() if not market_row.empty else "",
                "identity_source_team_abbr": str(
                    market_row.get(
                        "identity_source_team_abbr",
                        market_row.get("resolved_team_abbr", ""),
                    )
                    or ""
                ).strip().upper() if not market_row.empty else "",
                "game_home_team_abbr": _game_info.get("game_home_team_abbr", ""),
                "game_away_team_abbr": _game_info.get("game_away_team_abbr", ""),
                "market_type": normalized_market,
                "raw_prop_type": raw_prop_type,
                "raw_market_type": raw_market_type,
                "selection": str(market_row.get("selection", "")) if not market_row.empty else "",
                "sportsbook_line": line,
                "line": line,
                "model_projection": projection,
                "projection": projection,
                "projection_support_status": support_status,
                "minutes_avg": _nan_safe_to_float(player_row.get("min_avg")),
                "edge": edge,
                "edge_pct": edge_pct,
                "side_edge": side_edge,
                "side_edge_pct": side_edge_pct,
                "confidence": confidence,
                "quality_score": scoring_result.get("quality_score", 0.0),
                "selection_score": scoring_result.get("selection_score", 0.0),
                "is_elite": scoring_result.get("is_elite", False),
                "odds": int(odds),
                "is_live_market": is_live_market,
                "synthetic_line": synthetic_line,
                "qualification_reason": qualification_reason,
                "line_source": "live_market" if is_live_market and not synthetic_line else "synthetic",
                "source_lane": "live_market_candidate" if is_live_market and not synthetic_line else "partial_fill_candidate",
                "pre_rejection_reason": pre_rejection_reason,
                "stake_fraction": stake_fraction,
                "recommended_bet": recommended_bet,
                # Game status fields for slate-lock gate diagnostics
                "game_status": _game_info.get("game_status", ""),
                "game_status_bucket": _game_info.get("game_status_bucket", ""),
                "game_date": _game_info.get("game_date", ""),
                "game_datetime": _game_info.get("game_datetime", ""),
                "postseason": _game_info.get("postseason", ""),
                # Odds freshness field
                "odds_updated_at": str(market_row.get("updated_at", "")) if not market_row.empty else "",
                # Recalibration fields (shadow/enabled modes)
                **recal_fields,
                **injury_metadata,
            }

        def reject_candidate(
            player_row: pd.Series,
            market: str | None,
            reason: str,
            team: str | None = None,
            projection_support_status: str = "",
        ) -> dict[str, Any]:
            return {
                "prediction_date": self.config.prediction_date,
                "player_name": str(player_row.get("player_name", "")),
                "market_type": market or "",
                "rejection_reason": reason,
                "projection_support_status": projection_support_status,
                "team": team or str(player_row.get("team", "")),
            }

        def score_candidate_fn(
            candidate_row: dict[str, Any],
            player_row: pd.Series | None = None,
            market: str | None = None,
            market_rows: pd.DataFrame | None = None,
            partial_fill: bool = False,
        ) -> dict[str, Any] | None:
            # Apply thresholds
            edge = _nan_safe_to_float(candidate_row.get("edge"), 0.0) or 0.0
            side_edge = _nan_safe_to_float(candidate_row.get("side_edge"), edge)
            if side_edge is None:
                side_edge = edge
            confidence = _nan_safe_to_float(candidate_row.get("confidence"), 0.0) or 0.0
            normalized_market = normalize_market_alias(market) or str(market or candidate_row.get("market_type", "")).strip()
            gate = self.FULL_MARKET_READINESS_GATES.get(normalized_market)
            if gate:
                minutes_avg = _nan_safe_to_float(
                    candidate_row.get("minutes_avg")
                    if candidate_row.get("minutes_avg") is not None
                    else (player_row.get("min_avg") if player_row is not None else None),
                    0.0,
                ) or 0.0
                if minutes_avg < gate["min_minutes"]:
                    candidate_row["pre_rejection_reason"] = (
                        f"market_gate_minutes_lt_{gate['min_minutes']:g}"
                    )
                    return None
                if confidence < gate["min_confidence"]:
                    candidate_row["pre_rejection_reason"] = (
                        f"market_gate_confidence_lt_{gate['min_confidence']:.2f}"
                    )
                    return None

            if side_edge <= 0:
                candidate_row["pre_rejection_reason"] = REJECT_NEGATIVE_EDGE_DIRECTION
                return None
            if side_edge < self.config.min_edge:
                return None
            if confidence < self.config.min_confidence:
                return None

            # ---- Recalibration rejection (enabled mode only) ----
            # If recalibration rejected this player_points pick, exclude it
            if normalized_market == "player_points" and get_recalibration_mode() == RecalibrationMode.ENABLED:
                if not candidate_row.get("recalibration_selected", True):
                    reason = candidate_row.get("recalibration_rejection_reason", "recalibration_rejected")
                    candidate_row["pre_rejection_reason"] = reason
                    return None

            return candidate_row

        # Use the data module's candidate scoring
        accepted, rejected = score_player_markets(
            players_df=player_baselines,
            odds_df=odds,
            is_player_inactive=is_inactive,
            build_candidate_row=build_candidate_row,
            score_candidate_fn=score_candidate_fn,
            reject_candidate_fn=reject_candidate,
            allow_partial_fill=self.config.enable_partial_fill,
            player_lookup_size=self.config.player_lookup_size,
        )

        # Team-market candidates (moneyline, team totals) if offered by provider.
        team_rows = odds.copy() if isinstance(odds, pd.DataFrame) else pd.DataFrame()
        if not team_rows.empty:
            market_source_col = "market_type" if "market_type" in team_rows.columns else ("market" if "market" in team_rows.columns else None)
            if market_source_col:
                team_rows["_normalized_market"] = team_rows[market_source_col].map(lambda v: normalize_market_alias(v) or str(v).strip().lower())
                team_rows = team_rows[team_rows["_normalized_market"].isin({"moneyline", "team_total"})].copy()
            else:
                team_rows = pd.DataFrame()
        team_baselines_df = team_baselines.copy() if isinstance(team_baselines, pd.DataFrame) else pd.DataFrame()
        team_lookup: dict[str, pd.Series] = {}
        if not team_baselines_df.empty and "team_abbr" in team_baselines_df.columns:
            for _, row in team_baselines_df.iterrows():
                team_lookup[str(row.get("team_abbr", "")).strip().upper()] = row
        for _, row in team_rows.iterrows():
            market = str(row.get("_normalized_market", "")).strip().lower()
            team = str(row.get("team", row.get("team_abbr", ""))).strip().upper()
            opp = str(row.get("opponent", "")).strip().upper()
            odds_value = _nan_safe_to_float(row.get("odds"))
            line_value = _nan_safe_to_float(row.get("line"))
            if odds_value in (None, 0.0):
                rejected.append(
                    reject_candidate(
                        player_row=pd.Series({"player_name": "", "team": team}),
                        market=market,
                        reason="market_missing_odds",
                        team=team,
                    )
                )
                continue
            if market == "team_total" and line_value is None:
                rejected.append(
                    reject_candidate(
                        player_row=pd.Series({"player_name": "", "team": team}),
                        market=market,
                        reason="market_missing_line",
                        team=team,
                    )
                )
                continue
            team_base = team_lookup.get(team)
            opp_base = team_lookup.get(opp)
            if market == "team_total":
                projection = _nan_safe_to_float(team_base.get("team_pts_avg") if team_base is not None else None)
                if projection is None:
                    rejected.append(
                        reject_candidate(
                            player_row=pd.Series({"player_name": "", "team": team}),
                            market=market,
                            reason="projection_missing_for_market",
                            team=team,
                        )
                    )
                    continue
                selection = str(row.get("selection", "over")).strip().lower() or "over"
                edge = float(projection - float(line_value))
                confidence = 0.60
                sportsbook_line = float(line_value)
            else:
                if team_base is None or opp_base is None:
                    rejected.append(
                        reject_candidate(
                            player_row=pd.Series({"player_name": "", "team": team}),
                            market=market,
                            reason="market_not_supported_by_projection",
                            team=team,
                        )
                    )
                    continue
                team_pts = _nan_safe_to_float(team_base.get("team_pts_avg"), 0.0) or 0.0
                opp_pts = _nan_safe_to_float(opp_base.get("team_pts_avg"), 0.0) or 0.0
                win_prob_projection = min(max(0.5 + ((team_pts - opp_pts) / 40.0), 0.05), 0.95)
                implied_prob = 100.0 / (float(odds_value) + 100.0) if float(odds_value) > 0 else abs(float(odds_value)) / (abs(float(odds_value)) + 100.0)
                projection = float(win_prob_projection)
                sportsbook_line = float(implied_prob)
                edge = float(projection - sportsbook_line)
                selection = str(row.get("selection", team)).strip().lower() or team.lower()
                confidence = 0.58
            # Attach game status to team-market scoring input
            _team_game_info = _game_info_by_team.get(team, {})
            scoring_input = {
                "market_type": market,
                "projection": projection,
                "line": sportsbook_line,
                "sportsbook_line": sportsbook_line,
                "edge": edge,
                "edge_abs": abs(edge),
                "confidence": confidence,
                "player_name": team,
                "team": team,
                "minutes_avg": 0.0,
                "minutes_recent": 0.0,
                "is_live_market": bool(row.get("is_live", True)),
                "synthetic_line": bool(row.get("synthetic", False)),
                "odds": int(odds_value),
                "selection": selection,
                # Game status fields for slate-lock gate diagnostics
                "game_status": _team_game_info.get("game_status", ""),
                "game_status_bucket": _team_game_info.get("game_status_bucket", ""),
                "game_date": _team_game_info.get("game_date", ""),
                "postseason": _team_game_info.get("postseason", ""),
                # Odds freshness field
                "odds_updated_at": str(row.get("updated_at", "")).strip(),
            }
            scoring_result = self.scoring_policy.apply_scoring_metadata(scoring_input)
            candidate = {
                "prediction_date": self.config.prediction_date,
                "player_name": team,
                "entity_name": team,
                "player_id": "",
                "team": team,
                "team_abbr": team,
                "baseline_team_abbr": team,
                "provider_team_abbr": str(row.get("provider_team_abbr", row.get("team_abbr", row.get("team", ""))) or "").strip().upper(),
                "odds_team_abbr": str(row.get("odds_team_abbr", row.get("team_abbr", row.get("team", ""))) or "").strip().upper(),
                "resolved_team_abbr": str(row.get("resolved_team_abbr", "") or "").strip().upper(),
                "identity_source_team_abbr": str(row.get("identity_source_team_abbr", row.get("resolved_team_abbr", "")) or "").strip().upper(),
                "game_home_team_abbr": _team_game_info.get("game_home_team_abbr", ""),
                "game_away_team_abbr": _team_game_info.get("game_away_team_abbr", ""),
                "opponent": opp,
                "market_type": market,
                "selection": selection,
                "sportsbook_line": sportsbook_line,
                "model_projection": projection,
                "projection_support_status": "modeled",
                "edge": edge,
                "edge_pct": edge / sportsbook_line if sportsbook_line else 0.0,
                "confidence": confidence,
                "quality_score": scoring_result.get("quality_score", 0.0),
                "selection_score": scoring_result.get("selection_score", 0.0),
                "odds": int(odds_value),
                "is_elite": scoring_result.get("is_elite", False),
                "qualification_reason": "team_market_modeled",
                "is_live_market": bool(row.get("is_live", True)),
                "synthetic_line": bool(row.get("synthetic", False)),
                "line_source": "live_market",
                "source_lane": "live_market_candidate",
                "pre_rejection_reason": "",
                # Game status fields for slate-lock gate diagnostics
                "game_status": _team_game_info.get("game_status", ""),
                "game_status_bucket": _team_game_info.get("game_status_bucket", ""),
                "game_date": _team_game_info.get("game_date", ""),
                "postseason": _team_game_info.get("postseason", ""),
                # Odds freshness field
                "odds_updated_at": str(row.get("updated_at", "")).strip(),
            }
            if abs(edge) < self.config.min_edge:
                rejected.append(
                    reject_candidate(
                        player_row=pd.Series({"player_name": team, "team": team}),
                        market=market,
                        reason="market_supported_but_failed_quality",
                        team=team,
                    )
                )
            elif confidence < self.config.min_confidence:
                rejected.append(
                    reject_candidate(
                        player_row=pd.Series({"player_name": team, "team": team}),
                        market=market,
                        reason="market_supported_but_failed_confidence",
                        team=team,
                    )
                )
            else:
                accepted.append(candidate)

        # Stage-level diagnostics: rejection breakdown
        if rejected:
            rejection_counts: dict[str, int] = {}
            rejection_details: dict[str, list] = {"low_edge": [], "low_confidence": []}

            for r in rejected:
                reason = r.get("rejection_reason", "unknown")
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

                # Track edge/confidence values for debugging
                if reason == "low_edge":
                    rejection_details["low_edge"].append(r.get("edge", 0))
                elif reason == "low_confidence":
                    rejection_details["low_confidence"].append(r.get("confidence", 0))

            self.logger.info("rejection_breakdown %s", rejection_counts)

            # Identify top 3 rejection causes
            top_rejections = sorted(rejection_counts.items(), key=lambda x: x[1], reverse=True)[:3]
            self.logger.info("top_rejection_causes %s", top_rejections)

            # Log sample values for debugging
            if rejection_details["low_edge"]:
                sample_edges = rejection_details["low_edge"][:5]
                self.logger.info("sample_low_edge_values %s (threshold=%s)", sample_edges, self.config.min_edge)
            if rejection_details["low_confidence"]:
                sample_conf = rejection_details["low_confidence"][:5]
                self.logger.info("sample_low_confidence_values %s (threshold=%s)", sample_conf, self.config.min_confidence)

        accepted = player_identity_resolver.annotate_records(accepted)
        identity_diagnostic_rows = player_identity_resolver.diagnostic_rows()
        if identity_diagnostic_rows:
            rejected.extend(identity_diagnostic_rows)
        player_identity_summary = player_identity_resolver.summary()

        # ---- Game status / slate-lock gate diagnostics ----
        # Count how many candidates would be blocked by game status alone.
        _game_status_excluded_count = 0
        _game_status_reason_counts: dict[str, int] = {}
        for cand in accepted:
            gs_reason = game_status_ineligibility_reason(cand)
            if gs_reason:
                _game_status_excluded_count += 1
                _game_status_reason_counts[gs_reason] = _game_status_reason_counts.get(gs_reason, 0) + 1
        if _game_status_excluded_count > 0:
            print(f"[COUNT] candidates_excluded_by_game_status={_game_status_excluded_count}", flush=True)
            print(f"[COUNT] game_status_exclusion_reasons={dict(sorted(_game_status_reason_counts.items()))}", flush=True)

        # ---- Game status enrichment diagnostics ----
        _candidates_with_game_status = 0
        _candidates_with_game_datetime = 0
        _unknown_future = 0
        _unknown_missing = 0
        _unknown_past = 0
        _scheduled_future_iso_status = 0
        _unknown_missing_status = 0
        _unknown_missing_datetime = 0
        for cand in accepted:
            gs = str(cand.get("game_status", "") or "").strip().lower()
            gs_bucket = str(cand.get("game_status_bucket", "") or "").strip().lower()
            gd = cand.get("game_datetime") or cand.get("game_date") or ""
            if gs:
                _candidates_with_game_status += 1
            if gd:
                _candidates_with_game_datetime += 1
            if gs_bucket == "scheduled_future_iso_status":
                _scheduled_future_iso_status += 1
            if gs_bucket == "unknown_missing_status":
                _unknown_missing_status += 1
            if gs in ("unknown", ""):
                dt = _parse_game_datetime(gd)
                if dt is None:
                    _unknown_missing += 1
                    _unknown_missing_datetime += 1
                elif _is_before_lock_buffer(dt, None, 10):
                    _unknown_future += 1
                else:
                    _unknown_past += 1
        print(f"[COUNT] candidates_with_game_status_count={_candidates_with_game_status}", flush=True)
        print(f"[COUNT] candidates_with_game_datetime_count={_candidates_with_game_datetime}", flush=True)
        print(f"[COUNT] scheduled_future_iso_status_count={_scheduled_future_iso_status}", flush=True)
        print(f"[COUNT] unknown_missing_status_count={_unknown_missing_status}", flush=True)
        print(f"[COUNT] unknown_missing_datetime_count={_unknown_missing_datetime}", flush=True)
        print(f"[COUNT] game_status_unknown_with_future_datetime_count={_unknown_future}", flush=True)
        print(f"[COUNT] game_status_unknown_missing_datetime_count={_unknown_missing}", flush=True)
        print(f"[COUNT] game_status_unknown_past_datetime_count={_unknown_past}", flush=True)

        # ---- Odds freshness gate diagnostics ----
        # Count how many rows have updated_at, how many are stale, and by vendor.
        _odds_rows_total = 0
        _odds_rows_with_updated_at = 0
        _stale_odds_count = 0
        _stale_odds_by_vendor: dict[str, int] = {}
        _stale_odds_max_age_minutes = 0.0
        for cand in accepted:
            _odds_rows_total += 1
            oa = str(cand.get("odds_updated_at", "") or "").strip()
            if oa:
                _odds_rows_with_updated_at += 1
            stale_reason = odds_stale_ineligibility_reason(cand)
            if stale_reason:
                _stale_odds_count += 1
                vendor = str(cand.get("vendor", cand.get("bookmaker", "unknown")) or "unknown").strip()
                _stale_odds_by_vendor[vendor] = _stale_odds_by_vendor.get(vendor, 0) + 1
                # Compute rough age for diagnostics
                try:
                    dt = _parse_game_datetime(oa)
                    if dt:
                        age = datetime.now() - dt
                        age_mins = age.total_seconds() / 60.0
                        if age_mins > _stale_odds_max_age_minutes:
                            _stale_odds_max_age_minutes = age_mins
                except Exception:
                    pass
        print(f"[COUNT] odds_rows_total={_odds_rows_total}", flush=True)
        print(f"[COUNT] odds_rows_with_updated_at={_odds_rows_with_updated_at}", flush=True)
        print(f"[COUNT] stale_odds_count={_stale_odds_count}", flush=True)
        if _stale_odds_by_vendor:
            print(f"[COUNT] stale_odds_by_vendor={dict(sorted(_stale_odds_by_vendor.items()))}", flush=True)
            print(f"[COUNT] stale_odds_max_age_minutes={round(_stale_odds_max_age_minutes, 1)}", flush=True)

        self.logger.info("candidate_universe_output accepted=%d rejected=%d", len(accepted), len(rejected))

        return accepted, rejected, player_identity_summary

    def _compute_projection(
        self,
        player_row: pd.Series,
        market_type: str,
    ) -> float:
        """Compute projection for a player market."""
        # Map market type to stat key
        stat_key = market_type.replace("player_", "")
        stat_map = {
            "points": "pts_avg",
            "rebounds": "reb_avg",
            "assists": "ast_avg",
            "3pt_made": "threes_avg",
            "steals": "stl_avg",
            "blocks": "blk_avg",
            "threes": "threes_avg",
        }

        column = stat_map.get(stat_key, f"{stat_key}_avg")
        projection = _nan_safe_to_float(player_row.get(column))

        if projection is None:
            # Fallback to recent average if available
            recent_col = column.replace("_avg", "_recent")
            projection = _nan_safe_to_float(player_row.get(recent_col), 0.0) or 0.0

        return max(projection, 0.0)

    # ---- Combo (multi-stat) projections -------------------------------------
    # These markets are *not* enabled for candidate selection yet. The helper
    # below is intentionally read-only diagnostic plumbing: it lets us measure
    # how many players in today's baseline universe could be projected for a
    # combo market, without touching PRIMARY_PLAYER_MARKETS or
    # FULLY_MODELED_MARKETS. When we later flip the switch to enable combo
    # markets, the same helper can be invoked from build_candidate_row.
    COMBO_PROJECTION_MARKETS: dict[str, tuple[str, ...]] = {
        "player_points_rebounds": ("pts_avg", "reb_avg"),
        "player_points_assists": ("pts_avg", "ast_avg"),
        "player_rebounds_assists": ("reb_avg", "ast_avg"),
        "player_points_rebounds_assists": ("pts_avg", "reb_avg", "ast_avg"),
    }
    COMBO_MIN_MINUTES: float = 24.0

    def _compute_combo_projection(
        self,
        player_row: pd.Series,
        market_type: str,
    ) -> tuple[float | None, str]:
        """Compute a combo (multi-stat) projection and its support status.

        Returns
        -------
        (projection_value, status)
            projection_value : float | None
                The summed average across the configured component stats.
                None when at least one component is missing.
            status : str
                One of:
                  - ``"supported"``       : every component stat is present
                                            AND ``min_avg`` >= COMBO_MIN_MINUTES.
                  - ``"partial_support"`` : every component stat is present
                                            BUT ``min_avg`` < COMBO_MIN_MINUTES
                                            (still returns a numeric projection).
                  - ``"unsupported"``     : at least one component stat is
                                            missing on the baseline row.

        This helper performs **no** bucketing of the result into the
        candidate selector. It is wired only into the diagnostic [COUNT]
        emission inside `_build_candidate_universe`.
        """
        components = self.COMBO_PROJECTION_MARKETS.get(market_type)
        if not components:
            return None, "unsupported"

        values: list[float] = []
        for col in components:
            raw = _nan_safe_to_float(player_row.get(col))
            if raw is None:
                # Missing component => unsupported, regardless of minutes.
                return None, "unsupported"
            values.append(float(raw))

        projection = sum(values)
        minutes_avg = _nan_safe_to_float(player_row.get("min_avg"), 0.0) or 0.0
        if minutes_avg < self.COMBO_MIN_MINUTES:
            return projection, "partial_support"
        return projection, "supported"

    def _emit_combo_projection_audit(self, player_baselines: pd.DataFrame) -> None:
        """Emit [COUNT] lines summarizing combo-projection feasibility.

        Walks ``player_baselines`` once per configured combo market and
        aggregates the per-status counts. Combo markets are NOT added to
        ``PRIMARY_PLAYER_MARKETS`` or ``FULLY_MODELED_MARKETS`` here -
        this is purely diagnostic plumbing. Emits:

            [COUNT] combo_projection_supported=<N>
            [COUNT] combo_projection_rejected=<N>
            [COUNT] combo_projection_breakdown=[ ... per-market dicts ... ]

        ``rejected`` aggregates ``partial_support`` + ``unsupported``
        because both are currently disqualified from candidate selection
        (any component missing OR minutes_avg < 24).
        """
        if player_baselines is None or player_baselines.empty:
            print("[COUNT] combo_projection_supported=0", flush=True)
            print("[COUNT] combo_projection_rejected=0", flush=True)
            return

        breakdown: list[dict[str, Any]] = []
        total_supported = 0
        total_rejected = 0
        for market_type in self.COMBO_PROJECTION_MARKETS:
            counts = {"supported": 0, "partial_support": 0, "unsupported": 0}
            for _, player_row in player_baselines.iterrows():
                _, status = self._compute_combo_projection(player_row, market_type)
                counts[status] = counts.get(status, 0) + 1
            supported = counts["supported"]
            rejected = counts["partial_support"] + counts["unsupported"]
            total_supported += supported
            total_rejected += rejected
            breakdown.append(
                {
                    "market_type": market_type,
                    "supported": supported,
                    "partial_support": counts["partial_support"],
                    "unsupported": counts["unsupported"],
                }
            )

        print(f"[COUNT] combo_projection_supported={total_supported}", flush=True)
        print(f"[COUNT] combo_projection_rejected={total_rejected}", flush=True)
        print(f"[COUNT] combo_projection_breakdown={breakdown}", flush=True)
        self.logger.info("combo_projection_audit %s", breakdown)

    def _write_market_availability_audit(
        self,
        prediction_date: str,
        odds: pd.DataFrame,
        candidates_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        rejected_rows: list[dict[str, Any]],
    ) -> None:
        diagnostics_dir = Path(self.config.out_dir) / "runtime" / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        raw_market_col = "raw_market_name" if "raw_market_name" in odds.columns else ("market" if "market" in odds.columns else "market_type")
        raw_counts = (
            odds[raw_market_col].astype(str).value_counts().to_dict()
            if not odds.empty and raw_market_col in odds.columns
            else {}
        )
        normalized_series = odds[raw_market_col].map(lambda m: normalize_market_alias(m) or str(m).strip().lower()) if raw_counts else pd.Series(dtype=str)
        normalized_counts = normalized_series.value_counts().to_dict() if not normalized_series.empty else {}
        candidate_counts = candidates_df["market_type"].astype(str).value_counts().to_dict() if not candidates_df.empty and "market_type" in candidates_df.columns else {}
        elite_counts = elite_df["market_type"].astype(str).value_counts().to_dict() if not elite_df.empty and "market_type" in elite_df.columns else {}
        reject_df = pd.DataFrame(rejected_rows) if rejected_rows else pd.DataFrame()
        rejection_counts: dict[str, dict[str, int]] = {}
        if not reject_df.empty and {"market_type", "rejection_reason"}.issubset(reject_df.columns):
            for (market_type, reason), count in reject_df.groupby(["market_type", "rejection_reason"]).size().items():
                rejection_counts.setdefault(str(market_type), {})[str(reason)] = int(count)
        markets = sorted(set(raw_counts.keys()) | set(normalized_counts.keys()) | set(candidate_counts.keys()) | set(elite_counts.keys()) | set(rejection_counts.keys()))
        rows = []
        for market in markets:
            rows.append(
                {
                    "market": market,
                    "count_before_normalization": int(raw_counts.get(market, 0)),
                    "count_after_normalization": int(normalized_counts.get(market, 0)),
                    "count_after_candidate_generation": int(candidate_counts.get(market, 0)),
                    "count_after_scoring": int(candidate_counts.get(market, 0)),
                    "count_after_elite_filtering": int(elite_counts.get(market, 0)),
                    "count_in_final_elite_board": int(elite_counts.get(market, 0)),
                    "rejection_reason_counts": json.dumps(rejection_counts.get(market, {}), sort_keys=True),
                }
            )
        audit_df = pd.DataFrame(rows)
        csv_path = diagnostics_dir / f"market_availability_audit_{prediction_date}.csv"
        json_path = diagnostics_dir / f"market_availability_audit_{prediction_date}.json"
        caller = "courtvision.pipeline.predict_pipeline:PredictionPipeline._write_market_availability_audit"
        log_prediction_artifact_write(
            requested_prediction_date=prediction_date,
            output_path=csv_path,
            caller=caller,
            artifact_label="market_availability_audit_csv",
        )
        audit_df.to_csv(csv_path, index=False)
        log_prediction_artifact_write(
            requested_prediction_date=prediction_date,
            output_path=json_path,
            caller=caller,
            artifact_label="market_availability_audit_json",
        )
        json_path.write_text(
            json.dumps(
                {
                    "prediction_date": prediction_date,
                    "raw_provider_markets": raw_counts,
                    "normalized_markets": normalized_counts,
                    "counts": rows,
                    "rejection_counts_by_market": rejection_counts,
                    "elite_allowed_markets": sorted(self._elite_allowed_markets),
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    def _write_market_performance_readiness(
        self,
        prediction_date: str,
        full_market_df: pd.DataFrame,
        rejected_rows: list[dict[str, Any]],
    ) -> None:
        diagnostics_dir = Path(self.config.out_dir) / "runtime" / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)

        selected_by_market: dict[str, dict[str, Any]] = {}
        if (
            isinstance(full_market_df, pd.DataFrame)
            and not full_market_df.empty
            and "market_type" in full_market_df.columns
        ):
            working = full_market_df.copy()
            for col in ("edge", "confidence", "quality_score"):
                if col in working.columns:
                    working[col] = pd.to_numeric(working[col], errors="coerce")
            for market_type, group in working.groupby("market_type", sort=True):
                payload: dict[str, Any] = {"count": int(len(group))}
                for source_col, output_key in (
                    ("edge", "avg_edge"),
                    ("confidence", "avg_confidence"),
                    ("quality_score", "avg_quality_score"),
                ):
                    if source_col in group.columns:
                        value = group[source_col].mean()
                        payload[output_key] = None if pd.isna(value) else round(float(value), 6)
                    else:
                        payload[output_key] = None
                selected_by_market[str(market_type)] = payload

        reject_df = pd.DataFrame(rejected_rows) if rejected_rows else pd.DataFrame()
        rejection_counts: dict[str, dict[str, int]] = {}
        if not reject_df.empty and {"market_type", "rejection_reason"}.issubset(reject_df.columns):
            for (market_type, reason), count in reject_df.groupby(["market_type", "rejection_reason"]).size().items():
                market_key = str(market_type or "")
                reason_key = str(reason or "unknown")
                rejection_counts.setdefault(market_key, {})[reason_key] = int(count)

        markets = sorted(set(selected_by_market) | set(rejection_counts))
        market_rows = []
        for market_type in markets:
            selected = selected_by_market.get(
                market_type,
                {
                    "count": 0,
                    "avg_edge": None,
                    "avg_confidence": None,
                    "avg_quality_score": None,
                },
            )
            gate = self.FULL_MARKET_READINESS_GATES.get(market_type)
            row = {
                "market_type": market_type,
                "full_market_count": int(selected.get("count", 0)),
                "avg_edge": selected.get("avg_edge"),
                "avg_confidence": selected.get("avg_confidence"),
                "avg_quality_score": selected.get("avg_quality_score"),
                "rejection_count_by_reason": rejection_counts.get(market_type, {}),
            }
            if gate:
                row["min_minutes_gate"] = gate["min_minutes"]
                row["min_confidence_gate"] = gate["min_confidence"]
            market_rows.append(row)

        payload = {
            "prediction_date": prediction_date,
            "scope": "full_market_board_quality_tracking",
            "elite_locked_to": ["player_points"],
            "kelly_locked_to": ["player_points"],
            "market_gates": self.FULL_MARKET_READINESS_GATES,
            "full_market_by_market_type": selected_by_market,
            "rejection_count_by_market_type_reason": rejection_counts,
            "markets": market_rows,
        }
        json_path = diagnostics_dir / f"market_performance_readiness_{prediction_date}.json"
        log_prediction_artifact_write(
            requested_prediction_date=prediction_date,
            output_path=json_path,
            caller="courtvision.pipeline.predict_pipeline:PredictionPipeline._write_market_performance_readiness",
            artifact_label="market_performance_readiness",
        )
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"[COUNT] market_performance_readiness_path={json_path}", flush=True)

    def _compute_confidence(
        self,
        player_row: pd.Series,
        market_type: str,
    ) -> float:
        """Compute confidence for a player market."""
        minutes_avg = _nan_safe_to_float(player_row.get("min_avg"), 0.0) or 0.0

        # Base confidence from minutes
        if minutes_avg >= 30:
            base_confidence = 0.75
        elif minutes_avg >= 25:
            base_confidence = 0.70
        elif minutes_avg >= 20:
            base_confidence = 0.65
        elif minutes_avg >= 15:
            base_confidence = 0.60
        else:
            base_confidence = 0.55

        # Adjust for stat-specific stability
        if market_type == "player_points":
            base_confidence += 0.05
        elif market_type in {"player_steals", "player_blocks"}:
            base_confidence -= 0.05

        return min(max(base_confidence, 0.35), 0.98)

    def _compute_board_analytics(self, elite_df: pd.DataFrame) -> dict[str, Any]:
        """Compute board-level analytics for elite board.
        
        Returns metrics like:
        - elite_count: number of rows
        - overs_count / unders_count: side distribution
        - avg_edge / avg_abs_edge: edge distribution
        - max_team_exposure / max_game_exposure: concentration metrics
        - unique_teams / unique_games: coverage metrics
        """
        if elite_df.empty:
            return {
                "elite_count": 0,
                "overs_count": 0,
                "unders_count": 0,
                "avg_edge": 0.0,
                "avg_abs_edge": 0.0,
                "max_team_exposure": 0,
                "max_game_exposure": 0,
                "unique_teams": 0,
                "unique_games": 0,
            }
        
        # Basic counts
        elite_count = len(elite_df)
        
        # Side distribution
        selection_col = elite_df.get("selection", pd.Series("", index=elite_df.index))
        overs_count = int(selection_col.astype(str).str.lower().eq("over").sum())
        unders_count = int(selection_col.astype(str).str.lower().eq("under").sum())
        
        # Edge distribution
        edge_col = elite_df.get("edge", pd.Series(0.0, index=elite_df.index))
        edge_values = pd.to_numeric(edge_col, errors="coerce").dropna()
        avg_edge = float(edge_values.mean()) if not edge_values.empty else 0.0
        avg_abs_edge = float(edge_values.abs().mean()) if not edge_values.empty else 0.0
        
        # Concentration metrics
        team_col = elite_df.get("team_abbr", elite_df.get("team", pd.Series("", index=elite_df.index)))
        team_counts = team_col.astype(str).str.upper().value_counts()
        max_team_exposure = int(team_counts.max()) if not team_counts.empty else 0
        unique_teams = int(len(team_counts))
        
        # Use normalized game_key for consistent exposure calculation (matches cap enforcement)
        game_keys = [_normalize_game_key(row) for _, row in elite_df.iterrows()]
        game_counts = pd.Series(game_keys).value_counts()
        max_game_exposure = int(game_counts.max()) if not game_counts.empty else 0
        unique_games = int(len(game_counts))
        
        return {
            "elite_count": elite_count,
            "overs_count": overs_count,
            "unders_count": unders_count,
            "avg_edge": round(avg_edge, 4),
            "avg_abs_edge": round(avg_abs_edge, 4),
            "max_team_exposure": max_team_exposure,
            "max_game_exposure": max_game_exposure,
            "unique_teams": unique_teams,
            "unique_games": unique_games,
        }

    def _build_summary(
        self,
        games: pd.DataFrame,
        odds: pd.DataFrame,
        candidates: pd.DataFrame,
        elite_df: pd.DataFrame,
        selected_df: pd.DataFrame,
        injury_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build prediction summary."""
        summary = {
            "prediction_date": self.config.prediction_date,
            "games_count": len(games),
            "odds_count": len(odds),
            "candidate_count": len(candidates),
            "elite_count": len(elite_df),
            "selected_count": len(selected_df),
            "market_quality_status": "live" if not odds.empty else "degraded",
            "injury_context_built": bool(injury_context.get("teams", {}) or injury_context.get("players", {})),
        }

        # Add quality distribution if available
        if "quality_score" in candidates.columns:
            quality_scores = pd.to_numeric(candidates["quality_score"], errors="coerce").dropna()
            if not quality_scores.empty:
                summary["quality_mean"] = round(float(quality_scores.mean()), 2)
                summary["quality_median"] = round(float(quality_scores.median()), 2)
        
        # Add board-level analytics for elite board
        board_analytics = self._compute_board_analytics(elite_df)
        summary["board_analytics"] = board_analytics
        
        # Also add key metrics directly to summary for easy access
        summary.update({
            "elite_overs_count": board_analytics["overs_count"],
            "elite_unders_count": board_analytics["unders_count"],
            "elite_avg_edge": board_analytics["avg_edge"],
            "elite_avg_abs_edge": board_analytics["avg_abs_edge"],
            "elite_max_team_exposure": board_analytics["max_team_exposure"],
            "elite_max_game_exposure": board_analytics["max_game_exposure"],
            "elite_unique_teams": board_analytics["unique_teams"],
            "elite_unique_games": board_analytics["unique_games"],
        })

        return summary

    def _attach_player_identity_summary(
        self,
        summary: dict[str, Any],
        player_identity_summary: dict[str, Any],
        candidates_df: pd.DataFrame,
        *,
        candidate_identity_counts: dict[str, int] | None = None,
    ) -> None:
        """Attach P12 identity diagnostics without changing selection thresholds."""
        source_counts = dict(player_identity_summary.get("counts_by_reason", {}) or {})
        candidate_counts = (
            dict(candidate_identity_counts)
            if candidate_identity_counts is not None
            else player_identity_reason_counts(candidates_df)
        )
        payload = {
            **player_identity_summary,
            "invalid_candidate_count": int(sum(candidate_counts.values())),
            "invalid_candidate_counts_by_reason": candidate_counts,
        }
        summary["player_identity"] = payload
        summary["player_identity_conflict_count"] = int(player_identity_summary.get("conflict_count", 0) or 0)
        summary["player_identity_conflict_reason_counts"] = source_counts
        summary["player_identity_invalid_candidate_count"] = int(sum(candidate_counts.values()))
        summary["player_identity_invalid_candidate_counts_by_reason"] = candidate_counts
        summary.setdefault("source_identity_conflict_count", summary["player_identity_conflict_count"])

    def _build_empty_summary(
        self,
        games: pd.DataFrame,
        odds: pd.DataFrame,
    ) -> dict[str, Any]:
        """Build summary when no candidates found."""
        return {
            "prediction_date": self.config.prediction_date,
            "games_count": len(games),
            "odds_count": len(odds),
            "candidate_count": 0,
            "elite_count": 0,
            "selected_count": 0,
            "market_quality_status": "no_candidates",
            "warning": "No valid candidates found",
        }


# Convenience function for direct usage
def run_prediction_pipeline(
    prediction_date: str,
    games: pd.DataFrame,
    odds: pd.DataFrame,
    player_baselines: pd.DataFrame,
    team_baselines: pd.DataFrame | None = None,
    injuries: pd.DataFrame | None = None,
    config: PredictionConfig | None = None,
    logger: logging.Logger | None = None,
) -> PredictionResult:
    """Run prediction pipeline with provided data.

    This is a convenience function that creates and runs a PredictionPipeline.
    """
    cfg = config or PredictionConfig(prediction_date=prediction_date)
    pipeline = PredictionPipeline(config=cfg, logger=logger)
    return pipeline.run(
        games=games,
        odds=odds,
        player_baselines=player_baselines,
        team_baselines=team_baselines,
        injuries=injuries,
    )
