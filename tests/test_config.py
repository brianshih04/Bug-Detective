"""Tests for backend/config.py — load/save LLM config, defaults, presets."""

import json
from unittest.mock import patch

import pytest

import backend.config as config


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Clear TTL cache before each test to avoid cross-test pollution."""
    config._LLM_CONFIG_CACHE["data"] = None
    config._LLM_CONFIG_CACHE["mtime"] = 0.0
    yield


class TestLoadLlmConfig:
    """Test load_llm_config() with various config file states."""

    def test_returns_default_when_no_file(self, tmp_path):
        """No config file → return defaults."""
        with patch.object(config, "LLM_CONFIG_PATH", tmp_path / "nonexistent.json"):
            cfg = config.load_llm_config()
            assert cfg["provider"] == "ollama"
            assert cfg["model"] != ""
            assert cfg["base_url"] != ""

    def test_loads_valid_config(self, tmp_path):
        """Valid JSON config → merge with defaults."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text(json.dumps({"base_url": "http://custom:8000", "model": "my-model"}))

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            cfg = config.load_llm_config()
            assert cfg["base_url"] == "http://custom:8000"
            assert cfg["model"] == "my-model"
            assert cfg["provider"] == "ollama"  # filled from defaults

    def test_strips_chat_completions_suffix(self, tmp_path):
        """Old format URLs with /v1/chat/completions should be stripped to base."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text(json.dumps({"base_url": "http://localhost:11434/v1/chat/completions"}))

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            cfg = config.load_llm_config()
            assert "chat/completions" not in cfg["base_url"]
            assert cfg["base_url"].startswith("http://localhost:11434")

    def test_strips_chat_completions_no_v1(self, tmp_path):
        """Old format URLs with /chat/completions (no /v1) should be normalized."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text(json.dumps({"base_url": "http://localhost:11434/chat/completions"}))

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            cfg = config.load_llm_config()
            assert cfg["base_url"] == "http://localhost:11434"

    def test_corrupt_json_returns_default(self, tmp_path):
        """Corrupt JSON file → fallback to defaults."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text("{invalid json")

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            cfg = config.load_llm_config()
            assert cfg["provider"] == "ollama"

    def test_ttl_cache(self, tmp_path):
        """load_llm_config should return cached result within TTL."""
        cfg_path = tmp_path / "llm-config.json"
        cfg_path.write_text(json.dumps({"model": "cached-model"}))

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            cfg1 = config.load_llm_config()
            assert cfg1["model"] == "cached-model"

            # Delete file — cache should still return cached value
            cfg_path.unlink()
            cfg2 = config.load_llm_config()
            assert cfg2["model"] == "cached-model"
            assert cfg2 is cfg1  # same object from cache

    def test_save_invalidates_cache(self, tmp_path):
        """save_llm_config should update the cache."""
        cfg_path = tmp_path / "llm-config.json"
        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            config.save_llm_config({"model": "new-model"})
            cfg = config.load_llm_config()
            assert cfg["model"] == "new-model"


class TestSaveLlmConfig:
    """Test save_llm_config()."""

    def test_save_creates_file(self, tmp_path):
        cfg_path = tmp_path / "subdir" / "llm-config.json"

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            config.save_llm_config({"model": "test-model"})
            assert cfg_path.exists()
            saved = json.loads(cfg_path.read_text())
            assert saved["model"] == "test-model"
            assert saved["provider"] == "ollama"  # merged from defaults

    def test_save_overwrites(self, tmp_path):
        cfg_path = tmp_path / "llm-config.json"

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            config.save_llm_config({"model": "first"})
            config.save_llm_config({"model": "second"})
            saved = json.loads(cfg_path.read_text())
            assert saved["model"] == "second"

    def test_save_strips_api_key(self, tmp_path):
        """save_llm_config should write whatever is passed (stripping is done in server.py)."""
        cfg_path = tmp_path / "llm-config.json"

        with patch.object(config, "LLM_CONFIG_PATH", cfg_path):
            config.save_llm_config({"api_key": ""})
            saved = json.loads(cfg_path.read_text())
            assert saved["api_key"] == ""


class TestLLMPresets:
    """Test LLM_PRESETS dictionary structure."""

    @pytest.mark.parametrize("name", ["ollama", "glm5", "openrouter", "minimax", "deepseek"])
    def test_preset_has_required_keys(self, name):
        preset = config.LLM_PRESETS[name]
        for key in ("base_url", "api_key", "model", "provider", "max_tokens", "timeout"):
            assert key in preset, f"Preset '{name}' missing key '{key}'"

    def test_ollama_preset_provider(self):
        assert config.LLM_PRESETS["ollama"]["provider"] == "ollama"
