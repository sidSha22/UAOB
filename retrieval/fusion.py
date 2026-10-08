"""Response-stage fusion: Reciprocal Rank Fusion (Cormack et al., 2009).

    RRF(d) = sum_r  w_r / (c + rank_r(d))

Rank-based, so it needs no score normalisation across retrievers whose raw
scores live on different scales (BM25 ~0-15, cosine 0-1, graph counts).
c = 60 is the value from the original paper. Per-retriever weights give the
"weighted variant" named in CONTEXT.md section 5.1; equal weights is the
baseline.
"""

RRF_C = 60


def rrf(result_lists, k=10, c=RRF_C, weights=None):
    """result_lists: {retriever_name: [result, ...]} each in rank order, all
    in the common result shape (chunk_id, text, score, source_type,
    source_id, ...). Returns the top-k fused results; each carries
    `score` (the RRF score), `fused_from` {retriever: rank} and
    `component_scores` {retriever: raw score}."""
    weights = weights or {}
    fused = {}
    for name, results in result_lists.items():
        w = float(weights.get(name, 1.0))
        if w <= 0:
            continue
        for rank, r in enumerate(results, start=1):
            key = r["chunk_id"]
            entry = fused.get(key)
            if entry is None:
                entry = dict(r)
                entry["score"] = 0.0
                entry["fused_from"] = {}
                entry["component_scores"] = {}
                fused[key] = entry
            entry["score"] += w / (c + rank)
            entry["fused_from"][name] = rank
            entry["component_scores"][name] = r["score"]
            # keep graph provenance if any retriever supplied it
            if r.get("graph_path") and not entry.get("graph_path"):
                entry["graph_path"] = r["graph_path"]

    ranked = sorted(
        fused.values(),
        key=lambda e: (-e["score"], -len(e["fused_from"]), e["chunk_id"]),
    )
    return ranked[:k]
