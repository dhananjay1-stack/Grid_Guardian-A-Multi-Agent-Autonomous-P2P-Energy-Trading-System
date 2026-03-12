#!/usr/bin/env python3
"""
data_collection.py — Grid Guardian Data-Collection Script
=========================================================
Downloads/ingests weather and irradiance data for a location and timeframe,
optionally reads local meter sensors, normalizes & resamples everything to a
configurable timestep (default 5 min), validates & documents data quality,
and writes collected_data.csv.gz + collected_metadata.json ready to be
consumed by the Data-Generation script.

Author : Grid Guardian Team
Date   : 2026-02-27
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import math
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ALLOWED_TIMESTEPS = {1, 5, 15, 60}
CANONICAL_COLUMNS = [
    "ts", "lat", "lon", "source",
    "actual_irradiance_wm2", "temperature_C", "cloud_cover_percent",
    "wind_speed_m_s", "pv_gen_kw", "load_kw", "voltage_v", "current_a", "tz",
]
NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
FLOAT_PRECISION = 6
MAX_GAP_FILL_MINUTES = 30
MAX_API_RETRIES = 5
API_BACKOFF_BASE = 2.0  # seconds

# Logging setup — JSON structured lines
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("data_collection")


# ============================================================================
# Utility helpers
# ============================================================================

def git_commit_hash() -> Optional[str]:
    """Return the current git commit hash if inside a repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return None


def _iso_utc(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def _merge_config(args: argparse.Namespace, config: dict) -> argparse.Namespace:
    """Merge YAML config values into args (CLI takes precedence)."""
    for key, val in config.items():
        cli_key = key.replace("-", "_")
        if getattr(args, cli_key, None) is None:
            setattr(args, cli_key, val)
    return args


# ============================================================================
# CLI
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Grid Guardian — Data Collection Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--lat", type=float, help="Latitude")
    p.add_argument("--lon", type=float, help="Longitude")
    p.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    p.add_argument("--end", type=str, help="End date YYYY-MM-DD")
    p.add_argument("--timestep", type=int, default=None,
                    help="Resample timestep in minutes (1,5,15,60); default 5")
    p.add_argument("--source", type=str, default="nasa_power",
                    choices=["nasa_power", "era5"],
                    help="Primary weather data source")
    p.add_argument("--include-local-sensors", action="store_true",
                    help="Read PZEM/INA local sensors if present")
    p.add_argument("--sensors-config", type=str, default=None,
                    help="Path to sensors.yaml for local sensor config")
    p.add_argument("--out", type=str, default="./data/collected",
                    help="Output directory")
    p.add_argument("--format", type=str, default="csv",
                    choices=["csv", "parquet"],
                    help="Output format (csv=gzipped CSV, parquet)")
    p.add_argument("--seed", type=int, default=42,
                    help="Random seed for deterministic interpolation/noise")
    p.add_argument("--tz", type=str, default="Asia/Kolkata",
                    help="Local timezone string")
    p.add_argument("--dry-run", action="store_true",
                    help="Run validations only, no output written")
    p.add_argument("--config", type=str, default=None,
                    help="Path to YAML/JSON config file")
    p.add_argument("--sample-households", type=str, default=None,
                    help="Path to sample_households.json for join test")
    p.add_argument("--float-precision", type=int, default=FLOAT_PRECISION,
                    help="Decimal places for float columns in CSV")
    p.add_argument("--irradiance-noise", type=float, default=0.0,
                    help="Std-dev fraction for intra-hour irradiance stochastic noise (0=off)")
    return p


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Merge config file if provided
    if args.config:
        ext = os.path.splitext(args.config)[1].lower()
        if ext in (".yaml", ".yml"):
            cfg = _load_yaml(args.config)
        elif ext == ".json":
            with open(args.config) as f:
                cfg = json.load(f)
        else:
            cfg = _load_yaml(args.config)
        args = _merge_config(args, cfg)

    # Apply defaults for values that may come from config or CLI
    if args.timestep is None:
        args.timestep = 5

    # Validate required
    missing = []
    for req in ("lat", "lon", "start", "end"):
        if getattr(args, req, None) is None:
            missing.append(f"--{req}")
    if missing:
        parser.error(f"Missing required arguments: {', '.join(missing)}")

    if args.timestep not in ALLOWED_TIMESTEPS:
        parser.error(f"--timestep must be one of {sorted(ALLOWED_TIMESTEPS)}")

    return args


# ============================================================================
# NASA POWER fetcher
# ============================================================================

def _nasa_power_fetch(lat: float, lon: float,
                      start: str, end: str) -> Tuple[pd.DataFrame, dict]:
    """
    Fetch hourly weather from NASA POWER API.
    Returns (DataFrame indexed by UTC timestamp, query_info dict).
    """
    # NASA POWER expects dates as YYYYMMDD
    start_d = dt.datetime.strptime(start, "%Y-%m-%d")
    end_d = dt.datetime.strptime(end, "%Y-%m-%d")
    start_str = start_d.strftime("%Y%m%d")
    end_str = end_d.strftime("%Y%m%d")

    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M,WS2M,CLOUD_AMT",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": start_str,
        "end": end_str,
        "format": "JSON",
        "time-standard": "UTC",
    }

    query_url = requests.Request("GET", NASA_POWER_URL, params=params).prepare().url
    query_info = {
        "url": NASA_POWER_URL,
        "params": {k: str(v) for k, v in params.items()},
        "full_url": query_url,
    }

    logger.info(f"NASA POWER query: {query_url}")

    # Exponential back-off
    resp = None
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            resp = requests.get(NASA_POWER_URL, params=params, timeout=120)
            if resp.status_code == 429:
                wait = API_BACKOFF_BASE ** attempt
                logger.warning(f"Rate limited (429). Retrying in {wait:.1f}s (attempt {attempt})")
                query_info.setdefault("warnings", []).append(
                    f"Rate-limited on attempt {attempt}, waited {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as exc:
            wait = API_BACKOFF_BASE ** attempt
            logger.warning(f"API request error: {exc}. Retrying in {wait:.1f}s (attempt {attempt})")
            if attempt == MAX_API_RETRIES:
                raise RuntimeError(f"NASA POWER API failed after {MAX_API_RETRIES} attempts: {exc}")
            time.sleep(wait)

    data = resp.json()

    # Parse the parameters block
    param_data = data.get("properties", {}).get("parameter", {})

    # NASA POWER returns keys like "20250101" with sub-keys "0","1",…,"23"
    # Build rows
    rows: List[dict] = []
    irr = param_data.get("ALLSKY_SFC_SW_DWN", {})
    temp = param_data.get("T2M", {})
    wind = param_data.get("WS2M", {})
    cloud = param_data.get("CLOUD_AMT", {})  # may be missing

    # The keys are formatted YYYYMMDDHH
    for key in sorted(irr.keys()):
        # key = '2025010100' (date + 2 digit hour)
        try:
            ts = pd.Timestamp(dt.datetime.strptime(key, "%Y%m%d%H"), tz="UTC")
        except ValueError:
            continue
        irr_val = irr.get(key, None)
        temp_val = temp.get(key, None)
        wind_val = wind.get(key, None)
        cloud_val = cloud.get(key, None) if cloud else None

        # NASA POWER uses -999 for missing
        def _clean(v):
            if v is None or v == -999 or v == -999.0:
                return np.nan
            return float(v)

        rows.append({
            "ts": ts,
            "actual_irradiance_wm2": _clean(irr_val),
            "temperature_C": _clean(temp_val),
            "wind_speed_m_s": _clean(wind_val),
            "cloud_cover_percent": _clean(cloud_val),
        })

    if not rows:
        raise RuntimeError("NASA POWER returned no data rows. Check lat/lon and date range.")

    df = pd.DataFrame(rows).set_index("ts").sort_index()
    logger.info(f"NASA POWER: fetched {len(df)} hourly rows "
                f"[{df.index.min()} → {df.index.max()}]")
    return df, query_info


# ============================================================================
# ERA5 fetcher (optional)
# ============================================================================

def _era5_fetch(lat: float, lon: float,
                start: str, end: str) -> Tuple[pd.DataFrame, dict]:
    """
    Fetch hourly weather from ERA5 via CDS API.
    Requires `cdsapi` package and a valid ~/.cdsapirc.
    """
    try:
        import cdsapi  # type: ignore
        import netCDF4 as nc  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ERA5 source requires 'cdsapi' and 'netCDF4' packages. "
            "Install with: pip install cdsapi netCDF4"
        ) from exc

    start_d = dt.datetime.strptime(start, "%Y-%m-%d")
    end_d = dt.datetime.strptime(end, "%Y-%m-%d")

    # Build request
    years = sorted({str(y) for y in range(start_d.year, end_d.year + 1)})
    months = sorted({f"{m:02d}" for m in range(1, 13)})
    days = sorted({f"{d:02d}" for d in range(1, 32)})
    hours = [f"{h:02d}:00" for h in range(24)]

    request_params = {
        "product_type": "reanalysis",
        "variable": [
            "surface_solar_radiation_downwards",
            "2m_temperature",
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "total_cloud_cover",
        ],
        "year": years,
        "month": months,
        "day": days,
        "time": hours,
        "area": [lat + 0.25, lon - 0.25, lat - 0.25, lon + 0.25],
        "format": "netcdf",
    }

    tmp_nc = "era5_download_tmp.nc"
    query_info = {"dataset": "reanalysis-era5-single-levels",
                  "params": request_params}

    logger.info("ERA5: submitting CDS request (this may take minutes)...")

    c = cdsapi.Client()
    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            c.retrieve("reanalysis-era5-single-levels", request_params, tmp_nc)
            break
        except Exception as exc:
            wait = API_BACKOFF_BASE ** attempt
            logger.warning(f"ERA5 API error: {exc}. Retry {attempt}/{MAX_API_RETRIES} in {wait:.1f}s")
            if attempt == MAX_API_RETRIES:
                raise RuntimeError(f"ERA5 fetch failed after {MAX_API_RETRIES} attempts") from exc
            time.sleep(wait)

    # Parse netCDF
    ds = nc.Dataset(tmp_nc)
    times_raw = nc.num2date(ds.variables["time"][:], ds.variables["time"].units,
                            calendar=ds.variables["time"].calendar
                            if hasattr(ds.variables["time"], "calendar") else "standard")
    ssrd = ds.variables["ssrd"][:]  # J/m², accumulated → convert to W/m²
    t2m = ds.variables["t2m"][:] - 273.15  # K → °C
    u10 = ds.variables["u10"][:]
    v10 = ds.variables["v10"][:]
    tcc = ds.variables["tcc"][:] * 100  # fraction → %

    # Wind speed from u/v
    ws = np.sqrt(u10 ** 2 + v10 ** 2)

    # ssrd is accumulated J/m² per hour → W/m² instantaneous ≈ J/m² / 3600
    irradiance = ssrd / 3600.0

    # Take nearest grid point (index 0 for single-point request)
    squeeze = lambda arr: np.array(arr).squeeze()

    rows = []
    for i, t in enumerate(times_raw):
        ts = pd.Timestamp(t, tz="UTC")
        if ts < pd.Timestamp(start, tz="UTC") or ts > pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1):
            continue
        rows.append({
            "ts": ts,
            "actual_irradiance_wm2": float(squeeze(irradiance[i])),
            "temperature_C": float(squeeze(t2m[i])),
            "wind_speed_m_s": float(squeeze(ws[i])),
            "cloud_cover_percent": float(squeeze(tcc[i])),
        })

    ds.close()
    os.remove(tmp_nc)

    df = pd.DataFrame(rows).set_index("ts").sort_index()
    logger.info(f"ERA5: parsed {len(df)} hourly rows")
    return df, query_info


# ============================================================================
# Local sensor ingestion
# ============================================================================

def _read_local_sensors(sensors_config_path: Optional[str],
                        start: str, end: str,
                        timestep: int) -> Optional[pd.DataFrame]:
    """
    Attempt to read PZEM (RS485) and INA219/INA226 (I2C) sensors.
    Returns a DataFrame with columns: pv_gen_kw, load_kw, voltage_v, current_a
    indexed by UTC timestamp, or None if no sensors are available.
    """
    if sensors_config_path and os.path.exists(sensors_config_path):
        scfg = _load_yaml(sensors_config_path)
    else:
        scfg = {}
        logger.warning("No sensors config provided or file not found. Skipping sensor ingestion.")
        return None

    sensor_rows: List[dict] = []

    # --- PZEM sensor (RS485 via USB) ---
    pzem_cfg = scfg.get("pzem", {})
    if pzem_cfg.get("enabled", False):
        try:
            import serial  # type: ignore
            port = pzem_cfg.get("port", "COM3")
            baud = pzem_cfg.get("baud", 9600)
            addr = pzem_cfg.get("address", 0x01)
            mapping = pzem_cfg.get("mapping", "pv")  # 'pv' or 'load'

            logger.info(f"Reading PZEM sensor on {port} @ {baud} baud")

            ser = serial.Serial(port, baud, timeout=2)
            # Simplified: read available data in a loop for the sampling window
            # In production, this would run as a long-lived collection daemon.
            # For script-mode, we read whatever is currently available.
            # Here we send a Modbus RTU read holding registers command.

            import struct
            # Read voltage (register 0x0000, 2 bytes)
            cmd = struct.pack(">BBHH", addr, 0x04, 0x0000, 0x000A)
            # Add CRC (simplified)
            ser.write(cmd)
            time.sleep(0.5)
            raw = ser.read(ser.in_waiting or 25)

            if len(raw) >= 25:
                voltage = struct.unpack(">H", raw[3:5])[0] / 10.0
                current = struct.unpack(">I", raw[5:9])[0] / 1000.0
                power_w = struct.unpack(">I", raw[9:13])[0] / 10.0
                row = {
                    "ts": pd.Timestamp.now(tz="UTC"),
                    "voltage_v": voltage,
                    "current_a": current,
                    "source": "local_sensor",
                }
                if mapping == "pv":
                    row["pv_gen_kw"] = power_w / 1000.0
                else:
                    row["load_kw"] = power_w / 1000.0
                sensor_rows.append(row)
            else:
                logger.warning("PZEM: insufficient data received")

            ser.close()
        except ImportError:
            logger.warning("pyserial not installed — skipping PZEM sensor")
        except Exception as exc:
            logger.warning(f"PZEM sensor read failed: {exc}")

    # --- INA219/INA226 sensor (I2C) ---
    ina_cfg = scfg.get("ina", {})
    if ina_cfg.get("enabled", False):
        try:
            from smbus2 import SMBus  # type: ignore
            bus_num = ina_cfg.get("bus", 1)
            address = int(ina_cfg.get("address", "0x40"), 16) if isinstance(
                ina_cfg.get("address"), str) else ina_cfg.get("address", 0x40)
            mapping = ina_cfg.get("mapping", "pv")
            shunt_ohms = ina_cfg.get("shunt_ohms", 0.1)

            logger.info(f"Reading INA sensor on I2C bus {bus_num}, addr 0x{address:02X}")
            bus = SMBus(bus_num)

            # Read shunt voltage register (0x01) and bus voltage register (0x02)
            shunt_raw = bus.read_word_data(address, 0x01)
            bus_raw = bus.read_word_data(address, 0x02)

            # Byte-swap (SMBus returns little-endian, INA registers big-endian)
            shunt_raw = ((shunt_raw & 0xFF) << 8) | ((shunt_raw >> 8) & 0xFF)
            bus_raw = ((bus_raw & 0xFF) << 8) | ((bus_raw >> 8) & 0xFF)

            # INA219: shunt voltage LSB = 10µV, bus voltage LSB = 4mV (shift right 3)
            shunt_v = shunt_raw * 10e-6  # in Volts
            bus_v = (bus_raw >> 3) * 4e-3  # in Volts
            current_a = shunt_v / shunt_ohms
            power_w = bus_v * current_a

            row = {
                "ts": pd.Timestamp.now(tz="UTC"),
                "voltage_v": bus_v,
                "current_a": current_a,
                "source": "local_sensor",
            }
            if mapping == "pv":
                row["pv_gen_kw"] = power_w / 1000.0
            else:
                row["load_kw"] = power_w / 1000.0
            sensor_rows.append(row)

            bus.close()
        except ImportError:
            logger.warning("smbus2 not installed — skipping INA sensor")
        except Exception as exc:
            logger.warning(f"INA sensor read failed: {exc}")

    if not sensor_rows:
        logger.info("No sensor data collected")
        return None

    df = pd.DataFrame(sensor_rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    logger.info(f"Sensor ingestion: {len(df)} samples collected")
    return df


# ============================================================================
# Resampling & interpolation
# ============================================================================

def resample_weather(df: pd.DataFrame, timestep: int,
                     start: str, end: str,
                     seed: int = 42,
                     irradiance_noise: float = 0.0) -> pd.DataFrame:
    """
    Resample hourly (or other) weather data to `timestep` minutes.
    Uses time-based linear interpolation for temp, wind, cloud;
    linear interpolation (+ optional stochastic noise) for irradiance.
    Fills short gaps (<= 30 min) and leaves larger gaps as NaN.
    """
    rng = np.random.RandomState(seed)
    freq = f"{timestep}min"

    # Build target index
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(minutes=timestep)
    target_idx = pd.date_range(start_ts, end_ts, freq=freq)

    # Reindex to target, preserving existing data, then interpolate
    df_resampled = df.reindex(df.index.union(target_idx)).sort_index()

    # Interpolation limit: must cover at least the gap between source
    # data points (typically 60 min for hourly data) AND the gap-fill
    # threshold. Use the larger of source_interval/timestep and
    # MAX_GAP_FILL_MINUTES/timestep so normal hourly→5min upsampling
    # does not leave NaN holes inside known intervals.
    # Detect the typical source interval from the original data.
    if len(df) > 1:
        src_diffs = pd.Series(df.index[1:] - df.index[:-1])
        median_gap_min = int(src_diffs.median().total_seconds() / 60)
    else:
        median_gap_min = 60
    interp_limit = max(1, max(median_gap_min, MAX_GAP_FILL_MINUTES) // timestep)

    # Linear interpolation for weather vars
    for col in ["temperature_C", "wind_speed_m_s", "cloud_cover_percent"]:
        if col in df_resampled.columns:
            df_resampled[col] = df_resampled[col].interpolate(
                method="time", limit=interp_limit
            )

    # Irradiance: linear interpolation + optional noise
    if "actual_irradiance_wm2" in df_resampled.columns:
        df_resampled["actual_irradiance_wm2"] = df_resampled[
            "actual_irradiance_wm2"
        ].interpolate(method="time", limit=interp_limit)

        if irradiance_noise > 0:
            mask = df_resampled["actual_irradiance_wm2"].notna()
            vals = df_resampled.loc[mask, "actual_irradiance_wm2"]
            noise = rng.normal(0, irradiance_noise, size=len(vals)) * vals
            df_resampled.loc[mask, "actual_irradiance_wm2"] = np.maximum(
                0, vals + noise
            )

    # Keep only target timestamps
    df_resampled = df_resampled.reindex(target_idx)
    df_resampled.index.name = "ts"

    return df_resampled


def merge_sensors(weather_df: pd.DataFrame,
                  sensor_df: Optional[pd.DataFrame],
                  timestep: int) -> pd.DataFrame:
    """Merge sensor data into weather data by nearest timestamp (within half timestep)."""
    if sensor_df is None or sensor_df.empty:
        return weather_df

    tolerance = pd.Timedelta(minutes=timestep / 2)
    sensor_cols = ["pv_gen_kw", "load_kw", "voltage_v", "current_a"]

    # Ensure sensor columns exist
    for col in sensor_cols:
        if col not in sensor_df.columns:
            sensor_df[col] = np.nan

    # Use merge_asof for nearest-timestamp join
    weather_reset = weather_df.reset_index().rename(columns={"index": "ts"})
    if "ts" not in weather_reset.columns:
        weather_reset = weather_df.reset_index()
        weather_reset.columns.values[0] = "ts"

    sensor_reset = sensor_df[sensor_cols].reset_index()
    sensor_reset.columns.values[0] = "ts"

    merged = pd.merge_asof(
        weather_reset.sort_values("ts"),
        sensor_reset.sort_values("ts"),
        on="ts",
        tolerance=tolerance,
        direction="nearest",
    )
    merged = merged.set_index("ts")

    # Where sensor provided a source, tag those rows
    if "source" in sensor_df.columns:
        sensor_ts_set = set(sensor_df.index)
        for idx in merged.index:
            if idx in sensor_ts_set:
                merged.loc[idx, "source"] = "local_sensor"

    return merged


# ============================================================================
# Validation
# ============================================================================

class ValidationError(Exception):
    pass


def validate_data(df: pd.DataFrame, timestep: int,
                  metadata: dict,
                  sample_households: Optional[str] = None) -> List[str]:
    """
    Run all validation tests. Returns list of warning strings.
    Raises ValidationError on hard failures.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. Schema test — exact columns
    expected = set(CANONICAL_COLUMNS)
    actual = set(df.columns)
    if df.index.name == "ts" or isinstance(df.index, pd.DatetimeIndex):
        actual.add("ts")
    
    df_cols_check = list(df.columns)
    if df.index.name == "ts" or isinstance(df.index, pd.DatetimeIndex):
        df_cols_check = ["ts"] + df_cols_check
        
    col_set = set(df_cols_check)
    for c in CANONICAL_COLUMNS:
        if c not in col_set:
            errors.append(f"Schema: missing required column '{c}'")

    # 2. Time alignment test
    if df.index.name == "ts" or "ts" in df.columns:
        ts_index = df.index if df.index.name == "ts" else pd.to_datetime(df["ts"], utc=True)
        # Check multiples of timestep
        offsets = ts_index.map(lambda t: t.minute % timestep if timestep < 60 else t.minute)
        bad_align = (offsets != 0).sum()
        if bad_align > 0:
            errors.append(f"Time alignment: {bad_align} timestamps not aligned to {timestep}-min grid")
        # Check duplicates
        dupes = ts_index.duplicated().sum()
        if dupes > 0:
            errors.append(f"Time alignment: {dupes} duplicate timestamps found")

    # 3. Range tests (clip and count corrections)
    corrections = {}
    if "actual_irradiance_wm2" in df.columns:
        neg_irr = (df["actual_irradiance_wm2"] < 0).sum()
        if neg_irr > 0:
            df["actual_irradiance_wm2"] = df["actual_irradiance_wm2"].clip(lower=0)
            corrections["actual_irradiance_wm2_neg_clipped"] = int(neg_irr)
            warnings.append(f"Clipped {neg_irr} negative irradiance values to 0")

    if "temperature_C" in df.columns:
        lo = (df["temperature_C"] < -50).sum()
        hi = (df["temperature_C"] > 60).sum()
        if lo + hi > 0:
            df["temperature_C"] = df["temperature_C"].clip(-50, 60)
            corrections["temperature_C_clipped"] = int(lo + hi)
            warnings.append(f"Clipped {lo + hi} temperature values to [-50, 60]°C")

    if "wind_speed_m_s" in df.columns:
        neg_w = (df["wind_speed_m_s"] < 0).sum()
        if neg_w > 0:
            df["wind_speed_m_s"] = df["wind_speed_m_s"].clip(lower=0)
            corrections["wind_speed_m_s_neg_clipped"] = int(neg_w)
            warnings.append(f"Clipped {neg_w} negative wind speed values to 0")

    metadata["range_corrections"] = corrections

    # 4. Missing data
    total = len(df)
    missing_pct: Dict[str, float] = {}
    for col in df.columns:
        pct = float(df[col].isna().sum() / total * 100) if total > 0 else 0.0
        missing_pct[col] = round(pct, 2)
    metadata["missing_percent_per_column"] = missing_pct

    high_missing = {c: p for c, p in missing_pct.items() if p > 5.0}
    if high_missing:
        for c, p in high_missing.items():
            warnings.append(f">5% missing in '{c}': {p:.1f}%")

    # 5. Summary stats
    stats = {}
    for col in ["actual_irradiance_wm2", "temperature_C", "wind_speed_m_s", "cloud_cover_percent"]:
        if col in df.columns and df[col].notna().any():
            stats[col] = {
                "min": round(float(df[col].min()), 4),
                "max": round(float(df[col].max()), 4),
                "mean": round(float(df[col].mean()), 4),
            }
    metadata["summary_stats"] = stats

    # 6. Detect large gaps
    if df.index.name == "ts":
        ts_sorted = df.index.sort_values()
        diffs = pd.Series(ts_sorted[1:] - ts_sorted[:-1])
        diffs = diffs.reset_index(drop=True)
        large_gap_mask = diffs > pd.Timedelta(minutes=MAX_GAP_FILL_MINUTES)
        if large_gap_mask.any():
            gap_list = []
            for i in range(len(diffs)):
                if diffs[i] > pd.Timedelta(minutes=MAX_GAP_FILL_MINUTES):
                    gap_list.append({
                        "start": str(ts_sorted[i]),
                        "end": str(ts_sorted[i + 1]),
                        "duration_minutes": int(diffs[i].total_seconds() / 60),
                    })
            metadata["large_gaps"] = gap_list
            warnings.append(f"Detected {len(gap_list)} gaps > {MAX_GAP_FILL_MINUTES} min")

    # 7. Sample join test
    if sample_households:
        try:
            with open(sample_households) as f:
                hh_data = json.load(f)
            hh_ids = hh_data if isinstance(hh_data, list) else hh_data.get("household_ids", [])
            has_hh = "household_id" in df.columns
            join_warnings = []
            if has_hh:
                existing_ids = set(df["household_id"].dropna().unique())
                for hid in hh_ids:
                    if hid not in existing_ids:
                        join_warnings.append(f"household_id '{hid}' not found in collected data")
            else:
                join_warnings.append("No household_id column — generator will replicate weather for all households")
            if join_warnings:
                warnings.extend(join_warnings)
                metadata["sample_join_warnings"] = join_warnings
        except Exception as exc:
            warnings.append(f"Sample join test failed: {exc}")

    if errors:
        metadata["validation_errors"] = errors
        raise ValidationError(
            "Validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    metadata["validation_warnings"] = warnings
    return warnings


# ============================================================================
# Output
# ============================================================================

def write_outputs(df: pd.DataFrame, metadata: dict, args: argparse.Namespace) -> None:
    """Write all output files."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir = out_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    precision = args.float_precision

    # Prepare DataFrame for writing
    df_out = df.copy()
    if df_out.index.name == "ts":
        df_out = df_out.reset_index()
    elif "ts" not in df_out.columns:
        # Index is unnamed datetime → rename to ts
        df_out = df_out.reset_index()
        df_out = df_out.rename(columns={df_out.columns[0]: "ts"})

    # Ensure ts is formatted as UTC ISO8601
    df_out["ts"] = pd.to_datetime(df_out["ts"], utc=True).map(_iso_utc)

    # Ensure column order
    for col in CANONICAL_COLUMNS:
        if col not in df_out.columns:
            df_out[col] = np.nan
    df_out = df_out[CANONICAL_COLUMNS]

    # 1. Gzipped CSV
    csv_path = out_dir / "collected_data.csv.gz"
    df_out.to_csv(csv_path, index=False, float_format=f"%.{precision}f",
                  compression="gzip")
    logger.info(f"Wrote {csv_path} ({len(df_out)} rows)")

    # 2. Parquet (if requested)
    if args.format == "parquet":
        parquet_path = out_dir / "collected_data.parquet"
        # Need ts back as datetime for parquet
        df_pq = df_out.copy()
        df_pq["ts"] = pd.to_datetime(df_pq["ts"], utc=True)
        df_pq.to_parquet(parquet_path, index=False, engine="pyarrow")
        logger.info(f"Wrote {parquet_path}")

    # 3. Metadata JSON
    metadata["row_count"] = len(df_out)
    meta_path = out_dir / "collected_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    logger.info(f"Wrote {meta_path}")

    # 4. Sample 1-hour slice
    sample_path = out_dir / "collected_sample_hour.csv"
    df_sample = df_out.head(60 // args.timestep)  # 1 hour of data
    df_sample.to_csv(sample_path, index=False, float_format=f"%.{precision}f")
    logger.info(f"Wrote {sample_path} ({len(df_sample)} rows)")

    # 5. Execution log
    exec_log = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "args": vars(args),
        "rows_written": len(df_out),
        "output_files": [
            str(csv_path), str(meta_path), str(sample_path),
        ],
    }
    exec_log_path = log_dir / "execution_log.json"
    with open(exec_log_path, "w") as f:
        json.dump(exec_log, f, indent=2, default=str)
    logger.info(f"Wrote {exec_log_path}")


# ============================================================================
# Main pipeline
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(args.seed)
    random.seed(args.seed)

    metadata: Dict[str, Any] = {
        "script": "data_collection.py",
        "version": "1.0.0",
        "git_commit": git_commit_hash(),
        "cli_args": vars(args),
        "lat": args.lat,
        "lon": args.lon,
        "timezone": args.tz,
        "date_range": {"start": args.start, "end": args.end},
        "timestep_minutes": args.timestep,
        "seed": args.seed,
        "source": args.source,
        "collection_started_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # ---- Step 1: Fetch weather data ----
    logger.info(f"Fetching weather data from '{args.source}'...")
    try:
        if args.source == "nasa_power":
            weather_df, query_info = _nasa_power_fetch(
                args.lat, args.lon, args.start, args.end
            )
        elif args.source == "era5":
            weather_df, query_info = _era5_fetch(
                args.lat, args.lon, args.start, args.end
            )
        else:
            raise ValueError(f"Unknown source: {args.source}")
        metadata["api_query"] = query_info
    except Exception as exc:
        logger.error(f"Data fetch failed: {exc}")
        metadata["error"] = str(exc)
        # Write partial metadata
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "collected_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        return 1

    # ---- Step 2: Resample to target timestep ----
    logger.info(f"Resampling to {args.timestep}-minute intervals...")
    weather_df = resample_weather(
        weather_df, args.timestep,
        args.start, args.end,
        seed=args.seed,
        irradiance_noise=args.irradiance_noise,
    )
    metadata["interpolation_method"] = {
        "weather_vars": "time-based linear interpolation",
        "irradiance": "time-based linear interpolation"
                       + (f" + stochastic noise (std_frac={args.irradiance_noise})"
                          if args.irradiance_noise > 0 else ""),
        "gap_fill_limit_minutes": MAX_GAP_FILL_MINUTES,
    }

    # ---- Step 3: Local sensor ingestion ----
    sensor_df = None
    if args.include_local_sensors:
        logger.info("Ingesting local sensors...")
        sensor_df = _read_local_sensors(
            args.sensors_config, args.start, args.end, args.timestep
        )
        if sensor_df is not None:
            metadata["sensor_samples_ingested"] = len(sensor_df)

    # ---- Step 4: Merge sensor into weather ----
    weather_df = merge_sensors(weather_df, sensor_df, args.timestep)

    # ---- Step 5: Add canonical columns ----
    weather_df["lat"] = args.lat
    weather_df["lon"] = args.lon
    weather_df["source"] = args.source
    weather_df["tz"] = args.tz

    # Ensure all canonical columns present
    for col in CANONICAL_COLUMNS:
        if col not in weather_df.columns and col != "ts":
            weather_df[col] = np.nan

    # ---- Step 6: Validate ----
    logger.info("Running validation tests...")
    try:
        warnings = validate_data(
            weather_df, args.timestep, metadata,
            sample_households=args.sample_households,
        )
        for w in warnings:
            logger.warning(w)
    except ValidationError as ve:
        logger.error(str(ve))
        # Write partial metadata even on failure
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "collected_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        return 1

    metadata["collection_finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    # ---- Step 7: Write outputs ----
    if args.dry_run:
        logger.info("=== DRY RUN — Compatibility Report ===")
        logger.info(f"Rows       : {len(weather_df)}")
        logger.info(f"Columns    : {list(weather_df.columns)}")
        logger.info(f"Date range : {weather_df.index.min()} → {weather_df.index.max()}")
        logger.info(f"Timestep   : {args.timestep} min")
        has_pv = weather_df["pv_gen_kw"].notna().any() if "pv_gen_kw" in weather_df.columns else False
        has_load = weather_df["load_kw"].notna().any() if "load_kw" in weather_df.columns else False
        logger.info(f"Has PV gen : {has_pv}")
        logger.info(f"Has Load   : {has_load}")
        if has_pv and has_load:
            logger.info("→ Generator will use measured PV/Load (Case A)")
        else:
            logger.info("→ Generator will derive PV from irradiance & synthesize load (Case B)")
        logger.info(f"Warnings   : {len(warnings)}")
        for w in warnings:
            logger.info(f"  - {w}")
        logger.info("=== DRY RUN COMPLETE (no files written) ===")
        # Still write metadata for inspection
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "collected_metadata.json", "w") as f:
            json.dump(metadata, f, indent=2, default=str)
        return 0

    write_outputs(weather_df, metadata, args)
    logger.info("Data collection complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
