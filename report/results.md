# UAOB: results, caveats and open work

Everything below comes from files in `artifacts/` and can be regenerated with
the commands shown. Nothing here is estimated by hand.

## 1. Status against the project objectives

| Objective | Status | Evidence |
|---|---|---|
| O1 Knowledge graph from 5,000+ real work orders, ISO 14224 / ISO 15926 mapping, 3+ manuals | **Partial.** 8,076 work orders and 3 manuals are in. **No ISO 14224 failure-mode codes and no ISO 15926 asset classes.** | `ingest/load_maintie.py`, `ingest/chunk_manuals.py` |
| O2 BM25, dense and graph-augmented hybrid behind one service | **Done** | `retrieval/`, `api/main.py` |
| O3 Citation-enforced, safety-gated generation | **Done in code; gate validated on developer fixtures only** | `generation/`, section 4 |
| O4 RUL predictions as graph nodes | **Not started.** Needs the NASA C-MAPSS download and a trained model. | none |
| O5 100+ human-authored questions, RAGAS, 40+ case error analysis | **Not met.** 24 questions (21 scored), no RAGAS, 6 failures analysed. | section 3 and 6 |

## 2. Retrieval results

Question set: `questions/questions.json`, 24 questions (12 single-hop, 9
multi-hop, 3 unanswerable). Scores use the 21 answerable ones. Source:
`artifacts/eval_20261009T015501Z.json` (`python eval/harness.py`).

| Mode | Recall@10 single | multi | overall | nDCG@10 overall | Hit rate overall |
|---|---|---|---|---|---|
| BM25 | 61.5% | 0.0% | 35.1% | 0.259 | 42.9% |
| Dense | 41.7% | 2.8% | 25.0% | 0.144 | 33.3% |
| Graph | 81.2% | 23.6% | 56.5% | 0.369 | 95.2% |
| Hybrid (RRF) | 66.7% | 22.2% | 47.6% | 0.436 | 76.2% |

Paired bootstrap on Recall@10 (10,000 resamples, 95% interval,
`python eval/bootstrap.py`):

| Difference | Overall | 95% interval | Excludes 0? |
|---|---|---|---|
| Hybrid minus Dense | +0.226 | [+0.095, +0.381] | yes |
| Hybrid minus BM25 | +0.125 | [+0.048, +0.238] | yes |
| Graph minus Dense | +0.315 | [+0.161, +0.488] | yes |
| Graph minus BM25 | +0.214 | [+0.101, +0.351] | yes |
| Hybrid minus Graph | -0.089 | [-0.208, +0.000] | no (boundary) |

Warm retrieval latency per query, one warm-up query excluded:

| Mode | p50 | p95 |
|---|---|---|
| BM25 | 0.5 ms | 0.6 ms |
| Dense | 34.0 ms | 47.7 ms |
| Graph | 5.0 ms | 10.8 ms |
| Hybrid | 42.7 ms | 64.1 ms |

End-to-end answers are far slower: on this laptop (RTX 4060, 16 GB RAM, Gemma
12B Q4 through llama.cpp) one cited answer took 70 to 190 seconds, almost all of
it generation. Answer latency is not yet measured as p50/p95.

**Reading the table.** The graph and hybrid retrievers clearly beat the text
baselines, mostly on multi-hop questions that need counting across work orders.
Hybrid did not beat graph on its own (the difference is indistinguishable from
zero), so on this data fusion cost slightly more than it gained. The spec's
first hypothesis, that hybrid beats dense on multi-hop questions, holds: the
multi-hop interval is [+0.042, +0.417].

## 3. Error analysis

`python eval/error_analysis.py` re-runs every failed question and records where
the gold records ranked in each component. Hybrid fails 5 of 21, graph fails 1.

| Question | Category | What happened |
|---|---|---|
| q-001 tail pulley lookup | fusion dilution | Graph ranked the gold record 2nd; BM25 and dense lists agreed on other records and pushed it out of the fused top 10. |
| q-008 work on the bucket | fusion dilution | Graph ranked gold 4th; same cause. |
| q-017 parts of larger assemblies | below cutoff | Graph found gold only at rank 20. |
| q-018 recurring faults | below cutoff | Graph found gold only at rank 18. |
| q-019 activities across the widest range of assets | unreachable | No retriever found any gold record; the question needs a spread count the planner does not compute. Fails in graph mode too. |

Two practical consequences. The fusion weights in `retrieval/router.py` favour
text agreement over the graph for plain lookups, and the graph component placed
gold at ranks 18 and 20 for the two below-cutoff questions, so a deeper cutoff
might recover them. Neither change was made or tested, because the numbers
would then be tuned on the same 24 questions they are scored on.

Only 6 failures exist, so the spec's target of 40 analysed cases cannot be met
with this question set.

## 4. Safety gate

`generation/safety.py` decides whether a procedural question may be answered.
Questions that merely mention a safety word ("Which pumps were isolated?") are
not gated. A procedural question is answered only when manual evidence reaches
a dense score of 0.81 and shares at least 20% of the question's content words;
otherwise the LLM is never called and the user is pointed to the closest manual
sections and the site procedure. Safety-critical answers get a fixed warning
line that the model does not write.

Results from `python eval/safety_probes.py`, on **developer-written fixtures,
not the frozen benchmark**:

| Set | Result | Note |
|---|---|---|
| Intent classifier, 25 probes | 14 of 14 gated, 11 of 11 passed through | no database needed |
| Calibration, 16 queries | 13 of 13 gated correct | used to set the 0.81 threshold, so circular |
| Held-out, 12 queries | 12 of 12 | dense score alone let 3 irrelevant questions through (a tie, an excavator motor, a diesel injector); this motivated the coverage rule, so the set is now also a tuning set |
| Validation, 12 queries | 11 of 12 | written before the coverage rule ran on it; one irrelevant question passed ("How do I clean a swimming pool filter?", score 0.835, coverage 0.25). Reported, not retuned. |

What the gate does and does not guarantee. It checks that the manuals are on
topic. It does not check that they contain a procedure. The manual text
retrieved for the lagging question is a troubleshooting table ("pulley lagging
worn: replace worn pulley lagging") with no replacement steps, so the question
"How do I replace the lagging on a conveyor pulley?" passes the gate (score
0.92) and the model then correctly answers that the evidence has no
instructions. The citation check is the second layer that keeps the model from
inventing steps.

Another finding that predates the gate: before it, hybrid retrieval returned no
manual chunks at all for procedural questions, because 176 manual chunks sit
among 8,076 work-order fragments. The gate path now retrieves from the manuals
directly.

## 5. What the system does end to end

Five questions were run through the real pipeline with the local LLM: two record
questions (both answered with citations), one procedural question that passed
the gate (the model honestly reported no instructions), one safety-critical
question (answer plus the fixed warning), and one off-topic question (refused
instantly with manual pointers, no LLM call).

## 6. Caveats

- **Tuned and scored on the same questions.** The hybrid weights were chosen on
  the 24 questions in the table above. Graph and hybrid numbers are optimistic.
- **Gold evidence and graph retrieval share a source.** Aggregate graph plans
  count over gold-annotated work orders because the gold answers were built the
  same way, which favours graph mode.
- **Small sample.** 21 scored questions give direction and rough size only.
- **The committed BM25 row changed.** The first full run in PR #1 reported
  BM25 overall recall of 29.8%; a fresh index gives 35.1%. The earlier figure
  most likely used a stale index.
- **Unanswerable questions are not gated.** Only procedural questions pass
  through the gate. For other unanswerable questions the system relies on the
  prompt rule that the model must say "The evidence does not contain...".
  Spec hypothesis H4 is untested.
- **Thin gate margin.** The thresholds rest on a few dozen hand-written queries.

## 7. Open work and decisions needed from the project owner

| Item | Why it is not done | What is needed |
|---|---|---|
| 100+ question benchmark | The spec requires human-authored questions with a second person verifying (decision D8). Generated questions would invalidate it. | Two people write and cross-check the questions. |
| ISO 14224 failure modes, ISO 15926 asset classes | Needs the MWO2KG mapping data. Writing the codes from memory would be inventing a standard. | Permission to clone the MWO2KG repository and check its licence. |
| RUL prediction nodes (O4) | Needs the NASA C-MAPSS dataset and a trained model. | Permission to download the dataset (size and source to be confirmed). |
| RAGAS faithfulness and answer relevance | Needs a judge LLM and run time; `ragas` is in the frozen stack. | Go-ahead to install it and spend about an hour of local LLM time. |
| Work-order to manual-section links (G3) | The corpus has no such links; the spec proposes synthetic records, which it forbids as test evidence. | A decision on whether to generate flagged synthetic records. |
| Re-tuning fusion weights and cutoff | Would need a held-out question set to be meaningful. | The 100-question benchmark above. |
