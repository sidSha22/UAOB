"""End-to-end question answering: retrieval (any mode, via
retrieval/router.py) -> evidence map -> citation-enforced generation.

Shared by the API and the UI so both behave identically. If the LLM is not
configured or not reachable, `answer()` still returns the retrieved evidence
and reports the generation error instead of raising, so the UI can show the
evidence with a clear notice. The API turns that into HTTP 503.
"""

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "retrieval"))

import router  # noqa: E402
from generation import enforce  # noqa: E402
from generation import prompt as prompt_assembly  # noqa: E402


def answer(query, mode="hybrid", k=10, scope="gold", generate=True):
    """Returns {
        query, mode, answer (str|None), cited_evidence_ids, evidence_map,
        retried, dropped_sentences, generation_error (str|None),
        retrieval: {errors, timings_ms, weights, graph_plan}, timings_ms
    }"""
    t0 = time.perf_counter()
    retrieval = router.search_detailed(query, mode=mode, k=k, scope=scope)
    t_retrieval = (time.perf_counter() - t0) * 1000

    evidence_map = prompt_assembly.build_evidence_map(retrieval["results"])
    out = {
        "query": query,
        "mode": mode,
        "answer": None,
        "cited_evidence_ids": [],
        "evidence_map": evidence_map,
        "retried": False,
        "dropped_sentences": 0,
        "generation_error": None,
        "prompt_version": prompt_assembly.PROMPT_VERSION,
        "retrieval": {
            "errors": retrieval["errors"],
            "timings_ms": retrieval["timings_ms"],
            "weights": retrieval.get("weights"),
            "graph_plan": retrieval.get("graph_plan"),
        },
        "timings_ms": {"retrieval": round(t_retrieval, 1)},
    }
    if not generate:
        return out
    if not evidence_map:
        out["answer"] = "The evidence does not contain anything relevant to this question."
        return out

    t1 = time.perf_counter()
    try:
        outcome = enforce.generate_with_citations(query, evidence_map)
    except Exception as exc:
        out["generation_error"] = str(exc)
    else:
        out.update(
            answer=outcome["answer"],
            cited_evidence_ids=outcome["cited_evidence_ids"],
            retried=outcome["retried"],
            dropped_sentences=outcome["dropped_sentences"],
        )
    out["timings_ms"]["generation"] = round((time.perf_counter() - t1) * 1000, 1)
    return out
