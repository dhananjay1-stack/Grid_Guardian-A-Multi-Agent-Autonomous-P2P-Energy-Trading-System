#!/usr/bin/env python3
"""
data_generation.py — Grid Guardian Data-Generation Script (Part B)
===================================================================
Consumes collected_data.csv.gz (weather ± optional measured PV/load),
uses a generator config describing household archetypes, battery & panel
specs, and market rules, and synthesizes a full canonical dataset.

Outputs: generated_dataset.csv.gz, offers.csv.gz, trades.csv.gz,
         salt store, metadata and summary reports.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import gzip
import hashlib
import io
import json
import logging
import math
import os
import random
import secrets as secrets_mod
import subprocess
import stat
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

try:
    import pvlib
    HAS_PVLIB = True
except ImportError:
    HAS_PVLIB = False

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as crypto_hashes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATASET_HEADER = [
    "ts", "household_id", "pv_gen_kw", "load_kw", "net_kw",
    "soc_kwh", "soc_capacity_kwh", "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
    "offer_id", "offered_kwh", "offer_price",
    "trade_id", "commit_hash", "event_flag", "reward", "safety_violation",
]
OFFERS_HEADER = [
    "offer_id", "ts", "seller_id", "offered_kwh", "offer_price",
    "commit_hash", "expiry_ts", "matched_trade_id",
]
TRADES_HEADER = [
    "trade_id", "offer_id", "buyer_id", "seller_id",
    "open_ts", "accept_ts", "settle_ts", "amount_paid",
    "settled_bool", "settlement_tx_stub",
]
COLLECTED_HEADER = [
    "ts", "lat", "lon", "source", "actual_irradiance_wm2",
    "temperature_C", "cloud_cover_percent", "wind_speed_m_s",
    "pv_gen_kw", "load_kw", "voltage_v", "current_a", "tz",
]
FLOAT_PRECISION = 6
DEFAULT_CHUNK = 1440

# Logging — JSON structured
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("data_generation")


# ============================================================================
# 3 Default Load Templates (Indian household magnitudes, kW at 5-min res)
# ============================================================================
# Keys: hour (0-23). Values: (weekday_kw, weekend_kw)
LOAD_TEMPLATES = {
    "small_apartment_v1": {
        0: (0.15, 0.15), 1: (0.12, 0.12), 2: (0.10, 0.10),
        3: (0.10, 0.10), 4: (0.10, 0.10), 5: (0.15, 0.12),
        6: (0.40, 0.25), 7: (0.55, 0.30), 8: (0.45, 0.35),
        9: (0.30, 0.40), 10: (0.25, 0.35), 11: (0.25, 0.30),
        12: (0.35, 0.40), 13: (0.30, 0.35), 14: (0.25, 0.30),
        15: (0.25, 0.25), 16: (0.30, 0.30), 17: (0.40, 0.35),
        18: (0.60, 0.55), 19: (0.70, 0.65), 20: (0.65, 0.60),
        21: (0.50, 0.50), 22: (0.35, 0.35), 23: (0.20, 0.20),
    },
    "family_home_v1": {
        0: (0.25, 0.25), 1: (0.20, 0.20), 2: (0.18, 0.18),
        3: (0.18, 0.18), 4: (0.18, 0.18), 5: (0.25, 0.20),
        6: (0.70, 0.40), 7: (0.90, 0.55), 8: (0.75, 0.65),
        9: (0.50, 0.70), 10: (0.40, 0.65), 11: (0.45, 0.55),
        12: (0.55, 0.65), 13: (0.50, 0.60), 14: (0.45, 0.50),
        15: (0.45, 0.45), 16: (0.55, 0.50), 17: (0.70, 0.60),
        18: (1.00, 0.90), 19: (1.20, 1.10), 20: (1.10, 1.00),
        21: (0.85, 0.80), 22: (0.55, 0.55), 23: (0.35, 0.35),
    },
    "ev_owner_v1": {
        0: (0.30, 0.30), 1: (0.25, 0.25), 2: (0.22, 0.22),
        3: (0.22, 0.22), 4: (0.22, 0.22), 5: (0.30, 0.25),
        6: (0.80, 0.50), 7: (1.10, 0.65), 8: (0.90, 0.80),
        9: (0.60, 0.85), 10: (0.50, 0.75), 11: (0.55, 0.65),
        12: (0.65, 0.75), 13: (0.60, 0.70), 14: (0.55, 0.60),
        15: (0.55, 0.55), 16: (0.65, 0.60), 17: (0.85, 0.75),
        18: (1.40, 1.20), 19: (1.80, 1.60), 20: (1.60, 1.40),
        21: (1.20, 1.10), 22: (0.70, 0.70), 23: (0.40, 0.40),
    },
}


# ============================================================================
# Utility helpers
# ============================================================================

def _git_hash() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _set_file_mode_600(path: str) -> None:
    """Set file permissions to owner-read/write only (chmod 600)."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows doesn't support POSIX permissions fully


# ============================================================================
# Commitment hashing & salt management
# ============================================================================

def compute_commit_hash(ts_str: str, household_id: str,
                        offered_kwh: float, salt_hex: str,
                        algo: str = "sha256") -> str:
    commit_input = f"{ts_str}|{household_id}|{offered_kwh:.6f}|{salt_hex}"
    return hashlib.new(algo, commit_input.encode("utf-8")).hexdigest()


def generate_salt(length_bytes: int = 16,
                  rng: Optional[np.random.RandomState] = None) -> str:
    if rng is not None:
        return rng.bytes(length_bytes).hex()
    return secrets_mod.token_hex(length_bytes)


class SaltStore:
    """Manages salt storage — plain files or AES-GCM encrypted JSON."""

    def __init__(self, base_path: str, encrypt: bool = False,
                 passphrase: Optional[str] = None):
        self.base_path = Path(base_path)
        self.encrypt = encrypt and HAS_CRYPTO
        self.passphrase = passphrase
        self._encrypted_store: Dict[str, Dict[str, str]] = {}
        self._key: Optional[bytes] = None
        if self.encrypt and self.passphrase:
            self._derive_key()

    def _derive_key(self) -> None:
        kdf = PBKDF2HMAC(
            algorithm=crypto_hashes.SHA256(), length=32, iterations=100_000,
            salt=b"grid_guardian_salt_key_v1",
        )
        self._key = kdf.derive(self.passphrase.encode("utf-8"))

    def save_salt(self, household_id: str, offer_id: str, salt_hex: str) -> None:
        if self.encrypt:
            self._encrypted_store.setdefault(household_id, {})[offer_id] = salt_hex
        else:
            hh_dir = self.base_path / household_id
            hh_dir.mkdir(parents=True, exist_ok=True)
            salt_path = hh_dir / f"{offer_id}.salt"
            salt_path.write_text(salt_hex)
            _set_file_mode_600(str(salt_path))

    def flush_encrypted(self) -> None:
        if not self.encrypt or not self._encrypted_store:
            return
        self.base_path.mkdir(parents=True, exist_ok=True)
        enc_path = self.base_path / "encrypted_salts.bin"
        plaintext = json.dumps(self._encrypted_store).encode("utf-8")
        nonce = secrets_mod.token_bytes(12)
        aesgcm = AESGCM(self._key)
        ct = aesgcm.encrypt(nonce, plaintext, None)
        enc_path.write_bytes(nonce + ct)
        _set_file_mode_600(str(enc_path))
        logger.info(f"Encrypted salt store written to {enc_path}")

    def get_all_salts(self) -> Dict[str, Dict[str, str]]:
        return self._encrypted_store


# ============================================================================
# TOU price function
# ============================================================================

def _parse_tou_table(tou: dict) -> List[Tuple[int, int, float]]:
    """Parse TOU table from config into list of (start_minute, end_minute, price)."""
    entries = []
    for period, price in tou.items():
        parts = period.split("-")
        sh, sm = parts[0].strip().split(":")
        eh, em = parts[1].strip().split(":")
        entries.append((int(sh) * 60 + int(sm), int(eh) * 60 + int(em), float(price)))
    return sorted(entries)


def get_utility_price(ts: pd.Timestamp, tou_entries: List[Tuple[int, int, float]],
                      tz_str: str) -> float:
    """Return utility price for a given timestamp using TOU table."""
    local_ts = ts.tz_convert(tz_str)
    minutes = local_ts.hour * 60 + local_ts.minute
    for start_m, end_m, price in tou_entries:
        if start_m <= minutes < end_m:
            return price
    return tou_entries[-1][2] if tou_entries else 5.0


# ============================================================================
# PV generation using pvlib
# ============================================================================

def compute_pv_gen(irradiance_wm2: float, temperature_c: float,
                   ts: pd.Timestamp, lat: float, lon: float,
                   panel_kwp: float, tilt: float, azimuth: float,
                   efficiency: float, tz_str: str) -> float:
    """Compute PV generation in kW using pvlib or a simplified model."""
    if pd.isna(irradiance_wm2) or irradiance_wm2 <= 0:
        return 0.0

    if HAS_PVLIB:
        try:
            loc = pvlib.location.Location(lat, lon, tz=tz_str)
            solar_pos = loc.get_solarposition(ts)
            zenith = solar_pos["apparent_zenith"].iloc[0]
            azimuth_sun = solar_pos["azimuth"].iloc[0]

            if zenith >= 90:
                return 0.0

            # Get total irradiance on tilted surface
            total_irr = pvlib.irradiance.get_total_irradiance(
                surface_tilt=tilt, surface_azimuth=azimuth,
                solar_zenith=zenith, solar_azimuth=azimuth_sun,
                dni=irradiance_wm2 * 0.7,  # approximate DNI from GHI
                ghi=irradiance_wm2,
                dhi=irradiance_wm2 * 0.3,  # approximate DHI
            )
            poa = total_irr["poa_global"]
            if pd.isna(poa) or poa <= 0:
                return 0.0
            pv_kw = (poa / 1000.0) * panel_kwp * efficiency
            return max(0.0, float(pv_kw))
        except Exception:
            pass

    # Fallback: simplified model
    pv_kw = (irradiance_wm2 / 1000.0) * panel_kwp * efficiency
    return max(0.0, float(pv_kw))


# ============================================================================
# Load synthesis
# ============================================================================

def synthesize_load(ts: pd.Timestamp, template_name: str,
                    tz_str: str, rng: np.random.RandomState,
                    noise_std: float = 0.05) -> float:
    """Synthesize load in kW from template + noise."""
    template = LOAD_TEMPLATES.get(template_name, LOAD_TEMPLATES["family_home_v1"])
    local_ts = ts.tz_convert(tz_str)
    hour = local_ts.hour
    is_weekend = local_ts.dayofweek >= 5
    weekday_kw, weekend_kw = template.get(hour, (0.3, 0.3))
    base = weekend_kw if is_weekend else weekday_kw
    noise = rng.normal(0, noise_std * base)
    return max(0.01, base + noise)


# ============================================================================
# Forecast helpers (simple persistence-based)
# ============================================================================

def build_forecasts(weather_df: pd.DataFrame, ts: pd.Timestamp,
                    timestep_min: int) -> dict:
    """Build simple forecast values by looking ahead in weather data."""
    result = {
        "forecast_irradiance_1h": np.nan,
        "forecast_irradiance_3h": np.nan,
        "forecast_temp_1h": np.nan,
    }
    ts_1h = ts + pd.Timedelta(hours=1)
    ts_3h = ts + pd.Timedelta(hours=3)

    if ts_1h in weather_df.index:
        result["forecast_irradiance_1h"] = weather_df.loc[ts_1h].get(
            "actual_irradiance_wm2", np.nan)
        result["forecast_temp_1h"] = weather_df.loc[ts_1h].get(
            "temperature_C", np.nan)
    if ts_3h in weather_df.index:
        result["forecast_irradiance_3h"] = weather_df.loc[ts_3h].get(
            "actual_irradiance_wm2", np.nan)

    return result


# ============================================================================
# CLI
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grid Guardian — Data Generation Script (Part B)")
    p.add_argument("--collected", required=True,
                   help="Path to collected_data.csv.gz")
    p.add_argument("--config", required=True,
                   help="Path to generator_config.yaml")
    p.add_argument("--out", default="./data/generated",
                   help="Output directory")
    p.add_argument("--timestep", type=int, default=None,
                   help="Timestep minutes (should match collected)")
    p.add_argument("--start", type=str, default=None,
                   help="Override start date YYYY-MM-DD")
    p.add_argument("--end", type=str, default=None,
                   help="Override end date YYYY-MM-DD")
    p.add_argument("--seed", type=int, default=42, help="RNG seed")
    p.add_argument("--compute-reward", action="store_true",
                   help="Compute per-timestep reward")
    p.add_argument("--format", type=str, default="csv",
                   choices=["csv", "parquet"])
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK,
                   help="Timesteps per processing chunk")
    p.add_argument("--encrypt-salts", action="store_true",
                   help="Encrypt salts with passphrase (AES-GCM)")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate only, no output")
    return p


def parse_args(argv=None):
    return build_parser().parse_args(argv)


# ============================================================================
# Main simulation engine
# ============================================================================

class MarketEngine:
    """Simple P2P market engine for offer matching and settlement."""

    def __init__(self, cfg: dict, tou_entries: list, tz_str: str,
                 rng: np.random.RandomState):
        self.cfg = cfg
        self.tou = tou_entries
        self.tz = tz_str
        self.rng = rng
        self.open_offers: Dict[str, dict] = {}
        self.all_offers: List[dict] = []
        self.all_trades: List[dict] = []
        self._trade_counter = 0
        self.match_window = pd.Timedelta(
            minutes=cfg.get("match_window_min", 15))
        self.delivery_window = pd.Timedelta(
            minutes=cfg.get("delivery_window_min", 15))
        self.acceptance_rule = cfg.get("acceptance_rule", "price_below_utility")

    def create_offer(self, ts: pd.Timestamp, seller_id: str,
                     offered_kwh: float, offer_price: float,
                     commit_hash: str) -> dict:
        offer_id = f"offer_{seller_id}_{int(ts.timestamp())}"
        expiry_ts = ts + self.match_window
        offer = {
            "offer_id": offer_id,
            "ts": _iso(ts),
            "seller_id": seller_id,
            "offered_kwh": round(offered_kwh, FLOAT_PRECISION),
            "offer_price": round(offer_price, FLOAT_PRECISION),
            "commit_hash": commit_hash,
            "expiry_ts": _iso(expiry_ts),
            "matched_trade_id": "",
            "_ts_obj": ts,
            "_expiry": expiry_ts,
        }
        self.open_offers[offer_id] = offer
        self.all_offers.append(offer)
        return offer

    def try_match(self, ts: pd.Timestamp, buyer_id: str,
                  buyer_demand_kwh: float) -> Optional[dict]:
        """Try to match buyer with an open offer."""
        if buyer_demand_kwh <= 0:
            return None

        utility_price = get_utility_price(ts, self.tou, self.tz)

        # Expire old offers
        expired = [oid for oid, o in self.open_offers.items()
                   if ts > o["_expiry"]]
        for oid in expired:
            del self.open_offers[oid]

        # Find matching offer
        for oid, offer in list(self.open_offers.items()):
            if offer["seller_id"] == buyer_id:
                continue  # can't buy from self
            accept = False
            if self.acceptance_rule == "price_below_utility":
                accept = offer["offer_price"] <= utility_price
            elif self.acceptance_rule == "probabilistic":
                gap = utility_price - offer["offer_price"]
                prob = min(1.0, max(0.0, 0.5 + gap / utility_price))
                accept = self.rng.random() < prob
            else:
                accept = offer["offer_price"] <= utility_price

            if accept:
                trade = self._execute_trade(ts, offer, buyer_id)
                return trade
        return None

    def _execute_trade(self, ts: pd.Timestamp, offer: dict,
                       buyer_id: str) -> dict:
        self._trade_counter += 1
        trade_id = f"trade_{self._trade_counter:06d}"
        settle_ts = ts + self.delivery_window
        delivered = offer["offered_kwh"] * (
            1.0 + self.rng.normal(0, 0.01))
        tolerance = 0.1
        settled = abs(delivered - offer["offered_kwh"]) <= tolerance
        amount_paid = round(
            delivered * offer["offer_price"] if settled
            else 0.0, FLOAT_PRECISION)
        tx_stub = self.rng.bytes(16).hex() if settled else ""

        trade = {
            "trade_id": trade_id,
            "offer_id": offer["offer_id"],
            "buyer_id": buyer_id,
            "seller_id": offer["seller_id"],
            "open_ts": offer["ts"],
            "accept_ts": _iso(ts),
            "settle_ts": _iso(settle_ts) if settled else "",
            "amount_paid": amount_paid,
            "settled_bool": settled,
            "settlement_tx_stub": tx_stub,
        }
        self.all_trades.append(trade)

        # Update offer
        offer["matched_trade_id"] = trade_id
        if offer["offer_id"] in self.open_offers:
            del self.open_offers[offer["offer_id"]]

        return trade

    def get_offers_df(self) -> pd.DataFrame:
        rows = [{k: v for k, v in o.items()
                 if not k.startswith("_")} for o in self.all_offers]
        df = pd.DataFrame(rows, columns=OFFERS_HEADER)
        return df

    def get_trades_df(self) -> pd.DataFrame:
        df = pd.DataFrame(self.all_trades, columns=TRADES_HEADER)
        return df


class HouseholdState:
    """Maintains per-household battery state and per-timestep data."""

    def __init__(self, hh_cfg: dict):
        self.id = hh_cfg["household_id"]
        self.panel_kwp = hh_cfg.get("panel_kwp", 1.0)
        self.tilt = hh_cfg.get("panel_tilt", 18)
        self.azimuth = hh_cfg.get("panel_azimuth", 180)
        self.efficiency = hh_cfg.get("system_efficiency", 0.78)
        self.soc_capacity = hh_cfg.get("soc_capacity_kwh", 6.0)
        self.soc = hh_cfg.get("initial_soc_kwh", 3.0)
        self.soc_min = hh_cfg.get("soc_min", 0.6)
        self.soc_max = hh_cfg.get("soc_max", 5.9)
        self.batt_power = hh_cfg.get("battery_nominal_power_kw", 1.5)
        self.eff_charge = hh_cfg.get("efficiency_charge", 0.95)
        self.eff_discharge = hh_cfg.get("efficiency_discharge", 0.95)
        self.load_template = hh_cfg.get("load_template", "family_home_v1")


def run_simulation(collected_df: pd.DataFrame, cfg: dict, args,
                   salt_store: SaltStore) -> Tuple[
                       pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Run the full per-timestep simulation loop."""

    sim_cfg = cfg.get("simulation", {})
    market_cfg = cfg.get("market", {})
    event_cfg = cfg.get("events", {})
    reward_cfg = cfg.get("reward", {})
    noise_cfg = cfg.get("noise", {})
    zk_cfg = cfg.get("zk", {})

    seed = args.seed if args.seed is not None else sim_cfg.get("seed", 42)
    rng = np.random.RandomState(seed)
    random.seed(seed)

    timestep_min = args.timestep or sim_cfg.get("timestep_min", 5)
    timestep_hours = timestep_min / 60.0

    # Parse date range
    start_str = args.start or sim_cfg.get("start")
    end_str = args.end or sim_cfg.get("end")

    # Determine timezone from collected data
    tz_str = "Asia/Kolkata"
    if "tz" in collected_df.columns:
        tz_vals = collected_df["tz"].dropna().unique()
        if len(tz_vals) > 0:
            tz_str = str(tz_vals[0])

    # Determine lat/lon
    lat = collected_df["lat"].dropna().iloc[0] if "lat" in collected_df.columns else 18.5204
    lon = collected_df["lon"].dropna().iloc[0] if "lon" in collected_df.columns else 73.8567

    # Build timestamp index from collected data
    collected_df["ts"] = pd.to_datetime(collected_df["ts"], utc=True)
    weather_df = collected_df.set_index("ts").sort_index()

    if start_str:
        start_ts = pd.Timestamp(start_str, tz="UTC")
        weather_df = weather_df[weather_df.index >= start_ts]
    if end_str:
        end_ts = pd.Timestamp(end_str, tz="UTC") + pd.Timedelta(days=1)
        weather_df = weather_df[weather_df.index < end_ts]

    timestamps = weather_df.index.unique().sort_values()
    logger.info(f"Simulation: {len(timestamps)} timesteps, "
                f"{timestamps.min()} → {timestamps.max()}")

    # Check measured mode
    has_measured = ("household_id" in collected_df.columns and
                    collected_df["pv_gen_kw"].notna().any() and
                    collected_df["load_kw"].notna().any())
    if has_measured:
        logger.info("Measured-mode detected — using per-household PV/load")
    else:
        logger.info("Weather-only mode — synthesizing PV/load per household")

    # Initialize households
    households_cfg = cfg.get("households", [])
    if not households_cfg:
        raise ValueError("No households defined in config")
    hh_states = {h["household_id"]: HouseholdState(h) for h in households_cfg}

    # Parse market
    tou_table = market_cfg.get("utility_price_function", {}).get("tou_table", {})
    tou_entries = _parse_tou_table(tou_table) if tou_table else [
        (0, 360, 3.0), (360, 1080, 5.0), (1080, 1320, 8.0), (1320, 1440, 4.0)]
    market = MarketEngine(market_cfg, tou_entries, tz_str, rng)

    min_offer = market_cfg.get("min_offer_kwh", 0.1)
    max_offer = market_cfg.get("max_offer_kwh", 3.0)
    price_floor = market_cfg.get("price_floor", 2.0)
    price_ceiling = market_cfg.get("price_ceiling", 20.0)

    # Event probabilities
    cloud_prob = event_cfg.get("cloud_ramp_prob_per_day", 0.2)
    ev_prob = event_cfg.get("ev_arrival_prob_per_day", 0.1)
    outage_prob = event_cfg.get("outage_prob_per_day", 0.01)
    steps_per_day = int(1440 / timestep_min)
    cloud_prob_step = 1 - (1 - cloud_prob) ** (1 / steps_per_day)
    ev_prob_step = 1 - (1 - ev_prob) ** (1 / steps_per_day)
    outage_prob_step = 1 - (1 - outage_prob) ** (1 / steps_per_day)

    # Noise
    pv_noise_std = noise_cfg.get("pv_std_fraction", 0.02)
    load_noise_std = noise_cfg.get("load_std_fraction", 0.05)
    missing_rate = noise_cfg.get("missing_rate", 0.001)

    # ZK config
    hash_algo = zk_cfg.get("commit_hash_algo", "sha256")
    salt_len = zk_cfg.get("salt_length_bytes", 16)

    # Reward coefficients
    cost_savings_coeff = reward_cfg.get("cost_savings_coeff", 1.0)
    grid_stability_coeff = reward_cfg.get("grid_stability_coeff", 0.1)
    battery_deg_coeff = reward_cfg.get("battery_deg_coeff", 0.01)

    # Output accumulators
    dataset_rows: List[dict] = []
    safety_violation_count = 0
    total_reward = 0.0
    mass_balance_corrections = 0

    # Active events tracking (per-day)
    active_events: Dict[str, dict] = {}  # household_id -> event info

    logger.info("Starting per-timestep simulation loop...")

    for ts_idx, ts in enumerate(timestamps):
        w_row = weather_df.loc[ts]
        if isinstance(w_row, pd.DataFrame):
            w_row = w_row.iloc[0]

        irradiance = w_row.get("actual_irradiance_wm2", 0.0)
        if pd.isna(irradiance):
            irradiance = 0.0
        temperature = w_row.get("temperature_C", 25.0)
        if pd.isna(temperature):
            temperature = 25.0

        utility_price = get_utility_price(ts, tou_entries, tz_str)
        forecasts = build_forecasts(weather_df, ts, timestep_min)

        # Neighborhood net balance for grid stability
        neighborhood_net = 0.0

        for hh_id, hh in hh_states.items():
            # --- 1. Input selection / measured check ---
            pv_gen = np.nan
            load_kw = np.nan
            voltage_v = np.nan
            current_a = np.nan

            if has_measured:
                mask = (collected_df["ts"] == ts)
                if "household_id" in collected_df.columns:
                    mask = mask & (collected_df["household_id"] == hh_id)
                meas = collected_df.loc[mask]
                if len(meas) > 0:
                    mr = meas.iloc[0]
                    if pd.notna(mr.get("pv_gen_kw")):
                        pv_gen = float(mr["pv_gen_kw"])
                    if pd.notna(mr.get("load_kw")):
                        load_kw = float(mr["load_kw"])
                    if pd.notna(mr.get("voltage_v")):
                        voltage_v = float(mr["voltage_v"])
                    if pd.notna(mr.get("current_a")):
                        current_a = float(mr["current_a"])

            # --- 2. PV synthesis if not measured ---
            if pd.isna(pv_gen):
                pv_gen = compute_pv_gen(
                    irradiance, temperature, ts, lat, lon,
                    hh.panel_kwp, hh.tilt, hh.azimuth,
                    hh.efficiency, tz_str)
                # Add noise
                pv_gen += rng.normal(0, pv_noise_std * max(pv_gen, 0.001))
                pv_gen = max(0.0, pv_gen)

            # --- 3. Load synthesis if not measured ---
            if pd.isna(load_kw):
                load_kw = synthesize_load(ts, hh.load_template, tz_str,
                                          rng, load_noise_std)

            # --- Event injection ---
            event_flag = "normal"

            # Cloud ramp
            if rng.random() < cloud_prob_step:
                severity = rng.uniform(0.3, 0.8)
                pv_gen *= (1.0 - severity)
                event_flag = "cloud_ramp"

            # EV arrival
            if rng.random() < ev_prob_step:
                ev_spike = rng.uniform(1.0, 3.5)
                load_kw += ev_spike
                event_flag = "ev_arrival"

            # Grid outage
            if rng.random() < outage_prob_step:
                event_flag = "grid_outage"

            # Inject missing readings
            if rng.random() < missing_rate:
                pv_gen = np.nan
            if rng.random() < missing_rate:
                load_kw = np.nan

            # --- 4. net_kw ---
            if pd.notna(pv_gen) and pd.notna(load_kw):
                net_kw = pv_gen - load_kw
            else:
                net_kw = np.nan

            # --- 5. Battery control ---
            battery_power = 0.0
            safety_violation = False
            soc_before = hh.soc

            if pd.notna(net_kw):
                if net_kw > 0.05 and utility_price <= 6.0:
                    # Surplus + cheap: charge battery
                    charge = min(net_kw, hh.batt_power)
                    new_soc = hh.soc + charge * timestep_hours * hh.eff_charge
                    if new_soc > hh.soc_max:
                        charge = max(0, (hh.soc_max - hh.soc) /
                                     (timestep_hours * hh.eff_charge))
                        new_soc = hh.soc + charge * timestep_hours * hh.eff_charge
                        if new_soc > hh.soc_max:
                            new_soc = hh.soc_max
                            safety_violation = True
                    battery_power = charge
                    hh.soc = new_soc
                elif net_kw < -0.05 or utility_price >= 8.0:
                    # Deficit or expensive: discharge
                    discharge = min(abs(net_kw), hh.batt_power)
                    new_soc = hh.soc - discharge * timestep_hours / hh.eff_discharge
                    if new_soc < hh.soc_min:
                        discharge = max(0, (hh.soc - hh.soc_min) *
                                        hh.eff_discharge / timestep_hours)
                        new_soc = hh.soc - discharge * timestep_hours / hh.eff_discharge
                        if new_soc < hh.soc_min:
                            new_soc = hh.soc_min
                            safety_violation = True
                    battery_power = -discharge
                    hh.soc = new_soc

            # Final SoC clip
            if hh.soc > hh.soc_max:
                hh.soc = hh.soc_max
                safety_violation = True
            if hh.soc < hh.soc_min:
                hh.soc = hh.soc_min
                safety_violation = True

            if safety_violation:
                safety_violation_count += 1

            # --- 6. Offer creation ---
            offer_id = ""
            offered_kwh_val = np.nan
            offer_price_val = np.nan
            commit_hash = ""
            trade_id = ""

            min_sell_threshold = 0.05
            if (pd.notna(net_kw) and net_kw > min_sell_threshold
                    and hh.soc > hh.soc_min + 0.5
                    and event_flag != "grid_outage"):
                available = min(
                    net_kw * timestep_hours,
                    max_offer,
                    hh.soc - hh.soc_min,
                )
                if available >= min_offer:
                    offered_kwh_val = round(available, FLOAT_PRECISION)
                    markup = rng.uniform(0.8, 1.1)
                    offer_price_val = round(
                        min(max(utility_price * markup, price_floor),
                            price_ceiling),
                        FLOAT_PRECISION)

                    salt_hex = generate_salt(salt_len, rng=rng)
                    ts_str = _iso(ts)
                    commit_hash = compute_commit_hash(
                        ts_str, hh_id, offered_kwh_val, salt_hex, hash_algo)

                    offer = market.create_offer(
                        ts, hh_id, offered_kwh_val,
                        offer_price_val, commit_hash)
                    offer_id = offer["offer_id"]

                    # Save salt
                    salt_store.save_salt(hh_id, offer_id, salt_hex)

            # --- 7. Try to buy (if deficit) ---
            if (pd.notna(net_kw) and net_kw < -min_sell_threshold
                    and event_flag != "grid_outage"):
                demand = abs(net_kw) * timestep_hours
                trade = market.try_match(ts, hh_id, demand)
                if trade:
                    trade_id = trade["trade_id"]

            neighborhood_net += (net_kw if pd.notna(net_kw) else 0.0)

            # --- 8. Reward ---
            reward = np.nan
            if args.compute_reward and pd.notna(net_kw):
                cost_savings = 0.0
                if offered_kwh_val > 0 and pd.notna(offered_kwh_val):
                    cost_savings += offered_kwh_val * (
                        (offer_price_val if pd.notna(offer_price_val) else 0)
                        - utility_price)
                if net_kw < 0:
                    cost_savings += abs(net_kw) * timestep_hours * utility_price * 0.1

                grid_bonus = -abs(neighborhood_net) * grid_stability_coeff
                energy_cycled = abs(battery_power) * timestep_hours
                batt_cost = energy_cycled * battery_deg_coeff

                reward = round(
                    cost_savings * cost_savings_coeff + grid_bonus - batt_cost,
                    FLOAT_PRECISION)
                total_reward += reward

            # --- Mass balance check ---
            if pd.notna(pv_gen) and pd.notna(load_kw):
                expected_net = pv_gen - load_kw
                if abs((net_kw if pd.notna(net_kw) else 0) - expected_net) > 1e-6:
                    net_kw = expected_net
                    mass_balance_corrections += 1

            # --- Build row ---
            row = {
                "ts": _iso(ts),
                "household_id": hh_id,
                "pv_gen_kw": round(pv_gen, FLOAT_PRECISION) if pd.notna(pv_gen) else "",
                "load_kw": round(load_kw, FLOAT_PRECISION) if pd.notna(load_kw) else "",
                "net_kw": round(net_kw, FLOAT_PRECISION) if pd.notna(net_kw) else "",
                "soc_kwh": round(hh.soc, FLOAT_PRECISION),
                "soc_capacity_kwh": hh.soc_capacity,
                "battery_power_kw": round(battery_power, FLOAT_PRECISION),
                "price_signal": round(utility_price, FLOAT_PRECISION),
                "forecast_irradiance_1h": (
                    round(forecasts["forecast_irradiance_1h"], FLOAT_PRECISION)
                    if pd.notna(forecasts["forecast_irradiance_1h"]) else ""),
                "forecast_irradiance_3h": (
                    round(forecasts["forecast_irradiance_3h"], FLOAT_PRECISION)
                    if pd.notna(forecasts["forecast_irradiance_3h"]) else ""),
                "forecast_temp_1h": (
                    round(forecasts["forecast_temp_1h"], FLOAT_PRECISION)
                    if pd.notna(forecasts["forecast_temp_1h"]) else ""),
                "actual_irradiance_wm2": round(irradiance, FLOAT_PRECISION),
                "voltage_v": round(voltage_v, FLOAT_PRECISION) if pd.notna(voltage_v) else "",
                "current_a": round(current_a, FLOAT_PRECISION) if pd.notna(current_a) else "",
                "offer_id": offer_id,
                "offered_kwh": offered_kwh_val if pd.notna(offered_kwh_val) else "",
                "offer_price": offer_price_val if pd.notna(offer_price_val) else "",
                "trade_id": trade_id,
                "commit_hash": commit_hash,
                "event_flag": event_flag,
                "reward": reward if pd.notna(reward) else "",
                "safety_violation": safety_violation,
            }
            dataset_rows.append(row)

        # Progress logging
        if (ts_idx + 1) % 1000 == 0:
            logger.info(f"  Processed {ts_idx + 1}/{len(timestamps)} timesteps")

    logger.info(f"Simulation complete: {len(dataset_rows)} rows generated")

    # Build DataFrames
    dataset_df = pd.DataFrame(dataset_rows, columns=DATASET_HEADER)
    offers_df = market.get_offers_df()
    trades_df = market.get_trades_df()

    # Summary
    summary = {
        "total_rows": len(dataset_df),
        "total_offers": len(offers_df),
        "total_trades": len(trades_df),
        "acceptance_rate": (
            round(len(trades_df) / max(1, len(offers_df)), 4)),
        "avg_reward": (
            round(total_reward / max(1, len(dataset_df)), 6)
            if args.compute_reward else None),
        "safety_violation_count": safety_violation_count,
        "mass_balance_corrections": mass_balance_corrections,
        "households": list(hh_states.keys()),
        "timesteps": len(timestamps),
        "date_range": {
            "start": str(timestamps.min()),
            "end": str(timestamps.max()),
        },
    }

    return dataset_df, offers_df, trades_df, summary


# ============================================================================
# Validation
# ============================================================================

class ValidationError(Exception):
    pass


def validate_outputs(dataset_df: pd.DataFrame, offers_df: pd.DataFrame,
                     trades_df: pd.DataFrame, summary: dict,
                     cfg: dict) -> List[str]:
    """Run all quality checks. Returns warnings; raises on critical."""
    errors = []
    warnings = []

    # 1. Header checks
    if list(dataset_df.columns) != DATASET_HEADER:
        errors.append(f"Dataset header mismatch: {list(dataset_df.columns)}")
    if list(offers_df.columns) != OFFERS_HEADER:
        errors.append(f"Offers header mismatch: {list(offers_df.columns)}")
    if list(trades_df.columns) != TRADES_HEADER:
        errors.append(f"Trades header mismatch: {list(trades_df.columns)}")

    # 2. Mass balance
    for idx, row in dataset_df.iterrows():
        pv = row.get("pv_gen_kw")
        ld = row.get("load_kw")
        net = row.get("net_kw")
        try:
            pv_f = float(pv) if pv != "" and pd.notna(pv) else None
            ld_f = float(ld) if ld != "" and pd.notna(ld) else None
            net_f = float(net) if net != "" and pd.notna(net) else None
        except (ValueError, TypeError):
            continue
        if pv_f is not None and ld_f is not None and net_f is not None:
            if abs(net_f - (pv_f - ld_f)) > 1e-6:
                warnings.append(
                    f"Mass balance violation at row {idx}: "
                    f"net={net_f}, pv-load={pv_f - ld_f}")
        if idx > 5000:
            break  # Sample check for large datasets

    # 3. SoC bounds
    households_cfg = cfg.get("households", [])
    hh_bounds = {h["household_id"]: (h.get("soc_min", 0), h.get("soc_max", 999))
                 for h in households_cfg}
    soc_violations = 0
    for idx, row in dataset_df.iterrows():
        soc = row.get("soc_kwh")
        hh_id = row.get("household_id")
        try:
            soc_f = float(soc) if soc != "" and pd.notna(soc) else None
        except (ValueError, TypeError):
            continue
        if soc_f is not None and hh_id in hh_bounds:
            lo, hi = hh_bounds[hh_id]
            if soc_f < lo - 0.001 or soc_f > hi + 0.001:
                soc_violations += 1
        if idx > 5000:
            break
    if soc_violations > 0:
        warnings.append(f"SoC bound violations: {soc_violations}")

    # 4. Offer/trade integrity
    offer_ids = set(offers_df["offer_id"])
    for _, trade in trades_df.iterrows():
        if trade["offer_id"] not in offer_ids:
            errors.append(f"Trade {trade['trade_id']} references "
                          f"unknown offer {trade['offer_id']}")
    # Every offer must have commit_hash
    missing_hash = offers_df[
        offers_df["commit_hash"].isna() | (offers_df["commit_hash"] == "")]
    if len(missing_hash) > 0:
        errors.append(f"{len(missing_hash)} offers missing commit_hash")

    if errors:
        raise ValidationError("\n".join(errors))

    return warnings


# ============================================================================
# Salt leakage scanner
# ============================================================================

def scan_for_salt_leakage(salt_store: SaltStore, out_dir: Path) -> List[str]:
    """Scan CSV and log files for any leaked salt hex strings."""
    leaks = []
    all_salts = set()

    # Collect all salts
    if salt_store.encrypt:
        for hh_salts in salt_store.get_all_salts().values():
            all_salts.update(hh_salts.values())
    else:
        secrets_dir = salt_store.base_path
        if secrets_dir.exists():
            for salt_file in secrets_dir.rglob("*.salt"):
                all_salts.add(salt_file.read_text().strip())

    if not all_salts:
        return leaks

    # Scan output files
    for fpath in out_dir.rglob("*"):
        if fpath.is_file() and fpath.suffix in (".csv", ".gz", ".json", ".log"):
            try:
                if fpath.suffix == ".gz":
                    with gzip.open(fpath, "rt") as f:
                        content = f.read(100_000)
                else:
                    content = fpath.read_text(errors="ignore")[:100_000]
                for salt in all_salts:
                    if salt in content:
                        leaks.append(f"Salt leaked in {fpath}")
                        break
            except Exception:
                pass
    return leaks


# ============================================================================
# Output writing
# ============================================================================

def write_outputs(dataset_df: pd.DataFrame, offers_df: pd.DataFrame,
                  trades_df: pd.DataFrame, summary: dict,
                  cfg: dict, args, salt_store: SaltStore) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)

    # 1. generated_dataset.csv.gz
    ds_path = out_dir / "generated_dataset.csv.gz"
    dataset_df.to_csv(ds_path, index=False, compression="gzip")
    logger.info(f"Wrote {ds_path} ({len(dataset_df)} rows)")

    # 2. offers.csv.gz
    of_path = out_dir / "offers.csv.gz"
    offers_df.to_csv(of_path, index=False, compression="gzip")
    logger.info(f"Wrote {of_path} ({len(offers_df)} rows)")

    # 3. trades.csv.gz
    tr_path = out_dir / "trades.csv.gz"
    trades_df.to_csv(tr_path, index=False, compression="gzip")
    logger.info(f"Wrote {tr_path} ({len(trades_df)} rows)")

    # 4. Parquet (optional)
    if args.format == "parquet":
        dataset_df.to_parquet(out_dir / "generated_dataset.parquet", index=False)
        offers_df.to_parquet(out_dir / "offers.parquet", index=False)
        trades_df.to_parquet(out_dir / "trades.parquet", index=False)
        logger.info("Wrote Parquet versions")

    # 5. dataset_version.json
    version = {
        "config_hash": _config_hash(cfg),
        "seed": args.seed,
        "script_version": "1.0.0",
        "git_commit": _git_hash(),
        "commit_hash_format": (
            'sha256(f"{ts}|{household_id}|{offered_kwh:.6f}|{salt_hex}")'),
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    ver_path = out_dir / "dataset_version.json"
    with open(ver_path, "w") as f:
        json.dump(version, f, indent=2, default=str)
    logger.info(f"Wrote {ver_path}")

    # 6. generation_summary.json
    sum_path = out_dir / "generation_summary.json"
    with open(sum_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"Wrote {sum_path}")

    # 7. Flush encrypted salts
    salt_store.flush_encrypted()

    # 8. Salt leakage scan
    leaks = scan_for_salt_leakage(salt_store, out_dir)
    if leaks:
        for l in leaks:
            logger.error(f"SECURITY: {l}")
        raise ValidationError("Salt leakage detected in output files!")
    logger.info("Salt leakage scan: CLEAN")

    # 9. Execution log
    exec_log = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "args": vars(args),
        "rows": len(dataset_df),
        "offers": len(offers_df),
        "trades": len(trades_df),
    }
    log_path = out_dir / "logs" / "execution_log.json"
    with open(log_path, "w") as f:
        json.dump(exec_log, f, indent=2, default=str)


# ============================================================================
# Main
# ============================================================================

def main(argv=None) -> int:
    args = parse_args(argv)

    cfg = _load_yaml(args.config)
    logger.info(f"Loaded config from {args.config}")

    # Seed
    seed = args.seed
    np.random.seed(seed)
    random.seed(seed)

    # Load collected data
    logger.info(f"Loading collected data from {args.collected}")
    collected_df = pd.read_csv(
        args.collected,
        compression="gzip" if args.collected.endswith(".gz") else None)

    # Validate collected header
    expected_cols = set(COLLECTED_HEADER)
    actual_cols = set(collected_df.columns)
    missing = expected_cols - actual_cols
    if missing:
        logger.error(f"Collected data missing columns: {missing}")
        return 1

    # Salt store setup
    zk_cfg = cfg.get("zk", {})
    salt_path = zk_cfg.get("salt_store_path", str(Path(args.out) / "secrets"))
    passphrase = None
    if args.encrypt_salts:
        if HAS_CRYPTO:
            passphrase = getpass.getpass("Enter passphrase for salt encryption: ")
        else:
            logger.warning("cryptography not installed — salts will be stored unencrypted")
    salt_store = SaltStore(salt_path, encrypt=args.encrypt_salts,
                           passphrase=passphrase)

    # Run simulation
    try:
        dataset_df, offers_df, trades_df, summary = run_simulation(
            collected_df, cfg, args, salt_store)
    except Exception as exc:
        logger.error(f"Simulation failed: {exc}")
        traceback.print_exc()
        return 1

    # Validate
    logger.info("Running validation checks...")
    try:
        warnings = validate_outputs(dataset_df, offers_df, trades_df,
                                    summary, cfg)
        for w in warnings:
            logger.warning(w)
    except ValidationError as ve:
        logger.error(f"Validation failed: {ve}")
        return 1

    # Dry-run report
    if args.dry_run:
        logger.info("=== DRY RUN — Summary ===")
        for k, v in summary.items():
            logger.info(f"  {k}: {v}")
        logger.info("=== DRY RUN COMPLETE (no files written) ===")
        return 0

    # Write outputs
    write_outputs(dataset_df, offers_df, trades_df, summary, cfg,
                  args, salt_store)
    logger.info("Data generation complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
