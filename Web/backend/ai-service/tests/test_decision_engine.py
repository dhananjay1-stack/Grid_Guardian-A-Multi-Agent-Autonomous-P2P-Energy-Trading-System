"""Tests for AI Decision Engine."""
import pytest
import numpy as np
from unittest.mock import MagicMock, patch

# Test fixtures and basic tests


@pytest.fixture
def sample_telemetry():
    """Sample telemetry data for testing."""
    return {
        "soc_kwh": 2.0,
        "soc_capacity_kwh": 4.0,
        "pv_gen_kw": 1.5,
        "load_kw": 0.8,
        "price_signal": 0.15,
        "voltage_v": 230.0,
        "current_a": 3.5,
        "volatility": 0.1,
        "sensor_health": 0.98,
        "grid_risk": 0.05,
    }


@pytest.fixture
def high_risk_telemetry():
    """High-risk telemetry for testing policy selection."""
    return {
        "soc_kwh": 0.5,
        "soc_capacity_kwh": 4.0,
        "pv_gen_kw": 0.2,
        "load_kw": 1.5,
        "price_signal": 0.25,
        "volatility": 0.4,
        "sensor_health": 0.7,
        "grid_risk": 0.5,
    }


class TestPreprocessing:
    """Tests for preprocessing pipeline."""

    def test_build_observation_vector(self, sample_telemetry):
        """Test observation vector is built correctly."""
        from app.core.preprocessing import preprocessor

        result = preprocessor.preprocess(sample_telemetry, normalize=False)

        assert result.observation is not None
        assert len(result.observation) == 18
        assert result.valid or len(result.warnings) > 0

    def test_derived_features(self, sample_telemetry):
        """Test derived features are calculated."""
        from app.core.preprocessing import preprocessor

        result = preprocessor.preprocess(sample_telemetry, normalize=False)

        assert "soc_fraction" in result.derived_features
        assert result.derived_features["soc_fraction"] == 0.5  # 2.0 / 4.0

    def test_value_clipping(self):
        """Test out-of-range values are clipped."""
        from app.core.preprocessing import preprocessor

        telemetry = {
            "soc_kwh": 100.0,  # Way too high
            "voltage_v": -50.0,  # Invalid negative
        }

        result = preprocessor.preprocess(telemetry, normalize=False)

        # Values should be clipped
        assert result.obs_dict["soc_kwh"] <= 20.0
        assert result.obs_dict["voltage_v"] >= 0.0


class TestConditionDetector:
    """Tests for condition detection."""

    def test_stable_condition(self, sample_telemetry):
        """Test stable conditions return DT recommendation."""
        from app.core.condition_detector import condition_detector

        assessment = condition_detector.assess(sample_telemetry)

        assert assessment.recommended_policy == "DT"
        assert assessment.condition.value == "stable"
        assert assessment.risk_score < 0.3

    def test_risky_condition(self, high_risk_telemetry):
        """Test risky conditions return CQL recommendation."""
        from app.core.condition_detector import condition_detector

        assessment = condition_detector.assess(high_risk_telemetry)

        assert assessment.recommended_policy in ["CQL", "BC"]
        assert assessment.risk_score > 0.3

    def test_stress_test_mode(self, sample_telemetry):
        """Test stress test mode forces BC."""
        from app.core.condition_detector import condition_detector

        context = {"stress_test_mode": True}
        assessment = condition_detector.assess(sample_telemetry, context)

        assert assessment.recommended_policy == "BC"
        assert assessment.condition.value == "stress_test"


class TestSafetyShield:
    """Tests for safety shield."""

    def test_soc_low_protection(self):
        """Test discharge is limited when SoC is low."""
        from app.core.safety import safety_shield

        result = safety_shield.check(
            action_idx=4,  # discharge_large
            action_kw=-3.0,
            soc=0.5,  # Very low
            soc_capacity=4.0,
            confidence=0.9,
        )

        assert result.safe_action_kw > -3.0  # Should limit discharge
        assert len(result.violations) > 0

    def test_soc_high_protection(self):
        """Test charging is limited when SoC is high."""
        from app.core.safety import safety_shield

        result = safety_shield.check(
            action_idx=1,  # charge_large
            action_kw=3.0,
            soc=3.9,  # Very high
            soc_capacity=4.0,
            confidence=0.9,
        )

        assert result.safe_action_kw < 3.0  # Should limit charge
        assert len(result.violations) > 0

    def test_low_confidence_blocks(self):
        """Test low confidence triggers safety block."""
        from app.core.safety import safety_shield

        result = safety_shield.check(
            action_idx=5,  # offer_sell
            action_kw=-1.5,
            soc=2.0,
            soc_capacity=4.0,
            confidence=0.1,  # Very low confidence
        )

        assert result.status.value in ["MODIFIED", "BLOCKED"]
        assert len(result.violations) > 0

    def test_approved_action(self):
        """Test valid action is approved."""
        from app.core.safety import safety_shield

        result = safety_shield.check(
            action_idx=2,  # idle
            action_kw=0.0,
            soc=2.0,
            soc_capacity=4.0,
            confidence=0.9,
        )

        assert result.status.value == "APPROVED"
        assert len(result.violations) == 0


class TestPolicyRouter:
    """Tests for policy routing."""

    def test_route_to_available_policy(self, sample_telemetry):
        """Test routing to an available policy."""
        from app.core.router import policy_router

        # Mark DT as available
        policy_router.set_policy_available("DT", True)

        policy, decision = policy_router.route(sample_telemetry)

        assert policy in ["BC", "CQL", "DT"]
        assert decision.selected_policy == policy

    def test_fallback_when_unavailable(self, sample_telemetry):
        """Test fallback when preferred policy is unavailable."""
        from app.core.router import policy_router

        # Mark all as unavailable
        policy_router.set_policy_available("DT", False)
        policy_router.set_policy_available("CQL", False)
        policy_router.set_policy_available("BC", True)

        policy, decision = policy_router.route(sample_telemetry)

        # Should fall back to BC
        assert policy == "BC" or decision.fallback_used


class TestAPI:
    """Tests for API endpoints."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        from fastapi.testclient import TestClient
        from app.main import app

        return TestClient(app)

    def test_health_endpoint(self, client):
        """Test health endpoint returns valid response."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "models_loaded" in data

    def test_model_status_endpoint(self, client):
        """Test model status endpoint."""
        response = client.get("/model/status")

        assert response.status_code == 200
        data = response.json()
        assert "initialized" in data
        assert "policies" in data

    def test_metrics_endpoint(self, client):
        """Test metrics endpoint."""
        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.json()
        assert "inference_count" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
