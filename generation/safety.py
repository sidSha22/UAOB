"""Safety gate for procedural maintenance questions (spec O3 / gap G4).

Two parts, both deterministic and auditable (no LLM involved):

1. Intent classifier. A rule-based check of the question's wording:
     safety_critical  instructional wording AND a safety term
                      ("How do I isolate and lock out the drive?")
     procedural       instructional wording only
                      ("How do I replace the pulley lagging?")
     informational    everything else, including questions that merely
                      mention a safety term ("Which pumps were isolated?")

2. Evidence gate. A procedural question may only be answered from equipment
   manuals, never from work-order fragments (a work order records that
   something was done, not how to do it safely). The gate looks for manual
   chunks whose dense score reaches MANUAL_SCORE_THRESHOLD:
     enough manual evidence -> "answer" (procedural) or
                               "answer_with_warning" (safety_critical)
     otherwise              -> "refuse": no instructions are generated and
                               the user is pointed to the closest manual
                               sections and to their site procedure.

Both thresholds are PROVISIONAL. MANUAL_SCORE_THRESHOLD came from 16
hand-written queries (lowest on-topic best-manual score 0.822, highest
off-topic 0.803, a thin margin); MIN_LEXICAL_COVERAGE was added after a second
set of 12 showed dense score alone letting three irrelevant questions
through. Re-measure both whenever the manual set or the embedding model
changes (see eval/safety_probes.py). The score is the Neo4j vector-index
score for a cosine index, i.e. (1 + cosine) / 2.
"""

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "retrieval"))

MANUAL_SCORE_THRESHOLD = 0.81
MIN_LEXICAL_COVERAGE = 0.2
MANUAL_CANDIDATE_DEPTH = 300  # manuals are ~2% of chunks; look deep before filtering
MIN_ALPHA_RATIO = 0.6  # below this a chunk is table-of-contents / layout noise

WARNING_TEXT = (
    "Safety notice: confirm these steps against the current equipment manual and "
    "your site's isolation / lockout-tagout procedure before starting work. This "
    "tool does not replace a permit-to-work."
)

_SAFETY_RE = re.compile(
    r"\b(isolat\w*|lock[- ]?out|lock[- ]?off|tag[- ]?out|loto|"
    r"de-?energi[sz]\w*|re-?energi[sz]\w*|energi[sz]\w*|"
    r"(?:de)?pressuri[sz]\w*|confined[- ]space|hot[- ]work|"
    r"work(?:ing)? at heights?|stored energy|"
    r"release (?:the )?(?:pressure|tension|stored energy)|"
    r"bleed\w*|high[- ]voltage|permit[- ]to[- ]work)\b",
    re.IGNORECASE,
)
_INSTRUCTIONAL_RE = re.compile(
    r"\b(how (?:do|can|should|would|could|to|does one)\b|how-to|"
    r"steps? (?:to|for|of)|procedures?|instructions?|walk me through|"
    r"guide (?:me )?(?:to|through)|"
    r"what(?:'s| is| are) the (?:correct |safe |proper |right )?(?:way|method|process) (?:to|of|for)|"
    r"safely|can i|should i|do i need to)\b",
    re.IGNORECASE,
)
_IMPERATIVE_START_RE = re.compile(
    r"^\s*(?:please\s+)?(?:isolate|lock[- ]?out|tag[- ]?out|de-?energi[sz]e|energi[sz]e|"
    r"(?:de)?pressuri[sz]e|drain|bleed|release|disconnect|dismantle|disassemble|"
    r"remove|replace|install|fit|adjust|tension|align|lubricate|grease)\b",
    re.IGNORECASE,
)


def classify_intent(query):
    """Returns {intent, safety_terms, instructional}. See module docstring."""
    q = (query or "").strip()
    safety_terms = sorted({m.group(0).lower() for m in _SAFETY_RE.finditer(q)})
    instructional = bool(_INSTRUCTIONAL_RE.search(q) or _IMPERATIVE_START_RE.match(q))
    if instructional and safety_terms:
        intent = "safety_critical"
    elif instructional:
        intent = "procedural"
    else:
        intent = "informational"
    return {"intent": intent, "safety_terms": safety_terms, "instructional": instructional}


def is_noise_chunk(text):
    """Table-of-contents dot leaders, page furniture, number grids."""
    if not text:
        return True
    return sum(c.isalpha() for c in text) / len(text) < MIN_ALPHA_RATIO


_STOPWORDS = frozenset(
    "how do i the a an to of for and or in on at is are be it its my me we you your can "
    "should would could does what which when steps step procedure procedures instructions "
    "instruction correct way proper safe safely please walk through guide new use from with "
    "before after about this that these those if then than there their them".split()
)
_WORD_RE = re.compile(r"[a-z]+")


def _stem(word):
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _normalise(text):
    """Lowercase, split hyphens, and join the two-word safety spellings."""
    return re.sub(r"\b(lock|tag) out\b", r"\1out", text.lower().replace("-", " "))


def lexical_coverage(query, candidates, top=3):
    """Fraction of the question's content words (lightly stemmed) that appear
    in the text of the top manual candidates. None if the question has no
    content words."""
    wanted = {_stem(w) for w in _WORD_RE.findall(_normalise(query))
              if w not in _STOPWORDS and len(w) > 2}
    if not wanted:
        return None
    haystack = " ".join(c["text"] for c in candidates[:top])
    present = {_stem(w) for w in _WORD_RE.findall(_normalise(haystack))}
    return len(wanted & present) / len(wanted)


def manual_candidates(query, k=5, depth=MANUAL_CANDIDATE_DEPTH):
    """Manual-only evidence: dense + BM25 restricted to source_type 'manual'
    (noise chunks removed) and fused with RRF. Returns (results, best_score)
    where best_score is the best dense score among manual chunks (0.0 if none)."""
    import bm25
    import dense
    import fusion

    dense_hits = [r for r in dense.search(query, k=depth)
                  if r["source_type"] == "manual" and not is_noise_chunk(r["text"])]
    bm25_hits = [r for r in bm25.search(query, k=depth)
                 if r["source_type"] == "manual" and not is_noise_chunk(r["text"])]
    fused = fusion.rrf({"dense": dense_hits, "bm25": bm25_hits}, k=k)
    best = dense_hits[0]["score"] if dense_hits else 0.0
    return fused, best


def decide(intent, best_manual_score, coverage=1.0, threshold=MANUAL_SCORE_THRESHOLD,
           min_coverage=MIN_LEXICAL_COVERAGE):
    """Pure decision rule. coverage=None (no content words) counts as 0.
    Returns (decision, reason)."""
    if intent == "informational":
        return "answer", "informational question: no gate applied"
    cov = coverage or 0.0
    evidence = f"best manual score {best_manual_score:.3f}, lexical coverage {cov:.2f}"
    if best_manual_score >= threshold and cov >= min_coverage:
        if intent == "safety_critical":
            return "answer_with_warning", f"safety-critical procedure; manual evidence found ({evidence})"
        return "answer", f"procedural question; manual evidence found ({evidence})"
    return "refuse", (
        f"{intent.replace('_', ' ')} question without sufficient manual evidence "
        f"({evidence}; need score >= {threshold} and coverage >= {min_coverage})")


def refusal_text(candidates):
    pointers = ", ".join(c["source_id"] for c in candidates[:3])
    text = (
        "The evidence does not contain a sufficiently reliable procedure for this task, "
        "so no instructions are given. Consult the equipment manual directly and follow "
        "your site's permit-to-work and isolation / lockout-tagout procedure."
    )
    if pointers:
        text += f" Closest manual sections found: {pointers}."
    return text


def assess(query, k=5):
    """Full gate for one question. Returns {intent, safety_terms, decision,
    reason, best_manual_score, lexical_coverage, thresholds, candidates}. Manual retrieval only
    runs for non-informational questions."""
    info = classify_intent(query)
    candidates, best, coverage = [], 0.0, None
    if info["intent"] != "informational":
        candidates, best = manual_candidates(query, k=k)
        coverage = lexical_coverage(query, candidates)
    decision, reason = decide(info["intent"], best, coverage)
    return {
        "intent": info["intent"],
        "safety_terms": info["safety_terms"],
        "decision": decision,
        "reason": reason,
        "best_manual_score": round(best, 4),
        "lexical_coverage": None if coverage is None else round(coverage, 3),
        "threshold": MANUAL_SCORE_THRESHOLD,
        "min_lexical_coverage": MIN_LEXICAL_COVERAGE,
        "candidates": candidates,
    }
