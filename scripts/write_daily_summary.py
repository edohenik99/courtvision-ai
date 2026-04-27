from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return text


def _read_csv(path: Path, warnings: list[str]) -> pd.DataFrame:
    if not path.exists():
        warnings.append(f"Missing file: {path}")
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        warnings.append(f"Could not read CSV {path}: {exc}")
        return pd.DataFrame()


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"Missing file: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"Could not read JSON {path}: {exc}")
        return {}


def _format_money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _format_num(value: Any, digits: int = 3) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number:.{digits}f}"


def _format_pct(value: Any) -> str:
    number = _safe_float(value)
    return "n/a" if number is None else f"{number * 100:.1f}%"


def _market_counts(df: pd.DataFrame) -> Counter[str]:
    if df.empty or "market_type" not in df.columns:
        return Counter()
    return Counter(_safe_text(value) or "unknown" for value in df["market_type"])


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _sort_for_display(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("quality_score", "expected_value", "edge", "edge_pct"):
        if col in df.columns:
            working = df.copy()
            working[col] = pd.to_numeric(working[col], errors="coerce")
            return working.sort_values(col, ascending=False, na_position="last")
    return df


def _pick_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or _safe_text(row.get("entity_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    side = _safe_text(row.get("selection")) or "n/a"
    line = _format_num(row.get("sportsbook_line") if "sportsbook_line" in row.index else row.get("line"), 1)
    odds = _safe_text(row.get("odds") if "odds" in row.index else row.get("american_odds")) or "n/a"
    edge = _format_num(row.get("edge"), 3)
    confidence = _format_num(row.get("confidence"), 3)
    quality = _format_num(row.get("quality_score"), 2)
    return (
        f"- {player}: {market} {side} {line} "
        f"(odds={odds}, edge={edge}, conf={confidence}, quality={quality})"
    )


def _kelly_line(row: pd.Series) -> str:
    player = _safe_text(row.get("player_name")) or "Unknown"
    market = _safe_text(row.get("market_type")) or "unknown"
    side = _safe_text(row.get("selection")) or "n/a"
    line = _format_num(row.get("line"), 1)
    stake = _format_money(_safe_float(row.get("stake_amount")))
    ev = _format_money(_safe_float(row.get("expected_value")))
    edge = _format_pct(row.get("edge_pct"))
    return f"- {player}: {market} {side} {line} stake={stake} EV={ev} edge={edge}"


def build_daily_summary(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[str, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    operator_dir = runtime_root / "operator"
    diagnostics_dir = runtime_root / "diagnostics"
    warnings: list[str] = []

    elite_df = _read_csv(operator_dir / f"elite_board_{prediction_date}.csv", warnings)
    kelly_df = _read_csv(operator_dir / f"kelly_stakes_{prediction_date}.csv", warnings)
    full_market_df = _read_csv(operator_dir / f"full_market_board_{prediction_date}.csv", warnings)
    shadow = _read_json(diagnostics_dir / f"market_shadow_grading_{prediction_date}.json", warnings)
    readiness = _read_json(diagnostics_dir / f"market_performance_readiness_{prediction_date}.json", warnings)
    manual_context = _read_json(diagnostics_dir / f"manual_context_{prediction_date}.json", warnings)

    kelly_eligible = (
        kelly_df[kelly_df["eligible"].map(_is_truthy)].copy()
        if not kelly_df.empty and "eligible" in kelly_df.columns
        else kelly_df.copy()
    )
    total_exposure = (
        float(pd.to_numeric(kelly_eligible.get("stake_amount", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not kelly_eligible.empty
        else 0.0
    )
    expected_ev = (
        float(pd.to_numeric(kelly_eligible.get("expected_value", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        if not kelly_eligible.empty
        else 0.0
    )
    counts = _market_counts(full_market_df)
    shadow_totals = shadow.get("totals", {}) if isinstance(shadow, dict) else {}
    pending_grading = int(shadow_totals.get("pending_picks") or 0)

    readiness_markets = readiness.get("markets", []) if isinstance(readiness, dict) else []
    rejection_counts = readiness.get("rejection_count_by_market_type_reason", {}) if isinstance(readiness, dict) else {}

    lines = [
        f"Daily Summary - {prediction_date}",
        "=" * 72,
        "Scope: elite board and Kelly remain locked to player_points only.",
        "",
        "Elite Picks",
        "-" * 72,
    ]
    if elite_df.empty:
        lines.append("- None")
    else:
        for _, row in _sort_for_display(elite_df).iterrows():
            lines.append(_pick_line(row))

    lines.extend(["", "Kelly Stakes", "-" * 72])
    if kelly_eligible.empty:
        lines.append("- None")
    else:
        for _, row in _sort_for_display(kelly_eligible).iterrows():
            lines.append(_kelly_line(row))
    lines.append(f"Total exposure: {_format_money(total_exposure)}")
    lines.append(f"Expected EV: {_format_money(expected_ev)}")

    lines.extend(["", "Full-Market Market Counts", "-" * 72])
    if counts:
        for market, count in sorted(counts.items()):
            lines.append(f"- {market}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "Shadow Grading Totals", "-" * 72])
    lines.append(f"- total picks: {int(shadow_totals.get('total_picks') or 0)}")
    lines.append(f"- graded picks: {int(shadow_totals.get('graded_picks') or 0)}")
    lines.append(f"- pending picks: {pending_grading}")
    lines.append(f"- hit rate: {_format_pct(shadow_totals.get('hit_rate'))}")
    lines.append(f"Pending grading count: {pending_grading}")

    lines.extend(["", "Manual Context", "-" * 72])
    if manual_context:
        file_found = _is_truthy(manual_context.get("file_found"))
        rows = int(manual_context.get("rows") or 0)
        matches = int(manual_context.get("candidate_matches") or 0)
        lines.append(f"- file found: {str(file_found).lower()}")
        lines.append(f"- rows: {rows}")
        lines.append(f"- candidate matches: {matches}")
        lines.append("- passive mode: true")
        if manual_context.get("warnings"):
            for warning in manual_context.get("warnings") or []:
                lines.append(f"- warning: {warning}")
    else:
        lines.append("- file found: false")
        lines.append("- candidate matches: 0")
        lines.append("- passive mode: true")

    lines.extend(["", "Warnings / Readiness Notes", "-" * 72])
    lines.append("- Elite board locked to player_points only.")
    lines.append("- Kelly stakes locked to player_points only.")
    if manual_context:
        matches = int(manual_context.get("candidate_matches") or 0)
        lines.append(f"- Manual player context is diagnostic only; matched candidates: {matches}.")
    non_points = sum(count for market, count in counts.items() if market != "player_points")
    if non_points:
        lines.append(f"- {non_points} non-points full-market picks are diagnostic/readiness only.")
    if readiness_markets:
        for row in readiness_markets:
            market = _safe_text(row.get("market_type")) or "unknown"
            count = int(row.get("full_market_count") or row.get("count") or 0)
            avg_confidence = _format_num(row.get("avg_confidence"), 3)
            avg_quality = _format_num(row.get("avg_quality_score"), 2)
            lines.append(f"- readiness {market}: count={count}, avg_conf={avg_confidence}, avg_quality={avg_quality}")
    if rejection_counts:
        total_rejections = 0
        for reasons in rejection_counts.values():
            if isinstance(reasons, dict):
                total_rejections += sum(int(value or 0) for value in reasons.values())
        if total_rejections:
            lines.append(f"- full-market gate rejections tracked: {total_rejections}")
    for warning in warnings:
        lines.append(f"- WARNING: {warning}")

    metadata = {
        "elite_count": int(len(elite_df)),
        "kelly_eligible_count": int(len(kelly_eligible)),
        "total_exposure": round(total_exposure, 2),
        "expected_ev": round(expected_ev, 2),
        "full_market_counts": dict(sorted(counts.items())),
        "shadow_totals": shadow_totals,
        "pending_grading_count": pending_grading,
        "manual_context": manual_context,
        "warnings": warnings,
    }
    return "\n".join(lines) + "\n", metadata


def write_daily_summary_outputs(
    *,
    prediction_date: str,
    runtime_root: str | Path = "outputs/runtime",
) -> tuple[Path, dict[str, Any]]:
    runtime_root = Path(runtime_root)
    summary, metadata = build_daily_summary(prediction_date=prediction_date, runtime_root=runtime_root)
    output_path = runtime_root / "operator" / f"daily_summary_{prediction_date}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")
    return output_path, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the operator daily summary report.")
    parser.add_argument("--prediction-date", required=True)
    parser.add_argument("--runtime-root", default="outputs/runtime")
    args = parser.parse_args(argv)
    output_path, metadata = write_daily_summary_outputs(
        prediction_date=args.prediction_date,
        runtime_root=args.runtime_root,
    )
    print(f"daily_summary_txt={output_path}")
    print(
        "daily_summary_totals "
        f"elite={metadata['elite_count']} "
        f"kelly_eligible={metadata['kelly_eligible_count']} "
        f"exposure={metadata['total_exposure']:.2f} "
        f"expected_ev={metadata['expected_ev']:.2f} "
        f"pending_grading={metadata['pending_grading_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
