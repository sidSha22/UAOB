"""Paired bootstrap over per-question scores (spec section 6.7).

For each pair of retrievers and each question stratum, resamples the
questions with replacement and reports the mean difference in a metric with a
95% percentile interval. With only a couple of dozen questions the intervals
are wide: read them as direction and rough size, not as significance tests.

Usage:
  python eval/bootstrap.py                     # latest eval_*.json, recall_at_10
  python eval/bootstrap.py --metric ndcg_at_10 --artifact artifacts/eval_X.json
Writes artifacts/bootstrap_<artifact timestamp>.json next to the artifact.
"""

import argparse
import json
import random
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = _ROOT / "artifacts"

PAIRS = [("hybrid", "dense"), ("hybrid", "bm25"), ("graph", "dense"),
         ("graph", "bm25"), ("hybrid", "graph")]
STRATA = ["single_hop", "multi_hop", "overall"]
RESAMPLES = 10000
SEED = 1234


def latest_artifact():
    files = sorted(ARTIFACTS.glob("eval_*.json"))
    if not files:
        raise SystemExit("No artifacts/eval_*.json found; run eval/harness.py first.")
    return files[-1]


def paired_scores(per_question, a, b, stratum, metric):
    rows_a = {r["id"]: r for r in per_question[a]}
    rows_b = {r["id"]: r for r in per_question[b]}
    diffs = []
    for qid, ra in rows_a.items():
        if ra["stratum"] == "unanswerable":
            continue
        if stratum != "overall" and ra["stratum"] != stratum:
            continue
        diffs.append(ra[metric] - rows_b[qid][metric])
    return diffs


def bootstrap_ci(diffs, rng):
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(RESAMPLES))
    return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--artifact", default=None)
    parser.add_argument("--metric", default="recall_at_10", choices=["recall_at_10", "ndcg_at_10"])
    args = parser.parse_args()

    path = Path(args.artifact) if args.artifact else latest_artifact()
    data = json.loads(path.read_text(encoding="utf-8"))
    per_q = data["per_question"]
    rng = random.Random(SEED)

    print(f"{path.name}  metric={args.metric}  resamples={RESAMPLES}  seed={SEED}")
    print(f"\n{'A vs B':<18}{'stratum':<12}{'n':>3}{'mean diff':>11}   95% CI           includes 0?")
    results = []
    for a, b in PAIRS:
        if a not in per_q or b not in per_q:
            continue
        for stratum in STRATA:
            diffs = paired_scores(per_q, a, b, stratum, args.metric)
            if not diffs:
                continue
            mean = sum(diffs) / len(diffs)
            lo, hi = bootstrap_ci(diffs, rng)
            crosses = lo <= 0 <= hi
            print(f"{a + ' - ' + b:<18}{stratum:<12}{len(diffs):>3}{mean:>+11.3f}   [{lo:+.3f}, {hi:+.3f}]   {'yes' if crosses else 'NO'}")
            results.append({"a": a, "b": b, "stratum": stratum, "n": len(diffs),
                            "mean_diff": mean, "ci_low": lo, "ci_high": hi, "includes_zero": crosses})

    out = ARTIFACTS / f"bootstrap_{data['timestamp']}_{args.metric}.json"
    out.write_text(json.dumps({"source": path.name, "metric": args.metric,
                               "resamples": RESAMPLES, "seed": SEED, "results": results}, indent=2),
                   encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
