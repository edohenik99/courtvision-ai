"""NBA prospective/outcome boundaries; historical mixed files are audit-only.

These guards apply only at prospective admission and artifact writers. They do
not restrict retrospective/training data or establish model ownership.
"""

from collections.abc import Mapping
from pathlib import Path
from datetime import datetime, timedelta
import re

NBA_PROSPECTIVE_EVIDENCE = "NBA_PROSPECTIVE_EVIDENCE"
NBA_OUTCOME_EVIDENCE = "NBA_OUTCOME_EVIDENCE"
LEGACY_MIXED_ARTIFACT = "LEGACY_MIXED_ARTIFACT"
NBA_STAT_ARTIFACT_SCHEMA = "nba-stat-artifact-v1"

TARGET_OUTCOME_FIELDS = frozenset({
    "actual_points", "final_points", "target_game_actual_points",
    "target_game_final_points", "actual_minutes", "target_game_actual_minutes",
    "final_stats", "target_game_final_stats", "box_score", "boxscore",
    "settlement", "settlement_status", "settlement_result", "result", "results",
    "grade", "grading", "target_game_result", "target_game_grade",
})


def contains_target_game_outcome(value: object) -> bool:
    """Reject outcome keys even when null, nested, or differently capitalized."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
            name = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
            if name in TARGET_OUTCOME_FIELDS or name.startswith(("actual_", "target_game_actual_", "target_game_final_", "settlement_", "grading_", "result_", "grade_")):
                return True
            if name == "artifact_domain" and item != NBA_PROSPECTIVE_EVIDENCE:
                return True
            if contains_target_game_outcome(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(contains_target_game_outcome(item) for item in value)
    return False


def require_artifact_path(path: str | Path, domain: str) -> Path:
    """Reject cross-domain paths, including resolved aliases, before any I/O."""
    if domain not in {NBA_PROSPECTIVE_EVIDENCE, NBA_OUTCOME_EVIDENCE}:
        raise ValueError("unsupported NBA artifact domain")
    path = Path(path)
    forbidden = {"outcomes", "settlement", "settlements"} if domain == NBA_PROSPECTIVE_EVIDENCE else {"prospective", "runs", "ledgers", "closing"}
    for candidate in (path.absolute(), path.resolve()):
        if forbidden.intersection(part.casefold() for part in candidate.parts):
            raise ValueError(f"{domain} cannot use the opposite artifact namespace")
    return path


def stat_artifact_path(root: str | Path, domain: str, operating_date: str) -> Path:
    """Forward-only filenames: never reuse the historical shared CSV name."""
    require_artifact_path(root, domain)
    directory, stem = (
        ("prospective", "player_stat_projections")
        if domain == NBA_PROSPECTIVE_EVIDENCE
        else ("outcomes", "player_actual_stats")
    )
    return require_artifact_path(Path(root) / "nba" / directory / f"{stem}_{operating_date}.csv", domain)


def validate_prospective_stat_rows(rows: list[dict], operating_date: str) -> None:
    """Legacy CSV estimates need explicit semantics and pregame clocks.

    Unmarked historical files stay LEGACY_MIXED even if their columns look like
    projections. Raw stat aliases are forbidden on this prospective boundary.
    """
    for row in rows:
        if row.get("prospective_status") == "unqualified" or row.get("projection_context_qualification") == "legacy_unqualified":
            raise ValueError("legacy/unqualified projection context cannot be promoted to prospective evidence")
        if row.get("artifact_domain") != NBA_PROSPECTIVE_EVIDENCE or row.get("artifact_schema_version") != NBA_STAT_ARTIFACT_SCHEMA:
            raise ValueError("LEGACY_MIXED_ARTIFACT: explicit prospective schema is required")
        if contains_target_game_outcome(row) or {"points", "pts", "minutes", "rebounds", "reb", "assists", "ast"}.intersection(str(key).casefold() for key in row):
            raise ValueError("prospective source contains target-game actual/stat aliases")
        if str(row.get("operating_date")) != operating_date:
            raise ValueError("prospective operating_date mismatch")
        clocks = []
        for field in ("source_timestamp_utc", "projection_timestamp_utc", "evidence_cutoff_timestamp_utc", "commence_time_utc"):
            try:
                value = datetime.fromisoformat(str(row.get(field)).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"prospective {field} is required") from exc
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"prospective {field} must be UTC")
            clocks.append(value)
        if not clocks[0] <= clocks[1] <= clocks[2] < clocks[3]:
            raise ValueError("prospective source observation <= projection <= cutoff < tipoff required")
