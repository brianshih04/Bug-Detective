"""Tests for backend/server.py — API endpoints, security headers, API key handling."""
import pytest
import re
from unittest.mock import patch, MagicMock
from pathlib import Path

# Server imports (needs backend on path)
from server import app
from fastapi.testclient import TestClient
from backend.config import _LLM_CONFIG_CACHE


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear TTL cache before each test to avoid cross-test pollution."""
    _LLM_CONFIG_CACHE["data"] = None
    _LLM_CONFIG_CACHE["mtime"] = 0.0
    yield

client = TestClient(app)


class TestHealthEndpoint:
    """GET /api/health"""

    def test_health_returns_ok(self):
        res = client.get("/api/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestLlmConfigEndpoint:
    """GET /api/llm-config — API key must never be exposed."""

    def test_api_key_not_exposed(self, tmp_path):
        """Even when api_key is set on disk, it should NOT appear in response."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text('{"api_key": "***", "model": "test"}')

        with patch("backend.config.LLM_CONFIG_PATH", cfg_path):
            res = client.get("/api/llm-config")
            data = res.json()
            assert data.get("api_key", "") == ""
            assert data.get("api_key_set") is True

    def test_no_api_key_set_flag_false(self, tmp_path):
        """When no api_key on disk, api_key_set should not be True."""
        cfg_path = tmp_path / "nonexistent.json"
        with patch("backend.config.LLM_CONFIG_PATH", cfg_path):
            res = client.get("/api/llm-config")
            data = res.json()
            assert data.get("api_key_set", False) is not True

    def test_no_masked_prefix(self, tmp_path):
        """api_key_masked should NOT appear (replaced by api_key_set boolean)."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text('{"api_key": "sk-1234567890abcdef", "model": "test"}')

        with patch("backend.config.LLM_CONFIG_PATH", cfg_path):
            res = client.get("/api/llm-config")
            data = res.json()
            assert "api_key_masked" not in data
            assert "sk-1234" not in str(data)  # no prefix leaked


class TestPresetsEndpoint:
    """GET /api/llm-presets — API keys must never be exposed."""

    def test_presets_no_api_key_exposed(self):
        res = client.get("/api/llm-presets")
        data = res.json()
        for name, preset in data.items():
            assert preset.get("api_key", "") == "", f"Preset '{name}' leaked api_key"
            assert "api_key_masked" not in preset, f"Preset '{name}' has api_key_masked"
            # api_key_set is acceptable since it's boolean
            if preset.get("api_key_set") is True:
                # verify no key content leaked
                assert len(preset["api_key"]) == 0


class TestStaticFiles:
    """GET /static/... — should serve frontend files."""

    def test_serves_index_html(self):
        res = client.get("/")
        assert res.status_code == 200
        assert "Bug-Detective" in res.text

    def test_serves_js(self):
        res = client.get("/static/app.js")
        assert res.status_code == 200

    def test_serves_css(self):
        res = client.get("/static/style.css")
        assert res.status_code == 200

    def test_404_for_missing_static(self):
        res = client.get("/static/nonexistent.js")
        assert res.status_code == 404


class TestAnalyzeRequestValidation:
    """Test input validation on /api/analyze."""

    def test_log_text_too_large(self):
        """log_text exceeding 2MB should be rejected."""
        huge_log = "x" * 2_000_001
        res = client.post("/api/analyze", json={
            "log_text": huge_log,
            "bug_description": "test",
        })
        assert res.status_code == 422

    def test_log_text_within_limit(self):
        """Normal-sized log_text should pass validation (may fail for other reasons)."""
        res = client.post("/api/analyze", json={
            "log_text": "some log content",
            "bug_description": "test",
        })
        # Should NOT be a 422 validation error
        assert res.status_code != 422


class TestCORSPolicy:
    """CORS should not be wildcard."""

    def test_no_wildcard_cors(self):
        res = client.options(
            "/api/health",
            headers={"Origin": "https://evil-site.com", "Access-Control-Request-Method": "GET"},
        )
        # Should NOT get a wildcard allow-origin
        if "access-control-allow-origin" in res.headers:
            assert res.headers["access-control-allow-origin"] != "*"
