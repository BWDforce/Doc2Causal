# -*- coding: utf-8 -*-
"""Self-contained HTML viewer for one D2C_13_2 artifact."""

from __future__ import annotations

import json
import html
from pathlib import Path
from typing import Any

import networkx as nx

from .util import read_json, write_json

VIEWS = {
    "span_core": ("span_core",),
    "evidence_core": ("evidence_core",),
    "evidence_contract_core": ("evidence_contract_core",),
    "soft_core": ("soft_core",),
    "soft_core_lenient": ("soft_core_lenient",),
    "soft_core_balanced": ("soft_core_balanced",),
    "soft_core_precision": ("soft_core_precision",),
    "support_aware_core": ("support_aware_core",),
    "contract_support_aware_core": ("contract_support_aware_core",),
    "contract_verified_core": ("contract_verified_core",),
    "exact_support_core": ("exact_support_core",),
    "context_assisted_core": ("context_assisted_core",),
    "partial_evidence_review": ("partial_evidence_review",),
    "path_support_core": ("path_support_core",),
    "no_evidence_queue": ("no_evidence_queue",),
    "assertion_recovery": ("assertion_recovery",),
    "identity_review": ("identity_review",),
    "condition_review": ("condition_review",),
    "directness_review": ("directness_review",),
    "shortcut_review": ("shortcut_review",),
    "partial_forward_core": ("partial_forward_core",),
    "path_core": ("path_core",),
    "path_review": ("path_review",),
    "direct_core": ("direct_core",),
    "grounded": ("direct_core", "conditional", "regime"),
    "conditional": ("conditional",),
    "regime": ("regime",),
    "transitive_derived": ("transitive_derived",),
    "assertion_core": ("assertion_core",),
    "derived_core": ("derived_core",),
    "candidate": ("candidate",),
    "candidate_high": ("candidate_high",),
    "candidate_medium": ("candidate_medium",),
    "candidate_low": ("candidate_low",),
    "needs_review": ("needs_review",),
    "unsupported_hypothesis": ("unsupported_hypothesis",),
    "hypothesis": ("hypothesis",),
    "rejected": ("rejected",),
}


def _condition_text(artifact: dict, condition_id: str | None) -> str:
    if not condition_id:
        return ""
    for c in artifact.get("conditions", []):
        if c.get("id") == condition_id:
            return c.get("raw") or c.get("normalized") or ""
    return condition_id


def _span_map(artifact: dict) -> dict[str, dict]:
    return {s.get("id"): s for s in artifact.get("spans", [])}


def _assertion_map(artifact: dict) -> dict[str, dict]:
    return {a.get("id"): a for a in artifact.get("assertions", [])}


def _node_payload(artifact: dict) -> dict[str, dict]:
    return {e.get("id"): e for e in artifact.get("entities", [])}


def _edge_payload(artifact: dict) -> list[dict]:
    nodes = _node_payload(artifact)
    spans = _span_map(artifact)
    assertions = _assertion_map(artifact)
    out = []
    for edge in artifact.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        support = []
        for aid in edge.get("support_assertion_ids") or []:
            a = assertions.get(aid, {})
            span = spans.get(a.get("span_id"), {})
            support.append({
                "assertion_id": aid,
                "doc_id": a.get("doc_id"),
                "channel": a.get("channel"),
                "span_id": a.get("span_id"),
                "span": f"{span.get('start')}:{span.get('end')}" if span else "",
                "text": span.get("text") or "",
                "sign": a.get("sign"),
                "rung": a.get("rung"),
            })
        out.append({
            "id": edge.get("id"),
            "source": source,
            "target": target,
            "source_label": (nodes.get(source) or {}).get("label", source),
            "target_label": (nodes.get(target) or {}).get("label", target),
            "kind": edge.get("kind", "direct_core"),
            "confidence": edge.get("confidence", "unknown"),
            "strength": edge.get("strength", "unknown"),
            "semantic_support": edge.get("semantic_support", "untested"),
            "claim_scope": edge.get("claim_scope", "unknown"),
            "evidence_grade": edge.get("evidence_grade", "none"),
            "evidence_completeness": edge.get("evidence_completeness", ""),
            "support_scope": edge.get("support_scope", ""),
            "verifier_support_scope": edge.get("verifier_support_scope", ""),
            "best_window_kind": edge.get("best_window_kind", "none"),
            "best_window_source": edge.get("best_window_source", "none"),
            "evidence_exact_match": bool(edge.get("evidence_exact_match", False)),
            "context_window_complete": bool(edge.get("context_window_complete", False)),
            "evidence_packaging_stage": edge.get("evidence_packaging_stage", ""),
            "condition_grounding": edge.get("condition_grounding", ""),
            "temporal_consistency": edge.get("temporal_consistency", ""),
            "fraction_consistency": edge.get("fraction_consistency", ""),
            "evidence_coverage": edge.get("evidence_coverage") or {},
            "missing_evidence_components": edge.get("missing_evidence_components") or [],
            "evidence_window_count": edge.get("evidence_window_count", 0),
            "evidence_windows": edge.get("evidence_windows") or [],
            "verification_status": edge.get("verification_status", "unverified"),
            "candidate_tier": edge.get("candidate_tier", "none"),
            "candidate_origin": edge.get("candidate_origin", ""),
            "candidate_support_mode": edge.get("candidate_support_mode", ""),
            "view_tags": edge.get("view_tags", []),
            "direction_status": edge.get("direction_status", "unverified"),
            "provenance": edge.get("provenance", ""),
            "condition": _condition_text(artifact, edge.get("condition_id")),
            "description": (edge.get("attributes") or {}).get("description", ""),
            "mechanism": (edge.get("attributes") or {}).get("mechanism", ""),
            "support_docs": edge.get("support_doc_ids") or [],
            "span_count": len(edge.get("span_ids") or []),
            "derivation_count": len(edge.get("derivation_edge_ids") or []),
            "support": support,
        })
    return out


def _layouts(nodes: list[dict], edges: list[dict]) -> dict[str, dict]:
    layouts = {}
    for view, kinds in VIEWS.items():
        if view in {"span_core", "evidence_core", "soft_core", "soft_core_lenient", "soft_core_balanced", "soft_core_precision", "support_aware_core", "exact_support_core", "context_assisted_core", "partial_evidence_review", "path_support_core", "no_evidence_queue", "assertion_recovery", "identity_review", "condition_review", "directness_review", "shortcut_review", "partial_forward_core", "path_core", "path_review", "assertion_core", "derived_core", "candidate_high", "candidate_medium", "candidate_low", "needs_review"}:
            view_edges = [e for e in edges if view in (e.get("view_tags") or [])]
        else:
            view_edges = [e for e in edges if e.get("kind") in kinds]
        used = {e["source"] for e in view_edges} | {e["target"] for e in view_edges}
        graph = nx.DiGraph()
        graph.add_nodes_from(used)
        graph.add_edges_from((e["source"], e["target"]) for e in view_edges)
        if not used:
            layouts[view] = {}
            continue
        if len(used) == 1:
            positions = {next(iter(used)): (0.5, 0.5)}
        else:
            positions = nx.spring_layout(graph, seed=42, k=1.5 / max(1, len(used) ** 0.5), iterations=140)
        if positions:
            xs = [float(pos[0]) for pos in positions.values()]
            ys = [float(pos[1]) for pos in positions.values()]
            minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
            dx = max(maxx - minx, 1e-9)
            dy = max(maxy - miny, 1e-9)
            positions = {
                node: (0.08 + 0.84 * (float(pos[0]) - minx) / dx, 0.08 + 0.84 * (float(pos[1]) - miny) / dy)
                for node, pos in positions.items()
            }
        layouts[view] = {node: [round(float(pos[0]), 5), round(float(pos[1]), 5)] for node, pos in positions.items()}
    return layouts


def build_payload(artifact: dict, validation: dict | None = None, metrics: dict | None = None, source_overrides: dict | None = None) -> dict:
    nodes = _node_payload(artifact)
    edges = _edge_payload(artifact)
    counts = {
        name: len(artifact.get("views", {}).get(name, []) or [])
        if name in artifact.get("views", {}) else sum(1 for e in edges if name in (e.get("view_tags") or []))
        for name in VIEWS
    }
    counts["grounded"] = sum(1 for e in edges if e["kind"] in VIEWS["grounded"])
    source = dict(artifact.get("source", {}) or {})
    if source_overrides:
        source.update({key: value for key, value in source_overrides.items() if value not in (None, "")})
    payload = {
        "version": artifact.get("version", "D2C_13_2"),
        "artifact_id": artifact.get("artifact_id", ""),
        "source": source,
        "provenance": artifact.get("provenance", {}),
        "nodes": [
            {
                "id": eid,
                "label": e.get("label", eid),
                "type": e.get("entity_type", ""),
                "description": e.get("description", ""),
                "aliases": e.get("aliases", []),
                "values": e.get("values", []),
            }
            for eid, e in sorted(nodes.items(), key=lambda kv: kv[1].get("label", ""))
        ],
        "edges": edges,
        "views": {
            view: (
                [row.get("edge_id") for row in artifact.get("views", {}).get(view, [])]
                if view in artifact.get("views", {}) else
                [e["id"] for e in edges if view in (e.get("view_tags") or [])]
            )
            for view, kinds in VIEWS.items()
        },
        "layouts": _layouts(list(nodes.values()), edges),
        "counts": counts,
        "validation": validation or {},
        "metrics": metrics or {},
    }
    return payload


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>D2C_13_2 Causal Audit Viewer</title>
<style>
:root{--bg:#0b1220;--panel:#111b2e;--panel2:#16243a;--ink:#e8eef8;--muted:#8da1bb;--line:#263952;--core:#36d399;--cond:#f4b942;--regime:#60a5fa;--trans:#a78bfa;--hyp:#94a3b8;--rej:#fb7185;--accent:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,Segoe UI,Arial,sans-serif}
.app{display:grid;grid-template-rows:auto 1fr;height:100vh;overflow:hidden}
header{padding:18px 24px;border-bottom:1px solid var(--line);background:linear-gradient(120deg,#0f1b31,#111b2e 55%,#15233a)}
h1{margin:0;font-size:22px;letter-spacing:.2px}h1 span{color:var(--accent)}
.sub{color:var(--muted);margin-top:4px;font-size:12px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.chip{border:1px solid var(--line);background:#0e1828;border-radius:999px;padding:4px 9px;color:var(--muted);font-size:12px}
.chip.good{color:var(--core);border-color:#28664d}.chip.bad{color:var(--rej);border-color:#6e2b3a}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:12px}.stat{background:#0e1828;border:1px solid var(--line);border-radius:8px;padding:8px 10px}.stat b{display:block;font-size:17px}.stat span{color:var(--muted);font-size:11px}
.main{display:grid;grid-template-columns:210px minmax(0,1fr) 330px;min-height:0}
aside{background:var(--panel);border-right:1px solid var(--line);padding:14px;overflow:auto}aside.right{border-right:0;border-left:1px solid var(--line)}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:4px 0 10px}
.viewbtn{width:100%;display:flex;justify-content:space-between;align-items:center;background:#0d1727;border:1px solid var(--line);color:var(--ink);border-radius:7px;padding:7px 8px;margin:4px 0;cursor:pointer;text-align:left}.viewbtn:hover{border-color:var(--accent)}.viewbtn.active{border-color:var(--accent);background:#14243d}.viewbtn small{color:var(--muted)}
input[type=search]{width:100%;background:#0d1727;border:1px solid var(--line);border-radius:6px;color:var(--ink);padding:7px 8px;outline:none}input[type=search]:focus{border-color:var(--accent)}
.legend{display:grid;gap:5px;margin-top:10px;color:var(--muted);font-size:12px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.graphwrap{position:relative;min-width:0;min-height:0;background:#09111f;overflow:hidden}.graph{width:100%;height:100%;display:block;cursor:grab;touch-action:none;user-select:none}.graph.dragging{cursor:grabbing}
.toolbar{position:absolute;z-index:3;top:10px;left:10px;display:flex;gap:6px}.toolbar button{border:1px solid var(--line);background:#111b2e;color:var(--ink);border-radius:6px;padding:5px 9px;cursor:pointer}.toolbar button:hover{border-color:var(--accent)}
.help{position:absolute;z-index:2;left:12px;bottom:10px;color:var(--muted);font-size:11px;background:rgba(11,18,32,.78);padding:5px 8px;border-radius:5px}
.edge{stroke-width:1.35;fill:none;opacity:.78}.edge.direct_core{stroke:var(--core)}.edge.conditional{stroke:var(--cond)}.edge.regime{stroke:var(--regime)}.edge.transitive_derived{stroke:var(--trans);stroke-dasharray:7 4}.edge.candidate{stroke:#f59e0b;stroke-dasharray:5 4}.edge.unsupported_hypothesis,.edge.hypothesis{stroke:var(--hyp);stroke-dasharray:3 4}.edge.rejected{stroke:var(--rej);stroke-dasharray:2 5;opacity:.55}
.node{stroke:#dbeafe;stroke-width:1;cursor:pointer}.node:hover{stroke:#fff;stroke-width:2}.nlabel{fill:#dce8f8;font-size:7.6px;paint-order:stroke;stroke:#09111f;stroke-width:2.5px;pointer-events:none}.dim{opacity:.12}.hidden{display:none}
.inspector{font-size:12px}.empty{color:var(--muted);line-height:1.7}.card{border:1px solid var(--line);background:#0d1727;border-radius:8px;padding:9px;margin:8px 0}.card b{color:var(--ink)}.kv{display:grid;grid-template-columns:72px 1fr;gap:4px 8px;margin-top:7px}.kv span:nth-child(odd){color:var(--muted)}.quote{border-left:2px solid var(--accent);padding-left:8px;color:#c9d8eb;margin:7px 0}.badge{display:inline-block;border-radius:999px;padding:2px 7px;font-size:11px;border:1px solid currentColor;margin:1px}.b-direct_core{color:var(--core)}.b-conditional{color:var(--cond)}.b-regime{color:var(--regime)}.b-transitive_derived{color:var(--trans)}.b-hypothesis{color:var(--hyp)}.b-rejected{color:var(--rej)}.legend .marker{display:inline-block;width:14px;margin-right:5px;text-align:center;font-size:12px}.b-candidate{color:#f59e0b}.b-unsupported_hypothesis{color:#94a3b8}
details summary{cursor:pointer;color:var(--accent)}.muted{color:var(--muted)}code{font-size:11px;color:#bfe3ff;word-break:break-all}
@media(max-width:1000px){.main{grid-template-columns:180px minmax(0,1fr)}aside.right{display:none}.stats{grid-template-columns:repeat(3,1fr)}}
</style>
</head>
<body>
<div class="app">
<header>
  <h1>D2C_13_2 <span>Causal Audit Viewer</span></h1>
  <div class="sub" id="meta"></div>
  <div class="chips" id="chips"></div>
  <div class="stats" id="stats"></div>
</header>
<div class="main">
  <aside>
    <h2>Graph View</h2>
    <div id="views"></div>
    <h2 style="margin-top:18px">Search</h2>
    <input id="search" type="search" placeholder="Node or edge label...">
    <div class="legend">
      <div><i class="marker" style="color:var(--core)">&#9654;</i>direct core</div>
      <div><i class="marker" style="color:var(--cond)">&#9670;</i>conditional</div>
      <div><i class="marker" style="color:var(--regime)">&#9632;</i>regime</div>
      <div><i class="marker" style="color:var(--trans)">&#9651;</i>transitive derived</div>
      <div><i class="marker" style="color:#f59e0b">&#9675;</i>candidate</div>
      <div><i class="marker" style="color:var(--hyp)">&#9671;</i>unsupported / hypothesis</div>
      <div><i class="marker" style="color:var(--rej)">&#10005;</i>rejected / audit only</div>
    </div>
  </aside>
  <section class="graphwrap">
    <div class="toolbar"><button id="zin" title="Zoom in">+</button><button id="zout" title="Zoom out">-</button><button id="reset">Reset</button><button id="labels">Labels on</button></div>
    <svg id="graph" class="graph" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid meet">
      <defs>
        <marker id="arrow-direct_core" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,0 L8,4 L0,8 z" fill="#36d399"/></marker>
        <marker id="arrow-conditional" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,4 L4,0 L8,4 L4,8 z" fill="#f4b942"/></marker>
        <marker id="arrow-regime" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><rect x="1" y="1" width="6" height="6" fill="#60a5fa"/></marker>
        <marker id="arrow-transitive_derived" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,0 L8,4 L0,8 z" fill="#09111f" stroke="#a78bfa" stroke-width="1.4"/></marker>
        <marker id="arrow-candidate" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><circle cx="4" cy="4" r="3" fill="#09111f" stroke="#f59e0b" stroke-width="1.5"/></marker>
        <marker id="arrow-unsupported_hypothesis" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,4 L4,0 L8,4 L4,8 z" fill="#09111f" stroke="#94a3b8" stroke-width="1.4"/></marker>
        <marker id="arrow-hypothesis" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,4 L4,0 L8,4 L4,8 z" fill="#09111f" stroke="#94a3b8" stroke-width="1.4"/></marker>
        <marker id="arrow-rejected" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M1,1 L7,7 M7,1 L1,7" stroke="#fb7185" stroke-width="1.6"/></marker>
      </defs>
      <g id="viewport"><g id="edges"></g><g id="nodes"></g></g>
    </svg>
    <div class="help">Drag anywhere to pan &middot; Wheel to zoom at cursor &middot; Click a node or edge to inspect &middot; Reset restores the initial view</div>
  </section>
  <aside class="right inspector" id="inspector"><div class="empty">Select a node or edge to inspect support documents, spans, condition, mechanism and provenance.</div></aside>
</div>
</div>
<script>
const DATA = __D2C1_DATA__;
const NS='http://www.w3.org/2000/svg';
let currentView='soft_core_balanced', selected=null, scale=1, tx=0, ty=0;
let dragging=false, dragPointerId=null, dragCaptured=false, dragStartX=0, dragStartY=0, dragOriginX=0, dragOriginY=0, dragMoved=false, suppressClick=false;
let labelsOn = true;
const $=id=>document.getElementById(id);
const nodeMap=new Map(DATA.nodes.map(n=>[n.id,n]));
const edgeMap=new Map(DATA.edges.map(e=>[e.id,e]));
const kindColor={direct_core:'#36d399',conditional:'#f4b942',regime:'#60a5fa',transitive_derived:'#a78bfa',candidate:'#f59e0b',unsupported_hypothesis:'#64748b',hypothesis:'#94a3b8',rejected:'#fb7185'};
const kindLabel={direct_core:'direct_core',conditional:'conditional',regime:'regime',transitive_derived:'transitive_derived',candidate:'candidate',unsupported_hypothesis:'unsupported_hypothesis',hypothesis:'hypothesis',rejected:'rejected'};
const viewLabel={soft_core_balanced:'Soft core (balanced)',support_aware_core:'Support-aware core',direct_core:'Direct core',candidate:'Candidate edges',needs_review:'Needs review'};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function shortLabel(s,n=42){const text=String(s??'');return text.length>n?text.slice(0,n-3)+'...':text;}
function meta(){const s=DATA.source||{}; $('meta').innerHTML=`corpus <b>${esc(s.corpus||'unknown')}</b> &middot; pipeline <b>${esc(s.pipeline||s.method||s.extractor||'D2C_13_2')}</b> &middot; run <b>${esc(s.run||'unknown')}</b>${s.source_run!==undefined&&s.source_run!==null&&String(s.source_run)!==String(s.run)?` &middot; source run <b>${esc(s.source_run)}</b>`:''} &middot; artifact <code>${esc(DATA.artifact_id)}</code>`;}
function chips(){const v=DATA.validation||{}; const good=v.valid===true; const sh=(v.shacl||{}).conforms; $('chips').innerHTML=`<span class="chip ${good?'good':'bad'}">validation ${good?'valid':'invalid'}</span><span class="chip ${sh===true?'good':(sh===false?'bad':'')}">SHACL ${sh===true?'conforms':(sh===false?'violations':'n/a')}</span><span class="chip">${DATA.nodes.length} entities</span><span class="chip">${DATA.edges.length} typed edges</span>`;}
function stats(){const c=DATA.counts||{}; const items=[['soft_core_balanced',c.soft_core_balanced||0],['support_aware_core',c.support_aware_core||0],['direct_core',c.direct_core||0],['candidate',c.candidate||0],['needs_review',c.needs_review||0]]; $('stats').innerHTML=items.map(([k,n])=>`<div class="stat"><b>${n}</b><span>${viewLabel[k]||k}</span></div>`).join('');}
function views(){const order=['soft_core_balanced','support_aware_core','direct_core','candidate','needs_review']; $('views').innerHTML=order.map(v=>`<button class="viewbtn ${v===currentView?'active':''}" data-view="${v}"><span>${viewLabel[v]||v}</span><small>${DATA.views[v]?.length||0}</small></button>`).join(''); document.querySelectorAll('.viewbtn').forEach(b=>b.onclick=()=>{currentView=b.dataset.view; views(); render();});}
function render(){
  const svg=$('graph'), edgesG=$('edges'), nodesG=$('nodes'); edgesG.innerHTML=''; nodesG.innerHTML='';
  const ids=DATA.views[currentView]||[]; const edges=ids.map(id=>edgeMap.get(id)).filter(Boolean); const used=new Set();
  edges.forEach(e=>{used.add(e.source);used.add(e.target);});
  const pos=DATA.layouts[currentView]||{};
  const byId=new Map(DATA.nodes.map(n=>[n.id,n]));
  const edgeEls=[];
  edges.forEach(e=>{
    const a=pos[e.source], b=pos[e.target]; if(!a||!b) return;
    const x1=a[0]*1000+100,y1=a[1]*650+75,x2=b[0]*1000+100,y2=b[1]*650+75;
    const line=document.createElementNS(NS,'line'); line.setAttribute('x1',x1);line.setAttribute('y1',y1);line.setAttribute('x2',x2);line.setAttribute('y2',y2);line.setAttribute('class','edge '+e.kind);line.setAttribute('marker-end',`url(#arrow-${e.kind})`);line.style.pointerEvents='stroke';line.style.strokeWidth='1.5';
    line.onclick=ev=>{ev.stopPropagation();selectEdge(e);}; line.onmouseenter=()=>{line.style.strokeWidth='3.5';}; line.onmouseleave=()=>{line.style.strokeWidth='1.5';}; edgesG.appendChild(line);edgeEls.push([line,e]);
  });
  DATA.nodes.filter(n=>used.has(n.id)).forEach(n=>{
    const p=pos[n.id]; if(!p) return; const x=p[0]*1000+100,y=p[1]*650+75;
    const g=document.createElementNS(NS,'g');
    const c=document.createElementNS(NS,'circle'); c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',currentView==='hypothesis'?4:5);c.setAttribute('class','node');c.setAttribute('fill',colorForType(n.type));g.appendChild(c);
    const t=document.createElementNS(NS,'text');const labelRight=x<850;t.setAttribute('x',x+(labelRight?7:-7));t.setAttribute('y',y+3);t.setAttribute('text-anchor',labelRight?'start':'end');t.setAttribute('class','nlabel');t.textContent=shortLabel(n.label);if(!labelsOn)t.style.display='none';g.appendChild(t);
    const title=document.createElementNS(NS,'title');title.textContent=`${n.label}${n.type?' ('+n.type+')':''}`;g.appendChild(title);
    g.onclick=ev=>{ev.stopPropagation();selectNode(n);}; nodesG.appendChild(g);
  });
  applyTransform();
}
function colorForType(t){const colors={condition:'#38bdf8',measurement:'#f59e0b',event:'#fb7185',action:'#a78bfa',string:'#94a3b8'};return colors[t]||'#64748b';}
function applyTransform(){$('viewport').setAttribute('transform',`translate(${tx},${ty}) scale(${scale})`);}
function clientToSvg(evt){const svg=$('graph');const ctm=svg.getScreenCTM();if(!ctm)return{x:evt.clientX,y:evt.clientY};const p=svg.createSVGPoint();p.x=evt.clientX;p.y=evt.clientY;const q=p.matrixTransform(ctm.inverse());return{x:q.x,y:q.y};}
function zoomAt(point,factor){const next=Math.min(6,Math.max(.2,scale*factor));if(Math.abs(next-scale)<1e-6)return;const ratio=next/scale;tx=point.x-(point.x-tx)*ratio;ty=point.y-(point.y-ty)*ratio;scale=next;applyTransform();}
function zoomCenter(factor){const rect=$('graph').getBoundingClientRect();zoomAt(clientToSvg({clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}),factor);}
function selectEdge(e){
  selected={type:'edge',id:e.id}; highlightEdge(e);
  const support=(e.support||[]).map(s=>`<div class="card"><b>${esc(s.doc_id||'')}</b> <span class="muted">${esc(s.channel||'')}</span><div class="quote">${esc(s.text||'(no span text)')}</div><div class="muted">${esc(s.span||'')}</div></div>`).join('')||'<div class="empty">No assertion support.</div>';
  $('inspector').innerHTML=`<h2>Edge Detail</h2><div class="card"><div><span class="badge b-${esc(e.kind)}">${esc(kindLabel[e.kind]||e.kind)}</span></div><div class="kv"><span>source</span><b>${esc(e.source_label)}</b><span>target</span><b>${esc(e.target_label)}</b><span>confidence</span>${esc(e.confidence)}<span>support</span>${esc(e.semantic_support)}<span>claim</span>${esc(e.claim_scope)}<span>evidence</span>${esc(e.evidence_grade)}<span>verification</span>${esc(e.verification_status)}<span>candidate tier</span>${esc(e.candidate_tier)}<span>candidate origin</span>${esc(e.candidate_origin)}<span>direction</span>${esc(e.direction_status)}<span>provenance</span>${esc(e.provenance)}<span>documents</span>${esc((e.support_docs||[]).join(', ')||'none')}<span>condition</span>${esc(e.condition||'none')}</div><p>${esc(e.description||'')}</p><p class="muted">${esc(e.mechanism||'')}</p></div><details open><summary>Supporting spans (${(e.support||[]).length})</summary>${support}</details>`;
}
function selectNode(n){
  const incident=DATA.edges.filter(e=>e.source===n.id||e.target===n.id);
  $('inspector').innerHTML=`<h2>Entity Detail</h2><div class="card"><b>${esc(n.label)}</b><div class="muted">${esc(n.type||'')}</div><p>${esc(n.description||'')}</p><div class="kv"><span>ID</span><code>${esc(n.id)}</code><span>aliases</span>${esc((n.aliases||[]).join(', ')||'none')}<span>incident</span>${incident.length} edges</div></div><h2>Incident Edges</h2>${incident.map(e=>`<div class="card" style="cursor:pointer" onclick="selectEdgeById('${e.id}')"><span class="badge b-${esc(e.kind)}">${esc(kindLabel[e.kind]||e.kind)}</span><div>${esc(e.source_label)} &rarr; ${esc(e.target_label)}</div></div>`).join('')}`;
}
function highlightEdge(e){document.querySelectorAll('.edge').forEach(x=>x.style.opacity='.18');const els=[...document.querySelectorAll('.edge')];const idx=(DATA.views[currentView]||[]).indexOf(e.id); /* edge order follows filtered ids */ if(idx>=0&&els[idx]){els[idx].style.opacity='1';els[idx].style.strokeWidth='4';}}
function selectEdgeById(id){const e=edgeMap.get(id);if(e){selectEdge(e);}}
$('search').oninput=e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('#nodes g').forEach(g=>{const t=g.querySelector('text')?.textContent.toLowerCase()||'';g.classList.toggle('dim',q&&!t.includes(q));});document.querySelectorAll('.edge').forEach(x=>x.classList.toggle('dim',!!q));};
$('zin').onclick=()=>zoomCenter(1.2);$('zout').onclick=()=>zoomCenter(1/1.2);$('reset').onclick=()=>{scale=1;tx=0;ty=0;applyTransform();};$('labels').onclick=()=>{labelsOn=!labelsOn;$('labels').textContent=labelsOn?'Labels on':'Labels off';render();};
$('graph').addEventListener('pointerdown',ev=>{if(ev.button!==0)return;dragging=true;dragCaptured=false;dragPointerId=ev.pointerId;dragStartX=ev.clientX;dragStartY=ev.clientY;dragOriginX=tx;dragOriginY=ty;dragMoved=false;$('graph').classList.add('dragging');});
window.addEventListener('pointermove',ev=>{if(!dragging||ev.pointerId!==dragPointerId)return;const dx=ev.clientX-dragStartX,dy=ev.clientY-dragStartY;if(Math.hypot(dx,dy)>3){if(!dragMoved){dragMoved=true;try{$('graph').setPointerCapture(ev.pointerId);dragCaptured=true;}catch(_){}}}const start=clientToSvg({clientX:dragStartX,clientY:dragStartY});const current=clientToSvg({clientX:dragStartX+dx,clientY:dragStartY+dy});tx=dragOriginX+(current.x-start.x);ty=dragOriginY+(current.y-start.y);applyTransform();ev.preventDefault();});
function endPan(ev){if(!dragging||ev.pointerId!==dragPointerId)return;dragging=false;dragPointerId=null;$('graph').classList.remove('dragging');if(dragCaptured){try{$('graph').releasePointerCapture(ev.pointerId);}catch(_){}}dragCaptured=false;if(dragMoved){suppressClick=true;setTimeout(()=>{suppressClick=false;},0);}}
window.addEventListener('pointerup',endPan);window.addEventListener('pointercancel',endPan);
$('graph').addEventListener('click',ev=>{if(suppressClick){ev.preventDefault();ev.stopPropagation();suppressClick=false;}},true);
$('graph').addEventListener('wheel',ev=>{ev.preventDefault();zoomAt(clientToSvg(ev),ev.deltaY<0?1.12:1/1.12);},{passive:false});
meta();chips();stats();views();$('labels').textContent=labelsOn?'Labels on':'Labels off';render();
</script>
</body>
</html>'''


def write_viewer(artifact: dict, output_path: str | Path, validation: dict | None = None, metrics: dict | None = None, source_overrides: dict | None = None) -> Path:
    if not HTML_TEMPLATE.isascii() or "\ufffd" in HTML_TEMPLATE:
        raise RuntimeError("Viewer template must remain ASCII-clean to prevent encoding corruption")

    payload = build_payload(artifact, validation=validation, metrics=metrics, source_overrides=source_overrides)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    content = HTML_TEMPLATE.replace("__D2C1_DATA__", data)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def write_viewer_from_file(artifact_path: str | Path, output_path: str | Path, validation_path: str | Path | None = None, metrics_path: str | Path | None = None) -> Path:
    artifact_path = Path(artifact_path)
    artifact = read_json(artifact_path, {}) or {}
    validation = read_json(validation_path, {}) if validation_path else read_json(artifact_path.parent / "validation_report.json", {})
    metrics = read_json(metrics_path, {}) if metrics_path else None
    run_dir = artifact_path.resolve().parent
    source = artifact.get("source", {}) or {}
    source_overrides = {
        "corpus": run_dir.parent.name,
        "run": run_dir.name,
        "source_run": source.get("run"),
        "run_dir": str(run_dir),
    }
    return write_viewer(artifact, output_path, validation=validation or {}, metrics=metrics or {}, source_overrides=source_overrides)


