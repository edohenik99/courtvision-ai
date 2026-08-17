"""Explicit, unscheduled prospective MLB context acquisition CLI."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from courtvision.sports.mlb.data.prospective_context_acquisition import (  # noqa: E402
    AcquisitionPolicy,
    DEFAULT_ACQUISITION_ROOT,
    EvidenceRequest,
    HttpEvidenceProvider,
    ProspectiveAcquisitionError,
    ProviderResponse,
    acquire_daily_history,
    acquire_event_cluster,
    build_daily_history_request_plan,
    build_event_clusters,
    build_mlb_game_feed_requests,
    classify_cluster_time,
    parse_mlb_schedule,
    utc_text,
)
from courtvision.sports.mlb.data.prospective_statcast_history import (  # noqa: E402
    persist_request_accounting_manifest,
)


class _ExplicitlyUnavailable(RuntimeError):
    pass


class _ControlledProvider:
    def __init__(
        self,
        provider: HttpEvidenceProvider,
        *,
        prefetched: dict[str, ProviderResponse] | None = None,
        fetch_statcast_history: bool,
    ) -> None:
        self._provider = provider
        self._prefetched = dict(prefetched or {})
        self._fetch_statcast_history = fetch_statcast_history
        self.network_requests: list[dict[str, object]] = []

    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        cached = self._prefetched.pop(request.request_id, None)
        if cached is not None:
            return cached
        started = datetime.now(timezone.utc)
        if request.source_name == "statcast_pitch_history" and not self._fetch_statcast_history:
            completed = datetime.now(timezone.utc)
            self.network_requests.append(
                {
                    "request_id": request.request_id,
                    "endpoint_class": request.source_name,
                    "provider": request.provider,
                    "url": request.url,
                    "started_at_utc": utc_text(started),
                    "completed_at_utc": utc_text(completed),
                    "status": "skipped",
                    "reason": "Statcast history fetch requires --fetch-statcast-history",
                    "admissibility": "non_evidentiary_control",
                }
            )
            raise _ExplicitlyUnavailable(
                "Statcast history fetch requires --fetch-statcast-history"
            )
        try:
            response = self._provider.fetch(request)
        except Exception as exc:
            self.network_requests.append(
                {
                    "request_id": request.request_id,
                    "endpoint_class": request.source_name,
                    "provider": request.provider,
                    "url": request.url,
                    "started_at_utc": utc_text(started),
                    "completed_at_utc": utc_text(datetime.now(timezone.utc)),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "reason": "provider request failed before admissible evidence was preserved",
                    "admissibility": "non_evidentiary_control",
                }
            )
            raise
        self.network_requests.append(
            {
                "request_id": request.request_id,
                "endpoint_class": request.source_name,
                "provider": request.provider,
                "url": request.url,
                "started_at_utc": utc_text(started),
                "completed_at_utc": utc_text(response.captured_at_utc),
                "status": "completed",
                "status_code": response.status_code,
                "sha256": __import__("hashlib").sha256(response.body).hexdigest(),
                "byte_size": len(response.body),
                "reason": "response may contribute to acquisition or capture decision",
                "admissibility": "pending_acquisition_binding",
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-only MLB pregame context acquisition"
    )
    parser.add_argument("--operating-date", required=True)
    parser.add_argument("--event-id", action="append", required=True)
    parser.add_argument("--season-start-date", default="2026-03-25")
    parser.add_argument("--target-lead-minutes", type=int, default=60)
    parser.add_argument("--minimum-lead-minutes", type=int, default=45)
    parser.add_argument("--maximum-lead-minutes", type=int, default=90)
    parser.add_argument("--cluster-window-minutes", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--fetch-statcast-history", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument(
        "--acquisition-root", type=Path, default=DEFAULT_ACQUISITION_ROOT
    )
    parser.add_argument("--git-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_network:
        raise PermissionError("live acquisition requires --allow-network")
    operating_date = date.fromisoformat(args.operating_date)
    season_start = date.fromisoformat(args.season_start_date)
    policy = AcquisitionPolicy(
        target_lead_minutes=args.target_lead_minutes,
        minimum_lead_minutes=args.minimum_lead_minutes,
        maximum_lead_minutes=args.maximum_lead_minutes,
        cluster_window_minutes=args.cluster_window_minutes,
    )
    schedule_request = EvidenceRequest(
        request_id=f"statsapi-schedule-{operating_date.isoformat()}",
        evidence_class="stable_history",
        source_name="mlb_schedule",
        provider="mlb_statsapi",
        url=(
            "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="
            + operating_date.isoformat()
            + "&hydrate=probablePitcher,venue"
        ),
    )
    http_provider = HttpEvidenceProvider(timeout_seconds=args.timeout_seconds)
    discovery_provider = _ControlledProvider(
        http_provider,
        fetch_statcast_history=args.fetch_statcast_history,
    )
    commit = args.git_commit or _git_commit()
    provider: _ControlledProvider | None = None
    accounting_path: Path | None = None
    try:
        schedule_response = discovery_provider.fetch(schedule_request)
        events = parse_mlb_schedule(schedule_response.body, operating_date=operating_date)
        clusters = build_event_clusters(events, policy=policy)
        selected_ids = set(args.event_id)
        matches = [
            cluster
            for cluster in clusters
            if selected_ids == {event.event_id for event in cluster.events}
        ]
        if len(matches) != 1:
            raise ProspectiveAcquisitionError(
                "--event-id values must exactly match one deterministic cluster"
            )
        cluster = matches[0]
        now = datetime.now(timezone.utc)
        timing = classify_cluster_time(cluster, now)
        if timing != "admissible":
            raise ProspectiveAcquisitionError(
                "selected cluster is not inside its shared admissible window: " + timing
            )
        history_plan = build_daily_history_request_plan(
            events,
            operating_date=operating_date,
            season_start_date=season_start,
        )
        provider = _ControlledProvider(
            http_provider,
            prefetched={schedule_request.request_id: schedule_response},
            fetch_statcast_history=args.fetch_statcast_history,
        )
        provider.network_requests.extend(discovery_provider.network_requests)
        history = acquire_daily_history(
            events,
            requested_as_of_utc=cluster.window_closes_at_utc,
            requests=history_plan,
            provider=provider,
            acquisition_root=args.acquisition_root,
            git_commit=commit,
        )
        observed = datetime.now(timezone.utc)
        volatile = acquire_event_cluster(
            cluster,
            requested_as_of_utc=cluster.window_closes_at_utc,
            observed_at_utc=observed,
            requests=build_mlb_game_feed_requests(cluster),
            provider=provider,
            acquisition_root=args.acquisition_root,
            git_commit=commit,
            daily_history_reference=history.reference(),
        )
        accounting_path = persist_request_accounting_manifest(
            operating_date=operating_date,
            records=provider.network_requests,
            outcome="completed",
            reason="requests were bound to daily-history and volatile acquisition manifests",
            acquisition_root=args.acquisition_root,
            git_commit=commit,
            evidence_manifest_paths=(history.manifest_path, volatile.manifest_path),
        )
    except Exception as exc:
        records = (
            provider.network_requests
            if provider is not None
            else discovery_provider.network_requests
        )
        if records:
            persist_request_accounting_manifest(
                operating_date=operating_date,
                records=records,
                outcome="stopped_before_evidence_materialization",
                reason=f"{type(exc).__name__}: {exc}",
                acquisition_root=args.acquisition_root,
                git_commit=commit,
            )
        raise
    print(
        json.dumps(
            {
                "cluster": {
                    "cluster_id": cluster.cluster_id,
                    "event_ids": [event.event_id for event in cluster.events],
                    "window_opens_at_utc": utc_text(cluster.window_opens_at_utc),
                    "target_at_utc": utc_text(cluster.target_at_utc),
                    "window_closes_at_utc": utc_text(cluster.window_closes_at_utc),
                    "observed_at_utc": utc_text(observed),
                },
                "daily_history": {
                    "history_snapshot_id": history.history_snapshot_id,
                    "snapshot_state": history.snapshot_state,
                    "manifest_path": str(history.manifest_path),
                    "no_op": history.no_op,
                },
                "volatile_capture": {
                    "capture_id": volatile.capture_id,
                    "capture_state": volatile.capture_state,
                    "manifest_path": str(volatile.manifest_path),
                    "no_op": volatile.no_op,
                },
                "network_requests": provider.network_requests,
                "request_accounting_manifest": str(accounting_path),
                "model_training_performed": False,
                "prediction_or_operational_publication_performed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
