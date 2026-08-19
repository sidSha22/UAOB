"""Citation enforcer -- the correctness gate on generated answers.

Splits the answer into sentences. A factual sentence with no citation that
resolves to a real evidence ID is unsupported; pure refusal sentences
("The evidence does not contain...") are exempt. On any unsupported
sentence, retries generation once with an explicit instruction naming the
offending sentences. If sentences are still unsupported after the retry,
they are dropped and a visible note is appended -- an unsupported claim is
never silently passed through.

The local quantized model is expected to drop citation formatting under
pressure sometimes; the enforcer catching that and dropping/retrying is
correct behaviour, not a bug -- this check is intentionally not loosened."""

import re

from generation import llm
from generation import prompt as prompt_assembly

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_CITATION_RE = re.compile(r"\(\s*(E\d+(?:\s*,\s*E\d+)*)\s*\)")
_REFUSAL_PREFIX = "the evidence does not contain"

DROPPED_NOTE = "[Note: part of the answer could not be grounded in the provided evidence and was removed.]"
EMPTY_ANSWER_PROBLEM = (
    "(no answer text was returned -- the response was empty, likely truncated "
    "before it reached the answer)"
)


def split_sentences(text):
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def extract_evidence_ids(sentence):
    ids = []
    for match in _CITATION_RE.finditer(sentence):
        ids.extend(x.strip() for x in match.group(1).split(","))
    return ids


def is_refusal(sentence):
    return sentence.strip().lower().startswith(_REFUSAL_PREFIX)


def classify(sentences, valid_evidence_ids):
    """Returns (supported, unsupported) sentence lists. Supported =
    refusal sentence, or every cited ID resolves to a real evidence item
    and at least one citation is present."""
    supported, unsupported = [], []
    for s in sentences:
        if is_refusal(s):
            supported.append(s)
            continue
        ids = extract_evidence_ids(s)
        if ids and all(i in valid_evidence_ids for i in ids):
            supported.append(s)
        else:
            unsupported.append(s)
    return supported, unsupported


def generate_with_citations(query, evidence_map):
    """Runs generation + citation enforcement (with one retry) against the
    given evidence_map (as built by prompt.build_evidence_map). Returns
    {answer, cited_evidence_ids, retried, dropped_sentences}."""
    valid_ids = set(evidence_map)
    messages = prompt_assembly.build_messages(query, evidence_map)

    answer = llm.chat(messages)
    sentences = split_sentences(answer)
    supported, unsupported = classify(sentences, valid_ids)
    # An empty response (e.g. the model burns its token budget on internal
    # reasoning and never reaches the answer) is unsupported in the most
    # literal sense -- there's no citation because there's no claim at
    # all. Treat it the same way so it can never slip through as a blank
    # "successful" answer.
    empty_response = not sentences

    retried = False
    if unsupported or empty_response:
        retried = True
        problem_sentences = unsupported if unsupported else [EMPTY_ANSWER_PROBLEM]
        retry_messages = prompt_assembly.build_retry_messages(messages, answer, problem_sentences)
        answer = llm.chat(retry_messages)
        sentences = split_sentences(answer)
        supported, unsupported = classify(sentences, valid_ids)
        empty_response = not sentences

    dropped = len(unsupported) + (1 if empty_response else 0)
    final_sentences = list(supported)
    if dropped:
        final_sentences.append(DROPPED_NOTE)
    final_answer = " ".join(final_sentences)

    cited_ids = []
    for s in supported:
        for eid in extract_evidence_ids(s):
            if eid not in cited_ids:
                cited_ids.append(eid)

    return {
        "answer": final_answer,
        "cited_evidence_ids": cited_ids,
        "retried": retried,
        "dropped_sentences": dropped,
    }
