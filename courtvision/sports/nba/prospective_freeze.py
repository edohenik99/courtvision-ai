"""Market-free NBA model articles and a separate future market-assessment boundary.

Foundation v1 stores explicitly incomplete synthetic model state. It does not
produce minutes, points, distributions, line probabilities, picks, or live runs.
01D must supply a qualified frozen distribution and deterministic line evaluator.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path

from courtvision.sports.nba.artifact_domains import NBA_PROSPECTIVE_EVIDENCE, TARGET_OUTCOME_FIELDS
from courtvision.sports.nba.prospective_evidence import (
    ProspectiveEvidenceError, _PARAMETER_DESCRIPTORS, _safe_json, _semantic_key, canonical_bytes,
    digest, immutable, plain_path,
    read_document, require_date, require_hash, require_id, source_manifest, utc_clock,
    verify_capture, write_once,
)
from courtvision.sports.nba.player_points_research import toronto_operating_date

MODEL_SCHEMA = "nba-prospective-model-snapshot-v1"
FREEZE_SCHEMA = "nba-prospective-model-freeze-v1"
MEASUREMENT_SCHEMA = "nba-preseason-measurement-v1"
MARKET_SCHEMA = "nba-prospective-market-observation-v1"
ASSESSMENT_VERSION = "nba-frozen-model-market-binding-v1"
_PROHIBITED = frozenset("""sportsbook bookmaker vendor line observed_line market_line sportsbook_line line_value
    american_odds decimal_odds odds over_odds under_odds
    implied_probability market_timestamp_utc selected_side edge model_edge probability_based_edge
    closing_line closing_odds clv stake kelly bankroll result settlement actual_points actual_minutes
    final_points final_stats box_score points pts minutes model_over_probability model_under_probability""".split())
_PROBABILITY_MARKET_FIELDS = frozenset({"model_over_probability", "model_under_probability",
    "over_probability", "under_probability", "probability_over", "probability_under",
    "line_specific_probability", "implied_probability", "probability_based_edge"})
_POINTS_TOTAL_FIELDS = frozenset({"points_total", "total_points"})
_TOTAL_MARKET_FIELDS = (frozenset(f"{subject}_total" for subject in
    ("consensus", "book", "vegas", "sportsbook", "bookmaker", "market", "opening", "closing"))
    | {"over_under"})
_POINTS_TOTAL_PREFIXES = tuple(name.replace("_", "") for name in _POINTS_TOTAL_FIELDS)
_SCIENTIFIC_TOTAL_QUALIFIERS = ("historical", "projected")
_MARKET_FIELDS = (_PROHIBITED | _PROBABILITY_MARKET_FIELDS | _POINTS_TOTAL_FIELDS | _TOTAL_MARKET_FIELDS
    | {"points_line", "p_over", "p_under", "price", "consensus_price", "book_price"})
_MARKET_PRICE_PREFIXES = ("bookprice", "consensusprice", "pricevalue", "pricevalues",
    "priceamount", "pricequote", "pricequotes", "pricetimestamp")
_FIELD_DESCRIPTORS = ("value", "values", "context", "metadata", "data", "detail", "details",
    "observation", "observations", "timestamp", "utc",
    "quote", "quotes", "count", "probability", "probabilities", "estimate", "estimates",
    "odds", "price", "status", "threshold", "line", "amount", "amounts", "budget", "size",
    "sizing", "name", "names", "id", "ids", "source", "sources", "provenance") + _PARAMETER_DESCRIPTORS
_SHORT_FIELD_DESCRIPTORS = frozenset({"param", "params", "arg", "args"})
_FIELD_DESCRIPTOR_ORDER = tuple(sorted(_FIELD_DESCRIPTORS, key=len, reverse=True))
_FIELD_PREFIX_DESCRIPTORS = tuple(descriptor for descriptor in _FIELD_DESCRIPTORS
    if descriptor not in _SHORT_FIELD_DESCRIPTORS)
# `points_linear` is scientific data; the short `line` root needs a boundary.
_MARKET_COMPOUND_PREFIXES = (tuple(name.replace("_", "") for name in _MARKET_FIELDS
    if "_" in name and name not in {"points_line", "p_over", "p_under"})
    + tuple("pointsline" + descriptor for descriptor in _FIELD_PREFIX_DESCRIPTORS))
_MARKET_SINGLE_PREFIXES = tuple(root + descriptor for root in _MARKET_FIELDS
    if "_" not in root and root not in {"points", "pts", "minutes", "kelly"}
    for descriptor in _FIELD_PREFIX_DESCRIPTORS)
_MARKET_SHORT_DESCRIPTOR_STEMS = tuple(root + descriptor
    for root in (*tuple(root for root in _MARKET_FIELDS
        if "_" not in root and root not in {"points", "pts", "minutes", "kelly"}),
        "pointsline", "pover", "punder")
    for descriptor in _SHORT_FIELD_DESCRIPTORS)
_STAT_VALUE_PREFIXES = tuple(root + descriptor for root in _MARKET_FIELDS
    if root in {"points", "pts", "minutes"}
    for descriptor in ("value", "values", "data", "context", "metadata", "observation",
                       "observations", "count", "amount", "amounts"))
_MARKET_PROBABILITY_PREFIXES = (tuple(name.replace("_", "") for name in _PROBABILITY_MARKET_FIELDS)
    + tuple(stem + descriptor for stem in ("pover", "punder")
            for descriptor in _FIELD_PREFIX_DESCRIPTORS))
_STAT_OUTCOME_FIELDS = frozenset(f"{qualifier}_{stat}" for qualifier in ("actual", "final")
    for stat in ("points", "pts", "minutes", "stats", "score", "scores", "rebounds", "assists",
        "steals", "blocks", "turnovers", "fouls", "reb", "ast", "stl", "blk", "tov",
        "fgm", "fga", "fg3m", "fg3a", "ftm", "fta"))
_SCORED_OUTCOME_FIELDS = frozenset(name for stat in ("points", "pts")
    for name in (f"{stat}_scored", f"scored_{stat}"))
_SCORED_OUTCOME_PREFIXES = tuple(name.replace("_", "") for name in _SCORED_OUTCOME_FIELDS)
_SCIENTIFIC_SCORED_QUALIFIERS = ("historical", "season", "recent", "lastgame", "projected", "predicted")
_MODEL_OUTCOME_FIELDS = (TARGET_OUTCOME_FIELDS | _STAT_OUTCOME_FIELDS | _SCORED_OUTCOME_FIELDS
    | {"_".join(name.split("_")[1:] + name.split("_")[:1]) for name in _STAT_OUTCOME_FIELDS}
    | {"final_result", "final_results", "final_grade", "final_grading"})
_PROHIBITED_COMPACT = frozenset(name.replace("_", "") for name in _MARKET_FIELDS)
_OUTCOME_COMPACT = frozenset(name.replace("_", "") for name in _MODEL_OUTCOME_FIELDS)
_OUTCOME_COMPOUND_PREFIXES = tuple(name.replace("_", "") for name in _MODEL_OUTCOME_FIELDS
    if "_" in name)
_STAT_OUTCOME_PREFIXES = tuple(name.replace("_", "") for name in _STAT_OUTCOME_FIELDS
    | {"_".join(name.split("_")[1:] + name.split("_")[:1]) for name in _STAT_OUTCOME_FIELDS})
_OUTCOME_PREFIXES = ("actual", "targetgameactual", "targetgamefinal", "settlement",
    "grading", "result", "grade")
_MARKET_PREFIXES = ("sportsbook", "bookmaker", "kelly", "closing", "settlement", "market")
_OUTCOME_WORDS = frozenset({"actual", "settlement", "grading", "result", "results", "grade"})
_OUTCOME_PATTERNS = tuple(tuple(name.split("_")) for name in _MODEL_OUTCOME_FIELDS)
_MARKET_PATTERNS = tuple(tuple(name.split("_")) for name in _MARKET_FIELDS
    if name not in {"points", "pts", "minutes", "kelly"})
_SUBJECT_PREFIXES = ("targetgame", "player", "provider", "opponent", "event", "game", "team", "home", "away", "model",
    "sources", "source", "provenance")
_MARKET_SUBJECT_PREFIXES = _SUBJECT_PREFIXES + ("consensus", "observed", "selected")
_ECONOMIC_ROUTES = ("bet", "betting", "wager", "wagering", "kelly", "official_pick",
    "real_money", "real_kelly")
_ECONOMIC_ROUTE_COMPACT = frozenset(route.replace("_", "") for route in _ECONOMIC_ROUTES)
_ECONOMIC_GATES = ("eligible", "eligibility", "enabled", "allowed", "permitted", "authorized")
_ECONOMIC_FLAGS = (frozenset(route + gate for route in _ECONOMIC_ROUTE_COMPACT for gate in _ECONOMIC_GATES)
    | frozenset(gate + route for route in _ECONOMIC_ROUTE_COMPACT
                for gate in (*_ECONOMIC_GATES, "eligiblefor")) | {"bettable"})
_BOOLEAN_WRAPPERS = ("is", "can", "may", "has", "should", "will", "would")
_PERMISSION_ACTIONS = ("use", "place", "enable", "allow", "authorize", "permit")
# Short permission roots are token/exact-grammar matches: `beta` is scientific data.
_ECONOMIC_COMPOUNDS = tuple(route for route in _ECONOMIC_ROUTE_COMPACT if route != "bet")
_BET_DESCRIPTORS = ("amount", "budget", "stake", "size", "sizing", "permission", "approval",
    "enabled", "eligibility", "eligible", "allowed", "authorized", "permitted", "route")
_SHORT_BET_COMPOUNDS = frozenset(action + "bet" for action in _PERMISSION_ACTIONS)
_SHORT_BET_PREFIXES = tuple(prefix + "bet" + descriptor
    for prefix in ("", *_PERMISSION_ACTIONS) for descriptor in _BET_DESCRIPTORS) + ("bettable",)
_STATE_FIELDS = frozenset({"canonical_event_id", "provider_event_ids", "player_id",
    "canonical_player_name", "team", "opponent", "commence_time_utc", "model_id", "model_version",
    "minutes_evidence_ref", "projected_minutes", "minutes_uncertainty", "projection_evidence_ref",
    "projected_points", "projection_method", "projection_cutoff_utc", "projection_timestamp_utc",
    "distribution_evidence_ref", "distribution_model_id", "distribution_parameters", "projection_inputs"})
_ROW_FIELDS = _STATE_FIELDS | {"schema_version", "model_state_status", "prediction_run_id",
    "operating_date", "repository_commit_sha", "source_manifest_sha256", "measurement_metadata",
    "model_snapshot_id", "row_sha256"}
_MANIFEST_FIELDS = frozenset({"schema_version", "prediction_run_id", "operating_date",
    "repository_commit_sha", "measurement_metadata", "row_count", "source_manifest_sha256",
    "snapshot_file_sha256", "exclusion_file_sha256", "created_at_utc", "manifest_sha256"})
_FILES = {"manifest.json", "model_snapshots.jsonl", "sources.json", "exclusions.json", "freeze.json"}


@dataclass(frozen=True, slots=True)
class PreseasonMeasurement:
    operating_date: str
    prediction_run_id: str
    repository_commit_sha: str
    schema_version: str = MEASUREMENT_SCHEMA
    model_snapshot_schema_version: str = MODEL_SCHEMA
    measurement_class: str = "PRESEASON_REHEARSAL"
    season_phase: str = "PRESEASON"
    regular_season_model_evidence: bool = False
    research_only: bool = True
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    eligible_for_official_pick: bool = False
    target_market: str = "player_points"

    def __post_init__(self) -> None:
        require_date(self.operating_date)
        require_id(self.prediction_run_id)
        require_hash(self.repository_commit_sha, 40)
        policy = dict(schema_version=MEASUREMENT_SCHEMA, model_snapshot_schema_version=MODEL_SCHEMA,
            measurement_class="PRESEASON_REHEARSAL", season_phase="PRESEASON",
            regular_season_model_evidence=False, research_only=True, eligible_for_betting=False,
            kelly_eligible=False, eligible_for_official_pick=False, target_market="player_points")
        for key, value in policy.items():
            if type(getattr(self, key)) is not type(value) or getattr(self, key) != value:
                raise ProspectiveEvidenceError("preseason measurement policy cannot be promoted/changed")


def _measurement(value: dict) -> PreseasonMeasurement:
    if not isinstance(value, dict) or set(value) != set(PreseasonMeasurement.__dataclass_fields__):
        raise ProspectiveEvidenceError("measurement schema fields differ")
    try:
        return PreseasonMeasurement(**value)
    except TypeError as exc:
        raise ProspectiveEvidenceError("invalid measurement metadata") from exc


def _reject_model_field(key: str, item: object) -> None:
    name = _semantic_key(key)
    compact = name.replace("_", "")
    tokens = tuple(name.split("_"))
    def contains_pattern(pattern: tuple[str, ...]) -> bool:
        return any(tokens[index:index + len(pattern)] == pattern
                   for index in range(len(tokens) - len(pattern) + 1))
    def without_subject(value: str, prefixes: tuple[str, ...]) -> str:
        while True:
            prefix = next((p for p in prefixes if value.startswith(p)), None)
            if prefix is None:
                return value
            value = value[len(prefix):]
    def subject_forms(value: str, prefixes: tuple[str, ...]) -> tuple[str, ...]:
        forms = [value]
        while True:
            prefix = next((p for p in prefixes if value.startswith(p)), None)
            if prefix is None:
                return tuple(forms)
            value = value[len(prefix):]
            forms.append(value)
    market_forms = subject_forms(compact, _MARKET_SUBJECT_PREFIXES)
    market_name = market_forms[-1]
    outcome_name = market_name
    total_qualifier = next((prefix for prefix in _SCIENTIFIC_TOTAL_QUALIFIERS
                            if market_name.startswith(prefix)), None)
    scientific_total = (total_qualifier is not None
        and without_subject(market_name[len(total_qualifier):], _MARKET_SUBJECT_PREFIXES)
            .startswith(_POINTS_TOTAL_PREFIXES))
    scored_qualifier = next((prefix for prefix in _SCIENTIFIC_SCORED_QUALIFIERS
                             if market_name.startswith(prefix)), None)
    scored_forms = (subject_forms(market_name[len(scored_qualifier):], _MARKET_SUBJECT_PREFIXES)
                    if scored_qualifier is not None else ())
    scientific_scored = (any(form.startswith(_SCORED_OUTCOME_PREFIXES) for form in scored_forms)
        and not any(form.startswith("targetgame") for form in market_forms + scored_forms)
        and "final" not in tokens
        and not any(form.startswith(tuple(stem + qualifier for stem in _SCORED_OUTCOME_PREFIXES
            for qualifier in ("actual", "final", "settlement", "targetgame"))) for form in scored_forms))
    if scientific_scored:
        for form in scored_forms:
            stem = next((stem for stem in _SCORED_OUTCOME_PREFIXES if form.startswith(stem)), None)
            if stem is None:
                continue
            remainder = form[len(stem):]
            while remainder:
                # Keep each descriptor stage in its field role; a qualified
                # score cannot hide a declared market or target-outcome tail.
                _reject_model_field(remainder, item)
                descriptor = next((descriptor for descriptor in _FIELD_DESCRIPTOR_ORDER
                                   if remainder.startswith(descriptor)), None)
                if descriptor is None:
                    break
                tail = remainder[len(descriptor):]
                if descriptor in _SHORT_FIELD_DESCRIPTORS and tail and not tail.startswith(_FIELD_DESCRIPTORS):
                    break
                remainder = tail
    if (any((form in _OUTCOME_COMPACT or form.startswith(
            _STAT_OUTCOME_PREFIXES + _OUTCOME_PREFIXES + _OUTCOME_COMPOUND_PREFIXES)
            ) and not (scientific_scored and form in scored_forms
                       and form.startswith(_SCORED_OUTCOME_PREFIXES))
            for form in market_forms + scored_forms)
            or _OUTCOME_WORDS.intersection(tokens)
            or any(contains_pattern(pattern) for pattern in _OUTCOME_PATTERNS
                   if not scientific_scored or "_".join(pattern) not in _SCORED_OUTCOME_FIELDS)
            or outcome_name == "artifactdomain" and item != NBA_PROSPECTIVE_EVIDENCE):
        raise ProspectiveEvidenceError("target-game outcome is prohibited in model state")
    economic_name, wrapped = market_name, False
    while True:
        economic_name = without_subject(economic_name, _MARKET_SUBJECT_PREFIXES)
        wrapper = next((prefix for prefix in _BOOLEAN_WRAPPERS if economic_name.startswith(prefix)), None)
        if wrapper is None:
            break
        economic_name, wrapped = economic_name[len(wrapper):], True
    if wrapped and economic_name not in _ECONOMIC_FLAGS:
        economic_name = without_subject(economic_name, _PERMISSION_ACTIONS + _MARKET_SUBJECT_PREFIXES)
    if economic_name in _ECONOMIC_FLAGS or wrapped and economic_name in _ECONOMIC_ROUTE_COMPACT:
        if item is not False:
            raise ProspectiveEvidenceError("model state cannot enable an economic route")
        return
    if (any(route in economic_name for route in _ECONOMIC_COMPOUNDS)
            or economic_name in _SHORT_BET_COMPOUNDS or economic_name.startswith(_SHORT_BET_PREFIXES)
            or any(contains_pattern(tuple(route.split("_"))) for route in _ECONOMIC_ROUTES)):
        raise ProspectiveEvidenceError("unrecognized economic route field is prohibited")
    if (any(form in _PROHIBITED_COMPACT or form.startswith(
            _MARKET_COMPOUND_PREFIXES + _MARKET_SINGLE_PREFIXES + _STAT_VALUE_PREFIXES)
            # `linearg` is a field; `linear_gradient` is not an argument label.
            or any(form == stem or form.startswith(stem) and
                   form[len(stem):].startswith(_FIELD_DESCRIPTORS)
                   for stem in _MARKET_SHORT_DESCRIPTOR_STEMS)
            for form in market_forms)
            or market_name.startswith(_MARKET_PREFIXES + _MARKET_PRICE_PREFIXES)
            or market_name.startswith(_MARKET_PROBABILITY_PREFIXES)
            or not scientific_total and market_name.startswith(_POINTS_TOTAL_PREFIXES)
            or any(contains_pattern(pattern) for pattern in _MARKET_PATTERNS
                   if not scientific_total or "_".join(pattern) not in _POINTS_TOTAL_FIELDS)):
        raise ProspectiveEvidenceError("observed market/economic field is prohibited in model state")


def reject_market_outcomes(value: object) -> None:
    """Use the credential traversal for model market/outcome policy as well."""
    _safe_json(value, semantic_fields=True, field_policy=_reject_model_field)


def _nonnegative(value: object, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ProspectiveEvidenceError(f"invalid {name}")


def _validate_row(row: dict) -> None:
    if set(row) != _ROW_FIELDS or row["schema_version"] != MODEL_SCHEMA:
        raise ProspectiveEvidenceError("model snapshot fields/schema differ")
    reject_market_outcomes(row)
    metadata = _measurement(row["measurement_metadata"])
    for key in ("prediction_run_id", "operating_date", "repository_commit_sha"):
        if row[key] != getattr(metadata, key):
            raise ProspectiveEvidenceError("model/measurement binding mismatch")
    if row["model_state_status"] != "MODEL_STATE_INCOMPLETE":
        raise ProspectiveEvidenceError("foundation cannot qualify a live model producer")
    for key in ("canonical_event_id", "player_id", "team", "opponent", "model_id", "model_version"):
        require_id(row[key])
    if row["team"] == row["opponent"] or not isinstance(row["canonical_player_name"], str) or not row["canonical_player_name"].strip():
        raise ProspectiveEvidenceError("invalid canonical player/team identity")
    tipoff = utc_clock(row["commence_time_utc"])
    if toronto_operating_date(tipoff).isoformat() != metadata.operating_date:
        raise ProspectiveEvidenceError("target tipoff/operating date mismatch")
    events = row["provider_event_ids"]
    if not isinstance(events, dict):
        raise ProspectiveEvidenceError("provider event IDs must be an object")
    for provider, event_id in events.items():
        require_id(provider)
        require_id(event_id)
    for key in ("projected_minutes", "minutes_uncertainty", "projected_points"):
        if row[key] is not None:
            _nonnegative(row[key], key)
    for key in ("minutes_evidence_ref", "projection_evidence_ref", "distribution_evidence_ref"):
        ref = row[key]
        if ref is not None:
            if not isinstance(ref, dict) or set(ref) != {"request_id", "raw_body_sha256"}:
                raise ProspectiveEvidenceError("invalid model evidence reference")
            require_id(ref["request_id"])
            require_hash(ref["raw_body_sha256"])
    if (row["projected_minutes"] is not None or row["minutes_uncertainty"] is not None) and row["minutes_evidence_ref"] is None:
        raise ProspectiveEvidenceError("minutes values need preserved evidence")
    projection = ("projected_points", "projection_method", "projection_cutoff_utc", "projection_timestamp_utc")
    if row["projection_evidence_ref"] is not None or any(row[k] is not None for k in projection):
        if row["projection_evidence_ref"] is None or any(row[k] is None for k in projection):
            raise ProspectiveEvidenceError("incomplete projection evidence/clocks")
        require_id(row["projection_method"])
        if not utc_clock(row["projection_cutoff_utc"]) <= utc_clock(row["projection_timestamp_utc"]) < tipoff:
            raise ProspectiveEvidenceError("projection clocks must be prospective")
    distribution = ("distribution_model_id", "distribution_parameters", "distribution_evidence_ref")
    if any(row[k] is not None for k in distribution):
        if any(row[k] is None for k in distribution) or not isinstance(row["distribution_parameters"], dict):
            raise ProspectiveEvidenceError("incomplete distribution state")
        require_id(row["distribution_model_id"])
    if not isinstance(row["projection_inputs"], dict):
        raise ProspectiveEvidenceError("projection inputs must be an object")
    require_hash(row["source_manifest_sha256"])
    payload = {k: v for k, v in row.items() if k not in {"model_snapshot_id", "row_sha256"}}
    if digest(payload) != row["model_snapshot_id"] or digest({**payload, "model_snapshot_id": row["model_snapshot_id"]}) != row["row_sha256"]:
        raise ProspectiveEvidenceError("model snapshot identity/hash mismatch")


def model_snapshot(metadata: PreseasonMeasurement, *, source_manifest_sha256: str, **state) -> dict:
    """Bind explicit synthetic state without inferring missing future producer values."""
    reject_market_outcomes(state)
    if not set(state) <= _STATE_FIELDS:
        raise ProspectiveEvidenceError("unknown model snapshot field")
    payload = {k: None for k in _STATE_FIELDS}
    payload.update(provider_event_ids={}, projection_inputs={})
    payload.update(state)
    payload.update(schema_version=MODEL_SCHEMA, model_state_status="MODEL_STATE_INCOMPLETE",
        prediction_run_id=metadata.prediction_run_id, operating_date=metadata.operating_date,
        repository_commit_sha=metadata.repository_commit_sha, measurement_metadata=asdict(metadata),
        source_manifest_sha256=source_manifest_sha256)
    payload = json_clone(payload)
    payload["model_snapshot_id"] = digest(payload)
    payload["row_sha256"] = digest(payload)
    _validate_row(payload)
    return payload


def json_clone(value: object) -> object:
    # Accept only ordinary JSON at public write boundaries, never mutable aliases.
    from json import loads
    return loads(canonical_bytes(value))


def _validate_sources(sources: dict, metadata: PreseasonMeasurement, evidence_root: Path) -> None:
    if (not isinstance(sources, dict)
            or canonical_bytes(sources) != canonical_bytes(source_manifest(evidence_root, list(sources)))):
        raise ProspectiveEvidenceError("preserved source manifest mismatch")
    reject_market_outcomes(sources)
    for capture in sources.values():
        if capture["repository_commit_sha"] != metadata.repository_commit_sha:
            raise ProspectiveEvidenceError("source repository binding mismatch")
        if capture["operating_date"] not in {None, metadata.operating_date}:
            raise ProspectiveEvidenceError("source operating date mismatch")


def _row_source_bindings(row: dict, sources: dict, created: datetime) -> None:
    tipoff = utc_clock(row["commence_time_utc"])
    if created >= tipoff:
        raise ProspectiveEvidenceError("model freeze is not before tipoff")
    for provider, event_id in row["provider_event_ids"].items():
        if not any(c["provider"] == provider and c["provider_event_id"] == event_id and
                   c["canonical_event_id"] == row["canonical_event_id"] for c in sources.values()):
            raise ProspectiveEvidenceError("provider event identity lacks factual evidence")
    for key in ("minutes_evidence_ref", "projection_evidence_ref", "distribution_evidence_ref"):
        ref = row[key]
        if ref is None:
            continue
        capture = sources.get(ref["request_id"])
        if (capture is None or capture["raw_body_sha256"] != ref["raw_body_sha256"]
                or capture["canonical_event_id"] != row["canonical_event_id"]
                or capture["parameters"].get("player_id") != row["player_id"]
                or not 200 <= capture["http_status"] < 300):
            raise ProspectiveEvidenceError("model evidence is not bound to the canonical player/event")
        responded = utc_clock(capture["responded_at_utc"])
        if responded > created or responded >= tipoff:
            raise ProspectiveEvidenceError("model evidence was captured too late")
        if key == "projection_evidence_ref" and responded > utc_clock(row["projection_cutoff_utc"]):
            raise ProspectiveEvidenceError("projection evidence exceeds cutoff")
    if row["projection_timestamp_utc"] is not None and utc_clock(row["projection_timestamp_utc"]) > created:
        raise ProspectiveEvidenceError("projection was produced after freeze creation")


def _read_artifacts(root: Path, expected_repository_sha: str, evidence_root: Path) -> tuple[dict, list, dict]:
    manifest = read_document(root / "manifest.json")
    if set(manifest) != _MANIFEST_FIELDS or manifest["schema_version"] != FREEZE_SCHEMA:
        raise ProspectiveEvidenceError("unsupported freeze manifest schema/fields")
    metadata = _measurement(manifest["measurement_metadata"])
    require_hash(expected_repository_sha, 40)
    if manifest["repository_commit_sha"] != expected_repository_sha:
        raise ProspectiveEvidenceError("freeze differs from declared execution repository")
    for key in ("prediction_run_id", "operating_date", "repository_commit_sha"):
        if manifest[key] != getattr(metadata, key):
            raise ProspectiveEvidenceError("freeze/measurement binding mismatch")
    if root.name != metadata.prediction_run_id or root.parent.name != FREEZE_SCHEMA:
        raise ProspectiveEvidenceError("freeze path/identity mismatch")
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]:
        raise ProspectiveEvidenceError("freeze manifest hash mismatch")
    if type(manifest["row_count"]) is not int or manifest["row_count"] < 0:
        raise ProspectiveEvidenceError("invalid freeze row count")
    created = utc_clock(manifest["created_at_utc"])
    sources = read_document(root / "sources.json")
    exclusions = read_document(root / "exclusions.json")
    _validate_sources(sources, metadata, evidence_root)
    if digest(sources) != manifest["source_manifest_sha256"]:
        raise ProspectiveEvidenceError("source manifest hash mismatch")
    if (set(exclusions) != {"rows"} or not isinstance(exclusions["rows"], list)
            or any(not isinstance(r, dict) for r in exclusions["rows"])):
        raise ProspectiveEvidenceError("invalid exclusion evidence")
    reject_market_outcomes(exclusions)
    for capture in sources.values():
        if utc_clock(capture["responded_at_utc"]) > created:
            raise ProspectiveEvidenceError("source capture follows freeze creation")
    try:
        raw = plain_path(root / "model_snapshots.jsonl").read_bytes()
        exclusion_raw = plain_path(root / "exclusions.json").read_bytes()
    except OSError as exc:
        raise ProspectiveEvidenceError("freeze artifact missing/inaccessible") from exc
    if (hashlib.sha256(raw).hexdigest() != manifest["snapshot_file_sha256"] or
            hashlib.sha256(exclusion_raw).hexdigest() != manifest["exclusion_file_sha256"]):
        raise ProspectiveEvidenceError("freeze file hash mismatch")
    from courtvision.sports.nba.prospective_evidence import _decode_json
    rows = [_decode_json(line) for line in raw.splitlines()]
    if b"".join(canonical_bytes(row) + b"\n" for row in rows) != raw or len(rows) != manifest["row_count"]:
        raise ProspectiveEvidenceError("noncanonical snapshot file/row count")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProspectiveEvidenceError("snapshot row must be an object")
        _validate_row(row)
        if row["measurement_metadata"] != manifest["measurement_metadata"] or row["source_manifest_sha256"] != digest(sources):
            raise ProspectiveEvidenceError("row/run/source binding mismatch")
        key = (row["canonical_event_id"], row["player_id"])
        if key in seen:
            raise ProspectiveEvidenceError("duplicate canonical event/player snapshot")
        seen.add(key)
        _row_source_bindings(row, sources, created)
    if rows != sorted(rows, key=lambda r: (r["canonical_event_id"], r["player_id"])):
        raise ProspectiveEvidenceError("snapshot ordering differs")
    return manifest, rows, sources


@dataclass(frozen=True, slots=True)
class VerifiedModelFreeze:
    manifest: Mapping
    receipt: Mapping
    rows: tuple[Mapping, ...]


def verify_model_freeze(root: str | Path, *, expected_repository_sha: str,
                        evidence_root: str | Path) -> VerifiedModelFreeze:
    root = plain_path(root)
    manifest, rows, _ = _read_artifacts(root, expected_repository_sha, plain_path(evidence_root))
    receipt = read_document(root / "freeze.json")
    if (set(receipt) != {"schema_version", "manifest_sha256", "durable_at_utc", "receipt_sha256"}
            or receipt["schema_version"] != FREEZE_SCHEMA
            or receipt["manifest_sha256"] != manifest["manifest_sha256"]
            or digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != receipt["receipt_sha256"]):
        raise ProspectiveEvidenceError("invalid durable freeze receipt")
    durable = utc_clock(receipt["durable_at_utc"])
    if durable < utc_clock(manifest["created_at_utc"]) or any(durable >= utc_clock(r["commence_time_utc"]) for r in rows):
        raise ProspectiveEvidenceError("durable freeze clocks are not prospective")
    if {p.name for p in root.iterdir()} != _FILES:
        raise ProspectiveEvidenceError("incomplete/unexpected freeze artifacts")
    return VerifiedModelFreeze(immutable(manifest), immutable(receipt), tuple(immutable(r) for r in rows))


def freeze_models(freeze_root: str | Path, *, metadata: PreseasonMeasurement, rows: list[dict],
                  evidence_root: str | Path, request_ids: list[str], exclusions: list[dict],
                  clock=lambda: datetime.now(timezone.utc)) -> Path:
    """Claim one run directory; fsync/read back each file and publish a receipt last.

    Exact content retries return the original receipt. Conflicts and interrupted
    claims fail closed. Nothing is overwritten, rolled back, or deleted.
    """
    metadata = _measurement(asdict(metadata))
    sources = source_manifest(evidence_root, request_ids)
    _validate_sources(sources, metadata, Path(evidence_root))
    rows = sorted(json_clone(rows), key=lambda r: (r["canonical_event_id"], r["player_id"]))
    exclusions_payload = json_clone({"rows": exclusions})
    reject_market_outcomes(exclusions_payload)
    if not isinstance(exclusions, list) or any(not isinstance(r, dict) for r in exclusions):
        raise ProspectiveEvidenceError("exclusions must be a list of evidence objects")
    root = plain_path(Path(freeze_root) / FREEZE_SCHEMA / metadata.prediction_run_id)
    blobs = {"model_snapshots.jsonl": b"".join(canonical_bytes(r) + b"\n" for r in rows),
             "sources.json": canonical_bytes(sources) + b"\n",
             "exclusions.json": canonical_bytes(exclusions_payload) + b"\n"}
    # Validate all caller data before claiming a directory.
    seen = set()
    for row in rows:
        _validate_row(row)
        if row["measurement_metadata"] != asdict(metadata) or row["source_manifest_sha256"] != digest(sources):
            raise ProspectiveEvidenceError("snapshot does not bind this freeze/source manifest")
        key = (row["canonical_event_id"], row["player_id"])
        if key in seen:
            raise ProspectiveEvidenceError("duplicate canonical event/player snapshot")
        seen.add(key)
    if root.exists():
        saved = verify_model_freeze(root, expected_repository_sha=metadata.repository_commit_sha, evidence_root=evidence_root)
        if saved.manifest["measurement_metadata"] != immutable(asdict(metadata)) or any(
                plain_path(root / name).read_bytes() != raw for name, raw in blobs.items()):
            raise ProspectiveEvidenceError("conflicting same-run freeze")
        return root
    created = utc_clock(clock().isoformat())
    for capture in sources.values():
        if utc_clock(capture["responded_at_utc"]) > created:
            raise ProspectiveEvidenceError("source capture follows freeze creation")
    for row in rows:
        _row_source_bindings(row, sources, created)
    manifest = dict(schema_version=FREEZE_SCHEMA, prediction_run_id=metadata.prediction_run_id,
        operating_date=metadata.operating_date, repository_commit_sha=metadata.repository_commit_sha,
        measurement_metadata=asdict(metadata), row_count=len(rows), source_manifest_sha256=digest(sources),
        snapshot_file_sha256=hashlib.sha256(blobs["model_snapshots.jsonl"]).hexdigest(),
        exclusion_file_sha256=hashlib.sha256(blobs["exclusions.json"]).hexdigest(), created_at_utc=created.isoformat())
    manifest["manifest_sha256"] = digest(manifest)
    root.parent.mkdir(parents=True, exist_ok=True)
    plain_path(root)
    try:
        root.mkdir()
    except FileExistsError as exc:
        raise ProspectiveEvidenceError("freeze run was claimed concurrently") from exc
    for name, raw in blobs.items():
        write_once(root / name, raw)
    write_once(root / "manifest.json", canonical_bytes(manifest) + b"\n")
    _read_artifacts(root, metadata.repository_commit_sha, Path(evidence_root))
    durable = utc_clock(clock().isoformat())
    if durable < created or any(durable >= utc_clock(r["commence_time_utc"]) for r in rows):
        raise ProspectiveEvidenceError("tipoff/clock changed before durable freeze")
    receipt = dict(schema_version=FREEZE_SCHEMA, manifest_sha256=manifest["manifest_sha256"], durable_at_utc=durable.isoformat())
    receipt["receipt_sha256"] = digest(receipt)
    write_once(root / "freeze.json", canonical_bytes(receipt) + b"\n")
    verify_model_freeze(root, expected_repository_sha=metadata.repository_commit_sha, evidence_root=evidence_root)
    return root


def bind_market_observation(root: str | Path, *, expected_repository_sha: str,
                            evidence_root: str | Path, observation: dict) -> Mapping:
    """Read-only boundary for future adapters; creates no probability or pick.

    01D must evaluate a qualified frozen distribution at the observed line. This
    helper binds identities only and cannot qualify the incomplete v1 model.
    """
    fields = {"schema_version", "freeze_manifest_sha256", "model_snapshot_id", "canonical_event_id",
        "player_id", "bookmaker", "line", "decimal_odds", "observed_at_utc", "raw_market_evidence_ref"}
    if not isinstance(observation, dict) or set(observation) != fields or observation["schema_version"] != MARKET_SCHEMA:
        raise ProspectiveEvidenceError("market observation schema/fields differ")
    _safe_json(observation, semantic_fields=True)
    freeze = verify_model_freeze(root, expected_repository_sha=expected_repository_sha, evidence_root=evidence_root)
    if observation["freeze_manifest_sha256"] != freeze.manifest["manifest_sha256"]:
        raise ProspectiveEvidenceError("market observation references another freeze")
    matching = [r for r in freeze.rows if r["model_snapshot_id"] == observation["model_snapshot_id"]]
    if len(matching) != 1:
        raise ProspectiveEvidenceError("market observation references no unique frozen snapshot")
    row = matching[0]
    for key in ("canonical_event_id", "player_id"):
        if observation[key] != row[key]:
            raise ProspectiveEvidenceError("market/model identity mismatch")
    require_id(observation["bookmaker"])
    _nonnegative(observation["line"], "player-points line")
    _nonnegative(observation["decimal_odds"], "price")
    if observation["decimal_odds"] <= 1:
        raise ProspectiveEvidenceError("decimal price must exceed one")
    observed = utc_clock(observation["observed_at_utc"])
    durable = utc_clock(freeze.receipt["durable_at_utc"])
    if not durable < observed < utc_clock(row["commence_time_utc"]):
        raise ProspectiveEvidenceError("market observation must follow durable freeze and precede tipoff")
    ref = observation["raw_market_evidence_ref"]
    if not isinstance(ref, dict) or set(ref) != {"request_id", "raw_body_sha256"}:
        raise ProspectiveEvidenceError("invalid raw market evidence reference")
    capture = verify_capture(evidence_root, ref["request_id"])
    if (capture.manifest["source_role"] != "market" or capture.manifest["raw_body_sha256"] != ref["raw_body_sha256"]
            or capture.manifest["repository_commit_sha"] != expected_repository_sha
            or capture.manifest["operating_date"] != row["operating_date"]
            or capture.manifest["canonical_event_id"] != row["canonical_event_id"]
            or capture.manifest["parameters"].get("player_id") != row["player_id"]
            or not 200 <= capture.manifest["http_status"] < 300
            or not durable < utc_clock(capture.manifest["requested_at_utc"]) <= utc_clock(capture.manifest["responded_at_utc"]) <= observed):
        raise ProspectiveEvidenceError("market capture does not bind post-freeze player/event evidence")
    identity = digest(observation)
    return immutable(dict(assessment_id=digest({"model_snapshot_id": row["model_snapshot_id"],
        "market_observation_identity": identity, "assessment_algorithm_version": ASSESSMENT_VERSION}),
        market_observation_identity=identity, assessment_algorithm_version=ASSESSMENT_VERSION,
        model_snapshot_id=row["model_snapshot_id"], row_sha256=row["row_sha256"],
        freeze_manifest_sha256=freeze.manifest["manifest_sha256"], research_only=True,
        eligible_for_betting=False, model_state_status="MODEL_STATE_INCOMPLETE"))
