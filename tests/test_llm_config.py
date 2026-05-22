"""Tests for LLM configuration and factory."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antline.core.llm_config import (
    LLMConfig,
    _default_base_url,
    _resolve_api_key,
    create_llm_call,
    load_llm_config,
)


class TestLLMConfig:
    """Test LLMConfig Pydantic model."""

    def test_defaults(self) -> None:
        cfg = LLMConfig()
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"
        assert cfg.api_key == ""
        assert cfg.base_url == ""
        assert cfg.temperature == 0.2
        assert cfg.timeout == 120

    def test_anthropic_config(self) -> None:
        cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-6"

    def test_custom_values(self) -> None:
        cfg = LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://custom.example.com/v1",
            temperature=0.5,
            timeout=60,
        )
        assert cfg.model == "gpt-4o-mini"
        assert cfg.api_key == "sk-test"
        assert cfg.base_url == "https://custom.example.com/v1"
        assert cfg.temperature == 0.5
        assert cfg.timeout == 60


class TestLoadLLMConfig:
    """Test loading LLM config from YAML."""

    def test_load_from_yaml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "antline.yml"
        config_file.write_text(
            "llm:\n"
            "  provider: anthropic\n"
            "  model: claude-sonnet-4-6\n"
            "  api_key: sk-ant-test\n"
            "  temperature: 0.1\n",
            encoding="utf-8",
        )
        cfg = load_llm_config(config_file)
        assert cfg is not None
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-6"
        assert cfg.api_key == "sk-ant-test"
        assert cfg.temperature == 0.1

    def test_missing_llm_section(self, tmp_path: Path) -> None:
        config_file = tmp_path / "antline.yml"
        config_file.write_text(
            "project:\n  name: test\n",
            encoding="utf-8",
        )
        cfg = load_llm_config(config_file)
        assert cfg is None

    def test_file_not_found(self, tmp_path: Path) -> None:
        cfg = load_llm_config(tmp_path / "nonexistent.yml")
        assert cfg is None


class TestResolveApiKey:
    """Test API key resolution logic."""

    def test_from_config(self) -> None:
        cfg = LLMConfig(api_key="sk-config-key")
        assert _resolve_api_key(cfg) == "sk-config-key"

    def test_openai_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = LLMConfig(provider="openai")
        assert _resolve_api_key(cfg) == "sk-env-openai"

    def test_anthropic_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-anthropic")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = LLMConfig(provider="anthropic")
        assert _resolve_api_key(cfg) == "sk-env-anthropic"

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = LLMConfig(provider="openai")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            _resolve_api_key(cfg)


class TestDefaultBaseUrl:
    """Test default base URL resolution."""

    def test_openai(self) -> None:
        assert _default_base_url("openai") == "https://api.openai.com/v1"

    def test_anthropic(self) -> None:
        assert _default_base_url("anthropic") == "https://api.anthropic.com/v1"

    def test_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            _default_base_url("unknown")


class TestCreateLLMCall:
    """Test factory function for both providers."""

    def test_openai_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": '{"result": "ok"}'}}]
        }).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_cm):
            llm_call = create_llm_call(LLMConfig(provider="openai", model="gpt-4o"))
            result = llm_call("test prompt")

        assert result == '{"result": "ok"}'

    def test_anthropic_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "content": [{"type": "text", "text": '{"result": "ok"}'}]
        }).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_cm):
            llm_call = create_llm_call(
                LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
            )
            result = llm_call("test prompt")

        assert result == '{"result": "ok"}'

    def test_uses_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        captured_urls: list[str] = []
        original_urlopen = __import__("urllib.request").request.urlopen

        def capture_urlopen(req, **kwargs):  # type: ignore[no-untyped-def]
            if isinstance(req, str):
                captured_urls.append(req)
            else:
                captured_urls.append(req.full_url)
            return mock_cm

        with patch("urllib.request.urlopen", side_effect=capture_urlopen):
            llm_call = create_llm_call(
                LLMConfig(
                    provider="openai",
                    base_url="https://proxy.example.com/v1",
                )
            )
            llm_call("test")

        assert captured_urls
        assert "proxy.example.com" in captured_urls[0]

    def test_unknown_provider_raises(self) -> None:
        cfg = LLMConfig(provider="openai")  # type: ignore[typeddict-item]
        cfg.provider = "unknown"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="Unknown provider"):
            create_llm_call(cfg)

    def test_backward_compat_create_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """create_openai_llm_call still works via llm_config."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        from antline.core.analysis_skill import create_openai_llm_call

        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_cm):
            llm_call = create_openai_llm_call(model="gpt-4o-mini")
            result = llm_call("test")

        assert result == "ok"
