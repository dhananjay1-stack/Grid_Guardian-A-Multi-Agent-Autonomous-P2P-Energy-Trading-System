#!/usr/bin/env python3
"""
dataset_fixer.py — Grid Guardian Dataset Auto-Fix & Synthetic Imputation Tool
Repairs, fills, and retunes the Grid-Guardian dataset artifacts produced by Part B.

Usage:
    python dataset_fixer.py --generated ./data/generated/generated_dataset.csv.gz \
        --offers ./data/generated/offers.csv.gz \
        --trades ./data/generated/trades.csv.gz \
        --collected ./data/collected/collected_data.csv.gz \
        --config ./generator_config.yaml \
        --out ./data/fixed \
        --seed 42 --target_acceptance 0.05 \
        --fix-netkw --fix-voltage-current \
        --autotune-archetypes --reprice-offers \
        --apply
"""

import argparse
import copy
import hashlib
import json
import logging
import os
import random
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# ─────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("dataset_fixer")


# ─────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Grid-Guardian Dataset Fixer")
    p.add_argument("--generated",    required=True,  help="Path to generated_dataset.csv.gz")
    p.add_argument("--offers",       required=True,  help="Path to offers.csv.gz")
    p.add_argument("--trades",       required=True,  help="Path to trades.csv.gz")
    p.add_argument("--collected",    default=None,   help="Path to collected_data.csv.gz (optional)")
    p.add_argument("--secrets-dir",  default=None,   help="Path to secrets/ folder")
    p.add_argument("--config",       default=None,   help="Path to generator_config.yaml")
    p.add_argument("--out",          default="./data/fixed", help="Output folder")
    p.add_argument("--seed",         type=int, default=42)
    p.add_argument("--target_acceptance", type=float, default=0.05,
                   help="Target trade acceptance rate (default 0.05 = 5%%)")
    p.add_argument("--fix-netkw",           action="store_true")
    p.add_argument("--fix-voltage-current", action="store_true")
    p.add_argument("--autotune-archetypes", action="store_true")
    p.add_argument("--reprice-offers",      action="store_true")
    p.add_argument("--apply",               action="store_true",
                   help="Write fixed files; without this flag runs as dry-run")
    p.add_argument("--backup-dir",   default=None,
                   help="Where originals are copied before write (timestamped subfolder)")
    p.add_argument("--round-digits", type=int, default=9)
    p.add_argument("--min-offers-threshold", type=int, default=10,
                   help="Households below this #offers get autotuned")
    p.add_argument("--price-step",   type=float, default=0.05,
                   help="Fraction to reduce offer_price per iteration during re-pricing")
    p.add_argument("--plot",         action="store_true", help="Save diagnostic PNGs")
    return p.parse_args()


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────
def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def secure_salt(n_bytes: int = 16) -> bytes:
    return os.urandom(n_bytes)


def write_salt(salt_bytes: bytes, secrets_dir: Path, household_id: str, offer_id: str):
    folder = secrets_dir / household_id
    folder.mkdir(parents=True, exist_ok=True)
    fpath = folder / f"{offer_id}.salt"
    fpath.write_bytes(salt_bytes)
    try:
        os.chmod(fpath, 0o600)
    except Exception:
        pass  # Windows may not honour POSIX perms
    return fpath


def read_salt(secrets_dir: Path, household_id: str, offer_id: str) -> bytes | None:
    fpath = secrets_dir / household_id / f"{offer_id}.salt"
    if fpath.exists():
        return fpath.read_bytes()
    return None


def compute_commit_hash(ts: str, household_id: str, offered_kwh: float, salt_hex: str) -> str:
    commit_input = f"{ts}|{household_id}|{offered_kwh:.6f}|{salt_hex}"
    return sha256_hex(commit_input)


def tou_price(ts_str: str, tou_table: dict) -> float:
    """Return utility TOU price for a given UTC ts string."""
    try:
        dt = pd.to_datetime(ts_str, utc=True).tz_convert("Asia/Kolkata")
    except Exception:
        dt = pd.to_datetime(ts_str)
    hm = dt.hour * 60 + dt.minute
    for slot, price in tou_table.items():
        s, e = slot.split("-")
        sh, sm = (int(x) for x in s.split(":"))
        eh, em = (int(x) for x in e.split(":"))
        start_m = sh * 60 + sm
        end_m = eh * 60 + em
        if start_m <= hm < end_m:
            return float(price)
    return 5.0  # fallback


def load_config(config_path: str | None) -> dict:
    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    # Minimal safe defaults matching generator_config.yaml
    return {
        "simulation": {"timestep_min": 5, "seed": 42},
        "households": [],
        "market": {
            "min_offer_kwh": 0.1,
            "max_offer_kwh": 3.0,
            "price_floor": 2.0,
            "price_ceiling": 20.0,
            "utility_price_function": {
                "tou_table": {
                    "00:00-06:00": 3.0,
                    "06:00-10:00": 5.0,
                    "10:00-14:00": 6.0,
                    "14:00-18:00": 5.5,
                    "18:00-22:00": 8.0,
                    "22:00-24:00": 4.0,
                }
            },
        },
        "zk": {"salt_length_bytes": 16, "salt_store_path": "./data/generated/secrets"},
    }


# ─────────────────────────────────────────────────────────
# Fix A — Net kW mass-balance correction
# ─────────────────────────────────────────────────────────
def fix_netkw(gen: pd.DataFrame, round_digits: int = 9) -> tuple[pd.DataFrame, int]:
    mask = gen["pv_gen_kw"].notna() & gen["load_kw"].notna()
    corrected = (gen.loc[mask, "pv_gen_kw"] - gen.loc[mask, "load_kw"]).round(round_digits)
    orig_net = gen.loc[mask, "net_kw"].round(round_digits)
    # both are same-indexed slices now
    delta_mask_sub = ~((orig_net == corrected) | (orig_net.isna() & corrected.isna()))
    n_changed = int(delta_mask_sub.sum())
    gen.loc[mask, "net_kw"] = corrected
    log.info("Fix A — net_kw corrected on %d rows (round_digits=%d)", n_changed, round_digits)
    return gen, n_changed


# ─────────────────────────────────────────────────────────
# Fix B — Synthetic voltage & current fill
# ─────────────────────────────────────────────────────────
def fix_voltage_current(
    gen: pd.DataFrame, config: dict, force: bool = False
) -> tuple[pd.DataFrame, int, int]:
    # Determine nominal voltage per household from config (default 230 V)
    hh_voltage: dict[str, float] = {}
    for hh in config.get("households", []):
        hh_voltage[hh["household_id"]] = hh.get("nominal_voltage_v", 230.0)

    v_null = gen["voltage_v"].isna()
    a_null = gen["current_a"].isna()

    if "voltage_v_synth_flag" not in gen.columns:
        gen["voltage_v_synth_flag"] = False
    if "current_a_synth_flag" not in gen.columns:
        gen["current_a_synth_flag"] = False

    v_synth_count = 0
    a_synth_count = 0

    for hh, grp in gen.groupby("household_id"):
        nom_v = hh_voltage.get(str(hh), 230.0)
        hh_mask = gen["household_id"] == hh

        # Fill voltage
        v_fill_mask = hh_mask & v_null
        n_v = int(v_fill_mask.sum())
        if n_v > 0:
            gen.loc[v_fill_mask, "voltage_v"] = nom_v
            gen.loc[v_fill_mask, "voltage_v_synth_flag"] = True
            v_synth_count += n_v

        # Fill current from |net_kw| * 1000 / voltage_v
        a_fill_mask = hh_mask & a_null & gen["net_kw"].notna()
        # Use the (now filled) voltage_v
        vol = gen.loc[a_fill_mask, "voltage_v"].fillna(nom_v)
        gen.loc[a_fill_mask, "current_a"] = (
            gen.loc[a_fill_mask, "net_kw"].abs() * 1000.0 / vol
        ).round(6)
        gen.loc[a_fill_mask, "current_a_synth_flag"] = True
        n_a = int(a_fill_mask.sum())
        a_synth_count += n_a

    log.info("Fix B — voltage_v synthetic fills: %d, current_a synthetic fills: %d",
             v_synth_count, a_synth_count)
    return gen, v_synth_count, a_synth_count


# ─────────────────────────────────────────────────────────
# Fix C — Archetype autotune  (generate offers for small_apartment_01)
# ─────────────────────────────────────────────────────────
def _generate_offers_for_household(
    gen: pd.DataFrame,
    household_id: str,
    hh_cfg: dict,
    config: dict,
    secrets_dir: Path,
    rng: np.random.Generator,
    apply: bool,
) -> tuple[pd.DataFrame, list[dict]]:
    """Re-run local offer-generation pass with relaxed policy params."""
    mkt = config.get("market", {})
    tou_table = mkt.get("utility_price_function", {}).get("tou_table", {})
    price_floor = float(mkt.get("price_floor", 2.0))
    price_ceiling = float(mkt.get("price_ceiling", 20.0))
    min_offer_kwh = float(hh_cfg.get("min_offer_kwh_relaxed", mkt.get("min_offer_kwh", 0.05)))
    max_offer_kwh = float(mkt.get("max_offer_kwh", 3.0))
    timestep_hr = float(config.get("simulation", {}).get("timestep_min", 5)) / 60.0
    salt_len = int(config.get("zk", {}).get("salt_length_bytes", 16))

    soc_min = float(hh_cfg.get("soc_min", 0.4))
    soc_margin = float(hh_cfg.get("soc_margin_for_offer", 0.1))  # relaxed margin

    hh_mask = gen["household_id"] == household_id
    hh_rows = gen[hh_mask].copy()

    new_offers = []
    # Offer when net_kw > 0 (surplus) and soc above soc_min + margin
    surplus_mask = (
        (hh_rows["net_kw"] > 0)
        & hh_rows["soc_kwh"].notna()
        & (hh_rows["soc_kwh"] > soc_min + soc_margin)
    )

    for idx, row in hh_rows[surplus_mask].iterrows():
        ts = str(row["ts"])
        net = float(row["net_kw"])
        soc = float(row["soc_kwh"])
        available_kwh = min(net * timestep_hr, max_offer_kwh, soc - soc_min)
        if available_kwh < min_offer_kwh:
            continue
        offered_kwh = round(available_kwh, 6)
        util_price = tou_price(ts, tou_table)
        # price slightly below utility to be competitive
        markup = float(hh_cfg.get("offer_price_markup", 0.85))
        offer_price = round(min(max(util_price * markup, price_floor), price_ceiling), 4)
        offer_id = f"offer_{household_id}_{int(pd.to_datetime(ts, utc=True).timestamp())}"
        expiry_ts = str(
            pd.to_datetime(ts, utc=True)
            + pd.Timedelta(minutes=int(mkt.get("match_window_min", 15)))
        )

        # Salt & commit_hash
        if apply:
            salt_bytes = secure_salt(salt_len)
            write_salt(salt_bytes, secrets_dir, household_id, offer_id)
        else:
            salt_bytes = bytes(rng.integers(0, 256, salt_len, dtype=np.uint8))
        salt_hex = salt_bytes.hex()
        commit = compute_commit_hash(ts, household_id, offered_kwh, salt_hex)

        new_offers.append({
            "offer_id": offer_id,
            "ts": ts,
            "seller_id": household_id,
            "offered_kwh": offered_kwh,
            "offer_price": offer_price,
            "commit_hash": commit,
            "expiry_ts": expiry_ts,
            "matched_trade_id": None,
            "synthetic_offer": True,
        })

        # Back-fill generated_dataset row
        gen.loc[idx, "offer_id"] = offer_id
        gen.loc[idx, "offered_kwh"] = offered_kwh
        gen.loc[idx, "offer_price"] = offer_price
        gen.loc[idx, "commit_hash"] = commit

    log.info("  Archetype autotune: generated %d offers for %s", len(new_offers), household_id)
    return gen, new_offers


def autotune_archetypes(
    gen: pd.DataFrame,
    offers: pd.DataFrame,
    config: dict,
    secrets_dir: Path,
    min_offers_threshold: int,
    rng: np.random.Generator,
    apply: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict, list[dict]]:
    """
    For each household below min_offers_threshold, relax policy and re-generate offers.
    Returns updated gen, offers DataFrames, patched config, and change log.
    """
    offer_counts = offers.groupby("seller_id").size().to_dict()
    hh_list = config.get("households", [])
    patched_config = copy.deepcopy(config)
    changes = []
    all_new_offers = []

    for hh_cfg in hh_list:
        hid = hh_cfg["household_id"]
        count = offer_counts.get(hid, 0)
        if count >= min_offers_threshold:
            log.info("Autotune: %s has %d offers — OK, no change needed", hid, count)
            continue

        log.info("Autotune: %s has only %d offers — applying relaxed params", hid, count)
        relaxed = copy.deepcopy(hh_cfg)
        # Relax offer policy thresholds
        relaxed["min_offer_kwh_relaxed"] = max(0.01, relaxed.get("soc_min", 0.4) * 0.05)
        relaxed["soc_margin_for_offer"] = 0.05  # very small margin above soc_min
        relaxed["offer_price_markup"] = 0.88    # 12% below utility — competitive

        changes.append({
            "household_id": hid,
            "original_offers": count,
            "changes": {
                "min_offer_kwh_relaxed": relaxed["min_offer_kwh_relaxed"],
                "soc_margin_for_offer": relaxed["soc_margin_for_offer"],
                "offer_price_markup": relaxed["offer_price_markup"],
            },
        })

        gen, new_offers = _generate_offers_for_household(
            gen, hid, relaxed, config, secrets_dir, rng, apply
        )
        all_new_offers.extend(new_offers)

        for ph in patched_config["households"]:
            if ph["household_id"] == hid:
                ph["min_offer_kwh_relaxed"] = relaxed["min_offer_kwh_relaxed"]
                ph["soc_margin_for_offer"] = relaxed["soc_margin_for_offer"]
                ph["offer_price_markup"] = relaxed["offer_price_markup"]

    if all_new_offers:
        new_df = pd.DataFrame(all_new_offers)
        if "synthetic_offer" not in offers.columns:
            offers["synthetic_offer"] = False
        offers = pd.concat([offers, new_df], ignore_index=True)

    return gen, offers, patched_config, changes


# ─────────────────────────────────────────────────────────
# Fix D — Offer re-pricing & matching simulation
# ─────────────────────────────────────────────────────────
def reprice_and_match(
    gen: pd.DataFrame,
    offers: pd.DataFrame,
    trades: pd.DataFrame,
    config: dict,
    target_acceptance: float,
    price_step: float,
    rng: np.random.Generator,
    apply: bool,
    secrets_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    mkt = config.get("market", {})
    tou_table = mkt.get("utility_price_function", {}).get("tou_table", {})
    price_floor = float(mkt.get("price_floor", 2.0))
    timestep_hr = float(config.get("simulation", {}).get("timestep_min", 5)) / 60.0
    salt_len = int(config.get("zk", {}).get("salt_length_bytes", 16))

    total_offers = len(offers)
    existing_matched = set(offers["matched_trade_id"].dropna().tolist())
    n_existing_accepted = int(offers["matched_trade_id"].notna().sum())

    unmatched_mask = offers["matched_trade_id"].isna()
    unmatched = offers[unmatched_mask].copy()

    new_trades = []
    updated_offer_ids = []
    prices_updated = 0

    current_acceptance = n_existing_accepted / total_offers if total_offers > 0 else 0
    log.info("Re-pricing: current acceptance=%.4f, target=%.4f, unmatched=%d",
             current_acceptance, target_acceptance, len(unmatched))

    for idx, row in unmatched.iterrows():
        if current_acceptance >= target_acceptance:
            break
        ts = str(row["ts"])
        household_id = str(row["seller_id"])
        offer_id = str(row["offer_id"])
        orig_price = float(row["offer_price"])
        offered_kwh = float(row["offered_kwh"])
        util_price = tou_price(ts, tou_table)

        # Iteratively reduce price until offer_price <= utility_price or floor reached
        new_price = orig_price
        accepted = False
        iterations = 0
        max_iterations = 50
        while iterations < max_iterations:
            if new_price <= util_price:
                accepted = True
                break
            new_price = max(new_price * (1 - price_step), price_floor)
            if new_price <= price_floor:
                # Accept at floor regardless (floor is always competitive if price_floor <util)
                accepted = new_price <= util_price
                break
            iterations += 1

        if not accepted:
            continue

        offers.loc[idx, "offer_price"] = round(new_price, 4)
        prices_updated += 1

        # Re-compute commit_hash using existing salt or new salt
        salt_bytes = None
        if secrets_dir:
            salt_bytes = read_salt(secrets_dir, household_id, offer_id)
        if salt_bytes is None:
            salt_bytes = secure_salt(salt_len) if apply else bytes(rng.integers(0, 256, salt_len, dtype=np.uint8))
            if apply and secrets_dir:
                write_salt(salt_bytes, secrets_dir, household_id, offer_id)
        salt_hex = salt_bytes.hex()
        new_commit = compute_commit_hash(ts, household_id, offered_kwh, salt_hex)
        offers.loc[idx, "commit_hash"] = new_commit
        updated_offer_ids.append(offer_id)

        # Synthetic trade
        trade_id = f"synthetic_{uuid.uuid4().hex[:12]}"
        settle_ts = str(
            pd.to_datetime(ts, utc=True)
            + pd.Timedelta(minutes=int(mkt.get("delivery_window_min", 15)))
        )
        delivered = round(offered_kwh * (1 + rng.normal(0, 0.005)), 6)
        amount_paid = round(delivered * new_price, 4)
        stub = uuid.uuid4().hex

        offers.loc[idx, "matched_trade_id"] = trade_id
        new_trades.append({
            "trade_id": trade_id,
            "offer_id": offer_id,
            "buyer_id": "grid",
            "seller_id": household_id,
            "open_ts": ts,
            "accept_ts": ts,
            "settle_ts": settle_ts,
            "amount_paid": amount_paid,
            "settled_bool": True,
            "settlement_tx_stub": stub,
            "synthetic_trade": True,
        })

        # Update gen dataset row
        hh_mask = gen["household_id"] == household_id
        ts_mask = gen["ts"] == ts
        match_mask = hh_mask & ts_mask
        if match_mask.any():
            gen.loc[match_mask, "offer_price"] = round(new_price, 4)
            gen.loc[match_mask, "trade_id"] = trade_id
            gen.loc[match_mask, "commit_hash"] = new_commit

        n_existing_accepted += 1
        current_acceptance = n_existing_accepted / total_offers

    if new_trades:
        new_trades_df = pd.DataFrame(new_trades)
        if "synthetic_trade" not in trades.columns:
            trades["synthetic_trade"] = False
        trades = pd.concat([trades, new_trades_df], ignore_index=True)

    stats = {
        "acceptance_before": round(n_existing_accepted / total_offers - len(new_trades) / total_offers, 4),
        "acceptance_after": round(current_acceptance, 4),
        "offers_repriced": prices_updated,
        "synthetic_trades_created": len(new_trades),
        "target": target_acceptance,
    }
    log.info("Re-pricing done: acceptance %.4f → %.4f, %d synthetic trades",
             stats["acceptance_before"], stats["acceptance_after"], stats["synthetic_trades_created"])
    return gen, offers, trades, stats


# ─────────────────────────────────────────────────────────
# Fix E — Fill missing salts
# ─────────────────────────────────────────────────────────
def fix_missing_salts(
    offers: pd.DataFrame,
    secrets_dir: Path,
    config: dict,
    rng: np.random.Generator,
    apply: bool,
) -> tuple[pd.DataFrame, int]:
    salt_len = int(config.get("zk", {}).get("salt_length_bytes", 16))
    generated_count = 0
    for idx, row in offers.iterrows():
        offer_id = str(row["offer_id"])
        household_id = str(row["seller_id"])
        salt_bytes = read_salt(secrets_dir, household_id, offer_id)
        if salt_bytes is None:
            salt_bytes = secure_salt(salt_len) if apply else bytes(rng.integers(0, 256, salt_len, dtype=np.uint8))
            if apply:
                write_salt(salt_bytes, secrets_dir, household_id, offer_id)
            salt_hex = salt_bytes.hex()
            ts = str(row["ts"])
            offered_kwh = float(row["offered_kwh"])
            new_commit = compute_commit_hash(ts, household_id, offered_kwh, salt_hex)
            offers.loc[idx, "commit_hash"] = new_commit
            generated_count += 1
    log.info("Fix E — missing salts generated/patched: %d", generated_count)
    return offers, generated_count


# ─────────────────────────────────────────────────────────
# Fix F — Type consistency
# ─────────────────────────────────────────────────────────
def fix_types(gen: pd.DataFrame, offers: pd.DataFrame, trades: pd.DataFrame):
    # trade_id → nullable string
    for df in [gen, trades]:
        if "trade_id" in df.columns:
            df["trade_id"] = df["trade_id"].where(df["trade_id"].notna(), other=None)
            df["trade_id"] = df["trade_id"].astype(object)
    # offer_id → string
    for df in [gen, offers]:
        if "offer_id" in df.columns:
            df["offer_id"] = df["offer_id"].where(df["offer_id"].notna(), other=None)
            df["offer_id"] = df["offer_id"].astype(object)
    # safety_violation → bool (not mixed int/NaN)
    if "safety_violation" in gen.columns:
        gen["safety_violation"] = gen["safety_violation"].fillna(False).astype(bool)
    if "settled_bool" in trades.columns:
        trades["settled_bool"] = trades["settled_bool"].fillna(False).astype(bool)
    log.info("Fix F — type normalization applied")
    return gen, offers, trades


# ─────────────────────────────────────────────────────────
# Fix G — README & generation_summary patch
# ─────────────────────────────────────────────────────────
def fix_readme_and_summary(
    out_dir: Path,
    collected_path: str | None,
    generation_summary_path: str,
    apply: bool,
) -> dict:
    # Patch generation_summary
    gs = {}
    if Path(generation_summary_path).exists():
        with open(generation_summary_path) as f:
            gs = json.load(f)
    if "collected_data_source" not in gs and collected_path:
        gs["collected_data_source"] = str(collected_path)
        gs["fixer_patch_utc"] = datetime.now(timezone.utc).isoformat()
    return gs


def write_readme(out_dir: Path):
    readme = """# Grid Guardian — Dataset README (Auto-Generated by dataset_fixer.py)

## Files

| File | Description |
|------|-------------|
| `generated_dataset_fixed.csv.gz` | Full 23-column canonical dataset for RL training |
| `offers_fixed.csv.gz` | All energy offers (original + synthetic autotune/reprice) |
| `trades_fixed.csv.gz` | All settled trades (original + synthetic) |
| `secrets/<household>/<offer_id>.salt` | Per-offer salts (POSIX 600, never in CSVs) |
| `fix_report.json` | Structured before/after diff |
| `fix_report.md` | Human-readable remediation report |
| `sanity_check.json` | QA pass/fail summary |

## Canonical Header (generated_dataset_fixed.csv.gz)

```
ts,household_id,pv_gen_kw,load_kw,net_kw,soc_kwh,soc_capacity_kwh,battery_power_kw,
price_signal,forecast_irradiance_1h,forecast_irradiance_3h,forecast_temp_1h,
actual_irradiance_wm2,voltage_v,current_a,offer_id,offered_kwh,offer_price,
trade_id,commit_hash,event_flag,reward,safety_violation,
voltage_v_synth_flag,current_a_synth_flag
```

## Commit Hash Scheme

```
commit_input = f"{ts}|{household_id}|{offered_kwh:.6f}|{salt_hex}"
commit_hash  = sha256(commit_input.encode('utf-8')).hexdigest()
```

Salt files are stored in `secrets/<household_id>/<offer_id>.salt` (binary, 16 bytes).

## Synthetic Data Flags

- `voltage_v_synth_flag=True` — voltage was derived from nominal (230 V), not measured.
- `current_a_synth_flag=True` — current was computed as `|net_kw| * 1000 / voltage_v`.
- `synthetic_offer=True` (offers file) — offer generated by autotune fixer.
- `synthetic_trade=True` (trades file) — trade generated by reprice matching simulation.

## Reverting to Originals

Originals are stored in `--backup-dir`. Copy them back to `data/generated/` to revert.

## Run command

```bash
python dataset_fixer.py \\
  --generated ./data/generated/generated_dataset.csv.gz \\
  --offers ./data/generated/offers.csv.gz \\
  --trades ./data/generated/trades.csv.gz \\
  --collected ./data/collected/collected_data.csv.gz \\
  --config ./generator_config.yaml \\
  --out ./data/fixed \\
  --seed 42 --target_acceptance 0.05 \\
  --fix-netkw --fix-voltage-current \\
  --autotune-archetypes --reprice-offers \\
  --apply
```
"""
    readme_path = out_dir / "README_added.md"
    readme_path.write_text(readme, encoding="utf-8")
    log.info("Fix G — README_added.md written")


# ─────────────────────────────────────────────────────────
# Sanity Check
# ─────────────────────────────────────────────────────────
def run_sanity_check(gen: pd.DataFrame, offers: pd.DataFrame, trades: pd.DataFrame) -> dict:
    checks = {}

    # Header
    spec = ["ts","household_id","pv_gen_kw","load_kw","net_kw","soc_kwh","soc_capacity_kwh",
            "battery_power_kw","price_signal","forecast_irradiance_1h","forecast_irradiance_3h",
            "forecast_temp_1h","actual_irradiance_wm2","voltage_v","current_a","offer_id",
            "offered_kwh","offer_price","trade_id","commit_hash","event_flag","reward","safety_violation"]
    actual_cols = [c for c in gen.columns if c in spec]
    checks["header_match"] = (actual_cols == spec)

    # Mass balance
    m = gen.dropna(subset=["pv_gen_kw","load_kw","net_kw"])
    max_err = float((m["net_kw"] - (m["pv_gen_kw"] - m["load_kw"])).abs().max()) if len(m) > 0 else 0.0
    checks["mass_balance_max_error"] = max_err
    checks["mass_balance_pass"] = max_err < 1e-6

    # SoC bounds
    checks["soc_violations"] = int((gen["safety_violation"] == True).sum())
    checks["soc_bounds_pass"] = checks["soc_violations"] == 0

    # Offer integrity
    offers_with_hash = int(offers["commit_hash"].notna().sum())
    checks["offers_with_commit_hash"] = offers_with_hash
    checks["offers_total"] = len(offers)
    checks["offer_integrity_pass"] = (offers_with_hash == len(offers))

    # Trade → offer
    trade_offer_ids = set(trades["offer_id"].dropna())
    offer_ids = set(offers["offer_id"].dropna())
    checks["orphaned_trades"] = len(trade_offer_ids - offer_ids)
    checks["trade_integrity_pass"] = checks["orphaned_trades"] == 0

    # Acceptance
    matched = int(offers["matched_trade_id"].notna().sum())
    checks["acceptance_rate"] = round(matched / max(len(offers), 1), 4)

    # Missing pv
    checks["pv_missing_pct"] = round(gen["pv_gen_kw"].isna().mean() * 100, 3)
    checks["voltage_synthetic_pct"] = round(
        gen.get("voltage_v_synth_flag", pd.Series(False)).mean() * 100, 2
        if "voltage_v_synth_flag" in gen.columns else 0.0
    )

    checks["all_pass"] = all([
        checks["header_match"],
        checks["mass_balance_pass"],
        checks["soc_bounds_pass"],
        checks["offer_integrity_pass"],
        checks["trade_integrity_pass"],
    ])
    return checks


# ─────────────────────────────────────────────────────────
# Backup
# ─────────────────────────────────────────────────────────
def backup_originals(paths: list[Path], backup_dir: Path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = backup_dir / ts
    bdir.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if Path(p).exists():
            shutil.copy2(p, bdir / Path(p).name)
    log.info("Backup written to %s", bdir)
    return bdir


# ─────────────────────────────────────────────────────────
# Consistency samples
# ─────────────────────────────────────────────────────────
def build_consistency_samples(original: pd.DataFrame, fixed: pd.DataFrame, max_rows: int = 1000) -> pd.DataFrame:
    """Return rows where any numeric column changed between original and fixed."""
    numeric_cols = original.select_dtypes(include="number").columns.tolist()
    changed_mask = pd.Series(False, index=original.index)
    for c in numeric_cols:
        if c in fixed.columns:
            orig_vals = original[c].fillna(np.nan)
            fix_vals  = fixed[c].fillna(np.nan)
            changed_mask = changed_mask | ~((orig_vals == fix_vals) | (orig_vals.isna() & fix_vals.isna()))
    sample = fixed[changed_mask].head(max_rows)
    return sample


# ─────────────────────────────────────────────────────────
# Report builders
# ─────────────────────────────────────────────────────────
def build_fix_report(
    args,
    netkw_changes: int,
    v_synth: int,
    a_synth: int,
    autotune_changes: list,
    reprice_stats: dict,
    salt_fixes: int,
    sanity: dict,
    orig_checksums: dict,
    fixed_checksums: dict,
) -> dict:
    return {
        "fixer_version": "1.0.0",
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "apply_mode": args.apply,
        "target_acceptance": args.target_acceptance,
        "fixes_applied": {
            "A_netkw_rows_corrected": netkw_changes,
            "B_voltage_synthetic_fills": v_synth,
            "B_current_synthetic_fills": a_synth,
            "C_autotune_archetypes": autotune_changes,
            "D_reprice_stats": reprice_stats,
            "E_missing_salts_generated": salt_fixes,
        },
        "sanity_check": sanity,
        "file_checksums": {
            "original": orig_checksums,
            "fixed": fixed_checksums,
        },
    }


def build_fix_report_md(report: dict) -> str:
    fixes = report["fixes_applied"]
    sanity = report["sanity_check"]
    rp = fixes["D_reprice_stats"]

    lines = [
        "# Grid Guardian — Dataset Fix Report",
        "",
        f"**Run UTC:** {report['run_utc']}  ",
        f"**Seed:** {report['seed']}  ",
        f"**Mode:** {'APPLY' if report['apply_mode'] else 'DRY-RUN'}  ",
        f"**Target acceptance:** {report['target_acceptance']}  ",
        "",
        "## Fixes Applied",
        "",
        "### A. Mass-balance (net_kw)",
        f"- Rows corrected: **{fixes['A_netkw_rows_corrected']}**",
        "",
        "### B. Synthetic Voltage / Current",
        f"- `voltage_v` synthetic fills: **{fixes['B_voltage_synthetic_fills']}**",
        f"- `current_a` synthetic fills: **{fixes['B_current_synthetic_fills']}**",
        "",
        "### C. Archetype Autotune",
    ]
    for ch in fixes["C_autotune_archetypes"]:
        lines.append(f"- `{ch['household_id']}`: was **{ch['original_offers']}** offers → params relaxed: {ch['changes']}")
    lines += [
        "",
        "### D. Offer Re-pricing & Matching",
        f"- Acceptance before: **{rp.get('acceptance_before', 'N/A')}**",
        f"- Acceptance after:  **{rp.get('acceptance_after', 'N/A')}**",
        f"- Offers re-priced: **{rp.get('offers_repriced', 0)}**",
        f"- Synthetic trades created: **{rp.get('synthetic_trades_created', 0)}**",
        "",
        "### E. Missing Salts",
        f"- Salts generated/patched: **{fixes['E_missing_salts_generated']}**",
        "",
        "## Sanity Check Results",
        "",
        f"| Check | Result |",
        f"|-------|--------|",
        f"| Header match (23 cols) | {'✅ PASS' if sanity['header_match'] else '❌ FAIL'} |",
        f"| Mass balance max error | {sanity['mass_balance_max_error']:.2e} ({'✅' if sanity['mass_balance_pass'] else '❌'}) |",
        f"| SoC safety violations | {sanity['soc_violations']} ({'✅' if sanity['soc_bounds_pass'] else '❌'}) |",
        f"| Offer commit_hash coverage | {sanity['offers_with_commit_hash']}/{sanity['offers_total']} ({'✅' if sanity['offer_integrity_pass'] else '❌'}) |",
        f"| Trade→offer integrity | orphaned={sanity['orphaned_trades']} ({'✅' if sanity['trade_integrity_pass'] else '❌'}) |",
        f"| Acceptance rate | {sanity['acceptance_rate']} |",
        f"| PV missing % | {sanity['pv_missing_pct']}% |",
        f"| **Overall** | {'✅ ALL PASS' if sanity['all_pass'] else '⚠️ ISSUES REMAIN'} |",
        "",
        "## Reverting to Originals",
        "Originals are stored in `--backup-dir`. Copy them back to restore.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    args = parse_args()
    np.random.seed(args.seed)
    random.seed(args.seed)
    rng = np.random.default_rng(args.seed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=== Grid Guardian Dataset Fixer v1.0.0 ===")
    log.info("Mode: %s | seed=%d | target_acceptance=%.3f",
             "APPLY" if args.apply else "DRY-RUN", args.seed, args.target_acceptance)

    # ── Load config ──────────────────────────────────────
    config = load_config(args.config)
    tou_table = config.get("market", {}).get("utility_price_function", {}).get("tou_table", {})

    # ── Resolve secrets dir ──────────────────────────────
    secrets_dir = Path(args.secrets_dir) if args.secrets_dir else \
        Path(config.get("zk", {}).get("salt_store_path", "./data/generated/secrets"))
    out_secrets_dir = out_dir / "secrets"
    if args.apply:
        out_secrets_dir.mkdir(parents=True, exist_ok=True)

    # ── Load data ────────────────────────────────────────
    log.info("Loading generated_dataset …")
    gen = pd.read_csv(args.generated, low_memory=False)
    log.info("Loading offers …")
    offers = pd.read_csv(args.offers, low_memory=False)
    log.info("Loading trades …")
    trades = pd.read_csv(args.trades, low_memory=False)

    # Store originals for checksum + consistency-samples
    gen_orig = gen.copy()

    # ── Checksums before ─────────────────────────────────
    orig_checksums = {
        "generated": file_sha256(Path(args.generated)),
        "offers":    file_sha256(Path(args.offers)),
        "trades":    file_sha256(Path(args.trades)),
    }

    # ── Backup originals ─────────────────────────────────
    if args.apply:
        backup_base = Path(args.backup_dir) if args.backup_dir else Path("./data/backup")
        bdir = backup_originals(
            [Path(args.generated), Path(args.offers), Path(args.trades)],
            backup_base,
        )

    # ─────────────────────────────────────────────────────
    # Fix A — net_kw
    # ─────────────────────────────────────────────────────
    netkw_changes = 0
    if args.fix_netkw:
        gen, netkw_changes = fix_netkw(gen, args.round_digits)

    # ─────────────────────────────────────────────────────
    # Fix B — voltage / current
    # ─────────────────────────────────────────────────────
    v_synth = a_synth = 0
    if args.fix_voltage_current:
        gen, v_synth, a_synth = fix_voltage_current(gen, config)

    # ─────────────────────────────────────────────────────
    # Fix C — autotune archetypes
    # ─────────────────────────────────────────────────────
    autotune_changes = []
    patched_config = config
    if args.autotune_archetypes:
        apply_secrets_dir = out_secrets_dir if args.apply else secrets_dir
        gen, offers, patched_config, autotune_changes = autotune_archetypes(
            gen, offers, config, apply_secrets_dir,
            args.min_offers_threshold, rng, args.apply
        )

    # ─────────────────────────────────────────────────────
    # Fix D — reprice & match
    # ─────────────────────────────────────────────────────
    reprice_stats = {}
    if args.reprice_offers:
        apply_secrets_dir = out_secrets_dir if args.apply else secrets_dir
        gen, offers, trades, reprice_stats = reprice_and_match(
            gen, offers, trades, config,
            args.target_acceptance, args.price_step, rng,
            args.apply, apply_secrets_dir,
        )

    # ─────────────────────────────────────────────────────
    # Fix E — missing salts for all offers
    # ─────────────────────────────────────────────────────
    apply_secrets_dir = out_secrets_dir if args.apply else secrets_dir
    offers, salt_fixes = fix_missing_salts(offers, apply_secrets_dir, config, rng, args.apply)

    # ─────────────────────────────────────────────────────
    # Fix F — type consistency
    # ─────────────────────────────────────────────────────
    gen, offers, trades = fix_types(gen, offers, trades)

    # ─────────────────────────────────────────────────────
    # Fix G — README + generation_summary
    # ─────────────────────────────────────────────────────
    gen_summary_path = str(Path(args.generated).parent / "generation_summary.json")
    gs_patched = fix_readme_and_summary(out_dir, args.collected, gen_summary_path, args.apply)

    # update counts in summary
    gs_patched["total_rows_fixed"] = len(gen)
    gs_patched["total_offers_fixed"] = len(offers)
    gs_patched["total_trades_fixed"] = len(trades)
    gs_patched["acceptance_rate_fixed"] = round(
        offers["matched_trade_id"].notna().sum() / max(len(offers), 1), 4
    )

    # ─────────────────────────────────────────────────────
    # Sanity check
    # ─────────────────────────────────────────────────────
    sanity = run_sanity_check(gen, offers, trades)
    log.info("Sanity check all_pass=%s | acceptance=%.4f",
             sanity["all_pass"], sanity["acceptance_rate"])

    # ─────────────────────────────────────────────────────
    # Consistency samples
    # ─────────────────────────────────────────────────────
    samples = build_consistency_samples(gen_orig, gen, max_rows=1000)

    # ─────────────────────────────────────────────────────
    # Build reports
    # ─────────────────────────────────────────────────────
    fixed_checksums = {}  # will fill after write

    fix_report_data = build_fix_report(
        args, netkw_changes, v_synth, a_synth,
        autotune_changes, reprice_stats, salt_fixes,
        sanity, orig_checksums, {},
    )
    fix_report_md = build_fix_report_md(fix_report_data)

    # ─────────────────────────────────────────────────────
    # WRITE OUTPUTS
    # ─────────────────────────────────────────────────────
    if args.apply:
        log.info("Writing fixed files to %s …", out_dir)

        # generated_dataset_fixed.csv.gz
        gen_out = out_dir / "generated_dataset_fixed.csv.gz"
        gen.to_csv(gen_out, index=False, compression="gzip", float_format="%.9g")
        log.info("  Written: %s (%d rows)", gen_out.name, len(gen))

        # offers_fixed.csv.gz
        offers_out = out_dir / "offers_fixed.csv.gz"
        offers.to_csv(offers_out, index=False, compression="gzip")
        log.info("  Written: %s (%d rows)", offers_out.name, len(offers))

        # trades_fixed.csv.gz
        trades_out = out_dir / "trades_fixed.csv.gz"
        trades.to_csv(trades_out, index=False, compression="gzip")
        log.info("  Written: %s (%d rows)", trades_out.name, len(trades))

        # Checksums
        fixed_checksums = {
            "generated": file_sha256(gen_out),
            "offers":    file_sha256(offers_out),
            "trades":    file_sha256(trades_out),
        }
        fix_report_data["file_checksums"]["fixed"] = fixed_checksums

        # generator_config_patched.yaml
        config_out = out_dir / "generator_config_patched.yaml"
        with open(config_out, "w") as f:
            yaml.dump(patched_config, f, default_flow_style=False)
        log.info("  Written: generator_config_patched.yaml")

        # generation_summary_updated.json
        gs_out = out_dir / "generation_summary_updated.json"
        with open(gs_out, "w") as f:
            json.dump(gs_patched, f, indent=2)
        log.info("  Written: generation_summary_updated.json")

        # README
        write_readme(out_dir)

        # consistency_samples.csv
        samples_out = out_dir / "consistency_samples.csv"
        samples.to_csv(samples_out, index=False)
        log.info("  Written: consistency_samples.csv (%d rows)", len(samples))

    else:
        log.info("DRY-RUN mode — no files written. Preview below:\n"
                 "  Generated rows: %d | Offers: %d | Trades: %d",
                 len(gen), len(offers), len(trades))

    # Always write reports (dry-run or apply)
    report_json_path = out_dir / "fix_report.json"
    with open(report_json_path, "w") as f:
        json.dump(fix_report_data, f, indent=2, default=str)
    log.info("  Written: fix_report.json")

    report_md_path = out_dir / "fix_report.md"
    report_md_path.write_text(fix_report_md, encoding="utf-8")
    log.info("  Written: fix_report.md")

    sanity_out = out_dir / "sanity_check.json"
    with open(sanity_out, "w") as f:
        json.dump(sanity, f, indent=2)
    log.info("  Written: sanity_check.json")

    # Summary
    log.info("=== DONE ===")
    log.info("  All pass: %s | Acceptance rate: %.4f | Offers total: %d | Trades total: %d",
             sanity["all_pass"], sanity["acceptance_rate"], len(offers), len(trades))

    return 0 if sanity["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
