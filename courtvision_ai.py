"""Canonical CourtVision runtime and CLI entry point.

Governance note:
- `courtvision_ai.py` is the only live runtime entry point today.
- This file should stay orchestration-focused.
- New scoring, grading, filtering, and market logic belongs in `courtvision/`.
- When legacy logic here changes, prefer extracting a package-owned module and
  delegating to it instead of adding another unique implementation here.
"""

from __future__ import annotations

import argparse
import json
import itertools
import logging
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd
import requests
from courtvision.balldontlie_auth import (
    BALLDONTLIE_401_HINT,
    BALLDONTLIE_API_KEY_ENV_VAR,
    ENV_SOURCE_AUDIT as SHARED_ENV_SOURCE_AUDIT,
    build_unauthorized_message,
    clean_api_key,
    env_key_debug_details as shared_env_key_debug_details,
    load_env_file as shared_load_env_file,
    mask_api_key,
    prepared_url as shared_prepared_url,
    resolve_api_key,
    response_text_preview as shared_response_text_preview,
    safe_key_fingerprint as shared_safe_key_fingerprint,
    smoke_test_games_api,
)
from courtvision.calibration.grading_summary import (
    flatten_grading_summary,
    summarize_elite_filter_replay,
    summarize_graded_props,
    summarize_player_points_calibration,
    summarize_player_points_uplift_audit,
)
from courtvision.data.bdl_odds_adapter import (
    REQUIRED_COLUMNS as BDL_REQUIRED_COLUMNS,
    filter_valid_odds,
    normalize_bdl_player_props,
)
from courtvision.data.normalization import (
    infer_opponent_from_game,
    normalize_games_frame,
    normalize_injuries_frame,
    normalize_odds_frame,
    normalize_stats_frame,
    parse_minutes as shared_parse_minutes,
    safe_float as shared_safe_float,
)
from courtvision.runtime_markets import (
    CORE_PARTIAL_PLAYER_MARKETS,
    filter_player_markets as runtime_filter_player_markets,
    normalize_market_alias as runtime_normalize_market_alias,
    partial_fill_markets,
)
from courtvision.runtime_outputs import OutputLayoutConfig, OutputLayoutPolicy
from courtvision.runtime_selection import (
    BoardVolumeConfig,
    BoardVolumePolicy,
    PlayerSelectionConfig,
    PlayerSelectionPolicy,
    QualificationGateConfig,
    QualificationGatePolicy,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from courtvision.runtime_audit import (
    BoardAuditPolicy,
    get_elite_rejection_reason as _runtime_get_elite_rejection_reason,
)
from courtvision.runtime_scoring import BoardScoringConfig, BoardScoringPolicy


def get_elite_rejection_reason(row: dict[str, Any]) -> str | None:
    """Compatibility export for legacy elite-admission diagnostics.

    The package-owned helper expects canonical board fields. Older callers of
    courtvision_ai.py pass ``line`` and documented a tiny player-points edge
    floor. Keep that public diagnostic surface stable without changing the
    package-owned selection path.
    """
    normalized = dict(row)
    if "sportsbook_line" not in normalized and "line" in normalized:
        normalized["sportsbook_line"] = normalized["line"]

    market = str(normalized.get("market_type", normalized.get("market", ""))).lower()
    edge = float(normalized.get("edge_pct", normalized.get("edge", 0.0)) or 0.0)
    if market == "player_points" and 0.0 < abs(edge) < 0.013:
        return "reject_edge_below_minimum"

    return _runtime_get_elite_rejection_reason(normalized)
from courtvision.pipeline import PredictionPipeline, PredictionConfig
from courtvision.config import EliteThresholds
from courtvision.context import (
    apply_manual_player_context,
    load_manual_player_context,
    write_manual_context_diagnostics,
)

try:
    from balldontlie import BalldontlieAPI
    # The balldontlie SDK ships with a stray `print(response)` inside
    # base.py::_get_paginated_list that dumps the full raw API payload
    # (~40k chars per page) to stdout on every paginated call. We
    # neutralize it once at import time so the runtime log stays
    # readable. This only suppresses that single SDK print, not our
    # own structured [STAGE]/[COUNT]/[DIAGNOSIS] diagnostics.
    try:
        import balldontlie.base as _bdl_base  # type: ignore
        _bdl_base.print = lambda *_a, **_k: None  # type: ignore[attr-defined]
    except Exception:
        pass
except Exception:
    BalldontlieAPI = None

_ENV_SOURCE_AUDIT = SHARED_ENV_SOURCE_AUDIT
PRIMARY_PLAYER_MARKETS: tuple[str, ...] = tuple(CORE_PARTIAL_PLAYER_MARKETS)
PLAYER_POINTS_ELITE_ADMISSION_COLUMNS: tuple[str, ...] = (
    "player_name",
    "market",
    "side",
    "line",
    "projection",
    "edge",
    "confidence",
    "realism_score",
    "selection_score",
    "elite_guard_pass",
    "elite_guard_fail_reason",
    "rank_position_within_player_points",
    "rank_position_overall",
    "lost_to_non_points_candidate",
    "lost_to_same_player_exposure",
    "lost_to_board_cap",
    "lost_to_rescue_priority",
    "final_exclusion_stage",
    "qualification_gate_mode",
    "final_selection_source_lane",
    "player_profile_bucket",
    "player_points_line_band",
    "injury_influence_bucket",
    "elite_ranked_top_n",
)


def _safe_key_fingerprint(value: Optional[str]) -> str:
    return shared_safe_key_fingerprint(value)


def _env_key_debug_details(key: str, current_value: Optional[str] = None) -> dict[str, Any]:
    return shared_env_key_debug_details(key, current_value)


def _load_env_file() -> None:
    shared_load_env_file()


_load_env_file()

NBA_V1 = os.getenv("BALLDONTLIE_V1_BASE_URL", "https://api.balldontlie.io/v1").rstrip("/")
NBA_V2 = os.getenv("BALLDONTLIE_V2_BASE_URL", "https://api.balldontlie.io/v2").rstrip("/")
logger = logging.getLogger("courtvision_ai")


def _to_str_dict(data: pd.Series | dict[Any, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in data.items()}


def _pair_key(a: Any, b: Any) -> tuple[str, str]:
    a_str = str(a).strip().upper()
    b_str = str(b).strip().upper()
    return (a_str, b_str) if a_str <= b_str else (b_str, a_str)


def _get_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("courtvision_ai")
    logger.setLevel(logging.INFO)

    file_path = log_dir / "courtvision_ai.log"
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    file_handler_exists = False
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and Path(getattr(handler, "baseFilename", "")) == file_path:
            file_handler_exists = True
            break

    if not file_handler_exists:
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def _safe_json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


@dataclass
class CalibrationRule:
    market_type: str
    sample_size: int
    hit_rate: float
    mae: float
    confidence_multiplier: float


class BallDontLieClient:
    def __init__(self, api_key: str, timeout: int = 30) -> None:
        api_key, api_key_details = resolve_api_key(
            entrypoint="courtvision_ai.BallDontLieClient",
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
            current_value=api_key,
        )

        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": api_key,
                "Accept": "application/json",
            }
        )
        self.api_key_preview = mask_api_key(api_key)
        self.api_key_env_var = BALLDONTLIE_API_KEY_ENV_VAR
        self.api_key_details = dict(api_key_details)
        retry_total = int(os.getenv("COURTVISION_HTTP_RETRIES", "3") or "3")
        retry_backoff = float(os.getenv("COURTVISION_HTTP_BACKOFF", "1.5") or "1.5")
        retry = Retry(
            total=retry_total,
            connect=retry_total,
            read=retry_total,
            status=retry_total,
            backoff_factor=retry_backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.timeout = timeout
        self.last_odds_status = "not_requested"
        self.last_odds_message = ""
        self.last_http_error_message = ""
        vendor_config = os.getenv("BALLDONTLIE_VENDORS", "fanduel,draftkings,fanatics,caesars,betrivers").strip()
        self.preferred_vendors = {
            vendor.strip().lower()
            for vendor in vendor_config.split(",")
            if vendor.strip()
        }

    def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        prepared_url = self._prepared_url(url, params)
        response = self.session.get(url, params=params, timeout=self.timeout)

        if response.status_code == 401:
            error_message = self._format_http_error(
                response=response,
                url=url,
                params=params,
                prepared_url=prepared_url,
            )
            self.last_http_error_message = error_message
            logging.getLogger("courtvision_ai").error(error_message)
            raise RuntimeError(error_message)

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected response payload type from {response.url}")
        return payload

    def _prepared_url(self, url: str, params: Optional[dict[str, Any]] = None) -> str:
        return shared_prepared_url(url, params)

    def _format_http_error(
        self,
        response: requests.Response,
        url: str,
        params: Optional[dict[str, Any]] = None,
        prepared_url: Optional[str] = None,
    ) -> str:
        response_url = str(getattr(response, "url", "") or prepared_url or url)
        body_preview = self._response_text_preview(response)
        has_auth = bool(self.session.headers.get("Authorization"))
        if response.status_code == 401:
            return build_unauthorized_message(
                entrypoint="courtvision_ai.BallDontLieClient",
                env_var_name=self.api_key_env_var,
                masked_key_preview=self.api_key_preview,
                url=response_url,
                params=params,
                has_auth=has_auth,
                body_snippet=body_preview,
                details=self.api_key_details,
            )
        return (
            "BallDontLie request failed "
            f"status={response.status_code} "
            f"url={response_url} "
            f"params={params or {}} "
            f"has_auth={has_auth} "
            f"key={self.api_key_preview} "
            f"body={body_preview}"
        )

    @staticmethod
    def _response_text_preview(response: requests.Response, limit: int = 300) -> str:
        return shared_response_text_preview(response, limit=limit)

    def paginate(self, url: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("per_page", 100)

        cursor: Any = None
        rows: list[dict[str, Any]] = []

        while True:
            query = dict(params)
            if cursor is not None:
                query["cursor"] = cursor
            payload = self._get(url, query)

            data = payload.get("data", [])
            if isinstance(data, list):
                rows.extend(item for item in data if isinstance(item, dict))

            meta = payload.get("meta", {})
            next_cursor = meta.get("next_cursor")
            if next_cursor in (None, ""):
                next_cursor = meta.get("next_page")

            if next_cursor in (None, ""):
                break

            cursor = next_cursor

        return rows

    def get_stats(self, start_date: str, end_date: str) -> pd.DataFrame:
        rows = self.paginate(
            f"{NBA_V1}/stats",
            {
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        return pd.DataFrame(rows)

    def get_games(self, game_date: str) -> pd.DataFrame:
        payload = self._get(
            f"{NBA_V1}/games",
            {
                "dates[]": game_date,
                "per_page": 100,
            },
        )
        rows = payload.get("data", [])
        return pd.DataFrame(rows if isinstance(rows, list) else [])

    def get_injuries(self, game_date: Optional[str] = None) -> pd.DataFrame:
        endpoint_candidates: list[tuple[str, dict[str, Any]]] = []
        for base_url in [NBA_V1, NBA_V2]:
            if game_date:
                endpoint_candidates.extend([
                    (f"{base_url}/injuries", {"dates[]": game_date, "per_page": 100}),
                    (f"{base_url}/injuries", {"date": game_date, "per_page": 100}),
                ])
            endpoint_candidates.append((f"{base_url}/injuries", {"per_page": 100}))

        for url, params in endpoint_candidates:
            try:
                payload = self._get(url, params)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code in {400, 404}:
                    continue
                self.last_odds_message = f"injuries_request_failed:{status_code or 'error'}"
                return pd.DataFrame()
            except Exception:
                continue

            rows = payload.get("data", [])
            if isinstance(rows, list) and rows:
                return pd.json_normalize(rows)

        return pd.DataFrame()

    def get_odds(self, game_date: str, game_ids: Optional[list[int]] = None) -> pd.DataFrame:
        """
        Fetch BallDontLie player prop odds and return them in the canonical
        internal schema (see `bdl_odds_adapter.REQUIRED_COLUMNS`).

        Uses `/nba/v2/odds/player_props` (requires `game_id`) per the BallDontLie
        OpenAPI spec. The result is always a DataFrame with the required columns,
        even when no rows are available.
        """
        logger = logging.getLogger("courtvision_ai")
        print(f"[COUNT] active_odds_fetch_path=BallDontLieClient.get_odds", flush=True)
        print(f"[COUNT] games_for_odds={len(game_ids) if game_ids else 0}", flush=True)

        self.last_odds_status = "request_started"
        self.last_odds_message = ""

        # Fetch player props for each game_id using /nba/v2/odds/player_props endpoint
        all_player_props: list[dict[str, Any]] = []
        if game_ids:
            for game_id in game_ids:
                if game_id is None:
                    continue
                print(f"[COUNT] player_prop_request game_id={game_id}", flush=True)
                try:
                    payload = self._get(f"{NBA_V2}/odds/player_props", {"game_id": game_id})
                    data = payload.get("data", []) if isinstance(payload, dict) else []
                    row_count = len(data) if isinstance(data, list) else 0
                    print(f"[COUNT] player_prop_rows_returned game_id={game_id} rows={row_count}", flush=True)
                    if row_count == 0:
                        print(f"[WARNING] no_player_props_for_game game_id={game_id}", flush=True)
                    else:
                        all_player_props.extend(data)
                except Exception as exc:
                    print(f"[WARNING] player_props_fetch_failed game_id={game_id} error={type(exc).__name__}: {exc}", flush=True)
                    continue

        print(f"[COUNT] total_player_prop_rows={len(all_player_props)}", flush=True)

        # Build player lookup so the adapter can resolve player_name from player_id
        try:
            player_lookup = self._build_player_prop_identity_lookup(
                game_date, game_ids=game_ids or []
            )
        except Exception as exc:
            logger.warning("player_lookup_build_failed error=%s", exc)
            print(f"[DIAGNOSIS] player_lookup_build raised {type(exc).__name__}: {exc}", flush=True)
            player_lookup = {}
        print(f"[COUNT] player_lookup_size={len(player_lookup)}", flush=True)

        # Convert to DataFrame and run through canonical adapter
        raw_df = pd.json_normalize(all_player_props) if all_player_props else pd.DataFrame()
        print(f"[COUNT] raw_df_columns={list(raw_df.columns)}", flush=True)
        print(f"[COUNT] raw_df_rows={len(raw_df)}", flush=True)
        print(f"[COUNT] raw_df_sample={raw_df.head(1).to_dict('records') if not raw_df.empty else 'N/A'}", flush=True)
        
        normalized = normalize_bdl_player_props(
            raw_df,
            player_lookup=player_lookup,
            market_type_mapper=runtime_normalize_market_alias,
        )
        print(f"[COUNT] normalized_columns={list(normalized.columns)}", flush=True)
        print(f"[COUNT] normalized_rows={len(normalized)}", flush=True)
        if not normalized.empty:
            raw_prop_counts = (
                normalized["raw_prop_type"].fillna("").astype(str).value_counts().to_dict()
                if "raw_prop_type" in normalized.columns
                else {}
            )
            raw_market_type_counts = (
                normalized["raw_market_type"].fillna("").astype(str).value_counts().to_dict()
                if "raw_market_type" in normalized.columns
                else {}
            )
            unsupported_milestone_count = int(raw_market_type_counts.get("milestone", 0))
            print(f"[COUNT] odds_by_raw_prop_type={raw_prop_counts}", flush=True)
            print(f"[COUNT] odds_by_market_type={raw_market_type_counts}", flush=True)
            print(f"[COUNT] unsupported_milestone_count={unsupported_milestone_count}", flush=True)

        if normalized.empty:
            self.last_odds_status = "empty_response"
            self.last_odds_message = "Player prop odds endpoint returned no usable rows."
            print(f"[DIAGNOSIS] active odds fetch returned zero rows", flush=True)
        else:
            self.last_odds_status = "ok"
            unresolved = int(normalized["unresolved_reason"].notna().sum())
            print(
                f"[COUNT] odds_normalized_rows={len(normalized)} unresolved={unresolved}",
                flush=True,
            )

        return normalized

    def _normalize_game_odds(self, data: list[dict[str, Any]]) -> pd.DataFrame:
        """Normalize raw game odds into a defensive DataFrame."""
        rows: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            game_id = item.get("game_id")
            row: dict[str, Any] = {"game_id": game_id}

            # Try to extract bookmaker odds from nested structure
            bookmakers = item.get("bookmakers") or item.get("sportsbooks") or []
            if bookmakers:
                first = bookmakers[0] if isinstance(bookmakers, list) else None
                if isinstance(first, dict):
                    row["sportsbook"] = first.get("key") or first.get("title")
                    markets = first.get("markets") or []
                    for m in markets:
                        if not isinstance(m, dict):
                            continue
                        mkey = m.get("key") or m.get("market_type")
                        if mkey in ("spreads", "totals", "moneyline", "h2h"):
                            outcomes = m.get("outcomes") or []
                            for o in outcomes:
                                if isinstance(o, dict):
                                    oname = o.get("name") or o.get("point")
                                    oprice = o.get("price") or o.get("odds")
                                    row[f"{mkey}_{oname}"] = oprice
            else:
                # Flatten fields directly present
                row["sportsbook"] = item.get("sportsbook") or item.get("bookmaker_key")
                for k in ["spread_home", "spread_away", "total_over", "total_under", "moneyline_home", "moneyline_away", "home_spread", "away_spread"]:
                    if k in item:
                        row[k] = item[k]

            rows.append(row)

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _normalize_player_props(self, data: list[dict[str, Any]]) -> pd.DataFrame:
        """Normalize raw player prop odds into a defensive DataFrame."""
        rows: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {
                "game_id": item.get("game_id"),
                "player_id": item.get("player_id"),
                "prop_type": item.get("prop_type"),
                "line_value": item.get("line_value"),
                "vendor": item.get("vendor"),
            }
            # Extract market details
            market = item.get("market") or {}
            if isinstance(market, dict):
                row["market_type"] = market.get("type")
                row["over_odds"] = market.get("over_odds")
                row["under_odds"] = market.get("under_odds")
                row["odds"] = market.get("odds")
            rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _get_player_prop_rows(
        self,
        game_id: int,
        player_lookup: Optional[dict[int, dict[str, Any]]] = None,
        diagnostics: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch and normalize player prop rows for a specific game.
        """
        logger = logging.getLogger("courtvision_ai")
        try:
            payload = self._get(
                f"{NBA_V2}/odds/player_props",
                {"game_id": game_id},
            )
            data = payload.get("data", [])
            if not isinstance(data, list):
                return []
            return data
        except Exception as exc:
            logger.warning("player_props_fetch_failed game_id=%s error=%s", game_id, exc)
            return []

    def _normalize_injuries(self, injuries_raw: pd.DataFrame) -> pd.DataFrame:
        """Normalize injuries DataFrame from SDK or HTTP source."""
        if injuries_raw.empty:
            return injuries_raw
        rename_map = {
            "player.id": "player_id",
            "player.first_name": "first_name",
            "player.last_name": "last_name",
            "player.team_id": "team_id",
        }
        return injuries_raw.rename(columns={k: v for k, v in rename_map.items() if k in injuries_raw.columns})

    def _build_player_prop_identity_lookup(
        self,
        game_date: str,
        requested_game_ids: Optional[list[int]] = None,
    ) -> dict[int, dict[str, Any]]:
        """Build a lookup of player_id -> player identity info from baselines/players list."""
        logger = logging.getLogger("courtvision_ai")
        player_lookup: dict[int, dict[str, Any]] = {}

        try:
            games = self.get_games(game_date)
        except Exception as exc:
            logger.warning("get_odds_player_lookup_games_failed game_date=%s error=%s", game_date, exc)
            print(f"[DIAGNOSIS] player_lookup early-exit: get_games raised {type(exc).__name__}: {exc}", flush=True)
            return {}

        print(f"[COUNT] player_lookup_games_fetched rows={len(games)} columns={list(games.columns) if not games.empty else []}", flush=True)
        if games.empty:
            logger.warning("get_odds_player_lookup_no_games game_date=%s", game_date)
            print(f"[DIAGNOSIS] player_lookup early-exit: get_games returned empty DataFrame for date={game_date}", flush=True)
            return {}

        team_ids: set[int] = set()
        if requested_game_ids:
            for gid in requested_game_ids:
                game_row = games[games["id"] == gid]
                if not game_row.empty:
                    row = game_row.iloc[0]
                    for nested_key, flat_key in (
                        ("home_team", "home_team_id"),
                        ("visitor_team", "visitor_team_id"),
                    ):
                        nested = row.get(nested_key)
                        if isinstance(nested, dict):
                            tid = int(nested.get("id", 0) or 0)
                        else:
                            tid = int(row.get(flat_key, 0) or 0)
                        if tid:
                            team_ids.add(tid)

        print(f"[COUNT] player_lookup_team_ids={sorted(team_ids)}", flush=True)
        if not team_ids:
            logger.warning(
                "get_odds_player_lookup_missing_team_ids game_date=%s requested_game_ids=%s",
                game_date,
                sorted(requested_game_ids),
            )
            return {}

        try:
            sdk = BalldontlieAPI(api_key=self.api_key)
            players_api = sdk.nba.players
        except Exception as exc:
            logger.warning("get_odds_player_lookup_sdk_failed error=%s", exc)
            return {}

        for team_id in team_ids:
            try:
                players_page = players_api.list(team_ids=[team_id], per_page=100)
                players_data = getattr(players_page, "data", None)
                if players_data is None and isinstance(players_page, dict):
                    players_data = players_page.get("data", [])
                if not players_data:
                    continue
                for p in players_data:
                    if isinstance(p, dict):
                        pid = int(p.get("id", 0) or 0)
                        first = str(p.get("first_name", "") or "").strip()
                        last = str(p.get("last_name", "") or "").strip()
                        team_info = p.get("team") if isinstance(p.get("team"), dict) else None
                        team_abbr = str((team_info or {}).get("abbreviation", "") or "").strip()
                    else:
                        pid = int(getattr(p, "id", 0) or 0)
                        first = str(getattr(p, "first_name", "") or "").strip()
                        last = str(getattr(p, "last_name", "") or "").strip()
                        team_info = getattr(p, "team", None)
                        team_abbr = str(getattr(team_info, "abbreviation", "") or "").strip() if team_info else ""
                    if not pid:
                        continue
                    player_name = f"{first} {last}".strip()
                    player_lookup[pid] = {
                        "player_id": pid,
                        "player_name": player_name,
                        "team_id": team_id,
                        "team_abbr": team_abbr,
                    }
            except Exception as exc:
                logger.warning("get_odds_player_lookup_team_failed team_id=%s error=%s", team_id, exc)
                continue

        return player_lookup
        player_lookup = self._build_player_prop_identity_lookup(game_date, game_ids or [])
        print(f"[DEBUG_CALLER] player_lookup returned with {len(player_lookup)} entries", flush=True)
        if player_lookup:
            sample_keys = list(player_lookup.keys())[:3]
            print(f"[DEBUG_CALLER] sample keys: {sample_keys}, types: {[type(k).__name__ for k in sample_keys]}", flush=True)
        prop_errors: list[str] = []
        valid_game_ids = [gid for gid in game_ids if gid is not None]
        print(f"[COUNT] games_for_odds={len(valid_game_ids)}", flush=True)
        for raw_game_id in game_ids or []:
            try:
                game_id = int(raw_game_id)
            except (TypeError, ValueError):
                continue

            try:
                prop_stage: dict[str, Any] = {}
                prop_rows = self._get_player_prop_rows(
                    game_id,
                    player_lookup=player_lookup,
                    diagnostics=prop_stage,
                )
                stage_counts["player_prop_raw_rows"] += int(prop_stage.get("raw_count", 0) or 0)
                stage_counts["player_prop_normalize_called"] += int(prop_stage.get("normalize_called", 0) or 0)
                stage_counts["player_prop_normalized_rows"] += int(prop_stage.get("normalize_succeeded", 0) or 0)
                stage_counts["player_prop_normalized_with_name"] += int(prop_stage.get("normalize_named", 0) or 0)
                self._extend_sample_rows(raw_player_prop_samples, prop_stage.get("raw_samples", []), limit=5)
                self._extend_sample_rows(normalized_player_prop_samples, prop_stage.get("normalized_samples", []), limit=5)
                rows.extend(prop_rows)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                prop_errors.append(f"player_props_game_{game_id}:http_{status_code or 'error'}")
            except Exception as exc:
                prop_errors.append(f"player_props_game_{game_id}:{type(exc).__name__}")

        print(f"[COUNT] total_player_prop_rows={len(rows)}", flush=True)
        odds_df = pd.DataFrame(rows) if rows else pd.DataFrame()
        player_name_sample: list[str] = []
        overall_null_count = 0
        if not odds_df.empty and "player_name" in odds_df.columns:
            player_name_series = odds_df["player_name"].fillna("").astype(str).str.strip()
            player_name_sample = player_name_series.head(10).tolist()
            overall_null_count = int((player_name_series == "").sum())

        player_prop_df = odds_df.head(0).copy()
        player_prop_null_count = 0
        player_prop_name_sample: list[str] = []
        final_player_prop_samples: list[dict[str, Any]] = []
        if not odds_df.empty and "raw_market_name" in odds_df.columns:
            player_prop_df = odds_df[
                odds_df["raw_market_name"].astype(str).isin(self._supported_player_markets())
            ].copy()
            if not player_prop_df.empty and "player_name" in player_prop_df.columns:
                player_prop_name_series = player_prop_df["player_name"].fillna("").astype(str).str.strip()
                player_prop_name_sample = player_prop_name_series.head(10).tolist()
                player_prop_null_count = int((player_prop_name_series == "").sum())
            final_player_prop_samples = self._sample_row_dicts(
                player_prop_df.to_dict(orient="records") if not player_prop_df.empty else [],
                limit=5,
            )

        logger.info(
            "get_odds_final_frame rows=%d columns=%s player_name_sample=%s overall_player_name_null_or_empty=%d player_prop_rows=%d player_prop_player_name_sample=%s",
            len(odds_df),
            odds_df.columns.tolist(),
            player_name_sample,
            overall_null_count,
            len(player_prop_df),
            player_prop_name_sample,
        )
        logger.info("get_odds_stage_counts %s", stage_counts)

        if stage_counts["player_prop_raw_rows"] > 0 and stage_counts["player_prop_normalized_rows"] == 0:
            logger.warning(
                "get_odds_no_player_props_survived stage_counts=%s final_columns=%s raw_player_prop_samples=%s normalized_player_prop_samples=%s final_row_samples=%s",
                stage_counts,
                odds_df.columns.tolist(),
                raw_player_prop_samples,
                normalized_player_prop_samples,
                self._sample_row_dicts(rows),
            )

        if stage_counts["player_prop_normalized_rows"] > 0:
            if player_prop_df.empty:
                logger.error(
                    "get_odds_player_prop_subset_missing stage_counts=%s final_columns=%s raw_player_prop_samples=%s normalized_player_prop_samples=%s final_row_samples=%s",
                    stage_counts,
                    odds_df.columns.tolist(),
                    raw_player_prop_samples,
                    normalized_player_prop_samples,
                    self._sample_row_dicts(rows),
                )
                raise AssertionError(
                    "get_odds normalized player props but returned no player-prop rows in the final DataFrame"
                )
            if len(player_prop_df) > 0 and player_prop_null_count / len(player_prop_df) > 0.5:
                normalized_rows_had_names = bool(stage_counts["player_prop_normalized_with_name"])
                if not normalized_rows_had_names:
                    stage_hint = "normalize_player_prop_row returned blank player_name values"
                else:
                    stage_hint = "player_name existed after normalization but is missing in final returned player-prop rows"
                logger.error(
                    "get_odds_player_name_failure_counts raw_game_odds_rows=%d raw_player_prop_rows=%d normalized_player_prop_rows=%d normalized_player_prop_rows_with_name=%d prop_subset_null_or_empty=%d prop_subset_rows=%d final_columns=%s stage_hint=%s",
                    int(stage_counts["raw_game_odds_rows"]),
                    int(stage_counts["player_prop_raw_rows"]),
                    int(stage_counts["player_prop_normalized_rows"]),
                    int(stage_counts["player_prop_normalized_with_name"]),
                    player_prop_null_count,
                    len(player_prop_df),
                    odds_df.columns.tolist(),
                    stage_hint,
                )
                logger.error(
                    "get_odds_player_name_failure_raw_player_prop_samples=%s",
                    raw_player_prop_samples,
                )
                logger.error(
                    "get_odds_player_name_failure_normalized_player_prop_samples=%s",
                    normalized_player_prop_samples,
                )
                logger.error(
                    "get_odds_player_name_failure_final_player_prop_samples=%s",
                    final_player_prop_samples,
                )
                raise AssertionError(
                    "get_odds returned player-prop rows with >50% null/empty player_name values "
                    f"(null_count={player_prop_null_count}, total_prop_rows={len(player_prop_df)}, "
                    f"normalized_rows_had_names={normalized_rows_had_names}, stage_hint={stage_hint})"
                )

        if rows:
            self.last_odds_status = "ok" if not prop_errors else "partial_props"
            message = (
                f"Loaded {len(rows)} normalized market rows "
                f"(game_rows={stage_counts['normalized_game_rows']}, "
                f"player_prop_rows={stage_counts['player_prop_normalized_rows']})."
            )
            if prop_errors:
                message = f"{message} Player props degraded for {len(prop_errors)} game(s)."
            self.last_odds_message = message
            return odds_df
        else:
            self.last_odds_status = "no_markets_returned"
            if prop_errors:
                self.last_odds_message = (
                    "No normalized odds rows were loaded. "
                    f"Player props errors: {', '.join(prop_errors[:3])}."
                )
            else:
                self.last_odds_message = "Odds endpoint returned no market rows for this date."
        return pd.DataFrame()

    def _get_player_prop_rows(
        self,
        game_id: int,
        player_lookup: Optional[dict[int, dict[str, Any]]] = None,
        diagnostics: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        logger = logging.getLogger("courtvision_ai")
        print(f"[COUNT] player_prop_request game_id={game_id}", flush=True)
        
        payload = self._get(
            f"{NBA_V2}/odds/player_props",
            {"game_id": game_id},
        )
        data = payload.get("data", [])
        row_count = len(data) if isinstance(data, list) else 0
        print(f"[COUNT] player_prop_rows_returned game_id={game_id} rows={row_count}", flush=True)
        
        if row_count == 0:
            print(f"[WARNING] no_player_props_for_game game_id={game_id}", flush=True)
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(
                {
                    "raw_count": 0,
                    "normalize_called": 0,
                    "normalize_succeeded": 0,
                    "normalize_named": 0,
                    "sample_has_nested_player": False,
                    "sample_has_player_name": False,
                    "raw_samples": [],
                    "normalized_samples": [],
                    # Player lookup diagnostics
                    "player_lookup_size": len(player_lookup) if player_lookup else 0,
                    "odds_rows_with_player_id": 0,
                    "odds_rows_resolved_to_player_name": 0,
                    "odds_player_lookup_misses": 0,
                    "lookup_sample_player_ids": [],
                }
            )
        if not isinstance(data, list):
            return []

        rows: list[dict[str, Any]] = []
        raw_count = len(data)
        raw_samples = self._sample_row_dicts(data)
        sample_has_nested_player = any(
            isinstance(prop, dict) and isinstance(prop.get("player"), dict)
            for prop in data[:3]
        )
        sample_has_player_name = any(
            isinstance(prop, dict) and bool(str(prop.get("player_name") or "").strip())
            for prop in data[:3]
        )
        if diagnostics is not None:
            diagnostics.update(
                {
                    "raw_count": raw_count,
                    "sample_has_nested_player": sample_has_nested_player,
                    "sample_has_player_name": sample_has_player_name,
                    "raw_samples": raw_samples,
                }
            )
        # Debug: Print raw sample keys to understand API response structure
        if raw_samples and len(raw_samples) > 0:
            first_sample = raw_samples[0]
            if isinstance(first_sample, dict):
                sample_keys = list(first_sample.keys())
                player_data = first_sample.get("player")
                print(f"[DEBUG] Raw odds sample keys: {sample_keys}")
                print(f"[DEBUG] Player data type: {type(player_data)}")
                if isinstance(player_data, dict):
                    print(f"[DEBUG] Player keys: {list(player_data.keys())}")
        
        logger.info(
            "get_odds_player_props_raw game_id=%d raw_rows=%d sample_has_nested_player=%s sample_has_player_name=%s sample_rows=%s",
            game_id,
            raw_count,
            sample_has_nested_player,
            sample_has_player_name,
            raw_samples,
        )
        for index, sample_row in enumerate(data[:5]):
            if not isinstance(sample_row, dict):
                logger.info(
                    "get_odds_player_props_raw_sample game_id=%d index=%d repr=%r player_type=%s keys=%s",
                    game_id,
                    index,
                    sample_row,
                    "<non-dict>",
                    [],
                )
                continue
            logger.info(
                "get_odds_player_props_raw_sample game_id=%d index=%d repr=%r player_type=%s keys=%s",
                game_id,
                index,
                sample_row,
                type(sample_row.get("player")),
                sorted(sample_row.keys()),
            )
        # Compact lookup-vs-odds intersection summary (replaces previous
        # per-row [DEBUG_ODDS]/[DEBUG_INTERSECTION]/[DEBUG_JOIN] dumps).
        lookup_size = len(player_lookup) if player_lookup else 0
        sample_odds_ids: list[Any] = []
        for sample in data[:10]:
            if isinstance(sample, dict) and "player_id" in sample:
                sample_odds_ids.append(sample["player_id"])
        intersection_count = 0
        if player_lookup and sample_odds_ids:
            odds_ids_int: set[int] = set()
            for pid in sample_odds_ids:
                try:
                    odds_ids_int.add(int(pid))
                except (ValueError, TypeError):
                    pass
            lookup_keys_int: set[int] = set()
            for k in player_lookup.keys():
                try:
                    lookup_keys_int.add(int(k))
                except (ValueError, TypeError):
                    pass
            intersection_count = len(odds_ids_int & lookup_keys_int)
        print(
            f"[COUNT] odds_lookup_join lookup_size={lookup_size} "
            f"sample_odds_ids={len(sample_odds_ids)} sample_intersection={intersection_count}",
            flush=True,
        )
        
        for prop in data:
            if diagnostics is not None:
                diagnostics["normalize_called"] = int(diagnostics.get("normalize_called", 0) or 0) + 1
                # Track player_id presence
                prop_player_id_raw = prop.get("player_id") if isinstance(prop, dict) else None
                prop_player_id = self._coerce_int(prop_player_id_raw) if isinstance(prop, dict) else None
                prop_player_id_str = str(prop_player_id_raw) if isinstance(prop, dict) and prop_player_id_raw is not None else None
                
                if prop_player_id is not None:
                    diagnostics["odds_rows_with_player_id"] = int(diagnostics.get("odds_rows_with_player_id", 0) or 0) + 1
                    # Track lookup misses for diagnostics - try both int and str keys
                    lookup_hit = False
                    if player_lookup:
                        lookup_hit = prop_player_id in player_lookup
                        if not lookup_hit and prop_player_id_str:
                            lookup_hit = prop_player_id_str in player_lookup
                    
                    if player_lookup and not lookup_hit:
                        diagnostics["odds_player_lookup_misses"] = int(diagnostics.get("odds_player_lookup_misses", 0) or 0) + 1
                        if len(diagnostics.get("lookup_sample_player_ids", [])) < 10:
                            diagnostics["lookup_sample_player_ids"].append(prop_player_id)
            
            row = self._normalize_player_prop_row(prop, player_lookup=player_lookup)
            if row:
                rows.append(row)
                if diagnostics is not None:
                    diagnostics["normalize_succeeded"] = int(diagnostics.get("normalize_succeeded", 0) or 0) + 1
                    if str(row.get("player_name") or "").strip():
                        diagnostics["normalize_named"] = int(diagnostics.get("normalize_named", 0) or 0) + 1
                        diagnostics["odds_rows_resolved_to_player_name"] = int(diagnostics.get("odds_rows_resolved_to_player_name", 0) or 0) + 1

        normalized_samples = self._sample_row_dicts(rows, limit=5)
        if diagnostics is not None:
            diagnostics["normalized_samples"] = normalized_samples

        logger.info(
            "get_odds_player_props_normalized game_id=%d normalize_called=%d normalized_rows=%d",
            game_id,
            int(diagnostics.get("normalize_called", raw_count) if diagnostics is not None else raw_count),
            len(rows),
        )
        logger.info(
            "get_odds_player_props_normalized_samples game_id=%d normalized_named=%d sample_rows=%s",
            game_id,
            int(diagnostics.get("normalize_named", 0) if diagnostics is not None else 0),
            normalized_samples,
        )
        
        # Print [COUNT] diagnostics for visibility
        if diagnostics is not None:
            print(f"[COUNT] player_lookup_size={diagnostics.get('player_lookup_size', 0)}")
            print(f"[COUNT] odds_rows_with_player_id={diagnostics.get('odds_rows_with_player_id', 0)}")
            print(f"[COUNT] odds_rows_resolved_to_player_name={diagnostics.get('odds_rows_resolved_to_player_name', 0)}")
            print(f"[COUNT] odds_player_lookup_misses={diagnostics.get('odds_player_lookup_misses', 0)}")
            
            # If resolution is 0, print diagnostic info
            if diagnostics.get('odds_rows_resolved_to_player_name', 0) == 0 and diagnostics.get('odds_rows_with_player_id', 0) > 0:
                print(f"[DIAGNOSIS] Player lookup failed - sample missing IDs: {diagnostics.get('lookup_sample_player_ids', [])[:10]}")
                if player_lookup:
                    sample_lookup_keys = list(player_lookup.keys())[:10]
                    print(f"[DIAGNOSIS] Lookup has keys: {sample_lookup_keys}")
        
        return rows

    def _supported_player_markets(self) -> set[str]:
        """Return set of stat categories supported for player prop scoring."""
        return {"player_points", "player_rebounds", "player_assists", "player_3pt_made", 
                "player_steals", "player_blocks"}

    def _vendor_allowed(self, vendor: Any) -> bool:
        if not self.preferred_vendors:
            return True
        return str(vendor or "").strip().lower() in self.preferred_vendors

    def _normalize_game_odds_rows(self, market: Any) -> list[dict[str, Any]]:
        if not isinstance(market, dict):
            return []

        vendor = market.get("vendor")
        if not self._vendor_allowed(vendor):
            return []

        game_id = market.get("game_id")
        rows: list[dict[str, Any]] = []
        for side, odds_key in [("home", "moneyline_home_odds"), ("away", "moneyline_away_odds")]:
            odds_price = market.get(odds_key)
            if odds_price is None:
                continue

            rows.append(
                {
                    "game_id": game_id,
                    "bookmaker": vendor,
                    "raw_market_name": "moneyline",
                    "player_id": None,
                    "player_name": None,
                    "team": None,
                    "line": None,
                    "over_odds": None,
                    "under_odds": None,
                    "odds": odds_price,
                    "side": side,
                    "selection": side,
                }
            )

        return rows

    def _normalize_market_alias(self, raw_market_name: Any) -> Optional[str]:
        return runtime_normalize_market_alias(raw_market_name)

    def _normalize_player_prop_row(
        self,
        market: dict[str, Any],
        player_lookup: Optional[dict[int, dict[str, Any]]] = None,
    ) -> Optional[dict[str, Any]]:
        print(f"[DEBUG_NORMALIZE] called with player_lookup_size={len(player_lookup) if player_lookup else 0}", flush=True)
        if not isinstance(market, dict):
            return None

        vendor = market.get("vendor")
        if not self._vendor_allowed(vendor):
            return None

        # Support both nested market structure and flat prop_type
        market_detail = market.get("market", {}) if isinstance(market.get("market"), dict) else {}
        
        # Accept common over/under style bet types (from nested market if present)
        bet_type = str(market_detail.get("type", "")).strip().lower()
        if bet_type and bet_type not in {"over_under", "milestone", "point_spread", "point_spread_alternate", 
                                         "over", "under"}:
            return None

        # Normalize the stat category (e.g. "points" -> "player_points")
        # Support both flat prop_type and nested market.subtype
        prop_type = market.get("prop_type") or market_detail.get("subtype") or market_detail.get("type")
        normalized_market = self._normalize_market_alias(prop_type)
        if normalized_market is None:
            return None
        
        # Skip unsupported combo props for now
        if normalized_market not in self._supported_player_markets():
            return None

        # Get player_id from odds row - this is the ONLY player identifier in the API response
        raw_player_id = market.get("player_id")
        player_id = self._coerce_int(raw_player_id)
        if player_id is None:
            return None
        player_id_str = str(raw_player_id) if raw_player_id is not None else None
        
        # Look up player identity from pre-built lookup (primary source).
        # Try both int and str keys to handle type mismatches.
        resolved_identity: dict[str, Any] = {}
        lookup_hit = False
        if player_lookup and player_id is not None:
            resolved_identity = player_lookup.get(player_id, {})
            lookup_hit = bool(resolved_identity)
            if not lookup_hit and player_id_str:
                resolved_identity = player_lookup.get(player_id_str, {})
                lookup_hit = bool(resolved_identity)

        player_name = str(resolved_identity.get("player_name") or "").strip()

        # One-shot summary on the first call instead of per-row debug spam.
        if not getattr(self, "_player_join_logged", False):
            self._player_join_logged = True
            print(
                f"[COUNT] player_join_first_row raw_player_id={raw_player_id} "
                f"coerced={player_id} lookup_size={len(player_lookup) if player_lookup else 0} "
                f"lookup_hit={lookup_hit} resolved_name={player_name!r}",
                flush=True,
            )
        
        # If lookup failed, preserve player_id and record diagnostic, but don't silently fail
        if not player_name:
            logger.warning(
                "player_lookup_miss player_id=%s (type=%s) lookup_size=%d lookup_has_int_keys=%s lookup_has_str_keys=%s",
                player_id,
                type(raw_player_id).__name__,
                len(player_lookup) if player_lookup else 0,
                any(isinstance(k, int) for k in (player_lookup or {}).keys()) if player_lookup else False,
                any(isinstance(k, str) for k in (player_lookup or {}).keys()) if player_lookup else False,
            )
            # Still return the row with player_id preserved for diagnostics
            # This allows downstream code to see missing_player_lookup flag
            player_name = None
        
        # Log successful lookup
        logger.debug(
            "player_lookup_hit player_id=%s player_name=%s",
            player_id,
            player_name,
        )

        team = market.get("team", {}) if isinstance(market.get("team"), dict) else {}

        # Player prop identity lookup is the source of truth for team assignment.
        team_abbr = str(resolved_identity.get("team_abbr") or "").strip().upper()

        # Fallback to raw market data only if lookup failed.
        if not team_abbr:
            team_abbr = (
                str(team.get("abbreviation") or team.get("abbr") or "").strip().upper()
                or str(market.get("team_abbr") or "").strip().upper()
            )

        if player_name and team_abbr:
            logging.getLogger("courtvision_ai").info(
                "identity_fix name=%s team=%s source=%s",
                player_name,
                team_abbr,
                "lookup" if resolved_identity else "raw",
            )

        row = {
            "game_id": market.get("game_id"),
            "bookmaker": vendor,
            "raw_market_name": normalized_market,
            "player_id": player_id,
            "player_name": player_name,
            "missing_player_lookup": player_name is None,  # Flag for diagnostics
            "team": team_abbr,
            "line": market.get("line_value"),
            "over_odds": market_detail.get("over_odds"),
            "under_odds": market_detail.get("under_odds"),
            "odds": market_detail.get("odds"),
            "side": market_detail.get("type"),
            "selection": None,
        }
        return row

    @staticmethod
    def _sample_row_dicts(rows: Any, limit: int = 5) -> list[dict[str, Any]]:
        sample_rows: list[dict[str, Any]] = []
        iterable = rows if isinstance(rows, list) else list(rows) if rows is not None else []
        for row in iterable[:limit]:
            if isinstance(row, dict):
                sample_rows.append(dict(row))
            else:
                sample_rows.append({"value": repr(row)})
        return sample_rows

    @staticmethod
    def _extend_sample_rows(
        target: list[dict[str, Any]],
        rows: Any,
        limit: int = 5,
    ) -> None:
        if len(target) >= limit or rows is None:
            return
        iterable = rows if isinstance(rows, list) else list(rows)
        for row in iterable:
            if len(target) >= limit:
                break
            if isinstance(row, dict):
                target.append(dict(row))
            else:
                target.append({"value": repr(row)})

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value in (None, "", False):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(str(value).strip()))
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _name_from_mapping(mapping: Any) -> str:
        if not isinstance(mapping, dict):
            return ""
        first_name = str(
            mapping.get("first_name")
            or mapping.get("firstName")
            or ""
        ).strip()
        last_name = str(
            mapping.get("last_name")
            or mapping.get("lastName")
            or ""
        ).strip()
        return (
            f"{first_name} {last_name}".strip()
            or str(
                mapping.get("full_name")
                or mapping.get("fullName")
                or mapping.get("display_name")
                or mapping.get("displayName")
                or mapping.get("name")
                or ""
            ).strip()
        )

    def _extract_player_prop_name(self, market: Mapping[str, Any]) -> str:
        """Extract player name from various odds API formats."""
        # Try nested player dict structures first
        for key in ("player", "participant", "athlete", "entity", "competitor"):
            value = market.get(key)
            if isinstance(value, dict):
                candidate = self._name_from_mapping(value)
                if candidate:
                    return candidate
            if isinstance(value, str) and value.strip():
                return value.strip()
        
        # Try top-level player name fields
        top_level_name = (
            str(market.get("player_name") or "").strip()
            or str(market.get("playerName") or "").strip()
            or str(market.get("full_name") or market.get("fullName") or "").strip()
            or str(market.get("display_name") or market.get("displayName") or "").strip()
            or str(market.get("name") or "").strip()
            or str(market.get("label") or "").strip()
            or str(market.get("description") or "").strip()
        )
        if top_level_name:
            return top_level_name
        
        # Fallback: try to extract from the whole dict
        return self._name_from_mapping(dict(market))

    def _build_player_prop_identity_lookup(
        self,
        game_date: str,
        game_ids: Sequence[int] | None = None,
    ) -> dict[int, dict[str, Any]]:
        logger = logging.getLogger("courtvision_ai")
        if not self.api_key or BalldontlieAPI is None:
            print("[DIAGNOSIS] player_lookup early-exit: missing api_key or BalldontlieAPI", flush=True)
            return {}

        try:
            games = self.get_games(game_date)
        except Exception as exc:
            print(f"[WARNING] get_odds_player_lookup_games_failed game_date={game_date} error={exc}", flush=True)
            return {}

        if games.empty:
            print("[DIAGNOSIS] player_lookup early-exit: get_games returned empty", flush=True)
            return {}

        requested_game_ids = {
            item for item in (self._coerce_int(value) for value in (game_ids or [])) if item is not None
        }
        team_ids: set[int] = set()
        for row in games.to_dict(orient="records"):
            game_id = self._coerce_int(row.get("id") or row.get("game_id"))
            if requested_game_ids and game_id not in requested_game_ids:
                continue
            for nested_key, flat_key in (
                ("home_team", "home_team_id"),
                ("visitor_team", "visitor_team_id"),
            ):
                nested_team = row.get(nested_key)
                if isinstance(nested_team, dict):
                    team_id = self._coerce_int(nested_team.get("id"))
                else:
                    team_id = self._coerce_int(row.get(flat_key))
                if team_id is not None:
                    team_ids.add(team_id)

        print(f"[COUNT] player_lookup_team_ids={len(team_ids)}", flush=True)
        if not team_ids:
            print(f"[WARNING] get_odds_player_lookup_missing_team_ids game_date={game_date} requested_game_ids={sorted(requested_game_ids)}", flush=True)
            return {}

        try:
            api = BalldontlieAPI(api_key=self.api_key)
        except Exception as exc:
            print(f"[WARNING] get_odds_player_lookup_sdk_failed error={exc}", flush=True)
            return {}

        lookup: dict[int, dict[str, Any]] = {}
        cursor: Optional[Any] = None
        while True:
            try:
                if cursor is None:
                    page = api.nba.players.list_active(per_page=100)
                else:
                    page = api.nba.players.list_active(per_page=100, cursor=cursor)
            except Exception as exc:
                logger.warning("get_odds_player_lookup_page_failed cursor=%s error=%s", cursor, exc)
                break

            rows = page.data if hasattr(page, "data") else page
            if not isinstance(rows, list):
                rows = list(rows) if rows is not None else []

            for row in rows:
                if isinstance(row, dict):
                    team = row.get("team", {}) if isinstance(row.get("team"), dict) else {}
                    team_id = self._coerce_int(team.get("id"))
                    if team_id not in team_ids:
                        continue
                    player_id = self._coerce_int(row.get("id"))
                    if player_id is None:
                        continue
                    player_name = self._name_from_mapping(row)
                    team_abbr = str(team.get("abbreviation") or team.get("abbr") or "").strip().upper()
                else:
                    team = getattr(row, "team", None)
                    team_id = self._coerce_int(getattr(team, "id", None) if team is not None else None)
                    if team_id not in team_ids:
                        continue
                    player_id = self._coerce_int(getattr(row, "id", None))
                    if player_id is None:
                        continue
                    first_name = str(getattr(row, "first_name", "")).strip()
                    last_name = str(getattr(row, "last_name", "")).strip()
                    player_name = f"{first_name} {last_name}".strip()
                    team_abbr = str(getattr(team, "abbreviation", "") if team is not None else "").strip().upper()

                lookup[player_id] = {
                    "player_name": player_name,
                    "team_abbr": team_abbr,
                }

            meta = getattr(page, "meta", None)
            next_cursor = None
            if meta is not None:
                if isinstance(meta, dict):
                    next_cursor = meta.get("next_cursor")
                else:
                    next_cursor = getattr(meta, "next_cursor", None)
            if next_cursor in (None, "", 0):
                break
            cursor = next_cursor

        # Summary diagnostics for lookup (full sample retained in logger.info below)
        sample_keys = list(lookup.keys())[:5]
        print(f"[COUNT] player_lookup_built entries={len(lookup)} sample_keys={sample_keys}", flush=True)

        logger.info(
            "get_odds_player_lookup game_date=%s team_ids=%s lookup_entries=%d sample=%s",
            game_date,
            sorted(team_ids),
            len(lookup),
            self._sample_row_dicts(
                [{"player_id": player_id, **details} for player_id, details in list(lookup.items())[:5]],
                limit=5,
            ),
        )
        return lookup

    def _normalize_market_row(
        self,
        game_id: Any,
        source_name: Optional[str],
        market: Any,
    ) -> Optional[dict[str, Any]]:
        if not isinstance(market, dict):
            return None

        market_name = (
            market.get("market_type")
            or market.get("market")
            or market.get("key")
            or market.get("name")
        )

        player_name = market.get("player_name") or market.get("player")
        team = market.get("team")
        line = market.get("line") or market.get("points") or market.get("value")
        over_odds = market.get("over_odds")
        under_odds = market.get("under_odds")
        odds = market.get("odds")
        side = market.get("side")
        selection = market.get("selection")
        bookmaker = market.get("bookmaker") or source_name

        outcomes = market.get("outcomes")
        if isinstance(outcomes, list) and outcomes:
            over_price = None
            under_price = None
            moneyline_side = None
            moneyline_price = None

            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                label = str(
                    outcome.get("name")
                    or outcome.get("label")
                    or outcome.get("selection")
                    or ""
                ).lower()
                price = outcome.get("price") or outcome.get("odds")
                if label == "over":
                    over_price = price
                elif label == "under":
                    under_price = price
                elif label:
                    moneyline_side = outcome.get("name") or outcome.get("selection")
                    moneyline_price = price

            if over_odds is None:
                over_odds = over_price
            if under_odds is None:
                under_odds = under_price
            if selection is None and moneyline_side is not None:
                selection = moneyline_side
            if odds is None and moneyline_price is not None:
                odds = moneyline_price

        return {
            "game_id": game_id,
            "bookmaker": bookmaker,
            "raw_market_name": market_name,
            "player_name": player_name,
            "team": team,
            "line": line,
            "over_odds": over_odds,
            "under_odds": under_odds,
            "odds": odds,
            "side": side,
            "selection": selection,
        }


CourtVisionAIClient = BallDontLieClient


class ProviderClientAdapter:
    """
    Adapter to make ProviderManager work like BallDontLieClient.
    Provides SportsDataIO primary with BallDontLie fallback.
    """

    def __init__(self, api_key: str | None = None, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger(__name__)
        self._provider = None
        self._primary_source = None
        # Always initialize fallback so it's available for per-domain fallback
        self._fallback_client = BallDontLieClient(api_key=api_key)
        self._init_provider()

    def _init_provider(self) -> None:
        """Initialize provider with SportsDataIO primary, BallDontLie fallback."""
        try:
            from courtvision.clients.provider_manager import ProviderManager
            from courtvision.config import Settings

            self.logger.info("[provider] attempting sportsdataio")
            settings = Settings()
            self._provider = ProviderManager(settings)
            self._primary_source = "sportsdataio"
            self.logger.info("[provider] sportsdataio ready")
        except Exception as e:
            import traceback
            self.logger.warning(f"[provider] sportsdataio init failed: {e}")
            self.logger.debug(f"[provider] sportsdataio traceback: {traceback.format_exc()}")
            self._init_fallback()

    def _init_fallback(self) -> None:
        """Initialize BallDontLie as primary (fallback mode)."""
        self.logger.info("[provider] falling back to balldontlie")
        self._provider = None
        self._primary_source = "balldontlie"
        # Fallback client already initialized in __init__

    def get_games(self, date_str: str) -> pd.DataFrame:
        """Get games for date. Try SportsDataIO, fallback to BallDontLie."""
        if self._provider and self._primary_source == "sportsdataio":
            try:
                games = self._provider.get_games_by_date(date_str)
                self.logger.info(f"[provider] sportsdataio success games={len(games) if not games.empty else 0}")
                return games
            except Exception as e:
                self.logger.warning(f"[provider] sportsdataio failed: {e}")
                return self._fallback_client.get_games(date_str)
        else:
            games = self._fallback_client.get_games(date_str)
            self.logger.info(f"[provider] balldontlie success games={len(games) if not games.empty else 0}")
            return games

    def get_odds(self, date_str: str, game_ids: list[int] | None = None) -> pd.DataFrame:
        """Get odds. Try SportsDataIO, fallback to BallDontLie."""
        if self._provider and self._primary_source == "sportsdataio":
            try:
                if game_ids:
                    all_props = []
                    for game_id in game_ids:
                        props = self._provider.get_player_props_for_game(game_id)
                        all_props.append(props)
                    odds = pd.concat(all_props, ignore_index=True) if all_props else pd.DataFrame()
                else:
                    odds = pd.DataFrame()
                self.logger.info(f"[provider] sportsdataio success odds={len(odds)}")
                return odds
            except Exception as e:
                self.logger.warning(f"[provider] sportsdataio failed: {e}")
                return self._fallback_client.get_odds(date_str, game_ids)
        else:
            odds = self._fallback_client.get_odds(date_str, game_ids)
            self.logger.info(f"[provider] balldontlie success odds={len(odds) if not odds.empty else 0}")
            return odds

    def get_stats(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Get player stats. Try SportsDataIO, fallback to BallDontLie."""
        if self._provider and self._primary_source == "sportsdataio":
            try:
                stats = self._provider.get_stats_for_player_ids([], start_date, end_date)
                self.logger.info(f"[provider] sportsdataio success stats={len(stats) if not stats.empty else 0}")
                return stats
            except Exception as e:
                self.logger.warning(f"[provider] sportsdataio failed: {e}")
                return self._fallback_client.get_stats(start_date, end_date)
        else:
            stats = self._fallback_client.get_stats(start_date, end_date)
            self.logger.info(f"[provider] balldontlie success stats={len(stats) if not stats.empty else 0}")
            return stats


class CourtVisionAI:
    PLAYER_MARKETS = {
        "player_points": "pts",
        "player_rebounds": "reb",
        "player_assists": "ast",
        "player_3pt_made": "fg3m",
        "player_steals": "stl",
        "player_blocks": "blk",
    }
    STAT_TO_MARKET_MAP = {stat_key: market_type for market_type, stat_key in PLAYER_MARKETS.items()}

    DEFAULT_THRESHOLDS = {
        "player_points": {"edge": 2.0, "confidence": 0.62},
        "player_rebounds": {"edge": 1.5, "confidence": 0.61},
        "player_assists": {"edge": 1.5, "confidence": 0.61},
        "player_3pt_made": {"edge": 0.7, "confidence": 0.60},
        "player_steals": {"edge": 0.5, "confidence": 0.68},
        "player_blocks": {"edge": 0.5, "confidence": 0.68},
        "team_total": {"edge": 2.5, "confidence": 0.60},
        "moneyline": {"edge": 0.03, "confidence": 0.58},
    }
    STRIKE_MARKET_MULTIPLIERS = {
        "player_3pt_made": 1.25,
        "player_steals": 1.35,
        "player_blocks": 1.30,
    }
    PREDICTIVE_LINE_STEPS = {
        "player_points": 0.5,
        "player_rebounds": 0.5,
        "player_assists": 0.5,
        "player_3pt_made": 0.5,
        "player_steals": 0.5,
        "player_blocks": 0.5,
    }
    OPPONENT_ALLOWANCE_MAP = {
        "player_points": "opp_pts_allowed_avg",
        "player_rebounds": "opp_reb_allowed_avg",
        "player_assists": "opp_ast_allowed_avg",
        "player_3pt_made": "opp_fg3m_allowed_avg",
        "player_steals": "opp_stl_allowed_avg",
        "player_blocks": "opp_blk_allowed_avg",
    }

    def __init__(self, out_dir: str = "outputs") -> None:
        _load_env_file()
        self.request_timeout = self._env_int("BALLDONTLIE_REQUEST_TIMEOUT", 30)
        self.client: Optional[BallDontLieClient] = None

        self.out_dir = Path(out_dir)
        self.runtime_dir = self.out_dir / "runtime"
        self.runtime_history_dir = self.runtime_dir / "history"
        self.data_history_dir = Path("data") / "history"
        self.model_dir = self.out_dir / "model"
        self.log_dir = self.out_dir / "logs"

        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_history_dir.mkdir(parents=True, exist_ok=True)
        self.data_history_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = _get_logger(self.log_dir)
        self.api_key, self.api_key_details = resolve_api_key(
            entrypoint="courtvision_ai.py",
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
            logger=self.logger,
        )
        self._log_api_key_fingerprint()
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enable_telegram = os.getenv("COURTVISION_TELEGRAM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

        self.player_baselines_path = self.model_dir / "player_baselines.csv"
        self.team_baselines_path = self.model_dir / "team_baselines.csv"
        self.calibration_path = self.model_dir / "calibration.json"

        self.prediction_history_path = self.data_history_dir / "prediction_history.csv"
        self.rejection_history_path = self.runtime_history_dir / "rejection_history.csv"
        self.feedback_path = self.runtime_history_dir / "result_feedback.csv"
        self.run_log_path = self.runtime_history_dir / "run_log.csv"
        self.board_scoring = BoardScoringPolicy(
            BoardScoringConfig(
                elite_min_confidence=self.ELITE_MIN_CONFIDENCE,
                elite_min_quality_score=self.ELITE_MIN_QUALITY_SCORE,
                elite_min_player_minutes=self.ELITE_MIN_PLAYER_MINUTES,
                elite_min_player_edge=self.ELITE_MIN_PLAYER_EDGE,
                elite_min_player_confidence=self.ELITE_MIN_PLAYER_CONFIDENCE,
                elite_min_moneyline_edge=self.ELITE_MIN_MONEYLINE_EDGE,
                elite_min_moneyline_confidence=self.ELITE_MIN_MONEYLINE_CONFIDENCE,
                elite_max_plus_moneyline_odds=self.ELITE_MAX_PLUS_MONEYLINE_ODDS,
            )
        )
        self.board_audit = BoardAuditPolicy()
        self.player_selection = PlayerSelectionPolicy(
            PlayerSelectionConfig(
                hard_min_minutes=self.PLAYER_HARD_MIN_MINUTES,
                soft_minutes_buffer=self.PLAYER_SOFT_MINUTES_BUFFER,
                max_soft_edge_multiplier_penalty=self.PLAYER_SOFT_EDGE_PENALTY,
                min_soft_confidence_penalty=self.PLAYER_SOFT_CONFIDENCE_PENALTY_MIN,
                max_soft_confidence_penalty=self.PLAYER_SOFT_CONFIDENCE_PENALTY_MAX,
            )
        )
        self.qualification_gate = QualificationGatePolicy(QualificationGateConfig())
        self.board_volume = BoardVolumePolicy(BoardVolumeConfig())


    # Elite thresholds - centralized in EliteThresholds dataclass
    _elite = EliteThresholds.default()
    ELITE_BOARD_LIMIT = _elite.board_limit
    ELITE_MIN_CONFIDENCE = _elite.confidence
    ELITE_MIN_QUALITY_SCORE = _elite.quality_score
    ELITE_MIN_PLAYER_MINUTES = _elite.player_minutes
    ELITE_MIN_PLAYER_EDGE = _elite.player_edge
    ELITE_MIN_PLAYER_CONFIDENCE = _elite.player_confidence
    ELITE_MIN_MONEYLINE_EDGE = _elite.moneyline_edge
    ELITE_MIN_MONEYLINE_CONFIDENCE = _elite.moneyline_confidence
    ELITE_MAX_PLUS_MONEYLINE_ODDS = _elite.max_plus_moneyline_odds
    ELITE_MAX_NEGATIVE_MONEYLINE_ODDS = _elite.max_negative_moneyline_odds
    ELITE_MAX_PROPS_PER_PLAYER = 1
    PLAYER_HARD_MIN_MINUTES = 14.0
    PLAYER_SOFT_MINUTES_BUFFER = 4.0
    PLAYER_SOFT_EDGE_PENALTY = 0.10
    PLAYER_SOFT_CONFIDENCE_PENALTY_MIN = 0.01
    PLAYER_SOFT_CONFIDENCE_PENALTY_MAX = 0.03
    ELITE_TEAM_CAP = _elite.team_cap
    ELITE_GAME_CAP = _elite.game_cap
    FULL_MARKET_TEAM_CAP = 3
    FULL_MARKET_GAME_CAP = 4
    STRIKE_TEAM_CAP = 2
    STRIKE_GAME_CAP = 3
    PREDICTIVE_TEAM_CAP = 2
    PREDICTIVE_GAME_CAP = 3
    STAT_ONLY_TEAM_CAP = 3
    STAT_ONLY_GAME_CAP = 4
    TEAM_BOARD_TEAM_CAP = 2
    STRIKE_BOARD_LIMIT = 12
    STRIKE_MIN_CONFIDENCE = 0.60
    STRIKE_MIN_QUALITY_SCORE = 64.0
    STRIKE_PER_MARKET_LIMIT = 4
    PREDICTIVE_LINE_BOARD_LIMIT = 20
    PREDICTIVE_LINE_MIN_CONFIDENCE = 0.57
    PREDICTIVE_LINE_MIN_QUALITY_SCORE = 60.0
    SGP_BOARD_LIMIT = 12
    SGP_MIN_COMBINED_CONFIDENCE = 0.34
    SGP_MIN_LEGS = 2
    SGP_MAX_LEGS = 3
    SGP_PER_GAME_CANDIDATE_LIMIT = 14
    SGP_VOLATILE_MIN_EDGE = 0.65
    SGP_VOLATILE_MIN_CONFIDENCE = 0.70
    GRADE_LOOKBACK_DAYS = 14
    PLAYER_INJURY_BOOST_CAP = 0.20
    PLAYER_POINTS_MAX_CONFIDENCE_INJURY_UPLIFT = 0.03
    INJURY_STATUS_WEIGHTS = {
        "out": 1.0,
        "doubtful": 0.75,
        "questionable": 0.45,
        "probable": 0.15,
        "day-to-day": 0.35,
        "day to day": 0.35,
    }

    def _strike_multiplier(self, market_type: str, raw_stat_key: Optional[str] = None) -> float:
        if raw_stat_key:
            mapped_market = self.STAT_TO_MARKET_MAP.get(str(raw_stat_key))
            if mapped_market:
                market_type = mapped_market
        return float(self.STRIKE_MARKET_MULTIPLIERS.get(str(market_type), 1.0))

    def _log_api_key_fingerprint(self) -> None:
        details = dict(self.api_key_details)
        self.logger.info(
            "BALLDONTLIE key env_var=%s source=%s preview=%s",
            details.get("env_var_name", BALLDONTLIE_API_KEY_ENV_VAR),
            details.get("source", "unknown"),
            details.get("masked_preview", "<empty>"),
        )
        if details.get("dotenv_path"):
            self.logger.info(
                "BALLDONTLIE key dotenv_path=%s dotenv_fingerprint=%s",
                details.get("dotenv_path", ""),
                details.get("dotenv_fingerprint", "missing"),
            )
        if details.get("dotenv_ignored"):
            self.logger.warning(
                "BALLDONTLIE key precedence=process_env dotenv_ignored=%s dotenv_matches_process=%s",
                True,
                details.get("dotenv_matches_process", False),
            )
        if details.get("had_wrapping_quotes") or details.get("dotenv_had_wrapping_quotes"):
            self.logger.warning("BALLDONTLIE key quote_warning=true")
        if details.get("had_surrounding_whitespace") or details.get("dotenv_had_surrounding_whitespace"):
            self.logger.warning("BALLDONTLIE key whitespace_warning=true")
        if details.get("wrong_env_var_candidates"):
            self.logger.warning(
                "BALLDONTLIE alternate_envs=%s",
                ",".join(str(item) for item in details.get("wrong_env_var_candidates", [])),
            )

    def _apply_strike_system(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        out = df.copy()
        market_series = out.get("market_type", pd.Series("", index=out.index)).astype(str)
        raw_stat_series = out.get("raw_stat_key", pd.Series("", index=out.index)).astype(str)
        out["strike_multiplier"] = [
            self._strike_multiplier(market_type=market, raw_stat_key=raw_key)
            for market, raw_key in zip(market_series.tolist(), raw_stat_series.tolist())
        ]

        out["volatility_boost"] = out["strike_multiplier"]
        for col in ["edge", "edge_abs", "confidence", "quality_score"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        out["strike_edge"] = pd.to_numeric(out.get("edge_abs", pd.Series(dtype=float)), errors="coerce").fillna(0.0) * out["strike_multiplier"]
        out["strike_confidence"] = pd.to_numeric(out.get("confidence", pd.Series(dtype=float)), errors="coerce").fillna(0.0) * out["strike_multiplier"]
        out["strike_confidence"] = out["strike_confidence"].clip(upper=0.999)
        base_quality = pd.to_numeric(out.get("quality_score", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        out["strike_quality_score"] = base_quality * out["strike_multiplier"]
        out["board_source"] = out.get("board_source", pd.Series("stat_only", index=out.index)).astype(str)
        out["board_source"] = out["board_source"].replace({"all_stats_projection": "shadow_market"})
        out["reason"] = out.get("reason", pd.Series("", index=out.index)).astype(str)
        out["reason"] = out["reason"].replace({"no_live_line_found": "preline_opportunity"})
        return out

    def _build_strike_board(self, stat_only_df: pd.DataFrame, limit: Optional[int] = None) -> pd.DataFrame:
        if stat_only_df.empty:
            return pd.DataFrame()

        limit = self.STRIKE_BOARD_LIMIT if limit is None else int(limit)
        out = self._apply_strike_system(stat_only_df)
        out = out[
            (pd.to_numeric(out.get("strike_confidence", pd.Series(dtype=float)), errors="coerce").fillna(0.0) >= self.STRIKE_MIN_CONFIDENCE)
            & (pd.to_numeric(out.get("strike_quality_score", pd.Series(dtype=float)), errors="coerce").fillna(0.0) >= self.STRIKE_MIN_QUALITY_SCORE)
        ].copy()
        if out.empty:
            return out

        sort_cols = [c for c in ["strike_quality_score", "strike_confidence", "strike_edge", "quality_score", "confidence", "edge_abs"] if c in out.columns]
        out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)

        group_cols = [c for c in ["market_type", "team"] if c in out.columns]
        if group_cols:
            out = out.groupby(group_cols, as_index=False, dropna=False, sort=False).head(self.STRIKE_PER_MARKET_LIMIT).reset_index(drop=True)

        dedupe_cols = [c for c in ["entity_name", "market_type", "selection"] if c in out.columns]
        if dedupe_cols:
            out = out.drop_duplicates(subset=dedupe_cols, keep="first").reset_index(drop=True)

        out["board_source"] = "strike_system"
        out["reason"] = "high_upside_preline"
        if "market_label" in out.columns and "entity_name" in out.columns:
            out["bet_label"] = out["entity_name"].astype(str).str.strip() + " STRIKE " + out["market_label"].astype(str).str.upper()
        out = self._apply_team_exposure_caps(
            out,
            per_team_cap=self.STRIKE_TEAM_CAP,
            per_game_cap=self.STRIKE_GAME_CAP,
        )
        return out.head(limit).reset_index(drop=True)

    def _market_step(self, market_type: str) -> float:
        return float(self.PREDICTIVE_LINE_STEPS.get(str(market_type), 0.5))

    def _round_to_market_step(self, value: Any, market_type: str) -> float:
        step = self._market_step(market_type)
        numeric = self._to_float(value) or 0.0
        if step <= 0:
            return round(float(numeric), 4)
        return round(round(float(numeric) / step) * step, 4)

    def _apply_predictive_lines_engine(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        out = df.copy()
        for col in ["model_projection", "edge", "edge_abs", "confidence", "quality_score", "strike_confidence", "strike_quality_score", "strike_multiplier"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        market_series = out.get("market_type", pd.Series("", index=out.index)).astype(str)
        projection_series = pd.to_numeric(out.get("model_projection", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        edge_series = pd.to_numeric(out.get("edge", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        base_conf = pd.to_numeric(out.get("strike_confidence", out.get("confidence", pd.Series(dtype=float))), errors="coerce").fillna(0.0)
        base_quality = pd.to_numeric(out.get("strike_quality_score", out.get("quality_score", pd.Series(dtype=float))), errors="coerce").fillna(0.0)
        strike_multiplier = pd.to_numeric(out.get("strike_multiplier", pd.Series(1.0, index=out.index)), errors="coerce").fillna(1.0)

        fair_lines = []
        entry_lines = []
        recommended_sides = []
        line_buffers = []
        predicted_edges = []
        implied_probs = []
        quality_scores = []

        for idx, market_type in enumerate(market_series.tolist()):
            projection = float(projection_series.iloc[idx])
            edge = float(edge_series.iloc[idx])
            confidence = float(base_conf.iloc[idx])
            quality = float(base_quality.iloc[idx])
            mult = float(strike_multiplier.iloc[idx])
            step = self._market_step(market_type)
            recommended_side = "Over" if edge >= 0 else "Under"
            fair_line = self._round_to_market_step(projection, market_type)
            line_buffer = step if confidence >= 0.65 else step / 2.0
            entry_line = fair_line - line_buffer if recommended_side == "Over" else fair_line + line_buffer
            entry_line = round(float(entry_line), 4)
            predicted_edge = abs(projection - entry_line)
            implied_prob = min(max(0.50 + ((confidence - 0.50) * 0.90), 0.50), 0.92)
            predictive_quality = quality + (predicted_edge * 12.0) + ((implied_prob - 0.50) * 50.0) + ((mult - 1.0) * 10.0)

            fair_lines.append(fair_line)
            entry_lines.append(entry_line)
            recommended_sides.append(recommended_side)
            line_buffers.append(round(float(line_buffer), 4))
            predicted_edges.append(round(float(predicted_edge), 4))
            implied_probs.append(round(float(implied_prob), 4))
            quality_scores.append(round(float(predictive_quality), 4))

        out["predicted_book_line"] = fair_lines
        out["fair_line"] = fair_lines
        out["entry_line"] = entry_lines
        out["recommended_side"] = recommended_sides
        out["line_buffer"] = line_buffers
        out["predictive_edge"] = predicted_edges
        out["implied_hit_probability"] = implied_probs
        out["predictive_quality_score"] = quality_scores
        out["board_source"] = "predictive_lines_engine"
        out["reason"] = "predicted_opening_line"
        if "market_label" in out.columns and "entity_name" in out.columns:
            out["bet_label"] = (
                out["entity_name"].astype(str).str.strip()
                + " "
                + out["recommended_side"].astype(str).str.upper()
                + " "
                + out["entry_line"].map(lambda x: f"{(self._to_float(x) or 0.0):.1f}")
                + " "
                + out["market_label"].astype(str).str.upper()
            )
        return out

    def _build_predictive_lines_board(self, source_df: pd.DataFrame, limit: Optional[int] = None) -> pd.DataFrame:
        if source_df.empty:
            return pd.DataFrame()

        limit = self.PREDICTIVE_LINE_BOARD_LIMIT if limit is None else int(limit)
        out = self._apply_predictive_lines_engine(source_df)
        out = out[
            (pd.to_numeric(out.get("strike_confidence", out.get("confidence", pd.Series(dtype=float))), errors="coerce").fillna(0.0) >= self.PREDICTIVE_LINE_MIN_CONFIDENCE)
            & (pd.to_numeric(out.get("predictive_quality_score", pd.Series(dtype=float)), errors="coerce").fillna(0.0) >= self.PREDICTIVE_LINE_MIN_QUALITY_SCORE)
        ].copy()
        if out.empty:
            return out

        volatile_mask = out.get("market_type", pd.Series("", index=out.index)).astype(str).isin(["player_steals", "player_blocks"])
        if volatile_mask.any():
            predictive_edge = pd.to_numeric(out.get("predictive_edge", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
            confidence = pd.to_numeric(out.get("strike_confidence", out.get("confidence", pd.Series(dtype=float))), errors="coerce").fillna(0.0)
            entry_line = pd.to_numeric(out.get("entry_line", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
            out = out[(~volatile_mask) | ((predictive_edge >= 0.5) & (confidence >= 0.62) & (entry_line >= 0.5))].copy()
            if out.empty:
                return out

        sort_cols = [c for c in ["predictive_quality_score", "implied_hit_probability", "predictive_edge", "strike_quality_score", "strike_confidence"] if c in out.columns]
        out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
        out = self._apply_team_exposure_caps(
            out,
            per_team_cap=self.PREDICTIVE_TEAM_CAP,
            per_game_cap=self.PREDICTIVE_GAME_CAP,
        )
        return out.head(limit).reset_index(drop=True)

    def _market_label(self, market_type: str) -> str:
        labels = {
            "player_points": "Points",
            "player_rebounds": "Rebounds",
            "player_assists": "Assists",
            "player_3pt_made": "3PT Made",
            "player_steals": "Steals",
            "player_blocks": "Blocks",
            "player_points_rebounds": "Points + Rebounds",
            "player_points_assists": "Points + Assists",
            "player_rebounds_assists": "Rebounds + Assists",
            "player_points_rebounds_assists": "PRA",
            "player_blocks_steals": "Stocks",
            "team_total": "Team Total",
            "moneyline": "Moneyline",
        }
        return labels.get(str(market_type), str(market_type).replace("_", " ").title())

    def _build_bet_label(
        self,
        market_type: str,
        entity_name: str,
        selection: str,
        sportsbook_line: float,
    ) -> str:
        market_label = self._market_label(market_type)
        selection_text = str(selection).upper().strip()
        if market_type == "moneyline":
            return f"{entity_name} {selection_text}".strip()
        return f"{entity_name} {selection_text} {sportsbook_line:.1f} {market_label.upper()}".strip()

    def _player_tier_weight(self, market_type: str, minutes_projection: float) -> float:
        return self.board_scoring.player_tier_weight(market_type, minutes_projection)

    def _favorite_bias_factor(self, market_type: str, sportsbook_line: float, odds: Any) -> float:
        return self.board_scoring.favorite_bias_factor(market_type, sportsbook_line, odds)

    def _edge_pct_denominator(self, market_type: str, sportsbook_line: float) -> float:
        return self.board_scoring.edge_pct_denominator(market_type, sportsbook_line)

    def _longshot_penalty_points(self, odds: Any) -> float:
        return self.board_scoring.longshot_penalty_points(odds)

    def _volatility_penalty_points(self, row: Mapping[str, Any]) -> float:
        return self.board_scoring.volatility_penalty_points(row)

    def _historical_confidence_multiplier(self, row: Mapping[str, Any]) -> float:
        return self.board_scoring.historical_confidence_multiplier(row)

    def _edge_pct_value(self, market_type: str, adjusted_edge_abs: float, sportsbook_line: float) -> float:
        return self.board_scoring.edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)

    def _apply_scoring_metadata(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return self.board_scoring.apply_scoring_metadata(row)

    def _is_elite_candidate(self, row: Mapping[str, Any]) -> bool:
        return self.board_scoring.is_elite_candidate(row)

    def _apply_board_audit_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return self.board_audit.apply_row_audit(row)

    def _apply_board_audit_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.board_audit.apply_dataframe_audit(df)

    def _build_board_diagnostics(
        self,
        prediction_date: str,
        qualified_pool_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
        rejected_df: pd.DataFrame,
        final_board_construction: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self.board_audit.build_diagnostics(
            prediction_date=prediction_date,
            qualified_pool_df=qualified_pool_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
            rejected_df=rejected_df,
            final_board_construction=final_board_construction,
        )

    def _enrich_pick_row(self, row: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        market_type = str(enriched.get("market_type", ""))
        entity_name = str(enriched.get("entity_name", ""))
        selection = str(enriched.get("selection", ""))
        sportsbook_line = self._to_float(enriched.get("sportsbook_line")) or 0.0
        enriched["market_label"] = self._market_label(market_type)
        enriched["bet_label"] = self._build_bet_label(market_type, entity_name, selection, sportsbook_line)
        enriched["raw_stat_key"] = self._market_to_stat_key(market_type)
        enriched["market_alias"] = market_type
        enriched = self._apply_scoring_metadata(enriched)
        if "letter_grade" not in enriched:
            enriched["letter_grade"] = "B"
        return enriched

    def _grade_from_recent_matches(
        self,
        market_type: str,
        sportsbook_line: float,
        model_projection: float,
        confidence: float,
        edge: float,
        player_row: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if market_type not in self.PLAYER_MARKETS or not player_row:
            base_score = confidence * 100.0 + abs(edge) * 8.0
            return {
                "letter_grade": self._score_to_letter_grade(base_score),
                "grade_score": round(float(base_score), 4),
                "recent_avg": None,
                "season_avg": None,
                "recent_vs_line": None,
                "recent_form_flag": "N/A",
            }

        stat_col = self.PLAYER_MARKETS[market_type]
        recent_avg = self._to_float(player_row.get(f"{stat_col}_recent")) or 0.0
        season_avg = self._to_float(player_row.get(f"{stat_col}_avg")) or 0.0
        recent_vs_line = recent_avg - sportsbook_line
        recent_ratio = (recent_avg / sportsbook_line) if abs(sportsbook_line) > 0 else 1.0

        if recent_ratio >= 1.15:
            recent_form_flag = "HOT"
        elif recent_ratio <= 0.9:
            recent_form_flag = "COLD"
        else:
            recent_form_flag = "STABLE"

        grade_score = confidence * 100.0 + abs(edge) * 8.0
        grade_score += min(abs(recent_vs_line), 8.0) * 3.5
        if (selection := ("Over" if edge >= 0 else "Under")) == "Over":
            if recent_avg >= sportsbook_line:
                grade_score += 8.0
            else:
                grade_score -= 8.0
        else:
            if recent_avg <= sportsbook_line:
                grade_score += 8.0
            else:
                grade_score -= 8.0

        if recent_form_flag == "HOT":
            grade_score += 6.0
        elif recent_form_flag == "COLD":
            grade_score -= 6.0

        return {
            "letter_grade": self._score_to_letter_grade(grade_score),
            "grade_score": round(float(grade_score), 4),
            "recent_avg": round(float(recent_avg), 4),
            "season_avg": round(float(season_avg), 4),
            "recent_vs_line": round(float(recent_vs_line), 4),
            "recent_form_flag": recent_form_flag,
        }

    def _score_to_letter_grade(self, score: float) -> str:
        if score >= 95:
            return "A+"
        if score >= 88:
            return "A"
        if score >= 82:
            return "A-"
        if score >= 76:
            return "B+"
        if score >= 70:
            return "B"
        if score >= 64:
            return "B-"
        if score >= 58:
            return "C+"
        if score >= 52:
            return "C"
        if score >= 46:
            return "D"
        return "F"


    def _prepare_selected_board(self, selected_df: pd.DataFrame) -> pd.DataFrame:
        if selected_df.empty:
            return selected_df

        out = selected_df.copy()
        for col in ["confidence", "edge", "edge_abs", "sportsbook_line", "odds", "quality_score", "edge_pct"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        if "edge_abs" not in out.columns and "edge" in out.columns:
            out["edge_abs"] = out["edge"].abs()

        if "edge_pct" not in out.columns:
            sportsbook_series = pd.to_numeric(out.get("sportsbook_line", pd.Series(dtype=float)), errors="coerce").abs()
            denom = sportsbook_series.where(sportsbook_series > 0, 1.0)
            out["edge_pct"] = (
                pd.to_numeric(out.get("edge_abs", pd.Series(dtype=float)), errors="coerce").fillna(0.0) / denom
            ) * 100.0

        out = pd.DataFrame([self._apply_scoring_metadata(_to_str_dict(row)) for _, row in out.iterrows()])
        out = self._apply_board_audit_frame(out)

        if {"market_type", "edge"}.issubset(out.columns):
            keep_mask = ~(
                out["market_type"].astype(str).eq("moneyline")
                & (pd.to_numeric(out["edge"], errors="coerce").fillna(-999.0) <= 0.0)
            )
            out = out[keep_mask].copy()

        sort_cols = [c for c in ["is_live_market", "quality_score", "confidence", "edge_abs", "odds"] if c in out.columns]
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)

        exact_keys = [
            "prediction_date",
            "market_type",
            "entity_name",
            "team",
            "opponent",
            "selection",
            "sportsbook_line",
        ]
        exact_keys = [c for c in exact_keys if c in out.columns]
        if exact_keys:
            out = out.drop_duplicates(subset=exact_keys, keep="first").reset_index(drop=True)

        # Remove alternate-line duplicates for non-moneyline player/game props.
        broad_keys = ["prediction_date", "market_type", "entity_name", "team", "opponent"]
        broad_keys = [c for c in broad_keys if c in out.columns]
        if broad_keys:
            non_ml = out[out["market_type"].astype(str) != "moneyline"].copy()
            ml = out[out["market_type"].astype(str) == "moneyline"].copy()
            if not non_ml.empty:
                non_ml = non_ml.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
                non_ml = non_ml.drop_duplicates(subset=broad_keys, keep="first").reset_index(drop=True)
            out = pd.concat([non_ml, ml], ignore_index=True) if (not non_ml.empty or not ml.empty) else out

        # Keep only one moneyline side per matchup.
        if {"market_type", "team", "opponent"}.issubset(out.columns):
            ml = out[out["market_type"].astype(str) == "moneyline"].copy()
            non_ml = out[out["market_type"].astype(str) != "moneyline"].copy()
            if not ml.empty:
                ml["matchup_key"] = ml.apply(
                    lambda r: "__".join(sorted([str(r.get("team", "")).strip().upper(), str(r.get("opponent", "")).strip().upper()])),
                    axis=1,
                )
                if sort_cols:
                    ml = ml.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
                ml = ml.drop_duplicates(subset=[c for c in ["prediction_date", "matchup_key"] if c in ml.columns], keep="first")
                ml = ml.drop(columns=["matchup_key"], errors="ignore")
            out = pd.concat([non_ml, ml], ignore_index=True) if (not non_ml.empty or not ml.empty) else out

        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
        return out

    def _sort_priority_columns(self, df: pd.DataFrame, *, elite_priority: bool = False) -> list[str]:
        priority_cols = ["is_live_market"]
        if elite_priority and "elite_rank_score" in df.columns:
            priority_cols.append("elite_rank_score")
        priority_cols.extend(["quality_score", "confidence", "edge_abs", "odds"])
        return [c for c in priority_cols if c in df.columns]

    def _apply_team_exposure_caps(
        self,
        df: pd.DataFrame,
        per_team_cap: Optional[int] = None,
        per_game_cap: Optional[int] = None,
        *,
        sort_columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        out = df.copy()
        sort_cols = sort_columns or self._sort_priority_columns(out)
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)

        kept_rows: list[dict[str, Any]] = []
        team_counts: dict[str, int] = {}
        game_counts: dict[str, int] = {}

        for _, row in out.iterrows():
            row_dict = _to_str_dict(row)
            team = str(row_dict.get("team", "")).strip().upper()
            opp = str(row_dict.get("opponent", "")).strip().upper()
            game_key = "__".join(sorted([team, opp])) if team and opp else ""

            if per_team_cap is not None and team_counts.get(team, 0) >= int(per_team_cap):
                continue
            if per_game_cap is not None and game_key and game_counts.get(game_key, 0) >= int(per_game_cap):
                continue

            kept_rows.append(row_dict)
            if team:
                team_counts[team] = team_counts.get(team, 0) + 1
            if game_key:
                game_counts[game_key] = game_counts.get(game_key, 0) + 1

        return pd.DataFrame(kept_rows)

    def _team_exposure_summary(self, df: pd.DataFrame, top_n: int = 5) -> list[dict[str, Any]]:
        if df.empty or "team" not in df.columns:
            return []

        counts = (
            df["team"]
            .fillna("")
            .astype(str)
            .str.upper()
            .value_counts()
            .head(top_n)
            .rename_axis("team")
            .reset_index(name="count")
        )
        records = counts.to_dict(orient="records")
        return [{str(k): v for k, v in record.items()} for record in records]

    def _boolish_series(self, df: pd.DataFrame, column: str, default: bool) -> pd.Series:
        if column not in df.columns:
            return pd.Series(default, index=df.index, dtype=bool)
        series = df[column]
        if not isinstance(series, pd.Series):
            return pd.Series(default, index=df.index, dtype=bool)
        if pd.api.types.is_bool_dtype(series):
            return series.fillna(default).astype(bool)
        normalized = series.fillna("").astype(str).str.strip()
        return normalized.map(lambda value: default if value == "" else self.board_scoring.to_bool(value))

    def _live_market_only(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        live_mask = self._boolish_series(df, "is_live_market", True)
        synthetic_mask = self._boolish_series(df, "synthetic_line", False)
        out = df[live_mask & ~synthetic_mask].copy()
        raw_market_type = out.get("raw_market_type", pd.Series("", index=out.index)).fillna("").astype(str).str.strip().str.lower()
        selection = out.get("selection", pd.Series("", index=out.index)).fillna("").astype(str).str.strip().str.lower()
        out = out[raw_market_type.ne("milestone") & selection.ne("milestone")].copy()
        if "line_source" in out.columns:
            source = out["line_source"].fillna("").astype(str).str.strip().str.lower()
            out = out[(source == "") | (source == "live_market")].copy()
        return out.reset_index(drop=True)

    def _apply_player_exposure_caps(
        self,
        df: pd.DataFrame,
        per_player_cap: Optional[int] = None,
        *,
        sort_columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        if df.empty or per_player_cap is None:
            return df.copy()

        out = df.copy()
        sort_cols = sort_columns or self._sort_priority_columns(out)
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)

        kept_rows: list[dict[str, Any]] = []
        player_counts: dict[tuple[str, str], int] = {}
        for _, row in out.iterrows():
            row_dict = _to_str_dict(row)
            market_type = str(row_dict.get("market_type", "")).strip().lower()
            if not market_type.startswith("player_"):
                kept_rows.append(row_dict)
                continue

            player_key = (
                str(row_dict.get("entity_name", "")).strip().lower(),
                str(row_dict.get("team", "")).strip().upper(),
            )
            if player_counts.get(player_key, 0) >= int(per_player_cap):
                continue

            kept_rows.append(row_dict)
            player_counts[player_key] = player_counts.get(player_key, 0) + 1

        return pd.DataFrame(kept_rows)

    def _row_identity_key(self, row: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("entity_name", "")).strip().lower(),
            str(row.get("team", "")).strip().upper(),
            str(row.get("market_type", "")).strip().lower(),
            str(row.get("selection", "")).strip().lower(),
        )

    def _qualification_gate_mode_counts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty or "qualification_gate_mode" not in df.columns:
            return []
        counts = (
            df["qualification_gate_mode"]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        counts = counts[counts != ""]
        if counts.empty:
            return []
        frame = counts.value_counts().rename_axis("key").reset_index(name="count")
        return [
            {"key": str(row["key"]), "count": int(row["count"])}
            for _, row in frame.iterrows()
        ]

    def _final_selection_source_lane(self, row: Mapping[str, Any]) -> str:
        qualification_gate_mode = str(row.get("qualification_gate_mode", "")).strip()
        if qualification_gate_mode == "live_quality_rescue":
            return "live_quality_rescue_pass"
        return "core_pass"

    def _final_selection_source_lane_counts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty:
            return []
        lanes = pd.Series(
            [self._final_selection_source_lane(_to_str_dict(row)) for _, row in df.iterrows()],
            dtype="object",
        )
        lanes = lanes.fillna("").astype(str).str.strip()
        lanes = lanes[lanes != ""]
        if lanes.empty:
            return []
        frame = lanes.value_counts().rename_axis("key").reset_index(name="count")
        return [
            {"key": str(row["key"]), "count": int(row["count"])}
            for _, row in frame.iterrows()
        ]

    @staticmethod
    def _count_item_value(items: list[dict[str, Any]], key: str) -> int:
        for item in items:
            if str(item.get("key", "")).strip() == str(key).strip():
                return int(item.get("count", 0) or 0)
        return 0

    def _tag_final_selection_source_lane(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        out = df.copy()
        out["final_selection_source_lane"] = [
            self._final_selection_source_lane(_to_str_dict(row))
            for _, row in out.iterrows()
        ]
        return out

    def _board_stage_snapshot(self, df: pd.DataFrame) -> dict[str, Any]:
        return {
            "count": int(len(df)),
            "count_by_qualification_gate_mode": self._qualification_gate_mode_counts(df),
            "count_by_final_selection_source_lane": self._final_selection_source_lane_counts(df),
        }

    def _sort_board_frame(
        self,
        df: pd.DataFrame,
        *,
        elite_priority: bool = False,
        sort_columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        if df.empty:
            return df.copy()
        sort_cols = sort_columns or self._sort_priority_columns(df, elite_priority=elite_priority)
        out = df.copy()
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
        return out

    @staticmethod
    def _player_team_key(row: Mapping[str, Any]) -> tuple[str, str]:
        return (
            str(row.get("entity_name", "")).strip().lower(),
            str(row.get("team", "")).strip().upper(),
        )

    def _empty_player_points_elite_admission_df(self) -> pd.DataFrame:
        return pd.DataFrame(columns=list(PLAYER_POINTS_ELITE_ADMISSION_COLUMNS))

    def _build_player_points_elite_admission(
        self,
        *,
        input_candidates_df: pd.DataFrame,
        primary_df: pd.DataFrame,
        post_exposure_df: pd.DataFrame,
        final_df: pd.DataFrame,
        sort_columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        if input_candidates_df.empty or "market_type" not in input_candidates_df.columns:
            return self._empty_player_points_elite_admission_df()

        points_df = input_candidates_df[
            input_candidates_df["market_type"].fillna("").astype(str).str.strip().str.lower() == "player_points"
        ].copy()
        if points_df.empty:
            return self._empty_player_points_elite_admission_df()

        sort_cols = sort_columns or self._sort_priority_columns(input_candidates_df, elite_priority=True)
        overall_sorted = self._sort_board_frame(input_candidates_df, elite_priority=True, sort_columns=sort_cols)
        points_sorted = self._sort_board_frame(points_df, elite_priority=True, sort_columns=sort_cols)
        primary_sorted = self._sort_board_frame(primary_df, elite_priority=True, sort_columns=sort_cols)
        post_exposure_sorted = self._sort_board_frame(post_exposure_df, elite_priority=True, sort_columns=sort_cols)
        final_sorted = self._sort_board_frame(final_df, elite_priority=True, sort_columns=sort_cols).head(self.ELITE_BOARD_LIMIT)
        top_ranked_sorted = post_exposure_sorted.head(self.ELITE_BOARD_LIMIT)

        overall_rank_map = {
            self._row_identity_key(_to_str_dict(row)): index + 1
            for index, (_, row) in enumerate(overall_sorted.iterrows())
        }
        points_rank_map = {
            self._row_identity_key(_to_str_dict(row)): index + 1
            for index, (_, row) in enumerate(points_sorted.iterrows())
        }
        post_exposure_rank_map = {
            self._row_identity_key(_to_str_dict(row)): index + 1
            for index, (_, row) in enumerate(post_exposure_sorted.iterrows())
        }

        primary_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in primary_sorted.iterrows()
        }
        post_exposure_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in post_exposure_sorted.iterrows()
        }
        final_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in final_sorted.iterrows()
        }
        top_ranked_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in top_ranked_sorted.iterrows()
        }
        added_rows = self._board_added_rows(post_exposure_sorted, final_sorted)
        added_rescue_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in added_rows.iterrows()
            if self._final_selection_source_lane(_to_str_dict(row)) == "live_quality_rescue_pass"
        }

        post_exposure_player_map: dict[tuple[str, str], set[tuple[str, str, str, str]]] = {}
        for _, row in post_exposure_sorted.iterrows():
            row_dict = _to_str_dict(row)
            post_exposure_player_map.setdefault(self._player_team_key(row_dict), set()).add(
                self._row_identity_key(row_dict)
            )

        selected_non_point_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in final_sorted.iterrows()
            if str(row.get("market_type", "")).strip().lower() != "player_points"
        }

        confidence_floor = max(
            float(self.board_scoring.config.elite_min_confidence),
            float(self.board_scoring.config.elite_min_player_confidence),
        )
        rows: list[dict[str, Any]] = []

        for _, row in points_sorted.iterrows():
            row_dict = _to_str_dict(row)
            row_key = self._row_identity_key(row_dict)
            player_key = self._player_team_key(row_dict)

            confidence = self._to_float(row_dict.get("confidence")) or 0.0
            quality_score = self._to_float(row_dict.get("quality_score")) or 0.0
            selection_score = self._to_float(row_dict.get("elite_rank_score"))
            if selection_score is None:
                selection_score = quality_score
            edge_abs = self._to_float(row_dict.get("edge_abs"))
            if edge_abs is None:
                edge_abs = abs(self._to_float(row_dict.get("edge")) or 0.0)
            minutes_avg = self._to_float(row_dict.get("minutes_avg")) or 0.0
            minutes_recent = self._to_float(row_dict.get("minutes_recent")) or minutes_avg
            projected_minutes = max(minutes_avg, minutes_recent)
            elite_guard_fail_reason = str(row_dict.get("elite_points_risk_guard_reason", "") or "").strip()
            elite_guard_pass = elite_guard_fail_reason == ""
            realism_flag = bool(row_dict.get("player_points_realism_dampened")) or bool(
                str(row_dict.get("player_points_realism_dampener_reason", "") or "").strip()
            )
            confidence_failed = confidence < confidence_floor
            score_failed = (
                quality_score < float(self.board_scoring.config.elite_min_quality_score)
                or edge_abs < float(self.board_scoring.config.elite_min_player_edge)
                or projected_minutes < float(self.board_scoring.config.elite_min_player_minutes)
            )
            in_primary = row_key in primary_keys
            in_post_exposure = row_key in post_exposure_keys
            in_final = row_key in final_keys
            elite_ranked_top_n = row_key in top_ranked_keys
            same_player_kept = row_key not in post_exposure_keys and any(
                kept_key != row_key for kept_key in post_exposure_player_map.get(player_key, set())
            )
            lost_to_same_player_exposure = bool(same_player_kept)
            lost_to_board_cap = bool(in_post_exposure and not in_final)

            selected_non_points_above = False
            if lost_to_board_cap:
                candidate_rank = post_exposure_rank_map.get(row_key, len(post_exposure_sorted) + 1)
                higher_ranked = post_exposure_sorted.head(max(0, min(candidate_rank - 1, self.ELITE_BOARD_LIMIT)))
                selected_non_points_above = any(
                    str(item.get("market_type", "")).strip().lower() != "player_points"
                    for _, item in higher_ranked.iterrows()
                )

            lost_to_rescue_priority = bool(
                row_key not in post_exposure_keys
                and self._final_selection_source_lane(row_dict) == "core_pass"
                and self.board_volume.is_elite_backfill_candidate(row_dict)
                and added_rescue_keys
            )

            if in_final:
                final_exclusion_stage = "selected"
            elif not elite_guard_pass:
                final_exclusion_stage = "failed_hard_guard"
            elif not in_primary:
                if realism_flag:
                    final_exclusion_stage = "lost_on_realism"
                elif confidence_failed:
                    final_exclusion_stage = "lost_on_confidence"
                else:
                    final_exclusion_stage = "lost_on_score"
            elif not in_post_exposure:
                final_exclusion_stage = "lost_on_exposure"
            elif selected_non_points_above:
                final_exclusion_stage = "lost_on_cross-market_rank_pressure"
            elif lost_to_board_cap:
                final_exclusion_stage = "lost_on_board_capacity"
            else:
                final_exclusion_stage = "not_selected"

            rows.append(
                {
                    "player_name": str(row_dict.get("entity_name", "")).strip(),
                    "market": "player_points",
                    "side": str(row_dict.get("selection", "")).strip().lower(),
                    "line": self._to_float(row_dict.get("sportsbook_line")),
                    "projection": self._to_float(row_dict.get("model_projection")),
                    "edge": self._to_float(row_dict.get("edge")),
                    "confidence": round(float(confidence), 4),
                    "realism_score": round(float(quality_score), 4),
                    "selection_score": round(float(selection_score), 4),
                    "elite_guard_pass": bool(elite_guard_pass),
                    "elite_guard_fail_reason": elite_guard_fail_reason,
                    "rank_position_within_player_points": int(points_rank_map.get(row_key, 0)),
                    "rank_position_overall": int(overall_rank_map.get(row_key, 0)),
                    "lost_to_non_points_candidate": bool(selected_non_points_above and selected_non_point_keys),
                    "lost_to_same_player_exposure": bool(lost_to_same_player_exposure),
                    "lost_to_board_cap": bool(lost_to_board_cap),
                    "lost_to_rescue_priority": bool(lost_to_rescue_priority),
                    "final_exclusion_stage": str(final_exclusion_stage),
                    "qualification_gate_mode": str(row_dict.get("qualification_gate_mode", "")).strip(),
                    "final_selection_source_lane": self._final_selection_source_lane(row_dict),
                    "player_profile_bucket": str(row_dict.get("player_profile_bucket", "")).strip(),
                    "player_points_line_band": str(row_dict.get("player_points_line_band", "")).strip(),
                    "injury_influence_bucket": str(row_dict.get("injury_influence_bucket", "")).strip(),
                    "elite_ranked_top_n": bool(elite_ranked_top_n),
                }
            )

        return pd.DataFrame(rows, columns=list(PLAYER_POINTS_ELITE_ADMISSION_COLUMNS))

    def _board_added_rows(self, baseline_df: pd.DataFrame, final_df: pd.DataFrame) -> pd.DataFrame:
        if final_df.empty:
            return pd.DataFrame()
        baseline_keys = {
            self._row_identity_key(_to_str_dict(row))
            for _, row in baseline_df.iterrows()
        } if not baseline_df.empty else set()
        added_rows = []
        for _, row in final_df.iterrows():
            row_dict = _to_str_dict(row)
            if self._row_identity_key(row_dict) not in baseline_keys:
                added_rows.append(row_dict)
        return pd.DataFrame(added_rows)

    def _build_board_construction_trace(
        self,
        *,
        input_live_candidates: Optional[pd.DataFrame] = None,
        post_primary_selection: Optional[pd.DataFrame] = None,
        post_exposure_caps: Optional[pd.DataFrame] = None,
        post_backfill: Optional[pd.DataFrame] = None,
        player_points_elite_admission: Optional[pd.DataFrame] = None,
    ) -> dict[str, Any]:
        # Normalize None to empty DataFrames for backward compatibility
        input_live_candidates = input_live_candidates if input_live_candidates is not None else pd.DataFrame()
        post_primary_selection = post_primary_selection if post_primary_selection is not None else pd.DataFrame()
        post_exposure_caps = post_exposure_caps if post_exposure_caps is not None else pd.DataFrame()
        post_backfill = post_backfill if post_backfill is not None else pd.DataFrame()

        added_rows = self._board_added_rows(post_exposure_caps, post_backfill)
        input_lane_counts = self._final_selection_source_lane_counts(input_live_candidates)
        final_lane_counts = self._final_selection_source_lane_counts(post_backfill)
        trace = {
            "input_live_candidates": self._board_stage_snapshot(input_live_candidates),
            "post_primary_selection": self._board_stage_snapshot(post_primary_selection),
            "post_exposure_caps": self._board_stage_snapshot(post_exposure_caps),
            "post_backfill": self._board_stage_snapshot(post_backfill),
            "candidate_count_before_final_board_build": int(len(input_live_candidates)),
            "core_pass_candidate_count": self._count_item_value(input_lane_counts, "core_pass"),
            "live_quality_rescue_candidate_count": self._count_item_value(input_lane_counts, "live_quality_rescue_pass"),
            "final_selected_count": int(len(post_backfill)),
            "final_selected_by_source_lane": final_lane_counts,
            "backfill_added_count": int(len(added_rows)),
            "backfill_added_by_qualification_gate_mode": self._qualification_gate_mode_counts(added_rows),
        }
        if isinstance(player_points_elite_admission, pd.DataFrame):
            admission_df = player_points_elite_admission.copy()
            if not admission_df.empty:
                trace["player_points_elite_admission_rows"] = [
                    {str(key): value for key, value in row.items()}
                    for row in admission_df.to_dict(orient="records")
                ]
            else:
                trace["player_points_elite_admission_rows"] = []
        else:
            trace["player_points_elite_admission_rows"] = []
        return trace

    def _backfill_board_min_size(
        self,
        current_df: pd.DataFrame,
        source_df: pd.DataFrame,
        *,
        min_size: int,
        candidate_fn: Any,
        per_player_cap: Optional[int] = None,
        per_team_cap: Optional[int] = None,
        per_game_cap: Optional[int] = None,
        priority_fn: Optional[Any] = None,
        sort_columns: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        if source_df.empty or len(current_df) >= int(min_size):
            return current_df.copy()

        kept_rows = [_to_str_dict(row) for _, row in current_df.iterrows()]
        seen_keys = {self._row_identity_key(row) for row in kept_rows}
        team_counts: dict[str, int] = {}
        game_counts: dict[str, int] = {}
        player_counts: dict[tuple[str, str], int] = {}

        def _track(row_dict: Mapping[str, Any]) -> None:
            team = str(row_dict.get("team", "")).strip().upper()
            opp = str(row_dict.get("opponent", "")).strip().upper()
            game_key = "__".join(sorted([team, opp])) if team and opp else ""
            market_type = str(row_dict.get("market_type", "")).strip().lower()
            player_key = (
                str(row_dict.get("entity_name", "")).strip().lower(),
                team,
            )

            if team:
                team_counts[team] = team_counts.get(team, 0) + 1
            if game_key:
                game_counts[game_key] = game_counts.get(game_key, 0) + 1
            if market_type.startswith("player_"):
                player_counts[player_key] = player_counts.get(player_key, 0) + 1

        for row_dict in kept_rows:
            _track(row_dict)

        sort_cols = sort_columns or self._sort_priority_columns(source_df)
        source_sorted = source_df.copy()
        sort_by: list[str] = []
        ascending: list[bool] = []
        if priority_fn is not None:
            source_sorted["_cv_backfill_priority"] = [
                int(priority_fn(_to_str_dict(row)))
                for _, row in source_sorted.iterrows()
            ]
            sort_by.append("_cv_backfill_priority")
            ascending.append(False)
        if sort_cols:
            sort_by.extend(sort_cols)
            ascending.extend([False] * len(sort_cols))
        if sort_by:
            source_sorted = source_sorted.sort_values(by=sort_by, ascending=ascending)

        for _, row in source_sorted.iterrows():
            if len(kept_rows) >= int(min_size):
                break

            row_dict = _to_str_dict(row)
            row_key = self._row_identity_key(row_dict)
            if row_key in seen_keys:
                continue
            if not candidate_fn(row_dict):
                continue

            team = str(row_dict.get("team", "")).strip().upper()
            opp = str(row_dict.get("opponent", "")).strip().upper()
            game_key = "__".join(sorted([team, opp])) if team and opp else ""
            market_type = str(row_dict.get("market_type", "")).strip().lower()
            player_key = (
                str(row_dict.get("entity_name", "")).strip().lower(),
                team,
            )

            if per_team_cap is not None and team_counts.get(team, 0) >= int(per_team_cap):
                continue
            if per_game_cap is not None and game_key and game_counts.get(game_key, 0) >= int(per_game_cap):
                continue
            if market_type.startswith("player_") and per_player_cap is not None:
                if player_counts.get(player_key, 0) >= int(per_player_cap):
                    continue

            kept_rows.append(row_dict)
            seen_keys.add(row_key)
            _track(row_dict)

        if not kept_rows:
            return pd.DataFrame()
        out = pd.DataFrame(kept_rows)
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
        return out

    def _select_elite_board(
        self,
        prepared_df: pd.DataFrame,
        *,
        input_df: Optional[pd.DataFrame] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> pd.DataFrame:
        if prepared_df.empty:
            if trace is not None:
                trace.update(
                    self._build_board_construction_trace(
                        input_live_candidates=pd.DataFrame(),
                        post_primary_selection=pd.DataFrame(),
                        post_exposure_caps=pd.DataFrame(),
                        post_backfill=pd.DataFrame(),
                    )
                )
            return prepared_df

        input_candidates_df = input_df.copy() if input_df is not None else prepared_df.copy()
        primary_df = input_candidates_df[[self._is_elite_candidate(_to_str_dict(row)) for _, row in input_candidates_df.iterrows()]].copy()

        sort_cols = self._sort_priority_columns(primary_df, elite_priority=True)
        if sort_cols:
            primary_df = primary_df.sort_values(by=sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)
        post_exposure_df = self._apply_player_exposure_caps(
            primary_df,
            per_player_cap=self.ELITE_MAX_PROPS_PER_PLAYER,
            sort_columns=sort_cols,
        )
        post_exposure_df = self._apply_team_exposure_caps(
            post_exposure_df,
            per_team_cap=self.ELITE_TEAM_CAP,
            per_game_cap=self.ELITE_GAME_CAP,
            sort_columns=sort_cols,
        )
        target_size = min(
            self.ELITE_BOARD_LIMIT,
            max(
                int(self.board_volume.config.elite_min_size),
                int(getattr(self.board_volume.config, "elite_target_size", self.board_volume.config.elite_min_size)),
            ),
        )
        final_df = self._backfill_board_min_size(
            post_exposure_df,
            input_candidates_df,
            min_size=target_size,
            candidate_fn=self.board_volume.is_elite_backfill_candidate,
            per_player_cap=self.ELITE_MAX_PROPS_PER_PLAYER,
            per_team_cap=self.ELITE_TEAM_CAP + 1,
            per_game_cap=self.ELITE_GAME_CAP + 1,
            priority_fn=self.board_volume.backfill_priority,
            sort_columns=sort_cols,
        )
        final_df = self._tag_final_selection_source_lane(
            final_df.head(self.ELITE_BOARD_LIMIT).reset_index(drop=True)
        )
        player_points_elite_admission_df = self._build_player_points_elite_admission(
            input_candidates_df=input_candidates_df,
            primary_df=primary_df,
            post_exposure_df=post_exposure_df,
            final_df=final_df,
            sort_columns=sort_cols,
        )

        if trace is not None:
            trace.update(
                self._build_board_construction_trace(
                    input_live_candidates=input_candidates_df,
                    post_primary_selection=primary_df,
                    post_exposure_caps=post_exposure_df,
                    post_backfill=final_df,
                    player_points_elite_admission=player_points_elite_admission_df,
                )
            )
        return final_df

    def _select_top_per_market(
        self,
        prepared_df: pd.DataFrame,
        per_market_limit: int = 20,
        *,
        input_df: Optional[pd.DataFrame] = None,
        trace: Optional[dict[str, Any]] = None,
    ) -> pd.DataFrame:
        if prepared_df.empty or "market_type" not in prepared_df.columns:
            if trace is not None:
                trace.update(
                    self._build_board_construction_trace(
                        input_live_candidates=pd.DataFrame(),
                        post_primary_selection=pd.DataFrame(),
                        post_exposure_caps=pd.DataFrame(),
                        post_backfill=pd.DataFrame(),
                    )
                )
            return prepared_df

        buckets: list[pd.DataFrame] = []
        input_candidates_df = input_df.copy() if input_df is not None else prepared_df.copy()
        sort_cols = self._sort_priority_columns(input_candidates_df)
        for market_type, grp in input_candidates_df.groupby("market_type", sort=False):
            bucket = grp.copy()
            if sort_cols:
                bucket = bucket.sort_values(by=sort_cols, ascending=[False] * len(sort_cols))
            buckets.append(bucket.head(per_market_limit))
        if not buckets:
            if trace is not None:
                trace.update(
                    self._build_board_construction_trace(
                        input_live_candidates=input_candidates_df,
                        post_primary_selection=pd.DataFrame(),
                        post_exposure_caps=pd.DataFrame(),
                        post_backfill=pd.DataFrame(),
                    )
                )
            return pd.DataFrame()
        primary_df = pd.concat(buckets, ignore_index=True)
        if sort_cols:
            primary_df = primary_df.sort_values(by=["market_type"] + sort_cols, ascending=[True] + [False] * len(sort_cols)).reset_index(drop=True)
        post_exposure_df = self._apply_team_exposure_caps(
            primary_df,
            per_team_cap=self.FULL_MARKET_TEAM_CAP,
            per_game_cap=self.FULL_MARKET_GAME_CAP,
            sort_columns=sort_cols,
        )
        target_size = max(
            int(self.board_volume.config.full_market_min_size),
            int(getattr(self.board_volume.config, "full_market_target_size", self.board_volume.config.full_market_min_size)),
        )
        final_df = self._backfill_board_min_size(
            post_exposure_df,
            input_candidates_df,
            min_size=target_size,
            candidate_fn=self.board_volume.is_full_market_backfill_candidate,
            per_player_cap=2,
            per_team_cap=self.FULL_MARKET_TEAM_CAP + 1,
            per_game_cap=self.FULL_MARKET_GAME_CAP + 2,
            priority_fn=self.board_volume.backfill_priority,
            sort_columns=sort_cols,
        )
        final_df = self._tag_final_selection_source_lane(final_df)
        if trace is not None:
            trace.update(
                self._build_board_construction_trace(
                    input_live_candidates=input_candidates_df,
                    post_primary_selection=primary_df,
                    post_exposure_caps=post_exposure_df,
                    post_backfill=final_df,
                )
            )
        return final_df

    def _build_final_operator_boards(
        self,
        prepared_df: pd.DataFrame,
        *,
        per_market_limit: int = 20,
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        if prepared_df.empty:
            return pd.DataFrame(), pd.DataFrame(), {
                "elite": self._build_board_construction_trace(
                    input_live_candidates=pd.DataFrame(),
                    post_primary_selection=pd.DataFrame(),
                    post_exposure_caps=pd.DataFrame(),
                    post_backfill=pd.DataFrame(),
                ),
                "full_market": self._build_board_construction_trace(
                    input_live_candidates=pd.DataFrame(),
                    post_primary_selection=pd.DataFrame(),
                    post_exposure_caps=pd.DataFrame(),
                    post_backfill=pd.DataFrame(),
                ),
            }

        live_candidates_df = self._live_market_only(prepared_df)
        final_board_construction: dict[str, Any] = {"elite": {}, "full_market": {}}
        elite_df = self._select_elite_board(
            prepared_df,
            input_df=live_candidates_df,
            trace=final_board_construction["elite"],
        )
        full_market_df = self._select_top_per_market(
            prepared_df,
            per_market_limit=per_market_limit,
            input_df=live_candidates_df,
            trace=final_board_construction["full_market"],
        )
        return elite_df, full_market_df, final_board_construction

    def _build_near_miss_board(self, rejected_df: pd.DataFrame, limit: int = 60) -> pd.DataFrame:
        if rejected_df.empty:
            return rejected_df

        near = rejected_df.copy()
        for col in ["confidence", "edge_abs", "sportsbook_line", "model_projection", "edge"]:
            if col in near.columns:
                near[col] = pd.to_numeric(near[col], errors="coerce")

        if "edge_abs" not in near.columns and "edge" in near.columns:
            near["edge_abs"] = near["edge"].abs()

        def _miss_distance(row: pd.Series) -> float:
            market_type = str(row.get("market_type", ""))
            thresholds = self.DEFAULT_THRESHOLDS.get(market_type)
            if not thresholds:
                return 999.0
            edge_short = max(0.0, float(thresholds["edge"]) - float(row.get("edge_abs") or 0.0))
            conf_short = max(0.0, float(thresholds["confidence"]) - float(row.get("confidence") or 0.0))
            return edge_short + (conf_short * 10.0)

        near["miss_distance"] = near.apply(_miss_distance, axis=1)
        if "rejection_reason" in near.columns:
            near = near[~near["rejection_reason"].astype(str).isin(["missing_market_lines", "missing_line_value", "no_games_found"])]
        near = near.sort_values(
            by=[c for c in ["miss_distance", "quality_score", "confidence", "edge_abs"] if c in near.columns],
            ascending=[True, False, False, False][:len([c for c in ["miss_distance", "quality_score", "confidence", "edge_abs"] if c in near.columns])]
        ).reset_index(drop=True)
        return near.head(limit)

    def _build_stat_only_board(
        self,
        prediction_date: str,
        games: pd.DataFrame,
        player_baselines: pd.DataFrame,
        team_lookup: dict[str, dict[str, Any]],
        league_context: Mapping[str, float],
        supported_markets: list[str],
        per_market_limit: int = 20,
        injury_context: Optional[Mapping[str, Any]] = None,
    ) -> pd.DataFrame:
        if games.empty or player_baselines.empty:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        supported_market_set = {str(m) for m in supported_markets}
        active_teams = set(games["home_team_abbr"].astype(str).tolist() + games["visitor_team_abbr"].astype(str).tolist())
        candidate_players = player_baselines[player_baselines["team_abbr"].astype(str).isin(active_teams)].copy()
        if candidate_players.empty:
            return pd.DataFrame()

        for _, game in games.iterrows():
            home = str(game["home_team_abbr"])
            away = str(game["visitor_team_abbr"])
            game_players = candidate_players[candidate_players["team_abbr"].astype(str).isin([home, away])].copy()
            for _, player in game_players.iterrows():
                team_abbr = str(player["team_abbr"])
                opp_abbr = away if team_abbr == home else home
                player_row = _to_str_dict(player)
                min_avg = self._to_float(player_row.get("min_avg")) or 0.0
                if min_avg < 12.0:
                    continue

                for market_type in PRIMARY_PLAYER_MARKETS:
                    stat_col = self.PLAYER_MARKETS[market_type]
                    model_projection = self._project_player_market(
                        player_row=player_row,
                        market_type=market_type,
                        opponent_row=team_lookup.get(opp_abbr),
                        league_context=league_context,
                    )
                    recent_avg = self._to_float(player_row.get(f"{stat_col}_recent")) or 0.0
                    season_avg = self._to_float(player_row.get(f"{stat_col}_avg")) or 0.0
                    stat_std = self._to_float(player_row.get(f"{stat_col}_std")) or 0.0
                    blended_anchor = (season_avg * 0.65) + (recent_avg * 0.35)
                    edge_like = model_projection - blended_anchor
                    confidence = self._player_confidence(
                        market_type=market_type,
                        edge_abs=abs(edge_like),
                        stat_std=stat_std,
                        minutes_avg=min_avg,
                        calibration={},
                    )
                    model_projection, confidence, injury_payload = self._apply_player_injury_context(
                        player_row=player_row,
                        team_abbr=team_abbr,
                        opp_abbr=opp_abbr,
                        market_type=market_type,
                        projection=model_projection,
                        confidence=confidence,
                        injury_context=injury_context,
                    )
                    edge_like = model_projection - blended_anchor
                    live_line_available = False
                    reason = "predictive_market_fill"
                    rows.append(
                        {
                            "prediction_date": prediction_date,
                            "market_type": market_type,
                            "raw_stat_key": stat_col,
                            "market_alias": market_type,
                            "entity_name": str(player_row.get("player_name", "")),
                            "team": team_abbr,
                            "opponent": opp_abbr,
                            "selection": "Projection",
                            "sportsbook_line": None,
                            "model_projection": round(float(model_projection), 4),
                            "edge": round(float(edge_like), 4),
                            "edge_abs": round(abs(float(edge_like)), 4),
                            "confidence": round(float(confidence), 4),
                            "odds": None,
                            "recent_avg": round(float(recent_avg), 4),
                            "season_avg": round(float(season_avg), 4),
                            "recent_form_flag": "HOT" if recent_avg > season_avg else ("COLD" if recent_avg < season_avg else "STABLE"),
                            "letter_grade": self._score_to_letter_grade(confidence * 100.0 + abs(edge_like) * 6.0),
                            "market_label": self._market_label(market_type),
                            "bet_label": f"{str(player_row.get('player_name', '')).strip()} PROJECTION {self._market_label(market_type).upper()}",
                            "quality_score": round(float(confidence * 100.0 + abs(edge_like) * 6.0), 4),
                            "board_source": "all_stats_projection",
                            "reason": reason,
                            "live_line_available": bool(live_line_available),
                            **injury_payload,
                        }
                    )

        out = pd.DataFrame(rows)
        if out.empty:
            return out
        prepared = self._prepare_selected_board(out)
        prepared = self._apply_strike_system(prepared)
        return self._select_top_per_market(prepared, per_market_limit=per_market_limit)

    def _build_missing_player_market_rows(
        self,
        prediction_date: str,
        games: pd.DataFrame,
        player_baselines: pd.DataFrame,
        team_lookup: dict[str, dict[str, Any]],
        league_context: Mapping[str, float],
        live_supported_markets: list[str],
        injury_context: Optional[Mapping[str, Any]] = None,
    ) -> pd.DataFrame:
        if games.empty or player_baselines.empty:
            return pd.DataFrame()

        live_supported = {str(market) for market in live_supported_markets}
        missing_markets = [market for market in PRIMARY_PLAYER_MARKETS if market not in live_supported]
        if not missing_markets:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        active_teams = set(games["home_team_abbr"].astype(str).tolist() + games["visitor_team_abbr"].astype(str).tolist())
        candidate_players = player_baselines[player_baselines["team_abbr"].astype(str).isin(active_teams)].copy()
        if candidate_players.empty:
            return pd.DataFrame()

        for _, game in games.iterrows():
            home = str(game["home_team_abbr"])
            away = str(game["visitor_team_abbr"])
            game_players = candidate_players[candidate_players["team_abbr"].astype(str).isin([home, away])].copy()

            for _, player in game_players.iterrows():
                player_row = _to_str_dict(player)
                team_abbr = str(player_row.get("team_abbr", ""))
                opp_abbr = away if team_abbr == home else home
                player_name = str(player_row.get("player_name", "")).strip()
                min_avg = self._to_float(player_row.get("min_avg")) or 0.0
                if min_avg < 18.0:
                    continue

                for market_type in missing_markets:
                    stat_col = self.PLAYER_MARKETS[market_type]
                    model_projection = self._project_player_market(
                        player_row=player_row,
                        market_type=market_type,
                        opponent_row=team_lookup.get(opp_abbr),
                        league_context=league_context,
                    )
                    stat_std = self._to_float(player_row.get(f"{stat_col}_std")) or 0.0
                    confidence = self._player_confidence(
                        market_type=market_type,
                        edge_abs=0.0,
                        stat_std=stat_std,
                        minutes_avg=min_avg,
                        calibration={},
                    )
                    model_projection, confidence, injury_payload = self._apply_player_injury_context(
                        player_row=player_row,
                        team_abbr=team_abbr,
                        opp_abbr=opp_abbr,
                        market_type=market_type,
                        projection=model_projection,
                        confidence=confidence,
                        injury_context=injury_context,
                    )

                    fair_line = self._round_to_market_step(model_projection, market_type)
                    edge = model_projection - fair_line
                    selection = "Over" if edge >= 0 else "Under"
                    grade_payload = self._grade_from_recent_matches(
                        market_type=market_type,
                        sportsbook_line=fair_line,
                        model_projection=model_projection,
                        confidence=confidence,
                        edge=edge,
                        player_row=player_row,
                    )
                    row = self._enrich_pick_row(
                        {
                            "prediction_date": prediction_date,
                            "market_type": market_type,
                            "entity_name": player_name,
                            "team": team_abbr,
                            "opponent": opp_abbr,
                            "selection": selection,
                            "sportsbook_line": round(float(fair_line), 4),
                            "model_projection": round(float(model_projection), 4),
                            "edge": round(float(edge), 4),
                            "edge_abs": round(abs(float(edge)), 4),
                            "confidence": round(float(confidence), 4),
                            "odds": None,
                            "raw_stat_key": stat_col,
                            "market_alias": market_type,
                            "market_label": self._market_label(market_type),
                            "board_source": "predictive_market_fill",
                            "reason": "missing_live_market_filled_from_projection",
                            "live_line_available": False,
                            **grade_payload,
                            **injury_payload,
                        }
                    )
                    rows.append(row)

        if not rows:
            return pd.DataFrame()

        out = pd.DataFrame(rows)
        out["synthetic_line"] = True
        out["is_live_market"] = False
        out["line_source"] = "predictive_market_fill"
        return out.reset_index(drop=True)

    def _build_team_board(
        self,
        prediction_date: str,
        games: pd.DataFrame,
        odds: pd.DataFrame,
        team_lookup: dict[str, dict[str, Any]],
        injury_context: Optional[Mapping[str, Any]] = None,
    ) -> pd.DataFrame:
        if games.empty:
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        for _, game in games.iterrows():
            home = str(game["home_team_abbr"])
            away = str(game["visitor_team_abbr"])
            home_team = team_lookup.get(home)
            away_team = team_lookup.get(away)
            if not home_team or not away_team:
                continue

            game_total_projection, home_team_projection, away_team_projection = self._project_team_totals(
                home_team,
                away_team,
                home_abbr=home,
                away_abbr=away,
                injury_context=injury_context,
            )
            home_win_prob = self._project_home_win_probability(home_team_projection, away_team_projection)
            away_win_prob = 1.0 - home_win_prob
            game_odds = odds[odds["game_id"] == game["game_id"]].copy() if not odds.empty else pd.DataFrame()

            for team_abbr, opp_abbr, team_projection, win_prob in [
                (home, away, home_team_projection, home_win_prob),
                (away, home, away_team_projection, away_win_prob),
            ]:
                baseline_points = self._blend_average_and_recent(
                    team_lookup[team_abbr].get("team_pts_avg"),
                    team_lookup[team_abbr].get("team_pts_recent"),
                )
                point_edge = team_projection - baseline_points
                rows.append(
                    {
                        "prediction_date": prediction_date,
                        "market_type": "team_projection",
                        "entity_name": f"{team_abbr} Projected Points",
                        "team": team_abbr,
                        "opponent": opp_abbr,
                        "selection": "Projection",
                        "sportsbook_line": None,
                        "model_projection": round(float(team_projection), 4),
                        "edge": round(float(point_edge), 4),
                        "edge_abs": round(abs(float(point_edge)), 4),
                        "confidence": round(float(min(max(0.55 + (abs(point_edge) / 20.0), 0.0), 0.82)), 4),
                        "odds": None,
                        "recent_avg": round(float(self._to_float(team_lookup[team_abbr].get("team_pts_recent")) or baseline_points), 4),
                        "season_avg": round(float(self._to_float(team_lookup[team_abbr].get("team_pts_avg")) or baseline_points), 4),
                        "recent_form_flag": "HOT" if (self._to_float(team_lookup[team_abbr].get("team_pts_recent")) or baseline_points) > (self._to_float(team_lookup[team_abbr].get("team_pts_avg")) or baseline_points) else "STABLE",
                        "letter_grade": self._score_to_letter_grade(58.0 + abs(point_edge) * 5.0),
                        "market_label": "Team Projection",
                        "bet_label": f"{team_abbr} PROJECTED POINTS",
                        "quality_score": round(float(58.0 + abs(point_edge) * 5.0), 4),
                        "board_source": "team_board_projection",
                        "reason": "projection_snapshot",
                    }
                )

                team_total_rows = game_odds[
                    (game_odds["market_type"] == "team_total")
                    & (game_odds["team"].astype(str).str.upper() == team_abbr.upper())
                ].copy()
                if not team_total_rows.empty:
                    team_total_rows = team_total_rows.sort_values(by=["line"], ascending=[True])
                    market = team_total_rows.iloc[0]
                    line_value = self._to_float(market.get("line"))
                    if line_value is not None:
                        edge = team_projection - line_value
                        selection = "Over" if edge >= 0 else "Under"
                        confidence = min(max(0.58 + (abs(edge) / 12.0), 0.0), 0.86)
                        rows.append(
                            {
                                "prediction_date": prediction_date,
                                "market_type": "team_total",
                                "entity_name": f"{team_abbr} Team Total",
                                "team": team_abbr,
                                "opponent": opp_abbr,
                                "selection": selection,
                                "sportsbook_line": round(float(line_value), 4),
                                "model_projection": round(float(team_projection), 4),
                                "edge": round(float(edge), 4),
                                "edge_abs": round(abs(float(edge)), 4),
                                "confidence": round(float(confidence), 4),
                                "odds": self._to_float(market.get("over_odds") if selection == "Over" else market.get("under_odds")),
                                "market_label": "Team Total",
                                "bet_label": f"{team_abbr} {selection.upper()} {line_value:.1f} TEAM TOTAL",
                                "quality_score": round(float(confidence * 100.0 + abs(edge) * 8.0), 4),
                                "board_source": "team_board_market",
                                "reason": "live_team_total_line",
                            }
                        )

                ml_rows = game_odds[game_odds["market_type"] == "moneyline"].copy()
                if not ml_rows.empty:
                    team_ml = ml_rows[
                        (
                            ml_rows["selection"].astype(str).str.upper() == team_abbr.upper()
                        ) | (
                            ml_rows["team"].astype(str).str.upper() == team_abbr.upper()
                        ) | (
                            ml_rows["side"].astype(str).str.lower()
                            == ("home" if team_abbr == home else "away")
                        )
                    ].copy()
                    if not team_ml.empty:
                        market = team_ml.iloc[0]
                        implied = self._american_odds_to_implied_prob(market.get("odds"))
                        if implied is not None:
                            ml_edge = win_prob - implied
                            ml_conf = min(max(0.60 + abs(ml_edge) * 2.2, 0.0), 0.88)
                            rows.append(
                                {
                                    "prediction_date": prediction_date,
                                    "market_type": "moneyline",
                                    "entity_name": f"{team_abbr} Moneyline",
                                    "team": team_abbr,
                                    "opponent": opp_abbr,
                                    "selection": f"{team_abbr} ML",
                                    "sportsbook_line": round(float(implied), 4),
                                    "model_projection": round(float(win_prob), 4),
                                    "edge": round(float(ml_edge), 4),
                                    "edge_abs": round(abs(float(ml_edge)), 4),
                                    "confidence": round(float(ml_conf), 4),
                                    "odds": self._to_float(market.get("odds")),
                                    "market_label": "Moneyline",
                                    "bet_label": f"{team_abbr} MONEYLINE {team_abbr} ML",
                                    "quality_score": round(float(ml_conf * 100.0 + abs(ml_edge) * 150.0), 4),
                                    "board_source": "team_board_market",
                                    "reason": "live_moneyline",
                                }
                            )

            total_recent_home = self._to_float(home_team.get("team_pts_recent")) or home_team_projection
            total_recent_away = self._to_float(away_team.get("team_pts_recent")) or away_team_projection
            game_total_anchor = total_recent_home + total_recent_away
            total_edge = game_total_projection - game_total_anchor
            rows.append(
                {
                    "prediction_date": prediction_date,
                    "market_type": "game_total_projection",
                    "entity_name": f"{away} @ {home} Game Total",
                    "team": home,
                    "opponent": away,
                    "selection": "Projection",
                    "sportsbook_line": None,
                    "model_projection": round(float(game_total_projection), 4),
                    "edge": round(float(total_edge), 4),
                    "edge_abs": round(abs(float(total_edge)), 4),
                    "confidence": round(float(min(max(0.56 + abs(total_edge) / 30.0, 0.0), 0.82)), 4),
                    "odds": None,
                    "market_label": "Game Total Projection",
                    "bet_label": f"{away} @ {home} GAME TOTAL PROJECTION",
                    "quality_score": round(float(56.0 + abs(total_edge) * 4.0), 4),
                    "board_source": "team_board_projection",
                    "reason": "projection_snapshot",
                }
            )

        out = pd.DataFrame(rows)
        if out.empty:
            return out

        out = pd.DataFrame([self._apply_scoring_metadata(_to_str_dict(row)) for _, row in out.iterrows()])
        out = self._apply_board_audit_frame(out)

        sort_cols = [c for c in ["market_type", "quality_score", "confidence", "edge_abs"] if c in out.columns]
        if sort_cols:
            out = out.sort_values(by=sort_cols, ascending=[True, False, False, False]).reset_index(drop=True)
        return out

    def _finalize_selected_board(self, selected_df: pd.DataFrame) -> pd.DataFrame:
        prepared = self._prepare_selected_board(selected_df)
        return self._select_elite_board(prepared)


    def _get_client(self) -> "BallDontLieClient":
        """Get BallDontLie data client."""
        if self.client is None:
            _load_env_file()
            api_key = clean_api_key(os.getenv(BALLDONTLIE_API_KEY_ENV_VAR, "")) or self.api_key
            self.client = BallDontLieClient(api_key=api_key, timeout=self.request_timeout)
            self.logger.info("[provider] using_balldontlie")
        return self.client


    def _get_sdk_injuries(self) -> pd.DataFrame:
        api_key = clean_api_key(os.getenv(BALLDONTLIE_API_KEY_ENV_VAR, "")) or self.api_key
        if not api_key or BalldontlieAPI is None:
            return pd.DataFrame()

        try:
            api = BalldontlieAPI(api_key=api_key)
        except Exception as exc:
            self.logger.warning("sdk_injuries_failed error=%s", exc)
            return pd.DataFrame()

        rows: list[dict[str, Any]] = []
        cursor: Optional[Any] = None

        while True:
            try:
                if cursor is None:
                    page = api.nba.injuries.list()
                else:
                    page = api.nba.injuries.list(cursor=cursor)
            except Exception as exc:
                self.logger.warning("sdk_injuries_page_failed cursor=%s error=%s", cursor, exc)
                break

            data = getattr(page, "data", None)
            if data is None:
                if isinstance(page, dict):
                    data = page.get("data", [])
                else:
                    data = []

            if not isinstance(data, list) or not data:
                break

            for item in data:
                if item is None:
                    continue

                player_name = ""
                if isinstance(item, dict):
                    player_name = str(item.get("player_name") or "").strip()
                    if not player_name and item.get("player"):
                        p = item.get("player")
                        if isinstance(p, dict):
                            first_name = str(p.get("first_name", "")).strip()
                            last_name = str(p.get("last_name", "")).strip()
                            player_name = f"{first_name} {last_name}".strip()
                else:
                    player_name = str(getattr(item, "player_name", None) or "").strip()
                    if not player_name:
                        p = getattr(item, "player", None)
                        if p and hasattr(p, "first_name") and hasattr(p, "last_name"):
                            player_name = f"{p.first_name} {p.last_name}".strip()

                rows.append(
                    {
                        "player.id": getattr(item, "id", None),
                        "player.first_name": getattr(item, "first_name", None),
                        "player.last_name": getattr(item, "last_name", None),
                        "player.team_id": getattr(item, "team_id", None),
                        "status": getattr(item, "status", None),
                        "description": getattr(item, "description", None),
                        "return_date": getattr(item, "return_date", None),
                    }
                )

            next_cursor = getattr(page, "next_cursor", None)
            if next_cursor is None and isinstance(page, dict):
                meta = page.get("meta", {})
                if isinstance(meta, dict):
                    next_cursor = meta.get("next_cursor") or meta.get("next_page")

            if not next_cursor:
                break

            cursor = next_cursor

        return pd.DataFrame(rows)

    def fit(self, train_start: str, train_end: str) -> dict[str, Any]:
        self.logger.info("fit_start train_start=%s train_end=%s", train_start, train_end)
        client = self._get_client()
        raw_stats = client.get_stats(train_start, train_end)
        stats = self._normalize_stats(raw_stats)

        if stats.empty:
            raise RuntimeError("No training stats were returned. Cannot fit model.")

        player_baselines = self._build_player_baselines(stats)
        team_baselines = self._build_team_baselines(stats)

        player_baselines.to_csv(self.player_baselines_path, index=False)
        team_baselines.to_csv(self.team_baselines_path, index=False)

        metrics = {
            "train_start": train_start,
            "train_end": train_end,
            "games_in_sample": int(stats["game_id"].nunique()) if "game_id" in stats.columns else 0,
            "players_in_sample": int(stats["player_id"].nunique()) if "player_id" in stats.columns else 0,
            "stat_rows": int(len(stats)),
        }

        self.logger.info("fit_complete metrics=%s", _safe_json_dumps(metrics))
        self._log_run(
            {
                "run_type": "fit",
                "train_start": train_start,
                "train_end": train_end,
                "prediction_date": "",
                "selected_count": 0,
                "rejected_count": 0,
                "notes": json.dumps(metrics),
            }
        )

        return metrics

    def predict(self, prediction_date: str) -> dict[str, Any]:
        self.logger.info("predict_start prediction_date=%s", prediction_date)
        print(f"[STAGE] predict_method_start date={prediction_date}")
        client = self._get_client()
        player_baselines = self._safe_read_csv(self.player_baselines_path)
        team_baselines = self._safe_read_csv(self.team_baselines_path)

        if player_baselines.empty or team_baselines.empty:
            raise RuntimeError("Model baselines not found. Run fit() first.")

        calibration = self._load_calibration_rules()
        league_context = self._build_league_context(team_baselines)

        # Build team_lookup from team_baselines for opponent lookups
        # Maps team abbreviation to team baseline row dict
        team_lookup: dict[str, dict[str, Any]] = {}
        if not team_baselines.empty and "team_abbr" in team_baselines.columns:
            team_lookup = {
                str(row["team_abbr"]): {str(k): v for k, v in row.to_dict().items()}
                for _, row in team_baselines.iterrows()
                if pd.notna(row.get("team_abbr"))
            }

        games_raw = client.get_games(prediction_date)
        games = self._normalize_games(games_raw)
        game_ids = [int(game_id) for game_id in games["game_id"].dropna().tolist()] if not games.empty else []
        print(f"[STAGE] provider_fetch_start games={len(game_ids)}")
        print(f"[COUNT] active_odds_fetch_path=courtvision_ai.py:predict:client.get_odds", flush=True)
        print(f"[COUNT] games_for_odds={len(game_ids)}", flush=True)
        odds_raw = client.get_odds(prediction_date, game_ids=game_ids)
        print(f"[COUNT] odds_rows_after_fetch={len(odds_raw) if hasattr(odds_raw, '__len__') else 0}", flush=True)

        # Diagnostic logging for player_name in odds
        if not odds_raw.empty and "player_name" in odds_raw.columns:
            sample_names = odds_raw["player_name"].head(10).tolist()
            null_count = odds_raw["player_name"].isna().sum() + (odds_raw["player_name"] == "").sum()
            self.logger.info(
                "odds_raw_player_name sample=%s null_count=%d total=%d",
                sample_names,
                null_count,
                len(odds_raw),
            )
            # Warning: log if >50% null player names but continue gracefully
            if len(odds_raw) > 0 and null_count / len(odds_raw) > 0.5:
                # Get diagnostics from player prop processing
                print(f"[WARNING] player_name missing in odds: {null_count}/{len(odds_raw)} rows unresolved", flush=True)
                print(f"[DIAGNOSIS] Skipping player prop candidate generation due to unresolved player names", flush=True)
                # Filter out player prop rows with null player_name, keeping only team markets
                odds_raw = odds_raw[odds_raw["player_name"].notna() & (odds_raw["player_name"] != "")].copy()
                print(f"[DIAGNOSIS] Continuing with {len(odds_raw)} rows that have valid player_name", flush=True)

        odds = self._normalize_odds(odds_raw)
        sdk_injuries_raw = self._get_sdk_injuries()
        sdk_raw_rows = len(sdk_injuries_raw) if isinstance(sdk_injuries_raw, pd.DataFrame) else 0
        sdk_injuries = self._normalize_injuries(sdk_injuries_raw) if isinstance(sdk_injuries_raw, pd.DataFrame) else pd.DataFrame()
        sdk_normalized_rows = len(sdk_injuries) if isinstance(sdk_injuries, pd.DataFrame) else 0
        sdk_identity_coverage = self._injury_identity_coverage(sdk_injuries_raw)
        sdk_unusable_reason = ""
        if sdk_raw_rows > 0 and sdk_normalized_rows == 0:
            sdk_unusable_reason = "raw_rows_without_normalized_rows"
        if sdk_raw_rows > 0 and sdk_identity_coverage == 0:
            sdk_unusable_reason = (
                f"{sdk_unusable_reason};identity_coverage_zero"
                if sdk_unusable_reason
                else "identity_coverage_zero"
            )

        http_fallback_attempted = sdk_raw_rows == 0 or bool(sdk_unusable_reason)
        http_injuries_raw = pd.DataFrame()
        http_injuries = pd.DataFrame()
        http_raw_rows = 0
        http_normalized_rows = 0
        injury_source = "sdk"
        injuries_raw = sdk_injuries_raw if isinstance(sdk_injuries_raw, pd.DataFrame) else pd.DataFrame()
        injuries = sdk_injuries
        if http_fallback_attempted:
            http_injuries_raw = client.get_injuries(prediction_date)
            http_raw_rows = len(http_injuries_raw) if isinstance(http_injuries_raw, pd.DataFrame) else 0
            http_injuries = self._normalize_injuries(http_injuries_raw) if isinstance(http_injuries_raw, pd.DataFrame) else pd.DataFrame()
            http_normalized_rows = len(http_injuries) if isinstance(http_injuries, pd.DataFrame) else 0
            if http_normalized_rows > 0:
                injuries_raw = http_injuries_raw
                injuries = http_injuries
                injury_source = "http"
            elif sdk_normalized_rows > 0:
                injuries_raw = sdk_injuries_raw
                injuries = sdk_injuries
                injury_source = "sdk"
            else:
                injuries_raw = http_injuries_raw if isinstance(http_injuries_raw, pd.DataFrame) and not http_injuries_raw.empty else sdk_injuries_raw
                injuries = pd.DataFrame()
                injury_source = "http_failed" if http_raw_rows == 0 else "http_unusable"

        print(f"[INJURY] sdk_raw_rows={sdk_raw_rows}", flush=True)
        print(f"[INJURY] sdk_normalized_rows={sdk_normalized_rows}", flush=True)
        print(f"[INJURY] sdk_identity_coverage={sdk_identity_coverage}", flush=True)
        print(f"[INJURY] sdk_unusable_reason={sdk_unusable_reason or 'none'}", flush=True)
        print(f"[INJURY] http_fallback_attempted={str(bool(http_fallback_attempted)).lower()}", flush=True)
        print(f"[INJURY] http_raw_rows={http_raw_rows}", flush=True)
        print(f"[INJURY] http_normalized_rows={http_normalized_rows}", flush=True)
        self.logger.info(
            "injury_fetch_complete source=%s rows=%s prediction_date=%s sdk_raw=%d sdk_normalized=%d sdk_identity_coverage=%d http_attempted=%s http_raw=%d http_normalized=%d",
            injury_source,
            len(injuries_raw) if isinstance(injuries_raw, pd.DataFrame) else 0,
            prediction_date,
            sdk_raw_rows,
            sdk_normalized_rows,
            sdk_identity_coverage,
            bool(http_fallback_attempted),
            http_raw_rows,
            http_normalized_rows,
        )
        print("[COUNT] active_odds_fetch_path=courtvision_ai.py:active_provider_fetch_section", flush=True)
        print(f"[COUNT] games_for_odds={len(games)}", flush=True)
        print(f"[COUNT] odds_raw_type={type(odds_raw)}", flush=True)
        print(f"[COUNT] odds_raw_rows={len(odds_raw) if hasattr(odds_raw, '__len__') else 'unknown'}", flush=True)
        if hasattr(odds_raw, "__len__") and len(odds_raw) == 0:
            print("[DIAGNOSIS] active odds fetch returned zero rows", flush=True)
        print(f"[STAGE] provider_fetch_complete games={len(games)} odds={len(odds_raw)} injuries={len(injuries_raw) if isinstance(injuries_raw, pd.DataFrame) else 0}")
        if injuries.empty:
            self.logger.warning(
                "injury_data_empty_after_normalization prediction_date=%s source=%s",
                prediction_date,
                injury_source,
            )
        raw_market_names = (
            sorted(str(name) for name in odds_raw["raw_market_name"].dropna().astype(str).unique().tolist())
            if not odds_raw.empty and "raw_market_name" in odds_raw.columns
            else []
        )
        raw_market_name_counts = (
            odds_raw["raw_market_name"].fillna("null").astype(str).value_counts().head(30).to_dict()
            if not odds_raw.empty and "raw_market_name" in odds_raw.columns
            else {}
        )
        odds_supported_markets = (
            sorted(str(market) for market in odds["market_type"].dropna().unique().tolist())
            if not odds.empty
            else []
        )

        selected_rows: list[dict[str, Any]] = []
        rejected_rows: list[dict[str, Any]] = []

        print("[STAGE] prediction_processing_start")
        if games.empty:
            board_diagnostics = self._build_board_diagnostics(
                prediction_date=prediction_date,
                qualified_pool_df=pd.DataFrame(),
                elite_df=pd.DataFrame(),
                full_market_df=pd.DataFrame(),
                rejected_df=pd.DataFrame(),
            )
            summary = {
                "prediction_date": prediction_date,
                "games_analyzed": 0,
                "players_evaluated": 0,
                "markets_evaluated": 0,
                "selected_count": 0,
                "rejected_count": 0,
                "top_qualification_reasons": [],
                "top_rejection_reasons": [{"reason": "no_games_found", "count": 1}],
                "data_status": "No games found for selected date.",
                "odds_diagnostics": {
                    "fetch_status": client.last_odds_status,
                    "message": client.last_odds_message,
                    "raw_rows": int(len(odds_raw)),
                    "supported_rows": int(len(odds)),
                    "supported_markets": odds_supported_markets,
                    "raw_market_names": raw_market_names,
                    "raw_market_name_counts": raw_market_name_counts,
                },
                "model_diagnostics": {
                    "player_baseline_rows": int(len(player_baselines)),
                    "team_baseline_rows": int(len(team_baselines)),
                    "calibrated_markets": sorted(calibration.keys()),
                },
            }
            print("[STAGE] prediction_empty_return")
            return {
                "selected_props": pd.DataFrame(),
                "elite_props": pd.DataFrame(),
                "qualified_pool_props": pd.DataFrame(),
                "full_market_props": pd.DataFrame(),
                "near_miss_props": pd.DataFrame(),
                "stat_only_props": pd.DataFrame(),
                "all_stats_props": pd.DataFrame(),
                "team_board_props": pd.DataFrame(),
                "rejected_props": pd.DataFrame(),
                "board_diagnostics": board_diagnostics,
                "summary": summary,
                "games": games,
                "odds": odds,
            }

        # Delegate prediction orchestration to package pipeline
        # Thresholds now come from PredictionConfig defaults, not legacy monolith attributes
        elite_market_mode = os.getenv("ELITE_MARKET_MODE", "points_only").strip().lower() or "points_only"
        elite_allowed_markets_raw = os.getenv("ELITE_ALLOWED_MARKETS", "").strip()
        elite_allowed_markets = tuple(
            part.strip()
            for part in elite_allowed_markets_raw.split(",")
            if part.strip()
        )
        pipeline_config = PredictionConfig(
            prediction_date=prediction_date,
            enable_partial_fill=True,
            out_dir=str(self.out_dir),
            elite_market_mode=elite_market_mode,
            elite_allowed_markets=elite_allowed_markets,
        )
        pipeline = PredictionPipeline(pipeline_config)
        
        # Run package pipeline for orchestration
        print("[STAGE] prediction_pipeline_run_start")
        result = pipeline.run(
            games=games,
            odds=odds,
            player_baselines=player_baselines,
            team_baselines=team_baselines,
            injuries=injuries,
        )
        print("[STAGE] prediction_pipeline_run_complete")

        # Authoritative pipeline mode (default): use package pipeline outputs directly.
        # Legacy post-processing path remains available only when explicitly enabled.
        legacy_pipeline_enabled = os.getenv("COURTVISION_ENABLE_LEGACY_PIPELINE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not legacy_pipeline_enabled:
            elite_df = result.elite_props.copy() if not result.elite_props.empty else pd.DataFrame()
            full_market_df = result.full_market_props.copy() if not result.full_market_props.empty else pd.DataFrame()
            qualified_pool_df = result.merged_market_props.copy() if not result.merged_market_props.empty else pd.DataFrame()
            (
                qualified_pool_df,
                elite_df,
                full_market_df,
                manual_context_diagnostics,
                manual_context_path,
            ) = self._attach_manual_player_context(
                prediction_date=prediction_date,
                qualified_pool_df=qualified_pool_df,
                elite_df=elite_df,
                full_market_df=full_market_df,
            )
            rejected_df = pd.DataFrame()
            grading_df, grading_summary = self._grade_history(prediction_date=prediction_date)
            grading_bucket_summary = summarize_graded_props(grading_df.to_dict("records")) if not grading_df.empty else summarize_graded_props([])
            self._append_history(self.prediction_history_path, elite_df if not elite_df.empty else qualified_pool_df)
            board_diagnostics = {
                "board_counts": {
                    "elite": int(len(elite_df)),
                    "full_market": int(len(full_market_df)),
                    "qualified_pool": int(len(qualified_pool_df)),
                },
                "pipeline_mode": "authoritative_package_pipeline",
                "legacy_pipeline_enabled": False,
            }
            summary = dict(result.summary or {})
            summary.setdefault("prediction_date", prediction_date)
            summary["pipeline_mode"] = "authoritative_package_pipeline"
            summary["legacy_pipeline_enabled"] = False
            summary["selected_count"] = int(len(elite_df))
            summary["elite_count"] = int(len(elite_df))
            summary["full_market_count"] = int(len(full_market_df))
            summary["markets_evaluated"] = int(len(qualified_pool_df))
            summary["manual_context_diagnostics_path"] = str(manual_context_path)
            summary["manual_context"] = {
                "file_found": bool(manual_context_diagnostics.get("file_found", False)),
                "rows": int(manual_context_diagnostics.get("rows", 0) or 0),
                "candidate_matches": int(manual_context_diagnostics.get("candidate_matches", 0) or 0),
                "passive_mode": True,
            }
            injury_diagnostics = self._build_injury_context_diagnostics(
                prediction_date=prediction_date,
                injury_source=injury_source,
                injuries_raw=injuries_raw,
                injuries=injuries,
                games=games,
                player_baselines=player_baselines,
                candidate_df=qualified_pool_df,
                injury_context=result.injury_context,
                fetch_diagnostics={
                    "sdk_raw_rows": sdk_raw_rows,
                    "sdk_normalized_rows": sdk_normalized_rows,
                    "sdk_identity_coverage": sdk_identity_coverage,
                    "sdk_unusable_reason": sdk_unusable_reason or "",
                    "http_fallback_attempted": bool(http_fallback_attempted),
                    "http_raw_rows": http_raw_rows,
                    "http_normalized_rows": http_normalized_rows,
                    "http_identity_coverage": self._injury_identity_coverage(http_injuries_raw),
                    "http_raw_columns": [str(c) for c in http_injuries_raw.columns.tolist()] if isinstance(http_injuries_raw, pd.DataFrame) else [],
                    "http_normalized_columns": [str(c) for c in http_injuries.columns.tolist()] if isinstance(http_injuries, pd.DataFrame) else [],
                    "sdk_raw_columns": [str(c) for c in sdk_injuries_raw.columns.tolist()] if isinstance(sdk_injuries_raw, pd.DataFrame) else [],
                    "sdk_normalized_columns": [str(c) for c in sdk_injuries.columns.tolist()] if isinstance(sdk_injuries, pd.DataFrame) else [],
                },
            )
            self._emit_injury_context_diagnostics(injury_diagnostics)
            injury_json_path, injury_report_path = self._write_injury_context_diagnostics(injury_diagnostics)
            summary["injury_context_diagnostics_path"] = str(injury_json_path)
            summary["injury_context_report_path"] = str(injury_report_path)
            self._log_run(
                {
                    "run_type": "predict",
                    "train_start": "",
                    "train_end": "",
                    "prediction_date": prediction_date,
                    "selected_count": int(len(elite_df)),
                    "rejected_count": 0,
                    "notes": json.dumps(summary),
                }
            )
            return {
                "selected_props": elite_df,
                "elite_props": elite_df,
                "qualified_pool_props": qualified_pool_df,
                "full_market_props": full_market_df,
                "stat_only_props": pd.DataFrame(),
                "all_stats_props": pd.DataFrame(),
                "strike_props": pd.DataFrame(),
                "high_upside_props": pd.DataFrame(),
                "predictive_lines_props": pd.DataFrame(),
                "predictive_market_fill_props": pd.DataFrame(),
                "premarket_line_board": pd.DataFrame(),
                "sgp_props": pd.DataFrame(),
                "sgp_board": pd.DataFrame(),
                "grading_results": grading_df,
                "grading_bucket_summary": grading_bucket_summary,
                "team_board_props": pd.DataFrame(),
                "near_miss_props": pd.DataFrame(),
                "rejected_props": rejected_df,
                "board_diagnostics": board_diagnostics,
                "final_board_construction": summary.get("final_board_construction", {}),
                "summary": summary,
                "games": games,
                "odds": odds,
            }
        
        # Extract pipeline results for backward-compatible board building
        selected_df = result.selected_props.copy() if not result.selected_props.empty else pd.DataFrame()
        # Note: rejected_props not tracked by package pipeline; near_miss board will be empty
        rejected_df = pd.DataFrame()

        prepared_selected_df = pd.DataFrame()
        elite_df = pd.DataFrame()
        full_market_df = pd.DataFrame()
        stat_only_df = pd.DataFrame()
        near_miss_df = pd.DataFrame()
        final_board_construction = {
            "elite": self._build_board_construction_trace(
                input_live_candidates=pd.DataFrame(),
                post_primary_selection=pd.DataFrame(),
                post_exposure_caps=pd.DataFrame(),
                post_backfill=pd.DataFrame(),
            ),
            "full_market": self._build_board_construction_trace(
                input_live_candidates=pd.DataFrame(),
                post_primary_selection=pd.DataFrame(),
                post_exposure_caps=pd.DataFrame(),
                post_backfill=pd.DataFrame(),
            ),
        }

        # Use pipeline results directly for boards
        if not selected_df.empty:
            selected_df = pd.DataFrame([self._enrich_pick_row(_to_str_dict(row)) for _, row in selected_df.iterrows()])
            prepared_selected_df = self._prepare_selected_board(selected_df)
        
        # Get elite, full_market, and injury_context from pipeline results
        elite_df = result.elite_props.copy() if not result.elite_props.empty else pd.DataFrame()
        full_market_df = result.full_market_props.copy() if not result.full_market_props.empty else pd.DataFrame()
        injury_context = result.injury_context  # Extract from pipeline output
        
        # Build construction trace for diagnostics
        final_board_construction = {
            "elite": self._build_board_construction_trace(
                input_live_candidates=result.merged_market_props.copy() if not result.merged_market_props.empty else pd.DataFrame(),
                post_primary_selection=elite_df.copy(),
            ),
            "full_market": self._build_board_construction_trace(
                input_live_candidates=result.merged_market_props.copy() if not result.merged_market_props.empty else pd.DataFrame(),
                post_primary_selection=full_market_df.copy(),
            ),
        }

        if not rejected_df.empty:
            rejected_df = self._apply_board_audit_frame(rejected_df)
            rejected_df = rejected_df.sort_values(
                by=["market_type", "rejection_reason", "entity_name"]
            ).reset_index(drop=True)
            near_miss_df = self._build_near_miss_board(rejected_df, limit=60)

        stat_only_df = self._build_stat_only_board(
            prediction_date=prediction_date,
            games=games,
            player_baselines=player_baselines,
            team_lookup=team_lookup,
            league_context=league_context,
            supported_markets=odds_supported_markets,
            per_market_limit=20,
            injury_context=injury_context,
        )
        if not stat_only_df.empty:
            stat_only_df = self._apply_team_exposure_caps(
                stat_only_df,
                per_team_cap=self.STAT_ONLY_TEAM_CAP,
                per_game_cap=self.STAT_ONLY_GAME_CAP,
            )
        predictive_fill_df = self._build_missing_player_market_rows(
            prediction_date=prediction_date,
            games=games,
            player_baselines=player_baselines,
            team_lookup=team_lookup,
            league_context=league_context,
            live_supported_markets=odds_supported_markets,
            injury_context=injury_context,
        )
        if not predictive_fill_df.empty:
            predictive_fill_df = self._prepare_selected_board(predictive_fill_df)

        strike_source_df = stat_only_df.copy() if not stat_only_df.empty else pd.DataFrame()
        if not predictive_fill_df.empty:
            strike_source_df = pd.concat([strike_source_df, predictive_fill_df], ignore_index=True, sort=False) if not strike_source_df.empty else predictive_fill_df.copy()
            strike_source_df = self._prepare_selected_board(strike_source_df)
            dedupe_cols = [c for c in ["prediction_date", "market_type", "entity_name", "team", "opponent", "selection"] if c in strike_source_df.columns]
            if dedupe_cols:
                strike_source_df = strike_source_df.drop_duplicates(subset=dedupe_cols, keep="first").reset_index(drop=True)

        strike_df = self._build_strike_board(strike_source_df if not strike_source_df.empty else stat_only_df)
        predictive_source_df = strike_df if not strike_df.empty else (predictive_fill_df if not predictive_fill_df.empty else stat_only_df)
        predictive_lines_df = self._build_predictive_lines_board(predictive_source_df)

        sgp_df = self._build_sgp_board(
            prediction_date=prediction_date,
            games=games,
            elite_df=elite_df,
            full_market_df=full_market_df,
        )
        team_board_df = self._build_team_board(
            prediction_date=prediction_date,
            games=games,
            odds=odds,
            team_lookup=team_lookup,
            injury_context=injury_context,
        )
        if not team_board_df.empty:
            team_board_df = self._apply_team_exposure_caps(
                team_board_df,
                per_team_cap=self.TEAM_BOARD_TEAM_CAP,
                per_game_cap=None,
            )

        prepared_selected_df = self._apply_board_audit_frame(prepared_selected_df) if not prepared_selected_df.empty else prepared_selected_df
        elite_df = self._apply_board_audit_frame(elite_df) if not elite_df.empty else elite_df
        full_market_df = self._apply_board_audit_frame(full_market_df) if not full_market_df.empty else full_market_df
        stat_only_df = self._apply_board_audit_frame(stat_only_df) if not stat_only_df.empty else stat_only_df
        predictive_fill_df = self._apply_board_audit_frame(predictive_fill_df) if not predictive_fill_df.empty else predictive_fill_df
        strike_df = self._apply_board_audit_frame(strike_df) if not strike_df.empty else strike_df
        predictive_lines_df = self._apply_board_audit_frame(predictive_lines_df) if not predictive_lines_df.empty else predictive_lines_df
        sgp_df = self._apply_board_audit_frame(sgp_df) if not sgp_df.empty else sgp_df
        team_board_df = self._apply_board_audit_frame(team_board_df) if not team_board_df.empty else team_board_df
        near_miss_df = self._apply_board_audit_frame(near_miss_df) if not near_miss_df.empty else near_miss_df
        (
            prepared_selected_df,
            elite_df,
            full_market_df,
            manual_context_diagnostics,
            manual_context_path,
        ) = self._attach_manual_player_context(
            prediction_date=prediction_date,
            qualified_pool_df=prepared_selected_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
        )
        graded_df, grading_summary = self._grade_history(prediction_date=prediction_date)
        grading_bucket_summary = summarize_graded_props(graded_df.to_dict("records")) if not graded_df.empty else summarize_graded_props([])

        self._append_history(self.prediction_history_path, prepared_selected_df if not prepared_selected_df.empty else elite_df)
        self._append_history(self.rejection_history_path, rejected_df)

        if not rejected_df.empty and "rejection_reason" in rejected_df.columns:
            reason_counts = (
                rejected_df["rejection_reason"]
                .value_counts()
                .rename_axis("reason")
                .reset_index(name="count")
            )
        else:
            reason_counts = pd.DataFrame(columns=["reason", "count"])

        board_diagnostics = self._build_board_diagnostics(
            prediction_date=prediction_date,
            qualified_pool_df=prepared_selected_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
            rejected_df=rejected_df,
            final_board_construction=final_board_construction,
        )

        summary = {
            "prediction_date": prediction_date,
            "games_analyzed": int(len(games)),
            "players_evaluated": int(
                player_baselines[
                    player_baselines["team_abbr"].isin(
                        games["home_team_abbr"].tolist() + games["visitor_team_abbr"].tolist()
                    )
                ]["player_id"].nunique()
            ),
            "markets_evaluated": int(len(selected_df) + len(rejected_df)),
            "selected_count": int(len(elite_df)),
            "elite_count": int(len(elite_df)),
            "full_market_count": int(len(full_market_df)),
            "stat_only_count": int(len(stat_only_df)),
            "strike_count": int(len(strike_df)),
            "predictive_lines_count": int(len(predictive_lines_df)),
            "team_board_count": int(len(team_board_df)),
            "sgp_count": int(len(sgp_df)),
            "grading_rows": int(len(graded_df)),
            "near_miss_count": int(len(near_miss_df)),
            "rejected_count": int(len(rejected_df)),
            "manual_context_diagnostics_path": str(manual_context_path),
            "manual_context": {
                "file_found": bool(manual_context_diagnostics.get("file_found", False)),
                "rows": int(manual_context_diagnostics.get("rows", 0) or 0),
                "candidate_matches": int(manual_context_diagnostics.get("candidate_matches", 0) or 0),
                "passive_mode": True,
            },
            "top_qualification_reasons": board_diagnostics.get("qualified_by_reason", [])[:8],
            "top_rejection_reasons": reason_counts.head(8).to_dict(orient="records"),
            "final_board_construction": board_diagnostics.get("final_board_construction", {}),
            "data_status": self._build_data_status_message(
                games=games,
                odds=odds,
                selected_df=elite_df,
                rejected_df=rejected_df,
                odds_fetch_status=client.last_odds_status,
                odds_fetch_message=client.last_odds_message,
                raw_odds_rows=int(len(odds_raw)),
            ),
            "odds_diagnostics": {
                "fetch_status": client.last_odds_status,
                "message": client.last_odds_message,
                "raw_rows": int(len(odds_raw)),
                "supported_rows": int(len(odds)),
                "supported_markets": odds_supported_markets,
            },
            "model_diagnostics": {
                "player_baseline_rows": int(len(player_baselines)),
                "team_baseline_rows": int(len(team_baselines)),
                "calibrated_markets": sorted(calibration.keys()),
            },
        }

        summary["board_type"] = "multi_board_operator_mode"
        summary["board_counts"] = {
            "elite": int(len(elite_df)),
            "full_market": int(len(full_market_df)),
            "stat_only": int(len(stat_only_df)),
            "strike": int(len(strike_df)),
            "predictive_lines": int(len(predictive_lines_df)),
            "sgp": int(len(sgp_df)),
            "team_board": int(len(team_board_df)),
            "near_miss": int(len(near_miss_df)),
        }
        summary["grading_system"] = grading_summary
        summary["grading_bucket_overview"] = grading_bucket_summary.get("overall", {})
        missing_markets = [market for market in PRIMARY_PLAYER_MARKETS if market not in odds_supported_markets]
        summary["strike_system"] = {
            "board_name": "shadow_market_strike_system",
            "strike_count": int(len(strike_df)),
            "min_confidence": float(self.STRIKE_MIN_CONFIDENCE),
            "min_quality_score": float(self.STRIKE_MIN_QUALITY_SCORE),
            "market_multipliers": dict(self.STRIKE_MARKET_MULTIPLIERS),
        }
        summary["predictive_lines_engine"] = {
            "board_name": "predictive_lines_engine",
            "predictive_lines_count": int(len(predictive_lines_df)),
            "predictive_market_fill_count": int(len(predictive_fill_df)) if "predictive_fill_df" in locals() else 0,
            "min_confidence": float(self.PREDICTIVE_LINE_MIN_CONFIDENCE),
            "min_quality_score": float(self.PREDICTIVE_LINE_MIN_QUALITY_SCORE),
            "line_steps": dict(self.PREDICTIVE_LINE_STEPS),
        }
        active_injuries_df = injury_context.get("active_injuries", pd.DataFrame()) if isinstance(injury_context, Mapping) else pd.DataFrame()
        team_impacts = injury_context.get("teams", {}) if isinstance(injury_context, Mapping) else {}
        summary["injury_impact"] = {
            "active_injury_rows": int(len(active_injuries_df)) if isinstance(active_injuries_df, pd.DataFrame) else 0,
            "teams_with_material_injuries": int(sum(1 for payload in team_impacts.values() if float(payload.get("impact_score") or 0.0) >= 0.15)) if isinstance(team_impacts, Mapping) else 0,
            "top_team_impacts": sorted([
                {"team": str(team), **{k: v for k, v in payload.items() if k in {"impact_score", "usage_boost", "offense_penalty", "defense_penalty", "affected_players", "status_mix"}}}
                for team, payload in (team_impacts.items() if isinstance(team_impacts, Mapping) else [])
            ], key=lambda row: float(row.get("impact_score") or 0.0), reverse=True)[:8],
        }
        summary["injury_source"] = injury_source
        summary["injury_rows_raw"] = int(len(injuries_raw)) if isinstance(injuries_raw, pd.DataFrame) else 0
        summary["injury_rows_normalized"] = int(len(injuries))
        summary["missing_player_markets_with_no_live_lines"] = missing_markets
        summary["missing_player_stats_with_no_live_lines"] = [
            {
                "market_type": market,
                "raw_stat_key": self._market_to_stat_key(market),
                "market_label": self._market_label(market),
            }
            for market in missing_markets
        ]
        summary["team_exposure"] = {
            "elite_top_teams": self._team_exposure_summary(elite_df),
            "full_market_top_teams": self._team_exposure_summary(full_market_df),
            "stat_only_top_teams": self._team_exposure_summary(stat_only_df),
            "strike_top_teams": self._team_exposure_summary(strike_df),
            "predictive_top_teams": self._team_exposure_summary(predictive_lines_df),
            "team_board_top_teams": self._team_exposure_summary(team_board_df),
        }
        summary["exposure_caps"] = {
            "elite_team_cap": int(self.ELITE_TEAM_CAP),
            "elite_game_cap": int(self.ELITE_GAME_CAP),
            "full_market_team_cap": int(self.FULL_MARKET_TEAM_CAP),
            "full_market_game_cap": int(self.FULL_MARKET_GAME_CAP),
            "strike_team_cap": int(self.STRIKE_TEAM_CAP),
            "strike_game_cap": int(self.STRIKE_GAME_CAP),
            "predictive_team_cap": int(self.PREDICTIVE_TEAM_CAP),
            "predictive_game_cap": int(self.PREDICTIVE_GAME_CAP),
            "stat_only_team_cap": int(self.STAT_ONLY_TEAM_CAP),
            "stat_only_game_cap": int(self.STAT_ONLY_GAME_CAP),
            "team_board_team_cap": int(self.TEAM_BOARD_TEAM_CAP),
        }

        self.logger.info("predict_complete summary=%s", _safe_json_dumps(summary))
        self._log_run(
            {
                "run_type": "predict",
                "train_start": "",
                "train_end": "",
                "prediction_date": prediction_date,
                "selected_count": int(len(elite_df)),
                "rejected_count": int(len(rejected_df)),
                "notes": json.dumps(summary),
            }
        )
        print("[STAGE] prediction_processing_complete")
        return {
            "selected_props": elite_df,
            "elite_props": elite_df,
            "qualified_pool_props": prepared_selected_df,
            "full_market_props": full_market_df,
            "stat_only_props": stat_only_df,
            "all_stats_props": stat_only_df,
            "strike_props": strike_df,
            "high_upside_props": strike_df,
            "predictive_lines_props": predictive_lines_df,
            "predictive_market_fill_props": predictive_fill_df if "predictive_fill_df" in locals() else pd.DataFrame(),
            "premarket_line_board": predictive_lines_df,
            "sgp_props": sgp_df,
            "sgp_board": sgp_df,
            "grading_results": graded_df,
            "grading_bucket_summary": grading_bucket_summary,
            "team_board_props": team_board_df,
            "near_miss_props": near_miss_df,
            "rejected_props": rejected_df,
            "board_diagnostics": board_diagnostics,
            "final_board_construction": final_board_construction,
            "summary": summary,
            "games": games,
            "odds": odds,
        }


    def auto_grade(self, grade_date: str, only_qualified: bool = True) -> pd.DataFrame:
        self.logger.info("auto_grade_start grade_date=%s only_qualified=%s", grade_date, only_qualified)
        prediction_history = self.get_history()
        if prediction_history.empty or "prediction_date" not in prediction_history.columns:
            self.logger.info("auto_grade_no_history grade_date=%s", grade_date)
            return pd.DataFrame()

        candidate_rows = prediction_history[
            prediction_history["prediction_date"].astype(str) == str(grade_date)
        ].copy()
        if only_qualified and "recommendation" in candidate_rows.columns:
            candidate_rows = candidate_rows[
                candidate_rows["recommendation"].astype(str).str.lower() == "qualified"
            ].copy()

        if candidate_rows.empty:
            self.logger.info("auto_grade_no_candidate_rows grade_date=%s", grade_date)
            return pd.DataFrame()

        client = self._get_client()
        raw_stats = client.get_stats(grade_date, grade_date)
        stats = self._normalize_stats(raw_stats)

        player_actuals: dict[tuple[str, str, str], float] = {}
        if not stats.empty:
            grouped = (
                stats.groupby(["player_name", "team_abbr"], as_index=False)
                .agg(
                    pts=("pts", "sum"),
                    reb=("reb", "sum"),
                    ast=("ast", "sum"),
                    fg3m=("fg3m", "sum"),
                    stl=("stl", "sum"),
                    blk=("blk", "sum"),
                )
            )
            stat_lookup = {
                "player_points": "pts",
                "player_rebounds": "reb",
                "player_assists": "ast",
                "player_3pt_made": "fg3m",
                "player_steals": "stl",
                "player_blocks": "blk",
            }
            for _, row in grouped.iterrows():
                player_name = str(row.get("player_name", "")).strip().lower()
                team_abbr = str(row.get("team_abbr", "")).strip().upper()
                for market_type, stat_col in stat_lookup.items():
                    player_actuals[(player_name, team_abbr, market_type)] = float(row.get(stat_col, 0.0) or 0.0)

        games_raw = client.get_games(grade_date)
        team_scores: dict[str, float] = {}
        moneyline_winners: dict[str, float] = {}
        if not games_raw.empty:
            for _, row in games_raw.iterrows():
                home = row.get("home_team", {}) if isinstance(row.get("home_team"), dict) else {}
                visitor = row.get("visitor_team", {}) if isinstance(row.get("visitor_team"), dict) else {}
                home_abbr = str(home.get("abbreviation") or row.get("home_team_abbr") or "").strip().upper()
                visitor_abbr = str(visitor.get("abbreviation") or row.get("visitor_team_abbr") or "").strip().upper()
                home_score = self._to_float(row.get("home_team_score"))
                visitor_score = self._to_float(row.get("visitor_team_score"))
                if home_abbr and home_score is not None:
                    team_scores[home_abbr] = home_score
                if visitor_abbr and visitor_score is not None:
                    team_scores[visitor_abbr] = visitor_score
                if home_abbr and visitor_abbr and home_score is not None and visitor_score is not None:
                    moneyline_winners[home_abbr] = 1.0 if home_score > visitor_score else 0.0
                    moneyline_winners[visitor_abbr] = 1.0 if visitor_score > home_score else 0.0

        graded_rows: list[dict[str, Any]] = []
        for _, row in candidate_rows.iterrows():
            market_type = str(row.get("market_type", ""))
            entity_name = str(row.get("entity_name", ""))
            team = str(row.get("team", "")).strip().upper()
            selection = str(row.get("selection", "")).strip()
            sportsbook_line = self._to_float(row.get("sportsbook_line"))
            actual_value: Optional[float] = None
            hit: Optional[int] = None

            if market_type.startswith("player_"):
                actual_value = player_actuals.get((entity_name.strip().lower(), team, market_type))
                if actual_value is not None and sportsbook_line is not None:
                    selection_lower = selection.lower()
                    if selection_lower == "over":
                        hit = int(actual_value > sportsbook_line)
                    elif selection_lower == "under":
                        hit = int(actual_value < sportsbook_line)
            elif market_type == "team_total":
                actual_value = team_scores.get(team)
                if actual_value is not None and sportsbook_line is not None:
                    selection_lower = selection.lower()
                    if selection_lower == "over":
                        hit = int(actual_value > sportsbook_line)
                    elif selection_lower == "under":
                        hit = int(actual_value < sportsbook_line)
            elif market_type == "moneyline":
                actual_value = moneyline_winners.get(team)
                if actual_value is not None:
                    selection_upper = selection.upper()
                    if selection_upper.endswith("ML"):
                        hit = int(actual_value == 1.0)

            graded = _to_str_dict(row)
            if market_type == "moneyline":
                graded_result = "win" if hit == 1 else "loss" if hit == 0 else "unresolved"
            elif actual_value is not None and sportsbook_line is not None:
                selection_lower = selection.lower()
                if selection_lower == "over":
                    graded_result = "win" if actual_value > sportsbook_line else "push" if actual_value == sportsbook_line else "loss"
                elif selection_lower == "under":
                    graded_result = "win" if actual_value < sportsbook_line else "push" if actual_value == sportsbook_line else "loss"
                else:
                    graded_result = "unresolved"
            else:
                graded_result = "unresolved"

            graded["actual_value"] = actual_value
            graded["hit"] = hit
            graded["graded_result"] = graded_result
            graded["result"] = graded_result
            graded["is_win"] = 1 if graded_result == "win" else 0
            graded["is_push"] = 1 if graded_result == "push" else 0
            graded["is_loss"] = 1 if graded_result == "loss" else 0
            graded["graded_at"] = pd.Timestamp.utcnow().isoformat()
            graded_rows.append(graded)

        graded_df = pd.DataFrame(graded_rows)
        graded_df = graded_df[graded_df["actual_value"].notna()].reset_index(drop=True) if not graded_df.empty else graded_df
        if not graded_df.empty:
            self._append_history(self.feedback_path, graded_df)
            self._rebuild_calibration()
            self.logger.info(
                "auto_grade_complete grade_date=%s graded_rows=%s hit_rate=%s",
                grade_date,
                len(graded_df),
                float(pd.to_numeric(graded_df["hit"], errors="coerce").mean()) if "hit" in graded_df.columns and not graded_df.empty else 0.0,
            )
            self._log_run(
                {
                    "run_type": "grade",
                    "train_start": "",
                    "train_end": "",
                    "prediction_date": grade_date,
                    "selected_count": int(len(graded_df)),
                    "rejected_count": 0,
                    "notes": json.dumps({"graded_rows": int(len(graded_df))}),
                }
            )
        else:
            self.logger.info("auto_grade_complete grade_date=%s graded_rows=0", grade_date)

        return graded_df

    def send_telegram_top_plays(self, prediction_date: str, selected_df: pd.DataFrame, summary: dict[str, Any]) -> bool:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            self.logger.info("telegram_skip_missing_config prediction_date=%s", prediction_date)
            return False

        if selected_df.empty:
            message = f"🏀 CourtVision AI {prediction_date}\nNo qualified plays today."
        else:
            top_df = selected_df.copy()
            sort_columns = [c for c in ["quality_score", "confidence", "edge_abs"] if c in top_df.columns]
            if sort_columns:
                top_df = top_df.sort_values(by=sort_columns, ascending=[False] * len(sort_columns))
            top_df = top_df.head(8).reset_index(drop=True)

            lines = [
                f"🏀 CourtVision AI {prediction_date}",
                f"Qualified Plays: {summary.get('selected_count', 0)}",
                "",
            ]
            for _, row in top_df.iterrows():
                market = str(row.get("market_type", "")).replace("_", " ").title()
                lines.append(
                    f"• {row.get('entity_name', '')} | {market} | {row.get('selection', '')} "
                    f"{row.get('sportsbook_line', '')} | proj {row.get('model_projection', '')} | "
                    f"edge {row.get('edge', '')} | conf {row.get('confidence', '')}"
                )
            message = "\n".join(lines)

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": message[:4000],
                },
                timeout=20,
            )
            response.raise_for_status()
            self.logger.info("telegram_sent prediction_date=%s", prediction_date)
            return True
        except Exception as exc:
            self.logger.exception("telegram_send_failed prediction_date=%s error=%s", prediction_date, exc)
            return False

    def log_results(self, results_df: pd.DataFrame) -> None:
        if results_df.empty:
            return

        data = results_df.copy()
        self._append_history(self.feedback_path, data)
        self._rebuild_calibration()

    def get_history(self) -> pd.DataFrame:
        return self._safe_read_csv(self.prediction_history_path)

    def get_rejection_history(self) -> pd.DataFrame:
        return self._safe_read_csv(self.rejection_history_path)

    def get_feedback_history(self) -> pd.DataFrame:
        return self._safe_read_csv(self.feedback_path)

    def get_run_log(self) -> pd.DataFrame:
        return self._safe_read_csv(self.run_log_path)

    def get_calibration_summary(self) -> pd.DataFrame:
        rules = self._load_calibration_rules()
        if not rules:
            return pd.DataFrame(
                columns=["market_type", "sample_size", "hit_rate", "mae", "confidence_multiplier"]
            )

        rows = [
            {
                "market_type": rule.market_type,
                "sample_size": rule.sample_size,
                "hit_rate": rule.hit_rate,
                "mae": rule.mae,
                "confidence_multiplier": rule.confidence_multiplier,
            }
            for rule in rules.values()
        ]
        return pd.DataFrame(rows).sort_values(
            by=["sample_size", "market_type"],
            ascending=[False, True],
        ).reset_index(drop=True)

    def _normalize_stats(self, df: pd.DataFrame) -> pd.DataFrame:
        return normalize_stats_frame(df)

    def _build_player_baselines(self, stats: pd.DataFrame) -> pd.DataFrame:
        df = stats.copy()
        df = df[df["min"].fillna(0) >= 8].copy()

        if df.empty:
            return pd.DataFrame()

        df = df.sort_values(by=["player_id", "game_date", "game_id"])
        rows: list[dict[str, Any]] = []
        stat_columns = ["pts", "reb", "ast", "stl", "blk", "fg3m"]

        for (player_id, player_name, team_abbr), grp in df.groupby(
            ["player_id", "player_name", "team_abbr"],
            sort=False,
        ):
            grp = grp.sort_values(by=["game_date", "game_id"]).reset_index(drop=True)
            recency_weights = self._recency_weights(grp["game_date"], half_life_days=18.0)
            minute_multiplier = (
                grp["min"].fillna(0.0).clip(lower=10.0, upper=40.0) / 24.0
            ).clip(lower=0.70, upper=1.20)
            weights = recency_weights * minute_multiplier

            row: dict[str, Any] = {
                "player_id": player_id,
                "player_name": player_name,
                "team_abbr": team_abbr,
                "games": int(grp["game_id"].nunique()),
                "min_avg": self._weighted_average(grp["min"], weights),
                "min_recent": self._recent_average(grp["min"], count=5),
            }

            for stat_col in stat_columns:
                row[f"{stat_col}_avg"] = self._weighted_average(grp[stat_col], weights)
                row[f"{stat_col}_recent"] = self._recent_average(grp[stat_col], count=5)
                row[f"{stat_col}_std"] = self._weighted_std(grp[stat_col], weights)

            row["player_key"] = self._player_key(player_name, team_abbr)
            rows.append(row)

        return pd.DataFrame(rows).fillna(0.0)

    def _build_team_baselines(self, stats: pd.DataFrame) -> pd.DataFrame:
        if stats.empty:
            return pd.DataFrame()

        team_game = (
            stats.groupby(["game_id", "team_abbr"], as_index=False)
            .agg(
                game_date=("game_date", "max"),
                team_pts=("pts", "sum"),
                team_reb=("reb", "sum"),
                team_ast=("ast", "sum"),
                team_stl=("stl", "sum"),
                team_blk=("blk", "sum"),
                team_fg3m=("fg3m", "sum"),
                minutes_sum=("min", "sum"),
            )
        )

        opp = team_game.rename(
            columns={
                "game_date": "opp_game_date",
                "team_abbr": "opp_abbr",
                "team_pts": "opp_pts",
                "team_reb": "opp_reb",
                "team_ast": "opp_ast",
                "team_stl": "opp_stl",
                "team_blk": "opp_blk",
                "team_fg3m": "opp_fg3m",
                "minutes_sum": "opp_minutes_sum",
            }
        )

        merged = team_game.merge(opp, on="game_id", how="inner")
        merged = merged[merged["team_abbr"] != merged["opp_abbr"]].copy()

        merged = merged.sort_values(by=["team_abbr", "game_date", "game_id"])
        rows: list[dict[str, Any]] = []
        team_columns = [
            "team_pts",
            "team_reb",
            "team_ast",
            "team_stl",
            "team_blk",
            "team_fg3m",
            "opp_pts",
            "opp_reb",
            "opp_ast",
            "opp_stl",
            "opp_blk",
            "opp_fg3m",
        ]

        for team_abbr, grp in merged.groupby("team_abbr", sort=False):
            grp = grp.sort_values(by=["game_date", "game_id"]).reset_index(drop=True)
            weights = self._recency_weights(grp["game_date"], half_life_days=20.0)
            row: dict[str, Any] = {
                "team_abbr": team_abbr,
                "games": int(grp["game_id"].nunique()),
            }

            for col in team_columns:
                avg_key = {
                    "team_pts": "team_pts_avg",
                    "team_reb": "team_reb_avg",
                    "team_ast": "team_ast_avg",
                    "team_stl": "team_stl_avg",
                    "team_blk": "team_blk_avg",
                    "team_fg3m": "team_fg3m_avg",
                    "opp_pts": "opp_pts_allowed_avg",
                    "opp_reb": "opp_reb_allowed_avg",
                    "opp_ast": "opp_ast_allowed_avg",
                    "opp_stl": "opp_stl_allowed_avg",
                    "opp_blk": "opp_blk_allowed_avg",
                    "opp_fg3m": "opp_fg3m_allowed_avg",
                }[col]
                recent_key = avg_key.replace("_avg", "_recent")
                row[avg_key] = self._weighted_average(grp[col], weights)
                row[recent_key] = self._recent_average(grp[col], count=5)

            rows.append(row)

        return pd.DataFrame(rows).fillna(0.0)

    def _normalize_games(self, df: pd.DataFrame) -> pd.DataFrame:
        return normalize_games_frame(df)

    def _normalize_odds(self, df: pd.DataFrame) -> pd.DataFrame:
        # If the input already passed through the BDL adapter (it carries
        # `unresolved_reason`), keep only valid rows and ensure downstream
        # required columns exist without re-running the legacy normalizer.
        if isinstance(df, pd.DataFrame) and "unresolved_reason" in df.columns:
            valid = filter_valid_odds(df)
            if "team" not in valid.columns:
                valid = valid.copy()
                valid["team"] = None
            if "raw_stat_key" not in valid.columns:
                valid["raw_stat_key"] = valid["market_type"]
            if "market_alias" not in valid.columns:
                valid["market_alias"] = valid["market_type"]
            if "bookmaker" not in valid.columns:
                valid["bookmaker"] = valid["vendor"]
            if "over_odds" not in valid.columns:
                valid["over_odds"] = None
            if "under_odds" not in valid.columns:
                valid["under_odds"] = None

            # The BDL adapter expands one over/under API row into side-specific
            # rows. Preserve side prices in the legacy columns too, because some
            # downstream scoring/diagnostic paths still inspect over_odds or
            # under_odds instead of the generic odds column.
            if "selection" in valid.columns and "odds" in valid.columns:
                over_mask = valid["selection"].astype(str).str.lower().eq("over")
                under_mask = valid["selection"].astype(str).str.lower().eq("under")
                valid.loc[over_mask, "over_odds"] = valid.loc[over_mask, "odds"]
                valid.loc[under_mask, "under_odds"] = valid.loc[under_mask, "odds"]
            return valid
        return normalize_odds_frame(
            df,
            map_market_type=self._map_market_type,
            market_to_stat_key=self._market_to_stat_key,
            to_float=self._to_float,
        )

    def _score_team_markets(
        self,
        game: pd.Series,
        game_odds: pd.DataFrame,
        prediction_date: str,
        home_team_projection: float,
        away_team_projection: float,
        game_total_projection: float,
        home_win_prob: float,
        away_win_prob: float,
        calibration: dict[str, CalibrationRule],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected_rows: list[dict[str, Any]] = []
        rejected_rows: list[dict[str, Any]] = []

        home = str(game["home_team_abbr"])
        away = str(game["visitor_team_abbr"])

        if game_odds.empty:
            rejected_rows.append(
                self._rejected_row(
                    market_type="team_total",
                    entity_name=f"{away} @ {home}",
                    team=home,
                    opponent=away,
                    prediction_date=prediction_date,
                    rejection_reason="missing_market_lines",
                )
            )
            rejected_rows.append(
                self._rejected_row(
                    market_type="moneyline",
                    entity_name=f"{away} @ {home}",
                    team=home,
                    opponent=away,
                    prediction_date=prediction_date,
                    rejection_reason="missing_market_lines",
                )
            )
            return selected_rows, rejected_rows

        for team_abbr, opp_abbr, projection in [
            (home, away, home_team_projection),
            (away, home, away_team_projection),
        ]:
            team_total_rows = game_odds[
                (game_odds["market_type"] == "team_total")
                & (game_odds["team"].astype(str).str.upper() == team_abbr.upper())
            ].copy()

            if team_total_rows.empty:
                rejected_rows.append(
                    self._rejected_row(
                        market_type="team_total",
                        entity_name=f"{team_abbr} Team Total",
                        team=team_abbr,
                        opponent=opp_abbr,
                        prediction_date=prediction_date,
                        rejection_reason="missing_market_lines",
                    )
                )
                continue

            for _, market in team_total_rows.iterrows():
                line = market.get("line")
                if pd.isna(line):
                    rejected_rows.append(
                        self._rejected_row(
                            market_type="team_total",
                            entity_name=f"{team_abbr} Team Total",
                            team=team_abbr,
                            opponent=opp_abbr,
                            prediction_date=prediction_date,
                            rejection_reason="missing_line_value",
                        )
                    )
                    continue

                edge = projection - float(line)
                selection = "Over" if edge >= 0 else "Under"
                confidence = self._team_confidence(
                    market_type="team_total",
                    edge_abs=abs(edge),
                    calibration=calibration,
                )

                result = self._qualify_or_reject(
                    market_type="team_total",
                    entity_name=f"{team_abbr} Team Total",
                    team=team_abbr,
                    opponent=opp_abbr,
                    sportsbook_line=float(line),
                    model_projection=projection,
                    edge=edge,
                    confidence=confidence,
                    selection=selection,
                    prediction_date=prediction_date,
                    odds=market.get("over_odds") if selection == "Over" else market.get("under_odds"),
                )
                if result["qualified"]:
                    selected_rows.append(result["row"])
                else:
                    rejected_rows.append(result["row"])

        ml_rows = game_odds[game_odds["market_type"] == "moneyline"].copy()
        if ml_rows.empty:
            rejected_rows.append(
                self._rejected_row(
                    market_type="moneyline",
                    entity_name=f"{home} Moneyline",
                    team=home,
                    opponent=away,
                    prediction_date=prediction_date,
                    rejection_reason="missing_market_lines",
                )
            )
            rejected_rows.append(
                self._rejected_row(
                    market_type="moneyline",
                    entity_name=f"{away} Moneyline",
                    team=away,
                    opponent=home,
                    prediction_date=prediction_date,
                    rejection_reason="missing_market_lines",
                )
            )
        else:
            for team_abbr, opp_abbr, win_prob in [
                (home, away, home_win_prob),
                (away, home, away_win_prob),
            ]:
                team_ml = ml_rows[
                    (
                        ml_rows["selection"].astype(str).str.upper() == team_abbr.upper()
                    ) | (
                        ml_rows["team"].astype(str).str.upper() == team_abbr.upper()
                    ) | (
                        ml_rows["side"].astype(str).str.lower()
                        == ("home" if team_abbr == home else "away")
                    )
                ].copy()

                if team_ml.empty:
                    rejected_rows.append(
                        self._rejected_row(
                            market_type="moneyline",
                            entity_name=f"{team_abbr} Moneyline",
                            team=team_abbr,
                            opponent=opp_abbr,
                            prediction_date=prediction_date,
                            rejection_reason="missing_team_moneyline",
                        )
                    )
                    continue

                for _, market in team_ml.iterrows():
                    odds_price = market.get("odds")
                    implied = self._american_odds_to_implied_prob(odds_price)
                    if implied is None:
                        rejected_rows.append(
                            self._rejected_row(
                                market_type="moneyline",
                                entity_name=f"{team_abbr} Moneyline",
                                team=team_abbr,
                                opponent=opp_abbr,
                                prediction_date=prediction_date,
                                rejection_reason="missing_odds_price",
                            )
                        )
                        continue

                    edge = win_prob - implied
                    confidence = self._team_confidence(
                        market_type="moneyline",
                        edge_abs=abs(edge),
                        calibration=calibration,
                    )
                    selection = f"{team_abbr} ML"

                    result = self._qualify_or_reject(
                        market_type="moneyline",
                        entity_name=f"{team_abbr} Moneyline",
                        team=team_abbr,
                        opponent=opp_abbr,
                        sportsbook_line=implied,
                        model_projection=win_prob,
                        edge=edge,
                        confidence=confidence,
                        selection=selection,
                        prediction_date=prediction_date,
                        odds=odds_price,
                    )
                    if result["qualified"]:
                        selected_rows.append(result["row"])
                    else:
                        rejected_rows.append(result["row"])

        return selected_rows, rejected_rows

    def _filter_player_markets(
        self,
        game_odds: pd.DataFrame,
        player_name: str,
        team_abbr: str,
        player_id: Any = None,
    ) -> pd.DataFrame:
        return runtime_filter_player_markets(
            game_odds=game_odds,
            player_name=player_name,
            team_abbr=team_abbr,
            player_id=player_id,
        )

    def _player_selection_injury_profile(
        self,
        player_row: Mapping[str, Any],
        team_abbr: str,
        opp_abbr: str,
        injury_context: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        profile = {
            "inactive": False,
            "own_status": "",
            "own_description": "",
            "team_impact": 0.0,
            "opp_impact": 0.0,
            "min_minutes_threshold": 18.0,
            "threshold_overrides": {
                "edge_multiplier": 1.0,
                "edge_delta": 0.0,
                "confidence_delta": 0.0,
            },
            "notes": "",
        }
        if not injury_context or not isinstance(injury_context, Mapping):
            return profile

        players_ctx = injury_context.get("players", {}) if isinstance(injury_context.get("players", {}), Mapping) else {}
        teams_ctx = injury_context.get("teams", {}) if isinstance(injury_context.get("teams", {}), Mapping) else {}
        player_key = self._player_key(player_row.get("player_name"), team_abbr)
        own_ctx = players_ctx.get(player_key, {}) if isinstance(players_ctx, Mapping) else {}
        team_ctx = teams_ctx.get(str(team_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}
        opp_ctx = teams_ctx.get(str(opp_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}

        own_status = str(own_ctx.get("status") or "").strip()
        own_status_key = own_status.lower()
        own_description = str(own_ctx.get("description") or "").strip()
        team_impact = float(team_ctx.get("impact_score") or 0.0)
        opp_impact = float(opp_ctx.get("impact_score") or 0.0)
        usage_boost = float(team_ctx.get("usage_boost") or 0.0)
        rebound_boost = float(team_ctx.get("rebound_boost") or 0.0)
        affected_players = int(team_ctx.get("affected_players") or 0)
        opp_affected_players = int(opp_ctx.get("affected_players") or 0)
        minutes_avg = self._to_float(player_row.get("min_avg")) or 0.0
        minutes_recent = self._to_float(player_row.get("min_recent")) or minutes_avg
        role_factor = min(max((max(minutes_avg, minutes_recent) - 16.0) / 18.0, 0.0), 1.0)

        inactive_statuses = {"out", "out for season", "doubtful"}
        caution_statuses = {"questionable", "day-to-day", "day to day", "probable"}

        notes: list[str] = []
        if own_status:
            notes.append(f"own_status:{own_status}")

        if own_status_key in inactive_statuses:
            profile["inactive"] = True
        else:
            threshold_overrides = dict(profile["threshold_overrides"])
            if own_status_key in caution_statuses:
                threshold_overrides["edge_multiplier"] *= 1.08
                threshold_overrides["confidence_delta"] += 0.04
                notes.append("own_status_caution")
            else:
                if team_impact > 0:
                    minutes_relief = min(5.0, team_impact * 4.5 + usage_boost * 9.0 + rebound_boost * 4.0 + max(0, affected_players - 1) * 0.4)
                    dynamic_threshold = max(12.0, 18.0 - minutes_relief)
                    if role_factor >= 0.45:
                        dynamic_threshold = max(11.5, dynamic_threshold - 0.5)
                    profile["min_minutes_threshold"] = dynamic_threshold

                    threshold_relief = min(0.22, team_impact * 0.10 + usage_boost * 0.55 + max(0, affected_players - 1) * 0.02 + role_factor * 0.03)
                    threshold_overrides["edge_multiplier"] *= max(0.72, 1.0 - threshold_relief)
                    threshold_overrides["confidence_delta"] -= min(0.055, team_impact * 0.025 + usage_boost * 0.12 + max(0, affected_players - 1) * 0.006 + role_factor * 0.01)
                    notes.append(f"team_relief:{team_impact:.2f}")

                if opp_impact > 0:
                    opp_relief = min(0.08, opp_impact * 0.035 + max(0, opp_affected_players - 1) * 0.01)
                    threshold_overrides["edge_multiplier"] *= max(0.82, 1.0 - opp_relief)
                    threshold_overrides["confidence_delta"] -= min(0.025, opp_impact * 0.015 + max(0, opp_affected_players - 1) * 0.004)
                    notes.append(f"opp_relief:{opp_impact:.2f}")

            profile["threshold_overrides"] = threshold_overrides

        profile["own_status"] = own_status
        profile["own_description"] = own_description
        profile["team_impact"] = round(float(team_impact), 4)
        profile["opp_impact"] = round(float(opp_impact), 4)
        profile["notes"] = "; ".join(notes)
        return profile

    def _synthetic_player_market_line(
        self,
        player_row: Mapping[str, Any],
        market_type: str,
    ) -> Optional[float]:
        stat_col = self.PLAYER_MARKETS.get(market_type)
        if not stat_col:
            return None

        season_avg = self._to_float(player_row.get(f"{stat_col}_avg"))
        recent_avg = self._to_float(player_row.get(f"{stat_col}_recent"))
        if season_avg is None and recent_avg is None:
            return None

        anchor = self._blend_average_and_recent(season_avg, recent_avg, recent_weight=0.45)
        return self._round_to_market_step(anchor, market_type)

    def _score_player_markets(
        self,
        player_row: Mapping[str, Any],
        player_markets: pd.DataFrame,
        live_supported_markets: Sequence[str],
        team_abbr: str,
        opp_abbr: str,
        prediction_date: str,
        calibration: dict[str, CalibrationRule],
        opponent_row: Optional[Mapping[str, Any]],
        league_context: Mapping[str, float],
        injury_context: Optional[Mapping[str, Any]] = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected_rows: list[dict[str, Any]] = []
        rejected_rows: list[dict[str, Any]] = []

        for _, market_row in player_markets.iterrows():
            market_type = str(market_row.get('market_type', '')).strip()
            if market_type not in live_supported_markets:
                continue

            line_value = self._to_float(market_row.get('line'))
            if line_value is None:
                continue

            over_odds = self._to_float(market_row.get('over_odds'))
            under_odds = self._to_float(market_row.get('under_odds'))
            default_odds = self._to_float(market_row.get('odds'))

            projection = self._project_player_market(
                player_row=player_row,
                market_type=market_type,
                opponent_row=opponent_row,
                league_context=league_context,
            )

            # Apply injury context if available
            projection, confidence_adjustment, injury_metadata = self._apply_player_injury_context(
                player_row=player_row,
                team_abbr=team_abbr,
                opp_abbr=opp_abbr,
                market_type=market_type,
                projection=projection,
                confidence=0.5,  # placeholder
                injury_context=injury_context,
            )
            raw_prop_type = str(market_row.get("raw_prop_type", "") or "")
            raw_market_type = str(market_row.get("raw_market_type", market_row.get("market.type", "")) or "")
            if raw_market_type.strip().lower() == "milestone" or str(market_row.get("selection", "") or "").strip().lower() == "milestone":
                continue

            sportsbook_line = float(line_value)
            selection = "over" if projection > sportsbook_line else "under"
            odds = default_odds if default_odds is not None else (over_odds if selection == "over" else under_odds)
            if odds is None:
                continue

            edge = projection - sportsbook_line

            stat_std = self._to_float(player_row.get(f"{self.PLAYER_MARKETS.get(market_type, '')}_std", 0.0)) or 0.0
            minutes_avg = self._to_float(player_row.get("min_avg", 0.0)) or 0.0
            confidence = self._player_confidence(
                market_type=market_type,
                edge_abs=abs(edge),
                stat_std=stat_std,
                minutes_avg=minutes_avg,
                calibration=calibration,
            )
            confidence *= confidence_adjustment

            selection = "over" if projection > sportsbook_line else "under"

            result = self._qualify_or_reject(
                market_type=market_type,
                entity_name=str(player_row.get('player_name', '')),
                team=team_abbr,
                opponent=opp_abbr,
                sportsbook_line=sportsbook_line,
                model_projection=projection,
                edge=edge,
                confidence=confidence,
                selection=selection,
                prediction_date=prediction_date,
                odds=odds,
                extra_fields={
                    **injury_metadata,
                    "raw_prop_type": raw_prop_type,
                    "raw_market_type": raw_market_type,
                },
            )

            if result["qualified"]:
                selected_rows.append(result["row"])
            else:
                rejected_rows.append(result["row"])

        return selected_rows, rejected_rows

    def _qualify_or_reject(
        self,
        market_type: str,
        entity_name: str,
        team: str,
        opponent: str,
        sportsbook_line: float,
        model_projection: float,
        edge: float,
        confidence: float,
        selection: str,
        prediction_date: str,
        odds: Any,
        extra_fields: Optional[dict[str, Any]] = None,
        threshold_overrides: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        thresholds = self.DEFAULT_THRESHOLDS[market_type]
        edge_threshold = float(thresholds["edge"])
        confidence_threshold = float(thresholds["confidence"])

        if threshold_overrides:
            edge_threshold *= float(threshold_overrides.get("edge_multiplier", 1.0) or 1.0)
            edge_threshold += float(threshold_overrides.get("edge_delta", 0.0) or 0.0)
            confidence_threshold += float(threshold_overrides.get("confidence_delta", 0.0) or 0.0)

        odds_value = self._to_float(odds)
        if market_type.startswith("player_") and sportsbook_line <= 1.5:
            edge_threshold *= 1.15
            confidence_threshold += 0.02
        elif market_type.startswith("player_") and sportsbook_line <= 5.5:
            edge_threshold *= 1.08
            confidence_threshold += 0.01

        if market_type in {"player_steals", "player_blocks"}:
            edge_threshold = max(edge_threshold, 0.65)
            confidence_threshold = max(confidence_threshold, 0.70)

        if market_type == "moneyline" and odds_value is not None and odds_value > 0:
            if odds_value >= 400:
                edge_threshold += 0.12
                confidence_threshold += 0.10
            elif odds_value >= 250:
                edge_threshold += 0.08
                confidence_threshold += 0.06
            elif odds_value >= 150:
                edge_threshold += 0.04
                confidence_threshold += 0.03

        row = {
            "prediction_date": prediction_date,
            "market_type": market_type,
            "entity_name": entity_name,
            "team": team,
            "opponent": opponent,
            "selection": selection,
            "sportsbook_line": round(float(sportsbook_line), 4),
            "model_projection": round(float(model_projection), 4),
            "edge": round(float(edge), 4),
            "edge_abs": round(float(abs(edge)), 4),
            "confidence": round(float(confidence), 4),
            "odds": odds_value,
            "market_label": self._market_label(market_type),
            "bet_label": self._build_bet_label(market_type, entity_name, selection, float(sportsbook_line)),
        }

        if extra_fields:
            row.update(extra_fields)

        row = self._apply_scoring_metadata(row)
        market_trust_weight = self.board_audit.market_trust_weight(market_type)

        gate_result = self.qualification_gate.evaluate(
            edge_abs=abs(edge),
            confidence=confidence,
            edge_threshold=edge_threshold,
            confidence_threshold=confidence_threshold,
        )
        edge_threshold = float(gate_result["edge_threshold"])
        confidence_threshold = float(gate_result["confidence_threshold"])
        edge_abs = abs(edge)
        qualified = bool(gate_result["qualified"])
        qualification_gate_mode = str(gate_result["qualification_mode"])
        if market_type == "moneyline" and edge <= 0:
            qualified = False
            qualification_gate_mode = "negative_edge_blocked"
        if not qualified and self.board_volume.is_live_quality_rescue_candidate(
            row,
            edge_threshold=edge_threshold,
            confidence_threshold=confidence_threshold,
            market_trust_weight=market_trust_weight,
        ):
            qualified = True
            qualification_gate_mode = "live_quality_rescue"

        row.update(
            {
                "edge_threshold_used": round(float(edge_threshold), 4),
                "confidence_threshold_used": round(float(confidence_threshold), 4),
                "qualification_gate_mode": qualification_gate_mode,
                "edge_threshold_passed": bool(gate_result["edge_pass"]),
                "confidence_threshold_passed": bool(gate_result["confidence_pass"]),
                "strong_edge_override_passed": qualification_gate_mode == "strong_edge_override",
                "strong_confidence_override_passed": qualification_gate_mode == "strong_confidence_override",
                "live_quality_rescue_passed": qualification_gate_mode == "live_quality_rescue",
            }
        )

        quality_gate_failed = False
        if market_type == "moneyline":
            moneyline_quality_floor = 64.0
            if odds_value is not None and odds_value >= 250:
                moneyline_quality_floor = 68.0
            elif odds_value is not None and odds_value >= 150:
                moneyline_quality_floor = 66.0
            if (self._to_float(row.get("quality_score")) or 0.0) < moneyline_quality_floor:
                qualified = False
                quality_gate_failed = True
                qualification_gate_mode = "quality_gate_blocked"

        row["qualification_gate_mode"] = qualification_gate_mode

        if qualified:
            row["recommendation"] = "qualified"
            row["rejection_reason"] = ""
        else:
            row["recommendation"] = "rejected"
            if quality_gate_failed:
                row["rejection_reason"] = "quality_below_threshold"
            elif edge_abs < edge_threshold and confidence < confidence_threshold:
                row["rejection_reason"] = "edge_and_confidence_below_threshold"
            elif edge_abs < edge_threshold:
                row["rejection_reason"] = "edge_below_threshold"
            else:
                row["rejection_reason"] = "confidence_below_threshold"

        row = self._apply_board_audit_row(row)
        return {"qualified": qualified, "row": row}

    def _project_team_totals(
        self,
        home_team: Mapping[str, Any],
        away_team: Mapping[str, Any],
        home_abbr: str = "",
        away_abbr: str = "",
        injury_context: Optional[Mapping[str, Any]] = None,
    ) -> tuple[float, float, float]:
        home_off = self._blend_average_and_recent(
            home_team.get("team_pts_avg"),
            home_team.get("team_pts_recent"),
        )
        home_def = self._blend_average_and_recent(
            home_team.get("opp_pts_allowed_avg"),
            home_team.get("opp_pts_allowed_recent"),
        )
        away_off = self._blend_average_and_recent(
            away_team.get("team_pts_avg"),
            away_team.get("team_pts_recent"),
        )
        away_def = self._blend_average_and_recent(
            away_team.get("opp_pts_allowed_avg"),
            away_team.get("opp_pts_allowed_recent"),
        )

        home_proj = (home_off * 0.58) + (away_def * 0.42) + 1.5
        away_proj = (away_off * 0.58) + (home_def * 0.42)

        home_proj, away_proj = self._apply_team_injury_context(
            home_abbr=home_abbr or str(home_team.get('team_abbr') or ''),
            away_abbr=away_abbr or str(away_team.get('team_abbr') or ''),
            home_proj=home_proj,
            away_proj=away_proj,
            injury_context=injury_context,
        )
        total_proj = home_proj + away_proj
        return total_proj, home_proj, away_proj

    def _project_player_market(
        self,
        player_row: Mapping[str, Any],
        market_type: str,
        opponent_row: Optional[Mapping[str, Any]],
        league_context: Mapping[str, float],
    ) -> float:
        stat_col = self.PLAYER_MARKETS[market_type]
        base_avg = self._to_float(player_row.get(f"{stat_col}_avg")) or 0.0
        recent_avg = self._to_float(player_row.get(f"{stat_col}_recent")) or 0.0
        min_avg = self._to_float(player_row.get("min_avg")) or 0.0
        min_recent_val = self._to_float(player_row.get("min_recent"))

        projection = self._blend_average_and_recent(base_avg, recent_avg, recent_weight=0.35)

        if min_avg > 0 and min_recent_val is not None:
            minute_factor = min(max(min_recent_val / min_avg, 0.90), 1.12)
            projection *= minute_factor

        allowance_key = self.OPPONENT_ALLOWANCE_MAP.get(market_type)
        if opponent_row and allowance_key:
            opp_allowance = self._blend_average_and_recent(
                self._to_float(opponent_row.get(allowance_key)) or 0.0,
                self._to_float(opponent_row.get(allowance_key.replace("_avg", "_recent"))) or 0.0,
                recent_weight=0.35,
            )
            league_allowance = self._to_float(league_context.get(allowance_key)) or 0.0
            if opp_allowance > 0 and league_allowance > 0:
                matchup_factor = 1.0 + ((opp_allowance / league_allowance) - 1.0) * 0.35
                projection *= min(max(matchup_factor, 0.90), 1.10)

        return max(projection, 0.0)

    def _project_home_win_probability(self, home_points: float, away_points: float) -> float:
        margin = home_points - away_points
        win_prob = 1.0 / (1.0 + math.exp(-margin / 6.0))
        return min(max(win_prob, 0.05), 0.95)

    def _player_confidence(
        self,
        market_type: str,
        edge_abs: float,
        stat_std: float,
        minutes_avg: float,
        calibration: dict[str, CalibrationRule],
    ) -> float:
        thresholds = self.DEFAULT_THRESHOLDS[market_type]
        base = 0.50
        edge_component = min(0.25, edge_abs / max(thresholds["edge"] * 3.0, 0.75) * 0.25)
        volatility_penalty = min(0.20, (stat_std / max(minutes_avg / 2.5, 1.0)) * 0.08)
        minutes_bonus = min(0.10, max(0.0, (minutes_avg - 20.0) / 20.0) * 0.10)

        score = base + edge_component + minutes_bonus - volatility_penalty
        score *= calibration.get(market_type, CalibrationRule(market_type, 0, 0.0, 0.0, 1.0)).confidence_multiplier
        return min(max(score, 0.35), 0.95)

    def _team_confidence(
        self,
        market_type: str,
        edge_abs: float,
        calibration: dict[str, CalibrationRule],
    ) -> float:
        thresholds = self.DEFAULT_THRESHOLDS[market_type]
        base = 0.50
        edge_component = min(0.30, edge_abs / max(thresholds["edge"] * 3.0, 0.09) * 0.30)
        score = base + edge_component
        score *= calibration.get(market_type, CalibrationRule(market_type, 0, 0.0, 0.0, 1.0)).confidence_multiplier
        return min(max(score, 0.35), 0.95)

    def _normalize_injuries(self, injuries_raw: pd.DataFrame) -> pd.DataFrame:
        return normalize_injuries_frame(injuries_raw)

    def _series_nonempty_count(self, df: pd.DataFrame, columns: Sequence[str]) -> int:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return 0
        mask = pd.Series(False, index=df.index)
        for col in columns:
            if col not in df.columns:
                continue
            values = df[col]
            if col.endswith("_id") or col.endswith(".id") or col in {"player_id", "team_id"}:
                mask = mask | pd.to_numeric(values, errors="coerce").notna()
            else:
                mask = mask | values.fillna("").astype(str).str.strip().ne("")
        return int(mask.sum())

    def _id_set(self, df: pd.DataFrame, columns: Sequence[str]) -> set[int]:
        ids: set[int] = set()
        if not isinstance(df, pd.DataFrame) or df.empty:
            return ids
        for col in columns:
            if col not in df.columns:
                continue
            numeric = pd.to_numeric(df[col], errors="coerce").dropna()
            ids.update(int(value) for value in numeric.tolist() if int(value) > 0)
        return ids

    def _text_set(self, df: pd.DataFrame, columns: Sequence[str]) -> set[str]:
        values: set[str] = set()
        if not isinstance(df, pd.DataFrame) or df.empty:
            return values
        for col in columns:
            if col not in df.columns:
                continue
            values.update(
                str(value).strip().upper()
                for value in df[col].dropna().tolist()
                if str(value).strip()
            )
        return values

    def _nonzero_count(self, df: pd.DataFrame, column: str) -> int:
        if not isinstance(df, pd.DataFrame) or df.empty or column not in df.columns:
            return 0
        values = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
        return int(values.ne(0.0).sum())

    def _injury_identity_coverage(self, df: pd.DataFrame) -> int:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return 0
        identity_columns = [
            "player_id",
            "player.id",
            "player.first_name",
            "player.last_name",
            "player_name",
            "team_id",
            "team.id",
            "player.team_id",
            "team_abbr",
            "team.abbreviation",
        ]
        mask = pd.Series(False, index=df.index)
        for col in identity_columns:
            if col not in df.columns:
                continue
            values = df[col]
            if col in {"player_id", "player.id", "team_id", "team.id", "player.team_id"}:
                mask = mask | pd.to_numeric(values, errors="coerce").notna()
            else:
                mask = mask | values.fillna("").astype(str).str.strip().ne("")
        return int(mask.sum())

    def _build_injury_context_diagnostics(
        self,
        *,
        prediction_date: str,
        injury_source: str,
        injuries_raw: pd.DataFrame,
        injuries: pd.DataFrame,
        games: pd.DataFrame,
        player_baselines: pd.DataFrame,
        candidate_df: pd.DataFrame,
        injury_context: Mapping[str, Any] | None,
        fetch_diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        raw_df = injuries_raw if isinstance(injuries_raw, pd.DataFrame) else pd.DataFrame()
        norm_df = injuries if isinstance(injuries, pd.DataFrame) else pd.DataFrame()
        games_df = games if isinstance(games, pd.DataFrame) else pd.DataFrame()
        baselines_df = player_baselines if isinstance(player_baselines, pd.DataFrame) else pd.DataFrame()
        candidates = candidate_df if isinstance(candidate_df, pd.DataFrame) else pd.DataFrame()

        active_team_ids = self._id_set(games_df, ["home_team_id", "visitor_team_id"])
        active_team_abbrs = self._text_set(games_df, ["home_team_abbr", "visitor_team_abbr"])
        injury_player_ids = self._id_set(norm_df, ["player_id", "player.id"])
        injury_team_ids = self._id_set(norm_df, ["team_id", "team.id", "player.team_id"])
        injury_team_abbrs = self._text_set(norm_df, ["team_abbr", "team.abbreviation"])
        baseline_player_ids = self._id_set(baselines_df, ["player_id"])
        candidate_player_ids = self._id_set(candidates, ["player_id"])

        active_team_id_matches = injury_team_ids & active_team_ids
        active_team_abbr_matches = injury_team_abbrs & active_team_abbrs
        baseline_player_matches = injury_player_ids & baseline_player_ids
        candidate_player_matches = injury_player_ids & candidate_player_ids

        raw_date_columns = [c for c in raw_df.columns if "date" in str(c).lower()] if not raw_df.empty else []
        normalized_date_columns = [c for c in norm_df.columns if "date" in str(c).lower()] if not norm_df.empty else []
        date_values: set[str] = set()
        for frame, cols in ((raw_df, raw_date_columns), (norm_df, normalized_date_columns)):
            for col in cols:
                date_values.update(str(value)[:10] for value in frame[col].dropna().tolist() if str(value).strip())
        has_date_columns = bool(raw_date_columns or normalized_date_columns)
        date_matches = not has_date_columns or prediction_date in date_values

        rows_with_player_id = self._series_nonempty_count(norm_df, ["player_id", "player.id"])
        rows_with_team_id = self._series_nonempty_count(norm_df, ["team_id", "team.id", "player.team_id"])
        rows_with_team_abbr = self._series_nonempty_count(norm_df, ["team_abbr", "team.abbreviation"])

        nonzero_injury_impact = self._nonzero_count(candidates, "injury_impact_score")
        nonzero_team_impact = self._nonzero_count(candidates, "team_injury_impact")
        nonzero_opponent_impact = self._nonzero_count(candidates, "opponent_injury_impact")
        if candidates.empty:
            nonzero_candidate_impacts = 0
        else:
            impact_mask = (
                pd.to_numeric(candidates.get("injury_impact_score", pd.Series(0.0, index=candidates.index)), errors="coerce").fillna(0.0).ne(0.0)
                | pd.to_numeric(candidates.get("team_injury_impact", pd.Series(0.0, index=candidates.index)), errors="coerce").fillna(0.0).ne(0.0)
                | pd.to_numeric(candidates.get("opponent_injury_impact", pd.Series(0.0, index=candidates.index)), errors="coerce").fillna(0.0).ne(0.0)
            )
            nonzero_candidate_impacts = int(impact_mask.sum())

        mismatch_reasons: list[str] = []
        if len(raw_df) > 0 and len(norm_df) == 0:
            mismatch_reasons.append("unsupported injury schema")
        if len(norm_df) > 0 and rows_with_player_id == 0:
            mismatch_reasons.append("player_id mismatch")
        if len(norm_df) > 0 and rows_with_team_id == 0:
            mismatch_reasons.append("team_id mismatch")
        if len(norm_df) > 0 and rows_with_team_abbr == 0:
            mismatch_reasons.append("team_abbr missing")
        if has_date_columns and not date_matches:
            mismatch_reasons.append("date mismatch")
        if len(norm_df) > 0 and not active_team_id_matches and not active_team_abbr_matches:
            if rows_with_team_abbr == 0:
                mismatch_reasons.append("team_abbr missing")
            elif rows_with_team_id > 0:
                mismatch_reasons.append("team_id mismatch")
        if len(norm_df) > 0 and rows_with_player_id > 0 and not baseline_player_matches:
            mismatch_reasons.append("player_id mismatch")

        deduped_reasons: list[str] = []
        for reason in mismatch_reasons:
            if reason not in deduped_reasons:
                deduped_reasons.append(reason)

        context_team_count = 0
        context_player_count = 0
        if isinstance(injury_context, Mapping):
            context_team_count = len(injury_context.get("teams", {}) or {})
            context_player_count = len(injury_context.get("players", {}) or {})

        payload = {
            "prediction_date": prediction_date,
            "injury_source": injury_source,
            "raw_rows": int(len(raw_df)),
            "normalized_rows": int(len(norm_df)),
            "raw_columns": [str(c) for c in raw_df.columns.tolist()],
            "normalized_columns": [str(c) for c in norm_df.columns.tolist()],
            "rows_with_player_id": rows_with_player_id,
            "rows_with_team_id": rows_with_team_id,
            "rows_with_team_abbr": rows_with_team_abbr,
            "rows_with_team_id_or_abbr": int(max(rows_with_team_id, rows_with_team_abbr)),
            "active_slate_team_ids": sorted(active_team_ids),
            "active_slate_team_abbrs": sorted(active_team_abbrs),
            "injury_team_ids": sorted(injury_team_ids),
            "injury_team_abbrs": sorted(injury_team_abbrs),
            "active_team_matches": int(len(active_team_id_matches) + len(active_team_abbr_matches)),
            "active_team_id_matches": sorted(active_team_id_matches),
            "active_team_abbr_matches": sorted(active_team_abbr_matches),
            "baseline_player_id_count": int(len(baseline_player_ids)),
            "injury_player_id_count": int(len(injury_player_ids)),
            "baseline_player_matches": int(len(baseline_player_matches)),
            "candidate_player_id_count": int(len(candidate_player_ids)),
            "candidate_player_matches": int(len(candidate_player_matches)),
            "injury_context_team_count": int(context_team_count),
            "injury_context_player_count": int(context_player_count),
            "candidate_rows": int(len(candidates)),
            "candidate_rows_with_nonzero_injury_impact_score": nonzero_injury_impact,
            "candidate_rows_with_nonzero_team_injury_impact": nonzero_team_impact,
            "candidate_rows_with_nonzero_opponent_injury_impact": nonzero_opponent_impact,
            "nonzero_candidate_impacts": nonzero_candidate_impacts,
            "has_date_columns": bool(has_date_columns),
            "date_columns": sorted(set(raw_date_columns + normalized_date_columns)),
            "date_values_sample": sorted(date_values)[:10],
            "date_matches_prediction_date": bool(date_matches),
            "mismatch_reasons": deduped_reasons,
            "raw_sample": raw_df.head(3).to_dict("records") if not raw_df.empty else [],
            "normalized_sample": norm_df.head(3).to_dict("records") if not norm_df.empty else [],
        }
        if fetch_diagnostics:
            payload["fetch_diagnostics"] = dict(fetch_diagnostics)
            if (
                int(fetch_diagnostics.get("sdk_raw_rows", 0) or 0) > 0
                and int(fetch_diagnostics.get("sdk_normalized_rows", 0) or 0) == 0
                and int(fetch_diagnostics.get("http_normalized_rows", 0) or 0) == 0
            ):
                payload["recommended_provider_config_action"] = (
                    "SDK injury rows were unusable and HTTP fallback did not produce normalized rows. "
                    "Inspect BallDontLie injury endpoint schema/API plan and prefer direct HTTP fields "
                    "with player/team identity before enabling injury context."
                )
        return payload

    def _write_injury_context_diagnostics(self, payload: Mapping[str, Any]) -> tuple[Path, Path]:
        prediction_date = str(payload.get("prediction_date") or "")
        diagnostics_dir = self.out_dir / "runtime" / "diagnostics"
        operator_dir = self.out_dir / "runtime" / "operator"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        operator_dir.mkdir(parents=True, exist_ok=True)

        json_path = diagnostics_dir / f"injury_context_diagnostics_{prediction_date}.json"
        txt_path = operator_dir / f"injury_context_report_{prediction_date}.txt"
        json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        lines = [
            f"CourtVision Injury Context Diagnostics - {prediction_date}",
            "",
            "Fetch Fallback",
            f"- SDK raw rows: {(payload.get('fetch_diagnostics') or {}).get('sdk_raw_rows', payload.get('raw_rows', 0))}",
            f"- SDK normalized rows: {(payload.get('fetch_diagnostics') or {}).get('sdk_normalized_rows', payload.get('normalized_rows', 0))}",
            f"- SDK identity coverage: {(payload.get('fetch_diagnostics') or {}).get('sdk_identity_coverage', 0)}",
            f"- SDK unusable reason: {(payload.get('fetch_diagnostics') or {}).get('sdk_unusable_reason') or 'none'}",
            f"- HTTP fallback attempted: {(payload.get('fetch_diagnostics') or {}).get('http_fallback_attempted', False)}",
            f"- HTTP raw rows: {(payload.get('fetch_diagnostics') or {}).get('http_raw_rows', 0)}",
            f"- HTTP normalized rows: {(payload.get('fetch_diagnostics') or {}).get('http_normalized_rows', 0)}",
            "",
            "Counts",
            f"- raw injury rows: {payload.get('raw_rows', 0)}",
            f"- normalized injury rows: {payload.get('normalized_rows', 0)}",
            f"- rows with player_id: {payload.get('rows_with_player_id', 0)}",
            f"- rows with team_id: {payload.get('rows_with_team_id', 0)}",
            f"- rows with team_abbr: {payload.get('rows_with_team_abbr', 0)}",
            f"- active slate team matches: {payload.get('active_team_matches', 0)}",
            f"- baseline player ID matches: {payload.get('baseline_player_matches', 0)}",
            f"- candidate player ID matches: {payload.get('candidate_player_matches', 0)}",
            f"- candidate non-zero injury impacts: {payload.get('nonzero_candidate_impacts', 0)}",
            "",
            "Candidate Impact Columns",
            f"- non-zero injury_impact_score: {payload.get('candidate_rows_with_nonzero_injury_impact_score', 0)}",
            f"- non-zero team_injury_impact: {payload.get('candidate_rows_with_nonzero_team_injury_impact', 0)}",
            f"- non-zero opponent_injury_impact: {payload.get('candidate_rows_with_nonzero_opponent_injury_impact', 0)}",
            "",
            "Mismatch Reasons",
        ]
        reasons = payload.get("mismatch_reasons") or []
        lines.extend(f"- {reason}" for reason in reasons) if reasons else lines.append("- none detected")
        lines.extend(
            [
                "",
                f"Active teams: {payload.get('active_slate_team_abbrs', [])}",
                f"Injury team abbreviations: {payload.get('injury_team_abbrs', [])}",
                f"Date columns present: {payload.get('date_columns', [])}",
                f"Date values sample: {payload.get('date_values_sample', [])}",
                f"SDK raw keys: {(payload.get('fetch_diagnostics') or {}).get('sdk_raw_columns', payload.get('raw_columns', []))}",
                f"HTTP raw keys: {(payload.get('fetch_diagnostics') or {}).get('http_raw_columns', [])}",
                f"Recommended provider/config action: {payload.get('recommended_provider_config_action', 'none')}",
                "",
                f"JSON: {json_path}",
            ]
        )
        txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return json_path, txt_path

    def _emit_injury_context_diagnostics(self, payload: Mapping[str, Any]) -> None:
        print(f"[INJURY] raw_rows={int(payload.get('raw_rows', 0) or 0)}", flush=True)
        print(f"[INJURY] normalized_rows={int(payload.get('normalized_rows', 0) or 0)}", flush=True)
        print(f"[INJURY] active_team_matches={int(payload.get('active_team_matches', 0) or 0)}", flush=True)
        print(f"[INJURY] baseline_player_matches={int(payload.get('baseline_player_matches', 0) or 0)}", flush=True)
        print(f"[INJURY] candidate_player_matches={int(payload.get('candidate_player_matches', 0) or 0)}", flush=True)
        print(f"[INJURY] nonzero_candidate_impacts={int(payload.get('nonzero_candidate_impacts', 0) or 0)}", flush=True)

    def _attach_manual_player_context(
        self,
        *,
        prediction_date: str,
        qualified_pool_df: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], Path]:
        manual_context, load_diagnostics = load_manual_player_context(prediction_date)
        qualified_pool_df, candidate_diagnostics = apply_manual_player_context(qualified_pool_df, manual_context)
        elite_df, _ = apply_manual_player_context(elite_df, manual_context)
        full_market_df, _ = apply_manual_player_context(full_market_df, manual_context)
        diagnostics_path, diagnostics_payload = write_manual_context_diagnostics(
            prediction_date=prediction_date,
            runtime_root=self.runtime_dir,
            load_diagnostics=load_diagnostics,
            candidate_diagnostics=candidate_diagnostics,
            context_rows=manual_context,
        )
        print(
            f"[CONTEXT] manual_context_file_found={str(bool(diagnostics_payload.get('file_found'))).lower()}",
            flush=True,
        )
        print(f"[CONTEXT] manual_context_rows={int(diagnostics_payload.get('rows', 0) or 0)}", flush=True)
        print(
            f"[CONTEXT] manual_context_candidate_matches={int(diagnostics_payload.get('candidate_matches', 0) or 0)}",
            flush=True,
        )
        return qualified_pool_df, elite_df, full_market_df, diagnostics_payload, diagnostics_path

    def _injury_status_weight(self, status: Any) -> float:
        status_key = str(status or "").strip().lower()
        return float(self.INJURY_STATUS_WEIGHTS.get(status_key, 0.25 if status_key else 0.0))

    def _build_injury_context(
        self,
        injuries: pd.DataFrame,
        player_baselines: pd.DataFrame,
        active_teams: set[str],
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"players": {}, "teams": {}, "active_injuries": pd.DataFrame()}
        if injuries.empty or player_baselines.empty:
            return context

        player_cols = [c for c in ["player_name", "team_abbr", "player_id", "team_id", "pts_avg", "reb_avg", "ast_avg", "stl_avg", "blk_avg", "min_avg"] if c in player_baselines.columns]
        baseline_lookup = player_baselines[player_cols].copy()
        baseline_lookup["player_name_key"] = baseline_lookup.get("player_name", pd.Series("", index=baseline_lookup.index)).fillna("").astype(str).str.strip().str.lower()

        injuries = injuries.copy()
        injuries["player_name_key"] = injuries.get("player_name", pd.Series("", index=injuries.index)).fillna("").astype(str).str.strip().str.lower()

        enriched = injuries.merge(
            baseline_lookup,
            how="left",
            on="player_name_key",
            suffixes=("", "_baseline"),
        )
        if "team_abbr" not in enriched.columns:
            enriched["team_abbr"] = enriched.get("team_abbr_baseline", pd.Series("", index=enriched.index))
        else:
            missing_mask = enriched["team_abbr"].fillna("").astype(str).str.len() == 0
            enriched.loc[missing_mask, "team_abbr"] = enriched.loc[missing_mask, "team_abbr_baseline"]

        enriched["team_abbr"] = enriched.get("team_abbr", pd.Series("", index=enriched.index)).fillna("").astype(str).str.upper()
        if active_teams:
            enriched = enriched[enriched["team_abbr"].isin({str(team).upper() for team in active_teams})].copy()
        if enriched.empty:
            return context

        enriched["injury_weight"] = enriched["status"].map(self._injury_status_weight).fillna(0.0)
        for col in ["pts_avg", "reb_avg", "ast_avg", "stl_avg", "blk_avg", "min_avg"]:
            if col in enriched.columns:
                enriched[col] = pd.to_numeric(enriched[col], errors="coerce").fillna(0.0)
            else:
                enriched[col] = 0.0

        enriched["weighted_pts"] = enriched["pts_avg"] * enriched["injury_weight"]
        enriched["weighted_reb"] = enriched["reb_avg"] * enriched["injury_weight"]
        enriched["weighted_ast"] = enriched["ast_avg"] * enriched["injury_weight"]
        enriched["weighted_stocks"] = (enriched["stl_avg"] + enriched["blk_avg"]) * enriched["injury_weight"]
        enriched["weighted_minutes"] = enriched["min_avg"] * enriched["injury_weight"]

        active_details = []
        for _, row in enriched.iterrows():
            row_dict = _to_str_dict(row)
            player_name = str(row_dict.get("player_name") or row_dict.get("player_name_baseline") or "").strip()
            team_abbr = str(row_dict.get("team_abbr") or "").strip().upper()
            if not player_name or not team_abbr:
                continue
            key = self._player_key(player_name, team_abbr)
            availability_multiplier = max(0.0, 1.0 - float(row_dict.get("injury_weight") or 0.0))
            row_dict["availability_multiplier"] = availability_multiplier
            context["players"][key] = row_dict
            active_details.append(row_dict)

        context["active_injuries"] = pd.DataFrame(active_details)
        if context["active_injuries"].empty:
            return context

        for team_abbr, grp in context["active_injuries"].groupby("team_abbr"):
            weighted_pts = float(pd.to_numeric(grp.get("weighted_pts", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            weighted_reb = float(pd.to_numeric(grp.get("weighted_reb", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            weighted_ast = float(pd.to_numeric(grp.get("weighted_ast", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            weighted_stocks = float(pd.to_numeric(grp.get("weighted_stocks", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            weighted_minutes = float(pd.to_numeric(grp.get("weighted_minutes", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
            impact_score = min(1.0, (weighted_pts / 35.0) + (weighted_minutes / 120.0) + (weighted_stocks / 6.0))
            context["teams"][str(team_abbr)] = {
                "weighted_pts": weighted_pts,
                "weighted_reb": weighted_reb,
                "weighted_ast": weighted_ast,
                "weighted_stocks": weighted_stocks,
                "weighted_minutes": weighted_minutes,
                "impact_score": round(float(impact_score), 4),
                "usage_boost": round(min(0.18, (weighted_pts / 90.0) + (weighted_ast / 60.0) + (weighted_minutes / 500.0)), 4),
                "rebound_boost": round(min(0.10, weighted_reb / 70.0), 4),
                "defensive_event_boost": round(min(0.08, weighted_stocks / 25.0), 4),
                "offense_penalty": round(min(0.18, (weighted_pts / 110.0) + (weighted_ast / 90.0)), 4),
                "defense_penalty": round(min(0.12, weighted_stocks / 30.0), 4),
                "rim_penalty": round(min(0.08, weighted_stocks / 40.0), 4),
                "affected_players": int(len(grp)),
                "status_mix": ", ".join(sorted({str(s).strip() for s in grp.get("status", pd.Series(dtype=str)).astype(str).tolist() if str(s).strip()})),
            }

        return context

    def _apply_player_injury_context(
        self,
        player_row: Mapping[str, Any],
        team_abbr: str,
        opp_abbr: str,
        market_type: str,
        projection: float,
        confidence: float,
        injury_context: Optional[Mapping[str, Any]],
    ) -> tuple[float, float, dict[str, Any]]:
        if not injury_context:
            return projection, confidence, {
                "injury_status": "",
                "injury_impact_score": 0.0,
                "team_injury_impact": 0.0,
                "opponent_injury_impact": 0.0,
                "injury_notes": "",
                "injury_baseline_projection": round(float(projection), 4),
                "injury_adjusted_projection": round(float(projection), 4),
                "injury_projection_delta": 0.0,
                "injury_baseline_confidence": round(float(confidence), 4),
                "injury_adjusted_confidence": round(float(confidence), 4),
                "injury_confidence_delta": 0.0,
                "player_points_recent_form_ratio": 1.0,
                "player_points_injury_independent_support": 0.0,
                "player_points_confidence_uplift_dampened": False,
                "player_points_confidence_uplift_reason": "",
            }

        players_ctx = injury_context.get("players", {}) if isinstance(injury_context, Mapping) else {}
        teams_ctx = injury_context.get("teams", {}) if isinstance(injury_context, Mapping) else {}
        player_key = self._player_key(player_row.get("player_name"), team_abbr)
        own_injury = players_ctx.get(player_key, {}) if isinstance(players_ctx, Mapping) else {}
        team_ctx = teams_ctx.get(str(team_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}
        opp_ctx = teams_ctx.get(str(opp_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}

        notes: list[str] = []
        injury_status = str(own_injury.get("status", "")).strip()
        own_impact = float(own_injury.get("injury_weight") or 0.0)
        team_impact = float(team_ctx.get("impact_score") or 0.0)
        opp_impact = float(opp_ctx.get("impact_score") or 0.0)

        adjusted_projection = float(projection)
        adjusted_confidence = float(confidence)
        baseline_projection = float(projection)
        baseline_confidence = float(confidence)
        points_recent_form_ratio = self._player_points_recent_form_ratio(player_row, baseline_projection)
        points_independent_support = self._player_points_injury_independent_support(
            player_row=player_row,
            baseline_projection=baseline_projection,
            recent_form_ratio=points_recent_form_ratio,
        )
        confidence_uplift_dampened = False
        confidence_uplift_reason = ""

        if injury_status:
            availability = max(0.0, float(own_injury.get("availability_multiplier") or (1.0 - own_impact)))
            adjusted_projection *= availability
            adjusted_confidence *= max(0.2, availability)
            notes.append(f"player_status:{injury_status}")

        minutes_avg = self._to_float(player_row.get("min_avg")) or 0.0
        role_factor = min(max((minutes_avg - 18.0) / 18.0, 0.0), 1.0)

        if not injury_status:
            team_usage_boost = float(team_ctx.get("usage_boost") or 0.0)
            if market_type in {"player_points", "player_assists", "player_3pt_made"}:
                adjusted_projection *= 1.0 + (team_usage_boost * (0.65 + role_factor * 0.35))
            elif market_type == "player_rebounds":
                adjusted_projection *= 1.0 + float(team_ctx.get("rebound_boost") or 0.0) * (0.60 + role_factor * 0.40)
            elif market_type in {"player_steals", "player_blocks"}:
                adjusted_projection *= 1.0 + float(team_ctx.get("defensive_event_boost") or 0.0)
            adjusted_confidence *= 1.0 + min(team_impact * 0.06, 0.08)
            if team_impact > 0:
                notes.append(f"teammate_absences:{team_impact:.2f}")

        if opp_impact > 0:
            if market_type in {"player_points", "player_assists", "player_3pt_made"}:
                adjusted_projection *= 1.0 + float(opp_ctx.get("defense_penalty") or 0.0) * 0.60
            elif market_type == "player_rebounds":
                adjusted_projection *= 1.0 + float(opp_ctx.get("rebound_boost") or 0.0) * 0.40
            elif market_type == "player_blocks":
                adjusted_projection *= 1.0 + float(opp_ctx.get("rim_penalty") or 0.0)
            adjusted_confidence *= 1.0 + min(opp_impact * 0.04, 0.05)
            notes.append(f"opponent_absences:{opp_impact:.2f}")

        if not injury_status and market_type.startswith("player_"):
            capped_projection = min(adjusted_projection, float(projection) * (1.0 + self.PLAYER_INJURY_BOOST_CAP))
            if capped_projection < adjusted_projection:
                adjusted_projection = capped_projection
                notes.append("injury_boost_capped")

        if market_type == "player_points" and not injury_status:
            projection_delta = adjusted_projection - baseline_projection
            confidence_delta = adjusted_confidence - baseline_confidence
            injury_strength = max(team_impact, opp_impact)
            if (
                projection_delta > max(0.75, baseline_projection * 0.04)
                and confidence_delta > 0.0
                and injury_strength >= 0.15
            ):
                max_allowed_confidence_delta = self.PLAYER_POINTS_MAX_CONFIDENCE_INJURY_UPLIFT
                max_allowed_confidence_delta = min(max_allowed_confidence_delta, 0.018 + points_independent_support)
                if points_recent_form_ratio < 0.95:
                    max_allowed_confidence_delta = min(
                        max_allowed_confidence_delta,
                        0.015 + (points_independent_support * 0.35),
                    )
                if injury_strength >= 0.30:
                    max_allowed_confidence_delta = min(
                        max_allowed_confidence_delta,
                        0.02 + (points_independent_support * 0.30),
                    )
                capped_confidence = min(adjusted_confidence, baseline_confidence + max_allowed_confidence_delta)
                if capped_confidence < adjusted_confidence:
                    adjusted_confidence = capped_confidence
                    confidence_uplift_dampened = True
                    confidence_uplift_reason = "projection_injury_uplift_already_applied"
                    notes.append("points_confidence_uplift_dampened")

        adjusted_confidence = min(max(adjusted_confidence, 0.25), 0.98)
        projection_delta = adjusted_projection - baseline_projection
        confidence_delta = adjusted_confidence - baseline_confidence
        return adjusted_projection, adjusted_confidence, {
            "injury_status": injury_status,
            "injury_impact_score": round(float(max(own_impact, team_impact, opp_impact)), 4),
            "team_injury_impact": round(float(team_impact), 4),
            "opponent_injury_impact": round(float(opp_impact), 4),
            "injury_notes": "; ".join(notes),
            "injury_baseline_projection": round(float(baseline_projection), 4),
            "injury_adjusted_projection": round(float(adjusted_projection), 4),
            "injury_projection_delta": round(float(projection_delta), 4),
            "injury_baseline_confidence": round(float(baseline_confidence), 4),
            "injury_adjusted_confidence": round(float(adjusted_confidence), 4),
            "injury_confidence_delta": round(float(confidence_delta), 4),
            "player_points_recent_form_ratio": round(float(points_recent_form_ratio), 4),
            "player_points_injury_independent_support": round(float(points_independent_support), 4),
            "player_points_confidence_uplift_dampened": bool(confidence_uplift_dampened),
            "player_points_confidence_uplift_reason": confidence_uplift_reason,
        }

    def _player_points_recent_form_ratio(
        self,
        player_row: Mapping[str, Any],
        baseline_projection: float,
    ) -> float:
        recent_avg = self._to_float(player_row.get("pts_recent"))
        season_avg = self._to_float(player_row.get("pts_avg"))
        anchor = max(abs(season_avg or 0.0), abs(float(baseline_projection)), 12.0)
        if recent_avg is not None:
            return max(0.0, min(1.5, float(recent_avg) / anchor))
        if season_avg is not None:
            return max(0.0, min(1.5, float(season_avg) / anchor))
        return 1.0

    def _player_points_injury_independent_support(
        self,
        *,
        player_row: Mapping[str, Any],
        baseline_projection: float,
        recent_form_ratio: float,
    ) -> float:
        minutes_avg = self._to_float(player_row.get("min_avg")) or 0.0
        season_avg = self._to_float(player_row.get("pts_avg")) or baseline_projection
        support = 0.0
        if recent_form_ratio >= 1.05:
            support += 0.02
        elif recent_form_ratio >= 0.98:
            support += 0.01
        if minutes_avg >= 32.0:
            support += 0.01
        if season_avg >= 18.0:
            support += 0.01
        return min(0.05, max(0.0, support))

    def _apply_player_points_realism_dampener(
        self,
        *,
        player_row: Mapping[str, Any],
        sportsbook_line: float,
        selection: str,
        projection: float,
        confidence: float,
        injury_payload: Mapping[str, Any],
        is_live_market: bool,
    ) -> tuple[float, float, dict[str, Any]]:
        payload = {
            "player_points_realism_dampened": False,
            "player_points_realism_dampener_reason": "",
            "player_points_projection_dampener": 0.0,
            "player_points_confidence_dampener": 0.0,
        }
        if not is_live_market or str(selection).strip().lower() != "over":
            return projection, confidence, payload

        profile_context = {
            "market_type": "player_points",
            "sportsbook_line": sportsbook_line,
            "minutes_avg": player_row.get("min_avg"),
            "minutes_recent": player_row.get("min_recent"),
        }
        line_band = self.board_audit.player_points_line_band(profile_context)
        profile_bucket = self.board_audit.player_profile_bucket(profile_context)
        injury_influence = max(
            self._to_float(injury_payload.get("team_injury_impact")) or 0.0,
            self._to_float(injury_payload.get("opponent_injury_impact")) or 0.0,
            self._to_float(injury_payload.get("injury_impact_score")) or 0.0,
        )
        recent_form_ratio = self._to_float(injury_payload.get("player_points_recent_form_ratio")) or self._player_points_recent_form_ratio(
            player_row,
            self._to_float(injury_payload.get("injury_baseline_projection")) or projection,
        )
        projection_delta = max(0.0, self._to_float(injury_payload.get("injury_projection_delta")) or 0.0)
        confidence_delta = max(0.0, self._to_float(injury_payload.get("injury_confidence_delta")) or 0.0)

        if profile_bucket != "role_low_usage":
            return projection, confidence, payload
        if line_band not in {"lte_14_5", "15_to_19_5"}:
            return projection, confidence, payload
        if injury_influence < 0.15:
            return projection, confidence, payload
        if recent_form_ratio >= 0.95:
            return projection, confidence, payload

        reason = "fragile_low_line_injury_over" if line_band == "lte_14_5" else "fragile_mid_line_injury_over"
        raw_projection_penalty = 0.35
        if line_band == "lte_14_5":
            raw_projection_penalty += 0.35
        if injury_influence >= 0.30:
            raw_projection_penalty += 0.25
        if recent_form_ratio < 0.85:
            raw_projection_penalty += 0.25
        raw_projection_penalty += min(0.45, projection_delta * 0.20)

        current_edge = max(0.0, float(projection) - float(sportsbook_line))
        projection_penalty = min(raw_projection_penalty, max(0.0, current_edge - 0.15))
        confidence_penalty = min(
            0.04,
            0.01 + (confidence_delta * 0.75) + (0.01 if recent_form_ratio < 0.85 else 0.0),
        )
        if projection_penalty <= 0.0 and confidence_penalty <= 0.0:
            return projection, confidence, payload

        dampened_projection = max(0.0, float(projection) - projection_penalty)
        dampened_confidence = max(0.25, float(confidence) - confidence_penalty)
        payload.update(
            {
                "player_points_realism_dampened": True,
                "player_points_realism_dampener_reason": reason,
                "player_points_projection_dampener": round(float(projection_penalty), 4),
                "player_points_confidence_dampener": round(float(confidence_penalty), 4),
            }
        )
        return dampened_projection, dampened_confidence, payload

    def _apply_team_injury_context(
        self,
        home_abbr: str,
        away_abbr: str,
        home_proj: float,
        away_proj: float,
        injury_context: Optional[Mapping[str, Any]],
    ) -> tuple[float, float]:
        if not injury_context:
            return home_proj, away_proj
        teams_ctx = injury_context.get("teams", {}) if isinstance(injury_context, Mapping) else {}
        home_ctx = teams_ctx.get(str(home_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}
        away_ctx = teams_ctx.get(str(away_abbr).upper(), {}) if isinstance(teams_ctx, Mapping) else {}

        home_off_penalty = float(home_ctx.get("offense_penalty") or 0.0)
        away_off_penalty = float(away_ctx.get("offense_penalty") or 0.0)
        home_def_penalty = float(home_ctx.get("defense_penalty") or 0.0)
        away_def_penalty = float(away_ctx.get("defense_penalty") or 0.0)

        adj_home = home_proj * (1.0 - home_off_penalty + away_def_penalty * 0.55)
        adj_away = away_proj * (1.0 - away_off_penalty + home_def_penalty * 0.55)
        return max(adj_home, 0.0), max(adj_away, 0.0)

    def _rebuild_calibration(self) -> None:
        feedback = self.get_feedback_history()
        if feedback.empty or "market_type" not in feedback.columns:
            self.calibration_path.write_text(json.dumps({}, indent=2), encoding="utf-8")
            return

        rules: dict[str, dict[str, Any]] = {}

        for market_type, grp in feedback.groupby("market_type"):
            sample_size = len(grp)

            hit_rate = float(grp["hit"].mean()) if "hit" in grp.columns else 0.0
            mae = (
                float((grp["model_projection"] - grp["actual_value"]).abs().mean())
                if {"model_projection", "actual_value"}.issubset(grp.columns)
                else 0.0
            )

            if sample_size < 15:
                multiplier = 1.0
            elif hit_rate < 0.48:
                multiplier = 0.92
            elif hit_rate < 0.52:
                multiplier = 0.97
            elif hit_rate > 0.58:
                multiplier = 1.05
            else:
                multiplier = 1.0

            rules[str(market_type)] = {
                "market_type": str(market_type),
                "sample_size": sample_size,
                "hit_rate": round(hit_rate, 4),
                "mae": round(mae, 4),
                "confidence_multiplier": round(multiplier, 4),
            }

        self.calibration_path.write_text(json.dumps(rules, indent=2), encoding="utf-8")

    def _load_calibration_rules(self) -> dict[str, CalibrationRule]:
        if not self.calibration_path.exists():
            return {}

        try:
            raw = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        out: dict[str, CalibrationRule] = {}
        for market_type, rule in raw.items():
            if not isinstance(rule, dict):
                continue
            out[str(market_type)] = CalibrationRule(
                market_type=str(market_type),
                sample_size=int(rule.get("sample_size", 0)),
                hit_rate=float(rule.get("hit_rate", 0.0)),
                mae=float(rule.get("mae", 0.0)),
                confidence_multiplier=float(rule.get("confidence_multiplier", 1.0)),
            )
        return out

    def _build_data_status_message(
        self,
        games: pd.DataFrame,
        odds: pd.DataFrame,
        selected_df: pd.DataFrame,
        rejected_df: pd.DataFrame,
        odds_fetch_status: str,
        odds_fetch_message: str,
        raw_odds_rows: int,
    ) -> str:
        if games.empty:
            return "No games were found for this date."

        if odds.empty:
            if odds_fetch_status and odds_fetch_status != "ok":
                return (
                    "Games were found, but odds were unavailable or degraded. "
                    f"Status: {odds_fetch_status}. {odds_fetch_message}".strip()
                )
            if raw_odds_rows > 0:
                return (
                    "Games were found, but none of the returned markets matched the currently "
                    "supported market map."
                )
            return (
                "Games were found, but no supported odds/prop markets were returned by the API. "
                "The app evaluated the slate and logged missing market coverage instead of failing silently."
            )

        if selected_df.empty and not rejected_df.empty:
            return (
                "Market data was found, but no selections qualified. "
                "Check the rejection summary for the exact reasons."
            )

        return "Games and market data loaded successfully."

    def _rejected_row(
        self,
        market_type: str,
        entity_name: str,
        team: str,
        opponent: str,
        prediction_date: str,
        rejection_reason: str,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        row = {
            "prediction_date": prediction_date,
            "market_type": market_type,
            "entity_name": entity_name,
            "team": team,
            "opponent": opponent,
            "selection": "",
            "sportsbook_line": None,
            "model_projection": None,
            "edge": None,
            "edge_abs": None,
            "confidence": None,
            "odds": None,
            "recommendation": "rejected",
            "rejection_reason": rejection_reason,
        }
        if extra_fields:
            row.update(extra_fields)
        return self._apply_board_audit_row(row)

    def _market_to_stat_key(self, market_type: Any) -> Optional[str]:
        if market_type is None:
            return None
        return self.PLAYER_MARKETS.get(str(market_type))

    def _stat_key_to_market(self, stat_key: Any) -> Optional[str]:
        if stat_key is None:
            return None
        return self.STAT_TO_MARKET_MAP.get(str(stat_key))

    def _map_market_type(self, raw_name: Any) -> Optional[str]:
        return runtime_normalize_market_alias(raw_name)

    def _american_odds_to_implied_prob(self, odds: Any) -> Optional[float]:
        val = self._to_float(odds)
        if val is None or val == 0:
            return None

        if val > 0:
            return 100.0 / (val + 100.0)
        return abs(val) / (abs(val) + 100.0)

    def _parse_minutes(self, value: Any) -> float:
        return shared_parse_minutes(value)

    def _player_key(self, player_name: Any, team_abbr: Any) -> str:
        return f"{str(player_name).strip().lower()}__{str(team_abbr).strip().upper()}"

    def _infer_opponent_from_game(self, game: dict[str, Any], team_abbr: str) -> str:
        return infer_opponent_from_game(game, team_abbr)

    def _to_float(self, value: Any) -> Optional[float]:
        return shared_safe_float(value)

    def _env_int(self, key: str, default: int) -> int:
        value = os.getenv(key, "").strip()
        if not value:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    def _recency_weights(self, game_dates: pd.Series, half_life_days: float) -> pd.Series:
        dates = pd.to_datetime(game_dates, errors="coerce")
        if dates.notna().sum() == 0:
            return pd.Series(1.0, index=game_dates.index, dtype=float)

        latest = dates.max()
        days_ago = (latest - dates).dt.days
        fallback_days = float(days_ago.dropna().max()) if days_ago.notna().any() else 0.0
        days_ago = days_ago.fillna(fallback_days)
        weights = 0.5 ** (days_ago / max(half_life_days, 1.0))
        return weights.clip(lower=0.15, upper=1.0)

    def _weighted_average(self, values: pd.Series, weights: pd.Series) -> float:
        numeric_values = pd.to_numeric(values, errors="coerce")
        numeric_weights = pd.to_numeric(weights, errors="coerce")
        valid = numeric_values.notna() & numeric_weights.notna() & (numeric_weights > 0)
        if not valid.any():
            return 0.0

        value_slice = numeric_values[valid]
        weight_slice = numeric_weights[valid]
        return float((value_slice * weight_slice).sum() / weight_slice.sum())

    def _weighted_std(self, values: pd.Series, weights: pd.Series) -> float:
        numeric_values = pd.to_numeric(values, errors="coerce")
        numeric_weights = pd.to_numeric(weights, errors="coerce")
        valid = numeric_values.notna() & numeric_weights.notna() & (numeric_weights > 0)
        if not valid.any():
            return 0.0

        value_slice = numeric_values[valid]
        weight_slice = numeric_weights[valid]
        mean = self._weighted_average(value_slice, weight_slice)
        variance = (((value_slice - mean) ** 2) * weight_slice).sum() / weight_slice.sum()
        return float(math.sqrt(max(variance, 0.0)))

    def _recent_average(self, values: pd.Series, count: int) -> float:
        numeric_values = pd.to_numeric(values, errors="coerce").dropna()
        if numeric_values.empty:
            return 0.0
        return float(numeric_values.tail(count).mean())

    def _blend_average_and_recent(
        self,
        average_value: Any,
        recent_value: Any,
        recent_weight: float = 0.30,
    ) -> float:
        avg = self._to_float(average_value) or 0.0
        recent = self._to_float(recent_value)
        if recent is None:
            return avg

        recent_weight = min(max(recent_weight, 0.0), 0.5)
        return (avg * (1.0 - recent_weight)) + (recent * recent_weight)

    def _build_league_context(self, team_baselines: pd.DataFrame) -> dict[str, float]:
        if team_baselines.empty:
            return {}

        context: dict[str, float] = {}
        for column in self.OPPONENT_ALLOWANCE_MAP.values():
            if column in team_baselines.columns:
                numeric = pd.to_numeric(team_baselines[column], errors="coerce").dropna()
                if not numeric.empty:
                    context[column] = float(numeric.mean())
        return context

    def _sgp_empty_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "prediction_date",
                "game",
                "leg_count",
                "sgp_label",
                "legs",
                "combined_confidence",
                "combined_hit_probability",
                "estimated_decimal_odds",
                "estimated_american_odds",
                "sgp_quality_score",
                "source_mix",
                "correlation_penalty",
                "combo_signature",
            ]
        )

    def _sgp_leg_probability(self, row: Mapping[str, Any]) -> float:
        probability_candidates = [
            self._to_float(row.get("implied_hit_probability")),
            self._to_float(row.get("combined_hit_probability")),
            self._to_float(row.get("confidence")),
            self._to_float(row.get("strike_confidence")),
        ]
        for value in probability_candidates:
            if value is not None:
                return min(max(float(value), 0.50), 0.92)
        return 0.55

    def _sgp_leg_rank_score(self, row: Mapping[str, Any]) -> float:
        score_candidates = [
            self._to_float(row.get("predictive_quality_score")),
            self._to_float(row.get("strike_quality_score")),
            self._to_float(row.get("quality_score")),
        ]
        base_score = next((float(v) for v in score_candidates if v is not None), 0.0)
        confidence = self._sgp_leg_probability(row)
        edge_abs = abs(self._to_float(row.get("edge_abs")) or self._to_float(row.get("edge")) or 0.0)
        return base_score + (confidence * 25.0) + (edge_abs * 4.0)

    def _sgp_leg_side(self, row: Mapping[str, Any]) -> str:
        selection = str(row.get("selection", "")).strip()
        if selection and selection.lower() != "projection":
            return selection.title()
        recommended = str(row.get("recommended_side", "")).strip()
        if recommended:
            return recommended.title()
        edge = self._to_float(row.get("edge")) or 0.0
        return "Over" if edge >= 0 else "Under"

    def _sgp_leg_display_label(self, row: Mapping[str, Any]) -> str:
        explicit = str(row.get("bet_label", "")).strip()
        if explicit and "PROJECTION" not in explicit.upper():
            return explicit

        entity_name = str(row.get("entity_name", "")).strip()
        market_type = str(row.get("market_type", "")).strip()
        market_label = str(row.get("market_label") or self._market_label(market_type)).strip().upper()
        side = self._sgp_leg_side(row).upper()

        line_value = self._to_float(
            row.get("entry_line")
            if str(row.get("sgp_source", "")).strip() == "predictive"
            else row.get("sportsbook_line")
        )
        if line_value is None:
            line_value = self._to_float(row.get("predicted_book_line"))
        if line_value is None:
            line_value = self._to_float(row.get("model_projection"))

        if market_type == "moneyline":
            return f"{entity_name} {side}".strip()
        if line_value is None:
            return f"{entity_name} {side} {market_label}".strip()
        return f"{entity_name} {side} {line_value:.1f} {market_label}".strip()

    def _sgp_candidate_frame(self, frame: pd.DataFrame, label: str) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return pd.DataFrame()

        out = frame.copy()
        out["sgp_source"] = label
        out["market_type"] = out.get("market_type", pd.Series("", index=out.index)).astype(str)
        out["entity_name"] = out.get("entity_name", pd.Series("", index=out.index)).astype(str)
        out["team"] = out.get("team", pd.Series("", index=out.index)).astype(str)
        out["opponent"] = out.get("opponent", pd.Series("", index=out.index)).astype(str)
        out["market_label"] = out.get("market_label", out["market_type"].map(self._market_label))

        allowed_markets = {
            "player_points",
            "player_rebounds",
            "player_assists",
            "player_3pt_made",
            "player_steals",
            "player_blocks",
            "team_total",
            "moneyline",
        }
        out = out[out["market_type"].isin(allowed_markets)].copy()
        if out.empty:
            return out

        out = self._live_market_only(out)
        if out.empty:
            return out

        for col in ["confidence", "quality_score", "strike_quality_score", "predictive_quality_score", "edge_abs", "edge", "implied_hit_probability"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")

        if "edge_abs" not in out.columns and "edge" in out.columns:
            out["edge_abs"] = pd.to_numeric(out["edge"], errors="coerce").abs()

        volatile_mask = out["market_type"].isin(["player_steals", "player_blocks"])
        if volatile_mask.any():
            edge_series = pd.to_numeric(out.get("edge_abs", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
            confidence_series = pd.to_numeric(out.get("confidence", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
            live_line_series = pd.to_numeric(out.get("sportsbook_line", pd.Series(dtype=float)), errors="coerce")
            volatile_keep = (~volatile_mask) | (
                (edge_series >= self.SGP_VOLATILE_MIN_EDGE)
                & (confidence_series >= self.SGP_VOLATILE_MIN_CONFIDENCE)
                & (live_line_series.fillna(0.0) >= 0.5)
            )
            out = out[volatile_keep].copy()
            if out.empty:
                return out

        out["sgp_leg_probability"] = [self._sgp_leg_probability(_to_str_dict(row)) for _, row in out.iterrows()]
        out["sgp_rank_score"] = [self._sgp_leg_rank_score(_to_str_dict(row)) for _, row in out.iterrows()]
        out["sgp_side"] = [self._sgp_leg_side(_to_str_dict(row)) for _, row in out.iterrows()]
        out["sgp_leg_label"] = [self._sgp_leg_display_label(_to_str_dict(row)) for _, row in out.iterrows()]

        dedupe_cols = [c for c in ["entity_name", "market_type", "sgp_side"] if c in out.columns]
        if dedupe_cols:
            out = out.sort_values(by=["sgp_rank_score"], ascending=False).drop_duplicates(subset=dedupe_cols, keep="first")

        return out.reset_index(drop=True)

    def _sgp_combo_allowed(self, legs: Sequence[Mapping[str, Any]]) -> bool:
        player_names = [str(leg.get("entity_name", "")).strip() for leg in legs if str(leg.get("entity_name", "")).strip()]
        if len(player_names) != len(set(player_names)):
            return False

        leg_keys = [
            f"{str(leg.get('entity_name', '')).strip().lower()}|{str(leg.get('market_type', '')).strip().lower()}|{self._sgp_leg_side(leg).lower()}"
            for leg in legs
        ]
        if len(leg_keys) != len(set(leg_keys)):
            return False

        markets = [str(leg.get("market_type", "")).strip().lower() for leg in legs]

        team_total_count = sum(1 for market in markets if market == "team_total")
        moneyline_count = sum(1 for market in markets if market == "moneyline")
        if team_total_count > 1 or moneyline_count > 1:
            return False

        team_player_counts: dict[str, int] = {}
        for leg in legs:
            market_type = str(leg.get("market_type", "")).strip().lower()
            if market_type.startswith("player_"):
                team = str(leg.get("team", "")).strip().upper()
                if team:
                    team_player_counts[team] = team_player_counts.get(team, 0) + 1
        if any(count > 2 for count in team_player_counts.values()):
            return False

        over_count = sum(1 for leg in legs if self._sgp_leg_side(leg).lower() == "over")
        under_count = sum(1 for leg in legs if self._sgp_leg_side(leg).lower() == "under")
        if over_count == len(legs) and len(set(team_player_counts.keys())) == 1 and len(legs) >= 3:
            return False
        if under_count == len(legs) and len(legs) >= 3:
            return False

        return True

    def _sgp_correlation_penalty(self, legs: Sequence[Mapping[str, Any]]) -> float:
        penalty = 1.0
        teams = [str(leg.get("team", "")).strip().upper() for leg in legs]
        sides = [self._sgp_leg_side(leg).lower() for leg in legs]
        markets = [str(leg.get("market_type", "")).strip().lower() for leg in legs]

        if len(set(teams)) == 1 and len(legs) >= 2:
            penalty *= 0.95

        over_count = sum(1 for side in sides if side == "over")
        under_count = sum(1 for side in sides if side == "under")
        if over_count == len(legs):
            penalty *= 0.97
        if under_count == len(legs):
            penalty *= 0.98

        player_legs = [leg for leg in legs if str(leg.get("market_type", "")).startswith("player_")]
        same_team_player_pairs = 0
        for left, right in itertools.combinations(player_legs, 2):
            if str(left.get("team", "")).strip().upper() == str(right.get("team", "")).strip().upper():
                same_team_player_pairs += 1
        if same_team_player_pairs:
            penalty *= 0.97 ** same_team_player_pairs

        duplicate_market_pairs = 0
        for left, right in itertools.combinations(player_legs, 2):
            if str(left.get("market_type", "")).strip().lower() == str(right.get("market_type", "")).strip().lower():
                duplicate_market_pairs += 1
        if duplicate_market_pairs:
            penalty *= 0.985 ** duplicate_market_pairs

        if "team_total" in markets and any(m.startswith("player_") for m in markets):
            penalty *= 0.96

        if "moneyline" in markets and len(legs) >= 3:
            penalty *= 0.97

        if len(legs) >= 3:
            penalty *= 0.98

        return max(min(penalty, 1.0), 0.82)

    def _build_sgp_board(
        self,
        prediction_date: str,
        games: pd.DataFrame,
        elite_df: pd.DataFrame,
        full_market_df: pd.DataFrame,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        limit = self.SGP_BOARD_LIMIT if limit is None else int(limit)
        source_frames: list[pd.DataFrame] = []
        for label, frame in [("elite", elite_df), ("full_market", full_market_df)]:
            candidate_frame = self._sgp_candidate_frame(frame, label)
            if not candidate_frame.empty:
                source_frames.append(candidate_frame)
        if not source_frames:
            return self._sgp_empty_df()

        candidates = pd.concat(source_frames, ignore_index=True)
        if candidates.empty:
            return self._sgp_empty_df()

        candidates = candidates.sort_values(by=["sgp_rank_score"], ascending=False).reset_index(drop=True)

        game_pairs: list[tuple[str, str]] = []
        if isinstance(games, pd.DataFrame) and not games.empty:
            for _, row in games.iterrows():
                home = str(row.get("home_team_abbr", "")).strip().upper()
                away = str(row.get("visitor_team_abbr", "")).strip().upper()
                if home and away:
                    game_pairs.append((home, away))
        if not game_pairs and {"team", "opponent"}.issubset(candidates.columns):
            seen_pairs: set[tuple[str, str]] = set()
            for _, row in candidates.iterrows():
                team = str(row.get("team", "")).strip().upper()
                opponent = str(row.get("opponent", "")).strip().upper()
                if team and opponent:
                    pair = _pair_key(team, opponent)
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        game_pairs.append((pair[0], pair[1]))

        bundles: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()
        target_candidates = max(limit * 3, limit)

        for home, away in game_pairs:
            teams = {home, away}
            pool = candidates[
                candidates.get("team", pd.Series("", index=candidates.index)).astype(str).str.upper().isin(teams)
            ].copy()
            if pool.empty:
                continue

            if "opponent" in pool.columns:
                pool = pool[
                    pool["opponent"].astype(str).str.upper().isin(teams)
                    | pool["market_type"].astype(str).isin(["moneyline", "team_total"])
                ].copy()
            if len(pool) < self.SGP_MIN_LEGS:
                continue

            pool = pool.sort_values(by=["sgp_rank_score"], ascending=False).head(self.SGP_PER_GAME_CANDIDATE_LIMIT).reset_index(drop=True)
            leg_records = [_to_str_dict(row) for _, row in pool.iterrows()]
            local_bundles = 0

            for leg_count in range(self.SGP_MIN_LEGS, min(self.SGP_MAX_LEGS, len(leg_records)) + 1):
                for combo in itertools.combinations(leg_records, leg_count):
                    legs = list(combo)
                    if not self._sgp_combo_allowed(legs):
                        continue

                    leg_labels = [str(leg.get("sgp_leg_label") or leg.get("bet_label") or leg.get("entity_name") or "").strip() for leg in legs]
                    signature = "||".join(sorted(label.lower() for label in leg_labels if label))
                    if not signature or signature in seen_signatures:
                        continue

                    leg_probs = [self._sgp_leg_probability(leg) for leg in legs]
                    combined_hit_prob = 1.0
                    for prob in leg_probs:
                        combined_hit_prob *= prob
                    correlation_penalty = self._sgp_correlation_penalty(legs)
                    combined_hit_prob *= correlation_penalty
                    combined_hit_prob = min(max(combined_hit_prob, 0.01), 0.90)

                    combined_conf = sum(leg_probs) / len(leg_probs)
                    if combined_hit_prob < self.SGP_MIN_COMBINED_CONFIDENCE:
                        continue

                    quality_values = [self._sgp_leg_rank_score(leg) for leg in legs]
                    diversity_bonus = len({str(leg.get("sgp_source", "")).strip() for leg in legs if str(leg.get("sgp_source", "")).strip()}) * 1.5
                    sgp_quality = (sum(quality_values) / len(quality_values)) + (combined_hit_prob * 100.0) + diversity_bonus
                    decimal_odds = round(1.0 / max(combined_hit_prob, 0.01), 4)

                    bundles.append(
                        {
                            "prediction_date": prediction_date,
                            "game": f"{away} @ {home}",
                            "leg_count": len(legs),
                            "sgp_label": " + ".join(leg_labels),
                            "legs": " | ".join(leg_labels),
                            "combined_confidence": round(combined_conf, 4),
                            "combined_hit_probability": round(combined_hit_prob, 4),
                            "estimated_decimal_odds": decimal_odds,
                            "estimated_american_odds": self._decimal_to_american(decimal_odds),
                            "sgp_quality_score": round(sgp_quality, 4),
                            "source_mix": ",".join(sorted({str(leg.get("sgp_source", "")).strip() for leg in legs if str(leg.get("sgp_source", "")).strip()})),
                            "correlation_penalty": round(float(correlation_penalty), 4),
                            "combo_signature": signature,
                        }
                    )
                    seen_signatures.add(signature)
                    local_bundles += 1
                    if local_bundles >= target_candidates:
                        break
                if local_bundles >= target_candidates:
                    break

        out = pd.DataFrame(bundles)
        if out.empty:
            return self._sgp_empty_df()

        out = out.sort_values(
            by=["sgp_quality_score", "combined_hit_probability", "combined_confidence", "leg_count"],
            ascending=[False, False, False, True],
        ).drop_duplicates(subset=["combo_signature"], keep="first").head(limit).reset_index(drop=True)
        return out

    def _decimal_to_american(self, decimal_odds: float) -> int:
        decimal_odds = max(float(decimal_odds or 0.0), 1.01)
        if decimal_odds >= 2.0:
            return int(round((decimal_odds - 1.0) * 100.0))
        return int(round(-100.0 / max(decimal_odds - 1.0, 0.01)))

    def _grade_history(self, prediction_date: str, lookback_days: Optional[int] = None) -> tuple[pd.DataFrame, dict[str, Any]]:
        lookback_days = self.GRADE_LOOKBACK_DAYS if lookback_days is None else int(lookback_days)
        history = self._safe_read_csv(self.prediction_history_path)
        if history.empty:
            return pd.DataFrame(), {"graded_rows": 0, "window_days": lookback_days, "status": "no_history"}

        hist = history.copy()
        prediction_series = hist.get("prediction_date")
        if prediction_series is None:
            hist["prediction_date"] = pd.NaT
        else:
            hist["prediction_date"] = pd.to_datetime(prediction_series.astype(str), errors="coerce")
        cutoff = pd.to_datetime(prediction_date, errors="coerce")
        if pd.isna(cutoff):
            cutoff = pd.Timestamp.utcnow().normalize()
        start = cutoff - pd.Timedelta(days=lookback_days)
        hist = hist[(hist["prediction_date"].notna()) & (hist["prediction_date"] >= start) & (hist["prediction_date"] < cutoff)].copy()
        hist["prediction_date_str"] = hist["prediction_date"].dt.strftime("%Y-%m-%d")
        if hist.empty:
            return pd.DataFrame(), {"graded_rows": 0, "window_days": lookback_days, "status": "no_eligible_predictions"}

        existing_feedback = self._safe_read_csv(self.feedback_path)
        if not existing_feedback.empty and "grade_key" in existing_feedback.columns:
            graded_keys = set(existing_feedback["grade_key"].astype(str).tolist())
            hist["grade_key"] = hist.apply(lambda r: f"{r.get('prediction_date_str','')}|{r.get('market_type','')}|{r.get('entity_name','')}|{r.get('selection','')}|{r.get('sportsbook_line','')}", axis=1)
            hist = hist[~hist["grade_key"].astype(str).isin(graded_keys)].copy()
        else:
            hist["grade_key"] = hist.apply(lambda r: f"{r.get('prediction_date_str','')}|{r.get('market_type','')}|{r.get('entity_name','')}|{r.get('selection','')}|{r.get('sportsbook_line','')}", axis=1)
        if hist.empty:
            return pd.DataFrame(), {"graded_rows": 0, "window_days": lookback_days, "status": "already_graded"}

        client = self._get_client()
        results: list[dict[str, Any]] = []
        for raw_date_value, day_df in hist.groupby("prediction_date_str"):
            date_str = str(raw_date_value)
            raw_stats = client.get_stats(date_str, date_str)
            stats = self._normalize_stats(raw_stats)
            raw_games = client.get_games(date_str)
            game_rows = [_to_str_dict(row) for row in raw_games.to_dict("records")] if isinstance(raw_games, pd.DataFrame) and not raw_games.empty else []
            for _, row in day_df.iterrows():
                graded = self._grade_single_prediction(_to_str_dict(row), stats, game_rows)
                if graded:
                    results.append(graded)

        graded_df = pd.DataFrame(results)
        if graded_df.empty:
            return graded_df, {"graded_rows": 0, "window_days": lookback_days, "status": "no_resolved_results"}

        self._append_history(self.feedback_path, graded_df)
        win_rate = pd.to_numeric(graded_df.get("is_win", pd.Series(dtype=float)), errors="coerce").fillna(0.0).mean() if not graded_df.empty else 0.0
        return graded_df, {
            "graded_rows": int(len(graded_df)),
            "window_days": lookback_days,
            "status": "ok",
            "wins": int(pd.to_numeric(graded_df.get("is_win", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
            "pushes": int(pd.to_numeric(graded_df.get("is_push", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
            "losses": int(pd.to_numeric(graded_df.get("is_loss", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()),
            "win_rate": round(float(win_rate), 4),
        }

    def _grade_single_prediction(self, row: Mapping[str, Any], stats: pd.DataFrame, game_rows: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
        market_type = str(row.get("market_type", ""))
        selection = str(row.get("selection", ""))
        line = float(self._to_float(row.get("sportsbook_line")) or 0.0)
        actual_value: Optional[float] = None
        result = "unresolved"
        team = str(row.get("team", ""))
        opponent = str(row.get("opponent", ""))
        entity_name = str(row.get("entity_name", ""))

        if market_type in self.PLAYER_MARKETS:
            stat_key = self.PLAYER_MARKETS[market_type]
            if not stats.empty:
                mask = stats.get("player_name", pd.Series("", index=stats.index)).astype(str).eq(entity_name)
                if team:
                    mask = mask & stats.get("team_abbr", pd.Series("", index=stats.index)).astype(str).eq(team)
                sample = stats[mask].copy()
                if not sample.empty and stat_key in sample.columns:
                    actual_value = float(pd.to_numeric(sample[stat_key], errors="coerce").fillna(0.0).iloc[0])
        elif market_type == "moneyline":
            for g in game_rows:
                home = g.get("home_team", {}) if isinstance(g.get("home_team"), dict) else {}
                visitor = g.get("visitor_team", {}) if isinstance(g.get("visitor_team"), dict) else {}
                home_abbr = str(home.get("abbreviation") or g.get("home_team_abbr") or "")
                away_abbr = str(visitor.get("abbreviation") or g.get("visitor_team_abbr") or "")
                if {home_abbr, away_abbr} == {team, opponent}:
                    home_score = self._to_float(g.get("home_team_score")) or 0.0
                    away_score = self._to_float(g.get("visitor_team_score")) or 0.0
                    actual_value = 1.0 if ((selection == home_abbr and home_score > away_score) or (selection == away_abbr and away_score > home_score)) else 0.0
                    break
        elif market_type == "team_total":
            for g in game_rows:
                home = g.get("home_team", {}) if isinstance(g.get("home_team"), dict) else {}
                visitor = g.get("visitor_team", {}) if isinstance(g.get("visitor_team"), dict) else {}
                home_abbr = str(home.get("abbreviation") or g.get("home_team_abbr") or "")
                away_abbr = str(visitor.get("abbreviation") or g.get("visitor_team_abbr") or "")
                if {home_abbr, away_abbr} == {team, opponent}:
                    if team == home_abbr:
                        actual_value = float(self._to_float(g.get("home_team_score")) or 0.0)
                    elif team == away_abbr:
                        actual_value = float(self._to_float(g.get("visitor_team_score")) or 0.0)
                    break

        if actual_value is None:
            return None

        if market_type == "moneyline":
            result = "win" if actual_value >= 1.0 else "loss"
        else:
            if selection == "Over":
                result = "win" if actual_value > line else "push" if actual_value == line else "loss"
            elif selection == "Under":
                result = "win" if actual_value < line else "push" if actual_value == line else "loss"
            else:
                result = "unresolved"

        return {
            "grade_key": row.get("grade_key"),
            "prediction_date": row.get("prediction_date_str") or row.get("prediction_date"),
            "market_type": market_type,
            "entity_name": entity_name,
            "team": team,
            "opponent": opponent,
            "selection": selection,
            "sportsbook_line": line,
            "actual_value": round(float(actual_value), 4),
            "result": result,
            "graded_result": result,
            "is_win": 1 if result == "win" else 0,
            "is_push": 1 if result == "push" else 0,
            "is_loss": 1 if result == "loss" else 0,
        }

    def _append_history(self, path: Path, df: pd.DataFrame) -> None:
        if df.empty:
            return

        if path.exists():
            existing = self._safe_read_csv(path)
            existing_nonempty = not existing.empty and not existing.dropna(how="all").empty
            df_nonempty = not df.empty and not df.dropna(how="all").empty
            if existing_nonempty and df_nonempty:
                columns = list(dict.fromkeys([*existing.columns.tolist(), *df.columns.tolist()]))
                concat_frames = [
                    frame.dropna(axis=1, how="all")
                    for frame in (existing, df)
                    if not frame.empty and not frame.dropna(how="all").empty
                ]
                combined = pd.concat(concat_frames, ignore_index=True)
                for column in columns:
                    if column not in combined.columns:
                        combined[column] = pd.NA
                combined = combined.reindex(columns=columns)
            elif existing_nonempty:
                combined = existing.copy()
            else:
                combined = df.copy()
            combined.to_csv(path, index=False)
        else:
            df.to_csv(path, index=False)

    def _safe_read_csv(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(path, low_memory=False)
        except Exception:
            return pd.DataFrame()

    def _log_run(self, row: dict[str, Any]) -> None:
        self.logger.info("run_log %s", _safe_json_dumps(row))
        df = pd.DataFrame([row])
        self._append_history(self.run_log_path, df)


def _cli_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        return pd.DataFrame()
    return df.copy()


def _empty_player_points_elite_admission_df() -> pd.DataFrame:
    return pd.DataFrame(columns=list(PLAYER_POINTS_ELITE_ADMISSION_COLUMNS))


def _extract_player_points_elite_admission_df(
    prediction_outputs: Mapping[str, Any],
    final_board_construction: Any,
) -> pd.DataFrame:
    direct_payload = prediction_outputs.get("player_points_elite_admission")
    if isinstance(direct_payload, pd.DataFrame):
        out = direct_payload.copy()
    elif isinstance(direct_payload, list):
        out = pd.DataFrame(direct_payload)
    else:
        rows = []
        if isinstance(final_board_construction, Mapping):
            elite_payload = final_board_construction.get("elite", {})
            if isinstance(elite_payload, Mapping):
                rows = elite_payload.get("player_points_elite_admission_rows", []) or []
        out = pd.DataFrame(rows)

    if out.empty:
        return _empty_player_points_elite_admission_df()

    out = out.copy()
    for column in PLAYER_POINTS_ELITE_ADMISSION_COLUMNS:
        if column not in out.columns:
            out[column] = None
    return out.loc[:, list(PLAYER_POINTS_ELITE_ADMISSION_COLUMNS)].reset_index(drop=True)


def _write_dataframe(path: Path, df: pd.DataFrame) -> None:
    safe_df = _cli_dataframe(df)
    safe_df.to_csv(path, index=False)


def _top_rows(df: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    if df.empty:
        return df

    sortable = df.copy()
    for column in ["sgp_quality_score", "combined_hit_probability", "combined_confidence", "quality_score", "confidence", "edge_abs"]:
        if column in sortable.columns:
            sortable[column] = pd.to_numeric(sortable[column], errors="coerce")

    sort_columns = [col for col in ["sgp_quality_score", "combined_hit_probability", "combined_confidence", "quality_score", "confidence", "edge_abs"] if col in sortable.columns]
    if sort_columns:
        sortable = sortable.sort_values(by=sort_columns, ascending=[False] * len(sort_columns))
    return sortable.head(limit).reset_index(drop=True)


def _write_grading_outputs(
    out_dir: Path,
    prediction_date: str,
    grading_df: pd.DataFrame,
    grading_bucket_summary: Optional[dict[str, Any]] = None,
    elite_df: Optional[pd.DataFrame] = None,
    verbose_outputs: bool = False,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    graded = _cli_dataframe(grading_df)
    summary_payload = (
        dict(grading_bucket_summary)
        if isinstance(grading_bucket_summary, dict)
        else summarize_graded_props(graded.to_dict("records") if not graded.empty else [])
    )
    summary_df = flatten_grading_summary(summary_payload)
    elite_rows = _cli_dataframe(elite_df) if isinstance(elite_df, pd.DataFrame) else pd.DataFrame()
    elite_records = elite_rows.to_dict("records") if not elite_rows.empty else None
    points_calibration_payload = summarize_player_points_calibration(
        graded.to_dict("records") if not graded.empty else [],
        elite_records,
    )
    points_uplift_audit_payload = summarize_player_points_uplift_audit(
        graded.to_dict("records") if not graded.empty else [],
        elite_records,
    )
    replay_summary_payload = summarize_elite_filter_replay(
        graded.to_dict("records") if not graded.empty else [],
        elite_records,
    )

    output_layout = OutputLayoutPolicy(
        out_dir / "runtime",
        OutputLayoutConfig(verbose_outputs=verbose_outputs),
    )
    paths = output_layout.grading_paths(prediction_date)
    _write_dataframe(paths["grading_results"], graded)
    paths["grading_summary_json"].write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    paths["player_points_calibration_json"].write_text(
        json.dumps(
            {
                "prediction_date": prediction_date,
                "player_points_calibration": points_calibration_payload,
                "player_points_uplift_audit": points_uplift_audit_payload,
                "elite_filter_replay": replay_summary_payload,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if "grading_summary_csv" in paths:
        _write_dataframe(paths["grading_summary_csv"], summary_df)
    return paths



def _build_report_text(
    prediction_date: str,
    fit_metrics: Optional[dict[str, Any]],
    summary: dict[str, Any],
    elite_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
    all_stats_df: pd.DataFrame,
    team_board_df: pd.DataFrame,
    strike_df: pd.DataFrame,
    predictive_lines_df: pd.DataFrame,
    sgp_df: pd.DataFrame,
    grading_df: pd.DataFrame,
    near_miss_df: pd.DataFrame,
    board_diagnostics: Optional[dict[str, Any]] = None,
    verbose_sections: bool = False,
) -> str:
    lines = [f"CourtVision AI Report - {prediction_date}", ""]

    if fit_metrics:
        lines.append("Fit Metrics")
        for key, value in fit_metrics.items():
            lines.append(f"{key}: {value}")
        lines.append("")

    lines.append("Prediction Summary")
    for key in [
        "games_analyzed",
        "players_evaluated",
        "markets_evaluated",
        "selected_count",
        "elite_count",
        "full_market_count",
        "stat_only_count",
        "strike_count",
        "predictive_lines_count",
        "sgp_count",
        "team_board_count",
        "near_miss_count",
        "rejected_count",
    ]:
        if key in summary:
            lines.append(f"{key}: {summary.get(key)}")
    lines.append(f"board_type: {summary.get('board_type', '')}")
    lines.append(f"data_status: {summary.get('data_status', '')}")
    lines.append("")

    final_board_construction = {}
    if isinstance(board_diagnostics, dict):
        final_board_construction = board_diagnostics.get("final_board_construction", {})
    elif isinstance(summary.get("final_board_construction"), dict):
        final_board_construction = summary.get("final_board_construction", {})
    if isinstance(final_board_construction, dict) and final_board_construction:
        lines.append("Final Board Construction")
        for board_name in ["elite", "full_market"]:
            payload = final_board_construction.get(board_name, {})
            if not isinstance(payload, dict):
                continue
            input_count = int(((payload.get("input_live_candidates") or {}).get("count", 0)) or 0)
            core_input_count = int(payload.get("core_pass_candidate_count", 0) or 0)
            rescue_input_count = int(payload.get("live_quality_rescue_candidate_count", 0) or 0)
            primary_count = int(((payload.get("post_primary_selection") or {}).get("count", 0)) or 0)
            exposure_count = int(((payload.get("post_exposure_caps") or {}).get("count", 0)) or 0)
            final_count = int(((payload.get("post_backfill") or {}).get("count", 0)) or 0)
            backfill_added = int(payload.get("backfill_added_count", 0) or 0)
            lines.append(
                f"{board_name}: input_live={input_count} | core={core_input_count} | rescue={rescue_input_count} -> primary={primary_count} -> exposure={exposure_count} -> final={final_count} | backfill_added={backfill_added}"
            )
            backfill_modes = payload.get("backfill_added_by_qualification_gate_mode", [])
            if isinstance(backfill_modes, list) and backfill_modes:
                mode_bits = [
                    f"{str(item.get('key', ''))}={int(item.get('count', 0) or 0)}"
                    for item in backfill_modes
                    if str(item.get("key", "")).strip()
                ]
                if mode_bits:
                    lines.append(f"{board_name}_backfill_gate_modes: " + ", ".join(mode_bits))
            source_lanes = payload.get("final_selected_by_source_lane", [])
            if isinstance(source_lanes, list) and source_lanes:
                lane_bits = [
                    f"{str(item.get('key', ''))}={int(item.get('count', 0) or 0)}"
                    for item in source_lanes
                    if str(item.get("key", "")).strip()
                ]
                if lane_bits:
                    lines.append(f"{board_name}_final_source_lanes: " + ", ".join(lane_bits))
        lines.append("")

    if "odds_diagnostics" in summary:
        lines.append("Odds Diagnostics")
        for key, value in summary["odds_diagnostics"].items():
            lines.append(f"{key}: {value}")
    lines.append("")

    def _safe_report_text(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "null"} else text

    def _has_manual_context(row: pd.Series) -> bool:
        for col in [
            "manual_status",
            "manual_minutes_limit",
            "manual_projection_adjustment",
            "manual_confidence_adjustment",
            "manual_context_reason",
        ]:
            if col in row.index and _safe_report_text(row.get(col)):
                return True
        return False

    def _manual_context_bits(row: pd.Series) -> list[str]:
        return [
            f"manual_status={_safe_report_text(row.get('manual_status')) or 'n/a'}",
            f"manual_minutes_limit={_safe_report_text(row.get('manual_minutes_limit')) or 'n/a'}",
            f"manual_projection_adjustment={_safe_report_text(row.get('manual_projection_adjustment')) or 'n/a'}",
            f"manual_confidence_adjustment={_safe_report_text(row.get('manual_confidence_adjustment')) or 'n/a'}",
            f"manual_context_reason={_safe_report_text(row.get('manual_context_reason')) or 'n/a'}",
            f"manual_context_applied={_safe_report_text(row.get('manual_context_applied')) or 'False'}",
        ]

    def _append_section(title: str, df: pd.DataFrame, limit: int = 10) -> None:
        lines.append(title)
        if df.empty:
            lines.append("No rows.")
            lines.append("")
            return
        for _, row in _top_rows(df, limit=limit).iterrows():
            label = (
                row.get("bet_label")
                or row.get("sgp_label")
                or row.get("entity_name")
                or row.get("game")
                or "Unknown"
            )
            if "sgp_label" in row or "combined_hit_probability" in row:
                bits = [
                    f"hit_prob={row.get('combined_hit_probability', '')}",
                    f"combo_conf={row.get('combined_confidence', '')}",
                    f"legs={row.get('leg_count', '')}",
                ]
                if "estimated_american_odds" in row and pd.notna(row.get("estimated_american_odds")):
                    bits.append(f"est_odds={row.get('estimated_american_odds', '')}")
            else:
                bits = [
                    f"pred={row.get('model_projection', '')}",
                    f"edge={row.get('edge', '')}",
                    f"conf={row.get('confidence', '')}",
                ]
                if "sportsbook_line" in row and pd.notna(row.get("sportsbook_line")):
                    bits.insert(1, f"line={row.get('sportsbook_line', '')}")
                if "reason" in row and str(row.get("reason", "")):
                    bits.append(f"reason={row.get('reason', '')}")
            if title == "Elite Board" and _has_manual_context(row):
                bits.extend(_manual_context_bits(row))
            lines.append(f"{label} | " + " | ".join(bits))
        lines.append("")

    _append_section("Elite Board", elite_df, limit=20)
    _append_section("Full Market Board", full_market_df, limit=40)
    _append_section("SGP Board", sgp_df, limit=20)
    if verbose_sections:
        _append_section("All Stats Projection Board", all_stats_df, limit=60)
        _append_section("Strike Board", strike_df, limit=25)
        _append_section("Predictive Lines Board", predictive_lines_df, limit=20)
        _append_section("Team Board", team_board_df, limit=40)
        _append_section("Near Miss Board", near_miss_df, limit=30)

    lines.append("Grading Summary")
    if grading_df.empty:
        lines.append("No graded rows.")
    else:
        win_count = int(pd.to_numeric(grading_df.get("is_win", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        push_count = int(pd.to_numeric(grading_df.get("is_push", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        loss_count = int(pd.to_numeric(grading_df.get("is_loss", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum())
        lines.append(f"graded_rows={len(grading_df)} | wins={win_count} | pushes={push_count} | losses={loss_count}")
    lines.append("")

    lines.append("Grade Distribution")
    if elite_df.empty or "letter_grade" not in elite_df.columns:
        lines.append("No visible grading available.")
    else:
        grade_counts = elite_df["letter_grade"].astype(str).value_counts()
        for grade, count in grade_counts.items():
            lines.append(f"{grade}: {count}")

    return "\n".join(lines)


def _build_elite_decision_report_text(prediction_date: str, elite_df: pd.DataFrame) -> str:
    lines = [f"Elite Decision Report - {prediction_date}", ""]
    if elite_df.empty:
        lines.append("No elite picks.")
        return "\n".join(lines) + "\n"

    def _safe(value: Any) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        return "" if text.lower() in {"nan", "none", "null"} else text

    def _has_context(row: pd.Series) -> bool:
        return any(
            _safe(row.get(col))
            for col in [
                "manual_status",
                "manual_minutes_limit",
                "manual_projection_adjustment",
                "manual_confidence_adjustment",
                "manual_context_reason",
            ]
        )

    rows_with_context = 0
    for _, row in elite_df.iterrows():
        player = _safe(row.get("player_name")) or _safe(row.get("entity_name")) or "Unknown"
        market = _safe(row.get("market_type")) or "unknown"
        selection = _safe(row.get("selection")) or "n/a"
        line = _safe(row.get("sportsbook_line")) or _safe(row.get("line")) or "n/a"
        lines.append(f"{player} | market={market} | selection={selection} | line={line}")
        if _has_context(row):
            rows_with_context += 1
            lines.append(f"  manual_status={_safe(row.get('manual_status')) or 'n/a'}")
            lines.append(f"  manual_minutes_limit={_safe(row.get('manual_minutes_limit')) or 'n/a'}")
            lines.append(f"  manual_projection_adjustment={_safe(row.get('manual_projection_adjustment')) or 'n/a'}")
            lines.append(f"  manual_confidence_adjustment={_safe(row.get('manual_confidence_adjustment')) or 'n/a'}")
            lines.append(f"  manual_context_reason={_safe(row.get('manual_context_reason')) or 'n/a'}")
            lines.append(f"  manual_context_applied={_safe(row.get('manual_context_applied')) or 'False'}")
        lines.append("")

    lines.append(f"elite_picks_with_manual_context={rows_with_context}")
    lines.append("manual_context_mode=passive_diagnostic_only")
    return "\n".join(lines) + "\n"


def _write_cli_outputs(
    out_dir: Path,
    prediction_date: str,
    fit_metrics: Optional[dict[str, Any]],
    prediction_outputs: dict[str, Any],
    verbose_outputs: bool = False,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_policy = BoardAuditPolicy()
    selected_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("selected_props", pd.DataFrame())))
    elite_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("elite_props", selected_df)))
    qualified_pool_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("qualified_pool_props", elite_df)))
    full_market_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("full_market_props", pd.DataFrame())))
    stat_only_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("stat_only_props", pd.DataFrame())))
    all_stats_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("all_stats_props", stat_only_df)))
    team_board_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("team_board_props", pd.DataFrame())))
    strike_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("strike_props", prediction_outputs.get("high_upside_props", pd.DataFrame()))))
    predictive_lines_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("predictive_lines_props", prediction_outputs.get("premarket_line_board", pd.DataFrame()))))
    sgp_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("sgp_props", prediction_outputs.get("sgp_board", pd.DataFrame()))))
    grading_df = _cli_dataframe(prediction_outputs.get("grading_results", pd.DataFrame()))
    grading_bucket_summary = prediction_outputs.get("grading_bucket_summary")
    near_miss_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("near_miss_props", pd.DataFrame())))
    rejected_df = audit_policy.apply_dataframe_audit(_cli_dataframe(prediction_outputs.get("rejected_props", pd.DataFrame())))
    summary = dict(prediction_outputs.get("summary", {}))
    board_diagnostics = prediction_outputs.get("board_diagnostics")
    final_board_construction = prediction_outputs.get("final_board_construction") or summary.get("final_board_construction")
    player_points_elite_admission_df = _extract_player_points_elite_admission_df(
        prediction_outputs,
        final_board_construction,
    )
    if not isinstance(board_diagnostics, dict):
        board_diagnostics = audit_policy.build_diagnostics(
            prediction_date=prediction_date,
            qualified_pool_df=qualified_pool_df,
            elite_df=elite_df,
            full_market_df=full_market_df,
            rejected_df=rejected_df,
            final_board_construction=final_board_construction if isinstance(final_board_construction, Mapping) else None,
            player_points_elite_admission_df=player_points_elite_admission_df,
        )
    summary["final_board_construction"] = board_diagnostics.get("final_board_construction", {})
    qualified_pool_df = audit_policy._annotate_player_points_elite_outcome(
        qualified_pool_df,
        elite_df=elite_df,
        full_market_df=full_market_df,
    )
    elite_df = audit_policy._annotate_player_points_elite_outcome(
        elite_df,
        elite_df=elite_df,
        full_market_df=full_market_df,
    )
    full_market_df = audit_policy._annotate_player_points_elite_outcome(
        full_market_df,
        elite_df=elite_df,
        full_market_df=full_market_df,
    )
    diagnostics_df = audit_policy.diagnostics_dataframe(board_diagnostics)

    combined_df = pd.concat([elite_df, rejected_df], ignore_index=True) if not elite_df.empty or not rejected_df.empty else pd.DataFrame()
    player_df = combined_df[combined_df["market_type"].astype(str).str.startswith("player_")].copy() if not combined_df.empty and "market_type" in combined_df.columns else pd.DataFrame()
    game_df = combined_df[combined_df["market_type"].isin(["team_total", "moneyline"])].copy() if not combined_df.empty and "market_type" in combined_df.columns else pd.DataFrame()
    player_edges_df = elite_df[elite_df["market_type"].astype(str).str.startswith("player_")].copy() if not elite_df.empty and "market_type" in elite_df.columns else pd.DataFrame()
    game_edges_df = elite_df[elite_df["market_type"].isin(["team_total", "moneyline"])].copy() if not elite_df.empty and "market_type" in elite_df.columns else pd.DataFrame()

    output_layout = OutputLayoutPolicy(
        out_dir / "runtime",
        OutputLayoutConfig(verbose_outputs=verbose_outputs),
    )
    paths = output_layout.prediction_paths(prediction_date)

    _write_dataframe(paths["player_predictions"], player_df)
    _write_dataframe(paths["game_predictions"], game_df)
    _write_dataframe(paths["player_edges"], player_edges_df)
    _write_dataframe(paths["game_edges"], game_edges_df)
    
    # [FINAL_ELITE_WRITER] runtime marker - actual final writer path
    elite_count = len(elite_df)
    game_id_col = elite_df.get("game_id", pd.Series(dtype="int"))
    market_type_col = elite_df.get("market_type", pd.Series(dtype="object"))
    
    # Calculate game exposure
    game_counts = game_id_col.value_counts().to_dict() if not game_id_col.empty else {}
    max_game_exposure = max(game_counts.values()) if game_counts else 0
    
    # Calculate market distribution
    market_counts = market_type_col.value_counts().to_dict() if not market_type_col.empty else {}
    
    print(f"[FINAL_ELITE_WRITER] function=courtvision_ai.py:prediction_pipeline rows={elite_count} max_game_exposure={max_game_exposure} cap=4")
    print(f"[FINAL_ELITE_MARKETS] {market_counts}")
    print(f"[FINAL_ELITE_GAMES] {game_counts}")
    
    # Hard validation: game cap must be enforced
    if max_game_exposure > 4:
        raise RuntimeError(
            f"[CAP_VIOLATION] Game cap violated in final elite write: "
            f"max_game_exposure={max_game_exposure} > cap=4. "
            f"Game distribution: {game_counts}"
        )
    
    # Persist market coverage diagnostics
    coverage_path = out_dir / "runtime" / "diagnostics" / f"market_coverage_{prediction_date}.json"
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_data = {
        "prediction_date": prediction_date,
        "elite_count": elite_count,
        "max_game_exposure": max_game_exposure,
        "game_cap": 4,
        "market_distribution": market_counts,
        "game_distribution": game_counts
    }
    with open(coverage_path, "w", encoding="utf-8") as f:
        json.dump(coverage_data, f, indent=2)
    print(f"[MARKET_COVERAGE] persisted to {coverage_path}")
    
    _write_dataframe(paths["elite_board"], elite_df)
    _write_dataframe(paths["full_market_board"], full_market_df)
    _write_dataframe(paths["sgp_board"], sgp_df)
    _write_dataframe(paths["player_points_elite_admission_csv"], player_points_elite_admission_df)
    paths["player_points_elite_admission_json"].write_text(
        json.dumps(
            {
                "prediction_date": prediction_date,
                "rows": player_points_elite_admission_df.to_dict(orient="records"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if "top_player_edges" in paths:
        _write_dataframe(paths["top_player_edges"], _top_rows(player_edges_df))
    if "top_game_edges" in paths:
        _write_dataframe(paths["top_game_edges"], _top_rows(game_edges_df))
    if "stat_only_board" in paths:
        _write_dataframe(paths["stat_only_board"], all_stats_df)
    if "strike_board" in paths:
        _write_dataframe(paths["strike_board"], strike_df)
    if "predictive_lines_board" in paths:
        _write_dataframe(paths["predictive_lines_board"], predictive_lines_df)
    if "team_board" in paths:
        _write_dataframe(paths["team_board"], team_board_df)
    if "near_miss_board" in paths:
        _write_dataframe(paths["near_miss_board"], near_miss_df)
    if "board_diagnostics_csv" in paths:
        _write_dataframe(paths["board_diagnostics_csv"], diagnostics_df)
    paths.update(
        _write_grading_outputs(
            out_dir=out_dir,
            prediction_date=prediction_date,
            grading_df=grading_df,
            grading_bucket_summary=grading_bucket_summary,
            elite_df=elite_df,
            verbose_outputs=verbose_outputs,
        )
    )

    metrics_payload = {
        "fit_metrics": fit_metrics or {},
        "prediction_summary": summary,
    }
    paths["model_metrics"].write_text(json.dumps(metrics_payload, indent=2), encoding="utf-8")
    paths["board_diagnostics_json"].write_text(json.dumps(board_diagnostics, indent=2), encoding="utf-8")
    paths["elite_decision_report"].write_text(
        _build_elite_decision_report_text(
            prediction_date=prediction_date,
            elite_df=elite_df,
        ),
        encoding="utf-8",
    )
    paths["top_plays_report"].write_text(
        _build_report_text(
            prediction_date=prediction_date,
            fit_metrics=fit_metrics,
            summary=summary,
            elite_df=elite_df,
            full_market_df=full_market_df,
            all_stats_df=all_stats_df,
            team_board_df=team_board_df,
            strike_df=strike_df,
            predictive_lines_df=predictive_lines_df,
            sgp_df=sgp_df,
            grading_df=grading_df,
            near_miss_df=near_miss_df,
            board_diagnostics=board_diagnostics,
            verbose_sections=verbose_outputs,
        ),
        encoding="utf-8",
    )

    return paths


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run CourtVision AI model fitting and/or prediction from the command line.",
    )
    parser.add_argument("--prediction-date", help="Prediction date in YYYY-MM-DD format.")
    parser.add_argument("--train-start", help="Training start date in YYYY-MM-DD format.")
    parser.add_argument("--train-end", help="Training end date in YYYY-MM-DD format.")
    parser.add_argument("--out-dir", default="outputs", help="Output directory. Defaults to ./outputs")
    parser.add_argument("--fit-only", action="store_true", help="Only fit the model baselines.")
    parser.add_argument("--predict-only", action="store_true", help="Only run predictions using existing baselines.")
    parser.add_argument("--grade-date", help="Auto-grade predictions for a completed date in YYYY-MM-DD format.")
    parser.add_argument("--send-telegram", action="store_true", help="Send Telegram alert for top qualified plays after prediction.")
    parser.add_argument("--verbose-outputs", action="store_true", help="Write optional/debug boards and CSV mirrors in addition to the default daily package.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    print("[STAGE] main_start")
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.fit_only and args.predict_only:
        parser.error("Choose only one of --fit-only or --predict-only.")

    run_fit = args.fit_only or (not args.predict_only and bool(args.train_start and args.train_end))
    run_predict = args.predict_only or (not args.fit_only and bool(args.prediction_date))
    run_grade = bool(args.grade_date)

    if run_fit and (not args.train_start or not args.train_end):
        parser.error("Training requires both --train-start and --train-end.")

    if run_predict and not args.prediction_date:
        parser.error("Prediction requires --prediction-date.")

    if args.send_telegram and not run_predict:
        parser.error("--send-telegram requires --prediction-date or --predict-only.")

    if not run_fit and not run_predict and not run_grade:
        parser.error("Nothing to do. Provide training dates, a prediction date, a grade date, or an explicit mode flag.")

    ai: Optional[CourtVisionAI] = None
    fit_metrics: Optional[dict[str, Any]] = None

    try:
        print("[STAGE] config_load_start")
        _load_env_file()
        request_timeout = int(os.getenv("BALLDONTLIE_REQUEST_TIMEOUT", "30") or "30")
        print("[STAGE] config_load_complete")
        api_key, api_key_details = resolve_api_key(
            entrypoint="courtvision_ai.py",
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
        )
        print(
            f"[auth] env_var={api_key_details.get('env_var_name', BALLDONTLIE_API_KEY_ENV_VAR)} "
            f"source={api_key_details.get('source', 'unknown')} "
            f"key={api_key_details.get('masked_preview', '<empty>')}"
        )
        smoke_result = smoke_test_games_api(
            api_key,
            entrypoint="courtvision_ai.py",
            timeout=request_timeout,
            env_var_name=BALLDONTLIE_API_KEY_ENV_VAR,
        )
        print(
            f"[balldontlie_smoke] status={smoke_result.get('status_code')} "
            f"url={smoke_result.get('resolved_url')} "
            f"has_auth={smoke_result.get('has_auth')} "
            f"key={smoke_result.get('masked_key_preview')} "
            f"body={smoke_result.get('body_snippet')}"
        )

        print("[STAGE] courtvision_ai_init_complete")
        ai = CourtVisionAI(out_dir=args.out_dir)
        print("[STAGE] courtvision_ai_ready")
        if run_fit:
            print(f"[fit] Training model from {args.train_start} to {args.train_end} ...")
            fit_metrics = ai.fit(args.train_start, args.train_end)
            print(json.dumps(fit_metrics, indent=2))

        if run_predict:
            print(f"[STAGE] prediction_pipeline_start date={args.prediction_date}")
            print(f"[predict] Running predictions for {args.prediction_date} ...")
            prediction_outputs = ai.predict(args.prediction_date)
            print("[STAGE] prediction_pipeline_complete")
            summary = dict(prediction_outputs.get("summary", {}))
            print("[STAGE] artifact_write_start")
            output_paths = _write_cli_outputs(
                out_dir=Path(args.out_dir),
                prediction_date=args.prediction_date,
                fit_metrics=fit_metrics,
                prediction_outputs=prediction_outputs,
                verbose_outputs=args.verbose_outputs,
            )
            print("[STAGE] artifact_write_complete")

            if args.send_telegram:
                sent = ai.send_telegram_top_plays(
                    prediction_date=args.prediction_date,
                    selected_df=_cli_dataframe(prediction_outputs.get("selected_props", pd.DataFrame())),
                    summary=summary,
                )
                print(f"[telegram] {'sent' if sent else 'skipped_or_failed'}")

            print("[summary]")
            print(json.dumps(summary, indent=2))
            print("[files]")
            for label, path in output_paths.items():
                print(f"{label}: {path}")

        if run_grade:
            print(f"[grade] Auto-grading predictions for {args.grade_date} ...")
            graded_df = ai.auto_grade(args.grade_date)
            grading_bucket_summary = summarize_graded_props(graded_df.to_dict("records")) if not graded_df.empty else summarize_graded_props([])
            grading_output_paths = _write_grading_outputs(
                out_dir=Path(args.out_dir),
                prediction_date=args.grade_date,
                grading_df=graded_df,
                grading_bucket_summary=grading_bucket_summary,
                elite_df=pd.DataFrame(),
                verbose_outputs=args.verbose_outputs,
            )
            print(f"[grade_summary] graded_rows={len(graded_df)}")
            if not graded_df.empty:
                hit_rate = pd.to_numeric(graded_df["hit"], errors="coerce").mean()
                mae = (
                    pd.to_numeric(graded_df["model_projection"], errors="coerce")
                    - pd.to_numeric(graded_df["actual_value"], errors="coerce")
                ).abs().mean()
                print(json.dumps({
                    "graded_rows": int(len(graded_df)),
                    "hit_rate": round(float(hit_rate), 4) if pd.notna(hit_rate) else None,
                    "mae": round(float(mae), 4) if pd.notna(mae) else None,
                }, indent=2))
            print("[grade_files]")
            for label, path in grading_output_paths.items():
                print(f"{label}: {path}")

    except Exception as exc:
        if ai is not None:
            ai.logger.exception("cli_failure error=%s", exc)
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
