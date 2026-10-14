from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
LANGEXTRACT_ROOT = PROJECT_ROOT / "langextract"

# Prefer the workspace copies over globally installed packages.
sys.path.insert(0, str(LANGEXTRACT_ROOT))
sys.path.insert(0, str(SRC_DIR))

try:
    import langextract as lx
    from langextract.core import data as lx_data
    from langextract.providers.openai import OpenAILanguageModel
except ModuleNotFoundError as exc:
    raise SystemExit(
        "LangExtract dependencies are missing. "
        "Run inside the langcarto environment:\n"
        "  C:\\ProgramData\\Anaconda3\\envs\\langcarto\\python.exe -m pip "
        "install -r requirements.txt\n"
        f"Original error: {exc}"
    ) from exc

import networkx as nx
from langchain_community.embeddings import HuggingFaceEmbeddings
from smolagents import LiteLLMModel

from causal_world_modelling_agent.agents.causal_discovery.atomic_rag_agent import (
    AtomicRAGDiscoveryAgentFactory,
)
from causal_world_modelling_agent.world_model.world_manager import BaseWorldManager
from visualize_graph import save_graph_png, save_graph_png_via_browser
from causal_world_modelling_agent.visualization.graph_visualizer import GraphVisualizer


DEFAULT_INPUT = PROJECT_ROOT / "data" / "Test-text" / "input" / "case1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "test_output"


PROMPT_DESCRIPTION = """
Extract causal variables and explicit causal relationships from industrial
equipment documentation.

Rules:
1. Every extraction_text must be an exact span from the source text.
2. Use extraction_class "causal_variable" for causal variables or conditions.
3. Use extraction_class "causal_relation" for one explicit causal relation.
4. Do not collapse a multi-step mechanism into a direct relation when the
   intermediate variable is explicitly present in the source.
5. Do not add common-sense relations that are not supported by the source.
6. Preserve the direction of causality and the polarity of the relationship.
7. Keep maintenance actions separate from physical causal variables unless the
   text explicitly says that the action causes a physical change.

For causal_variable, provide attributes:
  canonical_name, entity_type, variable_role, polarity.

For causal_relation, provide attributes:
  cause, effect, relation_type, polarity, explicit, confidence.
""".strip()


def make_example() -> lx_data.ExampleData:
    text = (
        "Low oil flow reduces oil film quality. "
        "Reduced oil film quality increases friction. "
        "Increased friction raises bearing temperature."
    )
    extractions = [
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="Low oil flow",
            attributes={
                "canonical_name": "Oil Flow",
                "entity_type": "condition",
                "variable_role": "cause",
                "polarity": "decrease",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Low oil flow reduces oil film quality.",
            attributes={
                "cause": "Oil Flow",
                "effect": "Oil Film Quality",
                "relation_type": "physical_mechanism",
                "polarity": "decrease",
                "explicit": "true",
                "confidence": "high",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="oil film quality",
            attributes={
                "canonical_name": "Oil Film Quality",
                "entity_type": "condition",
                "variable_role": "effect",
                "polarity": "decrease",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Reduced oil film quality increases friction.",
            attributes={
                "cause": "Oil Film Quality",
                "effect": "Friction",
                "relation_type": "physical_mechanism",
                "polarity": "decrease",
                "explicit": "true",
                "confidence": "high",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="friction",
            attributes={
                "canonical_name": "Friction",
                "entity_type": "measurement",
                "variable_role": "effect",
                "polarity": "increase",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Increased friction raises bearing temperature.",
            attributes={
                "cause": "Friction",
                "effect": "Bearing Temperature",
                "relation_type": "physical_mechanism",
                "polarity": "increase",
                "explicit": "true",
                "confidence": "high",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="bearing temperature",
            attributes={
                "canonical_name": "Bearing Temperature",
                "entity_type": "measurement",
                "variable_role": "effect",
                "polarity": "increase",
            },
        ),
    ]
    return lx_data.ExampleData(text=text, extractions=extractions)


def load_source_document(path: Path) -> tuple[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Input document not found: {path}")

    with path.open("r", encoding="utf-8-sig") as file:
        document = json.load(file)

    title = document["title"]["eng"]
    summary = document["summary"]
    source_text = f"Title: {title}\n - \nContent:\n{summary}"
    return source_text, summary


def value_to_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(item) for item in value)
    return str(value)


def extraction_interval(extraction: Any) -> str:
    interval = getattr(extraction, "char_interval", None)
    if interval is None:
        return "ungrounded"
    return f"{interval.start_pos}:{interval.end_pos}"


def extraction_status(extraction: Any) -> str:
    status = getattr(extraction, "alignment_status", None)
    if status is None:
        return "unknown"
    return getattr(status, "value", str(status))


def grounded_extractions(document: Any) -> list[Any]:
    extractions = getattr(document, "extractions", None) or []
    return [
        extraction
        for extraction in extractions
        if getattr(extraction, "char_interval", None) is not None
    ]


def run_langextract(
    source_text: str,
    api_key: str,
    model_id: str,
    base_url: str,
    extraction_passes: int,
    max_char_buffer: int,
) -> Any:
    print("\n[LangExtract] Starting grounded causal extraction")
    print(f"[LangExtract] model_id={model_id}")
    print(f"[LangExtract] base_url={base_url}")
    print(f"[LangExtract] extraction_passes={extraction_passes}")
    print(f"[LangExtract] max_char_buffer={max_char_buffer}")
    print(f"[LangExtract] source_characters={len(source_text)}")

    model = OpenAILanguageModel(
        model_id=model_id,
        api_key=api_key,
        base_url=base_url,
        temperature=0.0,
        max_workers=1,
    )

    result = lx.extract(
        text_or_documents=source_text,
        prompt_description=PROMPT_DESCRIPTION,
        examples=[make_example()],
        model=model,
        extraction_passes=extraction_passes,
        max_char_buffer=max_char_buffer,
        context_window_chars=300,
        use_schema_constraints=False,
        batch_length=1,
        max_workers=1,
        resolver_params={
            "suppress_parse_errors": True,
            "enable_fuzzy_alignment": True,
            "fuzzy_alignment_threshold": 0.75,
        },
        show_progress=True,
    )

    document = result[0] if isinstance(result, list) else result
    extraction_count = len(getattr(document, 'extractions', None) or [])
    print(f'[LangExtract] Completed extraction: raw_extractions={extraction_count}')
    return document


def format_langextract_context(extractions: list[Any]) -> str:
    lines = [
        "LangExtract grounded pre-extraction.",
        "These entries were aligned to exact source spans and are candidate evidence.",
    ]

    for extraction in extractions:
        attributes = getattr(extraction, "attributes", None) or {}
        lines.append(
            "- class={class_name}; span={span}; status={status}; "
            "text={text!r}; attributes={attributes}".format(
                class_name=getattr(extraction, "extraction_class", "unknown"),
                span=extraction_interval(extraction),
                status=extraction_status(extraction),
                text=getattr(extraction, "extraction_text", ""),
                attributes=json.dumps(attributes, ensure_ascii=False, sort_keys=True),
            )
        )

    if len(lines) == 2:
        lines.append("- <no grounded extractions returned>")

    return "\n".join(lines)


def ensure_node(
    graph: nx.DiGraph,
    name: str,
    description: str,
    entity_type: str = "string",
    values: str = "unspecified",
    **extra: Any,
) -> None:
    if graph.has_node(name):
        graph.nodes[name].update(
            {
                key: value
                for key, value in extra.items()
                if value is not None
            }
        )
        return

    graph.add_node(
        name,
        name=name,
        description=description,
        type=entity_type,
        values=values,
        **{
            key: value
            for key, value in extra.items()
            if value is not None
        },
    )


def build_pre_graph(extractions: list[Any]) -> nx.DiGraph:
    graph = nx.DiGraph()

    for extraction in extractions:
        extraction_class = getattr(extraction, "extraction_class", "")
        attributes = getattr(extraction, "attributes", None) or {}
        extraction_text = getattr(extraction, "extraction_text", "")
        description = getattr(extraction, "description", None) or extraction_text

        if extraction_class == "causal_variable":
            name = value_to_text(attributes.get("canonical_name"), extraction_text).strip()
            if not name:
                continue
            ensure_node(
                graph,
                name,
                description,
                entity_type=value_to_text(attributes.get("entity_type"), "string"),
                values=value_to_text(attributes.get("values"), "unspecified"),
                supporting_text_snippets=[extraction_text],
                contextual_information=extraction_text,
            )
            continue

        if extraction_class != "causal_relation":
            continue

        cause = value_to_text(attributes.get("cause")).strip()
        effect = value_to_text(attributes.get("effect")).strip()
        if not cause or not effect:
            continue

        ensure_node(
            graph,
            cause,
            value_to_text(attributes.get("cause_description"), cause),
            entity_type="condition",
            values="unspecified",
            supporting_text_snippets=[extraction_text],
            contextual_information=extraction_text,
        )
        ensure_node(
            graph,
            effect,
            value_to_text(attributes.get("effect_description"), effect),
            entity_type="condition",
            values="unspecified",
            supporting_text_snippets=[extraction_text],
            contextual_information=extraction_text,
        )

        edge_attributes = {
            "cause": cause,
            "effect": effect,
            "description": extraction_text,
            "contextual_information": extraction_text,
            "type": value_to_text(attributes.get("relation_type"), "direct"),
            "strength": value_to_text(attributes.get("strength"), "unspecified"),
            "confidence": value_to_text(attributes.get("confidence"), "unspecified"),
            "function": None,
            "polarity": value_to_text(attributes.get("polarity"), "unspecified"),
            "explicit": value_to_text(attributes.get("explicit"), "true"),
            "evidence_span": extraction_interval(extraction),
            "alignment_status": extraction_status(extraction),
        }

        if graph.has_edge(cause, effect):
            graph.edges[cause, effect].update(edge_attributes)
        else:
            graph.add_edge(cause, effect, **edge_attributes)

    return graph


def run_original_discovery(
    source_text: str,
    pre_graph: nx.DiGraph,
    pre_context: str,
    api_key: str,
    model_id: str,
    base_url: str,
    temperature: float,
) -> nx.DiGraph:
    litellm_model_id = model_id if "/" in model_id else f"deepseek/{model_id}"

    print("\n[Original Agent] Starting original Causal Cartographer flow")
    print(f"[Original Agent] model_id={litellm_model_id}")
    print(f"[Original Agent] base_url={base_url}")
    print(f"[Original Agent] temperature={temperature}")
    print(
        "[Original Agent] RAG initial graph: "
        f"{pre_graph.number_of_nodes()} nodes, "
        f"{pre_graph.number_of_edges()} edges"
    )
    print(f"[Original Agent] grounded context characters={len(pre_context)}")

    base_model = LiteLLMModel(
        model_id=litellm_model_id,
        api_key=api_key,
        api_base=base_url,
        temperature=temperature,
    )

    embedding_model = HuggingFaceEmbeddings(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    agent_factory = AtomicRAGDiscoveryAgentFactory(
        embeddings=embedding_model,
        initial_graph=pre_graph if pre_graph.number_of_nodes() else None,
    )
    discovery_agent = agent_factory.createAgent(base_model, api_key=api_key)

    task = (
        f"{source_text}\n\n"
        "--- LangExtract grounded candidates ---\n"
        f"{pre_context}\n"
        "--- End of candidates ---\n\n"
        "Use the grounded candidates as high-priority variables and relations. "
        "Preserve explicit multi-step mechanisms, remove contradictions, and "
        "complete the causal graph using only the source text and the validated "
        "candidate graph. Return the final graph with networkx.DiGraph. "
        "Output compactness rules: in intermediate steps do NOT print the full "
        "graph, node attribute dictionaries or long JSON; print only short "
        "progress lines such as counts and `cause -> effect` edge lines, with at "
        "most one short supporting quote per node. Pass the final networkx.DiGraph "
        "directly to final_answer without printing it."
    )

    print(f"[Original Agent] task characters={len(task)}")
    print("[Original Agent] Calling DeepSeek CodeAgent...")
    result = discovery_agent.run(task)
    print(
        "[Original Agent] Returned graph: "
        f"{result.number_of_nodes() if isinstance(result, nx.DiGraph) else 'unknown'} nodes, "
        f"{result.number_of_edges() if isinstance(result, nx.DiGraph) else 'unknown'} edges"
    )
    if not isinstance(result, nx.DiGraph):
        raise TypeError(
            f"Original discovery agent returned {type(result).__name__}, "
            "expected networkx.DiGraph."
        )
    return result


def save_outputs(graph: nx.DiGraph, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    stem = f"causal_graph_{timestamp}"
    gml_path = output_dir / f"{stem}.gml"

    print(f"[Output] Writing GML: {gml_path}")
    nx.write_gml(graph, gml_path, stringizer=str)
    print("[Output] GML write completed")

    visualizer = GraphVisualizer(port=5000, debug=False, open_browser=False)
    visualizer.graph = graph
    visualizer.graph_path = str(gml_path)

    try:
        print("[Output] Starting headless web-rendered PNG export")
        png_path = Path(
            save_graph_png_via_browser(
                visualizer,
                str(gml_path),
                str(output_dir),
                stem,
            )
        )
    except Exception as browser_error:
        print(
            "Web-rendered PNG export failed; falling back to Matplotlib: "
            f"{browser_error}"
        )
        png_path = Path(
            save_graph_png(graph, str(output_dir), stem)
        )

    return gml_path, png_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use LangExtract for grounded causal pre-extraction, then run the "
            "original Causal Cartographer discovery pipeline."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Input JSON document.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for GML and PNG output.",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DEEPSEEK_API_KEY"),
        help="DeepSeek API key. Defaults to DEEPSEEK_API_KEY.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-v4-pro-0813",
        help="Model used by both LangExtract and the original agent.",
    )
    parser.add_argument(
        "--base-url",
        default="https://llmapi.zihangwu.com.cn:8443/v1",
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Temperature for the original discovery agent.",
    )
    parser.add_argument(
        "--extraction-passes",
        type=int,
        default=2,
        help="LangExtract extraction passes.",
    )
    parser.add_argument(
        "--max-char-buffer",
        type=int,
        default=1800,
        help="Maximum character buffer used by LangExtract chunking.",
    )
    return parser.parse_args()


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def json_preview(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def print_extraction_details(extractions, title):
    print(f"\n=== {title} ===")
    print(f"count={len(extractions)}")

    for index, extraction in enumerate(extractions, start=1):
        attributes = getattr(extraction, "attributes", None) or {}
        print(
            f"[{index}] class={getattr(extraction, 'extraction_class', 'unknown')} "
            f"span={extraction_interval(extraction)} "
            f"status={extraction_status(extraction)}"
        )
        print(f"    text={getattr(extraction, 'extraction_text', '')!r}")
        print(f"    description={getattr(extraction, 'description', None)!r}")
        print(f"    attributes={json_preview(attributes)}")
        print(f"    group_index={getattr(extraction, 'group_index', None)}")


def print_graph_details(graph, title):
    print(f"\n=== {title} ===")
    print(f"nodes={graph.number_of_nodes()} edges={graph.number_of_edges()}")
    print(f"is_directed={graph.is_directed()}")
    print(f"is_dag={nx.is_directed_acyclic_graph(graph)}")
    print("Nodes:")

    for node, attributes in graph.nodes(data=True):
        print(f"  NODE {node!r}")
        print(f"    {json_preview(dict(attributes))}")

    print("Edges:")
    for source, target, attributes in graph.edges(data=True):
        print(f"  EDGE {source!r} -> {target!r}")
        print(f"    {json_preview(dict(attributes))}")


def run_pipeline(args):
    print("=== Runtime information ===")
    print(f"python={sys.executable}")
    print(f"project_root={PROJECT_ROOT}")
    print(f"input={args.input}")
    print(f"output_dir={args.output_dir}")
    print(f"deepseek_model={args.model}")
    print(f"deepseek_base_url={args.base_url}")
    print(f"deepseek_api_key_present={bool(args.api_key)}")
    print(f"lang_extract_passes={args.extraction_passes}")
    print(f"max_char_buffer={args.max_char_buffer}")

    expected_python = Path(
        r"C:\ProgramData\Anaconda3\envs\langcarto\python.exe"
    )
    if expected_python.exists() and Path(sys.executable) != expected_python:
        print(
            "WARNING: You are not running with the langcarto interpreter.\n"
            f"Current:  {sys.executable}\n"
            f"Expected: {expected_python}\n"
        )

    source_text, _ = load_source_document(args.input)
    print("\n=== Source document ===")
    print(f"characters={len(source_text)}")
    print(f"preview={source_text[:500]!r}")

    example = make_example()
    print("\n=== LangExtract configuration ===")
    print(f"prompt_description={PROMPT_DESCRIPTION}")
    print(f"example_text={example.text!r}")
    print(f"example_extractions={len(example.extractions)}")

    print("\nStep 1/3: LangExtract grounded causal pre-extraction")
    lang_document = run_langextract(
        source_text=source_text,
        api_key=args.api_key,
        model_id=args.model,
        base_url=args.base_url,
        extraction_passes=args.extraction_passes,
        max_char_buffer=args.max_char_buffer,
    )

    raw_extractions = getattr(lang_document, "extractions", None) or []
    grounded = grounded_extractions(lang_document)
    ungrounded = [
        extraction
        for extraction in raw_extractions
        if getattr(extraction, "char_interval", None) is None
    ]

    print("\n=== LangExtract summary ===")
    print(f"raw_extractions={len(raw_extractions)}")
    print(f"grounded_extractions={len(grounded)}")
    print(f"ungrounded_extractions={len(ungrounded)}")
    print_extraction_details(raw_extractions, "LangExtract raw extractions")
    print_extraction_details(grounded, "LangExtract grounded extractions")
    print_extraction_details(ungrounded, "LangExtract discarded extractions")

    pre_context = format_langextract_context(grounded)
    print("\n=== Candidate context sent to the original agent ===")
    print(pre_context)

    pre_graph = build_pre_graph(grounded)
    print_graph_details(pre_graph, "LangExtract pre-graph")

    print("\nStep 2/3: Original Causal Cartographer discovery flow")
    llm_graph = run_original_discovery(
        source_text=source_text,
        pre_graph=pre_graph,
        pre_context=pre_context,
        api_key=args.api_key,
        model_id=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
    )
    print_graph_details(llm_graph, "Original agent output graph")

    world_manager = BaseWorldManager()
    print("\n=== World merge ===")
    print(f"initial_worlds={len(world_manager.get_worlds())}")

    if pre_graph.number_of_nodes():
        world_manager.merge(pre_graph)
        print(
            "merged LangExtract pre-graph as world_0: "
            f"{pre_graph.number_of_nodes()} nodes, "
            f"{pre_graph.number_of_edges()} edges"
        )

    world_manager.merge(llm_graph)
    print(
        "merged original agent graph: "
        f"{llm_graph.number_of_nodes()} nodes, "
        f"{llm_graph.number_of_edges()} edges"
    )

    final_graph = world_manager.get_complete_graph()
    print(f"world_count={len(world_manager.get_worlds())}")
    print_graph_details(final_graph, "Final merged graph")

    print("\nStep 3/3: Saving GML and web-rendered PNG")
    gml_path, png_path = save_outputs(final_graph, args.output_dir)

    print(f"\nGML saved to: {gml_path}")
    print(f"PNG saved to: {png_path}")


def main() -> None:
    args = parse_args()

    # Guard against Windows GBK console/pipes (UnicodeEncodeError on chars
    # such as the superscript 2 when rich/smolagents logs the agent code).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if not args.api_key:
        raise SystemExit(
            "DeepSeek API key is missing. Set DEEPSEEK_API_KEY or pass --api-key."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = args.output_dir / f"test_run_{timestamp}.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr

    with log_path.open("w", encoding="utf-8") as log_file:
        sys.stdout = Tee(original_stdout, log_file)
        sys.stderr = Tee(original_stderr, log_file)
        try:
            print(f"Detailed log file: {log_path}")
            run_pipeline(args)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            raise
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

    print(f"Detailed log saved to: {log_path}")

if __name__ == "__main__":
    main()