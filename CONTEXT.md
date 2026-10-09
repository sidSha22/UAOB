## 5. System Design and Architecture

### 5.1 Architectural overview

Modular monolith, five layers. One repository, one FastAPI process, Neo4j as the single stateful store (graph + native vector index; Neo4j ≥5.11), documents on disk. **No MongoDB, no separate vector DB** — four datastores for one project is DevOps tax with zero marks attached (frozen decision D3, §12).

```
┌────────────────────────────────────────────────────────────────┐
│  L5  PRESENTATION   Streamlit UI: query box, answer w/ inline  │
│                     citations, evidence panel, graph view,     │
│                     asset health summary page                  │
├────────────────────────────────────────────────────────────────┤
│  L4  GENERATION     LLM answerer (hosted API, pluggable):      │
│                     prompt assembly · citation enforcement ·   │
│                     safety gate (query classifier + retrieval  │
│                     confidence threshold → answer/refuse/      │
│                     escalate) · provenance stamping            │
├────────────────────────────────────────────────────────────────┤
│  L3  RETRIEVAL      Query router → [BM25] [Dense kNN]          │
│                     [Graph: entity link → subgraph expand →    │
│                     path serialise] → response-stage fusion    │
│                     (RRF baseline; weighted variant)           │
├────────────────────────────────────────────────────────────────┤
│  L2  KNOWLEDGE      Neo4j: MaintIE-schema graph, ISO 14224     │
│                     failure-mode nodes, ISO 15926 asset        │
│                     hierarchy, manual-section nodes, RUL       │
│                     prediction nodes; vector index on chunk    │
│                     embeddings; provenance on every node/edge  │
├────────────────────────────────────────────────────────────────┤
│  L1  INGESTION      MWO pipeline (normalise → MaintIE NER/RE   │
│                     via released models → ISO mapping) ·       │
│                     Manual pipeline (PDF → sections → chunks   │
│                     → embed) · RUL pipeline (C-MAPSS model →   │
│                     prediction nodes) · synthetic augmenter    │
│                     (KG-path-constrained, §6.5, clearly        │
│                     flagged `synthetic=true`)                  │
└────────────────────────────────────────────────────────────────┘
```

### 5.2 Technology stack (frozen)

| Concern | Choice | Justification (one line each) |
|---|---|---|
| Language | Python 3.11 | Team skill; entire NLP/graph ecosystem |
| | **Local env note:** this machine has two Python installs (3.12 and 3.13). Bare `pip install` can land in the wrong one and cause `ModuleNotFoundError` for packages that are actually installed. Always use `python -m pip install ...` so pip targets the same interpreter `python` will run. | Bit us once already during the graph/ smoke test |
| API | FastAPI | Approved in proposal; async; OpenAPI for free |
| Graph + vectors | Neo4j 5.x (native vector index) | One store for graph and embeddings; Cypher for traversal; removes a whole service |
| Lexical retrieval | `bm25s` or Elasticsearch-lite via `rank_bm25` | BM25 is the mandatory keyword baseline (§8) |
| Embeddings | `BAAI/bge-small-en-v1.5` (local, 384-d) | Free, fast, strong on short technical text; no API cost for indexing |
| NER/RE | MaintIE corpus (pre-annotated) + MaintIE training code (SpERT/REBEL) | Corpus ships gold/silver annotations directly usable as-is; no trained checkpoints are shipped — extracting entities/relations from *unannotated* text requires training a model first (deferred to M4, see risk R2) |
| LLM (generation) | Hosted API model, thin adapter interface | Pluggable by design; local fallback via Ollama if budget/keys fail (risk R4) |
| RUL model | Replicated published C-MAPSS baseline (CNN/LSTM) or public checkpoint | Commodity component (frozen decision D5) |
| UI | Streamlit | Days not weeks; nobody grades CSS (D4) |
| Eval | RAGAS + custom harness (pytest-style runners) | Standard faithfulness/relevance metrics + our stratified ablation |
| Infra | Docker Compose (api, neo4j, ui) | One-command bring-up for three remote teammates |

### 5.3 Data model (summary; full schema in Appendix B)

Node types: `Asset` (instance), `AssetClass` (ISO 15926-derived hierarchy), `WorkOrder`, `FailureMode` (ISO 14224 code), `Activity`, `State`, `ManualSection`, `Chunk` (embedded text unit, provenance-stamped), `RULPrediction` (value, confidence band, model version, timestamp), `Document`.
Key edges: `Asset—INSTANCE_OF→AssetClass`, `AssetClass—SUBCLASS_OF→AssetClass`, `WorkOrder—ON_ASSET→Asset`, `WorkOrder—OBSERVED→State`, `State—CLASSIFIED_AS→FailureMode`, `WorkOrder—PERFORMED→Activity`, `ManualSection—APPLIES_TO→AssetClass`, `WorkOrder—REFERENCES→ManualSection`, `RULPrediction—FOR_ASSET→Asset`, `Chunk—FROM→{WorkOrder|ManualSection}`.
The G3 join path is `Asset ← ON_ASSET — WorkOrder — REFERENCES → ManualSection`: any manual-grounded answer about an asset instance must traverse its work-order history first and surface conflicts.


## Appendix B: Graph Schema

Node labels & required properties (all nodes: `id`, `source_record_id|source_doc`, `pipeline_version`, `synthetic:boolean`):
- `Asset {tag, name}` · `AssetClass {name, iso15926_ref}` · `WorkOrder {wo_id, date, raw_text, norm_text}` · `State {surface_form}` · `FailureMode {code, label}` (ISO 14224 condensed set) · `Activity {surface_form, canonical}` · `ManualSection {doc, section, title, revision}` · `Chunk {text, embedding, tokens}` · `RULPrediction {value_cycles, ci_low, ci_high, model_version, ts}` · `Document {type, uri}`

Edges (domain → range): `INSTANCE_OF (Asset→AssetClass)` · `SUBCLASS_OF (AssetClass→AssetClass)` · `ON_ASSET (WorkOrder→Asset)` · `OBSERVED (WorkOrder→State)` · `CLASSIFIED_AS (State→FailureMode)` · `PERFORMED (WorkOrder→Activity)` · `APPLIES_TO (ManualSection→AssetClass)` · `REFERENCES (WorkOrder→ManualSection)` · `FOR_ASSET (RULPrediction→Asset)` · `FROM (Chunk→WorkOrder|ManualSection)`

Conformance checks (CI): no orphan `State`/`Activity`; every `Chunk` has `FROM`; every `FailureMode.code` ∈ the condensed ISO 14224 set; every synthetic node flagged; `REFERENCES` only to sections whose `APPLIES_TO` class is an ancestor-or-self of the work order's asset class (G3 sanity).

## Startup: reproducing the working environment from a fresh clone

The repo holds **code, prompts, the three manual PDFs, the question sets and eval results only**. It does NOT contain the corpus, the database contents, the embedding model, any LLM, or any secrets. Each of those is provisioned locally, in this order. Do not skip ahead: later steps assume earlier ones succeeded.

1. **Tooling.** Docker Desktop (running), Python 3.11+, git. If the machine has more than one Python, always use `python -m pip install ...`, never bare `pip` (it can install into a different interpreter than `python` runs).
2. **Corpus (not in repo).** From the repo root: `git clone https://github.com/nlp-tlp/maintie maintie`. It is MIT-licensed and git-ignored here. `ingest/load_maintie.py` reads `maintie/data/gold_release.json` and `silver_release.json` (1,076 + 7,000 records).
3. **Config.** `cp .env.example .env`, then set `NEO4J_PASSWORD` (any 8+ character value; `docker compose` and the Python code both read it from `.env`). `.env` is never committed.
4. **Python deps.** `python -m pip install -r graph/requirements.txt`. This pulls `sentence-transformers` (and PyTorch), so expect a large first download.
5. **Neo4j.** `docker compose up -d`, wait about 30 seconds for boot, then `python graph/smoke_test.py`. It also applies the schema (uniqueness constraints and the 384-d cosine vector index on `Chunk.embedding`) and must end with `13/13 checks passed`. Data lives in a Docker named volume, so it survives restarts but is not in git.
6. **Build the graph, in this order** (all resumable and safe to re-run):
   `python ingest/load_maintie.py`, then `python ingest/chunk_work_orders.py`, then `python ingest/chunk_manuals.py` (reads `manuals/*.pdf`, which are in the repo), then `python ingest/embed_chunks.py`. The first embedding run downloads `BAAI/bge-small-en-v1.5` from Hugging Face. Sanity check after step one: 8,076 `WorkOrder`, 2,572 `Asset`, 434 `State`, 423 `Activity`, 128 `Process`, 34 `Property` nodes, 24,455 `MENTIONS` edges and 16,215 entity-relation edges.
7. **Local LLM (not in repo, install it yourself).** Generation (`generation/`, `api/`) talks to any OpenAI-compatible chat endpoint through `LLM_BASE_URL` and `LLM_MODEL` in `.env`; the code stays untouched whichever server you use. The original author runs llama.cpp's `llama-server` on port 8080 with a Gemma 12B instruct GGUF (Q4 quantization), so `LLM_BASE_URL=http://localhost:8080/v1`. Install llama.cpp, download a GGUF model you are licensed to use, start `llama-server`, and put your own model identifier in `LLM_MODEL`. `LLM_API_KEY` is only needed for hosted providers. Retrieval, the UI and the eval harness work without any LLM; only `POST /query` needs one.
8. **Run.** UI: `python -m streamlit run ui/app.py` (http://localhost:8501). API: `python -m uvicorn api.main:app --reload`, then `POST /query` or `POST /search` (docs at `/docs`). Eval: `python eval/harness.py` (all four modes; `--modes bm25 graph` for a subset), which writes a timestamped `artifacts/eval_*.json` that the UI's Evaluation page reads. The BM25 index is built on first search into `retrieval/bm25_index/` (git-ignored); delete that folder to rebuild it after re-ingesting.

**Where the project stands:** MaintIE ingestion, work-order and manual chunking, embeddings, BM25 and dense retrieval, **graph-based retrieval** (`retrieval/graph_search.py`: entity linking against Asset/State/Activity aliases, a rule-based query planner -- lookup / rank / both / pair / recurrence / absence / part_of -- and serialised graph facts), **reciprocal-rank fusion** (`retrieval/fusion.py`, c=60, query-aware weights in `retrieval/router.py`), a retrieval eval harness covering all four modes, citation-enforced generation (prompt v2 adds the `[graph]` evidence type), a query API (`/health`, `/stats`, `/search`, `/query`, `/assets`) and a five-page Streamlit app are built. `/query` and the UI default to `hybrid`.

Graph retrieval reads the work-order graph once into memory (`graph/kg_index.py`, four read-only Cypher queries, ~1-3 s) and answers from there; Neo4j stays the only store and nothing is written. Aggregate graph plans count over gold-annotated work orders by default (the question set's gold evidence was built the same way), falling back to all work orders when gold has no match.

**Still not built:** ISO 14224 FailureMode mapping, AssetClass hierarchy, WorkOrder-REFERENCES->ManualSection links, RUL prediction nodes, a retrieval-confidence refusal threshold, and RAGAS answer-level metrics.

**Working rules for any Claude Code session:** read this file first; verify version-sensitive APIs (Neo4j syntax especially) against real docs, not memory; build one module at a time and verify each step before the next.

# Do not add dependencies, services, or datastores not listed here. If deemed extremely important, mission-critical, or a straight-up better option, then flag and ask. 