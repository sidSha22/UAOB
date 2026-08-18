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

# Do not add dependencies, services, or datastores not listed here. If deemed extremely important, mission-critical, or a straight-up better option, then flag and ask. 