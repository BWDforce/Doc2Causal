# -*- coding: utf-8 -*-
"""Optional LLM edge verifier for D2C_13_2 precision/direction refinement."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import requests

from . import api_runtime
from .config import DEFAULT_CONFIG_PATH, role_config
from .semantics import apply_verdicts, build_verification_requests, materialize_views
from .util import as_text, write_json


SYSTEM = """You are a strict evidence auditor for document-grounded causal claims.
Use exact spans first, then structured assertion/description evidence. Do not
invent evidence outside the supplied record. Return only a JSON object."""


def _strip_json(raw: str) -> str:
    text = as_text(raw).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def _prompt(batch: list[dict]) -> str:
    return """Audit each causal claim against the supplied evidence union.

Each request contains the claim scope, exact spans, structured assertions, packaged evidence windows,
the selected best window, and (for composed claims) the dependency path. A span may support one link of
a multi-step path without entailing the whole direct edge.

Evidence-scope rules:
- An exact source span is the strongest direct evidence.
- A minimal one/two-sentence exact window (best_window_kind=exact_window) is also exact evidence.
- A complete context window (best_window_kind=context, support_scope=context_assisted) MAY entail the
  unified claim when the window jointly establishes both endpoints, the causal relation and any condition.
- For context-assisted entailment, direct_support may be partial, uncertain or not_applicable because no
  single direct span states the whole claim. Record support_scope=context_assisted; do not downgrade a
  complete context window merely because direct_support is not entailed.
- Missing exact span is not evidence of contradiction; inspect the assertion, claim_text and complete
  windows before returning unsupported.

Return for each claim:
- support: entailed | conditional | transitive | partial | uncertain | unsupported | contradicted
- support_scope: exact_span | exact_sentence_window | context_assisted | partial_window | assertion_only | none
- direct_support: entailed | partial | uncertain | unsupported | not_applicable
- assertion_support: entailed | partial | uncertain | unsupported | not_applicable
- path_support: entailed | partial | uncertain | unsupported | not_applicable
- direction: forward | reverse | ambiguous | not_applicable
- direction_support_forward: number from 0 to 1
- direction_support_reverse: number from 0 to 1
- coverage: complete | endpoints_only | one_endpoint | context_only | none
- condition: short condition phrase when support is conditional, else ""
- reason: one short sentence

Definitions:
- support=entailed: the evidence union establishes the complete claim in the requested direction.
- support=conditional: the relation holds only under an explicitly stated condition.
- support=transitive: the claim is valid as a composed path, not as a direct claim.
- support=partial: only part of the relation or one endpoint is established.
- support=uncertain: the supplied evidence is insufficient to decide.
- support=unsupported: supplied evidence does not support the relation.
- support=contradicted: evidence supports the opposite direction or explicitly denies the relation.
- direction is independent of support and must be reported even when support is partial.
- Compare the forward and reverse direction candidates. Do not default to the edge-listed direction.
- For composed claims, use path_support; do not mark the whole claim entailed because one path link is entailed.
- Never mark a claim entailed solely because its endpoints co-occur.

Return exactly:
{"verdicts":[{"edge_id":"...", "support":"entailed", "support_scope":"context_assisted", "direct_support":"not_applicable", "assertion_support":"entailed", "path_support":"not_applicable", "direction":"forward", "direction_support_forward":0.0, "direction_support_reverse":0.0, "coverage":"complete", "condition":"", "reason":""}]}

CANDIDATES:
""" + json.dumps(batch, ensure_ascii=False, indent=2)

class LLMEdgeVerifier:
    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 200000, timeout: int = 1800, batch_size: int = 10, retries: int = 3, retry_backoff: int = 15):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.batch_size = batch_size
        self.retries = max(1, retries)
        self.retry_backoff = max(0, retry_backoff)
        self._telemetry_lock = threading.Lock()
        self.telemetry = {
            "api_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_seconds": 0.0,
            "max_latency_seconds": 0.0,
            "response_bytes": 0,
            "missing_verdict_retries": 0,
        }

    def _endpoint(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"

    def _call(self, batch: list[dict]) -> list[dict]:
        if not self.api_key:
            raise RuntimeError("verifier API key is not set")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(batch)}],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
        }
        started = time.monotonic()
        with api_runtime.api_slot():
            response = requests.post(self._endpoint(), headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=body, timeout=self.timeout)
        elapsed = time.monotonic() - started
        if response.status_code >= 400:
            raise RuntimeError(f"verifier HTTP {response.status_code}: {response.text[:800]}")
        payload = response.json()
        usage = payload.get("usage") or {}
        with self._telemetry_lock:
            self.telemetry["api_calls"] += 1
            self.telemetry["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.telemetry["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            self.telemetry["total_tokens"] += int(usage.get("total_tokens") or 0)
            self.telemetry["latency_seconds"] = round(self.telemetry["latency_seconds"] + elapsed, 3)
            self.telemetry["max_latency_seconds"] = round(max(self.telemetry["max_latency_seconds"], elapsed), 3)
            self.telemetry["response_bytes"] += len(response.content)
        content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not content:
            raise RuntimeError("verifier returned empty content")
        parsed = json.loads(_strip_json(content))
        return parsed.get("verdicts") or []


def _dedupe_verdicts(verdicts: Iterable[dict], allowed_ids: set[str] | None = None) -> list[dict]:
    unique: dict[str, dict] = {}
    for row in verdicts:
        if not isinstance(row, dict):
            continue
        edge_id = as_text(row.get("edge_id"))
        if not edge_id or (allowed_ids is not None and edge_id not in allowed_ids):
            continue
        unique.setdefault(edge_id, row)
    return list(unique.values())


def _call_complete_batch(
    verifier: LLMEdgeVerifier,
    batch: list[dict],
    batch_index: int,
    batch_count: int,
    completion_retries: int,
) -> list[dict]:
    """Request a batch until every supplied edge has exactly one verdict."""
    expected_ids = list(dict.fromkeys(as_text(row.get("edge_id")) for row in batch if as_text(row.get("edge_id"))))
    expected = set(expected_ids)
    collected: list[dict] = []
    pending = list(batch)
    max_attempts = max(1, verifier.retries, completion_retries + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            returned = _dedupe_verdicts(verifier._call(pending), expected)
        except Exception as exc:  # noqa: BLE001 - retry transport and parsing failures
            if attempt >= max_attempts:
                raise RuntimeError(
                    f"verifier batch {batch_index}/{batch_count} failed after {attempt} attempts: {exc}"
                ) from exc
            wait = verifier.retry_backoff * attempt
            print(
                f"[verifier] batch {batch_index}/{batch_count} attempt {attempt}/{max_attempts} failed: "
                f"{type(exc).__name__}: {exc}; retrying in {wait}s",
                flush=True,
            )
            if wait:
                time.sleep(wait)
            continue
        collected = _dedupe_verdicts([*collected, *returned], expected)
        missing = expected - {as_text(row.get("edge_id")) for row in collected}
        if not missing:
            ordered = {as_text(row.get("edge_id")): row for row in collected}
            return [ordered[edge_id] for edge_id in expected_ids]
        telemetry = getattr(verifier, "telemetry", None)
        telemetry_lock = getattr(verifier, "_telemetry_lock", None)
        if telemetry is not None:
            if telemetry_lock is not None:
                with telemetry_lock:
                    telemetry["missing_verdict_retries"] = telemetry.get("missing_verdict_retries", 0) + 1
            else:
                telemetry["missing_verdict_retries"] = telemetry.get("missing_verdict_retries", 0) + 1
        if attempt >= max_attempts:
            raise RuntimeError(
                f"verifier batch {batch_index}/{batch_count} omitted {len(missing)} verdict(s) "
                f"after {attempt} attempts: {sorted(missing)}"
            )
        pending_ids = missing
        pending = [row for row in batch if as_text(row.get("edge_id")) in pending_ids]
        wait = verifier.retry_backoff * attempt
        print(
            f"[verifier] batch {batch_index}/{batch_count} returned {len(returned)}/{len(expected)} verdicts; "
            f"retrying {len(missing)} missing edge(s) after {wait}s",
            flush=True,
        )
        if wait:
            time.sleep(wait)
    raise RuntimeError(f"verifier batch {batch_index}/{batch_count} returned no complete verdict set")

def verify_artifact(
    artifact: dict,
    config_path: str | Path = str(DEFAULT_CONFIG_PATH),
    batch_size: int | None = None,
    verification_policy: str = "balanced",
    checkpoint_path: str | Path | None = None,
) -> dict:
    config = role_config("verifier", config_path)
    api_key = os.environ.get(config["api_key_env"], "")
    batch_size = int(batch_size or config.get("batch_size", 10))
    retries = int(config.get("retries", 3))
    retry_backoff = int(config.get("retry_backoff", 15))
    verifier = LLMEdgeVerifier(
        api_key=api_key, base_url=config["base_url"], model=config["model"],
        max_tokens=int(config.get("max_tokens", 200000)),
        timeout=int(config.get("timeout", 1800)), batch_size=batch_size,
        retries=retries, retry_backoff=retry_backoff,
    )
    completion_retries = int(config.get("completion_retries", 2))
    api_runtime.configure()
    parallel_workers = max(1, api_runtime.verifier_workers(4))
    requests_payload = build_verification_requests(artifact)
    request_ids = list(dict.fromkeys(as_text(row.get("edge_id")) for row in requests_payload if as_text(row.get("edge_id"))))
    request_id_set = set(request_ids)
    fingerprint = hashlib.sha1(json.dumps(requests_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    verdicts = []
    checkpoint_hits = 0
    if checkpoint_path:
        checkpoint_path = Path(checkpoint_path)
        if checkpoint_path.exists():
            try:
                saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if saved.get("requests_fingerprint") == fingerprint:
                    verdicts = _dedupe_verdicts(saved.get("verdicts") or [], request_id_set)
                    checkpoint_hits = len(verdicts)
            except (OSError, json.JSONDecodeError):
                verdicts = []
    done_ids = {as_text(v.get("edge_id")) for v in verdicts if as_text(v.get("edge_id"))}
    pending_requests = [r for r in requests_payload if as_text(r.get("edge_id")) not in done_ids]
    batches = [pending_requests[i:i + batch_size] for i in range(0, len(pending_requests), batch_size)]
    def run_batch(index: int, batch: list[dict]) -> list[dict]:
        return _call_complete_batch(
            verifier,
            batch,
            index,
            len(batches),
            completion_retries,
        )

    if batches and parallel_workers > 1:
        with ThreadPoolExecutor(
            max_workers=min(parallel_workers, len(batches)),
            thread_name_prefix="d2c-verifier",
        ) as pool:
            futures = {
                pool.submit(run_batch, index, batch): index
                for index, batch in enumerate(batches, 1)
            }
            for future in as_completed(futures):
                batch_verdicts = future.result()
                verdicts = _dedupe_verdicts([*verdicts, *batch_verdicts], request_id_set)
                if checkpoint_path:
                    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                    with verifier._telemetry_lock:
                        telemetry = dict(verifier.telemetry)
                    write_json(checkpoint_path, {
                        "requests_fingerprint": fingerprint,
                        "verdicts": verdicts,
                        "telemetry": telemetry,
                    })
    else:
        for index, batch in enumerate(batches, 1):
            batch_verdicts = run_batch(index, batch)
            verdicts = _dedupe_verdicts([*verdicts, *batch_verdicts], request_id_set)
            if checkpoint_path:
                checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                with verifier._telemetry_lock:
                    telemetry = dict(verifier.telemetry)
                write_json(checkpoint_path, {
                    "requests_fingerprint": fingerprint,
                    "verdicts": verdicts,
                    "telemetry": telemetry,
                })
    verdicts = _dedupe_verdicts(verdicts, request_id_set)
    verdict_by_id = {as_text(row.get("edge_id")): row for row in verdicts}
    missing_verdict_ids = sorted(request_id_set - set(verdict_by_id))
    if missing_verdict_ids:
        raise RuntimeError(f"verifier returned incomplete verdict coverage: {missing_verdict_ids}")
    verdicts = [verdict_by_id[edge_id] for edge_id in request_ids if edge_id in verdict_by_id]
    applied = apply_verdicts(artifact, verdicts, direction_policy=verification_policy)
    materialize_views(artifact)
    support_counts = dict(Counter(
        as_text(row.get("support") or row.get("verdict") or "untested").lower()
        for row in verdicts
    ))
    direction_counts = dict(Counter(
        as_text(row.get("direction") or "unspecified").lower()
        for row in verdicts
    ))
    direct_support_counts = dict(Counter(
        as_text(row.get("direct_support") or "untested").lower()
        for row in verdicts
    ))
    assertion_support_counts = dict(Counter(
        as_text(row.get("assertion_support") or "untested").lower()
        for row in verdicts
    ))
    path_support_counts = dict(Counter(
        as_text(row.get("path_support") or "not_applicable").lower()
        for row in verdicts
    ))
    support_scope_counts = dict(Counter(
        as_text(row.get("support_scope") or "unspecified").lower()
        for row in verdicts
    ))
    coverage_counts = dict(Counter(
        as_text(row.get("coverage") or "unknown").lower()
        for row in verdicts
    ))
    artifact.setdefault("provenance", {})["verifier"] = {
        "model": config["model"],
        "base_url": config["base_url"],
        "verification_policy": verification_policy,
        "batches": len(batches),
        "parallel_workers": parallel_workers,
        "requests": len(requests_payload),
        "pending_requests": len(pending_requests),
        "checkpoint_hits": checkpoint_hits,
        "verdict_coverage": round(len(verdicts) / len(request_id_set), 6) if request_id_set else 1.0,
        "missing_verdict_ids": missing_verdict_ids,
        "verdicts": len(verdicts),
        "support_counts": support_counts,
        "direct_support_counts": direct_support_counts,
        "assertion_support_counts": assertion_support_counts,
        "path_support_counts": path_support_counts,
        "direction_counts": direction_counts,
        "support_scope_counts": support_scope_counts,
        "coverage_counts": coverage_counts,
        "demoted": applied.get("demoted", 0),
        "telemetry": verifier.telemetry,
    }
    return {
        "requests": len(requests_payload),
        "pending_requests": len(pending_requests),
        "checkpoint_hits": checkpoint_hits,
        "verdict_coverage": round(len(verdicts) / len(request_id_set), 6) if request_id_set else 1.0,
        "missing_verdict_ids": missing_verdict_ids,
        "batches": len(batches),
        "verdicts": len(verdicts),
        "support_counts": support_counts,
        "direct_support_counts": direct_support_counts,
        "assertion_support_counts": assertion_support_counts,
        "path_support_counts": path_support_counts,
        "direction_counts": direction_counts,
        "support_scope_counts": support_scope_counts,
        "coverage_counts": coverage_counts,
        "demoted": applied.get("demoted", 0),
        "verification_policy": verification_policy,
        "telemetry": verifier.telemetry,
        "records": verdicts,
    }


