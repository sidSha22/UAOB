"""Part A: one Chunk per WorkOrder (no splitting; work orders average 5.4
words). Creates Chunk{id, text, tokens} with a FROM edge to its WorkOrder,
carrying annotation_quality through from the source. Resumable: uses MERGE
on Chunk.id, so re-running only fills in gaps left by a prior crash."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "graph"))
from db import run_query  # noqa: E402

from embedder import count_tokens  # noqa: E402

BATCH_SIZE = 500
PIPELINE_VERSION = "v0-chunks"


def chunk_id_for(wo_id):
    return f"wochunk::{wo_id}"


def fetch_work_orders():
    return run_query("""
        MATCH (w:WorkOrder)
        RETURN w.id AS wo_id, w.text AS text, w.annotation_quality AS annotation_quality
    """)


def write_chunks(rows):
    cypher = f"""
    UNWIND $rows AS row
    MATCH (w:WorkOrder {{id: row.wo_id}})
    MERGE (c:Chunk {{id: row.chunk_id}})
    ON CREATE SET c.text = row.text,
                  c.tokens = row.tokens,
                  c.annotation_quality = row.annotation_quality,
                  c.source_type = 'work_order',
                  c.pipeline_version = '{PIPELINE_VERSION}',
                  c.synthetic = false
    MERGE (c)-[:FROM]->(w)
    """
    for i in range(0, len(rows), BATCH_SIZE):
        run_query(cypher, {"rows": rows[i:i + BATCH_SIZE]})


def main():
    work_orders = fetch_work_orders()
    print(f"Found {len(work_orders)} WorkOrder nodes")

    existing = run_query("""
        MATCH (c:Chunk {source_type: 'work_order'}) RETURN c.id AS id
    """)
    existing_ids = {row["id"] for row in existing}
    print(f"{len(existing_ids)} work-order Chunks already exist (resuming, will skip)")

    rows = []
    for wo in work_orders:
        cid = chunk_id_for(wo["wo_id"])
        if cid in existing_ids:
            continue
        rows.append({
            "wo_id": wo["wo_id"],
            "chunk_id": cid,
            "text": wo["text"],
            "tokens": count_tokens(wo["text"]),
            "annotation_quality": wo["annotation_quality"],
        })

    print(f"Writing {len(rows)} new Chunk nodes...")
    write_chunks(rows)
    print("Done.")

    total = run_query("MATCH (c:Chunk {source_type: 'work_order'}) RETURN count(c) AS n")[0]["n"]
    print(f"Total work-order Chunks in graph: {total}")


if __name__ == "__main__":
    main()
