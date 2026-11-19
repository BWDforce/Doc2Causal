# -*- coding: utf-8 -*-
"""Small shared utilities for D2C_13_2."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .constants import D2C_URN


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha1(value: str, length: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(value).strip().lower()).strip("_")
    return text[:80] or "item"


def norm_text(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\u2013\u2014]", "-", text)
    text = re.sub(r"\b(up|down|present|absent|active|inactive|normal|abnormal|okay|ok|confirmed|valid|invalid)\b", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;()[]{}\"'")


def stable_urn(kind: str, *parts: Any, length: int = 16) -> str:
    raw = "||".join(str(p) for p in parts if p is not None)
    return f"{D2C_URN}{kind}:{sha1(raw, length)}"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = sorted(Path(directory).glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def jsonl_write(path: Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def safe_rel(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except Exception:
        return Path(path).as_posix()

