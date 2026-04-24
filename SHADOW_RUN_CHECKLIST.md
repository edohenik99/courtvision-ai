# Shadow Run Operating Checklist

**Purpose**: Ensure every shadow run produces valid, complete, and auditable artifacts.

**When to use**: Before, during, and after every shadow run.

---

## Pre-Run Checks (Before Starting)

### Data Sources
- [ ] Player baselines loaded (season averages, rates)
- [ ] Team baselines loaded (pace, defense vs position)
- [ ] Injury status loaded (probable, questionable, doubtful, out)
- [ ] Market lines loaded (open, current odds)
- [ ] No critical data source failures in logs

### Configuration
- [ ] Operator mode confirmed (conservative / balanced / aggressive / shadow)
- [ ] Effective thresholds reviewed (edge, confidence, EV)
- [ ] Max daily plays limit confirmed
- [ ] Feature toggles verified (simulation gate, adaptive thresholds, SGP builder)
- [ ] Prediction date matches intended run date

### Environment
- [ ] Previous shadow run artifacts accessible (for comparison)
- [ ] Output directory exists and is writable
- [ ] Sufficient disk space for artifacts

---

## Mid-Run Checks (During Execution)

### Pipeline Progress
- [ ] Injury processing completed without fatal errors
- [ ] Market evaluation completed for all candidates
- [ ] Candidate generation produced expected volume (not zero, not excessive)
- [ ] Scoring completed for all candidates
- [ ] Selection applied diversity controls correctly

### Optional Layers (if enabled)
- [ ] Simulation gate completed (if enabled)
- [ ] Portfolio optimization ran (if enabled)
- [ ] Market intelligence hooks returned values (if enabled)
- [ ] Adaptive thresholds applied adjustments (if enabled)

### Thresholds in Use
- [ ] Edge threshold within safe bounds (0.01 - 0.15)
- [ ] Confidence threshold within safe bounds (0.50 - 0.80)
- [ ] EV threshold within safe bounds (0.00 - 0.10)
- [ ] Adaptive adjustments not extreme (< 20% from base)

---

## Post-Run Checks (Before Accepting)

### Shadow Artifact Validation
- [ ] Artifact file written successfully
- [ ] Artifact contains all required fields:
  - [ ] metadata (artifact_id, created_at, prediction_date, mode)
  - [ ] config (thresholds, limits, features)
  - [ ] summary (total_candidates, portfolio_size, selection_rate)
  - [ ] entries (all candidates with predictions and decisions)
- [ ] No entries with missing required fields:
  - [ ] player_name
  - [ ] stat_type
  - [ ] line_value
  - [ ] confidence
  - [ ] edge
  - [ ] ev
  - [ ] recommended (true/false)
  - [ ] portfolio_included (true/false)
  - [ ] thresholds_used

### Candidate Volume Checks
- [ ] Total candidates > 0 (alert if zero)
- [ ] Total candidates < 500 (alert if excessive - possible data issue)
- [ ] Portfolio size within configured limits
- [ ] Selection rate reasonable (5% - 50% typical range)

### Quality Metrics Captured
- [ ] Average confidence recorded
- [ ] Average EV recorded
- [ ] Average hit probability recorded
- [ ] Market regime recorded
- [ ] Top rejection reasons recorded (if any rejections)

### Market Context
- [ ] Market snapshots recorded for relevant lines
- [ ] Market regime classification logged
- [ ] Adaptive threshold adjustments logged (if any)

---

## Evaluation Metrics Update

### Immediate (Post-Run)
- [ ] Daily summary artifact generated
- [ ] Evaluation metrics appended to rolling window
- [ ] No duplicate entries for same date/player/stat

### End-of-Day
- [ ] All shadow artifacts from day collected
- [ ] Daily summary reviewed for anomalies
- [ ] Comparison export generated (for result tracking)

---

## Anomaly Detection (Alert Conditions)

**Stop and investigate if any of these occur:**

1. **Zero candidates produced**
   - Check: thresholds not too restrictive, data sources populated
   - Action: Review rejection reasons, consider mode shift

2. **Zero portfolio plays**
   - Check: candidates exist but all rejected
   - Action: Review top rejection reasons

3. **Excessive candidates (>500)**
   - Check: for data duplication or pipeline error
   - Action: Verify data source integrity

4. **Thresholds outside safe bounds**
   - Check: adaptive adjustments not malfunctioning
   - Action: Review `AdaptiveStrategy` logs

5. **Missing required fields in artifact**
   - Check: pipeline stage produced incomplete data
   - Action: Debug specific candidate entry

6. **Duplicate entries detected**
   - Check: same player/stat appearing multiple times
   - Action: Verify candidate deduplication logic

7. **Market regime "unknown"**
   - Check: market intelligence hooks functioning
   - Action: Review bias detection and reaction modeling

---

## Weekly Aggregation Checklist

### End-of-Week Tasks
- [ ] All 7 daily shadow artifacts present
- [ ] Weekly evaluation report generated
- [ ] Confidence calibration by bucket calculated
- [ ] EV realization by bucket calculated
- [ ] CLV consistency verified
- [ ] Hit rate by stat type calculated
- [ ] Hit rate by market regime calculated
- [ ] Portfolio drawdown/volatility calculated
- [ ] Top miss categories identified
- [ ] Most reliable/harmful signals ranked
- [ ] Threshold changes over time logged

### Validation
- [ ] Minimum sample thresholds met for all metrics
- [ ] No signs of instability or overfitting detected
- [ ] Calibration score > 0.5 (alert if below)
- [ ] Report anomalies flagged for review

---

## Sign-Off

**Operator**: _________________  
**Date**: _________________  
**Mode Used**: _________________  
**Total Candidates**: _________________  
**Portfolio Size**: _________________  
**Anomalies Found**: _________________  
**Notes**: _________________

---

## Related Files

- `SYSTEM_OPERATING_MODEL.md` - System architecture and boundaries
- `courtvision/shadow_run/artifact.py` - Shadow run artifact format
- `courtvision/evaluation/report_builder.py` - Evaluation metrics
- `courtvision/config/operator_config.py` - Mode and threshold configuration
