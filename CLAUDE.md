# Read this first (Claude Code session instructions)

This is a private repo shared with you by its owner (siddarth-05). It is a
maintenance-knowledge RAG project. A fresh clone contains **code only**; the
corpus, database contents, embedding model, local LLM and secrets are NOT in
it and must be set up on this machine.

## What to do at the start of a session

1. Read `CONTEXT.md` completely before touching anything. It holds the frozen
   tech stack, the graph schema, and a **"Startup" section** with the ordered
   steps (1-8) to reproduce the working environment. `README.md` is the short
   human version.
2. If this is a fresh clone (no `.env`, no `maintie/` folder, Neo4j not
   running), walk the user through the Startup steps in order and verify each
   one before moving to the next. Do not skip ahead; later steps assume earlier
   ones worked.
3. Report the results of the checks the Startup section names: the
   `13/13 checks passed` smoke test, and the node counts after
   `ingest/load_maintie.py` (8,076 WorkOrder, 2,572 Asset, 434 State,
   423 Activity, 128 Process, 34 Property).

## Ask the user before any of these

- Installing Docker, llama.cpp or any other software.
- Downloading large files: the PyTorch / sentence-transformers install, the
  `BAAI/bge-small-en-v1.5` embedding model, or an LLM model file. State the
  source and rough size when asking.
- Choosing which LLM model to use. Generation reads `LLM_BASE_URL` and
  `LLM_MODEL` from `.env` and works with any OpenAI-compatible server.
  **Do not edit `generation/llm.py` or other code to hardcode a model.** The
  owner runs llama.cpp with a Gemma 12B GGUF; this user installs their own
  server and model. Retrieval, the UI and the eval harness work without one.

## Rules for this repo

- `python -m pip install ...`, never bare `pip` (machines with several Python
  installs put packages in the wrong one).
- Do not add dependencies, services or datastores that are not listed in
  `CONTEXT.md`. If one seems truly necessary, flag it and ask.
- Verify version-sensitive APIs (Neo4j syntax in particular) against official
  docs, not memory. Build one module at a time and verify before the next.
- Never commit `.env` or any secret. `maintie/` and `retrieval/bm25_index/` are
  git-ignored on purpose.
- Access is by invitation to exactly two people. Do not make the repo public,
  add collaborators, or share its contents elsewhere.
- You have write access, so protect `master`: work on a branch, never
  force-push, and open a pull request or ask the owner before merging.

## Current state

Graph retrieval (`retrieval/graph_search.py`), RRF fusion (`retrieval/fusion.py`)
and a single retrieval entry point (`retrieval/router.py`, modes `hybrid`,
`bm25`, `dense`, `graph`) are built and used by the API, the Streamlit app and
the eval harness. A rule-based safety gate (`generation/safety.py`) sits in
front of generation for procedural questions. See CONTEXT.md "Where the project
stands" for what is still not built, and `report/results.md` for the latest
numbers and their caveats. Do not invent benchmark questions or ISO 14224 /
ISO 15926 codes: those need human authors and the MWO2KG data respectively.
