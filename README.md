# Unified Asset & Operations Brain (UAOB)

Maintenance-knowledge RAG over the MaintIE work-order corpus plus equipment
manuals, backed by a single Neo4j instance (graph + vector index). Read
[CONTEXT.md](CONTEXT.md) first: it holds the frozen tech stack and graph schema,
and its "Startup" section is the authoritative step-by-step for reproducing the
full environment from a fresh clone (corpus, database, embeddings, local LLM).

Status: ingestion, BM25 and dense retrieval, citation-enforced generation, an
API, a Streamlit inspector and a retrieval eval harness exist. Fusion and graph
retrieval are not built yet.

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

## Local LLM (only needed for the API's `/query`)

Not included. Install llama.cpp (or any OpenAI-compatible server), download a
GGUF model you are licensed to use, start the server, and point `LLM_BASE_URL`
and `LLM_MODEL` in `.env` at it. Retrieval, the UI and the eval harness run
without it.

## Run

```bash
python -m streamlit run ui/app.py                     # retrieval inspector
python -m uvicorn api.main:app --reload               # POST /query
python eval/harness.py                                # writes artifacts/eval_*.json
```

## Layout

| Path | Purpose |
|---|---|
| `graph/` | Neo4j connection, schema, smoke test, requirements |
| `ingest/` | MaintIE loader, chunking, embedding |
| `retrieval/` | BM25 and dense retrievers |
| `generation/` | Prompts, LLM adapter, citation enforcement |
| `api/`, `ui/` | FastAPI service, Streamlit app |
| `eval/`, `questions/`, `artifacts/` | Harness, question sets, results |
| `manuals/` | Public vendor PDFs used as the manual corpus |
