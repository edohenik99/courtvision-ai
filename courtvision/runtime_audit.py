from __future__ import annotations

from typing import Any, Mapping

import pandas as pd
from courtvision.artifact_guard import log_prediction_artifact_write
from courtvision.calibration.buckets import (
    confidence_band as bucket_confidence_band,
    injury_confidence_delta_value,
    injury_influence_bucket,
    injury_projection_delta_value,
    market_trust_weight_band,
    minutes_bucket as bucket_minutes_bucket,
    odds_bucket as bucket_odds_bucket,
    player_profile_bucket as bucket_player_profile_bucket,
    player_points_line_band,
    projected_minutes,
    quality_band as bucket_quality_band,
    to_float,
)
from courtvision.runtime_selection import (
    elite_points_risk_guard_reason,
    player_points_strong_over_calibration_reason,
)


class BoardAuditPolicy:
    MARKET_TRUST_WEIGHTS: dict[str, float] = {
        "player_points": 1.00,
        "player_rebounds": 0.97,
        "player_assists": 0.97,
        "player_3pt_made": 0.90,
        "player_points_rebounds": 0.94,
        "player_points_assists": 0.94,
        "player_rebounds_assists": 0.93,
        "player_points_rebounds_assists": 0.92,
        "player_blocks_steals": 0.82,
        "player_steals": 0.78,
        "player_blocks": 0.76,
        "team_total": 0.95,
        "moneyline": 0.96,
        "team_projection": 0.86,
        "game_total_projection": 0.85,
    }

    def apply_row_audit(self, row: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        market_type = self._clean_text(enriched.get("market_type", ""))
        rejection_reason = self._clean_text(enriched.get("rejection_reason", ""))
        recommendation = self._clean_text(enriched.get("recommendation", "")).lower()
        qualification_gate_mode = self._clean_text(enriched.get("qualification_gate_mode", ""))

        if not enriched.get("prop_type") and market_type:
            enriched["prop_type"] = market_type

        if not enriched.get("final_selection_source_lane") and qualification_gate_mode:
            enriched["final_selection_source_lane"] = (
                "live_quality_rescue_pass" if qualification_gate_mode == "live_quality_rescue" else "core_pass"
            )

        points_guard_reason = elite_points_risk_guard_reason(enriched)
        enriched["minutes_bucket"] = self.minutes_bucket(enriched)
        enriched["odds_bucket"] = self.odds_bucket(enriched.get("odds"))
        enriched["quality_band"] = self.quality_band(enriched.get("quality_score"))
        enriched["confidence_band"] = self.confidence_band(enriched.get("confidence"))
        enriched["selection_side"] = self.selection_side(enriched.get("selection"))
        enriched["player_profile_bucket"] = self.player_profile_bucket(enriched)
        enriched["player_points_line_band"] = self.player_points_line_band(enriched)
        enriched["injury_influence_bucket"] = self.injury_influence_bucket(enriched)
        enriched["injury_projection_delta"] = round(injury_projection_delta_value(enriched), 4)
        enriched["injury_confidence_delta"] = round(injury_confidence_delta_value(enriched), 4)
        enriched["blocked_by_elite_points_risk_guard"] = bool(points_guard_reason)
        enriched["elite_points_risk_guard_reason"] = points_guard_reason
        strong_over_reason = player_points_strong_over_calibration_reason(enriched)
        enriched["blocked_by_player_points_strong_over_calibration"] = bool(strong_over_reason)
        enriched["player_points_strong_over_calibration_reason"] = strong_over_reason
        enriched["player_points_realism_dampened"] = self._to_bool(enriched.get("player_points_realism_dampened"))
        enriched["player_points_realism_dampener_reason"] = self._clean_text(
            enriched.get("player_points_realism_dampener_reason", "")
        )
        enriched["market_trust_weight"] = round(self.market_trust_weight(enriched.get("market_type")), 4)
        enriched["market_trust_weight_band"] = market_trust_weight_band(enriched.get("market_trust_weight"))
        enriched["rejection_reason"] = rejection_reason

        if recommendation == "rejected" or rejection_reason:
            enriched["qualification_reason"] = ""
        else:
            enriched["qualification_reason"] = self.qualification_reason(enriched)

        return enriched

    def apply_dataframe_audit(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return pd.DataFrame() if not isinstance(df, pd.DataFrame) else df.copy()
        return pd.DataFrame([self.apply_row_audit(self._to_str_dict(row)) for _, row in df.iterrows()])

    def build_diagnostics(
        self,
        prediction_date: str,
        qualified_pool_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
        rejected_df: pd.DataFrame,
        final_board_construction: Mapping[str, Any] | None = None,
        player_points_elite_admission_df: pd.DataFrame | None = None,
    ) -> dict[str, Any]:
        qualified_pool_df = self.apply_dataframe_audit(qualified_pool_df)
        elite_df = self.apply_dataframe_audit(elite_df)
        full_market_df = self.apply_dataframe_audit(full_market_df)
        rejected_df = self.apply_dataframe_audit(rejected_df)
        if isinstance(player_points_elite_admission_df, pd.DataFrame):
            player_points_admission_df = player_points_elite_admission_df.copy()
        else:
            player_points_admission_df = self._extract_player_points_elite_admission_df(final_board_construction)
        qualified_pool_df = self._annotate_player_points_elite_outcome(
            qualified_pool_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
        )
        elite_df = self._annotate_player_points_elite_outcome(
            elite_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
        )
        full_market_df = self._annotate_player_points_elite_outcome(
            full_market_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
        )

        diagnostics = {
            "prediction_date": prediction_date,
            "board_counts": {
                "qualified_pool": int(len(qualified_pool_df)),
                "elite": int(len(elite_df)),
                "full_market": int(len(full_market_df)),
                "rejected": int(len(rejected_df)),
            },
            "qualified_pool": self._distribution_payload(qualified_pool_df),
            "elite_board": self._distribution_payload(elite_df),
            "full_market_board": self._distribution_payload(full_market_df),
            "rejected_by_reason": self._count_records(rejected_df, "rejection_reason"),
            "qualified_by_reason": self._count_records(qualified_pool_df, "qualification_reason"),
            "qualification_rate_by_side": self._qualification_rate_by_side(qualified_pool_df, rejected_df),
            "qualified_by_reason_and_side": self._reason_counts_by_side(qualified_pool_df, "qualification_reason"),
            "rejected_by_reason_and_side": self._reason_counts_by_side(rejected_df, "rejection_reason"),
            "player_points_guard": self._player_points_guard_payload(
                qualified_pool_df=qualified_pool_df,
                elite_df=elite_df,
                full_market_df=full_market_df,
            ),
            "player_points_strong_over_guard": self._player_points_strong_over_guard_payload(
                qualified_pool_df=qualified_pool_df,
            ),
            "player_points_realism": self._player_points_realism_payload(
                qualified_pool_df=qualified_pool_df,
                elite_df=elite_df,
                full_market_df=full_market_df,
            ),
            "player_points_elite_admission": self._player_points_elite_admission_payload(
                player_points_admission_df
            ),
            "final_board_construction": self._final_board_construction_payload(final_board_construction),
        }
        return diagnostics

    def diagnostics_dataframe(self, diagnostics: Mapping[str, Any]) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        distribution_sections = [
            "count_by_prop_type",
            "count_by_minutes_bucket",
            "count_by_odds_bucket",
            "count_by_quality_band",
            "count_by_confidence_band",
            "count_by_side",
            "count_by_player_profile_bucket",
            "count_by_player_points_line_band",
            "count_by_injury_influence_bucket",
            "count_by_qualification_gate_mode",
            "count_by_final_selection_source_lane",
            "count_by_blocked_by_elite_points_risk_guard",
            "count_by_elite_points_risk_guard_reason",
            "count_by_blocked_by_player_points_strong_over_calibration",
            "count_by_player_points_strong_over_calibration_reason",
            "count_by_elite_points_ranking_reason",
            "count_by_player_points_elite_outcome",
            "count_by_player_points_realism_dampener_reason",
        ]
        average_sections = [
            ("avg_quality_by_prop_type", "avg_quality_score"),
            ("avg_quality_by_side", "avg_quality_score"),
            ("avg_confidence_by_side", "avg_confidence"),
            ("avg_injury_projection_delta_by_side", "avg_injury_projection_delta"),
            ("avg_injury_confidence_delta_by_side", "avg_injury_confidence_delta"),
            ("avg_injury_projection_delta_by_line_band", "avg_injury_projection_delta"),
            ("avg_injury_confidence_delta_by_line_band", "avg_injury_confidence_delta"),
            ("avg_injury_projection_delta_by_injury_bucket", "avg_injury_projection_delta"),
            ("avg_injury_confidence_delta_by_injury_bucket", "avg_injury_confidence_delta"),
        ]

        board_counts = diagnostics.get("board_counts", {})
        if isinstance(board_counts, Mapping):
            for key, count in board_counts.items():
                rows.append(
                    {
                        "scope": "overview",
                        "section": "board_counts",
                        "key": str(key),
                        "count": int(count),
                        "value": None,
                    }
                )

        for scope in ["qualified_pool", "elite_board", "full_market_board"]:
            payload = diagnostics.get(scope, {})
            self._append_distribution_rows(
                rows,
                scope=scope,
                payload=payload,
                distribution_sections=distribution_sections,
                average_sections=average_sections,
            )

        player_points_guard = diagnostics.get("player_points_guard", {})
        if isinstance(player_points_guard, Mapping):
            for scope, payload in player_points_guard.items():
                self._append_distribution_rows(
                    rows,
                    scope=f"player_points_guard.{scope}",
                    payload=payload,
                    distribution_sections=distribution_sections,
                    average_sections=average_sections,
                )

        player_points_realism = diagnostics.get("player_points_realism", {})
        if isinstance(player_points_realism, Mapping):
            for scope, payload in player_points_realism.items():
                self._append_distribution_rows(
                    rows,
                    scope=f"player_points_realism.{scope}",
                    payload=payload,
                    distribution_sections=distribution_sections,
                    average_sections=average_sections,
                )

        player_points_admission = diagnostics.get("player_points_elite_admission", {})
        if isinstance(player_points_admission, Mapping):
            for key in [
                "player_points_post_realism_count",
                "player_points_core_pass_count",
                "player_points_elite_guard_pass_count",
                "player_points_elite_ranked_top_n_count",
                "player_points_final_selected_count",
            ]:
                rows.append(
                    {
                        "scope": "overview",
                        "section": "player_points_elite_admission",
                        "key": str(key),
                        "count": int(player_points_admission.get(key, 0) or 0),
                        "value": None,
                    }
                )
            for section in [
                "count_by_side",
                "count_by_player_profile_bucket",
                "count_by_player_points_line_band",
                "count_by_injury_influence_bucket",
                "count_by_final_selection_source_lane",
                "count_by_final_exclusion_stage",
                "count_by_elite_guard_fail_reason",
                "exclusion_buckets",
            ]:
                items = player_points_admission.get(section, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    rows.append(
                        {
                            "scope": "overview",
                            "section": f"player_points_elite_admission.{section}",
                            "key": str(item.get("key", "")),
                            "side": str(item.get("side", "") or ""),
                            "count": int(item.get("count", 0) or 0),
                            "value": None,
                        }
                    )

        final_board_construction = diagnostics.get("final_board_construction", {})
        if isinstance(final_board_construction, Mapping):
            for scope, payload in final_board_construction.items():
                if not isinstance(payload, Mapping):
                    continue
                for stage_name in ["input_live_candidates", "post_primary_selection", "post_exposure_caps", "post_backfill"]:
                    stage_payload = payload.get(stage_name, {})
                    if not isinstance(stage_payload, Mapping):
                        continue
                    rows.append(
                        {
                            "scope": str(scope),
                            "section": "final_board_construction",
                            "key": str(stage_name),
                            "count": int(stage_payload.get("count", 0) or 0),
                            "value": None,
                        }
                    )
                    gate_mode_items = stage_payload.get("count_by_qualification_gate_mode", [])
                    if isinstance(gate_mode_items, list):
                        for item in gate_mode_items:
                            rows.append(
                                {
                                    "scope": str(scope),
                                    "section": f"final_board_construction.{stage_name}.count_by_qualification_gate_mode",
                                    "key": str(item.get("key", "")),
                                    "count": int(item.get("count", 0) or 0),
                                    "value": None,
                                }
                            )
                    source_lane_items = stage_payload.get("count_by_final_selection_source_lane", [])
                    if isinstance(source_lane_items, list):
                        for item in source_lane_items:
                            rows.append(
                                {
                                    "scope": str(scope),
                                    "section": f"final_board_construction.{stage_name}.count_by_final_selection_source_lane",
                                    "key": str(item.get("key", "")),
                                    "count": int(item.get("count", 0) or 0),
                                    "value": None,
                                }
                            )

                for key in [
                    "candidate_count_before_final_board_build",
                    "core_pass_candidate_count",
                    "live_quality_rescue_candidate_count",
                    "final_selected_count",
                    "backfill_added_count",
                ]:
                    rows.append(
                        {
                            "scope": str(scope),
                            "section": "final_board_construction",
                            "key": str(key),
                            "count": int(payload.get(key, 0) or 0),
                            "value": None,
                        }
                    )

                final_source_items = payload.get("final_selected_by_source_lane", [])
                if isinstance(final_source_items, list):
                    for item in final_source_items:
                        rows.append(
                            {
                                "scope": str(scope),
                                "section": "final_selected_by_source_lane",
                                "key": str(item.get("key", "")),
                                "count": int(item.get("count", 0) or 0),
                                "value": None,
                            }
                        )
                backfill_items = payload.get("backfill_added_by_qualification_gate_mode", [])
                if isinstance(backfill_items, list):
                    for item in backfill_items:
                        rows.append(
                            {
                                "scope": str(scope),
                                "section": "backfill_added_by_qualification_gate_mode",
                                "key": str(item.get("key", "")),
                                "count": int(item.get("count", 0) or 0),
                                "value": None,
                            }
                        )

        for section in ["rejected_by_reason", "qualified_by_reason"]:
            items = diagnostics.get(section, [])
            if not isinstance(items, list):
                continue
            for item in items:
                rows.append(
                    {
                            "scope": "overview",
                            "section": section,
                            "key": str(item.get("key", "")),
                            "side": str(item.get("side", "") or ""),
                            "count": int(item.get("count", 0) or 0),
                            "value": None,
                        }
                    )

        items = diagnostics.get("qualification_rate_by_side", [])
        if isinstance(items, list):
            for item in items:
                rows.append(
                    {
                        "scope": "overview",
                        "section": "qualification_rate_by_side",
                        "key": str(item.get("key", "")),
                        "side": str(item.get("side", "") or item.get("key", "")),
                        "count": int(item.get("total_count", 0) or 0),
                        "value": float(item.get("qualification_rate", 0.0) or 0.0),
                    }
                )

        for section in ["qualified_by_reason_and_side", "rejected_by_reason_and_side"]:
            items = diagnostics.get(section, [])
            if not isinstance(items, list):
                continue
            for item in items:
                rows.append(
                    {
                        "scope": "overview",
                        "section": section,
                        "key": str(item.get("key", "")),
                        "side": str(item.get("side", "") or ""),
                        "count": int(item.get("count", 0) or 0),
                        "value": None,
                    }
                )

        return pd.DataFrame(rows)

    def market_trust_weight(self, market_type: Any) -> float:
        return float(self.MARKET_TRUST_WEIGHTS.get(str(market_type or "").strip(), 0.88))

    def qualification_reason(self, row: Mapping[str, Any]) -> str:
        recommendation = str(row.get("recommendation", "") or "").strip().lower()
        rejection_reason = str(row.get("rejection_reason", "") or "").strip()
        if recommendation == "rejected" or rejection_reason:
            return ""

        market_type = str(row.get("market_type", "") or "").strip()
        source_reason = str(row.get("reason", "") or "").strip()
        line_source = str(row.get("line_source", "") or "").strip()
        qualification_gate_mode = str(row.get("qualification_gate_mode", "") or "").strip()
        minutes_bucket = self.minutes_bucket(row)
        odds_bucket = self.odds_bucket(row.get("odds"))
        quality_score = self.to_float(row.get("quality_score")) or 0.0
        confidence = self.to_float(row.get("confidence")) or 0.0

        if not recommendation and source_reason:
            return source_reason
        if qualification_gate_mode == "live_quality_rescue":
            return "live_quality_rescue_pass"

        if self._to_bool(row.get("synthetic_line")) or line_source == "partial_market_fill":
            return "partial_market_fill_pass"
        if line_source == "predictive_market_fill":
            return "predictive_market_fill_pass"

        if market_type == "moneyline":
            if odds_bucket == "plus_big":
                return "moneyline_longshot_exception_pass"
            if odds_bucket == "plus_small":
                return "moneyline_plus_money_pass"
            if quality_score >= 85.0 and confidence >= 0.75:
                return "moneyline_high_quality_pass"
            return "moneyline_core_pass"

        if market_type.startswith("player_"):
            if market_type == "player_points":
                # Elite edge validation: directional edge must be meaningful
                edge = self.to_float(row.get("edge")) or 0.0
                selection = str(row.get("selection", "") or "").strip().lower()
                
                # Edge direction must match selection for the bet to be valid:
                # - Over bets need positive edge (projection > line)
                # - Under bets need negative edge (projection < line)
                if selection == "over" and edge <= 0:
                    # Over bet with non-positive edge is invalid
                    return "negative_edge_elite_reject"
                if selection == "under" and edge >= 0:
                    # Under bet with non-negative edge is invalid
                    return "negative_edge_elite_reject"
                
                if quality_score >= 85.0 and confidence >= 0.75:
                    return "player_points_high_quality_pass"
                if minutes_bucket in {"34_plus", "28_34"}:
                    return "player_points_scoring_quality_pass"
                return "player_points_edge_confidence_pass"
            if minutes_bucket == "lt_22":
                return "player_low_minutes_exception_pass"
            if quality_score >= 85.0 and confidence >= 0.75:
                return "player_high_quality_pass"
            if minutes_bucket in {"34_plus", "28_34"}:
                return "player_stable_minutes_pass"
            return "player_edge_confidence_pass"

        if market_type == "team_total":
            return "team_total_edge_confidence_pass"
        if market_type == "team_projection":
            return "team_projection_snapshot"
        if market_type == "game_total_projection":
            return "game_total_projection_snapshot"
        if source_reason:
            return source_reason
        return "board_candidate_pass"

    def minutes_bucket(self, row: Mapping[str, Any]) -> str:
        return bucket_minutes_bucket(row)

    def odds_bucket(self, odds: Any) -> str:
        return bucket_odds_bucket(odds)

    def quality_band(self, quality_score: Any) -> str:
        return bucket_quality_band(quality_score)

    def confidence_band(self, confidence: Any) -> str:
        return bucket_confidence_band(confidence)

    def selection_side(self, selection: Any) -> str:
        text = str(selection or "").strip().lower()
        if text in {"over", "o"}:
            return "over"
        if text in {"under", "u"}:
            return "under"
        if text in {"ml", "moneyline"}:
            return "moneyline"
        if text == "projection":
            return "projection"
        return text or "unknown"

    def projected_minutes(self, row: Mapping[str, Any]) -> float | None:
        return projected_minutes(row)

    def player_profile_bucket(self, row: Mapping[str, Any]) -> str:
        return bucket_player_profile_bucket(row)

    def player_points_line_band(self, row: Mapping[str, Any]) -> str:
        return player_points_line_band(row)

    def injury_influence_bucket(self, row: Mapping[str, Any]) -> str:
        return injury_influence_bucket(row)

    def _distribution_payload(self, df: pd.DataFrame) -> dict[str, Any]:
        return {
            "count_by_prop_type": self._count_prop_types(df),
            "count_by_minutes_bucket": self._count_records(df, "minutes_bucket"),
            "count_by_odds_bucket": self._count_records(df, "odds_bucket"),
            "count_by_quality_band": self._count_records(df, "quality_band"),
            "count_by_confidence_band": self._count_records(df, "confidence_band"),
            "count_by_side": self._count_side(df),
            "count_by_player_profile_bucket": self._count_records(df, "player_profile_bucket"),
            "count_by_player_points_line_band": self._count_records(df, "player_points_line_band"),
            "count_by_injury_influence_bucket": self._count_records(df, "injury_influence_bucket"),
            "count_by_qualification_gate_mode": self._count_records(df, "qualification_gate_mode"),
            "count_by_final_selection_source_lane": self._count_records(df, "final_selection_source_lane"),
            "count_by_blocked_by_elite_points_risk_guard": self._count_records(df, "blocked_by_elite_points_risk_guard"),
            "count_by_elite_points_risk_guard_reason": self._count_records(df, "elite_points_risk_guard_reason"),
            "count_by_blocked_by_player_points_strong_over_calibration": self._count_records(
                df,
                "blocked_by_player_points_strong_over_calibration",
            ),
            "count_by_player_points_strong_over_calibration_reason": self._count_records(
                df,
                "player_points_strong_over_calibration_reason",
            ),
            "count_by_elite_points_ranking_reason": self._count_records(df, "elite_points_ranking_reason"),
            "count_by_player_points_elite_outcome": self._count_records(df, "player_points_elite_outcome"),
            "count_by_player_points_realism_dampener_reason": self._count_records(
                df,
                "player_points_realism_dampener_reason",
            ),
            "count_by_market_trust_weight_band": self._count_records(df, "market_trust_weight_band"),
            "avg_quality_by_prop_type": self._avg_quality_by_prop_type(df),
            "avg_quality_by_side": self._avg_metric_by_side(df, "quality_score", "avg_quality_score"),
            "avg_confidence_by_side": self._avg_metric_by_side(df, "confidence", "avg_confidence"),
            "avg_injury_projection_delta_by_side": self._avg_metric_by_side(
                df,
                "injury_projection_delta",
                "avg_injury_projection_delta",
            ),
            "avg_injury_confidence_delta_by_side": self._avg_metric_by_side(
                df,
                "injury_confidence_delta",
                "avg_injury_confidence_delta",
            ),
            "avg_injury_projection_delta_by_line_band": self._avg_metric_by_group(
                df,
                group_column="player_points_line_band",
                metric_column="injury_projection_delta",
                value_field="avg_injury_projection_delta",
            ),
            "avg_injury_confidence_delta_by_line_band": self._avg_metric_by_group(
                df,
                group_column="player_points_line_band",
                metric_column="injury_confidence_delta",
                value_field="avg_injury_confidence_delta",
            ),
            "avg_injury_projection_delta_by_injury_bucket": self._avg_metric_by_group(
                df,
                group_column="injury_influence_bucket",
                metric_column="injury_projection_delta",
                value_field="avg_injury_projection_delta",
            ),
            "avg_injury_confidence_delta_by_injury_bucket": self._avg_metric_by_group(
                df,
                group_column="injury_influence_bucket",
                metric_column="injury_confidence_delta",
                value_field="avg_injury_confidence_delta",
            ),
        }

    def _player_points_guard_payload(
        self,
        *,
        qualified_pool_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
    ) -> dict[str, Any]:
        qualified_points = self._points_only_df(qualified_pool_df)
        elite_points = self._points_only_df(elite_df)
        full_market_points = self._points_only_df(full_market_df)
        blocked_points = qualified_points[
            qualified_points["blocked_by_elite_points_risk_guard"].fillna(False).astype(bool)
        ].copy() if not qualified_points.empty and "blocked_by_elite_points_risk_guard" in qualified_points.columns else pd.DataFrame()
        return {
            "qualified_pool": self._distribution_payload(qualified_points),
            "elite_board": self._distribution_payload(elite_points),
            "full_market_board": self._distribution_payload(full_market_points),
            "blocked_from_elite_candidate_pool": self._distribution_payload(blocked_points),
        }

    def _player_points_strong_over_guard_payload(
        self,
        *,
        qualified_pool_df: pd.DataFrame,
    ) -> dict[str, Any]:
        """Counts of strong-OVER calibration-blocked rows in the qualified pool.

        Returns:
          - player_points_strong_over_guard_count: total blocked rows
          - player_points_strong_over_guard_by_player: {player_name: count}
          - player_points_strong_over_guard_by_edge_bucket: {bucket: count}
            buckets: "edge_3_to_6", "edge_6_plus"
        """
        empty = {
            "player_points_strong_over_guard_count": 0,
            "player_points_strong_over_guard_by_player": {},
            "player_points_strong_over_guard_by_edge_bucket": {},
        }
        if (
            not isinstance(qualified_pool_df, pd.DataFrame)
            or qualified_pool_df.empty
            or "blocked_by_player_points_strong_over_calibration" not in qualified_pool_df.columns
        ):
            return empty

        blocked_mask = (
            qualified_pool_df["blocked_by_player_points_strong_over_calibration"]
            .fillna(False)
            .astype(bool)
        )
        blocked = qualified_pool_df[blocked_mask]
        if blocked.empty:
            return empty

        by_player: dict[str, int] = {}
        for player in blocked.get("entity_name", pd.Series(dtype=str)).fillna("").astype(str):
            key = player.strip() or "unknown"
            by_player[key] = by_player.get(key, 0) + 1

        by_bucket: dict[str, int] = {"edge_3_to_6": 0, "edge_6_plus": 0}
        edge_series = blocked.get("edge", pd.Series(dtype=float))
        for value in edge_series.tolist():
            try:
                edge = float(value)
            except (TypeError, ValueError):
                continue
            if edge >= 6.0:
                by_bucket["edge_6_plus"] += 1
            elif edge >= 3.0:
                by_bucket["edge_3_to_6"] += 1

        return {
            "player_points_strong_over_guard_count": int(len(blocked)),
            "player_points_strong_over_guard_by_player": dict(sorted(by_player.items())),
            "player_points_strong_over_guard_by_edge_bucket": by_bucket,
        }

    def _player_points_realism_payload(
        self,
        *,
        qualified_pool_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
    ) -> dict[str, Any]:
        qualified_points = self._points_only_df(qualified_pool_df)
        elite_points = self._points_only_df(elite_df)
        full_market_points = self._points_only_df(full_market_df)
        downgraded_points = qualified_points[
            qualified_points.get("player_points_elite_outcome", pd.Series(dtype=str)).fillna("").astype(str) == "downgraded"
        ].copy() if not qualified_points.empty else pd.DataFrame()
        full_market_only_points = qualified_points[
            qualified_points.get("player_points_elite_outcome", pd.Series(dtype=str)).fillna("").astype(str) == "full_market_only"
        ].copy() if not qualified_points.empty else pd.DataFrame()
        return {
            "qualified_pool": self._distribution_payload(qualified_points),
            "elite_board": self._distribution_payload(elite_points),
            "full_market_board": self._distribution_payload(full_market_points),
            "downgraded_from_elite": self._distribution_payload(downgraded_points),
            "full_market_only": self._distribution_payload(full_market_only_points),
        }

    def _player_points_elite_admission_payload(self, df: pd.DataFrame) -> dict[str, Any]:
        bucket_order = [
            ELITE_REJECT_GAME_CONTEXT_SUPPRESSED,
            ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER,
            "failed_hard_guard",
            "lost_on_score",
            "lost_on_confidence",
            "lost_on_realism",
            "lost_on_exposure",
            "lost_on_board_capacity",
            "lost_on_cross-market_rank_pressure",
        ]
        if df.empty:
            return {
                "player_points_post_realism_count": 0,
                "player_points_core_pass_count": 0,
                "player_points_elite_guard_pass_count": 0,
                "player_points_elite_ranked_top_n_count": 0,
                "player_points_final_selected_count": 0,
                "count_by_side": [],
                "count_by_player_profile_bucket": [],
                "count_by_player_points_line_band": [],
                "count_by_injury_influence_bucket": [],
                "count_by_final_selection_source_lane": [],
                "count_by_final_exclusion_stage": [],
                "count_by_elite_guard_fail_reason": [],
                "exclusion_buckets": [{"key": key, "count": 0} for key in bucket_order],
            }

        working = df.copy()
        for column in [
            "player_name",
            "market",
            "side",
            "elite_guard_fail_reason",
            "final_exclusion_stage",
            "qualification_gate_mode",
            "final_selection_source_lane",
            "player_profile_bucket",
            "player_points_line_band",
            "injury_influence_bucket",
        ]:
            if column in working.columns:
                working[column] = working[column].fillna("").astype(str).map(self._clean_text)

        guard_pass = working.get("elite_guard_pass", pd.Series(False, index=working.index))
        if not isinstance(guard_pass, pd.Series):
            guard_pass = pd.Series(False, index=working.index)
        guard_pass = guard_pass.fillna(False).astype(bool)

        top_n = working.get("elite_ranked_top_n", pd.Series(False, index=working.index))
        if not isinstance(top_n, pd.Series):
            top_n = pd.Series(False, index=working.index)
        top_n = top_n.fillna(False).astype(bool)

        final_stage = working.get("final_exclusion_stage", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        excluded = working[final_stage != "selected"].copy()
        raw_exclusion_counts = self._count_records(excluded, "final_exclusion_stage")
        raw_exclusion_lookup = {
            str(item.get("key", "")).strip(): int(item.get("count", 0) or 0)
            for item in raw_exclusion_counts
        }
        exclusion_counts = [
            {"key": key, "count": int(raw_exclusion_lookup.get(key, 0))}
            for key in bucket_order
        ]

        return {
            "player_points_post_realism_count": int(len(working)),
            "player_points_core_pass_count": int(
                working.get("final_selection_source_lane", pd.Series(dtype=str))
                .fillna("")
                .astype(str)
                .str.strip()
                .eq("core_pass")
                .sum()
            ),
            "player_points_elite_guard_pass_count": int(guard_pass.sum()),
            "player_points_elite_ranked_top_n_count": int(top_n.sum()),
            "player_points_final_selected_count": int(final_stage.eq("selected").sum()),
            "count_by_side": self._count_admission_side(working),
            "count_by_player_profile_bucket": self._count_records(working, "player_profile_bucket"),
            "count_by_player_points_line_band": self._count_records(working, "player_points_line_band"),
            "count_by_injury_influence_bucket": self._count_records(working, "injury_influence_bucket"),
            "count_by_final_selection_source_lane": self._count_records(working, "final_selection_source_lane"),
            "count_by_final_exclusion_stage": self._count_records(working, "final_exclusion_stage"),
            "count_by_elite_guard_fail_reason": self._count_records(working, "elite_guard_fail_reason"),
            "exclusion_buckets": exclusion_counts,
        }

    def _final_board_construction_payload(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {}
        return {
            str(scope): self._normalize_board_construction_scope(scope_payload)
            for scope, scope_payload in payload.items()
            if isinstance(scope_payload, Mapping)
        }

    def _normalize_board_construction_scope(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for stage_name in ["input_live_candidates", "post_primary_selection", "post_exposure_caps", "post_backfill"]:
            stage_payload = payload.get(stage_name, {})
            if not isinstance(stage_payload, Mapping):
                stage_payload = {}
            normalized[stage_name] = {
                "count": int(stage_payload.get("count", 0) or 0),
                "count_by_qualification_gate_mode": self._normalize_count_items(
                    stage_payload.get("count_by_qualification_gate_mode", [])
                ),
                "count_by_final_selection_source_lane": self._normalize_count_items(
                    stage_payload.get("count_by_final_selection_source_lane", [])
                ),
            }
        normalized["candidate_count_before_final_board_build"] = int(payload.get("candidate_count_before_final_board_build", 0) or 0)
        normalized["core_pass_candidate_count"] = int(payload.get("core_pass_candidate_count", 0) or 0)
        normalized["live_quality_rescue_candidate_count"] = int(payload.get("live_quality_rescue_candidate_count", 0) or 0)
        normalized["final_selected_count"] = int(payload.get("final_selected_count", 0) or 0)
        normalized["final_selected_by_source_lane"] = self._normalize_count_items(
            payload.get("final_selected_by_source_lane", [])
        )
        normalized["backfill_added_count"] = int(payload.get("backfill_added_count", 0) or 0)
        normalized["backfill_added_by_qualification_gate_mode"] = self._normalize_count_items(
            payload.get("backfill_added_by_qualification_gate_mode", [])
        )
        return normalized

    def _extract_player_points_elite_admission_df(
        self,
        payload: Mapping[str, Any] | None,
    ) -> pd.DataFrame:
        if not isinstance(payload, Mapping):
            return pd.DataFrame()
        elite_payload = payload.get("elite", {})
        if not isinstance(elite_payload, Mapping):
            return pd.DataFrame()
        rows = elite_payload.get("player_points_elite_admission_rows", [])
        if not isinstance(rows, list):
            return pd.DataFrame()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    @staticmethod
    def _normalize_count_items(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            normalized.append(
                {
                    "key": str(item.get("key", "")),
                    "count": int(item.get("count", 0) or 0),
                }
            )
        return normalized

    def _count_prop_types(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty:
            return []
        if "prop_type" in df.columns:
            series = df["prop_type"]
        elif "market_type" in df.columns:
            series = df["market_type"]
        else:
            return []
        counts = (
            series.fillna("unknown")
            .astype(str)
            .value_counts()
            .rename_axis("key")
            .reset_index(name="count")
        )
        return [
            {"key": str(row["key"]), "count": int(row["count"])}
            for _, row in counts.iterrows()
        ]

    def _avg_quality_by_prop_type(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty or "quality_score" not in df.columns:
            return []
        key_column = "prop_type" if "prop_type" in df.columns else ("market_type" if "market_type" in df.columns else "")
        if not key_column:
            return []

        quality = df.copy()
        quality[key_column] = quality[key_column].fillna("unknown").astype(str)
        quality["quality_score"] = pd.to_numeric(quality["quality_score"], errors="coerce")
        quality = quality.dropna(subset=["quality_score"])
        if quality.empty:
            return []

        grouped = (
            quality.groupby(key_column, as_index=False)["quality_score"]
            .mean()
            .rename(columns={key_column: "key", "quality_score": "avg_quality_score"})
            .sort_values(by=["avg_quality_score", "key"], ascending=[False, True])
            .reset_index(drop=True)
        )
        grouped["avg_quality_score"] = grouped["avg_quality_score"].round(4)
        return [
            {"key": str(row["key"]), "avg_quality_score": float(row["avg_quality_score"])}
            for _, row in grouped.iterrows()
        ]

    def _avg_metric_by_side(self, df: pd.DataFrame, metric_column: str, value_field: str) -> list[dict[str, Any]]:
        if df.empty or metric_column not in df.columns or "selection_side" not in df.columns:
            return []
        working = df.copy()
        working["selection_side"] = working["selection_side"].fillna("").astype(str).str.strip().str.lower()
        working = working[working["selection_side"].isin({"over", "under"})]
        if working.empty:
            return []
        working[metric_column] = pd.to_numeric(working[metric_column], errors="coerce")
        working = working.dropna(subset=[metric_column])
        if working.empty:
            return []

        grouped = (
            working.groupby("selection_side", as_index=False)[metric_column]
            .mean()
            .rename(columns={"selection_side": "key", metric_column: value_field})
            .sort_values(by=["key"], ascending=[True])
            .reset_index(drop=True)
        )
        grouped[value_field] = grouped[value_field].round(4)
        return [
            {
                "key": str(row["key"]),
                "side": str(row["key"]),
                value_field: float(row[value_field]),
            }
            for _, row in grouped.iterrows()
        ]

    def _avg_metric_by_group(
        self,
        df: pd.DataFrame,
        *,
        group_column: str,
        metric_column: str,
        value_field: str,
    ) -> list[dict[str, Any]]:
        if df.empty or group_column not in df.columns or metric_column not in df.columns:
            return []
        working = df.copy()
        working[group_column] = working[group_column].fillna("").astype(str).str.strip()
        working = working[~working[group_column].str.lower().isin({"", "nan", "none", "<na>"})]
        if working.empty:
            return []
        working[metric_column] = pd.to_numeric(working[metric_column], errors="coerce")
        working = working.dropna(subset=[metric_column])
        if working.empty:
            return []

        grouped = (
            working.groupby(group_column, as_index=False)[metric_column]
            .mean()
            .rename(columns={group_column: "key", metric_column: value_field})
            .sort_values(by=["key"], ascending=[True])
            .reset_index(drop=True)
        )
        grouped[value_field] = grouped[value_field].round(4)
        return [
            {
                "key": str(row["key"]),
                value_field: float(row[value_field]),
            }
            for _, row in grouped.iterrows()
        ]

    def _count_records(self, df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
        if df.empty or column not in df.columns:
            return []
        counts = (
            df[column]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        counts = counts[~counts.str.lower().isin({"", "nan", "none", "<na>"})]
        if counts.empty:
            return []
        frame = (
            counts.value_counts()
            .rename_axis("key")
            .reset_index(name="count")
        )
        return [
            {"key": str(row["key"]), "count": int(row["count"])}
            for _, row in frame.iterrows()
        ]

    def _count_side(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty or "selection_side" not in df.columns:
            return []
        working = df["selection_side"].fillna("").astype(str).str.strip().str.lower()
        working = working[working.isin({"over", "under"})]
        if working.empty:
            return []
        counts = (
            working.value_counts()
            .rename_axis("key")
            .reset_index(name="count")
        )
        return [
            {
                "key": str(row["key"]),
                "side": str(row["key"]),
                "count": int(row["count"]),
            }
            for _, row in counts.iterrows()
        ]

    def _count_admission_side(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty or "side" not in df.columns:
            return []
        working = df["side"].fillna("").astype(str).str.strip().str.lower()
        working = working[working.isin({"over", "under"})]
        if working.empty:
            return []
        counts = (
            working.value_counts()
            .rename_axis("key")
            .reset_index(name="count")
        )
        return [
            {
                "key": str(row["key"]),
                "side": str(row["key"]),
                "count": int(row["count"]),
            }
            for _, row in counts.iterrows()
        ]

    def _qualification_rate_by_side(self, qualified_df: pd.DataFrame, rejected_df: pd.DataFrame) -> list[dict[str, Any]]:
        frames: list[pd.DataFrame] = []
        if not qualified_df.empty:
            qualified = qualified_df.copy()
            qualified["selection_side"] = qualified.get("selection_side", "").astype(str)
            qualified["_cv_qualified"] = 1
            frames.append(qualified)
        if not rejected_df.empty:
            rejected = rejected_df.copy()
            rejected["selection_side"] = rejected.get("selection_side", "").astype(str)
            rejected["_cv_qualified"] = 0
            frames.append(rejected)
        if not frames:
            return []

        combined = pd.concat(frames, ignore_index=True)
        combined["selection_side"] = combined["selection_side"].fillna("").astype(str).str.strip().str.lower()
        combined = combined[combined["selection_side"].isin({"over", "under"})]
        if combined.empty:
            return []

        grouped = (
            combined.groupby("selection_side", as_index=False)["_cv_qualified"]
            .agg(["sum", "count"])
            .reset_index()
            .rename(columns={"selection_side": "key", "sum": "qualified_count", "count": "total_count"})
        )
        grouped["rejected_count"] = grouped["total_count"] - grouped["qualified_count"]
        grouped["qualification_rate"] = (grouped["qualified_count"] / grouped["total_count"]).round(4)
        grouped = grouped.sort_values(by=["key"], ascending=[True]).reset_index(drop=True)
        return [
            {
                "key": str(row["key"]),
                "side": str(row["key"]),
                "qualified_count": int(row["qualified_count"]),
                "rejected_count": int(row["rejected_count"]),
                "total_count": int(row["total_count"]),
                "qualification_rate": float(row["qualification_rate"]),
            }
            for _, row in grouped.iterrows()
        ]

    def _reason_counts_by_side(self, df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
        if df.empty or column not in df.columns or "selection_side" not in df.columns:
            return []
        working = df.copy()
        working["selection_side"] = working["selection_side"].fillna("").astype(str).str.strip().str.lower()
        working[column] = working[column].fillna("").astype(str).str.strip()
        working = working[working["selection_side"].isin({"over", "under"}) & (working[column] != "")]
        if working.empty:
            return []

        grouped = (
            working.groupby(["selection_side", column], as_index=False)
            .size()
            .rename(columns={column: "key", "size": "count"})
            .sort_values(by=["selection_side", "count", "key"], ascending=[True, False, True])
            .reset_index(drop=True)
        )
        return [
            {
                "key": str(row["key"]),
                "side": str(row["selection_side"]),
                "count": int(row["count"]),
            }
            for _, row in grouped.iterrows()
        ]

    def _append_distribution_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        scope: str,
        payload: Any,
        distribution_sections: list[str],
        average_sections: list[tuple[str, str]],
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        for section in distribution_sections:
            items = payload.get(section, [])
            if not isinstance(items, list):
                continue
            for item in items:
                rows.append(
                    {
                        "scope": scope,
                        "section": section,
                        "key": str(item.get("key", "")),
                        "side": str(item.get("side", "") or ""),
                        "count": int(item.get("count", 0) or 0),
                        "value": None,
                    }
                )

        for section, value_field in average_sections:
            items = payload.get(section, [])
            if not isinstance(items, list):
                continue
            for item in items:
                rows.append(
                    {
                        "scope": scope,
                        "section": section,
                        "key": str(item.get("key", "")),
                        "side": str(item.get("side", "") or ""),
                        "count": None,
                        "value": float(item.get(value_field, 0.0) or 0.0),
                    }
                )

    @staticmethod
    def _points_only_df(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()
        key_column = "prop_type" if "prop_type" in df.columns else ("market_type" if "market_type" in df.columns else "")
        if not key_column:
            return pd.DataFrame()
        return df[df[key_column].fillna("").astype(str).str.strip().str.lower() == "player_points"].copy()

    def _annotate_player_points_elite_outcome(
        self,
        df: pd.DataFrame,
        *,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
    ) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        out = df.copy()
        elite_keys = {
            self._identity_key(row)
            for _, row in elite_df.iterrows()
        } if not elite_df.empty else set()
        full_market_keys = {
            self._identity_key(row)
            for _, row in full_market_df.iterrows()
        } if not full_market_df.empty else set()

        outcomes: list[str] = []
        for _, row in out.iterrows():
            row_dict = self._to_str_dict(row)
            market_type = str(row_dict.get("prop_type") or row_dict.get("market_type") or "").strip().lower()
            if market_type != "player_points":
                outcomes.append("not_applicable")
                continue
            identity_key = self._identity_key(row_dict)
            if self._to_bool(row_dict.get("blocked_by_elite_points_risk_guard")):
                outcomes.append("blocked")
            elif identity_key in elite_keys:
                outcomes.append("kept")
            elif identity_key in full_market_keys:
                outcomes.append("full_market_only")
            elif (
                (to_float(row_dict.get("elite_points_ranking_penalty")) or 0.0) > 0.0
                or self._to_bool(row_dict.get("player_points_realism_dampened"))
            ):
                outcomes.append("downgraded")
            else:
                outcomes.append("not_selected")
        out["player_points_elite_outcome"] = outcomes
        return out

    @staticmethod
    def _identity_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
        line_value = to_float(row.get("sportsbook_line"))
        if line_value is None:
            line_value = to_float(row.get("line_value"))
        return (
            str(row.get("entity_name") or row.get("player_name") or "").strip().lower(),
            str(row.get("team") or row.get("team_abbreviation") or "").strip().upper(),
            str(row.get("prop_type") or row.get("market_type") or "").strip().lower(),
            str(row.get("selection") or row.get("side") or row.get("selection_side") or "").strip().lower(),
            f"{float(line_value or 0.0):.4f}",
        )

    @staticmethod
    def _to_str_dict(data: pd.Series | Mapping[str, Any]) -> dict[str, Any]:
        return {str(key): value for key, value in data.items()}

    @staticmethod
    def to_float(value: Any) -> float | None:
        return to_float(value)

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = str(value or "").strip()
        if text.lower() in {"nan", "none", "<na>"}:
            return ""
        return text


# =============================================================================
# ELITE TELEMETRY - Candidate filtering audit trail
# =============================================================================

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import csv
import json


# Normalized rejection reasons - do not freestyle these at runtime
REJECT_INJURY_FLAG = "reject_injury_flag"
REJECT_MINUTES_VOLATILITY = "reject_minutes_volatility"
REJECT_CONFIDENCE_BELOW_THRESHOLD = "reject_confidence_below_threshold"
REJECT_PROJECTION_REALISM = "reject_projection_realism"
REJECT_EXPOSURE_LIMIT = "reject_exposure_limit"
REJECT_NEGATIVE_EDGE_DIRECTION = "reject_negative_edge_direction"
REJECT_UNREALISTIC_LINE = "reject_unrealistic_line"
REJECT_HEAVY_FAVORITE_ODDS = "reject_heavy_favorite_odds"
REJECT_OTHER = "reject_other"
ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER = "elite_reject_context_high_caution_over"
ELITE_REJECT_GAME_CONTEXT_SUPPRESSED = "elite_reject_game_context_suppressed"
ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION = (
    "elite_reject_player_points_strong_over_calibration"
)
KELLY_PROJECTED_SKIP_CONTEXT_HIGH_CAUTION_OVER = "context_high_caution_over"
KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION = (
    "player_points_strong_over_calibration"
)

# Game status / slate-lock gate rejection reasons
ELITE_REJECT_GAME_NOT_BETTABLE = "elite_reject_game_not_bettable"
ELITE_REJECT_GAME_FINAL = "elite_reject_game_final"
ELITE_REJECT_GAME_IN_PROGRESS = "elite_reject_game_in_progress"
ELITE_REJECT_GAME_LOCKED = "elite_reject_game_locked"
ELITE_REJECT_GAME_POSTPONED = "elite_reject_game_postponed"
KELLY_SKIP_GAME_NOT_BETTABLE = "game_not_bettable"
KELLY_SKIP_GAME_FINAL = "game_final"
KELLY_SKIP_GAME_IN_PROGRESS = "game_in_progress"
KELLY_SKIP_GAME_LOCKED = "game_locked"
KELLY_SKIP_GAME_POSTPONED = "game_postponed"

PASSED_TO_ELITE = "passed_to_elite"
TOTAL_CANDIDATES = "total_candidates"


@dataclass
class EliteTelemetry:
    """
    Telemetry collector for elite candidate filtering.
    
    Records per-slate audit trail of which candidates passed/failed
    elite admission gates, with normalized rejection reasons.
    """
    slate_date: str
    counts: dict[tuple[str, str, str], int] = field(
        default_factory=lambda: defaultdict(int)
    )
    totals: dict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    summary: dict[str, Any] = field(default_factory=dict)

    def record(
        self,
        *,
        market: str,
        selection_side: str,
        rejection_reason: str,
        count: int = 1,
    ) -> None:
        """Record a telemetry event with normalized rejection reason."""
        key = (
            str(market or "unknown"),
            str(selection_side or "unknown"),
            str(rejection_reason or REJECT_OTHER),
        )
        self.counts[key] += count
        self.totals["total_events"] += count

        if rejection_reason == PASSED_TO_ELITE:
            self.totals["passed_to_elite"] += count
        elif rejection_reason == TOTAL_CANDIDATES:
            self.totals["total_candidates"] += count
        else:
            self.totals["total_rejections"] += count

    def record_candidate_seen(self, *, market: str, selection_side: str) -> None:
        """Record that a candidate was seen (entering elite filtering)."""
        self.record(
            market=market,
            selection_side=selection_side,
            rejection_reason=TOTAL_CANDIDATES,
        )

    def record_pass(self, *, market: str, selection_side: str) -> None:
        """Record that a candidate passed to elite."""
        self.record(
            market=market,
            selection_side=selection_side,
            rejection_reason=PASSED_TO_ELITE,
        )

    def set_summary(self, summary: dict[str, Any]) -> None:
        """Set the pipeline summary for persistence in audit JSON."""
        self.summary = summary.copy() if summary else {}

    def to_rows(self) -> list[dict[str, Any]]:
        """Convert telemetry to list of row dicts for CSV output."""
        timestamp = datetime.now().isoformat(timespec="seconds")
        rows: list[dict[str, Any]] = []
        for (market, selection_side, rejection_reason), count in sorted(
            self.counts.items()
        ):
            rows.append(
                {
                    "timestamp": timestamp,
                    "slate_date": self.slate_date,
                    "market": market,
                    "selection_side": selection_side,
                    "rejection_reason": rejection_reason,
                    "count": count,
                }
            )
        return rows

    def write_csv(self, out_dir: str | Path) -> Path:
        """Write telemetry to CSV file. Returns the path written."""
        out_path = Path(out_dir) / f"elite_pipeline_audit_{self.slate_date}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log_prediction_artifact_write(
            requested_prediction_date=self.slate_date,
            output_path=out_path,
            caller="courtvision.runtime_audit:EliteTelemetry.write_csv",
            artifact_label="elite_pipeline_audit_csv",
        )

        rows = self.to_rows()
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "timestamp",
                    "slate_date",
                    "market",
                    "selection_side",
                    "rejection_reason",
                    "count",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        return out_path

    def write_summary_json(self, out_dir: str | Path) -> Path:
        """Write telemetry summary to JSON file. Returns the path written."""
        out_path = Path(out_dir) / f"elite_pipeline_audit_summary_{self.slate_date}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log_prediction_artifact_write(
            requested_prediction_date=self.slate_date,
            output_path=out_path,
            caller="courtvision.runtime_audit:EliteTelemetry.write_summary_json",
            artifact_label="elite_pipeline_audit_summary",
        )
        payload = {
            "slate_date": self.slate_date,
            "totals": dict(sorted(self.totals.items())),
            "rows": self.to_rows(),
            "summary": self.summary,  # Include board analytics and other summary data
        }
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return out_path


def get_elite_rejection_reason(row: dict[str, Any], now: Any = None) -> str | None:
    """
    Determine rejection reason for elite admission.

    Returns None if row passes all gates, otherwise returns normalized
    rejection reason constant.

    Mirrors the real gate order in the pipeline.
    """
    from courtvision.runtime_selection import game_status_ineligibility_reason

    market = str(row.get("market_type", row.get("market", ""))).lower()
    selection = str(row.get("selection", "")).lower()
    edge = float(row.get("edge_pct", row.get("edge", 0.0)) or 0.0)

    # ---- Game status / slate-lock gate (early: no bets on completed games) ----
    game_status_reason = game_status_ineligibility_reason(row, now=now)
    if game_status_reason:
        reason_map = {
            "game_final": ELITE_REJECT_GAME_FINAL,
            "game_in_progress": ELITE_REJECT_GAME_IN_PROGRESS,
            "game_postponed": ELITE_REJECT_GAME_POSTPONED,
            "game_locked": ELITE_REJECT_GAME_LOCKED,
            "game_status_unknown": ELITE_REJECT_GAME_NOT_BETTABLE,
        }
        return reason_map.get(game_status_reason, ELITE_REJECT_GAME_NOT_BETTABLE)

    # Check injury flag
    if bool(row.get("injury_flag", False)):
        return REJECT_INJURY_FLAG

    # Check minutes volatility
    if bool(row.get("minutes_volatility_fail", False)):
        return REJECT_MINUTES_VOLATILITY

    # Check confidence threshold
    if bool(row.get("confidence_fail", False)):
        return REJECT_CONFIDENCE_BELOW_THRESHOLD

    # Check projection realism
    if bool(row.get("projection_realism_fail", False)):
        return REJECT_PROJECTION_REALISM

    # Check exposure limit
    if bool(row.get("exposure_limit_fail", False)):
        return REJECT_EXPOSURE_LIMIT

    context_reason = elite_context_rejection_reason(row)
    if context_reason is not None:
        return context_reason

    # Check directional edge for player_points
    if market == "player_points":
        if selection == "over" and edge <= 0:
            return REJECT_NEGATIVE_EDGE_DIRECTION
        if selection == "under" and edge >= 0:
            return REJECT_NEGATIVE_EDGE_DIRECTION

    # Check for unrealistic lines (e.g., 2.0 points for NBA players)
    line_val = float(row.get("sportsbook_line", 0))
    if market == "player_points" and line_val < 5.0:
        return REJECT_UNREALISTIC_LINE
    if market == "player_rebounds" and line_val < 2.0:
        return REJECT_UNREALISTIC_LINE
    if market == "player_assists" and line_val < 1.5:
        return REJECT_UNREALISTIC_LINE

    # Check for unprofitable heavy favorite odds (e.g., -2800 - risk $2800 to win $100)
    odds_val = int(row.get("odds", 0))
    if odds_val < -400:  # Worse than -400 (e.g., -500, -1000, -2800)
        return REJECT_HEAVY_FAVORITE_ODDS

    # Player_points strong-OVER calibration guard (historical 41.6% hit at edge>=+3)
    if player_points_strong_over_calibration_reason(row):
        return ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION

    return None


def projected_kelly_skip_reason(row: Mapping[str, Any], now: Any = None) -> str:
    """Return the Kelly skip reason implied by context-only safety rules.

    This function inspects row-level context flags and returns a skip-reason
    string if the row should be excluded from Kelly staking.  It is
    intentionally lighter than the full elite scoring pipeline so that Kelly
    sizing can be applied to Full-Market rows that are not already filtered
    out by the elite board.
    """
    from courtvision.runtime_selection import game_status_ineligibility_reason

    # Game status / slate-lock gate: never stake on completed/locked games
    game_status_reason = game_status_ineligibility_reason(row, now=now)
    if game_status_reason:
        reason_map = {
            "game_final": KELLY_SKIP_GAME_FINAL,
            "game_in_progress": KELLY_SKIP_GAME_IN_PROGRESS,
            "game_postponed": KELLY_SKIP_GAME_POSTPONED,
            "game_locked": KELLY_SKIP_GAME_LOCKED,
            "game_status_unknown": KELLY_SKIP_GAME_NOT_BETTABLE,
        }
        return reason_map.get(game_status_reason, KELLY_SKIP_GAME_NOT_BETTABLE)

    # High-caution conflicted over: historically ~49% hit (n=268)
    selection = str(row.get("selection", row.get("side", "")) or "").strip().lower()
    caution = str(row.get("context_caution_level", "") or "").strip().lower()
    if selection == "over" and caution == "high":
        return KELLY_PROJECTED_SKIP_CONTEXT_HIGH_CAUTION_OVER
    if player_points_strong_over_calibration_reason(row):
        return KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION
    return ""


def _is_truthy_context_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y"}


def elite_context_rejection_reason(row: Mapping[str, Any]) -> str | None:
    """Return the elite safety-gate reason for context-blocked picks."""
    suppression_reason = str(row.get("game_context_suppression_reason", "") or "").strip().lower()
    if (
        _is_truthy_context_flag(row.get("game_context_suppressed"))
        or _is_truthy_context_flag(row.get("candidate_team_not_in_game"))
        or suppression_reason == "team_not_in_game_context"
    ):
        return ELITE_REJECT_GAME_CONTEXT_SUPPRESSED

    selection = str(row.get("selection", row.get("side", "")) or "").strip().lower()
    caution = str(row.get("context_caution_level", "") or "").strip().lower()
    alignment = str(row.get("context_pick_alignment", "") or "").strip().lower()
    if selection == "over" and caution == "high" and alignment == "conflicted":
        return ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER
    return None


def assemble_elite_board(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Defensive assembly of elite board with directional edge validation.
    
    Tripwire catch for any rows that slipped through the main filter.
    """
    elite_rows: list[dict[str, Any]] = []

    for row in rows:
        market = str(row.get("market_type", row.get("market", ""))).lower()
        selection = str(row.get("selection", "")).lower()
        edge = float(row.get("edge_pct", row.get("edge", 0.0)) or 0.0)
        player = str(
            row.get("player_name", row.get("player", "unknown"))
        )

        if elite_context_rejection_reason(row) is not None:
            context_reason = elite_context_rejection_reason(row) or ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER
            print(
                f"[elite_defensive_catch] {player}: "
                f"{context_reason}"
            )
            continue

        if player_points_strong_over_calibration_reason(row):
            print(
                f"[elite_defensive_catch] {player}: "
                f"{ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION}"
            )
            continue

        if market == "player_points":
            if selection == "over" and edge <= 0:
                print(
                    f"[elite_defensive_catch] {player}: over with edge {edge}"
                )
                continue
            if selection == "under" and edge >= 0:
                print(
                    f"[elite_defensive_catch] {player}: under with edge {edge}"
                )
                continue

        elite_rows.append(row)

    return elite_rows


__all__ = [
    "BoardAuditPolicy",
    "EliteTelemetry",
    "get_elite_rejection_reason",
    "elite_context_rejection_reason",
    "projected_kelly_skip_reason",
    "assemble_elite_board",
    "REJECT_INJURY_FLAG",
    "REJECT_MINUTES_VOLATILITY",
    "REJECT_CONFIDENCE_BELOW_THRESHOLD",
    "REJECT_PROJECTION_REALISM",
    "REJECT_EXPOSURE_LIMIT",
    "REJECT_NEGATIVE_EDGE_DIRECTION",
    "REJECT_UNREALISTIC_LINE",
    "REJECT_HEAVY_FAVORITE_ODDS",
    "REJECT_OTHER",
    "ELITE_REJECT_CONTEXT_HIGH_CAUTION_OVER",
    "ELITE_REJECT_GAME_CONTEXT_SUPPRESSED",
    "ELITE_REJECT_PLAYER_POINTS_STRONG_OVER_CALIBRATION",
    "ELITE_REJECT_GAME_NOT_BETTABLE",
    "ELITE_REJECT_GAME_FINAL",
    "ELITE_REJECT_GAME_IN_PROGRESS",
    "ELITE_REJECT_GAME_LOCKED",
    "ELITE_REJECT_GAME_POSTPONED",
    "KELLY_PROJECTED_SKIP_CONTEXT_HIGH_CAUTION_OVER",
    "KELLY_PROJECTED_SKIP_PLAYER_POINTS_STRONG_OVER_CALIBRATION",
    "KELLY_SKIP_GAME_NOT_BETTABLE",
    "KELLY_SKIP_GAME_FINAL",
    "KELLY_SKIP_GAME_IN_PROGRESS",
    "KELLY_SKIP_GAME_LOCKED",
    "KELLY_SKIP_GAME_POSTPONED",
    "PASSED_TO_ELITE",
    "TOTAL_CANDIDATES",
]
