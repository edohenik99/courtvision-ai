"""Evaluation report exporter for CSV, JSON, and TXT formats.

VALIDATE + CALIBRATE mode - Measurement and validation only.

Task A: Build evaluation dashboard/report layer
"""

from __future__ import annotations

import json
from typing import Any

from courtvision.evaluation.report_builder import EvaluationReport


class ReportExporter:
    """Export evaluation reports to various formats."""

    def export_json(self, report: EvaluationReport, filepath: str) -> None:
        """Export report to JSON file.

        Args:
            report: Evaluation report
            filepath: Output file path
        """
        data = report.to_dict()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def export_csv_summary(self, report: EvaluationReport, filepath: str) -> None:
        """Export CSV summary of key metrics.

        Args:
            report: Evaluation report
            filepath: Output file path
        """
        lines = []
        lines.append("metric,value")
        lines.append(f"window_start,{report.window_start}")
        lines.append(f"window_end,{report.window_end}")
        lines.append(f"total_picks,{report.total_picks}")
        lines.append(f"portfolio_drawdown,{report.portfolio_drawdown}")
        lines.append(f"portfolio_volatility,{report.portfolio_volatility}")
        lines.append(f"calibration_score,{report.calibration_score}")

        # Confidence buckets
        for bucket in report.confidence_buckets:
            if bucket.count > 0:
                lines.append(f"hit_rate_{bucket.bucket_name},{bucket.hit_rate}")

        # Edge buckets
        for bucket in report.edge_buckets:
            if bucket.count > 0:
                lines.append(f"edge_performance_{bucket.bucket_name},{bucket.hit_rate}")

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def export_txt_summary(self, report: EvaluationReport, filepath: str) -> None:
        """Export human-readable text summary.

        Args:
            report: Evaluation report
            filepath: Output file path
        """
        lines = []
        lines.append("=" * 60)
        lines.append("EVALUATION REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"Window: {report.window_start} to {report.window_end}")
        lines.append(f"Total Picks: {report.total_picks}")
        lines.append("")

        # Calibration
        lines.append("-" * 40)
        lines.append("CALIBRATION")
        lines.append("-" * 40)
        lines.append(f"Score: {report.calibration_score:.3f} ({report._interpret_calibration()})")
        lines.append("")

        # Confidence buckets
        lines.append("-" * 40)
        lines.append("CONFIDENCE BUCKETS")
        lines.append("-" * 40)
        lines.append(f"{'Bucket':<12} {'Count':<8} {'Hit Rate':<10} {'Avg EV':<10} {'Avg CLV':<10}")
        lines.append("-" * 40)

        for bucket in report.confidence_buckets:
            if bucket.count > 0:
                lines.append(
                    f"{bucket.bucket_name:<12} {bucket.count:<8} "
                    f"{bucket.hit_rate:<10.3f} {bucket.avg_ev:<10.3f} {bucket.avg_clv:<10.3f}"
                )
        lines.append("")

        # Edge buckets
        lines.append("-" * 40)
        lines.append("EDGE BUCKETS")
        lines.append("-" * 40)
        lines.append(f"{'Bucket':<12} {'Count':<8} {'Hit Rate':<10} {'Avg CLV':<10}")
        lines.append("-" * 40)

        for bucket in report.edge_buckets:
            if bucket.count > 0:
                lines.append(
                    f"{bucket.bucket_name:<12} {bucket.count:<8} "
                    f"{bucket.hit_rate:<10.3f} {bucket.avg_clv:<10.3f}"
                )
        lines.append("")

        # Stat type
        if report.stat_type_metrics:
            lines.append("-" * 40)
            lines.append("STAT TYPE PERFORMANCE")
            lines.append("-" * 40)
            for stat, metrics in report.stat_type_metrics.items():
                lines.append(
                    f"{stat:<15} Count: {metrics.count:<4} Hit Rate: {metrics.hit_rate:.3f}"
                )
            lines.append("")

        # Market regimes
        if report.regime_metrics:
            lines.append("-" * 40)
            lines.append("MARKET REGIME PERFORMANCE")
            lines.append("-" * 40)
            for regime, metrics in report.regime_metrics.items():
                lines.append(
                    f"{regime:<15} Count: {metrics.count:<4} Hit Rate: {metrics.hit_rate:.3f}"
                )
            lines.append("")

        # Portfolio
        lines.append("-" * 40)
        lines.append("PORTFOLIO METRICS")
        lines.append("-" * 40)
        lines.append(f"Drawdown: {report.portfolio_drawdown:.3f}")
        lines.append(f"Volatility: {report.portfolio_volatility:.3f}")
        lines.append("")

        # Quality metrics
        if report.top_rejection_reasons:
            lines.append("-" * 40)
            lines.append("TOP REJECTION REASONS")
            lines.append("-" * 40)
            for reason, count in report.top_rejection_reasons[:5]:
                lines.append(f"{reason}: {count}")
            lines.append("")

        if report.top_miss_categories:
            lines.append("-" * 40)
            lines.append("TOP MISS CATEGORIES")
            lines.append("-" * 40)
            for category, count in report.top_miss_categories[:5]:
                lines.append(f"{category}: {count}")
            lines.append("")

        # Signals
        if report.reliable_signals:
            lines.append("-" * 40)
            lines.append("MOST RELIABLE SIGNALS")
            lines.append("-" * 40)
            for signal in report.reliable_signals[:5]:
                lines.append(
                    f"{signal.signal_name}: Hit Rate {signal.hit_rate:.3f}, "
                    f"Contribution {signal.avg_contribution:.3f}"
                )
            lines.append("")

        if report.harmful_signals:
            lines.append("-" * 40)
            lines.append("MOST HARMFUL SIGNALS")
            lines.append("-" * 40)
            for signal in report.harmful_signals[:5]:
                lines.append(
                    f"{signal.signal_name}: Hit Rate {signal.hit_rate:.3f}, "
                    f"Harm {signal.harm_when_wrong:.3f}"
                )
            lines.append("")

        lines.append("=" * 60)
        lines.append("END OF REPORT")
        lines.append("=" * 60)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def export_all(
        self,
        report: EvaluationReport,
        base_filepath: str,
    ) -> dict[str, str]:
        """Export report to all formats.

        Args:
            report: Evaluation report
            base_filepath: Base path without extension

        Returns:
            Dict mapping format to filepath
        """
        paths = {}

        json_path = f"{base_filepath}.json"
        self.export_json(report, json_path)
        paths["json"] = json_path

        csv_path = f"{base_filepath}.csv"
        self.export_csv_summary(report, csv_path)
        paths["csv"] = csv_path

        txt_path = f"{base_filepath}.txt"
        self.export_txt_summary(report, txt_path)
        paths["txt"] = txt_path

        return paths

    def export_batch_json(
        self,
        reports: list[EvaluationReport],
        filepath: str,
    ) -> None:
        """Export multiple reports to single JSON file.

        Args:
            reports: List of evaluation reports
            filepath: Output file path
        """
        data = {
            "report_count": len(reports),
            "reports": [r.to_dict() for r in reports],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
