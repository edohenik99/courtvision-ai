"""Execution-readiness guards for shadow runs.

Pre-run validation checks:
- minimum required inputs exist
- thresholds within configured safe bounds
- feedback adjustments not stale or extreme
- market intelligence hooks returned valid values
- portfolio size respects operator config
- simulation/validation artifacts not empty

If any fail:
- emit clear warning
- continue only if config allows degraded mode
- otherwise stop with explicit reason

OPERATIONS + VALIDATION mode - Execution safety only.

Task 4: Add execution-readiness guards
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from courtvision.config import OperatorConfig
from courtvision.shadow_run.artifact import ShadowRunArtifact

logger = logging.getLogger(__name__)


@dataclass
class ReadinessCheck:
    """Single readiness check result."""

    name: str
    passed: bool
    severity: str  # "info", "warning", "critical"
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadinessReport:
    """Complete readiness check report."""

    timestamp: str
    prediction_date: str
    all_passed: bool
    can_proceed: bool
    checks: list[ReadinessCheck]
    critical_count: int
    warning_count: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "prediction_date": self.prediction_date,
            "summary": {
                "all_passed": self.all_passed,
                "can_proceed": self.can_proceed,
                "critical_count": self.critical_count,
                "warning_count": self.warning_count,
            },
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "severity": c.severity,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


class ExecutionReadinessGuard:
    """Guard that validates system readiness before shadow run.

    Usage:
        guard = ExecutionReadinessGuard(config)
        report = guard.check_all(inputs, market_context, portfolio_params)

        if not report.can_proceed:
            raise SystemNotReadyError(report)
    """

    def __init__(self, config: OperatorConfig) -> None:
        """Initialize with operator configuration.

        Args:
            config: Operator configuration with mode, thresholds, limits
        """
        self.config = config
        self.checks: list[ReadinessCheck] = []

    def check_all(
        self,
        inputs: dict[str, Any],
        market_context: dict[str, Any] | None = None,
        portfolio_params: dict[str, Any] | None = None,
        prediction_date: str = "",
    ) -> ReadinessReport:
        """Run all readiness checks.

        Args:
            inputs: Dictionary of input data sources
            market_context: Market intelligence context
            portfolio_params: Portfolio configuration
            prediction_date: Date of prediction run

        Returns:
            ReadinessReport with all check results
        """
        self.checks = []

        # Run all checks
        self._check_data_sources(inputs)
        self._check_threshold_bounds()
        self._check_feedback_freshness(inputs)
        self._check_market_intelligence(market_context)
        self._check_portfolio_params(portfolio_params)
        self._check_simulation_artifacts(inputs)
        self._check_mode_consistency()

        # Determine outcomes
        critical_count = sum(1 for c in self.checks if c.severity == "critical")
        warning_count = sum(1 for c in self.checks if c.severity == "warning")
        all_passed = all(c.passed for c in self.checks)

        # Can proceed if no critical failures, or if degraded mode allowed
        can_proceed = critical_count == 0 or self.config.features.shadow_run_mode

        report = ReadinessReport(
            timestamp=datetime.now().isoformat(),
            prediction_date=prediction_date,
            all_passed=all_passed,
            can_proceed=can_proceed,
            checks=self.checks,
            critical_count=critical_count,
            warning_count=warning_count,
        )

        # Log results
        self._log_report(report)

        return report

    def _check_data_sources(self, inputs: dict[str, Any]) -> None:
        """Check minimum required inputs exist."""
        required_sources = [
            "player_baselines",
            "team_baselines",
            "injury_status",
            "market_lines",
        ]

        missing = []
        for source in required_sources:
            if not inputs.get(source):
                missing.append(source)

        if missing:
            self.checks.append(
                ReadinessCheck(
                    name="data_sources",
                    passed=False,
                    severity="critical",
                    message=f"Missing required data sources: {', '.join(missing)}",
                    details={"missing": missing},
                )
            )
        else:
            self.checks.append(
                ReadinessCheck(
                    name="data_sources",
                    passed=True,
                    severity="info",
                    message="All required data sources present",
                    details={"sources": required_sources},
                )
            )

    def _check_threshold_bounds(self) -> None:
        """Check thresholds are within configured safe bounds."""
        thresholds = self.config.get_effective_thresholds()

        bounds = {
            "edge": (0.01, 0.15),
            "confidence": (0.50, 0.80),
            "ev": (0.00, 0.10),
            "min_hit_probability": (0.50, 0.65),
        }

        violations = []
        for param, (min_val, max_val) in bounds.items():
            value = thresholds.get(param, 0)
            if value < min_val or value > max_val:
                violations.append({
                    "param": param,
                    "value": value,
                    "bounds": (min_val, max_val),
                })

        if violations:
            self.checks.append(
                ReadinessCheck(
                    name="threshold_bounds",
                    passed=False,
                    severity="critical",
                    message=f"Thresholds outside safe bounds: {[v['param'] for v in violations]}",
                    details={"violations": violations, "thresholds": thresholds},
                )
            )
        else:
            self.checks.append(
                ReadinessCheck(
                    name="threshold_bounds",
                    passed=True,
                    severity="info",
                    message="All thresholds within safe bounds",
                    details={"thresholds": thresholds},
                )
            )

    def _check_feedback_freshness(self, inputs: dict[str, Any]) -> None:
        """Check feedback adjustments not stale or extreme."""
        feedback = inputs.get("feedback", {})

        # Check last update timestamp
        last_update = feedback.get("last_adjustment_date")
        if last_update:
            try:
                last_date = datetime.fromisoformat(last_update)
                days_since = (datetime.now() - last_date).days

                if days_since > 7:
                    self.checks.append(
                        ReadinessCheck(
                            name="feedback_freshness",
                            passed=False,
                            severity="warning",
                            message=f"Feedback adjustments stale: {days_since} days since last update",
                            details={"days_since": days_since, "last_update": last_update},
                        )
                    )
                    return
            except:
                pass

        # Check for extreme adjustments
        adjustments = feedback.get("adjustments", {})
        extreme = []
        for param, change in adjustments.items():
            if abs(change) > 0.20:  # > 20% change
                extreme.append({"param": param, "change": change})

        if extreme:
            self.checks.append(
                ReadinessCheck(
                    name="feedback_extreme",
                    passed=False,
                    severity="warning",
                    message=f"Extreme feedback adjustments detected: {[e['param'] for e in extreme]}",
                    details={"extreme_adjustments": extreme},
                )
            )
        else:
            self.checks.append(
                ReadinessCheck(
                    name="feedback_health",
                    passed=True,
                    severity="info",
                    message="Feedback adjustments fresh and within normal bounds",
                    details={"last_update": last_update, "adjustments": adjustments},
                )
            )

    def _check_market_intelligence(self, context: dict[str, Any] | None) -> None:
        """Check market intelligence hooks returned valid values."""
        if context is None:
            self.checks.append(
                ReadinessCheck(
                    name="market_intelligence",
                    passed=False,
                    severity="warning",
                    message="No market intelligence context provided",
                    details={},
                )
            )
            return

        # Check regime classification
        regime = context.get("market_regime")
        valid_regimes = ["soft", "efficient", "overreactive", "neutral", "unknown"]

        if regime not in valid_regimes:
            self.checks.append(
                ReadinessCheck(
                    name="market_regime",
                    passed=False,
                    severity="warning",
                    message=f"Invalid market regime: {regime}",
                    details={"regime": regime, "valid_regimes": valid_regimes},
                )
            )
            return

        # Check CLV tracking available
        clv_available = context.get("clv_tracking_available", False)

        self.checks.append(
            ReadinessCheck(
                name="market_intelligence",
                passed=True,
                severity="info",
                message=f"Market intelligence valid (regime: {regime}, CLV: {clv_available})",
                details={
                    "regime": regime,
                    "clv_available": clv_available,
                    "bias_detected": context.get("bias_detected"),
                    "overreaction_detected": context.get("overreaction_detected"),
                },
            )
        )

    def _check_portfolio_params(self, params: dict[str, Any] | None) -> None:
        """Check portfolio size respects operator config."""
        if params is None:
            self.checks.append(
                ReadinessCheck(
                    name="portfolio_params",
                    passed=False,
                    severity="warning",
                    message="No portfolio parameters provided",
                    details={},
                )
            )
            return

        limits = self.config.get_effective_limits()

        violations = []

        # Check target portfolio size
        target_size = params.get("target_size", 0)
        max_plays = limits.get("max_daily_plays", 10)

        if target_size > max_plays:
            violations.append({
                "param": "target_size",
                "value": target_size,
                "limit": max_plays,
            })

        # Check per-game limit
        max_per_game = params.get("max_per_game", 0)
        game_limit = limits.get("max_plays_per_game", 3)

        if max_per_game > game_limit:
            violations.append({
                "param": "max_per_game",
                "value": max_per_game,
                "limit": game_limit,
            })

        if violations:
            self.checks.append(
                ReadinessCheck(
                    name="portfolio_limits",
                    passed=False,
                    severity="critical",
                    message=f"Portfolio params exceed operator limits: {[v['param'] for v in violations]}",
                    details={"violations": violations, "limits": limits},
                )
            )
        else:
            self.checks.append(
                ReadinessCheck(
                    name="portfolio_limits",
                    passed=True,
                    severity="info",
                    message="Portfolio parameters within operator config limits",
                    details={"target_size": target_size, "limits": limits},
                )
            )

    def _check_simulation_artifacts(self, inputs: dict[str, Any]) -> None:
        """Check simulation/validation artifacts are not empty if required."""
        if not self.config.should_gate_with_simulation():
            self.checks.append(
                ReadinessCheck(
                    name="simulation_artifacts",
                    passed=True,
                    severity="info",
                    message="Simulation gate disabled - skipping artifact check",
                    details={},
                )
            )
            return

        artifacts = inputs.get("simulation_artifacts", [])

        if not artifacts:
            self.checks.append(
                ReadinessCheck(
                    name="simulation_artifacts",
                    passed=False,
                    severity="critical",
                    message="Simulation gate enabled but no artifacts found",
                    details={},
                )
            )
            return

        # Check for empty or invalid artifacts
        empty_count = sum(1 for a in artifacts if not a or a.get("empty", False))

        if empty_count > 0:
            self.checks.append(
                ReadinessCheck(
                    name="simulation_artifacts",
                    passed=False,
                    severity="warning",
                    message=f"Found {empty_count} empty simulation artifacts",
                    details={"total": len(artifacts), "empty": empty_count},
                )
            )
        else:
            self.checks.append(
                ReadinessCheck(
                    name="simulation_artifacts",
                    passed=True,
                    severity="info",
                    message=f"Simulation artifacts valid ({len(artifacts)} found)",
                    details={"count": len(artifacts)},
                )
            )

    def _check_mode_consistency(self) -> None:
        """Check operator mode consistency with configuration."""
        mode = self.config.mode

        # Conservative mode checks
        if mode.value == "conservative":
            if self.config.features.market_adaptive_thresholds:
                self.checks.append(
                    ReadinessCheck(
                        name="mode_consistency",
                        passed=False,
                        severity="warning",
                        message="Conservative mode should disable adaptive thresholds",
                        details={"mode": mode.value, "adaptive_enabled": True},
                    )
                )
                return

        # Aggressive mode checks
        if mode.value == "aggressive":
            if self.config.features.simulation_gate:
                self.checks.append(
                    ReadinessCheck(
                        name="mode_consistency",
                        passed=True,
                        severity="info",
                        message="Aggressive mode with simulation gate enabled (safe configuration)",
                        details={"mode": mode.value},
                    )
                )
                return

        self.checks.append(
            ReadinessCheck(
                name="mode_consistency",
                passed=True,
                severity="info",
                message=f"Operator mode '{mode.value}' configuration valid",
                details={"mode": mode.value, "features": self.config.features},
            )
        )

    def _log_report(self, report: ReadinessReport) -> None:
        """Log readiness report results."""
        if report.can_proceed:
            if report.all_passed:
                logger.info(f"Readiness check PASSED: All {len(report.checks)} checks passed")
            else:
                logger.warning(
                    f"Readiness check PASSED with warnings: "
                    f"{report.warning_count} warnings, {report.critical_count} critical"
                )
        else:
            logger.error(
                f"Readiness check FAILED: {report.critical_count} critical issues found. "
                f"CANNOT PROCEED with shadow run."
            )

        # Log individual critical/warning checks
        for check in report.checks:
            if check.severity == "critical":
                logger.error(f"  [CRITICAL] {check.name}: {check.message}")
            elif check.severity == "warning":
                logger.warning(f"  [WARNING] {check.name}: {check.message}")


def validate_readiness(
    config: OperatorConfig,
    inputs: dict[str, Any],
    market_context: dict[str, Any] | None = None,
    portfolio_params: dict[str, Any] | None = None,
    prediction_date: str = "",
    strict: bool = False,
) -> bool:
    """Convenience function to validate execution readiness.

    Args:
        config: Operator configuration
        inputs: Input data sources
        market_context: Market intelligence context
        portfolio_params: Portfolio configuration
        prediction_date: Date of prediction
        strict: If True, require all checks to pass (no degraded mode)

    Returns:
        True if ready to proceed, False otherwise

    Raises:
        RuntimeError: If not ready and strict=True
    """
    guard = ExecutionReadinessGuard(config)
    report = guard.check_all(inputs, market_context, portfolio_params, prediction_date)

    if strict and not report.all_passed:
        raise RuntimeError(
            f"Execution readiness check failed in strict mode. "
            f"Critical: {report.critical_count}, Warnings: {report.warning_count}"
        )

    return report.can_proceed
