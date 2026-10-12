# Background for documentation: what the code does not tell you

Written for whoever is preparing the project documentation. The code, the
README, `CONTEXT.md`, `DEMO.md` and `report/results.md` cover what the system
does and how to run it. This file covers the rest: the specification the project
follows, why choices were made, how the work actually unfolded, the machine it
was measured on, and the gaps you will hit.

Every number here comes from a file in `artifacts/` or from the loader's output,
and every claim is tagged where it matters:

- **[spec]** comes from the project specification (a document held by the owner,
  not in this repo).
- **[measured]** was measured in this repo; the artifact file is named.
- **[owner]** only the project owner can confirm it. Section 14 collects these.

## 1. What the project is

- **Title [spec]:** AI for Industrial Knowledge Intelligence: Unified Asset &
  Operations Brain (UAOB).
- **Domain [spec]:** Artificial Intelligence, specifically retrieval-augmented
  generation (RAG) and knowledge graphs for maintenance and asset management.
- **Planned shape [spec]:** an 8-week project for a team of 3. The spec calls
  its own scope "frozen".
- **One sentence:** a question-answering system over real maintenance work
  orders and equipment manuals. It searches three ways (keyword, meaning-based,
  knowledge graph), merges the results, and a local language model writes an
  answer in which every sentence cites its evidence. A rule-based gate refuses
  procedural questions the manuals cannot support.

## 2. The specification, and how it maps to a report

The spec is a single "master" document. It was written so each assessed report
section can be lifted from a matching section of it. The sections it names:

| Report component [spec] | Spec section |
|---|---|
| Literature review and rationale | 2 (five literature threads, each ending in "what the project takes") |
| Gap identification | 3 (gaps G1 to G4, plus declared non-goals) |
| Objective framing | 4 (objectives O1 to O5, contribution claims C1 to C4) |
| Project plan | 10 (8-week plan, milestones M1 to M8), 11 (risk register R1 to R8) |
| Design and methodology | 5 (architecture), 6 (methodology), Appendix B (graph schema) |
| Datasets and evaluation | 7, 8 |
| Implementation and analysis | 6.7 (analysis method), plus this repo's `report/results.md` |

The spec also gives a rule of thumb that explains many decisions: effort follows
marks, so frontend polish was deliberately de-prioritised (decision D4) in favour
of methodology and evaluation.

### Gaps the project argues it addresses [spec]

| | Gap | Status in the build |
|---|---|---|
| G1 | LLM knowledge graphs ignore industrial standards (ISO 14224, ISO 15926) | **Not addressed.** The schema is MaintIE's. ISO mappings are not built. |
| G2 | Nobody has tested *when* a graph layer helps on maintenance data | **Addressed**, on a small development set (section 9). |
| G3 | Manuals describe the asset as designed; work orders record it as maintained, and no system joins them | **Not addressed.** The corpus has no work-order-to-manual links. |
| G4 | LLM answers to procedural questions are ungated in a safety-critical domain | **Addressed**, with the safety gate. |

### Objectives and contribution claims [spec]

| | Objective | Due (spec week) | Status |
|---|---|---|---|
| O1 | Graph from 5,000+ real work orders, ISO 14224 and ISO 15926 mapping, 3+ manuals | 3 | **Partial.** 8,076 work orders and 3 manuals. No ISO mapping. |
| O2 | BM25, dense and graph-hybrid retrieval behind one service | 5 | **Done** |
| O3 | Citation-enforced, safety-gated generation | 6 | **Done in code.** Gate checked on developer-written fixtures only. |
| O4 | Remaining-useful-life predictions as graph nodes | 6 | **Not started** |
| O5 | 100+ human-authored questions, RAGAS, 40+ case error analysis | 7 | **Not met** (24 questions, no RAGAS, 6 failures analysed) |

Claims the spec says the project will make: **C1** a standards-grounded graph
pipeline (only partly true, see G1), **C2** hybrid RAG with citation enforcement
and safety gating (true), **C3** the first question-type-stratified ablation of
graph versus conventional retrieval on maintenance data (true for the small set
in section 9; "first" is the spec's claim and is not checked here), **C4** a
reusable human-authored test set (not met). Write C1 and C4 as future work.

### Hypotheses stated in the spec, and where they stand

| | Hypothesis | Where it stands |
|---|---|---|
| H1 | Hybrid beats dense on multi-hop questions | Supported on the dev set: +0.194 recall, 95% interval [+0.042, +0.417] [measured] |
| H2 | Hybrid is about equal to dense on single-hop | Hybrid is *better*: +0.250 [+0.062, +0.458]. The point of H2 (the graph does not hurt) holds. |
| H3 | The gate fires on 7 of 8 safety probes with at most 2 false positives | The intent classifier gated 14 of 14 and passed 11 of 11 non-procedural probes, but the probes were written by the gate's developer, so this does not validate H3. |
| H4 | On unanswerable questions hybrid refuses more often than baselines | **Untested.** The three unanswerable questions were never run through the model. |

## 3. Frozen decisions, and the reasoning

The spec froze eight decisions. Changing one needs all team members' agreement.

| | Decision [spec] | Why |
|---|---|---|
| D1 | Primary corpus is MaintIE; two other candidate datasets were dropped | Depth on one asset domain beats breadth over unrelated files |
| D2 | Schema is MaintIE with ISO 14224 and ISO 15926 on top. No LLM-invented schema | Auditable in industry vocabulary |
| D3 | Neo4j is the only store, for graph and vectors | One service to run and keep consistent |
| D4 | Streamlit for the UI, polish de-prioritised | Marks follow methodology |
| D5 | Remaining-useful-life is a commodity component, not a contribution | Hundreds of published results exist already |
| D6 | Question set frozen at the end of week 4 | Prevents tuning to the test |
| D7 | Feature freeze at the end of week 5 | Leaves time to measure |
| D8 | Evaluation questions must be human-written against real records | Synthetic text may extend the corpus but never be the answer key |

### Decisions made during the build, and why

| Decision | Reason |
|---|---|
| Use the corpus's existing annotations; train nothing | The MaintIE repo ships training code but **no trained models**, so the spec's assumption of "released models" was wrong. Extraction for *unannotated* text was deferred. |
| Merge name variants into one node and keep every spelling as an alias | Otherwise "pump" and "pump assembly" count as different equipment |
| Add new edge types (`HAS_PART`, `IS_A`, `HAS_PARTICIPANT_PATIENT`, `HAS_PARTICIPANT_AGENT`, `CONTAINS`) | The spec's edge list had no place for MaintIE's own relations |
| Add a `MENTIONS` link from work order to equipment, deliberately not `ON_ASSET` | Without it "work orders for this asset" cannot be answered. `ON_ASSET` would overclaim: it means the order is *about* that asset, not merely mentions it. |
| Leave process and property nodes unconnected | Owner's instruction |
| Rule-based graph planner, not an LLM | Deterministic and auditable |
| Aggregate over gold-annotated work orders by default | Silver labels are model-made and noisier |
| Fuse with Reciprocal Rank Fusion, weighting the graph more on counting questions | Rank-based, so no score normalisation across retrievers |
| Run the language model locally | Privacy, cost, no outage risk. The spec preferred a hosted API with a local fallback; this build is local-first. |
| Switch the model's "thinking" off | Cut answers from 70 to 190 s down to about 10 to 25 s |
| Safety gate is rules plus two evidence checks and refuses *before* the model | Refusal must be certain, not left to the model |
| Two gate checks, not one | A dense score alone let "How do I tie a tie?" (0.811) pass |
| Do not retune the gate on the fresh validation set | Reported its one miss instead |
| Plain assert-based tests, no pytest | pytest is not in the frozen stack |
| Do not invent ISO codes or benchmark questions | They need the MWO2KG data and human authors respectively |

## 4. Specified versus built

| Area | Spec | As built |
|---|---|---|
| Graph retrieval | k-hop expansion along typed edges, then path serialisation | A rule-based planner (7 plans) that counts over an in-memory copy of the graph and returns a written "graph fact" plus supporting work orders |
| Fusion | RRF baseline, weighted variant as an ablation arm | RRF with query-aware weights (graph x3 on aggregate questions, x2 on lookups) used by default |
| Safety gate | Classifier plus a retrieval-confidence threshold | Classifier plus a manual-evidence score threshold (0.81) plus a word-overlap check (0.2) |
| Work-order fields | `wo_id`, `date`, `raw_text`, `norm_text` | `id`, `text`, `annotation_quality`, `date` (always null), `normalisation`. The corpus masks ids and dates and ships only normalised text. |
| Entity fields | `surface_form`, `canonical` | `aliases` list |
| LLM | Hosted API, local model as fallback | Local llama.cpp model |
| Docker Compose | api, neo4j and ui | Neo4j only; the API and app run directly on the host |
| Python | 3.11 | 3.12 on the development machine |
| Evaluation | 100+ questions, RAGAS, bootstrap, 40+ error cases | 24 questions, bootstrap, 6 failures analysed, no RAGAS |
| Timeline | Weekly milestones over 8 weeks | Not followed (section 6) |

## 5. Data

**Work orders: MaintIE** (public, MIT licence, `github.com/nlp-tlp/maintie`;
LREC-COLING 2024). Short maintenance notes from real equipment.

| | Gold | Silver | Total |
|---|---|---|---|
| Records | 1,076 | 7,000 | 8,076 |
| Annotated by | two domain experts | a model trained on gold, reviewed by one expert | |
| Entity mentions | 3,397 | 22,122 | 25,519 |
| Relations | 2,341 | 15,200 | 17,541 |

- Average length 5.4 words (1 to 13); vocabulary 2,409 words.
- Five top-level entity classes: equipment 15,466 mentions, activity 5,829,
  state or fault 3,185, process 874, property 165. The schema defines 224
  fine-grained types; 157 distinct type strings appear across the two files.
  Gold uses fine-grained types; silver uses the five top-level classes only.
- Gold and silver name two relations differently (`hasParticipant/hasPatient`
  versus `hasPatient`, `hasParticipant/hasAgent` versus `hasAgent`). The loader
  maps both.
- Identifiers, numbers and dates are masked (`<id>`, `<num>`, `<date>`). There
  are no real asset tags, timestamps or costs. That is why "cost", "supplier"
  and "date" questions are unanswerable by construction.

**Manuals:** three public PDFs in `manuals/`: `HDRB-manual.pdf` (667 KB),
`SOMM_Pulley.pdf` (209 KB), `conveyor-pulley-and-idler-iom.pdf` (5.6 MB). They
split into 168 sections and 176 chunks. They are mostly troubleshooting tables
("pulley lagging worn: replace worn pulley lagging"), not step-by-step
procedures. **[owner]** The repo does not record where each PDF came from or its
licence. The spec asks that the URLs be kept and the corpus cited.

**What is in the graph [measured, loader output]:**

| Node | Count | | Edge | Count |
|---|---|---|---|---|
| WorkOrder | 8,076 | | MENTIONS | 24,455 |
| Asset | 2,572 | | HAS_PARTICIPANT_PATIENT | 8,402 |
| State | 434 | | HAS_PART | 4,370 |
| Activity | 423 | | IS_A | 2,851 |
| Process | 128 | | HAS_PARTICIPANT_AGENT | 376 |
| Property | 34 | | CONTAINS | 216 |
| Chunk | 8,252 | | | |
| ManualSection | 168 | | | |
| Document | 3 | | | |

1,326 relation instances were dropped because an end was a process or property
node. The ten most-mentioned assets: pulley 351 work orders, hose 327, engine
283, bucket 264, air conditioner 263, oil 242, pump 195, brake 194, light 182,
bearing 180.

## 6. How the work actually unfolded

The spec planned eight weekly milestones. The git history shows something else:
two bursts of work with a gap between. Do not present it as the planned weekly
schedule.

| Date | What happened |
|---|---|
| 2026-08-18 | Repository created; Neo4j setup, schema, smoke test; MaintIE loader |
| 2026-08-19 | Chunking and embeddings; BM25 and dense retrieval; first evaluation harness and question sets; manual PDFs ingested and re-evaluated; first Streamlit app; generation layer and query API |
| (no commits for about seven weeks) | |
| 2026-10-08 | README, startup documentation, `CLAUDE.md`; repository shared privately with a collaborator |
| 2026-10-09 | Collaborator's pull request (graph retrieval, fusion, extended API, new app) reviewed and merged; then the safety gate, evaluation tooling, results report, one-click launcher, demo guide |
| 2026-10-12 | Documentation collaborator invited |

18 commits in total at the time of writing.

## 7. Who did what, and the AI-assisted workflow

State this plainly in any documentation, because assessors and readers will ask.

- **The owner** directed the project from the spec, made the decisions, and ran
  everything.
- **A collaborator** (GitHub `arihxnt25`) authored pull request #1: graph
  retrieval, fusion, the router, the extended API, the five-page app and the
  Windows setup script. Their commits are in the history.
- **Claude Code**, Anthropic's coding assistant, wrote the MaintIE loader, the
  Neo4j setup, the safety gate, the evaluation tooling (latency, bootstrap, error
  analysis, gate probes), the results report, the launcher and the docs, under
  the owner's direction. It also reviewed pull request #1. Its commits from
  `7302af3` onward carry a `Co-Authored-By` trailer. The 2026-08-19 commits
  (chunking, BM25 and dense retrieval, the first evaluation harness, the first
  app, the generation layer and API) were made in earlier sessions that this
  document's author did not see, and the history does not say how they were
  produced. **[owner]** confirm. The collaborator who wrote pull request #1
  also uses Claude Code, according to the owner.
- The owner gave Claude Code standing rules, written into `CONTEXT.md`: use only
  the listed stack, build one module at a time, verify each step, and check
  version-sensitive APIs (Neo4j syntax) against official documentation, not
  memory.

The spec names three roles (Knowledge Engineer, Retrieval and Evaluation
Engineer, Product/QA/Corpus Lead) with "(name)" placeholders. **[owner]** The
actual role assignments are not recorded.

## 8. Environment the numbers were measured on

| | |
|---|---|
| Machine | Windows 11 laptop, NVIDIA RTX 4060 (8 GB video memory), 16 GB RAM |
| Python | 3.12.3 (3.13 also installed; use `python -m pip`) |
| Docker | 29.2.1, Compose 5.1.0 |
| Database | Neo4j 5.26.29 community, native vector index (384 dimensions, cosine) |
| Embeddings | `BAAI/bge-small-en-v1.5` via sentence-transformers |
| Keyword search | `bm25s` |
| Language model | Gemma 12B instruction-tuned, quantised (Q4), about 6.3 GB, served by llama.cpp build 9787 (Vulkan) |
| Model settings | context 6,144 tokens, one slot, `--reasoning off` |
| Services | FastAPI and uvicorn (port 8000), Streamlit (8501), llama-server (8080), Neo4j (7687) |

Latency depends heavily on this machine. Generation, not retrieval, dominates
every answer.

## 9. Results: where each number lives

Question set: 24 questions in `questions/questions.json` (12 single-hop, 9
multi-hop, 3 unanswerable). Scored questions: 21.

| What | File in `artifacts/` | Command |
|---|---|---|
| Main evaluation (recall, nDCG, hit rate, latency, per question) | `eval_20261009T015501Z.json` | `python eval/harness.py` |
| Bootstrap intervals | `bootstrap_20261009T015501Z_recall_at_10.json`, `..._ndcg_at_10.json` | `python eval/bootstrap.py` |
| Failure analysis | `error_analysis_20261009T020907Z.json` | `python eval/error_analysis.py` |
| Safety gate probes | `safety_probes_20261009T015325Z.json` | `python eval/safety_probes.py` |
| Re-run that reproduced the collaborator's numbers | `eval_20261009T013935Z.json` | same as above |
| Collaborator's original run | `eval_20261008T210410Z.json` | same as above |
| Earlier BM25 and dense-only runs | `eval_20260818T203615Z.json`, `eval_20260818T210639Z.json` | before graph and fusion existed |

The headline table (Recall@10, 21 scored questions):

| | Single-hop | Multi-hop | Overall | nDCG overall | Hit rate overall | Warm p50 / p95 |
|---|---|---|---|---|---|---|
| BM25 | 61.5% | 0.0% | 35.1% | 0.259 | 42.9% | 0.5 / 0.6 ms |
| Dense | 41.7% | 2.8% | 25.0% | 0.144 | 33.3% | 34.0 / 47.7 ms |
| Graph | 81.2% | 23.6% | 56.5% | 0.369 | 95.2% | 5.0 / 10.8 ms |
| Hybrid | 66.7% | 22.2% | 47.6% | 0.436 | 76.2% | 42.7 / 64.1 ms |

Hybrid has the best ranking quality (nDCG); graph alone finds more of the right
records. Hybrid versus graph is statistically indistinguishable.

**Reproducibility note.** The collaborator's committed BM25 row (29.8% overall
recall) differs from a fresh run (35.1%). It was most likely measured against a
stale BM25 index. Cite the fresh figure.

**Safety gate** (developer-written fixtures, `eval/safety_probes.py`): intent
classifier 25 of 25. End-to-end decision: the calibration and held-out sets were
used to design the rules, so their perfect scores prove nothing. On the fresh
validation set the gate was right on **11 of 12**; the miss was "How do I clean a
swimming pool filter?" (score 0.835, overlap 0.25).

**Answer speed** (single manual runs, not a benchmark): 70 to 190 s with the
model's thinking on; 9 to 28 s with it off; 16 to 23 s on a later warm run, of
which retrieval is about 0.17 s.

## 10. Limitations to state, not hide

- 24 questions, and the fusion weights were tuned on the same 24, so graph and
  hybrid numbers are optimistic.
- The graph aggregates over gold-annotated work orders because the answer key was
  built from the same annotations. That favours graph retrieval.
- **[owner]** Who wrote the 24 questions is not recorded in the repo. The spec
  requires human authors checked by a second person (D8). Treat the set as a
  development set until the owner confirms authorship.
- The safety gate checks that the manuals are on topic, not that they contain a
  procedure. The manuals hold advice tables, not step-by-step procedures, so a
  passing answer is the manual's advice restated, not a procedure.
- Non-procedural unanswerable questions are not gated; they rely on a prompt rule
  that the model must begin "The evidence does not contain...". Untested.
- Two questions fail in graph or hybrid for structural reasons: one needs a
  "widest spread" count the planner does not compute; two surface just past the
  top-10 cutoff.
- No ISO 14224 or ISO 15926 mapping, no remaining-useful-life, no RAGAS.

## 11. Terminology: use and avoid

Safe, with a short gloss: RAG (find evidence first, then answer from it),
knowledge graph (things and the links between them), hybrid retrieval (several
search methods combined), BM25 (classic keyword ranking), embeddings and dense
retrieval (numbers that capture meaning), vector index, Reciprocal Rank Fusion
(merge ranked lists), entity linking, grounding and citation enforcement,
Recall@10, paired bootstrap and confidence interval, local inference and
quantisation.

Avoid or qualify:

| Avoid | Why |
|---|---|
| "GraphRAG" | Microsoft's specific method. Say "graph-augmented retrieval". |
| "Agentic" | The planner is plain rules. |
| "Fine-tuned" or "trained" | Nothing was trained. |
| "Standards-grounded" or "ISO 14224" | The mappings are not built. |
| "Hallucination-free" | Say "forces citations and removes unsupported sentences". |
| "Validated" (the gate) | Developer-written probes only. |
| "Predictive maintenance" | Not built. |
| "Production-ready", "real-time", "state of the art" | Not true. |
| "Multi-hop" unglossed | Here it means counting and joining across records. |

## 12. What exists only outside the repository

| Item | Where it is |
|---|---|
| The master specification | The owner holds it |
| The database contents (the built graph) | A Docker volume on the owner's machine; rebuild with the ingest scripts |
| The MaintIE corpus | `git clone https://github.com/nlp-tlp/maintie maintie` into the repo root |
| The language model and llama.cpp | Installed separately; not in the repo |
| `.env` (passwords, model path) | Never committed |
| Screenshots of the app | None in the repo; capture them yourself from the running app |

Rebuilding from a fresh clone is step-by-step in `CONTEXT.md` ("Startup"). A
Windows launcher, `start_demo.bat`, runs the whole stack; `DEMO.md` has a
10-minute walkthrough you can follow to capture screenshots.

## 13. Suggested documentation outline

1. Introduction and motivation (spec section 1 and 2.6).
2. Related work (spec section 2, five threads; section 13 has the references,
   several of which the spec itself marks as incomplete).
3. Gaps and objectives (section 2 above, with the status columns).
4. Design: the five layers, the graph schema, retrieval, generation, the gate
   (`CONTEXT.md`, section 4 above).
5. Implementation (the README layout table; section 8 above).
6. Evaluation and analysis (section 9 and `report/results.md`).
7. Limitations and future work (sections 10 and 14).
8. Reproducing it (`CONTEXT.md` Startup; `DEMO.md`).

## 14. Questions only the owner can answer

Ask before publishing the documentation:

1. Where each manual PDF came from (URL and licence).
2. Who wrote the 24 test questions, and whether a second person checked them.
3. The project's official title, institution, course and team name.
4. The role assignments for the three planned roles.
5. Whether the assessment weights matter for how sections are weighted.
6. Whether the master specification can be shared in full, or only the sections
   summarised here.
