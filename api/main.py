"""FastAPI service for UAOB.

Endpoints
  GET  /health          Neo4j, LLM and BM25-index status (never raises)
  GET  /stats           node / edge counts from Neo4j
  POST /search          retrieval only (no LLM needed)
  POST /query           retrieval + citation-enforced generation
  GET  /assets?q=...    entity search over Asset nodes
  GET  /assets/{id}     asset health profile from the knowledge graph

Retrieval modes: bm25 | dense | graph | hybrid (RRF over all three; default).
Interactive docs at /docs once running:
  python -m uvicorn api.main:app --reload
"""

import sys
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "retrieval"))
sys.path.insert(0, str(_ROOT / "graph"))

import router  # noqa: E402
from generation import llm, pipeline  # noqa: E402

app = FastAPI(
    title="UAOB -- Unified Asset & Operations Brain",
    description="Maintenance-knowledge RAG over the MaintIE graph and equipment manuals: "
                "BM25, dense and knowledge-graph retrieval fused with RRF, and "
                "citation-enforced answer generation.",
    version="1.0.0",
)

Mode = Literal["hybrid", "bm25", "dense", "graph"]
Scope = Literal["gold", "all"]


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, examples=["Which components are changed out most often?"])
    mode: Mode = "hybrid"
    k: int = Field(10, ge=1, le=50)
    scope: Scope = Field("gold", description="Graph aggregation scope: gold-annotated work orders only, or all.")


class QueryRequest(SearchRequest):
    pass


class Citation(BaseModel):
    evidence_id: str
    chunk_id: str
    source_type: str
    source_id: str
    text: str


class Enforcement(BaseModel):
    retried: bool
    dropped_sentences: int


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    mode: str
    retrieval_scores: list[dict]
    enforcement: Enforcement
    graph_plan: Optional[str] = None
    retrieval_warnings: dict = {}
    timings_ms: dict = {}


class SearchResponse(BaseModel):
    mode: str
    results: list[dict]
    graph_plan: Optional[str] = None
    warnings: dict = {}
    timings_ms: dict = {}


def _neo4j_status():
    try:
        from db import run_query
        run_query("RETURN 1 AS value")
        return {"ok": True, "detail": "connected"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@app.get("/health")
def health():
    neo = _neo4j_status()
    llm_status = llm.status()
    bm25_index = (_ROOT / "retrieval" / "bm25_index").exists()
    return {
        "status": "ok" if neo["ok"] else "degraded",
        "neo4j": neo,
        "llm": llm_status,
        "bm25_index_built": bm25_index,
        "modes": list(router.MODES),
    }


@app.get("/stats")
def stats():
    from db import run_query
    try:
        nodes = run_query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC")
        edges = run_query("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n ORDER BY n DESC")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {exc}")
    return {"nodes": nodes, "edges": edges}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    try:
        out = router.search_detailed(req.query, mode=req.mode, k=req.k, scope=req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Retrieval failed ({req.mode}): {exc}")
    graph_plan = out.get("graph_plan") or next(
        (r.get("graph_plan") for r in out["results"] if r.get("graph_plan")), None)
    return SearchResponse(mode=req.mode, results=out["results"], graph_plan=graph_plan,
                          warnings=out["errors"], timings_ms=out["timings_ms"])


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    try:
        out = pipeline.answer(req.query, mode=req.mode, k=req.k, scope=req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Retrieval failed ({req.mode}): {exc}")
    if out["generation_error"]:
        raise HTTPException(status_code=503, detail=f"Generation unavailable: {out['generation_error']}")

    evidence_map = out["evidence_map"]
    citations = [
        Citation(
            evidence_id=eid,
            chunk_id=evidence_map[eid]["chunk_id"],
            source_type=evidence_map[eid]["source_type"],
            source_id=evidence_map[eid]["source_id"],
            text=evidence_map[eid]["text"],
        )
        for eid in out["cited_evidence_ids"]
    ]
    retrieval_scores = [
        {"evidence_id": eid, "chunk_id": ev["chunk_id"], "source_id": ev["source_id"], "score": ev["score"]}
        for eid, ev in evidence_map.items()
    ]
    return QueryResponse(
        answer=out["answer"],
        citations=citations,
        mode=req.mode,
        retrieval_scores=retrieval_scores,
        enforcement=Enforcement(retried=out["retried"], dropped_sentences=out["dropped_sentences"]),
        graph_plan=out["retrieval"]["graph_plan"],
        retrieval_warnings=out["retrieval"]["errors"],
        timings_ms={**out["retrieval"]["timings_ms"], **out["timings_ms"]},
    )


@app.get("/assets")
def assets(q: str = "", limit: int = 20):
    from kg_index import get_index
    try:
        index = get_index()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {exc}")
    return [{"asset_id": k[1], "work_orders": index.df(k)}
            for k in index.search_entities(q, label="Asset", limit=limit)]


@app.get("/assets/{asset_id}")
def asset_profile(asset_id: str):
    from kg_index import get_index
    try:
        index = get_index()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Neo4j unavailable: {exc}")
    if ("Asset", asset_id) not in index.entities:
        raise HTTPException(status_code=404, detail=f"No Asset with id {asset_id!r}")
    p = index.asset_profile(asset_id)
    return {
        **{k: p[k] for k in ("asset_id", "aliases", "n_work_orders", "n_gold", "fault_share", "action_share")},
        "top_states": p["states"].most_common(10),
        "top_activities": p["activities"].most_common(10),
        "co_mentioned_assets": p["co_assets"].most_common(10),
        "parts": p["parts"].most_common(10),
        "part_of": p["part_of"].most_common(10),
        "work_orders": [index.wo_record(w) for w in p["work_orders"][:50]],
    }
