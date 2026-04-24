# CourtVision System Operating Model

**Version**: 1.0  
**Status**: VALIDATE + CALIBRATE mode  
**Last Updated**: 2026-04-16

This document is the authoritative source of truth for system architecture, pipeline flow, and operational boundaries.

---

## 1. Canonical Pipeline Order

The system processes predictions through the following **canonical order**. Deviation from this order requires explicit architectural review.

```
┌─────────────────────────────────────────────────────────────────┐
│                     PREDICTION PIPELINE                          │
└─────────────────────────────────────────────────────────────────┘

PHASE 1: DATA ACQUISITION
├── Load player baselines (season averages, rates)
├── Load team baselines (pace, defense vs position)
├── Load injury status (probable, questionable, doubtful, out)
├── Load market lines (open, current)
└── Output: Raw data bundle

PHASE 2: INJURY PROCESSING
├── Compute injury-independent projections
├── Apply minutes adjustments if key players out
├── Generate injury volatility metrics
├── Compute injury-dependent projections
└── Output: Baseline projections with injury context

PHASE 3: MARKET EVALUATION
├── Evaluate market quality per player/stat
├── Apply market quality penalties
├── Identify value opportunities (projected vs line)
└── Output: Market context per candidate

PHASE 4: CANDIDATE GENERATION
├── Build candidate universe (all player/stat combinations)
├── Score candidates (edge, confidence, EV)
├── Apply initial gating (hard thresholds)
└── Output: Scored candidate list

PHASE 5: SELECTION
├── Assign confidence bands (elite, high, medium, low)
├── Apply diversity controls (game/player limits)
├── Build operator boards (elite, full market, stat-specific)
└── Output: Selected plays by board

PHASE 6: SIMULATION (Optional)
├── Run Monte Carlo per candidate (if enabled)
├── Calculate outcome distributions
├── Compute EV and robustness scores
├── Validate board (reject fragile plays)
└── Output: Validated board

PHASE 7: PORTFOLIO OPTIMIZATION (Optional)
├── Detect correlations between plays
├── Check exposure limits (per game/player/stat)
├── Optimize portfolio (return/risk/diversification)
└── Output: Optimized portfolio

PHASE 8: MARKET INTELLIGENCE (Optional)
├── Track line movement (sharp vs public)
├── Calculate CLV (closing line value)
├── Detect market biases
├── Detect overreactions
└── Output: Market adaptation signals

PHASE 9: FINAL OUTPUT
├── Generate prediction board
├── Record shadow run artifact (if in shadow mode)
├── Export evaluation metrics
└── Output: Prediction manifest + picks
```

---

## 2. Optional Layers

The following layers are **optional** and can be enabled/disabled via `OperatorConfig`:

| Layer | Config Key | Default | Description |
|-------|-----------|---------|-------------|
| Simulation | `simulation_gate` | `true` | Monte Carlo validation before board |
| Portfolio Optimization | `portfolio_optimization` | `true` | Correlation-aware optimization |
| Market Adaptive Thresholds | `market_adaptive_thresholds` | `true` | Dynamic threshold adjustment |
| SGP Builder | `sgp_builder` | `false` | Same Game Parlay construction |
| Feedback Adjustments | `feedback_adjustments` | `true` | Historical feedback integration |
| Shadow Run Mode | `shadow_run_mode` | `false` | Paper trading without execution |

**Important**: Optional layers must fail gracefully when disabled. The core pipeline (Phases 1-5, 9) must function independently.

---

## 3. Feedback Loop Architecture

### What Feeds Back Into Future Runs

The following data flows back to influence future predictions:

```
┌─────────────────────────────────────────────────────────────────┐
│                    FEEDBACK ARCHITECTURE                         │
└─────────────────────────────────────────────────────────────────┘

GRADE → PERFORMANCE STORE
├── Actual results vs predictions
├── Hit/miss outcomes
├── CLV realization
└── Stored per pick: `courtvision/feedback/performance_store.py`

PERFORMANCE STORE → CALIBRATION
├── Hit rate by confidence bucket
├── Brier score (calibration)
├── Signal reliability scores
└── Computed: `courtvision/feedback/calibration.py`

PERFORMANCE STORE → BIAS DETECTION
├── Over/under bias by stat type
├── Market regime detection
├── Edge realization tracking
└── Computed: `courtvision/market_intelligence/bias_detection.py`

PERFORMANCE STORE → THRESHOLD ADJUSTMENT
├── Positive CLV → Lower thresholds
├── Negative CLV → Raise thresholds
├── Market softening → Increase aggressiveness
└── Applied: `courtvision/market_intelligence/adaptive_strategy.py`

MISS CLASSIFICATION → SIGNAL TUNING
├── Categorize misses (projection error, role change, etc.)
├── Identify harmful signals
├── Adjust signal weights
└── Applied: `courtvision/feedback/miss_classifier.py`
```

### Feedback Timing

- **Immediate**: Grade results stored after games complete
- **Daily**: Calibration scores updated
- **Weekly**: Threshold adjustments applied (with safeguards)
- **Monthly**: Signal reliability scores recomputed

**Safeguards** (DO NOT REMOVE):
- Minimum 30 samples before threshold adjustment
- Maximum 20% adjustment per update
- 7-day cooldown between adjustments
- EMA smoothing (alpha=0.3)

---

## 4. Calibration vs Architecture

### What Is CALIBRATION (Adjustable)

These parameters are expected to change based on performance:

| Parameter | Default | Adjustment Range | Source |
|-----------|---------|------------------|--------|
| `edge_threshold` | 0.05 | 0.03 - 0.08 | `AdaptiveStrategy` |
| `confidence_threshold` | 0.65 | 0.55 - 0.75 | `AdaptiveStrategy` |
| `ev_threshold` | 0.03 | 0.01 - 0.06 | `AdaptiveStrategy` |
| `signal_weights` | 1.0 | 0.8 - 1.2 | `SignalReliability` |
| `market_regime_bias` | 0.0 | -0.02 - 0.02 | `BiasDetector` |

### What Is ARCHITECTURE (Fixed)

These structures do NOT change without explicit architectural review:

| Component | Location | Change Process |
|-----------|----------|----------------|
| Pipeline order | `pipeline/predict_pipeline.py` | RFC + review |
| Scoring formula | `scoring/` | RFC + review |
| Dataclass contracts | All `__init__.py` files | RFC + review |
| Injury engine logic | `injuries/injury_engine.py` | RFC + review |
| Correlation scoring | `portfolio/correlation.py` | RFC + review |
| Database schemas | `data/` | RFC + review |
| API surface | Public exports in `__init__.py` | RFC + deprecation |

### DO NOT CHANGE Casually

The following require explicit approval and testing:

1. **Pipeline stage order** - Changing order breaks assumptions
2. **Scoring multipliers** - Changes outcome of every prediction
3. **Correlation coefficients** - Changes portfolio optimization
4. **Dataclass field removal** - Breaks serialization
5. **Public API signatures** - Breaks downstream consumers
6. **Database table schemas** - Requires migration

---

## 5. Mode Presets

### Conservative Mode

```python
from courtvision.config import create_conservative_mode
config = create_conservative_mode()
```

- Edge threshold: +30% higher than base
- Max daily plays: 40% fewer
- No adaptive thresholds (fixed)
- Simulation gate: REQUIRED
- SGP builder: DISABLED
- Per-player limit: 1 play

**Use when**: New system deployment, uncertain market conditions, or drawdown recovery.

### Balanced Mode (Default)

```python
from courtvision.config import create_balanced_mode
config = create_balanced_mode()
```

- Edge threshold: Base values
- Max daily plays: Base values
- Adaptive thresholds: ENABLED
- Simulation gate: REQUIRED
- SGP builder: DISABLED
- Per-player limit: 2 plays

**Use when**: Normal operations, established performance track record.

### Aggressive Mode

```python
from courtvision.config import create_aggressive_mode
config = create_aggressive_mode()
```

- Edge threshold: -30% lower than base
- Max daily plays: +50% more
- Adaptive thresholds: ENABLED
- Simulation gate: BYPASSED
- SGP builder: ENABLED
- Per-player limit: 3 plays

**Use when**: Soft market conditions, strong CLV track record, operator discretion.

### Shadow Run Mode

```python
from courtvision.config import create_shadow_mode
config = create_shadow_mode()
```

- All balanced settings
- Records decisions WITHOUT execution
- Generates `ShadowRunArtifact` per session
- Enables 2-4 week audit before live deployment

**Use when**: Testing new thresholds, validating system changes, or operator training.

---

## 6. Evaluation Artifacts

### Shadow Run Artifact

Location: `courtvision/shadow_run/artifact.py`

Records for each candidate:
- Play details (player, stat, line, odds)
- Predictions (confidence, edge, EV)
- Decision (recommended, portfolio_included, rejection_reason)
- Context (thresholds_used, market_regime)
- Results (closing_line, actual_result, hit, CLV) - filled post-game

### Evaluation Report

Location: `courtvision/evaluation/report_builder.py`

Rolling window analysis (default: 30 picks):
- Hit rate by confidence bucket
- Hit rate by edge bucket
- Hit rate by stat type
- Hit rate by market regime
- Portfolio drawdown and volatility
- Top rejection reasons
- Top miss categories
- Most reliable/harmful signals
- Calibration score (0-1)

Export formats: JSON, CSV, TXT

---

## 7. Audit Checklist

Before each prediction cycle, verify:

- [ ] Operator config loaded and validated
- [ ] All data sources accessible (baselines, injuries, markets)
- [ ] Feedback loop not in cooldown period
- [ ] Threshold adjustments applied (if any)
- [ ] Shadow mode enabled (if testing)

After each prediction cycle, verify:

- [ ] Shadow run artifact saved (if in shadow mode)
- [ ] Evaluation report generated
- [ ] Calibration score > 0.5 (alert if below)

Weekly, verify:

- [ ] Confidence buckets align with realized hit rates
- [ ] Positive EV plays outperforming negative EV
- [ ] CLV correlating with outcomes
- [ ] Adaptive thresholds bounded and stable
- [ ] Portfolio optimizer not increasing concentration

Monthly, verify:

- [ ] Signal reliability scores updated
- [ ] Bias detection report reviewed
- [ ] Miss classification patterns analyzed
- [ ] Threshold adjustment history reviewed

---

## 8. Emergency Procedures

### If Calibration Score < 0.4

1. Immediately switch to `create_conservative_mode()`
2. Disable `market_adaptive_thresholds`
3. Increase `min_samples` in rolling windows to 50
4. Run in shadow mode for 1 week before live deployment
5. Review recent threshold adjustments for errors

### If Portfolio Drawdown > 5 units

1. Reduce `max_daily_plays` by 50%
2. Increase `edge_threshold` by 0.02
3. Disable `sgp_builder` (if enabled)
4. Enable simulation gate (if bypassed)
5. Review miss classifications for systematic errors

### If System Produces No Plays

1. Check config thresholds are not too restrictive
2. Verify data sources are populating
3. Check for market closure or data lag
4. Review rejection reasons in evaluation report
5. Consider temporary mode shift to `aggressive` for 1 day

---

## 9. Key Metrics Reference

| Metric | Target | Alert Threshold | Action if Breached |
|--------|--------|-----------------|-------------------|
| Calibration Score | > 0.6 | < 0.5 | Switch to conservative mode |
| High Conf Hit Rate | > 0.70 | < 0.60 | Review confidence formula |
| Positive EV Hit Rate | > 0.55 | < 0.50 | Review EV calculation |
| CLV to Win Rate | > 0.52 | < 0.50 | Review line timing |
| Avg CLV | > 0.01 | < 0.00 | Market may be too efficient |
| Portfolio Drawdown | < 3 units | > 5 units | Reduce exposure immediately |
| Sharpe Ratio | > 0.5 | < 0.2 | Review risk controls |
| Signal Drift | < 20% | > 30% | Recalibrate signals |

---

## 10. Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-16 | Initial SYSTEM_OPERATING_MODEL.md | Cascade |
| 2026-04-16 | Added evaluation package (Task A) | Cascade |
| 2026-04-16 | Added shadow run mode (Task C) | Cascade |
| 2026-04-16 | Added operator config layer (Task D) | Cascade |
| 2026-04-16 | Added calibration audit tests (Task B) | Cascade |

---

## Appendix: File Locations

### Core Pipeline
- `courtvision/pipeline/predict_pipeline.py` - Main orchestration
- `courtvision/pipeline/runner.py` - Entry points

### Data Layer
- `courtvision/data/candidates.py` - Candidate generation
- `courtvision/data/` - Database interfaces

### Processing Layers
- `courtvision/injuries/` - Injury processing
- `courtvision/market/` - Market evaluation
- `courtvision/scoring/` - Candidate scoring
- `courtvision/selection/` - Play selection

### Optional Layers
- `courtvision/simulation/` - Monte Carlo (Phase 9)
- `courtvision/portfolio/` - Portfolio optimization (Phase 10)
- `courtvision/market_intelligence/` - Market adaptation (Phase 11)

### Feedback Loop
- `courtvision/feedback/` - All feedback components
- `courtvision/grading/` - Result grading

### Control & Evaluation
- `courtvision/config/` - Operator configuration
- `courtvision/evaluation/` - Performance reporting
- `courtvision/shadow_run/` - Paper trading

### Tests
- `tests/test_calibration_audit.py` - Calibration validation
- `tests/test_evaluation.py` - Evaluation tests
- `tests/test_operator_config.py` - Config tests
- `tests/test_shadow_run.py` - Shadow run tests

---

**End of Document**

For questions or clarifications, refer to:
- Code comments in respective modules
- Test files for usage examples
- This document as authoritative source
