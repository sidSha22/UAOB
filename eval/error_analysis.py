"""Error analysis for the hybrid and graph retrievers (spec section 6.7).

For every scored question where a mode finds no gold evidence in its top-10,
re-runs retrieval and records, per gold record, the best rank it reached in
each component list (depth 30) and in the fused list. Each failure is then
assigned ONE category by rule, so the breakdown is reproducible:

  gold_unreachable      gold appears in no component list within depth 30
  graph_not_linked      the graph component returned nothing (no entity linked)
  fusion_dilution       gold is in a component's top-10 but lost in fusion
  below_cutoff          gold is in a component list, but only below rank 10
  gold_is_aggregate     (graph only) gold lists need the whole fleet, the
                        returned top-10 holds supporting examples only

Usage:  python eval/error_analysis.py
Writes artifacts/error_analysis_<timestamp>.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "retrieval"))
sys.path.insert(0, str(_ROOT / "graph"))
import router  # noqa: E402

K = 10
DEPTH = 30


def best_ranks(results, gold):
    ranks = {}
    for rank, r in enumerate(results, start=1):
        if r["source_id"] in gold and r["source_id"] not in ranks:
            ranks[r["source_id"]] = rank
    return ranks


def categorize(mode, gold, component_ranks, fused_ranks, graph_nonempty, n_gold):
    if mode == "graph":
        if not graph_nonempty:
            return "graph_not_linked"
        if n_gold > K:
            return "gold_is_aggregate"
        return "gold_unreachable"
    if not any(component_ranks.values()):
        return "gold_unreachable"
    if any(min(r.values()) <= K for r in component_ranks.values() if r):
        return "fusion_dilution"
    return "below_cutoff"


def main():
    artifacts = sorted((_ROOT / "artifacts").glob("eval_*.json"))
    source = artifacts[-1]
    data = json.loads(source.read_text(encoding="utf-8"))
    questions = json.loads((_ROOT / "questions" / "questions.json").read_text(encoding="utf-8"))["questions"]
    by_id = {q["id"]: q for q in questions}

    report = []
    for mode in ("hybrid", "graph"):
        failures = [r for r in data["per_question"][mode]
                    if r["stratum"] != "unanswerable" and r["rank_of_first_hit"] is None]
        for row in failures:
            q = by_id[row["id"]]
            gold = set(q["gold_evidence"])
            out = router.search_detailed(q["text"], mode="hybrid", k=K, depth=DEPTH)
            comp = out["component_results"]
            component_ranks = {name: best_ranks(res, gold) for name, res in comp.items()}
            fused_ranks = best_ranks(out["results"], gold)
            graph_nonempty = bool(comp.get("graph"))
            if mode == "graph":
                graph_nonempty = bool(router.search_detailed(q["text"], mode="graph", k=K)["results"])
            category = categorize(mode, gold, component_ranks, fused_ranks, graph_nonempty, len(gold))
            report.append({
                "mode": mode, "id": q["id"], "stratum": q["stratum"], "question": q["text"],
                "n_gold": len(gold), "graph_plan": out.get("graph_plan"),
                "best_gold_rank_by_component": {c: (min(r.values()) if r else None)
                                                for c, r in component_ranks.items()},
                "category": category,
            })

    print(f"source: {source.name}\n")
    for mode in ("hybrid", "graph"):
        rows = [r for r in report if r["mode"] == mode]
        print(f"{mode}: {len(rows)} failed question(s)")
        for r in rows:
            print(f"  {r['id']} [{r['stratum']}] plan={r['graph_plan']} n_gold={r['n_gold']} "
                  f"best gold rank per component={r['best_gold_rank_by_component']} -> {r['category']}")
            print(f"      {r['question']}")
        print()

    out_path = _ROOT / "artifacts" / f"error_analysis_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps({"source": source.name, "k": K, "depth": DEPTH, "failures": report}, indent=2),
                        encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
