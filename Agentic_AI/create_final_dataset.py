#!/usr/bin/env python3
"""
create_final_dataset.py — Grid Guardian Final Dataset Merger
=============================================================
Merges collected_data.csv.gz (Part A) and generated_dataset.csv.gz (Part B)
into a single final_dataset.csv.gz that contains weather context alongside
per-household simulation outputs.

Usage:
    python create_final_dataset.py `
        --collected ./data/collected/collected_data.csv.gz `
        --generated ./data/generated/generated_dataset.csv.gz `
        --out ./data/final

Produces:
    final_dataset.csv.gz   — merged on 'ts', weather cols from collected joined
                              with per-household sim cols from generated
    final_metadata.json    — row counts, column list, date range, merge stats
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd


# -----------------------------------------------------------------------
# Canonical headers
# -----------------------------------------------------------------------

COLLECTED_WEATHER_COLS = [
    "actual_irradiance_wm2", "temperature_C", "cloud_cover_percent",
    "wind_speed_m_s",
]

FINAL_HEADER = [
    # From collected (weather context)
    "ts",
    "lat", "lon", "source", "tz",
    "temperature_C", "cloud_cover_percent", "wind_speed_m_s",
    # From generated (per-household simulation)
    "household_id",
    "pv_gen_kw", "load_kw", "net_kw",
    "soc_kwh", "soc_capacity_kwh", "battery_power_kw",
    "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2",
    "voltage_v", "current_a",
    "offer_id", "offered_kwh", "offer_price",
    "trade_id", "commit_hash",
    "event_flag", "reward", "safety_violation",
]


def load_collected(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def load_generated(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, compression="gzip" if path.endswith(".gz") else None,
                     low_memory=False)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def merge_datasets(collected_df: pd.DataFrame,
                   generated_df: pd.DataFrame) -> pd.DataFrame:
    """
    Left-join generated onto collected weather data on 'ts'.

    - collected has one row per timestamp (weather)
    - generated has N rows per timestamp (one per household)
    - Result: each generated row enriched with weather context cols
    """
    # Pick weather-only columns from collected (avoid duplicating
    # actual_irradiance_wm2 which also exists in generated)
    weather_cols = ["ts", "lat", "lon", "source", "tz",
                    "temperature_C", "cloud_cover_percent", "wind_speed_m_s"]
    weather_df = collected_df[weather_cols].drop_duplicates(subset=["ts"])

    # Merge
    merged = generated_df.merge(weather_df, on="ts", how="left")

    # Reorder columns to final header
    final_cols = []
    for col in FINAL_HEADER:
        if col in merged.columns:
            final_cols.append(col)
    # Add any extra columns not in the canonical list
    for col in merged.columns:
        if col not in final_cols:
            final_cols.append(col)

    merged = merged[final_cols]
    merged = merged.sort_values(["ts", "household_id"]).reset_index(drop=True)

    return merged


def build_metadata(merged_df: pd.DataFrame, collected_path: str,
                   generated_path: str) -> dict:
    ts_col = merged_df["ts"]
    return {
        "description": "Grid Guardian Final Merged Dataset (Collected + Generated)",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_rows": len(merged_df),
        "columns": list(merged_df.columns),
        "households": sorted(merged_df["household_id"].unique().tolist()),
        "date_range": {
            "start": str(ts_col.min()),
            "end": str(ts_col.max()),
        },
        "sources": {
            "collected": collected_path,
            "generated": generated_path,
        },
        "merge_stats": {
            "generated_rows": len(merged_df),
            "weather_coverage_pct": round(
                merged_df["temperature_C"].notna().mean() * 100, 2),
            "pv_non_null_pct": round(
                pd.to_numeric(merged_df["pv_gen_kw"], errors="coerce")
                .notna().mean() * 100, 2),
            "offers_with_hash": int(
                (merged_df["commit_hash"].fillna("") != "").sum()),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Grid Guardian — Create Final Merged Dataset")
    parser.add_argument("--collected", required=True,
                        help="Path to collected_data.csv.gz (Part A)")
    parser.add_argument("--generated", required=True,
                        help="Path to generated_dataset.csv.gz (Part B)")
    parser.add_argument("--out", default="./data/final",
                        help="Output directory for final_dataset.csv.gz")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Grid Guardian — Final Dataset Merger")
    print("=" * 60)

    # Load sources
    print(f"\nLoading collected:  {args.collected}")
    cdf = load_collected(args.collected)
    print(f"  → {len(cdf)} rows, {cdf['ts'].min()} → {cdf['ts'].max()}")

    print(f"Loading generated:  {args.generated}")
    gdf = load_generated(args.generated)
    print(f"  → {len(gdf)} rows, {gdf['ts'].min()} → {gdf['ts'].max()}")

    # Merge
    print("\nMerging datasets...")
    merged = merge_datasets(cdf, gdf)
    print(f"  → Final dataset: {len(merged)} rows, {len(merged.columns)} columns")

    # Write final dataset
    final_path = out_dir / "final_dataset.csv.gz"
    merged.to_csv(final_path, index=False, compression="gzip")
    print(f"\nWrote: {final_path}")

    # Also write a Parquet version for fast loading
    parquet_path = out_dir / "final_dataset.parquet"
    merged.to_parquet(parquet_path, index=False)
    print(f"Wrote: {parquet_path}")

    # Write metadata
    meta = build_metadata(merged, args.collected, args.generated)
    meta_path = out_dir / "final_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    print(f"Wrote: {meta_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("FINAL DATASET SUMMARY")
    print("=" * 60)
    print(f"  Total rows       : {meta['total_rows']:,}")
    print(f"  Households       : {meta['households']}")
    print(f"  Date range       : {meta['date_range']['start']} → {meta['date_range']['end']}")
    print(f"  Weather coverage : {meta['merge_stats']['weather_coverage_pct']}%")
    print(f"  PV non-null      : {meta['merge_stats']['pv_non_null_pct']}%")
    print(f"  Offers w/ hash   : {meta['merge_stats']['offers_with_hash']:,}")
    print(f"  Columns          : {len(meta['columns'])}")
    print("=" * 60)

    # Quick validation
    print("\n--- Quick Validation ---")
    issues = 0

    # Mass balance check
    mn = merged.copy()
    mn["pv_gen_kw"] = pd.to_numeric(mn["pv_gen_kw"], errors="coerce")
    mn["load_kw"] = pd.to_numeric(mn["load_kw"], errors="coerce")
    mn["net_kw"] = pd.to_numeric(mn["net_kw"], errors="coerce")
    valid = mn.dropna(subset=["pv_gen_kw", "load_kw", "net_kw"])
    balance_err = (abs(valid["net_kw"] - (valid["pv_gen_kw"] - valid["load_kw"])) > 1e-4).sum()
    if balance_err > 0:
        print(f"  [WARN] Mass balance violations: {balance_err}")
        issues += 1
    else:
        print("  [PASS] Mass balance: net_kw = pv_gen_kw - load_kw")

    # Weather join coverage
    if meta["merge_stats"]["weather_coverage_pct"] < 90:
        print(f"  [WARN] Low weather coverage: {meta['merge_stats']['weather_coverage_pct']}%")
        issues += 1
    else:
        print(f"  [PASS] Weather coverage: {meta['merge_stats']['weather_coverage_pct']}%")

    # Household coverage
    hh_expected = set(gdf["household_id"].unique())
    hh_final = set(merged["household_id"].unique())
    if hh_expected != hh_final:
        print(f"  [WARN] Household mismatch: expected {hh_expected}, got {hh_final}")
        issues += 1
    else:
        print(f"  [PASS] All {len(hh_final)} households present")

    if issues == 0:
        print("\n  All validations passed!")
    else:
        print(f"\n  {issues} issue(s) found")

    return 0


if __name__ == "__main__":
    sys.exit(main())
