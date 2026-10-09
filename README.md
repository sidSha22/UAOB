# Unified Asset & Operations Brain (UAOB)

Maintenance-knowledge RAG over the MaintIE work-order corpus plus equipment
manuals, backed by a single Neo4j instance (graph + vector index). Ask a
question in plain English; UAOB retrieves evidence with BM25, dense vectors
and the knowledge graph, fuses them, and writes an answer in which every
sentence cites its evidence.

Read [CONTEXT.md](CONTEXT.md) first: it holds the frozen tech stack and graph
schema, and its "Startup" section is the authoritative step-by-step for
reproducing the full environment from a fresh clone (corpus, database,
embeddings, local LLM).

## What it does

| Layer | What's built |
|---|---|
| Ingestion | MaintIE gold + silver annotations into Neo4j; work-order and manual-PDF chunking; bge-small embeddings |
| Retrieval | BM25 (`bm25s`), dense kNN (Neo4j vector index), **knowledge-graph retrieval** (entity linking + a rule-based query planner), **hybrid RRF fusion** with query-aware weights |
| Generation | Versioned prompts, any OpenAI-compatible LLM, citation enforcement with one retry and visible dropping of unsupported sentences |
| Interfaces | FastAPI service, five-page Streamlit app |
| Evaluation | Harness scoring Recall@10, nDCG@10, hit rate and first-hit rank for all four retrievers by question type |

**Graph retrieval** answers the questions text search can't, e.g.
"Which components are changed out most often?", "Which assets have both a
replacement and a repair?", or "Do any assets show the same fault more than
once?". It links entities in the question to Asset / State / Activity nodes,
picks a plan (lookup, rank, join, co-occurrence, recurrence, absence,
part-of), counts over the graph, and returns a serialised *graph fact* plus
the supporting work orders. Aggregations default to the 1,076 human-annotated
(gold) work orders; switch to all 8,076 in the UI or with `scope="all"`.

## Setup

You need Docker, Python 3.11+ and git. On machines with several Python
installs, always use `python -m pip`, never bare `pip`.

```bash
# 1. Get the corpus (it is not part of this repo)
git clone https://github.com/nlp-tlp/maintie maintie

# 2. Config
cp .env.example .env      # then set a Neo4j password and the LLM_* values

# 3. Dependencies
python -m pip install -r graph/requirements.txt

# 4. Neo4j
docker compose up -d
python graph/smoke_test.py          # should end with "13/13 checks passed"
```

## Build the graph

The database starts empty. Run these in order:

```bash
python ingest/load_maintie.py       # work orders, entities, relations
python ingest/chunk_work_orders.py  # one chunk per work order
python ingest/chunk_manuals.py      # PDFs in manuals/ -> sections -> chunks
python ingest/embed_chunks.py       # bge-small-en-v1.5 embeddings (resumable)
```

## Local LLM (only needed for written answers)

Not included. Install llama.cpp (or any OpenAI-compatible server), download a
GGUF model you are licensed to use, start the server, and point `LLM_BASE_URL`
and `LLM_MODEL` in `.env` at it. Retrieval, the asset explorer, the
evaluation dashboard and `POST /search` all work without it; the Ask page
then shows the retrieved evidence with a notice instead of an answer.

## Run

```bash
python -m streamlit run ui/app.py                     # the app, http://localhost:8501
python -m uvicorn api.main:app --reload               # API, docs at http://localhost:8000/docs
python eval/harness.py                                # writes artifacts/eval_*.json
```

### The app

| Page | Use it to |
|---|---|
| Ask | Ask a question; see it annotated with the linked entities, the planner's choice, a cited answer and every piece of evidence |
| Retrieval lab | Run one question through BM25, dense, graph and hybrid side by side and see where they agree |
| Asset explorer | Pick a piece of equipment; see its work-order count, top faults and activities, graph neighbourhood and filterable history |
| Evaluation | Compare retrievers on the question set by question type, down to each question |
| System | Check Neo4j, the LLM and the BM25 index; node and edge counts; reload the graph index |

### API

| Endpoint | Purpose |
|---|---|
| `GET /health` | Neo4j, LLM and BM25-index status |
| `GET /stats` | Node and edge counts |
| `POST /search` | Retrieval only: `{"query", "mode": "hybrid"\|"bm25"\|"dense"\|"graph", "k", "scope": "gold"\|"all"}` |
| `POST /query` | Retrieval + cited answer (503 with a clear message if the LLM is down) |
| `GET /assets?q=pump` | Find assets |
| `GET /assets/{id}` | Asset profile: faults, activities, parts, work orders |

## Layout

| Path | Purpose |
|---|---|
| `graph/` | Neo4j connection, schema, smoke test, in-memory graph index (`kg_index.py`), requirements |
| `ingest/` | MaintIE loader, chunking, embedding |
| `retrieval/` | `bm25.py`, `dense.py`, `graph_search.py`, `fusion.py` (RRF), `router.py` (single entry point) |
| `generation/` | Prompts (`prompts/*_v2.txt` current), LLM adapter, citation enforcement, `pipeline.py` |
| `api/`, `ui/` | FastAPI service; Streamlit app (`ui/app.py`, pages in `ui/views/`) |
| `eval/`, `questions/`, `artifacts/` | Harness, question sets, results |
| `manuals/` | Public vendor PDFs used as the manual corpus |
