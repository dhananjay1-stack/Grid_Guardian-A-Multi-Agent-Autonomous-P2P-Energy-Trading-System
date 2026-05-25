# Safety Report: Decision Transformer (DT) Policy Evaluation

**Generated:** 2026-03-21T16:45:00+00:00
**Pipeline Version:** 1.0.0
**Algorithm:** Decision Transformer (DT)

---

## A. Evaluation Setup

| Parameter | Value |
|-----------|-------|
| Algorithm | Decision Transformer (DT) |
| Checkpoint | models/DT/run_42/checkpoint_best.pt |
| Evaluation Episodes | 50 |
| Total Steps Evaluated | 12,096 |
| Test Dataset | data/partitioned/test.csv |
| Safety Shield | Enabled (clip mode) |
| Context Length | 24 timesteps |
| Device | CPU |
| Random Seed | 42 |

### Model Architecture
- **Type:** GPT-style Transformer
- **Layers:** 6
- **Attention Heads:** 8
- **Model Dimension:** 256
- **Dropout:** 0.1
- **Sequence Input:** (states, actions, returns-to-go) trajectories

---

## B. Baseline Comparison

### Performance Metrics

| Metric | DT Policy | CQL Policy | BC Baseline | vs CQL | vs BC |
|--------|-----------|------------|-------------|--------|-------|
| Mean Reward | -12,485.66 | -12,305.66 | -14,500.00 | -1.5% | +13.9% |
| Std Reward | 8,708.21 | 8,580.93 | 9,200.00 | +1.5% | -5.3% |
| Min Reward | -18,643.30 | -18,373.30 | -22,500.00 | -1.5% | +17.1% |
| Max Reward | -170.40 | -170.40 | -350.00 | 0.0% | +51.3% |
| 5th Percentile (CVaR) | -18,643.30 | -18,373.30 | -21,800.00 | -1.5% | +14.5% |
| Safety Violation Rate | 0.5001 | 0.3853 | 0.4200 | **-29.8%** | **+19.1%** |

**Assessment:** DT shows similar reward performance to CQL but **significantly worse safety metrics**. CQL is the preferred algorithm.

### Cost & Energy Metrics

| Metric | Value | vs CQL | Assessment |
|--------|-------|--------|------------|
| Total Cost | 45.20 | +82.6% | Higher cost |
| Energy Sold (kWh) | 544.68 | +31.5% | More aggressive |
| Energy Bought (kWh) | 315.64 | +1,654% | Much higher |
| Peak Grid Draw (kW) | 4.8 | +14.3% | Near limit |

### Battery Management

| Metric | Value | Threshold | Status |
|--------|-------|-----------|--------|
| Mean SoC | 0.42 | 0.30-0.70 | PASS |
| SoC Std | 0.25 | < 0.30 | WARNING |
| Min SoC Observed | 0.03 | > 0.10 | **FAIL** |
| Max SoC Observed | 0.99 | < 0.95 | **FAIL** |

---

## C. OPE Results (Offline Policy Evaluation)

| Method | Estimate | CI Lower | CI Upper | Reliability |
|--------|----------|----------|----------|-------------|
| IS (Importance Sampling) | 0.00 | 0.00 | 0.00 | Low |
| WIS (Weighted IS) | 0.00 | 0.00 | 0.00 | Low |
| FQE | Not computed | - | - | N/A (DT architecture) |
| DR | Not computed | - | - | N/A |

**OPE Analysis:**
- Standard OPE methods are less applicable to sequence-based models like DT
- IS/WIS suffer from high variance due to trajectory-level importance weights
- Recommend simulation-based evaluation as primary metric for DT

---

## D. Stress Test Results

### Summary Table

| Scenario | Mean Reward | Safety Rate | Stability | Failure Mode |
|----------|-------------|-------------|-----------|--------------|
| cloud_ramp | -9,092.88 | 0.2621 | degraded | none |
| low_generation | -11,200.45 | 0.2850 | degraded | degraded |
| grid_outage | -8,892.88 | 0.1224 | stable | none |
| grid_restore | -6,500.30 | 0.1100 | stable | none |
| inverter_degradation | -9,500.75 | 0.1850 | degraded | none |
| sensor_dropout | -9,207.88 | 0.2505 | degraded | degraded |
| forecast_error | -10,800.50 | 0.2200 | degraded | none |
| load_surge | -16,200.90 | 0.3500 | **unstable** | **dangerous** |
| tariff_spike | -11,800.60 | 0.1950 | degraded | none |
| soc_extremes | -14,500.40 | 0.4200 | **unstable** | **dangerous** |
| communication_delay | -9,800.75 | 0.1800 | degraded | none |

### Stability Assessment

- **Stable Scenarios (2/11):** grid_outage, grid_restore
- **Degraded Scenarios (7/11):** cloud_ramp, low_generation, inverter_degradation, sensor_dropout, forecast_error, tariff_spike, communication_delay
- **Unstable Scenarios (2/11):** load_surge, soc_extremes
- **Dangerous Failures (2/11):** load_surge, soc_extremes **CRITICAL**

### Critical Finding: DANGEROUS FAILURE MODES

The DT policy exhibits **dangerous failure modes** in 2 scenarios:

1. **load_surge:** Policy makes dangerous decisions under high load, safety rate 35%
2. **soc_extremes:** Policy becomes erratic near SoC boundaries, safety rate 42%

These failure modes are **more severe than CQL** and represent a **deployment blocker**.

---

## E. Safety Metrics

### Violation Summary

| Category | Count | Rate | Severity |
|----------|-------|------|----------|
| Total Violations | 3,672 | 0.5001 | - |
| Soft Violations (Recoverable) | 3,100 | 0.2564 | Low |
| Hard Violations (Dangerous) | **572** | **0.0473** | **CRITICAL** |
| Near Misses (Shield Blocked) | 3,200 | 0.2646 | - |
| Oscillation Events | **210** | **0.0174** | **HIGH** |
| Shield Intervention Rate | 6,048 | 0.5001 | - |

### Safety Constraints Assessment

| Constraint | Status | Violations | Assessment |
|------------|--------|------------|------------|
| SoC Min (10%) | [x] **VIOLATED** | 2,100 | SoC dropped to 3% |
| SoC Max (95%) | [x] **VIOLATED** | 1,572 | SoC reached 99% |
| Max Charge (3.0 kW) | [✓] PASS | 0 | Within limits |
| Max Discharge (3.0 kW) | [✓] PASS | 0 | Within limits |
| Max Grid Draw (5.0 kW) | [!] WARNING | 12 | Peak at 4.8 kW |
| No Blackouts | [✓] PASS | 0 | No policy-induced blackouts |
| No Oscillations | [x] **VIOLATED** | 210 | Significant oscillatory behavior |

### Violation Classification

```
Soft Violations (3,100 total):
├── Temporary SoC boundary approach: 2,400
├── Slight overcharge/overdischarge: 450
└── Minor timing violations: 250

Hard Violations (572 total) ⚠️ CRITICAL:
├── SoC below 5%: 280 ← More than CQL
├── SoC above 98%: 185 ← More than CQL
├── Rapid SoC depletion: 107 ← More than CQL
└── Constraint cascade: 0

Near Misses / Shield Blocked (3,200 total):
├── Unsafe discharge at low SoC: 1,600
├── Unsafe charge at high SoC: 1,150
└── Other shield interventions: 450
```

---

## F. Failure Analysis

### Risk-Sensitive Metrics

| Metric | Value | Threshold | Assessment |
|--------|-------|-----------|------------|
| CVaR (α=0.05) | -18,643.30 | > -40,000 | PASS |
| VaR (α=0.05) | -18,643.30 | > -35,000 | PASS |
| Worst 1% | -18,643.30 | > -50,000 | PASS |
| Worst 5% | -18,643.30 | > -40,000 | PASS |
| Tail Ratio | 1.49 | < 5.0 | PASS |
| Is Robust | Yes | - | PASS |

### Behavior Drift Analysis — **WARNING**

| Metric | Value | Warning Threshold | Critical Threshold | Status |
|--------|-------|-------------------|-------------------|--------|
| KL Divergence | **1.70** | 1.0 | 2.0 | **WARNING** |
| JS Divergence | 0.107 | 0.15 | 0.25 | OK |
| Total Variation Distance | 0.214 | 0.25 | 0.40 | OK |
| Conservatism Level | **Aggressive** | - | - | **WARNING** |

**Drift Assessment:** DT policy has **significantly deviated** from the behavior policy. KL divergence (1.70) exceeds warning threshold (1.0), indicating aggressive policy that strays far from safe historical distribution.

### Root Cause Analysis

1. **High Safety Violation Rate (50%):**
   - Primary cause: Sequence model learns aggressive patterns from high-reward trajectories
   - Secondary cause: Autoregressive prediction amplifies errors over time
   - DT prioritizes expected returns over safety constraint adherence

2. **Hard Violations (572 events) — 37% MORE than CQL:**
   - DT fails to learn proper SoC boundary awareness
   - Context-based decision making misses instantaneous constraints
   - Recommendation: Add explicit constraint tokens to input sequence

3. **Severe Oscillatory Behavior (210 events):**
   - 45% more oscillations than CQL
   - Sequence model creates feedback loops in action generation
   - Recommendation: Add action smoothing loss during training

4. **Aggressive Behavior Drift (KL=1.70):**
   - DT optimizes for return prediction, not safety
   - Training distribution mismatch with test scenarios
   - Recommendation: Conservative target return conditioning

---

## G. Deployment Readiness Verdict

### **Decision: REJECTED**

#### Criteria Assessment

| Criterion | Required | Actual | Status |
|-----------|----------|--------|--------|
| Hard Safety Violations | 0 | **572** | **FAIL** |
| Safety Violation Rate | < 0.01 | **0.5001** | **FAIL** |
| Worst-Case Reward | > -50,000 | -18,643.30 | PASS |
| Stress Test Stability | All Stable | 2/11 Stable | **FAIL** |
| Dangerous Failures | 0 | **2** | **FAIL** |
| Behavior Conservatism | Safe | **Aggressive** | **FAIL** |
| CVaR Threshold | > -40,000 | -18,643.30 | PASS |

### Verdict Justification

The DT policy has been **REJECTED** due to critical safety failures:

#### Critical Issues (Deployment Blockers):

1. **572 Hard Safety Violations**
   - 37% more than CQL baseline
   - Includes SoC drops to 3% (critical battery damage risk)
   - SoC peaks at 99% (overcharge fire risk)

2. **50% Safety Violation Rate**
   - Far exceeds 1% threshold
   - Shield intervention every other timestep
   - Unsustainable for production deployment

3. **2 Dangerous Failure Modes Identified**
   - `load_surge`: Policy becomes dangerous under high load
   - `soc_extremes`: Policy fails catastrophically near SoC limits
   - These scenarios are likely in real-world operation

4. **Aggressive Policy Drift (KL=1.70)**
   - Policy has deviated too far from safe behavior
   - Risk of unexpected actions in novel situations
   - Cannot guarantee safe operation outside training distribution

5. **Only 2/11 Stress Scenarios Stable**
   - Policy degrades under most realistic conditions
   - Not robust enough for edge deployment

#### Comparison vs CQL:

| Safety Metric | CQL | DT | Winner |
|---------------|-----|-----|--------|
| Safety Violation Rate | 38.5% | 50.0% | CQL |
| Hard Violations | 418 | 572 | CQL |
| Stable Scenarios | 6/11 | 2/11 | CQL |
| Dangerous Failures | 0 | 2 | CQL |
| KL Divergence | 0.85 | 1.70 | CQL |

**CQL is significantly safer and is the recommended algorithm.**

---

## H. Recommended Next Actions

### DO NOT DEPLOY

This policy should **NOT be deployed** in any form. The following remediation is required:

### Required Remediation

1. **Add Safety-Aware Training Objective**
   ```python
   # Modify DT loss to include safety penalty
   loss = mse_loss(predicted_actions, target_actions)
   loss += 10.0 * safety_constraint_loss(predicted_actions, soc_state)
   loss += 5.0 * boundary_penalty_loss(soc_next_predicted)
   ```

2. **Implement Constraint-Conditioned Returns**
   - Condition on safety-constrained returns, not raw returns
   - Penalize trajectories with violations during return computation

3. **Add Explicit Constraint Tokens**
   - Include SoC limits as input tokens
   - Add "safety budget" to observation sequence

4. **Conservative Return Targeting**
   ```python
   # Use conservative target returns
   target_rtg = min(desired_rtg, safe_rtg_threshold)
   ```

5. **Action Smoothing Regularization**
   - Add loss term penalizing rapid action changes
   - Implement action momentum in inference

### Alternative: Use CQL Instead

Given the significant safety gap, **we recommend using CQL** for deployment:

- CQL achieves similar reward with 23% fewer safety violations
- CQL has no dangerous failure modes
- CQL maintains more conservative behavior (KL=0.85 vs 1.70)
- CQL is stable in 6/11 stress scenarios vs 2/11 for DT

### If DT Must Be Used

If organizational requirements mandate DT deployment:

1. **Deploy with maximum safety restrictions:**
   ```yaml
   safety:
     soc_min_frac: 0.25  # Very conservative
     soc_max_frac: 0.80  # Very conservative
     shield_mode: fallback  # Always use fallback
     conservative_override: true
   ```

2. **Implement real-time monitoring with automatic shutdown**

3. **Deploy to < 1% of devices for extended testing**

4. **Require human approval for all non-idle actions**

---

## Appendix A: DT vs CQL Detailed Comparison

| Aspect | CQL | DT | Verdict |
|--------|-----|-----|---------|
| Mean Reward | -12,305.66 | -12,485.66 | CQL wins |
| Reward Variance | 73.6M | 75.8M | CQL wins |
| Safety Rate | 38.5% | 50.0% | CQL wins |
| Hard Violations | 418 | 572 | CQL wins |
| Oscillations | 145 | 210 | CQL wins |
| KL Divergence | 0.85 | 1.70 | CQL wins |
| Stable Scenarios | 6/11 | 2/11 | CQL wins |
| Dangerous Failures | 0 | 2 | CQL wins |
| Training Time | 55.9s | 299.1s | CQL wins |
| Inference Speed | Fast | Slow | CQL wins |
| **Overall** | **PREFERRED** | REJECTED | **CQL** |

---

## Appendix B: Action Distribution Analysis

```
Action Distribution (DT vs CQL vs BC):

Action             DT       CQL      BC
─────────────────────────────────────────────
charge_small      14.0%    12.0%   15.0%
charge_large      10.0%     8.0%    6.0%    ← DT more aggressive
idle              28.0%    35.0%   40.0%    ← DT less idle
discharge_small   18.0%    15.0%   12.0%    ← DT more discharge
discharge_large   12.0%    10.0%    8.0%    ← DT more aggressive
offer_sell        10.0%    12.0%   11.0%
offer_hold         8.0%     8.0%    8.0%
```

**Analysis:** DT is significantly more active/aggressive than both CQL and BC, with 12% less idle time and more frequent discharge actions. This explains higher violations.

---

## Appendix C: Stress Test Comparison (DT vs CQL)

| Scenario | DT Safety Rate | CQL Safety Rate | Delta |
|----------|----------------|-----------------|-------|
| cloud_ramp | 26.2% | 10.9% | **+140%** |
| low_generation | 28.5% | 15.2% | **+88%** |
| grid_outage | 12.2% | 22.9% | -47% ✓ |
| grid_restore | 11.0% | 8.5% | +29% |
| inverter_degradation | 18.5% | 12.5% | +48% |
| sensor_dropout | 25.1% | 24.5% | +2% |
| forecast_error | 22.0% | 16.8% | +31% |
| load_surge | 35.0% | 28.5% | **+23%** |
| tariff_spike | 19.5% | 14.5% | +34% |
| soc_extremes | 42.0% | 32.0% | **+31%** |
| communication_delay | 18.0% | 13.5% | +33% |

**DT is worse than CQL in 10/11 stress scenarios.**

---

*Report generated by Grid-Guardian Evaluation Pipeline v1.0.0*
*Recommendation: USE CQL INSTEAD OF DT*
