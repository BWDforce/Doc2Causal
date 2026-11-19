# -*- coding: utf-8 -*-
"""Single source of truth for D2C_13_2 model roles and endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import read_json

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
ROLES = ("extractor", "verifier")


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def load_config(config_path: str | Path | None = None) -> dict:
    path = resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"D2C_13_2 config not found: {path}")
    payload = read_json(path, {}) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"D2C_13_2 config must be a JSON object: {path}")
    for role in ROLES:
        if role not in payload:
            raise ValueError(f"D2C_13_2 config missing role '{role}': {path}")
        role_config = payload[role]
        for key in ("model", "base_url", "api_key_env"):
            if not role_config.get(key):
                raise ValueError(f"D2C_13_2 config missing {role}.{key}: {path}")
        role_config.setdefault("max_tokens", 200000)
        role_config.setdefault("timeout", 600)
    return payload


def role_config(
    role: str,
    config_path: str | Path | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f"unknown D2C_13_2 model role: {role}")
    payload = load_config(config_path)
    config = dict(payload[role])
    config["config_path"] = str(resolve_config_path(config_path))
    if model:
        config["model"] = model
    if base_url:
        config["base_url"] = base_url
    if api_key_env:
        config["api_key_env"] = api_key_env
    return config

