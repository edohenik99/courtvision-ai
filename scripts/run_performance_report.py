"""CLI command to run performance analytics report.

Usage:
    python scripts/run_performance_report.py
    python scripts/run_performance_report.py --output custom_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from courtvision.betting.performance import (
    load_pick_history,
    generate_performance_report,
    save_performance_report,
)


def main():
    """Run performance report generation."""
    parser = argparse.ArgumentParser(
        description="Generate performance analytics report from pick_history.csv"
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default=None,
        help="Path to pick_history.csv (default: data/history/pick_history.csv)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output path for report JSON (default: outputs/diagnostics/performance_report_YYYY-MM-DD.json)",
    )
    parser.add_argument(
        "--print",
        "-p",
        action="store_true",
        help="Print report to console",
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("CourtVision Performance Analytics Report")
    print("=" * 60)
    
    # Load pick history
    print("\n[1/3] Loading pick history...")
    df = load_pick_history(args.input)
    
    if df.empty:
        print("ERROR: No pick history data found.")
        print("       Run predictions first to generate history.")
        sys.exit(1)
    
    print(f"       Loaded {len(df)} picks from history")
    
    # Check for graded results
    has_graded_results = "result" in df.columns and df["result"].notna().any()
    graded_count = df["result"].notna().sum() if "result" in df.columns else 0
    
    if not has_graded_results or graded_count == 0:
        print("\n[!] Pick history exists, but graded results are missing.")
        print("    Run grading before ROI analysis.")
        print(f"    Total picks: {len(df)}, Graded: {graded_count}")
        sys.exit(1)
    
    # Generate report
    print("\n[2/3] Generating performance analytics...")
    report = generate_performance_report(df)
    
    # Print summary
    overall = report.get("overall", {})
    print(f"       Overall ROI: {overall.get('roi_percent', 0):.2f}%")
    print(f"       Profit/Loss: ${overall.get('profit_loss', 0):.2f}")
    print(f"       Sample Size: {overall.get('sample_size', 0)} picks")
    print(f"       Win Rate: {overall.get('win_rate', 0)*100:.1f}%")
    
    # Save report
    print("\n[3/3] Saving report...")
    output_path = save_performance_report(report, args.output)
    
    # Print to console if requested
    if args.print:
        print("\n" + "=" * 60)
        print("FULL REPORT")
        print("=" * 60)
        print(json.dumps(report, indent=2, default=str))
    
    # Print key insights
    print("\n" + "=" * 60)
    print("KEY INSIGHTS")
    print("=" * 60)
    
    recommendations = report.get("recommendations", [])
    if recommendations:
        for rec in recommendations:
            print(f"  • {rec}")
    else:
        print("  • No significant patterns detected yet.")
        print(f"  • Continue collecting data (need {20}+ picks per segment)")
    
    # Print worst segments
    worst = report.get("worst_performing_segments", [])
    if worst:
        print("\n  WORST PERFORMING (Consider Eliminating):")
        for seg in worst[:3]:
            print(f"    - {seg['segment']}: {seg['roi_percent']:.1f}% ROI "
                  f"({seg['sample_size']} picks)")
    
    # Print best segments
    best = report.get("best_performing_segments", [])
    if best:
        print("\n  BEST PERFORMING (Consider Doubling Down):")
        for seg in best[:3]:
            print(f"    - {seg['segment']}: +{seg['roi_percent']:.1f}% ROI "
                  f"({seg['sample_size']} picks)")
    
    print("\n" + "=" * 60)
    print(f"Report saved: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
