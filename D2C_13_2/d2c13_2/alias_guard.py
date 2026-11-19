# -*- coding: utf-8 -*-
"""Conservative guards for semantic alias/entity merges.

The guards are intentionally narrow.  They do not decide whether two names
are aliases in general; they only reject a small set of unsafe merges that can
be caused by shared document context:

* temporal qualifiers (before/after/during/when, time points) versus content
  variables;
* bare state/value words such as "level", "elevated" or "condition observed";
* description-only pairs whose entity types are event and non-event.

They are shared by the Lang7 normalizer and the D2C compiler repair pass.
"""
from __future__ import annotations

import re
from typing import Iterable

import networkx as nx

TEMPORAL_TOKENS = {
    "before", "after", "during", "while", "when", "until", "prior", "following",
    "once",
}
GENERIC_ALIASES = {
    "level", "levels", "elevated", "condition", "condition observed",
    "observed condition", "state", "status", "value", "values", "normal",
    "abnormal", "present", "absent", "healthy", "unhealthy", "operational",
    "unchanged", "unspecified", "none",
}
STATE_ALIASES = {
    "elevated", "normal", "abnormal", "present", "absent", "healthy",
    "unhealthy", "operational", "unchanged", "condition observed",
    "observed condition",
}
VARIABLE_ALIAS_TOKENS = {
    "data", "content", "level", "concentration", "value", "measurement",
    "payload", "output", "input", "memory", "speed", "rate", "status",
    "condition", "titre", "titer",
}
EVENT_TYPES = {"event"}
VARIABLE_TYPES = {"variable", "measurement", "component", "state"}


def norm(value: object) -> str:
    text = str(value or "").lower().replace("_", " ").replace(":", " ")
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def tokens(value: object) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(value))


def content_tokens(value: object) -> set[str]:
    stop = {
        "a", "an", "and", "are", "as", "at", "be", "because", "by", "for",
        "from", "has", "have", "in", "into", "is", "it", "of", "on", "or",
        "that", "the", "their", "this", "through", "to", "was", "were",
        "with", "within", "when", "which", "while", "after", "before", "then",
        "than", "under", "over",
    }
    generic = {
        "level", "levels", "status", "state", "condition", "value", "values",
        "reading", "readings", "result", "results", "amount", "concentration",
        "concentrations", "measurement", "measure", "measurements", "titre",
        "titer", "data",
    }
    return {x for x in tokens(value) if x not in stop and x not in generic and len(x) > 1 and not x.isdigit()}


def temporal_tokens(value: object) -> set[str]:
    return set(tokens(value)) & TEMPORAL_TOKENS


def clock_tokens(value: object) -> set[str]:
    return set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", str(value or "")))


def is_generic_alias(value: object) -> bool:
    key = norm(value)
    if key in GENERIC_ALIASES:
        return True
    return bool(re.search(r"\btime point\b", key))


def alias_conflict(canonical: object, alias: object) -> bool:
    """Return True for a known unsafe identity relationship."""
    a, b = norm(canonical), norm(alias)
    if not a or not b or a == b:
        return False
    if is_generic_alias(alias):
        return True
    ta, tb = temporal_tokens(canonical), temporal_tokens(alias)
    if ta != tb:
        # A temporal qualifier is not an entity identity when the remaining
        # content is disjoint.  Keep content-preserving variants such as
        # "Print Processing Duration Before" versus "duration of print processing".
        if ta and tb and ta != tb:
            return True
        if not (content_tokens(canonical) & content_tokens(alias)):
            return True
    if clock_tokens(canonical) and clock_tokens(alias) and clock_tokens(canonical) != clock_tokens(alias):
        return True
    return False


def type_conflict(left_type: object, right_type: object) -> bool:
    left = str(left_type or "").lower()
    right = str(right_type or "").lower()
    if not left or not right:
        return False
    if left in EVENT_TYPES and right in VARIABLE_TYPES:
        return True
    if right in EVENT_TYPES and left in VARIABLE_TYPES:
        return True
    return False


def semantic_alias_veto(
    left: object,
    right: object,
    left_type: object,
    right_type: object,
    channels: Iterable[tuple[str, object]],
) -> str | None:
    """Return a veto reason for a description-only candidate pair."""
    names = {str(item[0]) for item in channels}
    if names != {"desc"}:
        return None
    if alias_conflict(left, right) or alias_conflict(right, left):
        return "semantic_alias_guard"
    if type_conflict(left_type, right_type):
        return "type_conflict_guard"
    return None


def infer_alias_type(alias: object, fallback: object = "variable") -> str:
    text = norm(alias)
    if temporal_tokens(alias) or "time point" in text:
        return "event"
    if text in STATE_ALIASES:
        return "state"
    if "condition" in tokens(alias):
        return "condition"
    if set(tokens(alias)) & VARIABLE_ALIAS_TOKENS:
        return "variable"
    value = str(fallback or "variable").lower()
    return value if value in VARIABLE_TYPES or value in EVENT_TYPES else "variable"


def sanitize_graph_aliases(graph: nx.DiGraph) -> dict:
    """Remove unsafe aliases left in a GML and split endpoints when necessary.

    This is a replay/compiler repair for artifacts produced before the guard was
    introduced.  The original graph is modified in place.
    """
    report = {
        "enabled": True,
        "removed_aliases": [],
        "split_nodes": [],
        "rebound_edges": 0,
    }
    split_by_alias: dict[str, str] = {}

    for node in list(graph.nodes()):
        attrs = graph.nodes[node]
        label = str(attrs.get("name") or node)
        raw_aliases = attrs.get("merged_aliases") or []
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        aliases = [str(a) for a in raw_aliases if str(a).strip()]
        if not aliases:
            continue
        kept: list[str] = []
        for alias in aliases:
            if not alias_conflict(label, alias):
                kept.append(alias)
                continue

            # Rebind only when the graph evidence explicitly used this alias as
            # an endpoint.  Otherwise removing the alias is sufficient.
            matching_surfaces = []
            for u, v, data in list(graph.edges(data=True)):
                cause = data.get("cause")
                effect = data.get("effect")
                if str(u) == str(node) and norm(cause) == norm(alias):
                    matching_surfaces.append(str(cause))
                if str(v) == str(node) and norm(effect) == norm(alias):
                    matching_surfaces.append(str(effect))
            chosen = matching_surfaces[0] if matching_surfaces else alias
            new_label = chosen.strip() or alias.strip()
            if matching_surfaces:
                key = norm(new_label)
                target_label = split_by_alias.get(key)
                if target_label is None and graph.has_node(new_label):
                    target_label = new_label
                if target_label is None:
                    target_label = new_label
                    fallback_type = attrs.get("type") or "variable"
                    graph.add_node(
                        target_label,
                        name=target_label,
                        description=alias,
                        type=infer_alias_type(alias, fallback_type),
                        values=["unspecified"],
                        merged_aliases=[alias],
                    )
                    split_by_alias[key] = target_label
                    report["split_nodes"].append({
                        "from": str(node),
                        "alias": alias,
                        "to": target_label,
                    })
                rebound = 0
                for u, v, data in list(graph.edges(data=True)):
                    new_u, new_v = u, v
                    if str(u) == str(node) and norm(data.get("cause")) == norm(alias):
                        new_u = target_label
                    if str(v) == str(node) and norm(data.get("effect")) == norm(alias):
                        new_v = target_label
                    if new_u != u or new_v != v:
                        copy = dict(data)
                        copy["alias_guard_rebound"] = True
                        copy["alias_guard_alias"] = alias
                        graph.remove_edge(u, v)
                        graph.add_edge(new_u, new_v, **copy)
                        rebound += 1
                report["rebound_edges"] += rebound
            report["removed_aliases"].append({
                "entity": str(node),
                "alias": alias,
                "used_as_endpoint": bool(matching_surfaces),
            })
        attrs["merged_aliases"] = sorted(set(kept), key=lambda x: (x.lower(), x))
    report["removed_count"] = len(report["removed_aliases"])
    report["split_count"] = len(report["split_nodes"])
    return report
