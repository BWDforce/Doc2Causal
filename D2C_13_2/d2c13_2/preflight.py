# -*- coding: utf-8 -*-
"""Fail-fast API/model access checks for D2C_13_2 experiments."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

from .config import DEFAULT_CONFIG_PATH, load_config
from .util import write_json


def _endpoint(base_url: str) -> str:
    value = str(base_url or "").rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def _message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def _error_info(payload: Any, response_text: str) -> tuple[str, str]:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return (
                str(error.get("code") or error.get("type") or ""),
                str(error.get("message") or "")[:1000],
            )
    return "", response_text[:1000]


def check_api_access(
    config_path: str | Path,
    out_dir: str | Path | None = None,
    timeout: int = 90,
) -> dict:
    """Check that the configured extractor endpoint can return content.

    The check deliberately uses a tiny chat completion rather than only calling
    ``/models``: some gateways list a model but reject inference with
    ``AccessDenied.Unpurchased`` or ``Arrearage``.
    """
    config = load_config(config_path)
    role = dict(config["extractor"])
    key_env = str(role.get("api_key_env") or "DEEPSEEK_API_KEY")
    api_key = os.environ.get(key_env, "")
    endpoint = _endpoint(str(role["base_url"]))
    report: dict[str, Any] = {
        "version": "D2C_13_2",
        "config": str(Path(config_path).resolve()),
        "model": role["model"],
        "base_url": role["base_url"],
        "endpoint": endpoint,
        "api_key_env": key_env,
        "api_key_present": bool(api_key),
        "timeout": max(10, int(timeout)),
        "ok": False,
    }
    if not api_key:
        report["status"] = "missing_api_key"
        report["error"] = f"{key_env} is not set in this process"
        if out_dir is not None:
            write_json(Path(out_dir) / "api_preflight.json", report)
        return report

    body = {
        "model": role["model"],
        "messages": [
            {"role": "system", "content": "Return one short plain-text word."},
            {"role": "user", "content": "Say OK."},
        ],
        "temperature": 0.0,
        "max_tokens": 512,
    }
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=max(10, int(timeout)),
        )
    except Exception as exc:  # noqa: BLE001 - preflight must report transport errors
        report["status"] = "request_error"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if out_dir is not None:
            write_json(Path(out_dir) / "api_preflight.json", report)
        return report

    report["status_code"] = response.status_code
    try:
        payload = response.json()
    except Exception:
        payload = None
    try:
        content = _message_content(payload)
        code, message = _error_info(payload, response.text)
        report["response_code"] = code
        report["response_message"] = message
        report["content_present"] = bool(content)
        if response.status_code == 200 and content:
            report["ok"] = True
            report["status"] = "ok"
        elif response.status_code == 200:
            report["status"] = "empty_content"
            report["error"] = (
                "endpoint returned 200 but no message content; "
                "increase the model output-token budget or inspect the response"
            )
        elif code in {"AccessDenied.Unpurchased", "Arrearage"} or response.status_code in {401, 402, 403}:
            report["status"] = "access_denied"
            report["error"] = message or "model access denied"
        else:
            report["status"] = "http_error"
            report["error"] = message or response.text[:1000]
    except Exception as exc:  # noqa: BLE001 - preserve malformed response diagnostics
        report["status"] = "invalid_response"
        report["error"] = f"{type(exc).__name__}: {exc}"

    if out_dir is not None:
        write_json(Path(out_dir) / "api_preflight.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check D2C_13_2 API/model access")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    result = check_api_access(args.config, args.out_dir, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

