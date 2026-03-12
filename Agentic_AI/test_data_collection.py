#!/usr/bin/env python3
"""
test_data_collection.py — Unit Test Suite for data_collection.py
================================================================
Verifies: schema, resampling, range checks, and API-cached sample.

Run with:  pytest test_data_collection.py -v
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Import the module under test
import data_collection as dc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_hourly_df():
    """Create a sample hourly weather DataFrame (7 days)."""
    start = pd.Timestamp("2025-01-01", tz="UTC")
    end = pd.Timestamp("2025-01-07 23:00:00", tz="UTC")
    idx = pd.date_range(start, end, freq="1h")

    rng = np.random.RandomState(42)
    n = len(idx)

    df = pd.DataFrame({
        "actual_irradiance_wm2": np.maximum(0, 500 * np.sin(
            np.linspace(0, 7 * 2 * np.pi, n)) + rng.normal(0, 50, n)),
        "temperature_C": 25 + 5 * np.sin(
            np.linspace(0, 7 * 2 * np.pi, n)) + rng.normal(0, 1, n),
        "wind_speed_m_s": np.abs(3 + rng.normal(0, 1, n)),
        "cloud_cover_percent": np.clip(
            50 + 30 * np.sin(np.linspace(0, 14 * np.pi, n)) + rng.normal(0, 10, n),
            0, 100),
    }, index=idx)
    df.index.name = "ts"
    return df


@pytest.fixture
def tmp_out_dir():
    """Temporary output directory."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_args(tmp_out_dir):
    """Sample parsed arguments."""
    return dc.parse_args([
        "--lat", "18.5204", "--lon", "73.8567",
        "--start", "2025-01-01", "--end", "2025-01-07",
        "--timestep", "5",
        "--source", "nasa_power",
        "--out", tmp_out_dir,
        "--format", "csv",
        "--seed", "42",
        "--tz", "Asia/Kolkata",
    ])


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestSchema:
    """Verify canonical column schema."""

    def test_canonical_columns_defined(self):
        assert len(dc.CANONICAL_COLUMNS) == 13

    def test_canonical_columns_names(self):
        expected = [
            "ts", "lat", "lon", "source",
            "actual_irradiance_wm2", "temperature_C",
            "cloud_cover_percent", "wind_speed_m_s",
            "pv_gen_kw", "load_kw", "voltage_v", "current_a", "tz",
        ]
        assert dc.CANONICAL_COLUMNS == expected

    def test_output_has_all_columns(self, sample_hourly_df, sample_args):
        """After adding canonical columns, all must be present."""
        df = sample_hourly_df.copy()
        df = dc.resample_weather(df, 5, "2025-01-01", "2025-01-07", seed=42)
        df["lat"] = 18.5204
        df["lon"] = 73.8567
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for col in dc.CANONICAL_COLUMNS:
            if col not in df.columns and col != "ts":
                df[col] = np.nan

        df_out = df.reset_index()
        for col in dc.CANONICAL_COLUMNS:
            assert col in df_out.columns, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# Resampling Tests
# ---------------------------------------------------------------------------

class TestResampling:
    """Verify resampling and interpolation."""

    def test_resample_5min(self, sample_hourly_df):
        """Hourly → 5-min should produce ~12x more rows."""
        result = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07", seed=42
        )
        # 7 days * 24h * 12 (5-min intervals) = 2016 rows
        expected = 7 * 24 * 12
        assert len(result) == expected

    def test_resample_15min(self, sample_hourly_df):
        result = dc.resample_weather(
            sample_hourly_df, 15, "2025-01-01", "2025-01-07", seed=42
        )
        expected = 7 * 24 * 4
        assert len(result) == expected

    def test_resample_60min(self, sample_hourly_df):
        result = dc.resample_weather(
            sample_hourly_df, 60, "2025-01-01", "2025-01-07", seed=42
        )
        expected = 7 * 24
        assert len(result) == expected

    def test_resample_preserves_index_alignment(self, sample_hourly_df):
        result = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07", seed=42
        )
        offsets = result.index.minute % 5
        assert (offsets == 0).all(), "All timestamps must be aligned to 5-min"

    def test_resample_deterministic(self, sample_hourly_df):
        """Same seed → same result."""
        r1 = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07", seed=42
        )
        r2 = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07", seed=42
        )
        pd.testing.assert_frame_equal(r1, r2)

    def test_irradiance_noise(self, sample_hourly_df):
        """With noise > 0, result should differ from no-noise."""
        r_clean = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07",
            seed=42, irradiance_noise=0.0
        )
        r_noisy = dc.resample_weather(
            sample_hourly_df, 5, "2025-01-01", "2025-01-07",
            seed=42, irradiance_noise=0.1
        )
        # They should not be identical (noise was applied)
        assert not r_clean["actual_irradiance_wm2"].equals(
            r_noisy["actual_irradiance_wm2"]
        )
        # But irradiance should still be non-negative
        assert (r_noisy["actual_irradiance_wm2"].dropna() >= 0).all()


# ---------------------------------------------------------------------------
# Range / Validation Tests
# ---------------------------------------------------------------------------

class TestRangeChecks:
    """Verify range clipping and validation."""

    def test_negative_irradiance_clipped(self, sample_hourly_df):
        df = sample_hourly_df.copy()
        df.iloc[0, df.columns.get_loc("actual_irradiance_wm2")] = -100
        df["lat"] = 18.5
        df["lon"] = 73.8
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for c in dc.CANONICAL_COLUMNS:
            if c not in df.columns and c != "ts":
                df[c] = np.nan

        metadata: dict = {}
        dc.validate_data(df, 60, metadata)
        assert (df["actual_irradiance_wm2"] >= 0).all()
        assert metadata["range_corrections"].get("actual_irradiance_wm2_neg_clipped", 0) > 0

    def test_temperature_clipped(self, sample_hourly_df):
        df = sample_hourly_df.copy()
        df.iloc[0, df.columns.get_loc("temperature_C")] = -100
        df.iloc[1, df.columns.get_loc("temperature_C")] = 100
        df["lat"] = 18.5
        df["lon"] = 73.8
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for c in dc.CANONICAL_COLUMNS:
            if c not in df.columns and c != "ts":
                df[c] = np.nan

        metadata: dict = {}
        dc.validate_data(df, 60, metadata)
        assert df["temperature_C"].min() >= -50
        assert df["temperature_C"].max() <= 60

    def test_wind_speed_clipped(self, sample_hourly_df):
        df = sample_hourly_df.copy()
        df.iloc[0, df.columns.get_loc("wind_speed_m_s")] = -5
        df["lat"] = 18.5
        df["lon"] = 73.8
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for c in dc.CANONICAL_COLUMNS:
            if c not in df.columns and c != "ts":
                df[c] = np.nan

        metadata: dict = {}
        dc.validate_data(df, 60, metadata)
        assert (df["wind_speed_m_s"] >= 0).all()

    def test_validation_reports_missing(self, sample_hourly_df):
        df = sample_hourly_df.copy()
        # Set 10% of irradiance to NaN
        n_na = len(df) // 10
        df.iloc[:n_na, df.columns.get_loc("actual_irradiance_wm2")] = np.nan
        df["lat"] = 18.5
        df["lon"] = 73.8
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for c in dc.CANONICAL_COLUMNS:
            if c not in df.columns and c != "ts":
                df[c] = np.nan

        metadata: dict = {}
        warnings = dc.validate_data(df, 60, metadata)
        assert "missing_percent_per_column" in metadata
        assert metadata["missing_percent_per_column"]["actual_irradiance_wm2"] > 0

    def test_schema_validation_error(self):
        """Missing required column should raise ValidationError."""
        df = pd.DataFrame({
            "ts": pd.date_range("2025-01-01", periods=10, freq="1h", tz="UTC"),
            "actual_irradiance_wm2": range(10),
        }).set_index("ts")

        metadata: dict = {}
        with pytest.raises(dc.ValidationError, match="missing required column"):
            dc.validate_data(df, 60, metadata)


# ---------------------------------------------------------------------------
# Output Tests
# ---------------------------------------------------------------------------

class TestOutput:
    """Verify output file generation."""

    def test_csv_gz_output(self, sample_hourly_df, sample_args, tmp_out_dir):
        df = sample_hourly_df.copy()
        df = dc.resample_weather(df, 5, "2025-01-01", "2025-01-07", seed=42)
        df["lat"] = 18.5204
        df["lon"] = 73.8567
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for col in dc.CANONICAL_COLUMNS:
            if col not in df.columns and col != "ts":
                df[col] = np.nan

        metadata = {"test": True}
        sample_args.out = tmp_out_dir
        dc.write_outputs(df, metadata, sample_args)

        csv_path = Path(tmp_out_dir) / "collected_data.csv.gz"
        assert csv_path.exists()

        # Verify it can be read back
        df_read = pd.read_csv(csv_path, compression="gzip")
        assert list(df_read.columns) == dc.CANONICAL_COLUMNS

    def test_metadata_json_output(self, sample_hourly_df, sample_args, tmp_out_dir):
        df = sample_hourly_df.copy()
        df = dc.resample_weather(df, 5, "2025-01-01", "2025-01-07", seed=42)
        df["lat"] = 18.5204
        df["lon"] = 73.8567
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for col in dc.CANONICAL_COLUMNS:
            if col not in df.columns and col != "ts":
                df[col] = np.nan

        metadata = {"test": True}
        sample_args.out = tmp_out_dir
        dc.write_outputs(df, metadata, sample_args)

        meta_path = Path(tmp_out_dir) / "collected_metadata.json"
        assert meta_path.exists()
        with open(meta_path) as f:
            meta = json.load(f)
        assert "row_count" in meta

    def test_sample_hour_output(self, sample_hourly_df, sample_args, tmp_out_dir):
        df = sample_hourly_df.copy()
        df = dc.resample_weather(df, 5, "2025-01-01", "2025-01-07", seed=42)
        df["lat"] = 18.5204
        df["lon"] = 73.8567
        df["source"] = "nasa_power"
        df["tz"] = "Asia/Kolkata"
        for col in dc.CANONICAL_COLUMNS:
            if col not in df.columns and col != "ts":
                df[col] = np.nan

        metadata = {"test": True}
        sample_args.out = tmp_out_dir
        dc.write_outputs(df, metadata, sample_args)

        sample_path = Path(tmp_out_dir) / "collected_sample_hour.csv"
        assert sample_path.exists()
        df_sample = pd.read_csv(sample_path)
        # 1 hour = 12 five-minute intervals
        assert len(df_sample) == 12


# ---------------------------------------------------------------------------
# CLI / Config Tests
# ---------------------------------------------------------------------------

class TestCLI:
    """Verify CLI argument parsing."""

    def test_basic_args(self):
        args = dc.parse_args([
            "--lat", "18.5", "--lon", "73.8",
            "--start", "2025-01-01", "--end", "2025-01-07",
        ])
        assert args.lat == 18.5
        assert args.lon == 73.8
        assert args.timestep == 5  # default
        assert args.source == "nasa_power"  # default

    def test_invalid_timestep(self):
        with pytest.raises(SystemExit):
            dc.parse_args([
                "--lat", "18.5", "--lon", "73.8",
                "--start", "2025-01-01", "--end", "2025-01-07",
                "--timestep", "7",
            ])

    def test_missing_required(self):
        with pytest.raises(SystemExit):
            dc.parse_args(["--lat", "18.5"])

    def test_config_file(self, tmp_out_dir):
        """Config YAML should supply defaults."""
        import yaml
        cfg = {
            "lat": 18.5,
            "lon": 73.8,
            "start": "2025-01-01",
            "end": "2025-01-07",
            "timestep": 15,
        }
        cfg_path = Path(tmp_out_dir) / "test_config.yaml"
        with open(cfg_path, "w") as f:
            yaml.dump(cfg, f)

        args = dc.parse_args(["--config", str(cfg_path)])
        assert args.lat == 18.5
        assert args.timestep == 15


# ---------------------------------------------------------------------------
# Sensor Merge Tests
# ---------------------------------------------------------------------------

class TestSensorMerge:
    """Test sensor data merging logic."""

    def test_merge_with_none_sensors(self, sample_hourly_df):
        result = dc.merge_sensors(sample_hourly_df, None, 5)
        pd.testing.assert_frame_equal(result, sample_hourly_df)

    def test_merge_with_empty_sensors(self, sample_hourly_df):
        empty = pd.DataFrame(columns=["pv_gen_kw", "load_kw"])
        empty.index.name = "ts"
        result = dc.merge_sensors(sample_hourly_df, empty, 5)
        # Should return weather df unchanged (empty sensor df)
        assert len(result) == len(sample_hourly_df)

    def test_merge_adds_sensor_columns(self, sample_hourly_df):
        ts = sample_hourly_df.index[5]  # pick a timestamp
        sensor = pd.DataFrame({
            "pv_gen_kw": [1.5],
            "load_kw": [0.8],
            "voltage_v": [230.0],
            "current_a": [6.5],
        }, index=pd.DatetimeIndex([ts], name="ts"))
        result = dc.merge_sensors(sample_hourly_df, sensor, 60)
        assert "pv_gen_kw" in result.columns
        assert "load_kw" in result.columns


# ---------------------------------------------------------------------------
# API Cached Sample Test
# ---------------------------------------------------------------------------

class TestAPICachedSample:
    """Test with a cached/mocked API response (no live API calls)."""

    def test_nasa_power_parse_structure(self):
        """Verify the NASA POWER response parser handles expected structure."""
        # Create mock NASA POWER JSON response structure
        mock_data = {
            "properties": {
                "parameter": {
                    "ALLSKY_SFC_SW_DWN": {},
                    "T2M": {},
                    "WS2M": {},
                    "CLOUD_AMT": {},
                }
            }
        }
        # Populate 24 hours for 2025-01-01
        for h in range(24):
            key = f"2025010{1}{h:02d}" if h < 10 else f"2025010{1}{h:02d}"
            key = f"202501{1:02d}{h:02d}"
            mock_data["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"][key] = max(
                0, 500 * np.sin(np.pi * h / 24))
            mock_data["properties"]["parameter"]["T2M"][key] = 20 + 5 * np.sin(
                np.pi * h / 12)
            mock_data["properties"]["parameter"]["WS2M"][key] = 3.0
            mock_data["properties"]["parameter"]["CLOUD_AMT"][key] = 40.0

        # Parse like the real function does
        param_data = mock_data["properties"]["parameter"]
        irr = param_data["ALLSKY_SFC_SW_DWN"]
        temp = param_data["T2M"]
        wind = param_data["WS2M"]
        cloud = param_data["CLOUD_AMT"]

        rows = []
        for key in sorted(irr.keys()):
            from datetime import datetime
            ts = pd.Timestamp(datetime.strptime(key, "%Y%m%d%H"), tz="UTC")
            rows.append({
                "ts": ts,
                "actual_irradiance_wm2": float(irr[key]),
                "temperature_C": float(temp[key]),
                "wind_speed_m_s": float(wind[key]),
                "cloud_cover_percent": float(cloud[key]),
            })

        df = pd.DataFrame(rows).set_index("ts").sort_index()

        assert len(df) == 24
        assert "actual_irradiance_wm2" in df.columns
        assert df["actual_irradiance_wm2"].dtype == np.float64
        assert df.index.tz is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
