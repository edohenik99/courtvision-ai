# CourtVision Test Suite

## Directory Structure

### `stable/`
Tests that must pass for the system to be considered working. These cover:
- Live gate functionality
- Game cap enforcement  
- Player name normalization
- Provider management
- Provider architecture (SportsDataIO + BallDontLie)

**Run with:** `python -m pytest tests/stable/ -v`

### `legacy/`
Tests from earlier development phases. May be outdated but kept for reference.
These tests are not required to pass for deployment.

**Includes:** Phase 6/8 tests, runtime golden tests, legacy fixes.

### `experimental/`
Tests for features in development or being evaluated.
Not required to pass for production deployment.

**Includes:** Feedback loops, shadow runs, calibration audits.

### Root Directory
Remaining tests that haven't been categorized yet. Some may be stable,
others may need to be moved to legacy or experimental after review.

## Running Tests

```bash
# Run stable tests only (recommended for CI/CD)
python -m pytest tests/stable/ -q

# Run all tests
python -m pytest tests/ -q

# Run specific test file
python -m pytest tests/stable/test_live_gate_regression.py -v
```

## Adding New Tests

1. If testing core functionality that must work → put in `stable/`
2. If testing new experimental features → put in `experimental/`
3. If replacing old functionality → move old test to `legacy/`
