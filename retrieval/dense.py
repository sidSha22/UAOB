"""Dense kNN retrieval over Chunk.embedding via Neo4j's native vector index.
No API, no BM25, no fusion -- just the single dense-search primitive."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "graph"))
sys.path.insert(0, str(_ROOT / "ingest"))
from db import run_query  # noqa: E402
from embedder import QUERY_PREFIX, embed_batch  # noqa: E402

VECTOR_INDEX_NAME = "chunk_embedding_index"


def search(query: str, k: int = 10) -> list[dict]:
    """Embed `query` with the recorded query-side prefix convention (see
    ingest/embedder.py), run kNN against chunk_embedding_index, and return
    the top-k hits."""
    query_vector = embed_batch([QUERY_PREFIX + query], batch_size=1)[0]

    rows = run_query(
        """
        CALL db.index.vector.queryNodes($index_name, $k, $query_vector)
        YIELD node, score
        MATCH (node)-[:FROM]->(src)
        RETURN node.id AS chunk_id,
               node.text AS text,
               score AS score,
               node.source_type AS source_type,
               src.id AS source_id,
               node.annotation_quality AS annotation_quality
        ORDER BY score DESC
        """,
        {"index_name": VECTOR_INDEX_NAME, "k": k, "query_vector": query_vector},
    )
    return rows


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
