"""Prompt assembly: assigns each retrieved chunk a stable evidence ID
(E1, E2, ...) mapped back to its chunk_id, and fills the versioned prompt
templates in generation/prompts/ -- prompts live as files, not inline
strings, so they can be versioned independently of this logic."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = (PROMPTS_DIR / f"system_{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
USER_TEMPLATE = (PROMPTS_DIR / f"user_{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
RETRY_TEMPLATE = (PROMPTS_DIR / f"retry_{PROMPT_VERSION}.txt").read_text(encoding="utf-8")


def build_evidence_map(chunks):
    """chunks: retriever results (chunk_id, text, source_type, source_id,
    score, ...), already in rank order. Returns dict evidence_id ->
    enriched record, E1 = top-ranked result."""
    evidence_map = {}
    for i, c in enumerate(chunks, start=1):
        eid = f"E{i}"
        evidence_map[eid] = {
            "evidence_id": eid,
            "chunk_id": c["chunk_id"],
            "source_type": c["source_type"],
            "source_id": c["source_id"],
            "text": c["text"],
            "score": c["score"],
        }
    return evidence_map


def _format_evidence_block(evidence_map):
    return "\n".join(
        f"({eid}) [{ev['source_type']}] {ev['text']}"
        for eid, ev in evidence_map.items()
    )


def build_messages(query, evidence_map):
    user_msg = USER_TEMPLATE.format(
        evidence_block=_format_evidence_block(evidence_map), query=query
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]


def build_retry_messages(base_messages, previous_answer, problem_sentences):
    retry_msg = RETRY_TEMPLATE.format(
        problem_sentences="\n".join(f"- {s}" for s in problem_sentences)
    )
    return base_messages + [
        {"role": "assistant", "content": previous_answer},
        {"role": "user", "content": retry_msg},
    ]
