"""End-to-end question answering: safety gate -> retrieval (any mode, via
retrieval/router.py) -> evidence map -> citation-enforced generation.

Shared by the API and the UI so both behave identically. If the LLM is not
configured or not reachable, `answer()` still returns the retrieved evidence
and reports the generation error instead of raising, so the UI can show the
evidence with a clear notice. The API turns that into HTTP 503.

Procedural questions ("how do I ...") go through generation/safety.py first.
They are answered only from equipment-manual evidence; without enough of it
the gate refuses and the LLM is never called.
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
from generation import safety  # noqa: E402

HISTORY_RESULTS = 3  # work-order history added next to manual evidence


def _gate_summary(gate):
    return {key: gate[key] for key in
            ("intent", "decision", "reason", "best_manual_score", "lexical_coverage",
             "threshold", "min_lexical_coverage", "safety_terms")}


def answer(query, mode="hybrid", k=10, scope="gold", generate=True):
    """Returns {
        query, mode, answer (str|None), cited_evidence_ids, evidence_map,
        retried, dropped_sentences, generation_error (str|None), gate,
        retrieval: {errors, timings_ms, weights, graph_plan}, timings_ms
    }"""
    t0 = time.perf_counter()
    gate = safety.assess(query)
    t_gate = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    retrieval = router.search_detailed(query, mode=mode, k=k, scope=scope)
    t_retrieval = (time.perf_counter() - t1) * 1000

    results = retrieval["results"]
    if gate["intent"] != "informational":
        # Manual evidence first (that is what a procedure must rest on), then a
        # little work-order history for the same query so the answer can note
        # what was actually done on site.
        history = [r for r in results if r["source_type"] != "manual"][:HISTORY_RESULTS]
        results = gate["candidates"] + history if gate["decision"] != "refuse" else gate["candidates"]

    evidence_map = prompt_assembly.build_evidence_map(results)
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
        "gate": _gate_summary(gate),
        "retrieval": {
            "errors": retrieval["errors"],
            "timings_ms": retrieval["timings_ms"],
            "weights": retrieval.get("weights"),
            "graph_plan": retrieval.get("graph_plan"),
        },
        "timings_ms": {"gate": round(t_gate, 1), "retrieval": round(t_retrieval, 1)},
    }
    if gate["decision"] == "refuse":
        out["answer"] = safety.refusal_text(gate["candidates"])
        return out
    if not generate:
        return out
    if not evidence_map:
        out["answer"] = "The evidence does not contain anything relevant to this question."
        return out

    t2 = time.perf_counter()
    try:
        outcome = enforce.generate_with_citations(query, evidence_map)
    except Exception as exc:
        out["generation_error"] = str(exc)
    else:
        text = outcome["answer"]
        if gate["decision"] == "answer_with_warning":
            text = f"{text} {safety.WARNING_TEXT}"
        out.update(
            answer=text,
            cited_evidence_ids=outcome["cited_evidence_ids"],
            retried=outcome["retried"],
            dropped_sentences=outcome["dropped_sentences"],
        )
    out["timings_ms"]["generation"] = round((time.perf_counter() - t2) * 1000, 1)
    return out
