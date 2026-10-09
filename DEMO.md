# Showing UAOB to a reviewer

## Start and stop

1. Open **Docker Desktop** and wait until it says the engine is running.
2. Double-click **`start_demo.bat`**. It starts the database, the language
   model, the API and the app, and opens the browser. About one minute.
3. The first time the page opens it shows "Loading the embedding model" for
   roughly 40 seconds. That happens once per start, so open the page before the
   reviewer arrives.
4. When you are done, double-click **`stop_demo.bat`**. It stops everything and
   keeps the data.

If something is missing the launcher says so and stops. Options:
`start_demo.bat -NoLlm` (no written answers), `-NoBrowser`, `-NoPause`.
Logs are `uaob_*.log` in this folder. First time on a new machine, run
`run_uaob.bat` once instead: it builds the database (see `README.md`).

## What the project does, in 30 seconds

You ask a maintenance question in plain English. The system searches 8,076 real
work-order records (the public MaintIE corpus) and three equipment manuals in
three ways: keyword search (BM25), meaning-based search (dense vectors), and a
knowledge graph built from the work orders that can count and join across
records. It merges the results, and a local language model writes a short answer
in which every sentence cites its evidence. A sentence without a valid citation
is retried once and then removed. For "how do I..." questions a safety gate
requires matching equipment-manual text and otherwise refuses without calling
the model at all.

## A 10-minute walk-through

Ask these on the **Ask** page, in this order. Answers take 15 to 25 seconds the
first time; an answer you have already asked returns instantly for 15 minutes,
so ask the slow ones just before the reviewer arrives.

| # | Question | What to point out |
|---|---|---|
| 1 | Which components are changed out most often? | The question is highlighted with the entities it found and the plan the system chose. The answer ranks components by counting over the graph: engine 15 work orders, cylinder 14, pump 12, hose 11. Keyword search cannot do this: it matches words, not counts. |
| 2 | What was reported about the tail pulley? | A plain lookup. Each sentence carries citation chips that jump to the work orders behind it. |
| 3 | Which assets have both a replacement and a repair recorded against them? | A join across records: 43 assets. |
| 4 | Do any assets show a recurring pattern of the same fault being reported more than once? | 78 asset and fault pairs repeat; a blown hose is the worst at 19 reports. |
| 5 | How do I replace the lagging on a conveyor pulley? | The gate passes it because the manuals cover pulleys, and the answer is drawn from manual text. The manuals are troubleshooting tables, not step-by-step procedures, so say that this is the manual's advice restated, not a procedure. |
| 6 | How do I isolate and lock out the conveyor drive before replacing a pulley? | Safety-critical wording. The model says the evidence contains no isolation procedure, and a fixed warning is added that the model does not write. |
| 7 | How do I bake a chocolate cake? | Refused in about two seconds, with pointers to the closest manual sections. The model was never called. |

Then show the other pages:

- **Retrieval lab:** one question run through BM25, dense, graph and hybrid side
  by side. Use question 1 to show text search failing where the graph works.
- **Asset explorer:** pick `pulley` (351 work orders) for its top faults and
  activities, and history.
- **Evaluation:** the comparison table below, down to each question.
- **System:** database and model status, node and edge counts.

## The numbers, and what they do and do not show

Recall@10 on 21 scored questions (`report/results.md` has the full tables):

| BM25 | Dense | Graph | Hybrid |
|---|---|---|---|
| 35.1% | 25.0% | 56.5% | 47.6% |

- Graph and hybrid beat the text-only methods, mostly on questions that need
  counting across records (BM25 scores 0% there). The confidence intervals
  exclude zero.
- Hybrid did not beat graph alone; the difference is within noise.
- Warm retrieval takes 43 ms (p50) and 64 ms (p95) for hybrid.

Say these limits before you are asked:

- The question set has 24 questions, and the hybrid weights were tuned on the
  same 24, so the numbers are optimistic.
- The graph counts over annotated work orders because the answer key was built
  the same way, which favours the graph.
- The safety gate was tuned and checked on questions the developers wrote.
  On a fresh set it got 11 of 12 right.

## Questions a reviewer is likely to ask

- **Why a graph and not just a vector search?** Counting, ranking and joining
  across records is not something similarity search can do. The multi-hop
  numbers show it (dense 2.8%, graph 23.6%).
- **How do you stop the model making things up?** Every sentence must cite
  evidence that exists. Otherwise it is retried once and then removed with a
  visible note. Procedural questions also pass the safety gate.
- **Is it using a hosted AI service?** No. The model runs on this laptop
  through llama.cpp, so nothing leaves the machine.
- **What is not finished?** ISO 14224 failure codes and ISO 15926 asset classes
  (they need the MWO2KG data), remaining-useful-life predictions (they need the
  NASA C-MAPSS data), the 100-question human-written test set, and RAGAS
  metrics. See `report/results.md`, section 7.
- **Where did the data come from?** MaintIE, a public annotated corpus of real
  maintenance work orders (MIT licence), plus three public equipment manuals.

## If something goes wrong

| Symptom | Fix |
|---|---|
| "Docker is not running" | Open Docker Desktop, wait for the engine, run again. |
| "The graph is not loaded" | Run `run_uaob.bat` once to build it. |
| No written answers, evidence only | The model did not start. Check `uaob_llm.err.log`, or use the "Write an answer" switch under Retrieval settings to show retrieval alone. |
| First question is slow | The app is still loading its indexes (about 40 s after the page first opens). |
| A port is busy | Run `stop_demo.bat`, then start again. |
