"""Shared BAAI/bge-small-en-v1.5 embedding wrapper (384-d, local, batched).

bge model card convention: the query-side prefix "Represent this sentence
for searching relevant passages: " is only added at *retrieval* time, not
when indexing documents/chunks. This pipeline indexes with NO prefix, and
stamps that choice onto every Chunk (`embedding_convention`) so the future
retrieval step (step 4) knows to add the prefix on the query side only.
"""

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSIONS = 384
EMBEDDING_CONVENTION = "no_prefix_indexing__query_prefix_at_retrieval"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_model = None


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def count_tokens(text):
    tokenizer = get_model().tokenizer
    return len(tokenizer.encode(text, add_special_tokens=False))


def embed_batch(texts, batch_size=64):
    """Index-side embedding: no query prefix. Returns list[list[float]]."""
    model = get_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]
