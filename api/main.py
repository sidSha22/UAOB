"""FastAPI app: a single POST /query endpoint over retrieval + citation-
enforced generation. No fusion, no graph retrieval -- mode picks exactly
one of retrieval/bm25.py or retrieval/dense.py, unmodified."""

import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "retrieval"))

import bm25 as bm25_mod  # noqa: E402
import dense as dense_mod  # noqa: E402
from generation import enforce  # noqa: E402
from generation import prompt as prompt_assembly  # noqa: E402

app = FastAPI(title="Maintenance RAG -- retrieval + generation (no fusion, no graph retrieval)")

RETRIEVERS = {"bm25": bm25_mod.search, "dense": dense_mod.search}


class QueryRequest(BaseModel):
    query: str
    mode: Literal["bm25", "dense"]
    k: int = 10


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


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    search_fn = RETRIEVERS[req.mode]
    results = search_fn(req.query, k=req.k)

    evidence_map = prompt_assembly.build_evidence_map(results)
    outcome = enforce.generate_with_citations(req.query, evidence_map)

    citations = [
        Citation(
            evidence_id=eid,
            chunk_id=evidence_map[eid]["chunk_id"],
            source_type=evidence_map[eid]["source_type"],
            source_id=evidence_map[eid]["source_id"],
            text=evidence_map[eid]["text"],
        )
        for eid in outcome["cited_evidence_ids"]
    ]

    retrieval_scores = [
        {
            "evidence_id": eid,
            "chunk_id": ev["chunk_id"],
            "source_id": ev["source_id"],
            "score": ev["score"],
        }
        for eid, ev in evidence_map.items()
    ]

    return QueryResponse(
        answer=outcome["answer"],
        citations=citations,
        mode=req.mode,
        retrieval_scores=retrieval_scores,
        enforcement=Enforcement(
            retried=outcome["retried"], dropped_sentences=outcome["dropped_sentences"]
        ),
    )
