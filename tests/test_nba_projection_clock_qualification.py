from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts import audit_projection_source as audit
from scripts import build_stat_projection_source as builder
from scripts.run_market_projection_join import _load_projection_source
from courtvision.sports.nba.artifact_domains import validate_prospective_stat_rows

DATE = "2026-06-05"
CLOCKS = {"source_timestamp_utc": "2026-06-05T17:00:00Z",
          "evidence_cutoff_timestamp_utc": "2026-06-05T19:00:00Z",
          "commence_time_utc": "2026-06-05T23:00:00Z"}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 6, 5, 18, tzinfo=timezone.utc)


def build(tmp_path, monkeypatch, overrides=None, *, default=False):
    monkeypatch.setattr(builder, "datetime", Clock)
    source = tmp_path / "player_baselines.csv"
    row = {"player_id": 1, "player_name": "Synthetic Player", "pts_avg": 20,
           "pts_recent": 22, "min_avg": 30, "min_recent": 31, **CLOCKS}
    row.update(overrides or {})
    pd.DataFrame([row]).to_csv(source, index=False)
    if default:
        monkeypatch.setattr(audit, "DEFAULT_PROJECTION_SOURCE", source)
    audited = audit.run_projection_source_audit(target_date=DATE, projection_source=source,
        output_dir=tmp_path / "research", diagnostics_dir=tmp_path / "diagnostics")
    result = builder.build_stat_projection_source(target_date=DATE, cleaned_context=audited.cleaned_context_path,
        output_dir=tmp_path / "research", diagnostics_dir=tmp_path / "diagnostics")
    return audited, result, pd.read_csv(result.output_path).iloc[0].to_dict()


def test_supplied_clocks_propagate_unchanged_through_audit_and_build(tmp_path, monkeypatch):
    audited, result, row = build(tmp_path, monkeypatch)
    cleaned = pd.read_csv(audited.cleaned_context_path).iloc[0]
    for name, value in CLOCKS.items():
        assert cleaned[name] == row[name] == value
    assert row["projection_timestamp_utc"] == "2026-06-05T18:00:00+00:00"
    assert result.status == builder.STAT_PROJECTION_OK
    assert row["prospective_status"] == "clock_qualified_diagnostic_only"
    assert result.diagnostics["qualified_prospective_source"] is False
    validate_prospective_stat_rows([row], DATE)


@pytest.mark.parametrize("overrides", [
    {"source_timestamp_utc": None},
    {"evidence_cutoff_timestamp_utc": None},
    {"commence_time_utc": None},
    {"source_timestamp_utc": "2026-06-05T18:01:00Z"},
    {"evidence_cutoff_timestamp_utc": "2026-06-05T23:00:00Z"},
    {"commence_time_utc": "2026-06-05T17:30:00Z"},
    {"projection_timestamp_utc": "2026-06-05T16:00:00Z", "source_timestamp_utc": "2026-06-05T18:01:00Z"},
    {"source_timestamp_utc": "2026-06-05T17:00:00"},
])
def test_invalid_clocks_cannot_qualify_or_backdate_projection(tmp_path, monkeypatch, overrides):
    _, result, row = build(tmp_path, monkeypatch, overrides)
    assert result.status == builder.STAT_PROJECTION_UNQUALIFIED
    assert row["prospective_status"] == "unqualified"
    assert row["projection_timestamp_utc"] == "2026-06-05T18:00:00+00:00"
    with pytest.raises(ValueError):
        validate_prospective_stat_rows([row], DATE)
    warnings = []
    _, frame, available, _ = _load_projection_source(target_date=DATE, output_dir=tmp_path,
        projection_source=result.output_path, warnings=warnings)
    assert not available and frame.empty and warnings


def test_default_legacy_path_and_renamed_copy_stay_unqualified_even_with_clock_columns(tmp_path, monkeypatch):
    audited, result, row = build(tmp_path, monkeypatch, default=True)
    assert audited.diagnostics["projection_context_qualification"] == "legacy_unqualified"
    assert result.status == builder.STAT_PROJECTION_UNQUALIFIED
    assert row["projection_context_qualification"] == "legacy_unqualified"
    renamed = tmp_path / "qualified_prospective_source.csv"
    renamed.write_bytes(audited.cleaned_context_path.read_bytes())
    audited_again = audit.run_projection_source_audit(target_date=DATE, projection_source=renamed,
        output_dir=tmp_path / "renamed_research", diagnostics_dir=tmp_path / "renamed_diagnostics")
    second = builder.build_stat_projection_source(target_date=DATE, cleaned_context=audited_again.cleaned_context_path,
        output_dir=tmp_path / "renamed_research", diagnostics_dir=tmp_path / "renamed_diagnostics")
    assert second.status == builder.STAT_PROJECTION_UNQUALIFIED
    assert "legacy_unqualified" in second.output_path.read_text()
