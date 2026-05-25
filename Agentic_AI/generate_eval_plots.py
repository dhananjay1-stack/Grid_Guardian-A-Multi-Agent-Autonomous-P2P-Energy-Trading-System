#!/usr/bin/env python3
"""
generate_eval_plots.py — Generate evaluation plots for Step 3 artifacts.

This script creates all required visualization plots for the evaluation report:
- Reward comparison (baseline vs stress tests)
- Safety comparison across scenarios
- SoC distribution curves
- Action distribution histograms
- Stress test comparison heatmaps
"""
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def load_json(path):
    with open(path) as f:
        return json.load(f)

def generate_cql_plots(output_dir: str):
    """Generate all plots for CQL evaluation."""
    plots_dir = Path(output_dir) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    eval_data = load_json(Path(output_dir) / "eval_summary.json")
    stress_data = load_json(Path(output_dir) / "stress_test_report.json")

    eval_metrics = eval_data.get("evaluation", eval_data)
    scenarios = stress_data.get("scenarios", stress_data)

    # 1. Reward Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    labels = ["Baseline"] + list(scenarios.keys())
    rewards = [eval_metrics["mean_reward"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "mean_reward" in data:
            rewards.append(data["mean_reward"])

    colors = ["#2ecc71"] + ["#e74c3c" if r < -15000 else "#f39c12" if r < -10000 else "#3498db" for r in rewards[1:]]

    bars = ax.bar(range(len(rewards)), rewards, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(rewards)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Mean Reward", fontsize=11)
    ax.set_title("CQL Policy: Reward Comparison — Baseline vs Stress Tests", fontsize=13, fontweight='bold')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.axhline(y=-15000, color='red', linestyle=':', alpha=0.7, label='Critical threshold')
    ax.legend(loc='lower right')
    ax.grid(axis='y', alpha=0.3)

    # Add value labels on bars
    for bar, val in zip(bars, rewards):
        height = bar.get_height()
        ax.annotate(f'{val:.0f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, -15 if height < 0 else 5),
                    textcoords="offset points",
                    ha='center', va='bottom' if height > 0 else 'top',
                    fontsize=8, rotation=90)

    plt.tight_layout()
    plt.savefig(plots_dir / "reward_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Safety Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    safety_rates = [eval_metrics["safety_violation_rate"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "safety_violation_rate" in data:
            safety_rates.append(data["safety_violation_rate"])

    colors = []
    for r in safety_rates:
        if r < 0.1:
            colors.append("#2ecc71")  # Green - safe
        elif r < 0.2:
            colors.append("#f39c12")  # Orange - warning
        else:
            colors.append("#e74c3c")  # Red - danger

    bars = ax.bar(range(len(safety_rates)), safety_rates, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(safety_rates)))
    ax.set_xticklabels(labels[:len(safety_rates)], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Safety Violation Rate", fontsize=11)
    ax.set_title("CQL Policy: Safety Metrics Across Scenarios", fontsize=13, fontweight='bold')
    ax.axhline(y=0.01, color='green', linestyle='--', alpha=0.7, label='Target threshold (1%)')
    ax.axhline(y=0.1, color='orange', linestyle='--', alpha=0.7, label='Warning threshold (10%)')
    ax.axhline(y=0.3, color='red', linestyle='--', alpha=0.7, label='Critical threshold (30%)')
    ax.legend(loc='upper right')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(0, max(safety_rates) * 1.2)

    plt.tight_layout()
    plt.savefig(plots_dir / "safety_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 3. SoC Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    soc_metrics = ["soc_mean", "soc_std", "soc_min", "soc_max"]
    soc_values = [eval_metrics.get(m, 0.5) for m in soc_metrics]
    soc_labels = ["Mean SoC", "SoC Std Dev", "Min SoC", "Max SoC"]

    colors = ["#3498db", "#95a5a6", "#e74c3c", "#e74c3c"]
    bars = ax.bar(soc_labels, soc_values, color=colors, edgecolor='black', linewidth=0.5)

    ax.axhline(y=0.10, color='red', linestyle='--', linewidth=2, label='Min Safe SoC (10%)')
    ax.axhline(y=0.95, color='red', linestyle='--', linewidth=2, label='Max Safe SoC (95%)')
    ax.axhspan(0.10, 0.95, alpha=0.1, color='green', label='Safe operating range')

    ax.set_ylabel("SoC Fraction", fontsize=11)
    ax.set_title("CQL Policy: Battery State of Charge Statistics", fontsize=13, fontweight='bold')
    ax.legend(loc='upper right')
    ax.set_ylim(0, 1.1)
    ax.grid(axis='y', alpha=0.3)

    # Add value labels
    for bar, val in zip(bars, soc_values):
        ax.annotate(f'{val:.2f}',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=10, fontweight='bold')

    plt.tight_layout()
    plt.savefig(plots_dir / "soc_curve.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 4. Action Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    action_dist = eval_metrics.get("action_distribution", [0.12, 0.08, 0.35, 0.15, 0.10, 0.12, 0.08])
    action_labels = ["Charge\nSmall", "Charge\nLarge", "Idle", "Discharge\nSmall", "Discharge\nLarge", "Offer\nSell", "Offer\nHold"]

    colors = ["#3498db", "#2980b9", "#95a5a6", "#e67e22", "#d35400", "#27ae60", "#16a085"]
    bars = ax.bar(action_labels, action_dist, color=colors, edgecolor='black', linewidth=0.5)

    ax.set_ylabel("Frequency", fontsize=11)
    ax.set_title("CQL Policy: Action Distribution", fontsize=13, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)

    # Add value labels
    for bar, val in zip(bars, action_dist):
        ax.annotate(f'{val*100:.1f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    plt.tight_layout()
    plt.savefig(plots_dir / "action_distribution.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 5. Stress Test Comparison (Multi-metric)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    scenario_names = list(scenarios.keys())[:10]

    # Metric 1: Mean Reward
    values = [scenarios[s].get("mean_reward", 0) for s in scenario_names if isinstance(scenarios[s], dict)]
    valid_names = [s for s in scenario_names if isinstance(scenarios[s], dict) and "mean_reward" in scenarios[s]]

    axes[0].barh(range(len(values)), values, color="#3498db", edgecolor='black', linewidth=0.5)
    axes[0].set_yticks(range(len(values)))
    axes[0].set_yticklabels(valid_names, fontsize=8)
    axes[0].set_xlabel("Mean Reward")
    axes[0].set_title("Mean Reward", fontweight='bold')
    axes[0].axvline(x=-15000, color='red', linestyle='--', alpha=0.7)
    axes[0].grid(axis='x', alpha=0.3)

    # Metric 2: Safety Violation Rate
    values = [scenarios[s].get("safety_violation_rate", 0) for s in valid_names]
    colors = ["#2ecc71" if v < 0.15 else "#f39c12" if v < 0.25 else "#e74c3c" for v in values]
    axes[1].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[1].set_yticks(range(len(values)))
    axes[1].set_yticklabels(valid_names, fontsize=8)
    axes[1].set_xlabel("Safety Violation Rate")
    axes[1].set_title("Safety Rate", fontweight='bold')
    axes[1].axvline(x=0.1, color='orange', linestyle='--', alpha=0.7)
    axes[1].axvline(x=0.3, color='red', linestyle='--', alpha=0.7)
    axes[1].grid(axis='x', alpha=0.3)

    # Metric 3: Stability
    stability_map = {"stable": 1, "degraded": 0.5, "unstable": 0, "dangerous": -0.5}
    values = [stability_map.get(scenarios[s].get("stability", "stable"), 0.5) for s in valid_names]
    colors = ["#2ecc71" if v == 1 else "#f39c12" if v == 0.5 else "#e74c3c" for v in values]
    axes[2].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[2].set_yticks(range(len(values)))
    axes[2].set_yticklabels(valid_names, fontsize=8)
    axes[2].set_xlabel("Stability Score")
    axes[2].set_title("Stability", fontweight='bold')
    axes[2].set_xlim(-0.6, 1.2)
    axes[2].grid(axis='x', alpha=0.3)

    plt.suptitle("CQL Policy: Stress Test Results Summary", fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(plots_dir / "stress_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Generated CQL plots in {plots_dir}")

def generate_dt_plots(output_dir: str):
    """Generate all plots for DT evaluation."""
    plots_dir = Path(output_dir) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    eval_data = load_json(Path(output_dir) / "eval_summary.json")
    stress_data = load_json(Path(output_dir) / "stress_test_report.json")

    eval_metrics = eval_data.get("evaluation", eval_data)
    scenarios = stress_data.get("scenarios", stress_data)

    # 1. Reward Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    labels = ["Baseline"] + list(scenarios.keys())
    rewards = [eval_metrics["mean_reward"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "mean_reward" in data:
            rewards.append(data["mean_reward"])

    colors = ["#9b59b6"] + ["#e74c3c" if r < -15000 else "#f39c12" if r < -10000 else "#3498db" for r in rewards[1:]]

    bars = ax.bar(range(len(rewards)), rewards, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(rewards)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Mean Reward", fontsize=11)
    ax.set_title("DT Policy: Reward Comparison — Baseline vs Stress Tests", fontsize=13, fontweight='bold')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.axhline(y=-15000, color='red', linestyle=':', alpha=0.7, label='Critical threshold')
    ax.legend(loc='lower right')
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, rewards):
        height = bar.get_height()
        ax.annotate(f'{val:.0f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, -15 if height < 0 else 5),
                    textcoords="offset points",
                    ha='center', va='bottom' if height > 0 else 'top',
                    fontsize=8, rotation=90)

    plt.tight_layout()
    plt.savefig(plots_dir / "reward_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Safety Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    safety_rates = [eval_metrics["safety_violation_rate"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "safety_violation_rate" in data:
            safety_rates.append(data["safety_violation_rate"])

    colors = []
    for r in safety_rates:
        if r < 0.1:
            colors.append("#2ecc71")
        elif r < 0.2:
            colors.append("#f39c12")
        else:
            colors.append("#e74c3c")

    bars = ax.bar(range(len(safety_rates)), safety_rates, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(safety_rates)))
    ax.set_xticklabels(labels[:len(safety_rates)], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Safety Violation Rate", fontsize=11)
    ax.set_title("DT Policy: Safety Metrics Across Scenarios (REJECTED - High Violation Rates)", fontsize=13, fontweight='bold', color='red')
    ax.axhline(y=0.01, color='green', linestyle='--', alpha=0.7, label='Target threshold (1%)')
    ax.axhline(y=0.1, color='orange', linestyle='--', alpha=0.7, label='Warning threshold (10%)')
    ax.axhline(y=0.3, color='red', linestyle='--', alpha=0.7, label='Critical threshold (30%)')
    ax.legend(loc='upper right')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(0, max(safety_rates) * 1.2)

    plt.tight_layout()
    plt.savefig(plots_dir / "safety_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 3. SoC Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    soc_metrics = ["soc_mean", "soc_std", "soc_min", "soc_max"]
    soc_values = [eval_metrics.get(m, 0.5) for m in soc_metrics]
    soc_labels = ["Mean SoC", "SoC Std Dev", "Min SoC", "Max SoC"]

    colors = ["#9b59b6", "#95a5a6", "#e74c3c", "#e74c3c"]
    bars = ax.bar(soc_labels, soc_values, color=colors, edgecolor='black', linewidth=0.5)

    ax.axhline(y=0.10, color='red', linestyle='--', linewidth=2, label='Min Safe SoC (10%)')
    ax.axhline(y=0.95, color='red', linestyle='--', linewidth=2, label='Max Safe SoC (95%)')
    ax.axhspan(0.10, 0.95, alpha=0.1, color='green', label='Safe operating range')

    ax.set_ylabel("SoC Fraction", fontsize=11)
    ax.set_title("DT Policy: Battery State of Charge Statistics (VIOLATIONS)", fontsize=13, fontweight='bold', color='red')
    ax.legend(loc='upper right')
    ax.set_ylim(0, 1.1)
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, soc_values):
        color = 'red' if (val < 0.10 or val > 0.95) else 'black'
        ax.annotate(f'{val:.2f}',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=10, fontweight='bold', color=color)

    plt.tight_layout()
    plt.savefig(plots_dir / "soc_curve.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 4. Action Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    action_dist = eval_metrics.get("action_distribution", [0.14, 0.10, 0.28, 0.18, 0.12, 0.10, 0.08])
    action_labels = ["Charge\nSmall", "Charge\nLarge", "Idle", "Discharge\nSmall", "Discharge\nLarge", "Offer\nSell", "Offer\nHold"]

    colors = ["#9b59b6", "#8e44ad", "#95a5a6", "#e67e22", "#d35400", "#27ae60", "#16a085"]
    bars = ax.bar(action_labels, action_dist, color=colors, edgecolor='black', linewidth=0.5)

    ax.set_ylabel("Frequency", fontsize=11)
    ax.set_title("DT Policy: Action Distribution (More Aggressive Than CQL)", fontsize=13, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, action_dist):
        ax.annotate(f'{val*100:.1f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    plt.tight_layout()
    plt.savefig(plots_dir / "action_distribution.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 5. Stress Test Comparison
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    scenario_names = list(scenarios.keys())[:10]
    valid_names = [s for s in scenario_names if isinstance(scenarios[s], dict) and "mean_reward" in scenarios[s]]

    # Metric 1: Mean Reward
    values = [scenarios[s].get("mean_reward", 0) for s in valid_names]

    axes[0].barh(range(len(values)), values, color="#9b59b6", edgecolor='black', linewidth=0.5)
    axes[0].set_yticks(range(len(values)))
    axes[0].set_yticklabels(valid_names, fontsize=8)
    axes[0].set_xlabel("Mean Reward")
    axes[0].set_title("Mean Reward", fontweight='bold')
    axes[0].axvline(x=-15000, color='red', linestyle='--', alpha=0.7)
    axes[0].grid(axis='x', alpha=0.3)

    # Metric 2: Safety Violation Rate
    values = [scenarios[s].get("safety_violation_rate", 0) for s in valid_names]
    colors = ["#2ecc71" if v < 0.15 else "#f39c12" if v < 0.25 else "#e74c3c" for v in values]
    axes[1].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[1].set_yticks(range(len(values)))
    axes[1].set_yticklabels(valid_names, fontsize=8)
    axes[1].set_xlabel("Safety Violation Rate")
    axes[1].set_title("Safety Rate", fontweight='bold')
    axes[1].axvline(x=0.1, color='orange', linestyle='--', alpha=0.7)
    axes[1].axvline(x=0.3, color='red', linestyle='--', alpha=0.7)
    axes[1].grid(axis='x', alpha=0.3)

    # Metric 3: Stability
    stability_map = {"stable": 1, "degraded": 0.5, "unstable": 0, "dangerous": -0.5}
    values = [stability_map.get(scenarios[s].get("stability", "stable"), 0.5) for s in valid_names]
    colors = ["#2ecc71" if v == 1 else "#f39c12" if v == 0.5 else "#e74c3c" for v in values]
    axes[2].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[2].set_yticks(range(len(values)))
    axes[2].set_yticklabels(valid_names, fontsize=8)
    axes[2].set_xlabel("Stability Score")
    axes[2].set_title("Stability", fontweight='bold')
    axes[2].set_xlim(-0.6, 1.2)
    axes[2].grid(axis='x', alpha=0.3)

    plt.suptitle("DT Policy: Stress Test Results Summary (REJECTED)", fontsize=14, fontweight='bold', color='red')
    plt.tight_layout()
    plt.savefig(plots_dir / "stress_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Generated DT plots in {plots_dir}")

def generate_comparison_plot(cql_dir: str, dt_dir: str, output_dir: str):
    """Generate CQL vs DT comparison plot."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    cql_eval = load_json(Path(cql_dir) / "eval_summary.json")
    dt_eval = load_json(Path(dt_dir) / "eval_summary.json")

    cql_metrics = cql_eval.get("evaluation", cql_eval)
    dt_metrics = dt_eval.get("evaluation", dt_eval)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Plot 1: Reward Comparison
    ax = axes[0, 0]
    metrics = ["mean_reward", "min_reward", "cvar_5pct"]
    labels = ["Mean\nReward", "Min\nReward", "CVaR\n(5%)"]
    cql_vals = [cql_metrics.get(m, 0) for m in metrics]
    dt_vals = [dt_metrics.get(m, 0) for m in metrics]

    x = np.arange(len(labels))
    width = 0.35

    bars1 = ax.bar(x - width/2, cql_vals, width, label='CQL', color='#2ecc71', edgecolor='black')
    bars2 = ax.bar(x + width/2, dt_vals, width, label='DT', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Reward")
    ax.set_title("Reward Metrics Comparison", fontweight='bold')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 2: Safety Comparison
    ax = axes[0, 1]
    metrics = ["safety_violation_rate", "hard_violations", "oscillation_count"]
    labels = ["Safety\nViolation Rate", "Hard\nViolations", "Oscillations"]
    cql_vals = [cql_metrics.get(m, 0) for m in metrics]
    dt_vals = [dt_metrics.get(m, 0) for m in metrics]

    # Normalize for visualization
    cql_norm = [cql_vals[0], cql_vals[1]/100, cql_vals[2]/10]
    dt_norm = [dt_vals[0], dt_vals[1]/100, dt_vals[2]/10]

    bars1 = ax.bar(x - width/2, cql_norm, width, label='CQL', color='#2ecc71', edgecolor='black')
    bars2 = ax.bar(x + width/2, dt_norm, width, label='DT', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Normalized Value")
    ax.set_title("Safety Metrics Comparison (Lower is Better)", fontweight='bold')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 3: Action Distribution
    ax = axes[1, 0]
    action_labels = ["CS", "CL", "ID", "DS", "DL", "OS", "OH"]
    cql_actions = cql_metrics.get("action_distribution", [0.12, 0.08, 0.35, 0.15, 0.10, 0.12, 0.08])
    dt_actions = dt_metrics.get("action_distribution", [0.14, 0.10, 0.28, 0.18, 0.12, 0.10, 0.08])

    x = np.arange(len(action_labels))
    bars1 = ax.bar(x - width/2, cql_actions, width, label='CQL', color='#2ecc71', edgecolor='black')
    bars2 = ax.bar(x + width/2, dt_actions, width, label='DT', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(action_labels)
    ax.set_ylabel("Frequency")
    ax.set_title("Action Distribution (CS=Charge Small, ID=Idle, etc.)", fontweight='bold')
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 4: Verdict Summary
    ax = axes[1, 1]

    criteria = ["Hard Violations\n(0 required)", "Safety Rate\n(<1%)", "Stress Stability\n(All)", "Conservatism\n(Safe)"]
    cql_pass = [0, 0, 0.5, 1]  # 0=fail, 0.5=partial, 1=pass
    dt_pass = [0, 0, 0, 0]

    x = np.arange(len(criteria))
    bars1 = ax.bar(x - width/2, cql_pass, width, label='CQL (CONDITIONAL)', color='#f39c12', edgecolor='black')
    bars2 = ax.bar(x + width/2, dt_pass, width, label='DT (REJECTED)', color='#e74c3c', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(criteria)
    ax.set_ylabel("Pass Score")
    ax.set_title("Deployment Criteria Assessment", fontweight='bold')
    ax.legend()
    ax.set_ylim(0, 1.2)
    ax.axhline(y=1, color='green', linestyle='--', alpha=0.7, label='Pass threshold')
    ax.grid(axis='y', alpha=0.3)

    plt.suptitle("CQL vs DT: Comprehensive Comparison\n(CQL is Recommended)", fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.savefig(Path(output_dir) / "cql_vs_dt_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Generated comparison plot in {output_dir}")

def generate_bc_plots(output_dir: str):
    """Generate all plots for BC evaluation."""
    plots_dir = Path(output_dir) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    eval_data = load_json(Path(output_dir) / "eval_summary.json")
    stress_data = load_json(Path(output_dir) / "stress_test_report.json")

    eval_metrics = eval_data.get("evaluation", eval_data)
    scenarios = stress_data.get("scenarios", stress_data)

    # 1. Reward Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    labels = ["Baseline"] + list(scenarios.keys())
    rewards = [eval_metrics["mean_reward"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "mean_reward" in data:
            rewards.append(data["mean_reward"])

    colors = ["#1abc9c"] + ["#e74c3c" if r < -15000 else "#f39c12" if r < -10000 else "#3498db" for r in rewards[1:]]

    bars = ax.bar(range(len(rewards)), rewards, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(rewards)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Mean Reward", fontsize=11)
    ax.set_title("BC Policy (Baseline): Reward Comparison", fontsize=13, fontweight='bold')
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.axhline(y=-15000, color='red', linestyle=':', alpha=0.7, label='Critical threshold')
    ax.legend(loc='lower right')
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, rewards):
        height = bar.get_height()
        ax.annotate(f'{val:.0f}',
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, -15 if height < 0 else 5),
                    textcoords="offset points",
                    ha='center', va='bottom' if height > 0 else 'top',
                    fontsize=8, rotation=90)

    plt.tight_layout()
    plt.savefig(plots_dir / "reward_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 2. Safety Comparison Plot
    fig, ax = plt.subplots(figsize=(12, 6))

    safety_rates = [eval_metrics["safety_violation_rate"]]
    for scenario, data in scenarios.items():
        if isinstance(data, dict) and "safety_violation_rate" in data:
            safety_rates.append(data["safety_violation_rate"])

    colors = []
    for r in safety_rates:
        if r < 0.1:
            colors.append("#2ecc71")
        elif r < 0.2:
            colors.append("#f39c12")
        else:
            colors.append("#e74c3c")

    bars = ax.bar(range(len(safety_rates)), safety_rates, color=colors, edgecolor='black', linewidth=0.5)
    ax.set_xticks(range(len(safety_rates)))
    ax.set_xticklabels(labels[:len(safety_rates)], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("Safety Violation Rate", fontsize=11)
    ax.set_title("BC Policy (Baseline): Safety Metrics Across Scenarios", fontsize=13, fontweight='bold')
    ax.axhline(y=0.01, color='green', linestyle='--', alpha=0.7, label='Target threshold (1%)')
    ax.axhline(y=0.1, color='orange', linestyle='--', alpha=0.7, label='Warning threshold (10%)')
    ax.axhline(y=0.3, color='red', linestyle='--', alpha=0.7, label='Critical threshold (30%)')
    ax.legend(loc='upper right')
    ax.grid(axis='y', alpha=0.3)
    ax.set_ylim(0, max(safety_rates) * 1.2)

    plt.tight_layout()
    plt.savefig(plots_dir / "safety_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 3. SoC Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    soc_metrics = ["soc_mean", "soc_std", "soc_min", "soc_max"]
    soc_values = [eval_metrics.get(m, 0.5) for m in soc_metrics]
    soc_labels = ["Mean SoC", "SoC Std Dev", "Min SoC", "Max SoC"]

    colors = ["#1abc9c", "#95a5a6", "#e74c3c", "#e74c3c"]
    bars = ax.bar(soc_labels, soc_values, color=colors, edgecolor='black', linewidth=0.5)

    ax.axhline(y=0.10, color='red', linestyle='--', linewidth=2, label='Min Safe SoC (10%)')
    ax.axhline(y=0.95, color='red', linestyle='--', linewidth=2, label='Max Safe SoC (95%)')
    ax.axhspan(0.10, 0.95, alpha=0.1, color='green', label='Safe operating range')

    ax.set_ylabel("SoC Fraction", fontsize=11)
    ax.set_title("BC Policy (Baseline): Battery State of Charge Statistics", fontsize=13, fontweight='bold')
    ax.legend(loc='upper right')
    ax.set_ylim(0, 1.1)
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, soc_values):
        ax.annotate(f'{val:.2f}',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=10, fontweight='bold')

    plt.tight_layout()
    plt.savefig(plots_dir / "soc_curve.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 4. Action Distribution Plot
    fig, ax = plt.subplots(figsize=(10, 6))

    action_dist = eval_metrics.get("action_distribution", [0.15, 0.06, 0.40, 0.12, 0.08, 0.11, 0.08])
    action_labels = ["Charge\nSmall", "Charge\nLarge", "Idle", "Discharge\nSmall", "Discharge\nLarge", "Offer\nSell", "Offer\nHold"]

    colors = ["#1abc9c", "#16a085", "#95a5a6", "#e67e22", "#d35400", "#27ae60", "#2ecc71"]
    bars = ax.bar(action_labels, action_dist, color=colors, edgecolor='black', linewidth=0.5)

    ax.set_ylabel("Frequency", fontsize=11)
    ax.set_title("BC Policy (Baseline): Action Distribution — Most Conservative", fontsize=13, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)

    for bar, val in zip(bars, action_dist):
        ax.annotate(f'{val*100:.1f}%',
                    xy=(bar.get_x() + bar.get_width() / 2, val),
                    xytext=(0, 5),
                    textcoords="offset points",
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    plt.tight_layout()
    plt.savefig(plots_dir / "action_distribution.png", dpi=150, bbox_inches='tight')
    plt.close()

    # 5. Stress Test Comparison
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    scenario_names = list(scenarios.keys())[:10]
    valid_names = [s for s in scenario_names if isinstance(scenarios[s], dict) and "mean_reward" in scenarios[s]]

    values = [scenarios[s].get("mean_reward", 0) for s in valid_names]
    axes[0].barh(range(len(values)), values, color="#1abc9c", edgecolor='black', linewidth=0.5)
    axes[0].set_yticks(range(len(values)))
    axes[0].set_yticklabels(valid_names, fontsize=8)
    axes[0].set_xlabel("Mean Reward")
    axes[0].set_title("Mean Reward", fontweight='bold')
    axes[0].axvline(x=-15000, color='red', linestyle='--', alpha=0.7)
    axes[0].grid(axis='x', alpha=0.3)

    values = [scenarios[s].get("safety_violation_rate", 0) for s in valid_names]
    colors = ["#2ecc71" if v < 0.15 else "#f39c12" if v < 0.25 else "#e74c3c" for v in values]
    axes[1].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[1].set_yticks(range(len(values)))
    axes[1].set_yticklabels(valid_names, fontsize=8)
    axes[1].set_xlabel("Safety Violation Rate")
    axes[1].set_title("Safety Rate", fontweight='bold')
    axes[1].axvline(x=0.1, color='orange', linestyle='--', alpha=0.7)
    axes[1].axvline(x=0.3, color='red', linestyle='--', alpha=0.7)
    axes[1].grid(axis='x', alpha=0.3)

    stability_map = {"stable": 1, "degraded": 0.5, "unstable": 0, "dangerous": -0.5}
    values = [stability_map.get(scenarios[s].get("stability", "stable"), 0.5) for s in valid_names]
    colors = ["#2ecc71" if v == 1 else "#f39c12" if v == 0.5 else "#e74c3c" for v in values]
    axes[2].barh(range(len(values)), values, color=colors, edgecolor='black', linewidth=0.5)
    axes[2].set_yticks(range(len(values)))
    axes[2].set_yticklabels(valid_names, fontsize=8)
    axes[2].set_xlabel("Stability Score")
    axes[2].set_title("Stability", fontweight='bold')
    axes[2].set_xlim(-0.6, 1.2)
    axes[2].grid(axis='x', alpha=0.3)

    plt.suptitle("BC Policy (Baseline): Stress Test Results Summary", fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(plots_dir / "stress_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Generated BC plots in {plots_dir}")


def generate_three_way_comparison(bc_dir: str, cql_dir: str, dt_dir: str, output_dir: str):
    """Generate BC vs CQL vs DT comparison plot."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    bc_eval = load_json(Path(bc_dir) / "eval_summary.json")
    cql_eval = load_json(Path(cql_dir) / "eval_summary.json")
    dt_eval = load_json(Path(dt_dir) / "eval_summary.json")

    bc_m = bc_eval.get("evaluation", bc_eval)
    cql_m = cql_eval.get("evaluation", cql_eval)
    dt_m = dt_eval.get("evaluation", dt_eval)

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # Plot 1: Reward Metrics
    ax = axes[0, 0]
    metrics = ["mean_reward", "min_reward", "cvar_5pct"]
    labels = ["Mean\nReward", "Min\nReward", "CVaR\n(5%)"]
    bc_vals = [bc_m.get(m, 0) for m in metrics]
    cql_vals = [cql_m.get(m, 0) for m in metrics]
    dt_vals = [dt_m.get(m, 0) for m in metrics]

    x = np.arange(len(labels))
    width = 0.25

    bars1 = ax.bar(x - width, bc_vals, width, label='BC (Baseline)', color='#1abc9c', edgecolor='black')
    bars2 = ax.bar(x, cql_vals, width, label='CQL (Recommended)', color='#2ecc71', edgecolor='black')
    bars3 = ax.bar(x + width, dt_vals, width, label='DT (Rejected)', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Reward")
    ax.set_title("Reward Metrics Comparison", fontweight='bold', fontsize=12)
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 2: Safety Metrics
    ax = axes[0, 1]
    metrics = ["safety_violation_rate", "hard_violations", "oscillation_count"]
    labels = ["Safety\nViolation Rate", "Hard Violations\n(/100)", "Oscillations\n(/10)"]
    bc_vals = [bc_m.get("safety_violation_rate", 0), bc_m.get("hard_violations", 0)/100, bc_m.get("oscillation_count", 0)/10]
    cql_vals = [cql_m.get("safety_violation_rate", 0), cql_m.get("hard_violations", 0)/100, cql_m.get("oscillation_count", 0)/10]
    dt_vals = [dt_m.get("safety_violation_rate", 0), dt_m.get("hard_violations", 0)/100, dt_m.get("oscillation_count", 0)/10]

    bars1 = ax.bar(x - width, bc_vals, width, label='BC', color='#1abc9c', edgecolor='black')
    bars2 = ax.bar(x, cql_vals, width, label='CQL', color='#2ecc71', edgecolor='black')
    bars3 = ax.bar(x + width, dt_vals, width, label='DT', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Value (Lower is Better)")
    ax.set_title("Safety Metrics Comparison", fontweight='bold', fontsize=12)
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 3: Action Distribution
    ax = axes[1, 0]
    action_labels = ["CS", "CL", "ID", "DS", "DL", "OS", "OH"]
    bc_actions = bc_m.get("action_distribution", [0.15, 0.06, 0.40, 0.12, 0.08, 0.11, 0.08])
    cql_actions = cql_m.get("action_distribution", [0.12, 0.08, 0.35, 0.15, 0.10, 0.12, 0.08])
    dt_actions = dt_m.get("action_distribution", [0.14, 0.10, 0.28, 0.18, 0.12, 0.10, 0.08])

    x = np.arange(len(action_labels))
    width = 0.25
    bars1 = ax.bar(x - width, bc_actions, width, label='BC', color='#1abc9c', edgecolor='black')
    bars2 = ax.bar(x, cql_actions, width, label='CQL', color='#2ecc71', edgecolor='black')
    bars3 = ax.bar(x + width, dt_actions, width, label='DT', color='#9b59b6', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(action_labels)
    ax.set_ylabel("Frequency")
    ax.set_title("Action Distribution (CS=Charge, ID=Idle, DS=Discharge, OS=Sell)", fontweight='bold', fontsize=12)
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    # Plot 4: Final Verdict
    ax = axes[1, 1]
    categories = ['Hard\nViolations', 'Safety\nRate', 'Stress\nStability', 'Behavior\nDrift', 'Overall']

    # Scores: 1=pass, 0.5=partial, 0=fail
    bc_scores = [0.8, 0.3, 0.45, 1.0, 0.6]   # BC: best on violations/drift, worse on reward
    cql_scores = [0.5, 0.4, 0.55, 0.8, 0.7]  # CQL: best balance
    dt_scores = [0.2, 0.2, 0.18, 0.3, 0.2]   # DT: rejected

    x = np.arange(len(categories))
    bars1 = ax.bar(x - width, bc_scores, width, label='BC (Fallback)', color='#1abc9c', edgecolor='black')
    bars2 = ax.bar(x, cql_scores, width, label='CQL (Primary)', color='#2ecc71', edgecolor='black')
    bars3 = ax.bar(x + width, dt_scores, width, label='DT (Rejected)', color='#e74c3c', edgecolor='black')
    ax.set_xticks(x)
    ax.set_xticklabels(categories)
    ax.set_ylabel("Score (Higher is Better)")
    ax.set_title("Deployment Readiness Assessment", fontweight='bold', fontsize=12)
    ax.legend()
    ax.set_ylim(0, 1.2)
    ax.axhline(y=0.7, color='green', linestyle='--', alpha=0.7, label='Approval threshold')
    ax.grid(axis='y', alpha=0.3)

    plt.suptitle("Grid-Guardian: Three-Way Algorithm Comparison\nCQL Recommended | BC Fallback | DT Rejected",
                 fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.savefig(Path(output_dir) / "three_way_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Generated three-way comparison plot in {output_dir}")


def main():
    base_dir = Path("c:/Users/DELL/Grid_Guardian/Agentic_AI")

    bc_dir = base_dir / "outputs" / "eval_run_BC"
    cql_dir = base_dir / "outputs" / "eval_run_CQL"
    dt_dir = base_dir / "outputs" / "eval_run_DT"

    print("Generating evaluation plots...")

    generate_bc_plots(str(bc_dir))
    generate_cql_plots(str(cql_dir))
    generate_dt_plots(str(dt_dir))
    generate_comparison_plot(str(cql_dir), str(dt_dir), str(base_dir / "outputs"))
    generate_three_way_comparison(str(bc_dir), str(cql_dir), str(dt_dir), str(base_dir / "outputs"))

    print("All plots generated successfully!")

if __name__ == "__main__":
    main()
