#!/usr/bin/env python3
"""
merge_check.py — Merge Compatibility Check
============================================
Demonstrates merging collected_data.csv.gz with a small generator-config
sample and reports any mismatches (missing timestamps, tz mismatch, etc.).

Usage:
    python merge_check.py \
        --collected ./data/collected/collected_data.csv.gz \
        --generator-config sample_generator_config.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yaml


CANONICAL_COLUMNS = [
    "ts", "lat", "lon", "source",
    "actual_irradiance_wm2", "temperature_C", "cloud_cover_percent",
    "wind_speed_m_s", "pv_gen_kw", "load_kw", "voltage_v", "current_a", "tz",
]


def load_collected(path: str) -> pd.DataFrame:
    """Load collected_data.csv.gz and parse timestamps."""
    df = pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def load_generator_config(path: str) -> dict:
    """Load a sample generator config YAML."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def check_schema(df: pd.DataFrame) -> List[str]:
    """Check that all canonical columns are present."""
    issues = []
    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            issues.append(f"Missing column: '{col}'")
    return issues


def check_timestamps(df: pd.DataFrame, expected_timestep: int = 5) -> List[str]:
    """Check timestamp alignment and continuity."""
    issues = []

    # Check UTC
    if df["ts"].dt.tz is None:
        issues.append("Timestamps are timezone-naive (expected UTC)")
    elif str(df["ts"].dt.tz) != "UTC":
        issues.append(f"Timestamps timezone is '{df['ts'].dt.tz}' (expected UTC)")

    # Check alignment
    offsets = df["ts"].dt.minute % expected_timestep
    bad = (offsets != 0).sum()
    if bad > 0:
        issues.append(f"{bad} timestamps not aligned to {expected_timestep}-min grid")

    # Check for duplicates
    dupes = df["ts"].duplicated().sum()
    if dupes > 0:
        issues.append(f"{dupes} duplicate timestamps")

    # Check for gaps
    diffs = df["ts"].diff().dropna()
    expected_diff = pd.Timedelta(minutes=expected_timestep)
    gaps = diffs[diffs > expected_diff]
    if len(gaps) > 0:
        issues.append(f"{len(gaps)} gaps found (expected continuous {expected_timestep}-min)")
        # Report top 5 largest
        for idx in gaps.nlargest(5).index:
            start = df["ts"].iloc[idx - 1]
            end = df["ts"].iloc[idx]
            issues.append(f"  Gap: {start} → {end} ({diffs.iloc[idx]})")

    return issues


def check_irradiance(df: pd.DataFrame) -> List[str]:
    """Check irradiance column quality."""
    issues = []
    if "actual_irradiance_wm2" not in df.columns:
        issues.append("actual_irradiance_wm2 column missing")
        return issues

    na_count = df["actual_irradiance_wm2"].isna().sum()
    total = len(df)
    if na_count > 0:
        pct = na_count / total * 100
        issues.append(f"actual_irradiance_wm2: {na_count}/{total} missing ({pct:.1f}%)")

    neg = (df["actual_irradiance_wm2"] < 0).sum()
    if neg > 0:
        issues.append(f"actual_irradiance_wm2: {neg} negative values")

    return issues


def check_household_merge(df: pd.DataFrame, gen_config: dict) -> List[str]:
    """Test merge compatibility with generator config households."""
    issues = []
    households = gen_config.get("households", [])
    if not households:
        issues.append("Generator config has no 'households' section — skipping household check")
        return issues

    hh_ids = [h.get("household_id", h.get("id", "")) for h in households]

    has_pv = "pv_gen_kw" in df.columns and df["pv_gen_kw"].notna().any()
    has_load = "load_kw" in df.columns and df["load_kw"].notna().any()
    has_hh = "household_id" in df.columns

    if has_pv and has_load:
        # Case A: measured PV/load
        if has_hh:
            collected_ids = set(df["household_id"].dropna().unique())
            for hid in hh_ids:
                if hid not in collected_ids:
                    issues.append(f"household_id '{hid}' from generator config not found in collected data")
        else:
            issues.append(
                "Collected data has pv_gen_kw and load_kw but no household_id column. "
                "Generator may not be able to match measurements to specific households."
            )
    else:
        # Case B: weather-only — informational, not a failure
        pass  # Weather-only mode is valid; generator synthesizes PV/load

    # Check timezone consistency
    if "tz" in df.columns:
        tz_val = df["tz"].dropna().unique()
        gen_tz = gen_config.get("timezone", gen_config.get("tz"))
        if gen_tz and len(tz_val) > 0:
            if gen_tz not in tz_val:
                issues.append(
                    f"Timezone mismatch: collected='{tz_val}', generator='{gen_tz}'"
                )

    return issues


GENERATED_HEADER = [
    "ts", "household_id", "pv_gen_kw", "load_kw", "net_kw",
    "soc_kwh", "soc_capacity_kwh", "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
    "offer_id", "offered_kwh", "offer_price",
    "trade_id", "commit_hash", "event_flag", "reward", "safety_violation",
]


def check_generated_data(path: str, gen_config: dict) -> List[str]:
    """Validate generated_dataset.csv.gz against Part B requirements."""
    issues = []
    df = pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None,
                     low_memory=False)

    # Header check
    if list(df.columns) != GENERATED_HEADER:
        missing = set(GENERATED_HEADER) - set(df.columns)
        extra = set(df.columns) - set(GENERATED_HEADER)
        if missing:
            issues.append(f"Missing columns: {missing}")
        if extra:
            issues.append(f"Extra columns: {extra}")
        col_order_ok = False
    else:
        col_order_ok = True

    # Row count
    households = gen_config.get("households", [])
    hh_ids = {h["household_id"] for h in households}
    ds_hh_ids = set(df["household_id"].unique())
    missing_hh = hh_ids - ds_hh_ids
    if missing_hh:
        issues.append(f"Households in config but not in data: {missing_hh}")

    # Mass balance
    dfn = df.copy()
    dfn["pv_gen_kw"] = pd.to_numeric(dfn["pv_gen_kw"], errors="coerce")
    dfn["load_kw"] = pd.to_numeric(dfn["load_kw"], errors="coerce")
    dfn["net_kw"] = pd.to_numeric(dfn["net_kw"], errors="coerce")
    valid = dfn.dropna(subset=["pv_gen_kw", "load_kw", "net_kw"])
    violations = (abs(valid["net_kw"] - (valid["pv_gen_kw"] - valid["load_kw"])) > 1e-4).sum()
    if violations > 0:
        issues.append(f"Mass balance violations: {violations}")

    # SoC bounds
    dfn["soc_kwh"] = pd.to_numeric(dfn["soc_kwh"], errors="coerce")
    for h in households:
        hid = h["household_id"]
        lo, hi = h.get("soc_min", 0), h.get("soc_max", 999)
        hh_soc = dfn.loc[dfn["household_id"] == hid, "soc_kwh"].dropna()
        below = (hh_soc < lo - 0.01).sum()
        above = (hh_soc > hi + 0.01).sum()
        if below > 0:
            issues.append(f"{hid}: {below} SoC values below min {lo}")
        if above > 0:
            issues.append(f"{hid}: {above} SoC values above max {hi}")

    return issues, col_order_ok, df


def check_collected_generated_join(collected_path: str, generated_path: str) -> List[str]:
    """Check that generated data can be joined with collected data on ts."""
    issues = []
    cdf = pd.read_csv(collected_path,
                      compression="gzip" if collected_path.endswith(".gz") else None)
    cdf["ts"] = pd.to_datetime(cdf["ts"], utc=True)
    gdf = pd.read_csv(generated_path,
                      compression="gzip" if generated_path.endswith(".gz") else None,
                      low_memory=False)
    gdf["ts"] = pd.to_datetime(gdf["ts"], utc=True)

    c_ts = set(cdf["ts"].unique())
    g_ts = set(gdf["ts"].unique())

    in_gen_not_col = g_ts - c_ts
    if in_gen_not_col:
        issues.append(f"{len(in_gen_not_col)} generated timestamps not in collected data")

    overlap = g_ts & c_ts
    coverage = len(overlap) / max(1, len(g_ts)) * 100
    if coverage < 90:
        issues.append(f"Only {coverage:.1f}% timestamp overlap between collected and generated")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Merge compatibility check")
    parser.add_argument("--collected", required=True,
                        help="Path to collected_data.csv.gz")
    parser.add_argument("--generator-config", required=False, default=None,
                        help="Path to generator_config.yaml (optional)")
    parser.add_argument("--generated", required=False, default=None,
                        help="Path to generated_dataset.csv.gz (Part B)")
    parser.add_argument("--timestep", type=int, default=5,
                        help="Expected timestep in minutes")
    args = parser.parse_args()

    print("=" * 60)
    print("Grid Guardian — Merge Compatibility Check")
    print("=" * 60)

    # Load collected data
    print(f"\nLoading: {args.collected}")
    df = load_collected(args.collected)
    print(f"  Rows   : {len(df)}")
    print(f"  Columns: {list(df.columns)}")
    print(f"  Range  : {df['ts'].min()} → {df['ts'].max()}")

    all_issues: List[str] = []

    # Schema check
    print("\n--- Schema Check ---")
    issues = check_schema(df)
    all_issues.extend(issues)
    if issues:
        for i in issues:
            print(f"  [FAIL] {i}")
    else:
        print("  [PASS] All canonical columns present")

    # Timestamp check
    print("\n--- Timestamp Check ---")
    issues = check_timestamps(df, args.timestep)
    all_issues.extend(issues)
    if issues:
        for i in issues:
            print(f"  [WARN] {i}")
    else:
        print(f"  [PASS] Timestamps UTC, aligned to {args.timestep}-min, no gaps/dupes")

    # Irradiance check
    print("\n--- Irradiance Check ---")
    issues = check_irradiance(df)
    all_issues.extend(issues)
    if issues:
        for i in issues:
            print(f"  [WARN] {i}")
    else:
        print("  [PASS] Irradiance column present, numeric, non-negative")

    # Household merge check
    if args.generator_config:
        print("\n--- Household Merge Check ---")
        gen_config = load_generator_config(args.generator_config)
        issues = check_household_merge(df, gen_config)
        all_issues.extend(issues)
        for i in issues:
            severity = "[FAIL]" if "not found" in i or "mismatch" in i.lower() else "[INFO]"
            print(f"  {severity} {i}")
    else:
        print("\n--- Household Merge Check ---")
        print("  [SKIP] No --generator-config provided")

    # Generated data check (Part B)
    if args.generated and args.generator_config:
        print("\n--- Generated Data Check (Part B) ---")
        gen_config = load_generator_config(args.generator_config)
        issues, col_ok, gen_df = check_generated_data(args.generated, gen_config)
        all_issues.extend(issues)
        if col_ok:
            print(f"  [PASS] Generated header matches canonical order ({len(gen_df)} rows)")
        else:
            print("  [FAIL] Generated header mismatch")
        for i in issues:
            print(f"  [WARN] {i}")
        if not issues:
            print("  [PASS] Mass balance, SoC bounds, household coverage OK")

        # Join check
        print("\n--- Collected ↔ Generated Join Check ---")
        join_issues = check_collected_generated_join(args.collected, args.generated)
        all_issues.extend(join_issues)
        if join_issues:
            for i in join_issues:
                print(f"  [WARN] {i}")
        else:
            print("  [PASS] All generated timestamps found in collected data")

    # Summary
    print("\n" + "=" * 60)
    if all_issues:
        print(f"Total issues: {len(all_issues)}")
        for i in all_issues:
            print(f"  • {i}")
    else:
        print("All checks passed — data is ready for the generator.")
    print("=" * 60)

    return 0 if not all_issues else 1


if __name__ == "__main__":
    sys.exit(main())
