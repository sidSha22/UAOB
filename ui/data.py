"""Data access for the UI: cached wrappers over retrieval, generation and
the knowledge-graph index, plus status probes. Everything Neo4j-bound is
wrapped so a stopped database produces a clear message, not a traceback."""

import json
import re
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "retrieval", ROOT / "graph", ROOT / "ingest"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import router  # noqa: E402
from db import run_query  # noqa: E402
from generation import llm, pipeline  # noqa: E402

ARTIFACTS_DIR = ROOT / "artifacts"
QUESTIONS_PATH = ROOT / "questions" / "questions.json"

EXAMPLE_QUESTIONS = [
    "What was reported about the tail pulley?",
    "Which components are changed out most often across the fleet?",
    "Which assets have both a replacement and a repair recorded against them?",
    "Do any assets show a recurring pattern of the same fault being reported more than once?",
    "How should conveyor pulley bearings be lubricated?",
    "What was done about the weeping boom cylinder hose?",
]


# ------------------------------------------------------------------ status
@st.cache_data(ttl=15, show_spinner=False)
def neo4j_status():
    try:
        run_query("RETURN 1 AS value")
        return {"ok": True, "detail": "Connected"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@st.cache_data(ttl=15, show_spinner=False)
def llm_status():
    return llm.status()


def bm25_index_built():
    return (ROOT / "retrieval" / "bm25_index").exists()


@st.cache_data(ttl=60, show_spinner=False)
def graph_counts():
    nodes = run_query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC")
    edges = run_query("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n ORDER BY n DESC")
    return nodes, edges


# ------------------------------------------------------------------ index
@st.cache_resource(show_spinner="Loading the knowledge graph into memory...")
def kg():
    from kg_index import get_index
    return get_index()


def reload_kg():
    from kg_index import get_index
    get_index(refresh=True)
    kg.clear()
    graph_counts.clear()


@st.cache_resource(show_spinner="Loading the BM25 index (built from Neo4j on first use)...")
def warm_bm25():
    import bm25
    bm25.get_retriever()
    return True


@st.cache_resource(show_spinner="Loading the embedding model (first start only)...")
def warm_dense():
    import dense
    dense.search("warm up", k=1)
    return True


def warm_all():
    """Load every heavy index once per server process so the first question is
    fast. Failures are ignored here: each page already shows a clear message
    when Neo4j or the embedding model is unavailable."""
    for load in (kg, warm_bm25, warm_dense):
        try:
            load()
        except Exception:
            pass


# ------------------------------------------------------------------ queries
@st.cache_data(ttl=900, show_spinner=False, max_entries=256)
def ask(query, mode, k, scope, generate):
    if mode in ("bm25", "hybrid"):
        warm_bm25()
    kg()
    return pipeline.answer(query, mode=mode, k=k, scope=scope, generate=generate)


@st.cache_data(ttl=900, show_spinner=False, max_entries=256)
def retrieve(query, mode, k, scope):
    if mode in ("bm25", "hybrid"):
        warm_bm25()
    kg()
    out = router.search_detailed(query, mode=mode, k=k, scope=scope)
    out.pop("component_results", None)
    return out


@st.cache_data(ttl=900, show_spinner=False, max_entries=256)
def explain(query):
    import graph_search
    kg()
    info = graph_search.explain(query)
    return {"plan": info["plan"], "targets": info["targets"],
            "links": [{"span": e["span"], "label": e["label"], "id": e["id"], "df": e["df"]}
                      for e in info["links"]]}


@st.cache_data(ttl=3600, show_spinner=False, max_entries=2048)
def manual_section(section_id):
    """Human-readable label for a ManualSection id."""
    try:
        rows = run_query(
            "MATCH (s:ManualSection {id: $id}) RETURN s.title AS title, s.doc AS doc, s.start_page AS start_page",
            {"id": section_id})
    except Exception:
        rows = []
    doc = section_id.split("::")[1] if section_id.count("::") >= 1 else section_id
    doc = re.sub(r"[_-]+", " ", doc).strip()
    if rows:
        r = rows[0]
        page = f", p. {r['start_page'] + 1}" if r.get("start_page") is not None else ""
        title = r.get("title") or ""
        if title.lower().startswith("page "):
            title = ""
        return f"{doc}{page}" + (f": {title}" if title else "")
    return doc


def wo_entities(wo_id):
    """(label, surface) pairs for a work order, from the graph index."""
    index = kg()
    pairs = []
    for label, eid in index.wo_entities.get(wo_id, ()):
        pairs.append((label, eid))
        for alias in index.entities.get((label, eid), {}).get("aliases", []):
            pairs.append((label, alias))
    return pairs


# ------------------------------------------------------------------ eval
def eval_runs():
    runs = sorted(ARTIFACTS_DIR.glob("eval_*.json"), reverse=True)
    return runs


@st.cache_data(show_spinner=False)
def load_eval(path_str, mtime):
    with open(path_str, encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def load_questions():
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)["questions"]
