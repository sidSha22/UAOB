"""Unit tests for generation/safety.py (pure rules, no database needed).
Run:  python tests/test_safety.py
Plain asserts on purpose: pytest is not in the frozen stack (CONTEXT.md)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from generation import safety  # noqa: E402

CASES = [
    # (query, expected intent)
    ("How do I isolate and lock out the conveyor drive?", "safety_critical"),
    ("What is the procedure to depressurise the hydraulic system?", "safety_critical"),
    ("How can I safely de-energise the motor starter?", "safety_critical"),
    ("Steps to tag out a pump before replacing the seal", "safety_critical"),
    ("Isolate the bucket hydraulics", "safety_critical"),
    ("How do I replace the lagging on a pulley?", "procedural"),
    ("Please walk me through fitting a new scraper blade", "procedural"),
    ("Steps to change out an idler roller", "procedural"),
    # mention a safety term but ask about the record, not for instructions
    ("Which pumps were isolated most often?", "informational"),
    ("How often was the hydraulic system depressurised?", "informational"),
    ("List assets with repeated lockout events", "informational"),
    # plain record questions
    ("What was reported about the tail pulley?", "informational"),
    ("Which components are changed out most often?", "informational"),
    ("How many work orders mention the brake?", "informational"),
    ("", "informational"),
]


def test_intent_classification():
    for query, expected in CASES:
        got = safety.classify_intent(query)["intent"]
        assert got == expected, f"{query!r}: expected {expected}, got {got}"


def test_decision_rule():
    t = safety.MANUAL_SCORE_THRESHOLD
    assert safety.decide("informational", 0.0)[0] == "answer"
    assert safety.decide("procedural", t)[0] == "answer"
    assert safety.decide("procedural", t - 0.001)[0] == "refuse"
    assert safety.decide("safety_critical", t)[0] == "answer_with_warning"
    assert safety.decide("safety_critical", 0.0)[0] == "refuse"
    # no manual evidence at all can never be answered, whatever the intent
    assert safety.decide("procedural", 0.0)[0] == "refuse"
    # a high dense score without lexical support is not enough
    assert safety.decide("procedural", 0.95, coverage=0.0)[0] == "refuse"
    assert safety.decide("procedural", 0.95, coverage=None)[0] == "refuse"
    assert safety.decide("procedural", 0.95, coverage=safety.MIN_LEXICAL_COVERAGE)[0] == "answer"


def test_lexical_coverage():
    cands = [{"text": "Remove the pulley lagging before checking the bearings."}]
    assert safety.lexical_coverage("How do I replace the lagging on a pulley?", cands) >= 0.5
    assert safety.lexical_coverage("How do I tie a tie?", cands) == 0.0
    assert safety.lexical_coverage("How do I?", cands) is None  # no content words
    # spelling variants of the same safety words match
    assert safety.lexical_coverage("lock out", [{"text": "Apply lockout."}]) == 1.0


def test_noise_chunks():
    assert safety.is_noise_chunk("................... 10 replacement parts.................... 12")
    assert safety.is_noise_chunk("")
    assert not safety.is_noise_chunk("Remove the lagging by cutting along the weld line.")


def test_refusal_text_points_to_sources_and_never_gives_steps():
    text = safety.refusal_text([{"source_id": "doc::A::sec0001"}, {"source_id": "doc::B::sec0002"}])
    assert text.startswith("The evidence does not contain")  # recognised as a refusal by the citation enforcer
    assert "doc::A::sec0001" in text and "doc::B::sec0002" in text
    assert "lockout" in text
    assert safety.refusal_text([]).endswith("procedure.")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"[PASS] {t.__name__}")
    print(f"\n{len(tests)}/{len(tests)} tests passed.")


if __name__ == "__main__":
    main()
