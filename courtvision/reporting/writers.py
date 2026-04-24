from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.models import Projection


def write_projection_csv(path: Path, rows: list[Projection]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row.to_dict() for row in rows]).to_csv(path, index=False)


def write_status_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_text_report(path: Path, title: str, rows: list[Projection]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [title, "=" * len(title), ""]
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"{idx}. {row.player_name} | {row.team_abbreviation} vs {row.opponent_abbreviation} | "
            f"PTS {row.points:.1f} REB {row.rebounds:.1f} AST {row.assists:.1f} | "
            f"Min {row.expected_minutes:.1f} | Exposure {row.exposure_score:.2f} | {row.confidence_tier}"
        )
        if row.reasons:
            lines.append(f"   Notes: {'; '.join(row.reasons)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
