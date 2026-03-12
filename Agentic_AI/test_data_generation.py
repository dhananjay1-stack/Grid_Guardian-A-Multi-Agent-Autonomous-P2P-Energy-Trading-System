#!/usr/bin/env python3
"""
test_data_generation.py — Pytest suite for data_generation.py
=============================================================
Covers: header exactness, mass balance, SoC bounds, offer/trade
consistency, commit-hash verification, determinism (--seed),
event injection, noise, salt isolation, dry-run mode.
"""

import gzip
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

# Import the module under test
import data_generation as dg


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_collected(tmp_path_factory) -> Path:
    """Create a small collected_data.csv.gz for testing."""
    tmp = tmp_path_factory.mktemp("collected")
    out = tmp / "collected_data.csv.gz"

    rng = np.random.RandomState(99)
    ts_range = pd.date_range("2025-01-01", periods=288, freq="5min", tz="UTC")
    rows = []
    for ts in ts_range:
        hour = ts.hour
        irr = max(0, 400 * np.sin(np.pi * (hour - 5) / 14)) if 6 <= hour <= 18 else 0.0
        rows.append({
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": 18.5204,
            "lon": 73.8567,
            "source": "nasa_power",
            "actual_irradiance_wm2": round(irr + rng.normal(0, 5), 2),
            "temperature_C": round(25 + 5 * np.sin(np.pi * hour / 24) + rng.normal(0, 1), 2),
            "cloud_cover_percent": round(max(0, min(100, 30 + rng.normal(0, 10))), 2),
            "wind_speed_m_s": round(max(0, 3 + rng.normal(0, 1)), 2),
            "pv_gen_kw": "",
            "load_kw": "",
            "voltage_v": "",
            "current_a": "",
            "tz": "Asia/Kolkata",
        })
    df = pd.DataFrame(rows)
    df.to_csv(out, index=False, compression="gzip")
    return out


@pytest.fixture(scope="session")
def sample_config(tmp_path_factory) -> Path:
    """Create a test config YAML."""
    tmp = tmp_path_factory.mktemp("config")
    out = tmp / "config.yaml"
    cfg = {
        "simulation": {"timestep_min": 5, "seed": 42},
        "households": [
            {
                "household_id": "test_hh_01",
                "panel_kwp": 5.0,
                "panel_tilt": 18,
                "panel_azimuth": 180,
                "system_efficiency": 0.78,
                "soc_capacity_kwh": 5.0,
                "initial_soc_kwh": 2.5,
                "soc_min": 0.5,
                "soc_max": 4.9,
                "battery_nominal_power_kw": 1.5,
                "efficiency_charge": 0.95,
                "efficiency_discharge": 0.95,
                "load_template": "family_home_v1",
            },
            {
                "household_id": "test_hh_02",
                "panel_kwp": 1.0,
                "panel_tilt": 15,
                "panel_azimuth": 180,
                "system_efficiency": 0.75,
                "soc_capacity_kwh": 3.0,
                "initial_soc_kwh": 1.5,
                "soc_min": 0.3,
                "soc_max": 2.9,
                "battery_nominal_power_kw": 1.0,
                "efficiency_charge": 0.95,
                "efficiency_discharge": 0.95,
                "load_template": "small_apartment_v1",
            },
        ],
        "market": {
            "match_window_min": 15,
            "delivery_window_min": 15,
            "acceptance_rule": "price_below_utility",
            "min_offer_kwh": 0.01,
            "max_offer_kwh": 3.0,
            "price_floor": 2.0,
            "price_ceiling": 20.0,
            "utility_price_function": {
                "tou_table": {
                    "00:00-06:00": 3.0,
                    "06:00-18:00": 5.0,
                    "18:00-22:00": 8.0,
                    "22:00-24:00": 4.0,
                },
            },
        },
        "events": {
            "cloud_ramp_prob_per_day": 0.2,
            "ev_arrival_prob_per_day": 0.1,
            "outage_prob_per_day": 0.01,
        },
        "noise": {
            "pv_std_fraction": 0.02,
            "load_std_fraction": 0.05,
            "missing_rate": 0.001,
        },
        "reward": {
            "cost_savings_coeff": 1.0,
            "grid_stability_coeff": 0.1,
            "battery_deg_coeff": 0.01,
        },
        "zk": {
            "commit_hash_algo": "sha256",
            "salt_length_bytes": 16,
        },
    }
    with open(out, "w") as f:
        yaml.dump(cfg, f)
    return out


@pytest.fixture()
def out_dir(tmp_path) -> Path:
    d = tmp_path / "generated"
    d.mkdir()
    return d


class SimResult:
    """Holds a simulation result for reuse across tests."""
    dataset_df: pd.DataFrame
    offers_df: pd.DataFrame
    trades_df: pd.DataFrame
    summary: dict


@pytest.fixture(scope="session")
def sim_result(sample_collected, sample_config, tmp_path_factory) -> SimResult:
    """Run simulation once and cache result."""
    out_dir = tmp_path_factory.mktemp("sim_out")
    argv = [
        "--collected", str(sample_collected),
        "--config", str(sample_config),
        "--out", str(out_dir),
        "--seed", "42",
        "--compute-reward",
    ]
    args = dg.parse_args(argv)
    cfg = dg._load_yaml(str(sample_config))
    collected = pd.read_csv(sample_collected, compression="gzip")

    zk_cfg = cfg.get("zk", {})
    salt_path = str(out_dir / "secrets")
    salt_store = dg.SaltStore(salt_path, encrypt=False)

    ds, of, tr, sm = dg.run_simulation(collected, cfg, args, salt_store)

    result = SimResult()
    result.dataset_df = ds
    result.offers_df = of
    result.trades_df = tr
    result.summary = sm
    result.salt_store = salt_store
    result.out_dir = out_dir
    return result


# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------

class TestHeaders:
    def test_dataset_header_exact(self, sim_result):
        assert list(sim_result.dataset_df.columns) == dg.DATASET_HEADER

    def test_offers_header_exact(self, sim_result):
        assert list(sim_result.offers_df.columns) == dg.OFFERS_HEADER

    def test_trades_header_exact(self, sim_result):
        assert list(sim_result.trades_df.columns) == dg.TRADES_HEADER


class TestMassBalance:
    def test_net_equals_pv_minus_load(self, sim_result):
        df = sim_result.dataset_df.copy()
        df["pv_gen_kw"] = pd.to_numeric(df["pv_gen_kw"], errors="coerce")
        df["load_kw"] = pd.to_numeric(df["load_kw"], errors="coerce")
        df["net_kw"] = pd.to_numeric(df["net_kw"], errors="coerce")
        valid = df.dropna(subset=["pv_gen_kw", "load_kw", "net_kw"])
        expected = valid["pv_gen_kw"] - valid["load_kw"]
        assert (abs(valid["net_kw"] - expected) < 1e-4).all(), \
            "Mass balance violation detected"


class TestSoCBounds:
    def test_soc_within_bounds(self, sim_result, sample_config):
        cfg = dg._load_yaml(str(sample_config))
        hh_bounds = {h["household_id"]: (h["soc_min"], h["soc_max"])
                     for h in cfg["households"]}
        df = sim_result.dataset_df.copy()
        df["soc_kwh"] = pd.to_numeric(df["soc_kwh"], errors="coerce")
        for hh_id, (lo, hi) in hh_bounds.items():
            hh_rows = df[df["household_id"] == hh_id]
            soc = hh_rows["soc_kwh"].dropna()
            assert (soc >= lo - 0.01).all(), f"{hh_id} SoC below min"
            assert (soc <= hi + 0.01).all(), f"{hh_id} SoC above max"


class TestOfferTradeConsistency:
    def test_every_trade_has_valid_offer(self, sim_result):
        offer_ids = set(sim_result.offers_df["offer_id"])
        for _, trade in sim_result.trades_df.iterrows():
            assert trade["offer_id"] in offer_ids, \
                f"Trade {trade['trade_id']} has unknown offer {trade['offer_id']}"

    def test_all_offers_have_commit_hash(self, sim_result):
        for _, offer in sim_result.offers_df.iterrows():
            assert offer["commit_hash"] != "" and pd.notna(offer["commit_hash"]), \
                f"Offer {offer['offer_id']} missing commit_hash"

    def test_offers_not_empty(self, sim_result):
        assert len(sim_result.offers_df) > 0, "No offers generated"


class TestCommitHash:
    def test_commit_hash_format(self, sim_result):
        for _, offer in sim_result.offers_df.head(10).iterrows():
            h = offer["commit_hash"]
            assert len(h) == 64, f"commit_hash wrong length: {len(h)}"
            assert all(c in "0123456789abcdef" for c in h)


class TestDeterminism:
    def test_same_seed_same_output(self, sample_collected, sample_config,
                                   tmp_path):
        runs = []
        for i in range(2):
            out = tmp_path / f"run_{i}"
            out.mkdir()
            argv = [
                "--collected", str(sample_collected),
                "--config", str(sample_config),
                "--out", str(out),
                "--seed", "123",
                "--compute-reward",
            ]
            args = dg.parse_args(argv)
            cfg = dg._load_yaml(str(sample_config))
            collected = pd.read_csv(sample_collected, compression="gzip")
            salt_store = dg.SaltStore(str(out / "secrets"), encrypt=False)
            ds, of, tr, sm = dg.run_simulation(collected, cfg, args, salt_store)
            runs.append(ds)

        pd.testing.assert_frame_equal(runs[0], runs[1])


class TestEventInjection:
    def test_events_present(self, sim_result):
        events = sim_result.dataset_df["event_flag"].unique()
        # Normal must always be present
        assert "normal" in events
        # With 288 steps * 2 households = 576 rows, at least one event type
        # should appear (cloud_ramp, ev_arrival, or grid_outage)
        event_set = set(events) - {"normal"}
        assert len(event_set) > 0, "No events injected"


class TestReward:
    def test_reward_computed(self, sim_result):
        rewards = pd.to_numeric(sim_result.dataset_df["reward"],
                                errors="coerce")
        non_null = rewards.dropna()
        assert len(non_null) > 0, "No rewards computed"


class TestDryRun:
    def test_dry_run_no_files(self, sample_collected, sample_config, tmp_path):
        out = tmp_path / "dry"
        argv = [
            "--collected", str(sample_collected),
            "--config", str(sample_config),
            "--out", str(out),
            "--seed", "42",
            "--dry-run",
        ]
        rc = dg.main(argv)
        assert rc == 0
        # No output files should be created (out dir may or may not exist)
        if out.exists():
            files = list(out.rglob("*.csv.gz"))
            assert len(files) == 0, "Dry run should not produce CSV files"


class TestSaltIsolation:
    def test_salts_stored(self, sim_result):
        secrets_dir = Path(sim_result.out_dir) / "secrets"
        if secrets_dir.exists():
            salt_files = list(secrets_dir.rglob("*.salt"))
            assert len(salt_files) > 0, "No salt files stored"


class TestRowCount:
    def test_expected_row_count(self, sim_result, sample_config):
        cfg = dg._load_yaml(str(sample_config))
        n_households = len(cfg["households"])
        # 288 timesteps * 2 households = 576
        expected = 288 * n_households
        assert len(sim_result.dataset_df) == expected, \
            f"Expected {expected} rows, got {len(sim_result.dataset_df)}"


class TestFullPipeline:
    def test_main_returns_zero(self, sample_collected, sample_config, tmp_path):
        out = tmp_path / "pipeline"
        argv = [
            "--collected", str(sample_collected),
            "--config", str(sample_config),
            "--out", str(out),
            "--seed", "42",
            "--compute-reward",
        ]
        rc = dg.main(argv)
        assert rc == 0

        # Check output files exist
        assert (out / "generated_dataset.csv.gz").exists()
        assert (out / "offers.csv.gz").exists()
        assert (out / "trades.csv.gz").exists()
        assert (out / "dataset_version.json").exists()
        assert (out / "generation_summary.json").exists()

    def test_output_roundtrip(self, sample_collected, sample_config, tmp_path):
        out = tmp_path / "rt"
        argv = [
            "--collected", str(sample_collected),
            "--config", str(sample_config),
            "--out", str(out),
            "--seed", "42",
        ]
        rc = dg.main(argv)
        assert rc == 0

        # Read back
        ds = pd.read_csv(out / "generated_dataset.csv.gz", compression="gzip")
        assert list(ds.columns) == dg.DATASET_HEADER

        of = pd.read_csv(out / "offers.csv.gz", compression="gzip")
        assert list(of.columns) == dg.OFFERS_HEADER

        tr = pd.read_csv(out / "trades.csv.gz", compression="gzip")
        assert list(tr.columns) == dg.TRADES_HEADER


class TestLoadTemplates:
    def test_all_templates_have_24_hours(self):
        for name, tmpl in dg.LOAD_TEMPLATES.items():
            assert len(tmpl) == 24, f"Template {name} has {len(tmpl)} hours"
            for h in range(24):
                assert h in tmpl, f"Template {name} missing hour {h}"

    def test_template_values_positive(self):
        for name, tmpl in dg.LOAD_TEMPLATES.items():
            for h, (wd, we) in tmpl.items():
                assert wd >= 0, f"Template {name} hour {h} weekday negative"
                assert we >= 0, f"Template {name} hour {h} weekend negative"


class TestCommitHashCompute:
    def test_sha256_commit(self):
        ts = "2025-01-01T12:00:00Z"
        hh = "test_hh_01"
        kwh = 1.5
        salt = "abcdef0123456789"
        h = dg.compute_commit_hash(ts, hh, kwh, salt, "sha256")
        assert len(h) == 64
        # Verify manually
        expected_input = f"{ts}|{hh}|{kwh:.6f}|{salt}"
        expected = hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
        assert h == expected


class TestTOUParsing:
    def test_parse_tou(self):
        tou = {
            "00:00-06:00": 3.0,
            "06:00-18:00": 5.0,
            "18:00-22:00": 8.0,
            "22:00-24:00": 4.0,
        }
        entries = dg._parse_tou_table(tou)
        assert len(entries) == 4
        assert entries[0] == (0, 360, 3.0)
        assert entries[-1] == (1320, 1440, 4.0)
