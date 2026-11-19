# -*- coding: utf-8 -*-
"""Standalone D2C_13_2 document extraction.

The module intentionally does not import lang7 or the upstream Causal
Cartographer discovery agents. It supports a DeepSeek-compatible chat endpoint
and an offline mock/from-extractions path for reproducible development.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .util import as_text, read_json, write_json

PROMPT_VERSION = "D2C_13_2-chain-v1"

SYSTEM_PROMPT = """You extract causal knowledge from engineering documents.
Return only a JSON object. Do not include markdown or prose outside JSON."""

EXTRACTION_PROMPT = """DOCUMENT TITLE: {title}

SOURCE TEXT:
{text}

Extract document-grounded causal knowledge.

Return exactly this JSON shape:
{{
  "entities": [
    {{
      "name": "<short canonical variable or state name>",
      "type": "condition|measurement|event|action|string",
      "description": "<one sentence>",
      "aliases": ["<surface form>", "..."],
      "quote": "<verbatim source quote>",
      "span": "<start:int>:<end:int> or null"
    }}
  ],
  "links": [
    {{
      "cause": "<entity name>",
      "effect": "<entity name>",
      "quote": "<verbatim source quote>",
      "span": "<start:int>:<end:int> or null",
      "sign": "+|-|0",
      "rung": 1,
      "condition": "<regime/condition phrase or empty string>",
      "mechanism": "<one-line causal mechanism>",
      "direct_evidence": true
    }}
  ],
  "chains": [
    {{
      "steps": ["<entity A>", "<entity B>", "<entity C>"],
      "quote": "<verbatim source quote>",
      "span": "<start:int>:<end:int> or null"
    }}
  ]
}}

Rules:
1. Preserve causal steps. If the text states A -> B -> C, do not emit
   A -> C as a direct link unless the text separately states that direct link.
2. A link must be adjacent to an explicit causal cue. Use direct_evidence=true
   only when the cited quote directly supports cause -> effect.
3. Conditional, moderating, or regime-dependent relations must carry a
   non-empty condition. Do not turn a condition into a direct edge.
4. Use short canonical names, but list every surface form used in the text in
   aliases. Do not merge numerically or temporally distinct variables.
5. Spans are character offsets into SOURCE TEXT. If the exact span cannot be
   determined, set span to null but preserve the quote.
6. Do not use external domain knowledge that is not stated in the text.
"""


@dataclass
class Document:
    doc_id: str
    source_file: str
    title: str
    text: str
    path: Path


def load_documents(input_dir: str | Path) -> list[Document]:
    input_dir = Path(input_dir)
    files = sorted(
        p for p in input_dir.glob("*.json")
        if p.name != "provenance.json" and not p.name.startswith("_")
    )
    docs = []
    for index, path in enumerate(files, 1):
        payload = read_json(path, {}) or {}
        title = payload.get("title")
        if isinstance(title, dict):
            title = title.get("eng") or title.get("en") or next(iter(title.values()), "")
        text = as_text(payload.get("summary") or payload.get("text") or "")
        docs.append(Document(f"doc{index}", path.name, as_text(title), text, path))
    return docs


def _strip_json(raw: str) -> str:
    text = as_text(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text


def parse_extraction(raw: str) -> dict:
    payload = json.loads(_strip_json(raw))
    if not isinstance(payload, dict):
        raise ValueError("extraction must be a JSON object")
    payload.setdefault("entities", [])
    payload.setdefault("links", [])
    payload.setdefault("chains", [])
    return payload


def _shift_extraction(payload: dict, offset: int) -> dict:
    if not offset:
        return payload
    for entity in payload.get("entities", []) or []:
        span = as_text(entity.get("span"))
        m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", span)
        if m:
            entity["span"] = f"{int(m.group(1)) + offset}:{int(m.group(2)) + offset}"
    for link in payload.get("links", []) or []:
        span = as_text(link.get("span"))
        m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", span)
        if m:
            link["span"] = f"{int(m.group(1)) + offset}:{int(m.group(2)) + offset}"
    for chain in payload.get("chains", []) or []:
        span = as_text(chain.get("span"))
        m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", span)
        if m:
            chain["span"] = f"{int(m.group(1)) + offset}:{int(m.group(2)) + offset}"
    return payload


def _chunks(text: str, max_chars: int = 6000, overlap: int = 200) -> list[tuple[int, str]]:
    if len(text) <= max_chars:
        return [(0, text)]
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = text.rfind("\n", start + max_chars // 2, end)
            if cut > start:
                end = cut
        chunks.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


class DeepSeekExtractor:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 200000,
        timeout: int = 600,
        max_chars: int = 6000,
        overlap: int = 200,
        dry_run: bool = False,
        raw_dir: str | Path | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_chars = max_chars
        self.overlap = overlap
        self.dry_run = dry_run
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self._call_index = 0
        self._json_mode = True

    def _save_raw(self, status: int, attempt: int, label: str, response_text: str, parsed: dict | None = None) -> str:
        if self.raw_dir is None:
            return ""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._call_index += 1
        path = self.raw_dir / f"call_{self._call_index:04d}.json"
        payload = {
            "label": label,
            "attempt": attempt,
            "status": status,
            "response_text": response_text,
            "parsed": parsed,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def prompt(self, title: str, text: str) -> str:
        return EXTRACTION_PROMPT.format(title=title, text=text)

    def call(self, prompt: str, label: str = "") -> dict:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set; use --from-extractions or --dry-run")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        last_error = None
        last_raw = ""
        for attempt in range(1, 4):
            try:
                request_body = dict(body)
                response = requests.post(self._endpoint(), headers=headers, json=request_body, timeout=self.timeout)
                raw_text = response.text
                last_raw = raw_text
                if response.status_code >= 400:
                    self._save_raw(response.status_code, attempt, label, raw_text)
                    # Some compatible endpoints reject response_format.
                    if response.status_code == 400 and request_body.get("response_format"):
                        self._json_mode = False
                        body.pop("response_format", None)
                        continue
                    raise RuntimeError(f"HTTP {response.status_code}: {raw_text[:800]}")
                try:
                    payload = response.json()
                except Exception as exc:
                    self._save_raw(response.status_code, attempt, label, raw_text)
                    raise RuntimeError(f"response is not JSON: {type(exc).__name__}: {exc}") from exc
                choices = payload.get("choices") or []
                message = (choices[0].get("message") if choices and isinstance(choices[0], dict) else {}) or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(str(part.get("text", part)) if isinstance(part, dict) else str(part) for part in content)
                if not content:
                    content = message.get("reasoning_content")
                raw_path = self._save_raw(response.status_code, attempt, label, raw_text, payload)
                finish_reason = ((choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else None) or "")
                if not content and finish_reason == "length":
                    raise RuntimeError(
                        f"completion token budget exhausted during reasoning "
                        f"(max_tokens={self.max_tokens}, reasoning_content={len(message.get('reasoning_content') or '')} chars); "
                        f"increase max_tokens in D2C_13_2/config.json; raw={raw_path}"
                    )
                if not content:
                    raise RuntimeError(f"empty API content (finish_reason={finish_reason}, raw={raw_path})")
                try:
                    parsed = parse_extraction(content)
                except Exception as exc:
                    if body.get("response_format"):
                        self._json_mode = False
                        body.pop("response_format", None)
                        last_error = exc
                        continue
                    raise
                if raw_path:
                    print(f"[D2C_13_2] API response saved: {raw_path}", flush=True)
                return parsed
            except Exception as exc:  # noqa: BLE001 - retry boundary
                last_error = exc
                if body.get("response_format") and attempt < 3:
                    self._json_mode = False
                    body.pop("response_format", None)
                    continue
                if attempt < 3:
                    time.sleep(2 ** (attempt - 1))
        raise RuntimeError(f"extraction failed: {last_error}; last_response={last_raw[:500]!r}")

    def extract_document(self, doc: Document) -> dict:
        merged = {"entities": [], "links": [], "chains": []}
        for offset, chunk in _chunks(doc.text, self.max_chars, self.overlap):
            payload = self.call(self.prompt(doc.title, chunk), label=f"{doc.doc_id}:{offset}")
            payload = _shift_extraction(payload, offset)
            for key in merged:
                merged[key].extend(payload.get(key, []) or [])
        merged.update({"doc_id": doc.doc_id, "source_file": doc.source_file, "title": doc.title})
        return merged


class MockExtractor:
    """Offline extractor for tests and reproducible dry runs."""

    def __init__(self, payload: dict):
        if isinstance(payload, dict) and "extractions" in payload:
            payload = payload["extractions"]
        self.payload = payload

    def extract_document(self, doc: Document) -> dict:
        value = None
        if isinstance(self.payload, dict):
            value = self.payload.get(doc.source_file) or self.payload.get(doc.doc_id)
        if value is None:
            value = {"entities": [], "links": [], "chains": []}
        result = json.loads(json.dumps(value))
        result.update({"doc_id": doc.doc_id, "source_file": doc.source_file, "title": doc.title})
        return result


def load_from_extractions(path: str | Path) -> dict:
    payload = read_json(Path(path), {}) or {}
    return payload.get("extractions", payload) if isinstance(payload, dict) else {}


def extract_corpus(docs: list[Document], extractor: Any, dry_run_dir: Path | None = None) -> dict:
    results = {}
    for doc in docs:
        results[doc.doc_id] = extractor.extract_document(doc)
    return results

