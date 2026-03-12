#!/usr/bin/env python3
"""
finalize_dataset.py — Merge all collected, generated, and fixed data into
a single clean data/final/ folder ready for downstream RL training.
"""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    base = Path(__file__).resolve().parent
    collected_dir = base / "data" / "collected"
    fixed_dir = base / "data" / "fixed"
    final_dir = base / "data" / "final"

    # ── Clean & recreate final/ ──────────────────────────
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True)
    print(f"--- Assembling Final Dataset in: {final_dir} ---\n")

    # ── 1. Core data files (fixed → final) ───────────────
    core_files = [
        ("generated_dataset_fixed.csv.gz", "final_dataset.csv.gz"),
        ("offers_fixed.csv.gz",            "offers.csv.gz"),
        ("trades_fixed.csv.gz",            "trades.csv.gz"),
    ]
    for src_name, dst_name in core_files:
        src = fixed_dir / src_name
        dst = final_dir / dst_name
        if src.exists():
            shutil.copy2(src, dst)
            print(f"  [OK] {dst_name}")
        else:
            print(f"  [MISS] {src}")

    # ── 2. Collected weather data ────────────────────────
    for f in ["collected_data.csv.gz", "collected_metadata.json", "collected_sample_hour.csv"]:
        src = collected_dir / f
        if src.exists():
            shutil.copy2(src, final_dir / f)
            print(f"  [OK] {f}")

    # ── 3. Secrets tree ──────────────────────────────────
    src_secrets = fixed_dir / "secrets"
    dst_secrets = final_dir / "secrets"
    if src_secrets.exists():
        shutil.copytree(src_secrets, dst_secrets)
        salt_count = sum(1 for _ in dst_secrets.rglob("*.salt"))
        print(f"  [OK] secrets/ ({salt_count} salt files across "
              f"{sum(1 for d in dst_secrets.iterdir() if d.is_dir())} households)")
    else:
        print("  [MISS] secrets/")

    # ── 4. Configs & metadata ────────────────────────────
    meta_copies = [
        (fixed_dir / "generator_config_patched.yaml",    "generator_config.yaml"),
        (fixed_dir / "generation_summary_updated.json",  "generation_summary.json"),
        (fixed_dir / "README_added.md",                  "README.md"),
        (fixed_dir / "fix_report.json",                  "fix_report.json"),
        (fixed_dir / "fix_report.md",                    "fix_report.md"),
        (fixed_dir / "sanity_check.json",                "sanity_check.json"),
        (fixed_dir / "consistency_samples.csv",          "consistency_samples.csv"),
        (base / "data" / "generated" / "dataset_version.json", "dataset_version.json"),
    ]
    for src, dst_name in meta_copies:
        if src.exists():
            shutil.copy2(src, final_dir / dst_name)
            print(f"  [OK] {dst_name}")

    # ── 5. Logs ──────────────────────────────────────────
    logs_dst = final_dir / "logs"
    logs_dst.mkdir(exist_ok=True)
    for log_src in [collected_dir / "logs", base / "data" / "generated" / "logs"]:
        if log_src.exists():
            for lf in log_src.iterdir():
                tag = log_src.parent.name  # collected or generated
                shutil.copy2(lf, logs_dst / f"{tag}_{lf.name}")
    print(f"  [OK] logs/ ({len(list(logs_dst.iterdir()))} files)")

    # ── 6. Build final_metadata.json ─────────────────────
    # Read fixed dataset for stats
    ds_path = final_dir / "final_dataset.csv.gz"
    df = pd.read_csv(ds_path, low_memory=False)

    offers = pd.read_csv(final_dir / "offers.csv.gz", low_memory=False)
    trades = pd.read_csv(final_dir / "trades.csv.gz", low_memory=False)

    # Per-household stats
    hh_stats = {}
    for hid, g in df.groupby("household_id"):
        hh_stats[str(hid)] = {
            "rows": int(len(g)),
            "pv_gen_kw_mean": round(float(g["pv_gen_kw"].mean()), 6),
            "load_kw_mean": round(float(g["load_kw"].mean()), 6),
            "soc_kwh_min": round(float(g["soc_kwh"].min()), 3),
            "soc_kwh_max": round(float(g["soc_kwh"].max()), 3),
            "offer_count": int(g["offer_id"].notna().sum()),
            "trade_count": int(g["trade_id"].notna().sum()),
        }

    metadata = {
        "description": "Grid Guardian Unified Final Dataset — collected + generated + fixed",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "total_rows": int(len(df)),
        "columns": list(df.columns),
        "households": sorted(df["household_id"].unique().tolist()),
        "date_range": {
            "start": str(df["ts"].min()),
            "end": str(df["ts"].max()),
        },
        "per_household": hh_stats,
        "offers_total": int(len(offers)),
        "trades_total": int(len(trades)),
        "acceptance_rate": round(
            float(offers["matched_trade_id"].notna().sum()) / max(len(offers), 1), 4
        ),
        "synthetic_offers": int(offers["synthetic_offer"].fillna(False).sum())
            if "synthetic_offer" in offers.columns else 0,
        "synthetic_trades": int(trades["synthetic_trade"].fillna(False).sum())
            if "synthetic_trade" in trades.columns else 0,
        "mass_balance_max_error": round(float(
            (df.dropna(subset=["pv_gen_kw","load_kw","net_kw"])
             .eval("net_kw - (pv_gen_kw - load_kw)").abs().max())
        ), 18),
        "safety_violations": int((df["safety_violation"] == True).sum()),
        "voltage_synthetic_pct": round(
            float(df["voltage_v_synth_flag"].mean() * 100), 2
        ) if "voltage_v_synth_flag" in df.columns else None,
        "sources": {
            "collected": "collected_data.csv.gz",
            "generated_fixed": "final_dataset.csv.gz",
            "offers": "offers.csv.gz",
            "trades": "trades.csv.gz",
        },
        "file_checksums": {
            "final_dataset": file_sha256(ds_path),
            "offers": file_sha256(final_dir / "offers.csv.gz"),
            "trades": file_sha256(final_dir / "trades.csv.gz"),
        },
    }

    meta_path = final_dir / "final_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"  [OK] final_metadata.json")

    # ── Summary ──────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  FINAL DATASET READY")
    print(f"{'='*60}")
    print(f"  Location:       {final_dir}")
    print(f"  Rows:           {metadata['total_rows']:,}")
    print(f"  Households:     {', '.join(metadata['households'])}")
    print(f"  Date range:     {metadata['date_range']['start']} → {metadata['date_range']['end']}")
    print(f"  Offers:         {metadata['offers_total']:,}")
    print(f"  Trades:         {metadata['trades_total']:,}")
    print(f"  Acceptance:     {metadata['acceptance_rate']*100:.2f}%")
    print(f"  Safety viols:   {metadata['safety_violations']}")
    print(f"  Mass-bal err:   {metadata['mass_balance_max_error']:.2e}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
