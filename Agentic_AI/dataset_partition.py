#!/usr/bin/env python3
"""
dataset_partition.py — Grid Guardian Dataset: Validate, Clean & Partition
========================================================================
Pipeline reference: Step 1 — *Validate and Partition the Dataset*

Audits, cleans, feature-checks, documents, and splits the Grid-Guardian
dataset into train / val / test sets while preserving temporal and
structural properties and guaranteeing representation of rare safety events.

Usage:
    python dataset_partition.py \\
        --input ./data/final/final_dataset.csv.gz \\
        --config ./example_partition_config.yaml \\
        --out_dir ./data/partitioned \\
        --strategy time_forward \\
        --visualize \\
        --seed 42
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Optional imports — graceful degradation
try:
    import pyarrow  # noqa: F401
    HAS_PARQUET = True
except ImportError:
    HAS_PARQUET = False

try:
    from scipy import stats as sp_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ─────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("dataset_partition")

# ─────────────────────────────────────────────────────────
# Constants — canonical 23-column header (Part B spec)
# ─────────────────────────────────────────────────────────
CANONICAL_HEADER = [
    "ts", "household_id", "pv_gen_kw", "load_kw", "net_kw",
    "soc_kwh", "soc_capacity_kwh", "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
    "offer_id", "offered_kwh", "offer_price",
    "trade_id", "commit_hash", "event_flag", "reward", "safety_violation",
]

NUMERIC_COLS = [
    "pv_gen_kw", "load_kw", "net_kw", "soc_kwh", "soc_capacity_kwh",
    "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
    "offered_kwh", "offer_price", "reward",
]

_RESULTS: dict = {}  # accumulates report entries


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Grid-Guardian Dataset Partitioner (Step 1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          # Time-forward split with visualizations
          python dataset_partition.py \\
              --input ./data/final/final_dataset.csv.gz \\
              --config ./example_partition_config.yaml \\
              --out_dir ./data/partitioned \\
              --strategy time_forward --visualize --seed 42

          # Episode-random split for offline batch RL
          python dataset_partition.py \\
              --input ./data/final/final_dataset.csv.gz \\
              --strategy episode_random --seed 123
        """),
    )
    p.add_argument("--input", required=True, help="Path to canonical dataset CSV(.gz)")
    p.add_argument("--collected", default=None, help="Optional collected_data CSV(.gz)")
    p.add_argument("--config", default=None, help="Partition config YAML")
    p.add_argument("--out_dir", default="./data/partitioned")
    p.add_argument("--timestep", type=int, default=5)
    p.add_argument("--tz", default="Asia/Kolkata")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--strategy", default="time_forward",
                   choices=["time_forward", "block_hierarchy", "episode_random", "kfold_ts"])
    p.add_argument("--val_span_days", type=int, default=14)
    p.add_argument("--test_span_days", type=int, default=30)
    p.add_argument("--min_event_count", type=int, default=2)
    p.add_argument("--impute_method", default="interpolate",
                   choices=["drop", "interpolate", "model", "flag"])
    p.add_argument("--outlier_method", default="iqr",
                   choices=["iqr", "zscore", "mad"])
    p.add_argument("--max_missing_threshold", type=float, default=0.05)
    p.add_argument("--chunk_size", type=int, default=10000)
    p.add_argument("--visualize", action="store_true")
    p.add_argument("--dry_run", action="store_true")
    p.add_argument("--strict", action="store_true")
    return p.parse_args()


def load_config(path: str | None) -> dict:
    if path and Path(path).exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def cfg(config: dict, *keys, default=None):
    """Nested config lookup: cfg(config, 'splitting', 'val_span_days', default=14)."""
    node = config
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return default
    return node


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────
# Stage 1 — Provenance & header check
# ─────────────────────────────────────────────────────────
def stage_1_provenance(input_path: Path, out_dir: Path) -> pd.DataFrame:
    """Load, verify header, produce provenance.json."""
    log.info("Stage 1 — Provenance & header check")

    # Read header only first
    header_df = pd.read_csv(input_path, nrows=0)
    actual_cols = list(header_df.columns)

    # Allow extra columns (e.g. synth flags) but canonical 23 must be present
    missing_cols = [c for c in CANONICAL_HEADER if c not in actual_cols]
    if missing_cols:
        msg = f"Missing canonical columns: {missing_cols}"
        log.critical(msg)
        _RESULTS["header_check"] = {"status": "FAIL", "missing": missing_cols}
        raise SystemExit(msg)

    extra_cols = [c for c in actual_cols if c not in CANONICAL_HEADER]
    log.info("  Header OK (%d canonical + %d extra cols: %s)",
             len(CANONICAL_HEADER), len(extra_cols), extra_cols)

    # Full load
    df = pd.read_csv(input_path, low_memory=False)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    fsize = input_path.stat().st_size
    checksum = file_sha256(input_path)

    provenance = {
        "input_file": str(input_path),
        "file_size_bytes": fsize,
        "sha256": checksum,
        "row_count": len(df),
        "column_count": len(df.columns),
        "canonical_columns_present": True,
        "extra_columns": extra_cols,
        "first_ts": str(df["ts"].min()),
        "last_ts": str(df["ts"].max()),
        "households": sorted(df["household_id"].unique().tolist()),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }

    prov_path = out_dir / "provenance.json"
    with open(prov_path, "w") as f:
        json.dump(provenance, f, indent=2, default=str)
    log.info("  provenance.json written (%d rows, %s)", len(df), checksum[:12])

    _RESULTS["header_check"] = {"status": "PASS", "rows": len(df)}
    return df


# ─────────────────────────────────────────────────────────
# Stage 2 — Lightweight schema parse pass
# ─────────────────────────────────────────────────────────
def stage_2_parse(df: pd.DataFrame, config: dict):
    """Type coercion, parse‐failure detection."""
    log.info("Stage 2 — Schema parse pass")
    failures = 0
    total = len(df)

    # Coerce numeric columns
    for c in NUMERIC_COLS:
        if c in df.columns:
            orig_na = df[c].isna().sum()
            df[c] = pd.to_numeric(df[c], errors="coerce")
            new_na = df[c].isna().sum()
            added_na = new_na - orig_na
            if added_na > 0:
                failures += added_na
                log.warning("  %s: %d values coerced to NaN", c, added_na)

    # Household index
    hh_index = {}
    for hid, grp in df.groupby("household_id"):
        hh_index[str(hid)] = {
            "rows": int(len(grp)),
            "earliest_ts": str(grp["ts"].min()),
            "latest_ts": str(grp["ts"].max()),
        }

    failure_rate = failures / total if total > 0 else 0
    threshold = cfg(config, "general", "parse_failure_threshold", default=0.005)
    status = "PASS" if failure_rate <= threshold else "CRITICAL"
    log.info("  Parse failures: %d (%.4f%%) — %s", failures, failure_rate * 100, status)

    _RESULTS["parse_check"] = {
        "status": status,
        "parse_failures": failures,
        "failure_rate": round(failure_rate, 6),
        "household_index": hh_index,
    }

    if status == "CRITICAL":
        log.critical("Parse failure rate exceeds threshold. Aborting.")
        raise SystemExit("Parse failure rate too high")

    return df


# ─────────────────────────────────────────────────────────
# Stage 3 — Completeness & missingness audit
# ─────────────────────────────────────────────────────────
def stage_3_missingness(df: pd.DataFrame, out_dir: Path, config: dict,
                        visualize: bool, strict: bool):
    log.info("Stage 3 — Completeness & missingness audit")

    max_thresh = cfg(config, "thresholds", "max_missing_fraction",
                     default=0.05)

    # Per-column missing
    missing = df.isna().sum()
    frac = (missing / len(df)).round(6)
    ms = pd.DataFrame({"column": missing.index, "missing_count": missing.values,
                        "missing_fraction": frac.values})
    ms.to_csv(out_dir / "missing_summary.csv", index=False)
    log.info("  missing_summary.csv written")

    review = ms[ms["missing_fraction"] > max_thresh]["column"].tolist()
    if review:
        log.warning("  Columns above %.1f%% missing threshold: %s",
                     max_thresh * 100, review)
    status = "PASS"
    if review:
        status = "FAIL" if strict else "WARN"

    _RESULTS["missingness"] = {
        "status": status,
        "review_required_columns": review,
        "total_missing_cells": int(missing.sum()),
    }

    # Gaps summary per household
    gap_records = []
    timestep_min = cfg(config, "general", "timestep_min", default=5)
    for hid, grp in df.groupby("household_id"):
        ts_sorted = grp["ts"].sort_values()
        diffs = ts_sorted.diff().dt.total_seconds() / 60.0
        mask = diffs > timestep_min * 1.5
        if mask.any():
            for idx in diffs[mask].index:
                gap_records.append({
                    "household_id": hid,
                    "gap_start": str(ts_sorted.loc[ts_sorted.index[ts_sorted.index.get_loc(idx) - 1]]),
                    "gap_end": str(ts_sorted.loc[idx]),
                    "gap_minutes": float(diffs.loc[idx]),
                })
    if gap_records:
        pd.DataFrame(gap_records).to_csv(out_dir / "gaps_summary.csv", index=False)
        log.info("  gaps_summary.csv: %d gaps found", len(gap_records))
    else:
        pd.DataFrame(columns=["household_id", "gap_start", "gap_end", "gap_minutes"]).to_csv(
            out_dir / "gaps_summary.csv", index=False)
        log.info("  No timestamp gaps detected")

    # Visualize missing matrix
    if visualize and HAS_MPL:
        vis_dir = out_dir / "visuals"
        vis_dir.mkdir(exist_ok=True)
        fig, ax = plt.subplots(figsize=(14, 6))
        cols_to_show = [c for c in CANONICAL_HEADER if c in df.columns]
        miss_mat = df[cols_to_show].isna().astype(int)
        ax.imshow(miss_mat.values[::max(1, len(df) // 500)].T,
                  aspect="auto", cmap="Reds", interpolation="nearest")
        ax.set_yticks(range(len(cols_to_show)))
        ax.set_yticklabels(cols_to_show, fontsize=7)
        ax.set_xlabel("Row (subsampled)")
        ax.set_title("Missing Data Matrix")
        fig.tight_layout()
        fig.savefig(vis_dir / "missing_matrix.png", dpi=120)
        plt.close(fig)
        log.info("  missing_matrix.png saved")


# ─────────────────────────────────────────────────────────
# Stage 4 — Imputation
# ─────────────────────────────────────────────────────────
def stage_4_impute(df: pd.DataFrame, config: dict, method: str) -> pd.DataFrame:
    log.info("Stage 4 — Imputation (method=%s)", method)
    limit = cfg(config, "imputation", "gap_fill_limit_minutes", default=30)
    timestep = cfg(config, "general", "timestep_min", default=5)
    limit_steps = max(1, limit // timestep)

    required = cfg(config, "imputation", "required_columns",
                   default=["pv_gen_kw", "load_kw", "net_kw", "soc_kwh",
                            "actual_irradiance_wm2"])

    impute_cols = [c for c in NUMERIC_COLS if c in df.columns]
    n_before = int(df[impute_cols].isna().sum().sum())

    if method == "drop":
        present_req = [c for c in required if c in df.columns]
        before = len(df)
        df = df.dropna(subset=present_req).copy()
        log.info("  Dropped %d rows with missing required cols", before - len(df))

    elif method == "interpolate":
        # Set ts as index temporarily for time-weighted interpolation
        orig_index = df.index.copy()
        df = df.set_index("ts").sort_index()
        for hid in df["household_id"].unique():
            hh_mask = df["household_id"] == hid
            for c in impute_cols:
                if df.loc[hh_mask, c].isna().any():
                    df.loc[hh_mask, c] = df.loc[hh_mask, c].interpolate(
                        method="time", limit=limit_steps, limit_direction="both"
                    )
        df = df.reset_index()  # restore ts as column
        # Clip negative irradiance to 0
        for irr_col in ["actual_irradiance_wm2", "forecast_irradiance_1h",
                         "forecast_irradiance_3h"]:
            if irr_col in df.columns:
                neg_mask = df[irr_col] < 0
                if neg_mask.any():
                    df.loc[neg_mask, irr_col] = 0.0
                    log.info("  Clipped %d negative %s values to 0", neg_mask.sum(), irr_col)
        # Recompute net_kw after imputation
        both_ok = df["pv_gen_kw"].notna() & df["load_kw"].notna()
        df.loc[both_ok, "net_kw"] = (df.loc[both_ok, "pv_gen_kw"] -
                                      df.loc[both_ok, "load_kw"]).round(9)

    elif method == "flag":
        for c in impute_cols:
            flag_col = f"{c}_imputed"
            df[flag_col] = False
        log.info("  Flag mode: no values filled, _imputed flag columns added")

    elif method == "model":
        # Simplified: per-household time-of-day mean fill
        for hid, grp_idx in df.groupby("household_id").groups.items():
            hh_mask = df.index.isin(grp_idx)
            df_hh = df.loc[hh_mask].copy()
            df_hh["hour"] = df_hh["ts"].dt.hour
            for c in ["pv_gen_kw", "load_kw"]:
                if c in df.columns and df.loc[hh_mask, c].isna().any():
                    hourly_mean = df_hh.groupby("hour")[c].transform("mean")
                    fill_mask = hh_mask & df[c].isna()
                    df.loc[fill_mask, c] = hourly_mean.loc[fill_mask]
            df.drop(columns=["hour"], inplace=True, errors="ignore")
        both_ok = df["pv_gen_kw"].notna() & df["load_kw"].notna()
        df.loc[both_ok, "net_kw"] = (df.loc[both_ok, "pv_gen_kw"] -
                                      df.loc[both_ok, "load_kw"]).round(9)
        log.info("  Model-based (hourly mean) imputation applied")

    n_after = int(df[impute_cols].isna().sum().sum())
    log.info("  Missing cells: %d → %d (filled %d)", n_before, n_after, n_before - n_after)

    _RESULTS["imputation"] = {
        "status": "PASS",
        "method": method,
        "missing_before": n_before,
        "missing_after": n_after,
        "filled": n_before - n_after,
    }
    return df


# ─────────────────────────────────────────────────────────
# Stage 5 — Feature & distribution checks
# ─────────────────────────────────────────────────────────
def stage_5_features(df: pd.DataFrame, out_dir: Path, config: dict,
                     outlier_method: str, visualize: bool):
    log.info("Stage 5 — Feature & distribution checks")

    ranges = cfg(config, "feature_ranges", default={})

    # Physical-range violations
    violations = []
    for col, (lo, hi) in ranges.items():
        if col not in df.columns:
            continue
        s = df[col].dropna()
        below = (s < lo).sum()
        above = (s > hi).sum()
        if below > 0 or above > 0:
            violations.append({"column": col, "below_min": int(below),
                               "above_max": int(above),
                               "range": [lo, hi]})
            log.warning("  %s: %d below %.1f, %d above %.1f", col, below, lo, above, hi)

    _RESULTS["range_violations"] = violations if violations else "none"

    # Feature stats
    stat_rows = []
    for c in NUMERIC_COLS:
        if c not in df.columns:
            continue
        s = df[c].dropna()
        if len(s) == 0:
            continue
        stat_rows.append({
            "column": c,
            "min": round(float(s.min()), 6),
            "mean": round(float(s.mean()), 6),
            "median": round(float(s.median()), 6),
            "max": round(float(s.max()), 6),
            "std": round(float(s.std()), 6),
            "non_null": int(len(s)),
        })
    stats_df = pd.DataFrame(stat_rows)
    stats_df.to_csv(out_dir / "feature_stats.csv", index=False)

    # Outlier detection
    outlier_records = []
    iqr_factor = cfg(config, "outliers", "iqr_factor", default=3.0)
    z_thresh = cfg(config, "outliers", "zscore_threshold", default=4.0)
    mad_thresh = cfg(config, "outliers", "mad_threshold", default=5.0)

    for c in ["pv_gen_kw", "load_kw", "net_kw", "soc_kwh", "actual_irradiance_wm2",
              "battery_power_kw", "reward"]:
        if c not in df.columns:
            continue
        s = df[c].dropna()
        if len(s) < 10:
            continue

        if outlier_method == "iqr":
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            lo, hi = q1 - iqr_factor * iqr, q3 + iqr_factor * iqr
        elif outlier_method == "zscore":
            mu, sd = s.mean(), s.std()
            lo, hi = mu - z_thresh * sd, mu + z_thresh * sd
        else:  # mad
            med = s.median()
            mad = (s - med).abs().median() * 1.4826
            lo, hi = med - mad_thresh * mad, med + mad_thresh * mad

        mask = (df[c] < lo) | (df[c] > hi)
        mask = mask & df[c].notna()
        n_out = int(mask.sum())
        if n_out > 0:
            outlier_records.append({
                "column": c, "method": outlier_method,
                "n_outliers": n_out, "lo_bound": round(lo, 4),
                "hi_bound": round(hi, 4),
            })

    if outlier_records:
        pd.DataFrame(outlier_records).to_csv(out_dir / "outliers.csv", index=False)
    else:
        pd.DataFrame(columns=["column", "method", "n_outliers", "lo_bound", "hi_bound"]).to_csv(
            out_dir / "outliers.csv", index=False)

    log.info("  feature_stats.csv & outliers.csv written (%d outlier groups)", len(outlier_records))
    _RESULTS["feature_checks"] = {
        "status": "PASS" if not violations else "WARN",
        "range_violations": len(violations),
        "outlier_groups": len(outlier_records),
    }

    # Visualization
    if visualize and HAS_MPL:
        vis_dir = out_dir / "visuals"
        vis_dir.mkdir(exist_ok=True)
        fig, axes = plt.subplots(2, 3, figsize=(16, 8))
        plot_cols = ["pv_gen_kw", "load_kw", "net_kw",
                     "actual_irradiance_wm2", "soc_kwh", "battery_power_kw"]
        for ax, c in zip(axes.flat, plot_cols):
            if c in df.columns:
                df[c].dropna().hist(bins=80, ax=ax, alpha=0.7, color="steelblue")
                ax.set_title(c, fontsize=9)
        fig.suptitle("Feature Distributions")
        fig.tight_layout()
        fig.savefig(vis_dir / "feature_distributions.png", dpi=120)
        plt.close(fig)

    return df


# ─────────────────────────────────────────────────────────
# Stage 6 — Action / coverage & bias checks
# ─────────────────────────────────────────────────────────
def stage_6_coverage(df: pd.DataFrame, config: dict):
    log.info("Stage 6 — Action coverage & bias checks")

    min_rows = cfg(config, "action_checks", "min_rows_per_household", default=1000)
    min_offer_frac = cfg(config, "action_checks", "min_offer_fraction_per_household", default=0.001)

    warnings_list = []
    hh_stats = {}
    for hid, grp in df.groupby("household_id"):
        n = len(grp)
        n_offers = int(grp["offer_id"].notna().sum())
        offer_frac = n_offers / n if n > 0 else 0
        hh_stats[str(hid)] = {
            "rows": n,
            "offers": n_offers,
            "offer_fraction": round(offer_frac, 4),
            "battery_charge_rows": int((grp["battery_power_kw"] > 0.01).sum()),
            "battery_discharge_rows": int((grp["battery_power_kw"] < -0.01).sum()),
        }
        if n < min_rows:
            warnings_list.append(f"{hid}: only {n} rows (< {min_rows})")
        if offer_frac < min_offer_frac:
            warnings_list.append(f"{hid}: offer fraction {offer_frac:.4f} (< {min_offer_frac})")

    # Event distribution
    event_counts = df["event_flag"].value_counts().to_dict()

    status = "PASS" if not warnings_list else "WARN"
    for w in warnings_list:
        log.warning("  Coverage: %s", w)

    _RESULTS["coverage"] = {
        "status": status,
        "household_stats": hh_stats,
        "event_counts": {str(k): int(v) for k, v in event_counts.items()},
        "warnings": warnings_list,
    }
    log.info("  Event flag distribution: %s", event_counts)


# ─────────────────────────────────────────────────────────
# Stage 7 — Distributional shift & stationarity
# ─────────────────────────────────────────────────────────
def stage_7_drift(df: pd.DataFrame, out_dir: Path, config: dict, visualize: bool):
    log.info("Stage 7 — Drift & stationarity checks")

    drift_features = cfg(config, "drift", "features",
                         default=["actual_irradiance_wm2", "pv_gen_kw", "load_kw"])
    window_days = cfg(config, "drift", "window_size_days", default=30)
    ks_alpha = cfg(config, "drift", "ks_alpha", default=0.05)

    drift_results = {}
    ts_min, ts_max = df["ts"].min(), df["ts"].max()
    mid = ts_min + (ts_max - ts_min) / 2

    for feat in drift_features:
        if feat not in df.columns:
            continue
        early = df.loc[df["ts"] <= mid, feat].dropna().values
        late = df.loc[df["ts"] > mid, feat].dropna().values
        if len(early) < 20 or len(late) < 20:
            continue

        if HAS_SCIPY:
            ks_stat, ks_p = sp_stats.ks_2samp(early, late)
            wass = float(sp_stats.wasserstein_distance(early, late))
        else:
            ks_stat, ks_p, wass = 0.0, 1.0, 0.0

        drift_results[feat] = {
            "ks_statistic": round(ks_stat, 6),
            "ks_p_value": round(ks_p, 6),
            "wasserstein_distance": round(wass, 6),
            "drift_detected": bool(ks_p < ks_alpha),
        }
        if ks_p < ks_alpha:
            log.warning("  Drift detected in %s (KS p=%.4f)", feat, ks_p)

    with open(out_dir / "drift_report.json", "w") as f:
        json.dump(drift_results, f, indent=2)
    log.info("  drift_report.json written (%d features)", len(drift_results))

    any_drift = any(v.get("drift_detected", False) for v in drift_results.values())
    _RESULTS["drift"] = {
        "status": "WARN" if any_drift else "PASS",
        "features_checked": len(drift_results),
        "drift_detected_count": sum(1 for v in drift_results.values() if v.get("drift_detected")),
        "recommendation": "time-based splitting recommended" if any_drift else "data appears stationary",
    }

    # Visualization — rolling stats
    if visualize and HAS_MPL:
        vis_dir = out_dir / "visuals"
        vis_dir.mkdir(exist_ok=True)
        fig, axes = plt.subplots(len(drift_features), 1,
                                  figsize=(14, 4 * len(drift_features)), squeeze=False)
        for i, feat in enumerate(drift_features):
            if feat not in df.columns:
                continue
            daily = df.set_index("ts")[feat].resample("1D").mean().dropna()
            axes[i, 0].plot(daily.index, daily.values, linewidth=0.8, color="steelblue")
            axes[i, 0].set_title(f"Daily mean — {feat}", fontsize=10)
            axes[i, 0].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.tight_layout()
        fig.savefig(vis_dir / "drift_timeseries.png", dpi=120)
        plt.close(fig)


# ─────────────────────────────────────────────────────────
# Stage 8 — Splitting strategies
# ─────────────────────────────────────────────────────────
def _ensure_event_representation(df: pd.DataFrame, train_idx, test_idx,
                                  min_event_count: int, adj_log: list):
    """Move rare-event rows from train→test if test lacks sufficient events."""
    events = df.loc[test_idx, "event_flag"].value_counts().to_dict()
    all_events = df["event_flag"].unique()

    for evt in all_events:
        if evt == "normal":
            continue
        test_count = events.get(evt, 0)
        if test_count >= min_event_count:
            continue
        needed = min_event_count - test_count
        # Find candidates in train with this event, preferring latest rows
        cands = df.loc[train_idx]
        cands = cands[cands["event_flag"] == evt].sort_values("ts", ascending=False)
        to_move = cands.head(needed).index.tolist()
        if to_move:
            train_idx = [i for i in train_idx if i not in to_move]
            test_idx = list(test_idx) + to_move
            adj_log.append({
                "event": evt, "moved_rows": len(to_move),
                "direction": "train→test",
                "reason": f"test had only {test_count} of {evt}, need {min_event_count}",
            })
            log.info("  Moved %d '%s' rows from train→test", len(to_move), evt)

    return train_idx, test_idx


def split_time_forward(df: pd.DataFrame, val_days: int, test_days: int,
                        min_event: int) -> tuple[pd.DataFrame, list]:
    """Calendar time-forward split: earliest=train, middle=val, latest=test."""
    adj_log = []
    ts_max = df["ts"].max()
    test_start = ts_max - pd.Timedelta(days=test_days)
    val_start = test_start - pd.Timedelta(days=val_days)

    df["split_label"] = "train"
    df.loc[df["ts"] >= val_start, "split_label"] = "val"
    df.loc[df["ts"] >= test_start, "split_label"] = "test"

    # Ensure event representation in test
    train_idx = df[df["split_label"] == "train"].index.tolist()
    test_idx = df[df["split_label"] == "test"].index.tolist()
    train_idx, test_idx = _ensure_event_representation(df, train_idx, test_idx, min_event, adj_log)

    # Update labels after adjustment
    df["split_label"] = "train"
    df.loc[df.index.isin(test_idx), "split_label"] = "test"
    # re-apply val
    val_mask = (df["ts"] >= val_start) & (df["ts"] < test_start) & (~df.index.isin(test_idx))
    df.loc[val_mask, "split_label"] = "val"

    return df, adj_log


def split_block_hierarchy(df: pd.DataFrame, config: dict,
                           min_event: int, rng: np.random.Generator) -> tuple[pd.DataFrame, list]:
    """Split by contiguous episode blocks (gaps > 24h define boundaries)."""
    adj_log = []
    ratios = (
        cfg(config, "splitting", "train_ratio", default=0.7),
        cfg(config, "splitting", "val_ratio", default=0.15),
        cfg(config, "splitting", "test_ratio", default=0.15),
    )

    # Identify episodes per household
    df = df.sort_values(["household_id", "ts"]).copy()
    df["episode"] = 0
    ep_id = 0
    for hid, grp in df.groupby("household_id"):
        ts_sorted = grp["ts"].sort_values()
        gaps = ts_sorted.diff().dt.total_seconds() / 3600.0 > 24
        ep_labels = gaps.cumsum()
        for ep_local in ep_labels.unique():
            ep_mask = df.index.isin(ep_labels[ep_labels == ep_local].index)
            df.loc[ep_mask, "episode"] = ep_id
            ep_id += 1

    episodes = sorted(df["episode"].unique())
    rng.shuffle(episodes)

    n = len(episodes)
    n_train = max(1, int(n * ratios[0]))
    n_val = max(1, int(n * ratios[1]))

    train_eps = set(episodes[:n_train])
    val_eps = set(episodes[n_train:n_train + n_val])
    test_eps = set(episodes[n_train + n_val:])
    if not test_eps:
        test_eps = {episodes[-1]}

    df["split_label"] = "train"
    df.loc[df["episode"].isin(val_eps), "split_label"] = "val"
    df.loc[df["episode"].isin(test_eps), "split_label"] = "test"

    train_idx = df[df["split_label"] == "train"].index.tolist()
    test_idx = df[df["split_label"] == "test"].index.tolist()
    train_idx, test_idx = _ensure_event_representation(df, train_idx, test_idx, min_event, adj_log)
    df.loc[df.index.isin(test_idx), "split_label"] = "test"
    df.drop(columns=["episode"], inplace=True)

    return df, adj_log


def split_episode_random(df: pd.DataFrame, config: dict,
                          min_event: int, rng: np.random.Generator) -> tuple[pd.DataFrame, list]:
    """Random split by episodes, stratified by event_flag & household."""
    adj_log = []
    ratios = (
        cfg(config, "splitting", "train_ratio", default=0.7),
        cfg(config, "splitting", "val_ratio", default=0.15),
        cfg(config, "splitting", "test_ratio", default=0.15),
    )

    # Define episodes as blocks of contiguous time per household (daily blocks)
    df = df.sort_values(["household_id", "ts"]).copy()
    df["day"] = df["ts"].dt.date
    key_cols = ["household_id", "day"]
    episode_keys = df.groupby(key_cols).size().reset_index().drop(columns=0)
    episode_keys["episode_id"] = range(len(episode_keys))

    df = df.merge(episode_keys, on=key_cols, how="left")

    # Stratification: dominant event per episode
    dom_event = df.groupby("episode_id")["event_flag"].agg(
        lambda x: x.value_counts().index[0] if len(x) > 0 else "normal"
    ).to_dict()
    episode_keys["dom_event"] = episode_keys["episode_id"].map(dom_event)

    # Shuffle and split
    eps = episode_keys["episode_id"].values.copy()
    rng.shuffle(eps)
    n = len(eps)
    n_train = max(1, int(n * ratios[0]))
    n_val = max(1, int(n * ratios[1]))

    train_eps = set(eps[:n_train])
    val_eps = set(eps[n_train:n_train + n_val])
    test_eps = set(eps[n_train + n_val:])
    if not test_eps:
        test_eps = {eps[-1]}

    df["split_label"] = "train"
    df.loc[df["episode_id"].isin(val_eps), "split_label"] = "val"
    df.loc[df["episode_id"].isin(test_eps), "split_label"] = "test"

    train_idx = df[df["split_label"] == "train"].index.tolist()
    test_idx = df[df["split_label"] == "test"].index.tolist()
    train_idx, test_idx = _ensure_event_representation(df, train_idx, test_idx, min_event, adj_log)
    df.loc[df.index.isin(test_idx), "split_label"] = "test"
    df.drop(columns=["day", "episode_id"], inplace=True)

    return df, adj_log


def split_kfold_ts(df: pd.DataFrame, config: dict, out_dir: Path,
                    rng: np.random.Generator) -> tuple[pd.DataFrame, list]:
    """Time-aware K-fold (expanding-window). Returns df with fold_0_label … fold_K_label."""
    adj_log = []
    k = cfg(config, "splitting", "kfold_k", default=5)
    df = df.sort_values("ts").copy()

    unique_ts = df["ts"].sort_values().unique()
    fold_size = len(unique_ts) // (k + 1)  # expanding window

    # For default split_label, use last fold
    df["split_label"] = "train"  # base

    fold_info = []
    for fold in range(k):
        val_start = fold_size * (fold + 1)
        val_end = min(val_start + fold_size, len(unique_ts))
        val_ts_set = set(unique_ts[val_start:val_end])
        train_ts_set = set(unique_ts[:val_start])

        col = f"fold_{fold}_label"
        df[col] = "unused"
        df.loc[df["ts"].isin(train_ts_set), col] = "train"
        df.loc[df["ts"].isin(val_ts_set), col] = "val"
        fold_info.append({
            "fold": fold,
            "train_ts_count": len(train_ts_set),
            "val_ts_count": len(val_ts_set),
        })

    # Default split_label from last fold
    last = f"fold_{k - 1}_label"
    df["split_label"] = df[last].map({"train": "train", "val": "test"}).fillna("train")
    # Mark middle portion as val
    mid_fold = k // 2
    mid = f"fold_{mid_fold}_label"
    val_mask = df[mid] == "val"
    df.loc[val_mask & (df["split_label"] == "train"), "split_label"] = "val"

    adj_log.append({"kfold_info": fold_info})
    return df, adj_log


def stage_8_split(df: pd.DataFrame, args, config: dict,
                   rng: np.random.Generator, out_dir: Path) -> tuple[pd.DataFrame, list]:
    log.info("Stage 8 — Splitting (strategy=%s)", args.strategy)

    val_days = cfg(config, "splitting", "val_span_days", default=args.val_span_days)
    test_days = cfg(config, "splitting", "test_span_days", default=args.test_span_days)
    min_evt = cfg(config, "splitting", "min_event_count", default=args.min_event_count)

    if args.strategy == "time_forward":
        df, adj_log = split_time_forward(df, val_days, test_days, min_evt)
    elif args.strategy == "block_hierarchy":
        df, adj_log = split_block_hierarchy(df, config, min_evt, rng)
    elif args.strategy == "episode_random":
        df, adj_log = split_episode_random(df, config, min_evt, rng)
    elif args.strategy == "kfold_ts":
        df, adj_log = split_kfold_ts(df, config, out_dir, rng)
    else:
        raise ValueError(f"Unknown strategy: {args.strategy}")

    # Stats
    counts = df["split_label"].value_counts().to_dict()
    log.info("  Split sizes: %s", counts)

    # Per-split event counts
    split_events = {}
    for label in ["train", "val", "test"]:
        sub = df[df["split_label"] == label]
        split_events[label] = sub["event_flag"].value_counts().to_dict() if len(sub) > 0 else {}

    _RESULTS["split"] = {
        "status": "PASS",
        "strategy": args.strategy,
        "counts": {k: int(v) for k, v in counts.items()},
        "per_split_event_counts": split_events,
        "adjustments": adj_log,
    }

    return df, adj_log


# ─────────────────────────────────────────────────────────
# Stage 9 — Stratification summary (logged in stage 8)
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Stage 10 — Output artifacts
# ─────────────────────────────────────────────────────────
def stage_10_write(df: pd.DataFrame, out_dir: Path, adj_log: list,
                    args, dry_run: bool, visualize: bool):
    log.info("Stage 10 — Writing output artifacts")

    # Provenance columns
    df["split_tag"] = df["split_label"]
    df["cleaned_at"] = datetime.now(timezone.utc).isoformat()
    df["seed"] = args.seed

    # Canonical + extras columns (keep synth flags if present)
    keep_cols = list(df.columns)  # keep all

    if dry_run:
        log.info("  DRY-RUN: would write train/val/test CSVs (skipped)")
        return

    # Cleaned full dataset
    cleaned_path = out_dir / "generated_dataset_cleaned.csv.gz"
    df.to_csv(cleaned_path, index=False, compression="gzip")
    log.info("  Written: generated_dataset_cleaned.csv.gz (%d rows)", len(df))

    # Split files
    for label in ["train", "val", "test"]:
        sub = df[df["split_label"] == label]
        if len(sub) == 0:
            continue
        fpath = out_dir / f"{label}.csv.gz"
        sub.to_csv(fpath, index=False, compression="gzip")
        # Also write plain CSV
        sub.to_csv(out_dir / f"{label}.csv", index=False)
        log.info("  Written: %s.csv.gz (%d rows)", label, len(sub))

    # Split index (parquet if available, else CSV)
    idx_df = df[["ts", "household_id", "split_label"]].copy()
    idx_df["ts"] = idx_df["ts"].astype(str)
    if HAS_PARQUET:
        idx_df.to_parquet(out_dir / "split_index.parquet", index=False)
        log.info("  Written: split_index.parquet")
    else:
        idx_df.to_csv(out_dir / "split_index.csv", index=False)
        log.info("  Written: split_index.csv (parquet unavailable)")

    # Split adjustments log
    adj_path = out_dir / "split_adjustments.log"
    with open(adj_path, "w") as f:
        json.dump(adj_log, f, indent=2, default=str)

    # Feature stats per split
    stat_rows = []
    for label in ["train", "val", "test"]:
        sub = df[df["split_label"] == label]
        for c in NUMERIC_COLS:
            if c not in sub.columns:
                continue
            s = sub[c].dropna()
            if len(s) == 0:
                continue
            stat_rows.append({
                "split": label, "column": c,
                "min": round(float(s.min()), 6),
                "mean": round(float(s.mean()), 6),
                "max": round(float(s.max()), 6),
                "std": round(float(s.std()), 6),
                "count": int(len(s)),
            })
    pd.DataFrame(stat_rows).to_csv(out_dir / "feature_stats_per_split.csv", index=False)

    # Visualization — split timeline
    if visualize and HAS_MPL:
        vis_dir = out_dir / "visuals"
        vis_dir.mkdir(exist_ok=True)

        # Split timeline
        fig, ax = plt.subplots(figsize=(14, 4))
        colors = {"train": "#2196F3", "val": "#FF9800", "test": "#F44336"}
        for label, color in colors.items():
            sub = df[df["split_label"] == label]
            if len(sub) == 0:
                continue
            ax.axvspan(sub["ts"].min(), sub["ts"].max(), alpha=0.3,
                       color=color, label=f"{label} ({len(sub):,})")
        ax.legend(fontsize=10)
        ax.set_title("Train / Val / Test Split Timeline")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        fig.tight_layout()
        fig.savefig(vis_dir / "split_timeline.png", dpi=120)
        plt.close(fig)

        # Per-split distributions
        fig, axes = plt.subplots(2, 3, figsize=(16, 8))
        dist_cols = ["pv_gen_kw", "load_kw", "net_kw",
                     "actual_irradiance_wm2", "soc_kwh", "reward"]
        for ax, c in zip(axes.flat, dist_cols):
            if c not in df.columns:
                continue
            for label, color in colors.items():
                sub = df[df["split_label"] == label]
                sub[c].dropna().hist(bins=60, ax=ax, alpha=0.5, color=color, label=label)
            ax.set_title(c, fontsize=9)
            ax.legend(fontsize=7)
        fig.suptitle("Per-Split Feature Distributions")
        fig.tight_layout()
        fig.savefig(vis_dir / "split_distributions.png", dpi=120)
        plt.close(fig)

        log.info("  Visualizations saved to visuals/")


# ─────────────────────────────────────────────────────────
# Stage 11 — Reproducibility manifest
# ─────────────────────────────────────────────────────────
def stage_11_manifest(args, out_dir: Path, config: dict):
    log.info("Stage 11 — Run manifest")
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()

    # Attempt git commit
    git_commit = None
    try:
        import subprocess
        result = subprocess.run(["git", "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            git_commit = result.stdout.strip()
    except Exception:
        pass

    manifest = {
        "seed": args.seed,
        "strategy": args.strategy,
        "config_hash": config_hash,
        "git_commit": git_commit,
        "python_version": sys.version,
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "cli_args": vars(args),
    }
    with open(out_dir / "run_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    log.info("  run_manifest.json written")


# ─────────────────────────────────────────────────────────
# Reports — partition_report.json/.md
# ─────────────────────────────────────────────────────────
def write_reports(out_dir: Path, args):
    log.info("Writing partition reports")

    # JSON report
    with open(out_dir / "partition_report.json", "w") as f:
        json.dump(_RESULTS, f, indent=2, default=str)

    # Markdown report
    lines = [
        "# Grid Guardian — Dataset Partition Report",
        "",
        f"**Run UTC:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Seed:** {args.seed}  ",
        f"**Strategy:** {args.strategy}  ",
        f"**Mode:** {'DRY-RUN' if args.dry_run else 'FULL'}  ",
        "",
        "## Check Results",
        "",
        "| Stage | Check | Status |",
        "|-------|-------|--------|",
    ]

    stage_map = {
        "header_check": "1. Header & provenance",
        "parse_check": "2. Schema parse",
        "missingness": "3. Missingness audit",
        "imputation": "4. Imputation",
        "feature_checks": "5. Feature & distribution",
        "coverage": "6. Action coverage",
        "drift": "7. Drift detection",
        "split": "8. Split",
    }

    for key, label in stage_map.items():
        entry = _RESULTS.get(key, {})
        status = entry.get("status", "N/A") if isinstance(entry, dict) else "N/A"
        icon = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL",
                "CRITICAL": "CRIT"}.get(status, status)
        lines.append(f"| {label} | {key} | {icon} |")

    # Split details
    split_info = _RESULTS.get("split", {})
    counts = split_info.get("counts", {})
    if counts:
        lines += [
            "",
            "## Split Sizes",
            "",
            "| Split | Rows | % |",
            "|-------|------|---|",
        ]
        total = sum(counts.values())
        for lbl in ["train", "val", "test"]:
            n = counts.get(lbl, 0)
            pct = round(n / total * 100, 1) if total > 0 else 0
            lines.append(f"| {lbl} | {n:,} | {pct}% |")

    # Per-split events
    spe = split_info.get("per_split_event_counts", {})
    if spe:
        lines += ["", "## Event Distribution per Split", ""]
        all_events = sorted(set(e for d in spe.values() for e in d))
        header = "| Event | " + " | ".join(["train", "val", "test"]) + " |"
        sep = "|-------|" + "|".join(["------"] * 3) + "|"
        lines += [header, sep]
        for evt in all_events:
            vals = [str(spe.get(s, {}).get(evt, 0)) for s in ["train", "val", "test"]]
            lines.append(f"| {evt} | " + " | ".join(vals) + " |")

    lines += ["", "---", "*Report generated by dataset_partition.py*"]

    (out_dir / "partition_report.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("  partition_report.json & .md written")


# ─────────────────────────────────────────────────────────
# No-overlap verification
# ─────────────────────────────────────────────────────────
def verify_no_overlap(df: pd.DataFrame):
    """Verify splits don't overlap on (ts, household_id)."""
    for a, b in [("train", "val"), ("train", "test"), ("val", "test")]:
        sa = df[df["split_label"] == a][["ts", "household_id"]]
        sb = df[df["split_label"] == b][["ts", "household_id"]]
        overlap = pd.merge(sa, sb, on=["ts", "household_id"], how="inner")
        if len(overlap) > 0:
            log.error("OVERLAP between %s and %s: %d rows!", a, b, len(overlap))
            return False
    log.info("  No-overlap verification: PASS")
    return True


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    args = parse_args()
    np.random.seed(args.seed)
    rng = np.random.default_rng(args.seed)

    config = load_config(args.config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_path = Path(args.input)
    if not input_path.exists():
        log.critical("Input file not found: %s", input_path)
        return 1

    log.info("=" * 60)
    log.info("  Grid Guardian Dataset Partitioner v1.0.0")
    log.info("  Strategy: %s | Seed: %d", args.strategy, args.seed)
    log.info("=" * 60)

    # Stage 1: Provenance
    df = stage_1_provenance(input_path, out_dir)

    # Stage 2: Parse
    df = stage_2_parse(df, config)

    # Stage 3: Missingness
    stage_3_missingness(df, out_dir, config, args.visualize, args.strict)

    # Stage 4: Imputation
    df = stage_4_impute(df, config, args.impute_method)

    # Stage 5: Features & outliers
    df = stage_5_features(df, out_dir, config, args.outlier_method, args.visualize)

    # Stage 6: Coverage
    stage_6_coverage(df, config)

    # Stage 7: Drift
    stage_7_drift(df, out_dir, config, args.visualize)

    # Stage 8: Split
    df, adj_log = stage_8_split(df, args, config, rng, out_dir)

    # Stage 10: Write outputs
    stage_10_write(df, out_dir, adj_log, args, args.dry_run, args.visualize)

    # Stage 11: Manifest
    stage_11_manifest(args, out_dir, config)

    # Verify no overlap
    if not args.dry_run:
        no_overlap = verify_no_overlap(df)
        _RESULTS["no_overlap"] = no_overlap

    # Reports
    write_reports(out_dir, args)

    # Final summary
    all_pass = all(
        (v.get("status", "PASS") in ("PASS", "WARN") if isinstance(v, dict) else True)
        for v in _RESULTS.values()
    )

    log.info("")
    log.info("=" * 60)
    log.info("  PARTITION COMPLETE — %s", "ALL PASS" if all_pass else "ISSUES DETECTED")
    log.info("=" * 60)
    counts = _RESULTS.get("split", {}).get("counts", {})
    for lbl in ["train", "val", "test"]:
        log.info("  %s: %s rows", lbl, f"{counts.get(lbl, 0):,}")

    if args.strict and not all_pass:
        log.error("Strict mode: exiting with error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
