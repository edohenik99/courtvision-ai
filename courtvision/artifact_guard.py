from __future__ import annotations

import re
from pathlib import Path


_ARTIFACT_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def artifact_date_from_path(path: Path) -> str:
    path = Path(path)
    filename_match = _ARTIFACT_DATE_PATTERN.search(path.name)
    if filename_match:
        return filename_match.group(0)
    for part in reversed(path.parts):
        part_match = _ARTIFACT_DATE_PATTERN.fullmatch(str(part))
        if part_match:
            return part_match.group(0)
    return ""


def guard_prediction_artifact_date(
    *,
    requested_prediction_date: str,
    output_path: Path,
    caller: str,
) -> str:
    artifact_date = artifact_date_from_path(output_path)
    if artifact_date and artifact_date != str(requested_prediction_date):
        raise RuntimeError(
            "[ARTIFACT_DATE_GUARD] "
            f"requested_prediction_date={requested_prediction_date} "
            f"artifact_date={artifact_date} "
            f"output_path={output_path} "
            f"caller={caller}"
        )
    return artifact_date or str(requested_prediction_date)


def guard_no_existing_artifact(
    *,
    output_path: Path,
    force: bool = False,
    caller: str,
    artifact_label: str,
) -> None:
    output_path = Path(output_path)
    if force:
        return
    if output_path.exists():
        raise RuntimeError(
            "[ARTIFACT_OVERWRITE_GUARD] "
            f"output_path={output_path} already exists "
            f"artifact={artifact_label} "
            f"caller={caller}. "
            "Pass the explicit force overwrite flag only when intentional."
        )


def log_prediction_artifact_write(
    *,
    requested_prediction_date: str,
    output_path: Path,
    caller: str,
    artifact_label: str,
) -> None:
    artifact_date = guard_prediction_artifact_date(
        requested_prediction_date=requested_prediction_date,
        output_path=output_path,
        caller=caller,
    )
    print(
        "[ARTIFACT_WRITE] "
        f"requested_prediction_date={requested_prediction_date} "
        f"artifact_date={artifact_date} "
        f"output_path={output_path} "
        f"caller={caller} "
        f"artifact={artifact_label}",
        flush=True,
    )
