from db import run_query

CONSTRAINT_LABELS = [
    "WorkOrder",
    "Asset",
    "State",
    "Activity",
    "Chunk",
    "Document",
    "ManualSection",
]

VECTOR_INDEX_NAME = "chunk_embedding_index"
VECTOR_DIMENSIONS = 384
VECTOR_SIMILARITY_FUNCTION = "cosine"


def create_constraints():
    for label in CONSTRAINT_LABELS:
        constraint_name = f"{label.lower()}_id_unique"
        cypher = (
            f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
        )
        run_query(cypher)
        print(f"Ensured uniqueness constraint on {label}.id")


def create_vector_index():
    # Syntax verified against Neo4j Cypher Manual + neo4j-graphrag-python
    # source (indexes.py) for Neo4j 5.x: CREATE VECTOR INDEX ... OPTIONS
    # { indexConfig: { `vector.dimensions`, `vector.similarity_function` } }.
    cypher = (
        f"CREATE VECTOR INDEX {VECTOR_INDEX_NAME} IF NOT EXISTS "
        f"FOR (c:Chunk) ON (c.embedding) "
        f"OPTIONS {{ indexConfig: {{ "
        f"`vector.dimensions`: {VECTOR_DIMENSIONS}, "
        f"`vector.similarity_function`: '{VECTOR_SIMILARITY_FUNCTION}' "
        f"}} }}"
    )
    run_query(cypher)
    print(
        f"Ensured vector index {VECTOR_INDEX_NAME} on Chunk.embedding "
        f"({VECTOR_DIMENSIONS}d, {VECTOR_SIMILARITY_FUNCTION})"
    )


def apply_schema():
    create_constraints()
    create_vector_index()


if __name__ == "__main__":
    apply_schema()
