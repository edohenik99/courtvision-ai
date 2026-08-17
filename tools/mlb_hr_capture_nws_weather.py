"""Capture NWS hourly forecasts for one persisted admissible event cluster."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.prospective_context_acquisition import (  # noqa: E402
    DEFAULT_ACQUISITION_ROOT,
    EventCluster,
    EvidenceRequest,
    HttpEvidenceProvider,
    ProspectiveAcquisitionError,
    ProviderResponse,
    ScheduledEvent,
    acquire_event_cluster,
    parse_utc,
    utc_text,
)


class _CachedProvider:
    def __init__(
        self,
        provider: HttpEvidenceProvider,
        cached: Mapping[str, ProviderResponse],
    ) -> None:
        self.provider = provider
        self.cached = dict(cached)
        self.network_requests: list[dict[str, object]] = []

    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        cached = self.cached.pop(request.request_id, None)
        if cached is not None:
            return cached
        started = datetime.now(timezone.utc)
        response = self.provider.fetch(request)
        self.network_requests.append(
            {
                "request_id": request.request_id,
                "url": request.url,
                "started_at_utc": utc_text(started),
                "captured_at_utc": utc_text(response.captured_at_utc),
                "status_code": response.status_code,
                "sha256": hashlib.sha256(response.body).hexdigest(),
                "byte_size": len(response.body),
            }
        )
        return response


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("capture_state") != "completed":
        raise ProspectiveAcquisitionError("base volatile capture is not complete")
    return payload


def _events(payload: Mapping[str, object]) -> tuple[ScheduledEvent, ...]:
    events = []
    for item in payload.get("events") or []:
        if not isinstance(item, Mapping):
            raise ProspectiveAcquisitionError("base capture event identity is invalid")
        events.append(
            ScheduledEvent(
                event_id=str(item.get("event_id") or ""),
                operating_date=date.fromisoformat(str(item.get("operating_date") or "")),
                scheduled_start_utc=parse_utc(
                    item.get("scheduled_start_utc", ""), "scheduled_start_utc"
                ),
                away_team_id=str(item.get("away_team_id") or ""),
                away_team=str(item.get("away_team") or ""),
                home_team_id=str(item.get("home_team_id") or ""),
                home_team=str(item.get("home_team") or ""),
                venue_id=str(item.get("venue_id") or ""),
                venue_name=str(item.get("venue_name") or ""),
                status=str(item.get("status") or "unknown"),
                away_probable_pitcher_id=(
                    str(item["away_probable_pitcher_id"])
                    if item.get("away_probable_pitcher_id")
                    else None
                ),
                home_probable_pitcher_id=(
                    str(item["home_probable_pitcher_id"])
                    if item.get("home_probable_pitcher_id")
                    else None
                ),
            )
        )
    return tuple(events)


def _source(payload: Mapping[str, object], request_id: str) -> Mapping[str, object]:
    matches = [
        item
        for item in payload.get("sources") or []
        if isinstance(item, Mapping) and item.get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise ProspectiveAcquisitionError(f"missing base feed: {request_id}")
    return matches[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture NWS pregame hourly forecasts")
    parser.add_argument("--base-volatile-manifest", type=Path, required=True)
    parser.add_argument("--acquisition-root", type=Path, default=DEFAULT_ACQUISITION_ROOT)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--git-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network:
        raise PermissionError("NWS capture requires --allow-network")
    manifest_path = args.base_volatile_manifest.expanduser().resolve()
    base = _load_manifest(manifest_path)
    events = _events(base)
    cluster_window = base.get("cluster_window")
    if not isinstance(cluster_window, Mapping):
        raise ProspectiveAcquisitionError("base capture lacks cluster window")
    cluster = EventCluster(
        cluster_id=str(base.get("cluster_id") or ""),
        operating_date=events[0].operating_date,
        events=events,
        window_opens_at_utc=parse_utc(
            cluster_window.get("window_opens_at_utc", ""), "window_opens_at_utc"
        ),
        target_at_utc=parse_utc(
            cluster_window.get("target_at_utc", ""), "target_at_utc"
        ),
        window_closes_at_utc=parse_utc(
            cluster_window.get("window_closes_at_utc", ""), "window_closes_at_utc"
        ),
    )
    http = HttpEvidenceProvider(timeout_seconds=args.timeout_seconds)
    cached: dict[str, ProviderResponse] = {}
    requests: list[EvidenceRequest] = []
    network: list[dict[str, object]] = []
    observed_at = datetime.now(timezone.utc)
    for event in events:
        feed_record = _source(base, f"statsapi-feed-{event.event_id}")
        feed_path = (manifest_path.parent / str(feed_record.get("body_path") or "")).resolve()
        feed = json.loads(feed_path.read_text(encoding="utf-8-sig"))
        coordinates = feed["gameData"]["venue"]["location"]["defaultCoordinates"]
        points_request = EvidenceRequest(
            request_id=f"nws-points-{event.event_id}",
            evidence_class="volatile_pregame",
            source_name="nws_points",
            provider="weather_gov",
            url=(
                "https://api.weather.gov/points/"
                f"{coordinates['latitude']},{coordinates['longitude']}"
            ),
            event_id=event.event_id,
        )
        started = datetime.now(timezone.utc)
        points_response = http.fetch(points_request)
        network.append(
            {
                "request_id": points_request.request_id,
                "url": points_request.url,
                "started_at_utc": utc_text(started),
                "captured_at_utc": utc_text(points_response.captured_at_utc),
                "status_code": points_response.status_code,
                "sha256": hashlib.sha256(points_response.body).hexdigest(),
                "byte_size": len(points_response.body),
            }
        )
        cached[points_request.request_id] = points_response
        points = json.loads(points_response.body.decode("utf-8-sig"))
        forecast_url = str(points["properties"]["forecastHourly"])
        if not forecast_url.startswith("https://api.weather.gov/"):
            raise ProspectiveAcquisitionError("NWS points returned an unsafe forecast URL")
        requests.extend(
            (
                points_request,
                EvidenceRequest(
                    request_id=f"nws-hourly-{event.event_id}",
                    evidence_class="volatile_pregame",
                    source_name="nws_hourly_forecast",
                    provider="weather_gov",
                    url=forecast_url,
                    event_id=event.event_id,
                ),
            )
        )
    provider = _CachedProvider(http, cached)
    provider.network_requests.extend(network)
    result = acquire_event_cluster(
        cluster,
        requested_as_of_utc=cluster.window_closes_at_utc,
        observed_at_utc=observed_at,
        requests=tuple(requests),
        provider=provider,
        acquisition_root=args.acquisition_root,
        git_commit=args.git_commit or _git_commit(),
        daily_history_reference={
            "base_volatile_capture_id": base.get("capture_id"),
            "base_volatile_manifest": str(manifest_path),
        },
    )
    print(
        json.dumps(
            {
                "weather_capture_id": result.capture_id,
                "capture_state": result.capture_state,
                "manifest_path": str(result.manifest_path),
                "network_requests": provider.network_requests,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
