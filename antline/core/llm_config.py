"""LLM configuration and factory supporting OpenAI and Anthropic protocols."""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel


class LLMConfig(BaseModel):
    """LLM configuration for antline assess --auto.

    Supported providers: openai, anthropic.
    API key is read from config first, then falls back to environment variables
    (OPENAI_API_KEY / ANTHROPIC_API_KEY).
    """

    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.2
    timeout: int = 120


def load_llm_config(config_path: Path) -> LLMConfig | None:
    """Load LLM configuration from antline.yml (or any YAML file)."""
    if not config_path.exists():
        return None
    data = yaml.safe_load(config_path.read_text())
    if not data or not isinstance(data, dict):
        return None
    llm_data = data.get("llm")
    if not llm_data:
        return None
    return LLMConfig.model_validate(llm_data)


def _resolve_api_key(config: LLMConfig) -> str:
    """Return API key from config or environment variable."""
    if config.api_key:
        return config.api_key
    env_var = "OPENAI_API_KEY" if config.provider == "openai" else "ANTHROPIC_API_KEY"
    key = os.environ.get(env_var, "")
    if not key:
        raise RuntimeError(
            f"LLM API key not configured. Set {env_var} environment variable, "
            f"pass api_key in antline.yml, or use --env {env_var}=..."
        )
    return key


def _default_base_url(provider: str) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "anthropic":
        return "https://api.anthropic.com/v1"
    raise ValueError(f"Unknown provider: {provider}")


def create_llm_call(config: LLMConfig | None = None) -> Callable[[str], str]:
    """Factory: create an *llm_call* function from LLMConfig.

    Usage::

        config = LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
        llm_call = create_llm_call(config)
        response = llm_call("Write a dbt model SQL...")
    """
    if config is None:
        config = LLMConfig()

    if config.provider not in ("openai", "anthropic"):
        raise ValueError(f"Unknown provider: {config.provider}")

    key = _resolve_api_key(config)
    base_url = config.base_url or _default_base_url(config.provider)

    if config.provider == "openai":
        return _build_openai_call(config, key, base_url)
    return _build_anthropic_call(config, key, base_url)


def _build_openai_call(
    config: LLMConfig,
    key: str,
    base_url: str,
) -> Callable[[str], str]:
    """Return an llm_call backed by OpenAI Chat Completions API."""

    def _call(prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior data engineer. "
                        "Respond ONLY with the requested JSON. No markdown, no explanations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": config.temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]

    return _call


def _build_anthropic_call(
    config: LLMConfig,
    key: str,
    base_url: str,
) -> Callable[[str], str]:
    """Return an llm_call backed by Anthropic Messages API."""

    def _call(prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": config.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
            "system": (
                "You are a senior data engineer. "
                "Respond ONLY with the requested JSON. No markdown, no explanations."
            ),
            "temperature": config.temperature,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/messages",
            data=data,
            headers={
                "x-api-key": key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]

    return _call
