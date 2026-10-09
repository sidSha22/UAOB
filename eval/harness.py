"""Retrieval-only evaluation harness. Runs every retrieval mode (bm25,
dense, graph, hybrid RRF -- see retrieval/router.py) at k=10 against
questions/questions.json and scores
Recall@10, nDCG@10, and rank-of-first-hit per question, aggregated by
stratum. No LLM, no generation, no API -- retrieval scoring only.

For the unanswerable stratum (empty gold_evidence), Recall/nDCG/rank are
undefined; top-1 score and mean top-10 score are recorded instead, kept
separate from the main metrics table since they're what a confidence
threshold would consume later, not a correctness signal.

Graph "fact" results (source_type 'graph', source_id 'graph::...') are
serialised aggregations, not records; they can never match gold evidence,
so they only cost the graph/hybrid modes a slot -- a conservative choice.

Usage:
  python eval/harness.py                       # all modes
  python eval/harness.py --modes bm25 graph    # a subset
A mode that fails (e.g. dense before the embedding model is downloaded) is
reported and skipped instead of aborting the whole run.
"""

import argparse

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "retrieval"))
import router  # noqa: E402

QUESTIONS_PATH = _ROOT / "questions" / "questions.json"
ARTIFACTS_DIR = _ROOT / "artifacts"

K = 10
MODES = list(router.MODES[1:]) + ["hybrid"]  # bm25, dense, graph, hybrid


def dcg(relevances):
    return sum(r / math.log2(i + 2) for i, r in enumerate(relevances))


def recall_at_k(source_ids, gold_set, k):
    if not gold_set:
        return None
    hit_ids = {sid for sid in source_ids[:k] if sid in gold_set}
    return len(hit_ids) / len(gold_set)


def ndcg_at_k(source_ids, gold_set, k):
    if not gold_set:
        return None
    relevances = [1 if sid in gold_set else 0 for sid in source_ids[:k]]
    dcg_val = dcg(relevances)
    ideal_count = min(len(gold_set), k)
    ideal = [1] * ideal_count + [0] * (k - ideal_count)
    idcg_val = dcg(ideal)
    return dcg_val / idcg_val if idcg_val > 0 else 0.0


def rank_of_first_hit(source_ids, gold_set, k):
    if not gold_set:
        return None
    for i, sid in enumerate(source_ids[:k]):
        if sid in gold_set:
            return i + 1
    return None


def score_question(question, results, k):
    gold_set = set(question["gold_evidence"])
    source_ids = [r["source_id"] for r in results]
    scores = [r["score"] for r in results]

    row = {
        "id": question["id"],
        "stratum": question["stratum"],
        "text": question["text"],
        "gold_evidence": question["gold_evidence"],
        "retrieved": [
            {"chunk_id": r["chunk_id"], "source_id": r["source_id"], "score": r["score"]}
            for r in results
        ],
    }

    if question["stratum"] == "unanswerable":
        row["top1_score"] = scores[0] if scores else None
        row["mean_top10_score"] = sum(scores[:k]) / len(scores[:k]) if scores else None
    else:
        row["recall_at_10"] = recall_at_k(source_ids, gold_set, k)
        row["ndcg_at_10"] = ndcg_at_k(source_ids, gold_set, k)
        row["rank_of_first_hit"] = rank_of_first_hit(source_ids, gold_set, k)

    return row


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def aggregate(rows):
    """Aggregate per-stratum and overall (overall excludes unanswerable,
    since recall/ndcg are undefined there)."""
    by_stratum = {}
    for row in rows:
        by_stratum.setdefault(row["stratum"], []).append(row)

    agg = {}
    scored_strata = ["single_hop", "multi_hop"]
    for stratum in scored_strata:
        strat_rows = by_stratum.get(stratum, [])
        hits = [r for r in strat_rows if r["rank_of_first_hit"] is not None]
        agg[stratum] = {
            "n_questions": len(strat_rows),
            "recall_at_10": mean(r["recall_at_10"] for r in strat_rows),
            "ndcg_at_10": mean(r["ndcg_at_10"] for r in strat_rows),
            "hit_rate": len(hits) / len(strat_rows) if strat_rows else None,
            "mean_rank_of_first_hit": mean(r["rank_of_first_hit"] for r in strat_rows),
        }

    overall_rows = [r for r in rows if r["stratum"] in scored_strata]
    overall_hits = [r for r in overall_rows if r["rank_of_first_hit"] is not None]
    agg["overall"] = {
        "n_questions": len(overall_rows),
        "recall_at_10": mean(r["recall_at_10"] for r in overall_rows),
        "ndcg_at_10": mean(r["ndcg_at_10"] for r in overall_rows),
        "hit_rate": len(overall_hits) / len(overall_rows) if overall_rows else None,
        "mean_rank_of_first_hit": mean(r["rank_of_first_hit"] for r in overall_rows),
    }

    unanswerable_rows = by_stratum.get("unanswerable", [])
    agg["unanswerable"] = {
        "n_questions": len(unanswerable_rows),
        "mean_top1_score": mean(r["top1_score"] for r in unanswerable_rows),
        "mean_top10_score": mean(r["mean_top10_score"] for r in unanswerable_rows),
    }

    return agg


def percentile(values, p):
    """Nearest-rank percentile (never an interpolated, never-observed value)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summarize_latency(latencies_ms):
    return {
        "n": len(latencies_ms),
        "p50_ms": round(percentile(latencies_ms, 50), 1),
        "p95_ms": round(percentile(latencies_ms, 95), 1),
        "mean_ms": round(sum(latencies_ms) / len(latencies_ms), 1),
    }


def print_latency_table(latency_by_mode):
    print("\nWarm end-to-end retrieval latency per query (one warm-up query excluded)")
    print(f"{'mode':<10}{'n':>4}{'p50 ms':>12}{'p95 ms':>12}{'mean ms':>12}")
    for mode, lat in latency_by_mode.items():
        print(f"{mode:<10}{lat['n']:>4}{lat['p50_ms']:>12}{lat['p95_ms']:>12}{lat['mean_ms']:>12}")


def fmt(x, pct=False, decimals=3):
    if x is None:
        return "n/a"
    if pct:
        return f"{x * 100:.1f}%"
    return f"{x:.{decimals}f}"


def print_metric_table(title, agg_by_mode, metric_key, pct=False):
    print(f"\n{title}")
    print(f"{'mode':<10}{'single_hop':>14}{'multi_hop':>14}{'overall':>14}")
    for mode, agg in agg_by_mode.items():
        row = [fmt(agg[s][metric_key], pct=pct) for s in ("single_hop", "multi_hop", "overall")]
        print(f"{mode:<10}{row[0]:>14}{row[1]:>14}{row[2]:>14}")


def print_unanswerable_table(agg_by_mode):
    print("\nUnanswerable stratum -- confidence-threshold inputs (not correctness metrics)")
    print(f"{'mode':<10}{'n':>4}{'mean top-1 score':>20}{'mean top-10 score':>20}")
    for mode, agg in agg_by_mode.items():
        u = agg["unanswerable"]
        print(f"{mode:<10}{u['n_questions']:>4}{fmt(u['mean_top1_score']):>20}{fmt(u['mean_top10_score']):>20}")


def print_failures(rows_by_mode):
    print("\nPer-question failures (no gold hit in top-10, scored strata only)")
    for mode, rows in rows_by_mode.items():
        failed = [r for r in rows if r["stratum"] != "unanswerable" and r["rank_of_first_hit"] is None]
        print(f"\n  {mode}: {len(failed)} failed")
        for r in failed:
            print(f"    {r['id']:8s} [{r['stratum']}] {r['text']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--modes", nargs="+", default=MODES, choices=router.MODES)
    args = parser.parse_args()

    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        questions = json.load(f)["questions"]

    rows_by_mode = {}
    agg_by_mode = {}
    latency_by_mode = {}
    errors = {}
    for mode in args.modes:
        try:
            rows = []
            latencies = []
            # warm-up: loading the embedding model / graph index / BM25 index is a
            # one-off start-up cost, not per-query latency
            router.search_detailed(questions[0]["text"], mode=mode, k=K)
            for q in questions:
                t0 = time.perf_counter()
                out = router.search_detailed(q["text"], mode=mode, k=K)
                latencies.append((time.perf_counter() - t0) * 1000)
                for name, msg in out["errors"].items():
                    errors.setdefault(f"{mode}/{name}", msg)
                rows.append(score_question(q, out["results"], K))
        except Exception as exc:
            errors[mode] = f"{type(exc).__name__}: {exc}"
            print(f"[skip] {mode}: {errors[mode]}")
            continue
        rows_by_mode[mode] = rows
        agg_by_mode[mode] = aggregate(rows)
        latency_by_mode[mode] = summarize_latency(latencies)

    if not agg_by_mode:
        print("No retrieval mode could be evaluated.")
        sys.exit(1)
    if errors:
        print("\nWarnings (components that failed and were skipped):")
        for name, msg in errors.items():
            print(f"  {name}: {msg}")

    print_metric_table("Recall@10", agg_by_mode, "recall_at_10", pct=True)
    print_metric_table("nDCG@10", agg_by_mode, "ndcg_at_10")
    print_metric_table("Hit rate (>=1 gold in top-10)", agg_by_mode, "hit_rate", pct=True)
    print_metric_table("Mean rank of first hit", agg_by_mode, "mean_rank_of_first_hit")
    print_unanswerable_table(agg_by_mode)
    print_latency_table(latency_by_mode)
    print_failures(rows_by_mode)

    ARTIFACTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = ARTIFACTS_DIR / f"eval_{timestamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "k": K,
            "n_questions": len(questions),
            "modes": list(agg_by_mode),
            "errors": errors,
            "aggregate": agg_by_mode,
            "latency": latency_by_mode,
            "per_question": rows_by_mode,
        }, f, indent=2)

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
