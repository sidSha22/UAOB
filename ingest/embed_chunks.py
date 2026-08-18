"""Part C: embed every Chunk that doesn't have an embedding yet, in batches,
via BAAI/bge-small-en-v1.5 (384-d, no query prefix at indexing time -- see
embedder.py). Resumable by construction: only Chunks with embedding IS NULL
are selected, so a crash only costs the in-flight batch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "graph"))
from db import run_query  # noqa: E402

from embedder import (  # noqa: E402
    EMBEDDING_CONVENTION,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    embed_batch,
)

BATCH_SIZE = 64


def fetch_unembedded(limit):
    return run_query("""
        MATCH (c:Chunk)
        WHERE c.embedding IS NULL
        RETURN c.id AS id, c.text AS text
        LIMIT $limit
    """, {"limit": limit})


def write_embeddings(rows_with_vectors):
    cypher = """
    UNWIND $rows AS row
    MATCH (c:Chunk {id: row.id})
    SET c.embedding = row.embedding,
        c.embedding_model = row.embedding_model,
        c.embedding_convention = row.embedding_convention
    """
    run_query(cypher, {"rows": rows_with_vectors})


def main():
    total_embedded = 0
    while True:
        rows = fetch_unembedded(BATCH_SIZE)
        if not rows:
            break
        vectors = embed_batch([r["text"] for r in rows], batch_size=BATCH_SIZE)
        write_embeddings([
            {
                "id": r["id"],
                "embedding": vec,
                "embedding_model": EMBEDDING_MODEL,
                "embedding_convention": EMBEDDING_CONVENTION,
            }
            for r, vec in zip(rows, vectors)
        ])
        total_embedded += len(rows)
        print(f"Embedded {total_embedded} chunks so far...")

    print(f"Done. Embedded {total_embedded} new chunks this run.")

    remaining = run_query("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS n")[0]["n"]
    total = run_query("MATCH (c:Chunk) RETURN count(c) AS n")[0]["n"]
    bad_dim = run_query(
        "MATCH (c:Chunk) WHERE c.embedding IS NOT NULL AND size(c.embedding) <> $d RETURN count(c) AS n",
        {"d": EMBEDDING_DIMENSIONS},
    )[0]["n"]
    print(f"Total chunks: {total}, still unembedded: {remaining}, wrong-dimension embeddings: {bad_dim}")


if __name__ == "__main__":
    main()
