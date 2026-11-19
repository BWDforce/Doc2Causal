# -*- coding: utf-8 -*-
"""Entity-first recall helpers for D2C_13_2.

The first extraction pass remains relation-oriented.  Before the second pass,
D2C_13_2 builds a high-recall variable inventory, derives evidence-local relation
candidate pairs, and uses that inventory to drive a relation-matrix pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from .normalize import chat_json  # type: ignore
    from .. import api_runtime  # type: ignore
except ImportError:  # script-mode import from run_extract.py
    from normalize import chat_json  # type: ignore
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import api_runtime  # type: ignore

CAUSAL_CUES = (
    "cause", "causes", "caused", "lead", "leads", "led", "result", "results",
    "resulted", "produce", "produces", "produced", "generate", "generates",
    "raise", "raises", "reduce", "reduces", "increase", "increases", "decrease",
    "decreases", "trigger", "triggers", "enable", "enables", "feed", "feeds",
    "drive", "drives", "driven", "determine", "determines", "affect", "affects",
    "depend", "depends", "because", "therefore", "thereby", "when", "if",
    "propagate", "propagates", "propagated", "carry", "carries", "carried",
    "contribute", "contributes", "contributed", "govern", "governs", "governed",
    "influence", "influences", "influenced", "set", "sets", "track", "tracks",
    "tracked", "follow", "follows", "followed", "precede", "precedes", "preceded",
)
ENTITY_TYPES = ("measurement", "state", "component", "condition", "event", "variable")


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(value).casefold()).strip()


def _safe_key(value) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", _clean(value)) or "document"


def inventory_cache_path(args) -> Path | None:
    root = getattr(args, "checkpoint_dir", None)
    key = getattr(args, "current_doc_key", None)
    if not root or not key:
        return None
    return Path(root) / f"{_safe_key(key)}.entity_inventory.json"


def _source_chunks(source_text: str, max_chars: int = 12000, overlap: int = 500) -> list[str]:
    text = _clean(source_text)
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_chars:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap else ""
            current = (tail + " " + sentence).strip()
        else:
            current = (current + " " + sentence).strip()
    if current:
        chunks.append(current.strip())
    return chunks


def _inventory_prompt(source_text: str) -> str:
    return f"""Build a high-recall inventory of causal variables from technical text.

Return ONLY JSON:
{{"entities":[{{"mention":"exact source phrase","canonical_name":"short canonical name","entity_type":"measurement|state|component|condition|event|variable","aliases":["surface variants"],"time_index":"or empty","unit":"or empty","evidence_text":"short exact quote"}}]}}

Rules:
- Enumerate ALL variables, measurements, component states, fault states, conditions,
  events and process parameters that could participate in a causal relation.
- Extract every mention, including synonyms and time/unit variants. Do not merge
  near-synonyms at this stage; later normalization handles identity.
- Do not require a variable to already have an extracted relation.
- Do not invent terms. Every mention and evidence_text must occur in the source.
- Include status variables such as online/offline, enabled/disabled, present/missing,
  selected/unselected, and temporal measurements.
- Return at most 160 entities.

SOURCE:
{source_text}"""


def inventory_from_grounded(grounded_items) -> list[dict]:
    rows: list[dict] = []
    for item in grounded_items or []:
        cls = str(getattr(item, "extraction_class", "") or "")
        attrs = getattr(item, "attributes", None) or {}
        text = _clean(getattr(item, "extraction_text", ""))
        if cls == "causal_variable":
            name = _clean(attrs.get("canonical_name") or text)
            if name:
                rows.append({
                    "mention": text or name,
                    "canonical_name": name,
                    "entity_type": _clean(attrs.get("entity_type") or "variable"),
                    "aliases": [],
                    "time_index": "",
                    "unit": "",
                    "evidence_text": text,
                })
        elif cls == "causal_relation":
            for side in ("cause", "effect"):
                name = _clean(attrs.get(side))
                if name:
                    rows.append({
                        "mention": name,
                        "canonical_name": name,
                        "entity_type": "variable",
                        "aliases": [],
                        "time_index": "",
                        "unit": "",
                        "evidence_text": text,
                    })
    return rows


def _merge_inventory(rows: list[dict], source_text: str, limit: int) -> list[dict]:
    source_key = _norm(source_text)
    merged: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        mention = _clean(row.get("mention"))
        canonical = _clean(row.get("canonical_name") or mention)
        if not canonical:
            continue
        evidence = _clean(row.get("evidence_text"))
        mention_ok = bool(_norm(mention)) and _norm(mention) in source_key
        evidence_ok = bool(_norm(evidence)) and _norm(evidence) in source_key
        if not (mention_ok or evidence_ok):
            continue
        key = _norm(canonical)
        if not key:
            continue
        entity_type = _clean(row.get("entity_type") or "variable").lower()
        if entity_type not in ENTITY_TYPES:
            entity_type = "variable"
        aliases = [_clean(value) for value in (row.get("aliases") or []) if _clean(value)]
        entry = merged.setdefault(key, {
            "mention": mention or canonical,
            "canonical_name": canonical,
            "entity_type": entity_type,
            "aliases": [],
            "time_index": _clean(row.get("time_index")),
            "unit": _clean(row.get("unit")),
            "evidence_text": evidence[:500],
        })
        for alias in [mention, canonical, *aliases]:
            if alias and _norm(alias) != key and alias not in entry["aliases"]:
                entry["aliases"].append(alias)
    result = list(merged.values())[: max(1, int(limit))]
    for index, row in enumerate(result, 1):
        row["inventory_id"] = f"inv_{index:04d}"
    return result


def build_entity_inventory(args, source_text: str, grounded_items=None) -> list[dict]:
    if not getattr(args, "entity_first", True):
        return []
    cache = inventory_cache_path(args)
    if cache and cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8-sig"))
            rows = payload.get("entities") or []
            if rows:
                return rows
        except (OSError, json.JSONDecodeError):
            pass
    rows: list[dict] = []
    llm_error = ""
    max_chunks = max(1, int(getattr(args, "entity_inventory_max_chunks", 6)))
    for chunk in _source_chunks(source_text)[:max_chunks]:
        try:
            with api_runtime.api_slot():
                payload = chat_json(
                    args.api_key,
                    args.base_url,
                    getattr(args, "entity_inventory_model", None) or args.model,
                    float(getattr(args, "entity_inventory_temperature", 0.0)),
                    _inventory_prompt(chunk),
                    retries=2,
                )
            rows.extend(payload.get("entities") or [])
        except Exception as exc:  # noqa: BLE001 - fall back to grounded inventory
            llm_error = f"{type(exc).__name__}: {exc}"
            break
    rows.extend(inventory_from_grounded(grounded_items))
    inventory = _merge_inventory(rows, source_text, int(getattr(args, "entity_inventory_max_items", 200)))
    payload = {
        "source": "grounded_fallback" if llm_error else "llm+grounded",
        "error": llm_error,
        "entity_count": len(inventory),
        "entities": inventory,
    }
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return inventory


def _entry_terms(entry: dict) -> list[str]:
    values = [entry.get("canonical_name"), entry.get("mention"), *(entry.get("aliases") or [])]
    return list(dict.fromkeys(_clean(value) for value in values if _clean(value)))


def _term_position(text: str, term: str) -> int:
    """Locate a surface without matching one-letter names inside ordinary words."""
    needle = _clean(term)
    if not needle:
        return -1
    pattern = r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
    match = re.search(pattern, _clean(text), flags=re.IGNORECASE)
    return match.start() if match else -1


def _has_causal_cue(text: str) -> bool:
    """Match causal cue words on token boundaries, not as substrings."""
    key = _clean(text).casefold()
    if not key:
        return False
    for cue in CAUSAL_CUES:
        needle = re.escape(cue.casefold())
        if re.search(r"(?<![a-z0-9])" + needle + r"(?![a-z0-9])", key):
            return True
    return False


def _pair_sort_key(row: dict) -> tuple:
    """Rank explicit, local, same-sentence candidates without imposing a pair count."""
    return (
        not bool(row.get("causal_cue")),
        int(row.get("window_width") or 99),
        int(row.get("mention_gap") or 9999),
        len(row.get("evidence_text") or ""),
        str(row.get("window_id") or ""),
        str(row.get("source") or ""),
        str(row.get("target") or ""),
    )


def build_relation_pairs(source_text: str, inventory: list[dict], max_pairs: int | None = None) -> list[dict]:
    """Build all de-duplicated, locality-ranked evidence-window candidates.

    There is no corpus-specific global pair cap.  Candidates retain their source
    paragraph so the completion prompt can be split on document structure rather
    than on an arbitrary pair count.  The optional ``max_pairs`` argument is
    retained only for backwards-compatible tests and legacy callers.
    """
    text = str(source_text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    sentences: list[tuple[int, str]] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            sentence = _clean(sentence)
            if sentence:
                sentences.append((paragraph_index, sentence))
    if not sentences or not inventory:
        return []
    windows: list[tuple[str, str, int, str]] = []
    for index, (paragraph_index, sentence) in enumerate(sentences):
        paragraph_id = f"p{paragraph_index + 1}"
        windows.append((sentence, f"{paragraph_id}s{index + 1}", 1, paragraph_id))
        if index + 1 < len(sentences) and sentences[index + 1][0] == paragraph_index:
            windows.append((sentence + " " + sentences[index + 1][1], f"{paragraph_id}s{index + 1}-s{index + 2}", 2, paragraph_id))

    pairs: dict[tuple[str, str], dict] = {}
    for window, window_id, window_width, paragraph_id in windows:
        key = _norm(window)
        present = []
        for entry in inventory:
            located = [(_term_position(window, term), term) for term in _entry_terms(entry)]
            located = [(position, term) for position, term in located if position >= 0]
            if not located:
                continue
            position, surface = min(located, key=lambda row: (row[0], len(row[1])))
            present.append((position, entry, surface))
        present.sort(key=lambda row: row[0])
        causal_cue = _has_causal_cue(window)
        for i, (left_pos, left, left_surface) in enumerate(present):
            for right_pos, right, right_surface in present[i + 1:]:
                if left.get("inventory_id") == right.get("inventory_id"):
                    continue
                left_name = _clean(left.get("canonical_name"))
                right_name = _clean(right.get("canonical_name"))
                if _norm(left_name) == _norm(right_name):
                    continue
                pair_key = tuple(sorted((_norm(left_name), _norm(right_name))))
                mention_gap = abs(left_pos - right_pos)
                candidate = {
                    "source": left_name,
                    "target": right_name,
                    "source_surface": left_surface,
                    "target_surface": right_surface,
                    "window_id": window_id,
                    "paragraph_id": paragraph_id,
                    "window_width": window_width,
                    "mention_gap": mention_gap,
                    "same_sentence": window_width == 1,
                    "evidence_text": window[:700],
                    "causal_cue": causal_cue,
                    "pair_score": round(
                        (4.0 if causal_cue else 0.0)
                        + (2.0 if window_width == 1 else 0.0)
                        + 1.0 / (1.0 + mention_gap / 50.0),
                        4,
                    ),
                }
                previous = pairs.get(pair_key)
                if previous is None or _pair_sort_key(candidate) < _pair_sort_key(previous):
                    pairs[pair_key] = candidate
    ordered = sorted(pairs.values(), key=_pair_sort_key)
    return ordered if max_pairs is None else ordered[: max(1, int(max_pairs))]


def _relation_block_text(window_id: str, evidence: str, rows: list[dict]) -> str:
    lines = [
        f"EVIDENCE BLOCK {window_id} (candidate_count={len(rows)}, chars={len(evidence)})",
        evidence,
        "CANDIDATE PAIRS FOR THIS BLOCK:",
    ]
    for index, pair in enumerate(rows, 1):
        lines.append(
            f"  [{index}] {pair['source']} <-> {pair['target']} | "
            f"cue={'yes' if pair.get('causal_cue') else 'no'} | "
            f"distance={pair.get('mention_gap', '')} | "
            f"surface=({pair['source_surface']} / {pair['target_surface']})"
        )
    return "\n".join(lines)


def _relation_instruction(inventory: list[dict]) -> str:
    inventory_lines = []
    for entry in inventory[:160]:
        aliases = ", ".join(entry.get("aliases") or [])
        inventory_lines.append(
            f"- {entry.get('canonical_name')} | type={entry.get('entity_type')} | aliases={aliases}"
        )
    return f"""

ENTITY-FIRST RELATION-COMPLETION PASS
This pass targets missed variables and missed relations. Use the same extraction classes
and attributes as the main prompt.

First, recover any source phrase that names an inventory entity, including statuses,
measurements, time-indexed values and aliases. Emit a causal_variable extraction for
each distinct source mention even if it has no relation.

Process the evidence blocks below independently. Each candidate pair belongs to exactly
one block; do not combine endpoints from different blocks. Emit a causal_relation only
when the evidence in that block explicitly supports a causal direction. Evaluate both
directions and keep the exact source/target surfaces and any time index stated in the
source. Do not infer domain physics, do not add a relation from co-occurrence alone,
and do not duplicate a pair unless a second mention has distinct evidence.
For cue=yes candidates, do not silently skip a pair when its evidence contains a direct
causal verb or phrase connecting the two surfaces. For cue=no candidates, emit a
relation only when the evidence itself states one explicitly.
You may add another explicit relation outside the candidate list when the source states it.

ENTITY INVENTORY:
{chr(10).join(inventory_lines) if inventory_lines else '- <empty>'}

EVIDENCE WINDOWS:
"""


def build_relation_completion_prompts(base_prompt: str, inventory: list[dict], pairs: list[dict]) -> list[str]:
    """Build one prompt per source paragraph without discarding candidates."""
    grouped: dict[str, dict[tuple[str, str], list[dict]]] = {}
    for pair in pairs:
        paragraph_id = str(pair.get("paragraph_id") or "p1")
        window_id = str(pair.get("window_id") or "unknown")
        evidence = str(pair.get("evidence_text") or "")
        grouped.setdefault(paragraph_id, {}).setdefault((window_id, evidence), []).append(pair)
    if not grouped:
        return [base_prompt + _relation_instruction(inventory) + "- <none>"]
    prompts = []
    def paragraph_key(value: str) -> tuple[int, object]:
        if value.startswith("p") and value[1:].isdigit():
            return (0, int(value[1:]))
        return (1, value)

    for paragraph_id in sorted(grouped, key=paragraph_key):
        blocks = [
            _relation_block_text(window_id, evidence, rows)
            for (window_id, evidence), rows in grouped[paragraph_id].items()
        ]
        prompts.append(base_prompt + _relation_instruction(inventory) + "\n\n".join(blocks))
    return prompts




def build_relation_completion_prompt(base_prompt: str, inventory: list[dict], pairs: list[dict]) -> str:
    """Backward-compatible single-prompt view of the paragraph-batched builder."""
    return build_relation_completion_prompts(base_prompt, inventory, pairs)[0]


def _paragraph_records(source_text: str) -> list[dict]:
    """Return stripped paragraphs with their offsets in the original text."""
    text = str(source_text or "").replace("\r\n", "\n").replace("\r", "\n")
    records: list[dict] = []
    cursor = 0
    paragraph_index = 0
    for part in re.split(r"(\n{2,})", text):
        if not part:
            continue
        if re.fullmatch(r"\n{2,}", part):
            cursor += len(part)
            continue
        stripped = part.strip()
        if stripped:
            leading = len(part) - len(part.lstrip())
            paragraph_index += 1
            records.append({
                "paragraph_id": f"p{paragraph_index}",
                "text": stripped,
                "start": cursor + leading,
            })
        cursor += len(part)
    return records


def _relevant_inventory_for_paragraph(
    paragraph_text: str,
    inventory: list[dict],
    pairs: list[dict],
) -> list[dict]:
    relevant = [
        entry for entry in inventory
        if any(_term_position(paragraph_text, term) >= 0 for term in _entry_terms(entry))
    ]
    if relevant:
        return relevant
    wanted = {
        _norm(row.get("source"))
        for row in pairs
        if row.get("source")
    } | {
        _norm(row.get("target"))
        for row in pairs
        if row.get("target")
    }
    return [
        entry for entry in inventory
        if _norm(entry.get("canonical_name")) in wanted
    ]


def _compact_relation_instruction(inventory: list[dict]) -> str:
    inventory_lines = []
    for entry in inventory[:160]:
        aliases = ", ".join(entry.get("aliases") or [])
        inventory_lines.append(
            f"- {entry.get('canonical_name')} | type={entry.get('entity_type')} | aliases={aliases}"
        )
    return f"""

ENTITY-FIRST RELATION-COMPLETION PASS
The SOURCE PARAGRAPH is the only evidence. The candidate list is a recall
checklist, not evidence, and all candidates belong to this paragraph.

Recover source phrases that name inventory entities, including statuses,
measurements, time-indexed values and aliases. Emit each distinct source
mention once as causal_variable even when it has no relation. Do not emit the
same mention again for every candidate pair.

Evaluate both directions of each candidate pair. Emit causal_relation only
when the source paragraph explicitly supports the direction; keep exact
source/target surfaces and any stated time index. Do not infer domain physics,
do not merge endpoints across evidence windows, and do not add a relation from
co-occurrence alone. For cue=yes candidates, do not silently skip an explicit
causal verb or phrase connecting the surfaces. For cue=no candidates, emit a
relation only when the paragraph states one explicitly. You may add another
explicit relation outside the candidate list when the source states it.

ENTITY INVENTORY FOR THIS PARAGRAPH:
{chr(10).join(inventory_lines) if inventory_lines else "- <empty>"}

CANDIDATE PAIRS FOR THIS PARAGRAPH:
"""


def _compact_candidate_lines(pairs: list[dict]) -> str:
    grouped: dict[str, list[dict]] = {}
    for pair in pairs:
        grouped.setdefault(str(pair.get("window_id") or "unknown"), []).append(pair)
    lines: list[str] = []
    for window_id, rows in grouped.items():
        lines.append(f"WINDOW {window_id}:")
        for index, pair in enumerate(sorted(rows, key=_pair_sort_key), 1):
            lines.append(
                f"  [{index}] {pair.get('source')} <-> {pair.get('target')} | "
                f"cue={'yes' if pair.get('causal_cue') else 'no'} | "
                f"distance={pair.get('mention_gap', '')}"
            )
    return "\n".join(lines) if lines else "- <none>"


def build_relation_completion_batches(
    base_prompt: str,
    inventory: list[dict],
    pairs: list[dict],
    source_text: str,
) -> list[dict]:
    """Build paragraph-local extraction batches without duplicating the document.

    Each returned batch carries the paragraph text and its global character
    offset.  The caller can therefore run LangExtract on one paragraph and
    shift grounded spans back into the original document coordinate system.
    """
    paragraph_map = {
        record["paragraph_id"]: record
        for record in _paragraph_records(source_text)
    }
    grouped: dict[str, list[dict]] = {}
    for pair in pairs:
        paragraph_id = str(pair.get("paragraph_id") or "p1")
        grouped.setdefault(paragraph_id, []).append(pair)

    if not grouped:
        grouped["p1"] = []

    def paragraph_key(value: str) -> tuple[int, object]:
        if value.startswith("p") and value[1:].isdigit():
            return (0, int(value[1:]))
        return (1, value)

    batches: list[dict] = []
    for paragraph_id in sorted(grouped, key=paragraph_key):
        rows = grouped[paragraph_id]
        record = paragraph_map.get(paragraph_id)
        if record is None:
            evidence_parts = []
            for row in rows:
                evidence = str(row.get("evidence_text") or "").strip()
                if evidence and evidence not in evidence_parts:
                    evidence_parts.append(evidence)
            paragraph_text = " ".join(evidence_parts) or str(source_text or "")
            offset = 0
            max_char_buffer = 1800
        else:
            paragraph_text = record["text"]
            offset = int(record["start"])
            max_char_buffer = max(1800, len(paragraph_text))
        relevant_inventory = _relevant_inventory_for_paragraph(paragraph_text, inventory, rows)
        prompt = (
            base_prompt
            + _compact_relation_instruction(relevant_inventory)
            + _compact_candidate_lines(rows)
        )
        batches.append({
            "paragraph_id": paragraph_id,
            "source_text": paragraph_text,
            "offset": offset,
            "max_char_buffer": max_char_buffer,
            "pair_count": len(rows),
            "window_count": len({str(row.get("window_id") or "unknown") for row in rows}),
            "prompt": prompt,
        })
    return batches
def attach_inventory_nodes(graph, inventory: list[dict]) -> int:
    added = 0
    for entry in inventory or []:
        name = _clean(entry.get("canonical_name"))
        if not name or graph.has_node(name):
            continue
        graph.add_node(
            name,
            name=name,
            description=_clean(entry.get("evidence_text")) or name,
            type=_clean(entry.get("entity_type") or "variable"),
            values=_clean(entry.get("unit") or "unspecified"),
            inventory_id=entry.get("inventory_id"),
            inventory_aliases=list(entry.get("aliases") or []),
        )
        added += 1
    return added