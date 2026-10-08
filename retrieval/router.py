"""Single entry point for retrieval, shared by the API, the UI and the eval
harness.

Modes:
  bm25    lexical (retrieval/bm25.py)
  dense   kNN over Neo4j's vector index (retrieval/dense.py)
  graph   entity linking + graph planner (retrieval/graph_search.py)
  hybrid  RRF over bm25 + dense + graph (retrieval/fusion.py)

Hybrid degrades gracefully: if one component fails (for example the
embedding model has not been downloaded yet, so dense raises), the others
are still fused and the failure is reported in `errors` instead of
crashing the request. Single-retriever modes raise, so a misconfiguration is
never silently hidden.
"""

import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import fusion  # noqa: E402

MODES = ("hybrid", "bm25", "dense", "graph")
HYBRID_COMPONENTS = ("bm25", "dense", "graph")

# Query-aware fusion weights (the "weighted variant" of RRF).
# - Aggregate questions ("which assets ... most often", "both X and Y", ...):
#   lexical/semantic similarity only matches the question's wording, not the
#   answer, so the graph component dominates (3.0).
# - Lookups where the graph linked an entity: graph matches are exact entity
#   matches, so graph counts double (2.0) -- a result that BM25 *and* dense
#   agree on still ties a graph rank-1 hit.
# - Nothing linked: the graph returns nothing and equal weights apply.
# Chosen on the 24-question dev set (questions/questions.json); re-check with
# `python eval/harness.py` if the question set or the embedding model changes.
AGGREGATE_GRAPH_WEIGHT = 3.0
LOOKUP_GRAPH_WEIGHT = 2.0
MODE_LABELS = {
    "hybrid": "Hybrid (RRF)",
    "bm25": "BM25 lexical",
    "dense": "Dense vector",
    "graph": "Knowledge graph",
}


def _component(name):
    # Imported lazily so e.g. the graph retriever works on a machine where
    # sentence-transformers is not installed yet.
    if name == "bm25":
        import bm25
        return bm25.search
    if name == "dense":
        import dense
        return dense.search
    if name == "graph":
        import graph_search
        return graph_search.search
    raise ValueError(f"Unknown retriever {name!r}; expected one of {HYBRID_COMPONENTS}")


def _run(name, query, k, scope):
    fn = _component(name)
    if name == "graph":
        return fn(query, k=k, scope=scope)
    return fn(query, k=k)


def search_detailed(query, mode="hybrid", k=10, components=HYBRID_COMPONENTS,
                    weights=None, depth=None, scope="gold"):
    """Returns {results, mode, errors: {name: msg}, timings_ms: {name: ms},
    component_results: {name: [...]}, and for hybrid also weights and
    graph_plan}.

    depth: how many results each component contributes before fusion
    (default max(3k, 30)). scope: graph aggregation scope ('gold' | 'all').
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("Query must not be empty.")
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}")

    timings, errors, component_results = {}, {}, {}

    if mode != "hybrid":
        t0 = time.perf_counter()
        results = _run(mode, query, k, scope)
        timings[mode] = round((time.perf_counter() - t0) * 1000, 1)
        component_results[mode] = results
        return {"results": results, "mode": mode, "errors": errors,
                "timings_ms": timings, "component_results": component_results}

    depth = depth or max(3 * k, 30)
    for name in components:
        t0 = time.perf_counter()
        try:
            component_results[name] = _run(name, query, depth, scope)
        except Exception as exc:  # degrade, but report
            errors[name] = f"{type(exc).__name__}: {exc}"
        timings[name] = round((time.perf_counter() - t0) * 1000, 1)

    if not component_results:
        raise RuntimeError("All hybrid components failed: " + "; ".join(
            f"{n}: {m}" for n, m in errors.items()))

    graph_plan = None
    if component_results.get("graph"):
        graph_plan = component_results["graph"][0].get("graph_plan")
    if weights is None:
        weights = {}
        if graph_plan and graph_plan != "lookup":
            weights["graph"] = AGGREGATE_GRAPH_WEIGHT
        elif graph_plan == "lookup":
            weights["graph"] = LOOKUP_GRAPH_WEIGHT

    t0 = time.perf_counter()
    results = fusion.rrf(component_results, k=k, weights=weights)
    timings["fusion"] = round((time.perf_counter() - t0) * 1000, 1)
    return {"results": results, "mode": mode, "errors": errors,
            "timings_ms": timings, "component_results": component_results,
            "weights": {n: weights.get(n, 1.0) for n in component_results},
            "graph_plan": graph_plan}


def search(query, mode="hybrid", k=10, **kwargs):
    """List-only convenience wrapper (same shape as bm25.search etc.)."""
    return search_detailed(query, mode=mode, k=k, **kwargs)["results"]
