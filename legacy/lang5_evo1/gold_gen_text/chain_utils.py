#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DAG -> topology-preserving chain decomposition.

Given a gold DAG, split it into a set of directed paths ("chains") such that:

  * every edge is covered by at least one chain  (edge cover by paths),
  * no chain edge is fabricated                    (union == original edge set),
  * long-range mechanisms are kept on one chain    (greedy longest-path cover),
  * isolated nodes are returned as single-node chains (never dropped).

This preserves the COMPLETE topological relationships of the gold DAG while
making it easy to hand each chain to a document writer.
"""
from __future__ import annotations

from collections import deque

import networkx as nx


def decompose_into_chains(g: nx.DiGraph) -> list[list]:
    """Return a list of chains (lists of node names) covering every edge.

    Uses a greedy longest-path edge cover: repeatedly extract a longest directed
    path from the not-yet-covered edges. Deterministic for a fixed node order.
    """
    nodes = list(g.nodes())
    remaining = set((u, v) for u, v in g.edges())
    chains = []
    while remaining:
        path = _longest_path(nodes, remaining)
        if len(path) < 2:
            # defensive: shouldn't happen while remaining is non-empty
            break
        for i in range(len(path) - 1):
            remaining.discard((path[i], path[i + 1]))
        chains.append(path)
    for n in nodes:
        if g.degree(n) == 0:
            chains.append([n])
    return chains


def _longest_path(nodes: list, edges: set) -> list:
    adj = {n: [] for n in nodes}
    indeg = {n: 0 for n in nodes}
    for u, v in edges:
        adj[u].append(v)
        indeg[v] += 1

    q = deque(n for n in nodes if indeg[n] == 0)
    topo = []
    while q:
        u = q.popleft()
        topo.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    dist = {n: -1 for n in nodes}
    parent = {n: None for n in nodes}
    for n in topo:
        if dist[n] < 0:
            dist[n] = 0
        for v in adj[n]:
            if dist[n] + 1 > dist[v]:
                dist[v] = dist[n] + 1
                parent[v] = n

    end = max(nodes, key=lambda n: dist[n])
    path = []
    cur = end
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path


def chain_edges(path: list) -> list:
    return [[path[i], path[i + 1]] for i in range(len(path) - 1)]


def verify_cover(g: nx.DiGraph, chains: list[list]) -> None:
    """Assert that chain edges exactly reproduce the original edge set."""
    gold = set((u, v) for u, v in g.edges())
    covered = set()
    for path in chains:
        for i in range(len(path) - 1):
            covered.add((path[i], path[i + 1]))
    assert covered == gold, (
        f"chain cover mismatch: missing={gold - covered}, extra={covered - gold}"
    )
