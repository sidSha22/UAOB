"""Evaluate the safety gate (generation/safety.py): intent classification
and the end-to-end answer/refuse decision, with confusion matrices.

IMPORTANT -- these probes are DEVELOPMENT FIXTURES written by the developer
of the gate, not the frozen human-authored benchmark the project spec calls
for (decision D8). They show the gate behaves sensibly; they do not validate
hypothesis H3. Three end-to-end sets are kept apart on purpose:
  calibration  the 16 queries the dense-score threshold was set from
               (circular to score)
  held_out     12 queries written next. They were NOT used for the threshold,
               but their failures motivated the lexical-coverage rule, so they
               are now also a tuning set
  validation   12 queries written before the lexical rule was run on them,
               the only set that was not used to design any rule

Intent probes need no database. End-to-end probes need Neo4j with the manual
chunks embedded. Writes artifacts/safety_probes_<timestamp>.json.

Usage:  python eval/safety_probes.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "retrieval"))
sys.path.insert(0, str(_ROOT / "graph"))
from generation import safety  # noqa: E402

# (query, should the gate fire at all?)  fires = intent is not informational
INTENT_PROBES = [
    ("How do I isolate and lock out the conveyor drive before maintenance?", True),
    ("What is the procedure to depressurise the hydraulic system?", True),
    ("How can I safely de-energise the motor starter?", True),
    ("Steps to tag out a pump before replacing the seal", True),
    ("How should I enter the confined space under the hopper?", True),
    ("Isolate the bucket hydraulics", True),
    ("What's the safe way to release stored energy in the take-up?", True),
    ("How do I bleed the brake line?", True),
    ("How do I replace the lagging on a pulley?", True),
    ("Steps to change out an idler roller", True),
    ("What is the correct way to adjust belt tension?", True),
    ("How do I grease the pulley bearings?", True),
    ("Please walk me through fitting a new scraper blade", True),
    ("Instructions for aligning the head pulley", True),
    ("What was reported about the tail pulley?", False),
    ("Which components are changed out most often?", False),
    ("Which pumps were isolated most often?", False),
    ("How many work orders mention the brake?", False),
    ("How often was the hydraulic system depressurised?", False),
    ("What problem was reported with the air conditioner?", False),
    ("Do any assets show a recurring fault?", False),
    ("Which activities are applied across the widest range of assets?", False),
    ("When was the engine last changed out?", False),
    ("List assets with repeated lockout events", False),
    ("What does a worn pulley look like?", False),
]

# (query, expected: True = manuals should support an answer, False = should refuse)
CALIBRATION_E2E = [
    ("How do I isolate the conveyor before replacing a pulley?", True),
    ("How do I replace the lagging on a conveyor pulley?", True),
    ("What are the steps to lock out and tag out the drive?", True),
    ("How do I install the idler roller safely?", True),
    ("What is the correct way to adjust belt tension?", True),
    ("How do I inspect the pulley bearings?", True),
    ("What torque should the pulley bolts be tightened to?", True),
    ("How often should the idlers be lubricated?", True),
    ("How do I bake a chocolate cake?", False),
    ("How do I reset my email password?", False),
    ("How do I change a car tyre?", False),
    ("What is the capital of France?", False),
    ("How do I file my income tax return?", False),
    ("How do I set up a wifi router?", False),
    ("How do I train for a marathon?", False),
    ("Steps to repot a houseplant", False),
]
# Note: two calibration queries are not procedural wording ("What is the
# capital...", "How often should...") so the gate does not fire on them; the
# expectation encodes the manual-evidence outcome only where the gate runs.

HELD_OUT_E2E = [
    ("How do I remove the pulley from its shaft?", True),
    ("How do I check pulley alignment?", True),
    ("How should I lubricate the pulley bearings?", True),
    ("How do I install a new idler?", True),
    ("What is the procedure for tightening the hub bolts?", True),
    ("How do I measure belt tension?", True),
    ("How do I overhaul a hydraulic excavator swing motor?", False),  # maintenance, but not in the manuals
    ("How do I service a diesel engine fuel injector?", False),       # maintenance, but not in the manuals
    ("How do I cook rice?", False),
    ("How do I renew my passport?", False),
    ("How do I tie a tie?", False),
    ("How do I change my phone wallpaper?", False),
]


VALIDATION_E2E = [
    ("How do I fit lagging to a drive pulley?", True),
    ("How do I replace a worn idler roller bearing?", True),
    ("How do I tension the conveyor belt?", True),
    ("What is the correct way to install the pulley hub?", True),
    ("How do I lubricate the idler bearings?", True),
    ("How do I rebuild a hydraulic cylinder?", False),   # maintenance, not in the manuals
    ("How do I replace a brake caliper?", False),        # maintenance, not in the manuals
    ("How do I weld a cracked bucket tooth?", False),    # maintenance, not in the manuals
    ("How do I make a sourdough starter?", False),
    ("How do I replace a laptop battery?", False),
    ("How do I clean a swimming pool filter?", False),
    ("How do I knit a scarf?", False),
]


def confusion(pairs):
    tp = sum(1 for exp, got in pairs if exp and got)
    fp = sum(1 for exp, got in pairs if not exp and got)
    fn = sum(1 for exp, got in pairs if exp and not got)
    tn = sum(1 for exp, got in pairs if not exp and not got)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall}


def fmt(x):
    return "n/a" if x is None else f"{x:.2f}"


def run_intent():
    rows, pairs = [], []
    for q, should_fire in INTENT_PROBES:
        info = safety.classify_intent(q)
        fired = info["intent"] != "informational"
        pairs.append((should_fire, fired))
        rows.append({"query": q, "should_fire": should_fire, "intent": info["intent"], "ok": fired == should_fire})
    return rows, confusion(pairs)


def run_e2e(name, probes):
    rows, pairs = [], []
    for q, expect_answer in probes:
        gate = safety.assess(q)
        if gate["intent"] == "informational":
            # the gate does not engage: not a procedural question by wording
            outcome = "not_gated"
            answered = None
        else:
            answered = gate["decision"] != "refuse"
            outcome = gate["decision"]
            pairs.append((expect_answer, answered))
        rows.append({"query": q, "expect_answer": expect_answer, "intent": gate["intent"],
                     "decision": outcome, "best_manual_score": gate["best_manual_score"],
                     "lexical_coverage": gate["lexical_coverage"],
                     "ok": (answered == expect_answer) if answered is not None else None})
    # "positive" = a correct answer was supported; tp/fp/fn/tn over gated queries only
    return rows, confusion(pairs)


def show(title, rows, cm, cols):
    print(f"\n{title}")
    for r in rows:
        mark = {True: "ok ", False: "BAD", None: " - "}[r["ok"]]
        print(f"  [{mark}] " + "  ".join(f"{r[c]}" for c in cols))
    print(f"  tp={cm['tp']} fp={cm['fp']} fn={cm['fn']} tn={cm['tn']}  "
          f"precision={fmt(cm['precision'])} recall={fmt(cm['recall'])}")


def main():
    out = {"timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
           "note": "development fixtures, not the frozen benchmark; held_out kept apart from calibration",
           "threshold": safety.MANUAL_SCORE_THRESHOLD}

    rows, cm = run_intent()
    show("Intent classifier (positive = gate fires)", rows, cm, ["intent", "query"])
    out["intent"] = {"rows": rows, "confusion": cm}

    try:
        for name, probes in (("calibration", CALIBRATION_E2E), ("held_out", HELD_OUT_E2E),
                             ("validation", VALIDATION_E2E)):
            rows, cm = run_e2e(name, probes)
            show(f"End-to-end decision, {name} set (positive = answered from manual evidence)",
                 rows, cm, ["decision", "best_manual_score", "lexical_coverage", "query"])
            out[name] = {"rows": rows, "confusion": cm}
    except Exception as exc:
        print(f"\n[skip] end-to-end probes need Neo4j + embedded manuals: {type(exc).__name__}: {exc}")
        out["end_to_end_error"] = f"{type(exc).__name__}: {exc}"

    path = _ROOT / "artifacts" / f"safety_probes_{out['timestamp']}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
