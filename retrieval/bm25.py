"""BM25 lexical retrieval over the same Chunk set as retrieval/dense.py, via
bm25s. No API, no fusion -- just the single lexical-search primitive.
Index is built once from Neo4j and persisted to disk (INDEX_DIR) so repeat
searches load it instead of rebuilding from Chunk text every time."""

import sys
from pathlib import Path

import bm25s

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "graph"))
from db import run_query  # noqa: E402

INDEX_DIR = Path(__file__).resolve().parent / "bm25_index"

_retriever = None


def _fetch_chunks():
    return run_query("""
        MATCH (c:Chunk)-[:FROM]->(src)
        RETURN c.id AS chunk_id,
               c.text AS text,
               c.source_type AS source_type,
               src.id AS source_id,
               c.annotation_quality AS annotation_quality
    """)


def build_index():
    """Pull all Chunks from Neo4j, build the BM25 index, and persist it
    (index + per-doc metadata) to INDEX_DIR."""
    rows = _fetch_chunks()
    corpus_texts = [r["text"] for r in rows]
    corpus_metadata = [
        {
            "chunk_id": r["chunk_id"],
            "text": r["text"],
            "source_type": r["source_type"],
            "source_id": r["source_id"],
            "annotation_quality": r["annotation_quality"],
        }
        for r in rows
    ]

    tokenized = bm25s.tokenize(corpus_texts, stopwords="en")
    retriever = bm25s.BM25()
    retriever.index(tokenized)
    retriever.save(str(INDEX_DIR), corpus=corpus_metadata)
    print(f"Indexed {len(rows)} chunks -> {INDEX_DIR}")
    return retriever


def get_retriever():
    """Load the persisted index, building it first if it doesn't exist yet.
    Cached per-process so repeated search() calls don't reload from disk."""
    global _retriever
    if _retriever is not None:
        return _retriever

    if INDEX_DIR.exists():
        _retriever = bm25s.BM25.load(str(INDEX_DIR), load_corpus=True)
    else:
        _retriever = build_index()
    return _retriever


def search(query: str, k: int = 10) -> list[dict]:
    """Tokenize `query` and run BM25 retrieval against the persisted index.
    Returns the top-k hits in the same shape as retrieval/dense.py."""
    retriever = get_retriever()
    query_tokens = bm25s.tokenize([query], stopwords="en")
    docs, scores = retriever.retrieve(query_tokens, k=k, show_progress=False)

    results = []
    for doc, score in zip(docs[0], scores[0]):
        results.append({
            "chunk_id": doc["chunk_id"],
            "text": doc["text"],
            "score": float(score),
            "source_type": doc["source_type"],
            "source_id": doc["source_id"],
            "annotation_quality": doc["annotation_quality"],
        })
    return results


def _print_results(query, results):
    print(f"\nQuery: {query!r}")
    print("-" * 70)
    for i, r in enumerate(results, 1):
        quality = f" [{r['annotation_quality']}]" if r["annotation_quality"] else ""
        print(f"{i:2d}. score={r['score']:.4f}  ({r['source_type']}:{r['source_id']}){quality}")
        print(f"    {r['text']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        queries = [" ".join(sys.argv[1:])]
    else:
        queries = ["seal leak on pump", "brake not working", "replace hydraulic hose"]

    for q in queries:
        _print_results(q, search(q, k=10))
