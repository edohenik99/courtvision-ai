"""Post-freeze LIVE-01 market observations; predictions are read-only inputs."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import csv
import io
from pathlib import Path

from courtvision.core.odds import american_to_decimal
from courtvision.sports.mlb.fact_backfill_evidence import (
    BackfillError, publish_bytes, publish_document, read_document, utc_now,
)
from courtvision.sports.mlb.live01_freeze import verify_freeze
from courtvision.sports.mlb.market_data import MLBMarketVariant, MLBPropSide
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name
from courtvision.sports.mlb.providers.the_odds_api_live import (
    MLBOddsIngestionConfig, build_request_plan, execute_ingestion,
)
from courtvision.sports.mlb.providers.the_odds_api_transport import RequestsOddsAPITransport


class _FrozenTransport(RequestsOddsAPITransport):
    def __init__(self, before_send):
        super().__init__(network_enabled=True)
        self.before_send = before_send

    def send(self, request, **kwargs):
        self.before_send()
        return super().send(request, **kwargs)


class _FrozenTestTransport:
    def __init__(self, before_send, delegate):
        self.before_send, self.delegate = before_send, delegate

    def send(self, request, **kwargs):
        self.before_send()
        return self.delegate.send(request, **kwargs)


def attach_quotes(rows: list[dict], sources: dict, batches, *, durable_at: datetime) -> tuple[list, list]:
    """Resolve quotes only against the already frozen event/player population."""
    attached = []
    for batch in batches:
        for quote in batch.records:
            if (quote.provider_market_key != "batter_hits"
                    or quote.market_variant is not MLBMarketVariant.MAIN
                    or quote.side is not MLBPropSide.OVER or quote.line != 0.5):
                continue
            possible = [row for row in rows if row["home_team_name"] == quote.home_team
                and row["away_team_name"] == quote.away_team
                and abs((datetime.fromisoformat(row["scheduled_start_utc"]) -
                         quote.commence_time).total_seconds()) <= 120]
            games = {row["mlbam_game_id"] for row in possible}
            if len(games) > 1:
                raise BackfillError("ambiguous market event identity")
            if not possible:
                continue
            roster = sources[possible[0]["prediction_id"]]["roster"]
            matches = [p for p in roster if normalize_mlb_player_name(p["player_name"]) ==
                       normalize_mlb_player_name(quote.participant_name)]
            if len(matches) > 1:
                raise BackfillError("ambiguous market player identity")
            if not matches:
                continue
            eligible = [row for row in possible if row["mlbam_player_id"] == matches[0]["mlbam_player_id"]]
            if not eligible:
                continue
            if len(eligible) != 1:
                raise BackfillError("duplicate frozen player identity")
            row = eligible[0]
            if not durable_at < quote.collected_at < datetime.fromisoformat(row["scheduled_start_utc"]):
                raise BackfillError("market observation is outside post-freeze pregame interval")
            decimal = american_to_decimal(quote.american_odds)
            implied = 1 / decimal
            attached.append({
                "prediction_id": row["prediction_id"],
                "prediction_payload_sha256": row["prediction_payload_sha256"],
                "mlbam_game_id": row["mlbam_game_id"], "mlbam_player_id": row["mlbam_player_id"],
                "provider": quote.provider, "provider_event_id": quote.provider_event_id,
                "bookmaker_key": quote.bookmaker_key, "bookmaker_name": quote.bookmaker_name,
                "provider_market_key": quote.provider_market_key, "side": quote.side.value,
                "point": quote.line, "american_odds": quote.american_odds,
                "decimal_odds": decimal, "market_implied_probability": implied,
                "edge": row["model_probability"] - implied,
                "market_updated_at": quote.market_updated_at.isoformat(),
                "captured_at": quote.collected_at.isoformat(), "source_refs": list(quote.source_refs)})
    attached.sort(key=lambda q: (q["prediction_id"], -q["decimal_odds"], q["bookmaker_key"],
                                 q["bookmaker_name"], q["captured_at"]))
    board = []
    for row in rows:
        quotes = [q for q in attached if q["prediction_id"] == row["prediction_id"]]
        primary = quotes[0] if quotes else None
        board.append({
            "player": row["player_name"], "game": row["away_team_name"] + " at " + row["home_team_name"],
            "batting_order_position": row["batting_order_position"], "season_hits": row["season_hits"],
            "season_at_bats": row["season_at_bats"], "distinct_batting_games": row["distinct_batting_games"],
            "projected_at_bats": row["projected_at_bats"], "model_probability": row["model_probability"],
            "sportsbook": primary["bookmaker_name"] if primary else None,
            "price": primary["american_odds"] if primary else None,
            "market_implied_probability": primary["market_implied_probability"] if primary else None,
            "edge": primary["edge"] if primary else None, "prediction_id": row["prediction_id"],
            "prediction_payload_sha256": row["prediction_payload_sha256"],
            "market_status": "OBSERVED" if primary else "MARKET_UNAVAILABLE",
            "research_only": True, "approval_status": "not_approved",
            "kelly_eligible": False, "eligible_for_betting": False,
            "outcome_status": "PENDING", "closing_status": "PENDING"})
    return attached, board


def capture_market(freeze_root: Path, *, config: MLBOddsIngestionConfig | None,
                   api_key: str | None, transport=None, clock=utc_now) -> dict:
    """There is no market entry point in LIVE-01 that accepts unfrozen model inputs."""
    freeze, rows = verify_freeze(freeze_root)
    sources = read_document(freeze_root / "sources.json")
    durable = datetime.fromisoformat(freeze["prediction_artifact_durable_at"])
    destination = freeze_root / "market"
    if (destination / "observations.json").exists():
        saved = read_document(destination / "observations.json")
        if saved["prediction_manifest_hash"] != freeze["manifest_hash"]:
            raise BackfillError("market artifact belongs to another frozen population")
        return saved
    calls, gate_errors = [], []

    def before_send():
        try:
            current, _ = verify_freeze(freeze_root)
            requested = clock()
            if current["manifest_hash"] != freeze["manifest_hash"] or requested <= durable:
                raise BackfillError("market call requires an earlier, unchanged durable freeze")
        except Exception as exc:
            gate_errors.append(exc)
            raise
        calls.append(requested)

    result = None
    state = "NO_ELIGIBLE_PREDICTIONS" if not rows else "MARKET_UNAVAILABLE"
    if rows and config is not None and api_key:
        if config.operating_date != date.fromisoformat(freeze["operating_date"]) or config.markets != ("batter_hits",):
            raise BackfillError("market plan differs from the frozen date/main Hits scope")
        # Bound scope before seeing discovery or prices; preserve the configured caps.
        events = sorted({(r["scheduled_start_utc"], r["mlbam_game_id"], r["home_team_name"],
                         r["away_team_name"]) for r in rows})
        selected = events[:config.maximum_events]
        targets = tuple((home, away, start) for start, _, home, away in selected)
        config = replace(config, target_event_keys=targets)
        plan = build_request_plan(config, captured_at=clock())
        if transport is None:
            gated = _FrozenTransport(before_send)
        else:
            if isinstance(transport, RequestsOddsAPITransport):
                raise BackfillError("live transport must retain the permit-aware freeze gate")
            gated = _FrozenTestTransport(before_send, transport)
        result = execute_ingestion(plan, api_key=api_key, transport=gated,
            output_root=destination / "provider", run_id=freeze["prediction_run_id"], clock=clock)
        if gate_errors:
            raise gate_errors[0]
        corrupt = {"INVALID_JSON", "INVALID_RESPONSE_SHAPE", "CONFLICTING_DUPLICATE",
                   "SECRET_REDACTED", "CLOCK_FAILURE", "ACCOUNTING_CONFLICT",
                   "EXECUTION_PERMIT_REQUIRED"}
        if corrupt.intersection(result.transport_failures):
            raise BackfillError("market provider evidence failed integrity validation")
        state = result.status
    current, _ = verify_freeze(freeze_root)
    if current["manifest_hash"] != freeze["manifest_hash"]:
        raise BackfillError("prediction changed during market observation")
    quotes, board = attach_quotes(rows, sources, result.market_batches if result else (), durable_at=durable)
    bound = len({q["prediction_id"] for q in quotes})
    document = {
        "schema_version": "cv_mlb_hits_live01_market_v1", "prediction_manifest_hash": freeze["manifest_hash"],
        "status": state, "prediction_artifact_durable_at": durable.isoformat(),
        "first_market_request_at": calls[0].isoformat() if calls else None,
        "odds_max_events": config.maximum_events if config else None,
        "odds_max_http_requests": config.maximum_http_requests if config else None,
        "odds_declared_credit_budget": config.maximum_provider_credits if config else None,
        "odds_provider_calls": len(calls),
        "odds_provider_credits_used": result.reported_credit_cost if result else None,
        "transport_failures": list(result.transport_failures) if result else [],
        "normalization_diagnostic_count": len(result.normalization_diagnostics) if result else 0,
        "market_quotes_captured": len(quotes), "frozen_rows_with_market": bound,
        "frozen_rows_without_market": len(rows) - bound,
        "quotes": quotes, "research_board": board, "research_only": True,
        "outcome_status": "PENDING", "closing_status": "PENDING"}
    publish_document(destination / "observations.json", document)
    if board:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(board[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(board)
        publish_bytes(destination / "research-board.csv", stream.getvalue().encode("utf-8"))
    verify_freeze(freeze_root)
    return document
