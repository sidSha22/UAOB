"""System status and setup checklist."""

import pandas as pd
import streamlit as st

import data
import theme
from theme import esc


def _row(ok, title, detail, fix=None):
    cls = "ok" if ok is True else ("warn" if ok is None else "off")
    fix_html = f"<div style='color:var(--ink-3);font-size:.88rem;margin-top:.15rem'>{fix}</div>" if fix and ok is not True else ""
    st.markdown(f'<div class="ev"><div class="status" style="font-size:1rem;color:var(--ink)">'
                f'<span class="dot {cls}"></span><b>{esc(title)}</b>'
                f'<span style="color:var(--ink-3)">{esc(detail)}</span></div>{fix_html}</div>',
                unsafe_allow_html=True)


def render():
    st.title("System")
    theme.lede("What is running, what the graph contains, and how to fix anything that isn't ready.")

    if st.button("Recheck everything"):
        data.neo4j_status.clear()
        data.llm_status.clear()
        data.graph_counts.clear()
        st.rerun()

    neo = data.neo4j_status()
    llm = data.llm_status()
    _row(neo["ok"], "Neo4j", neo["detail"][:160],
         "Run <code>docker compose up -d</code>, then <code>python graph/smoke_test.py</code> "
         "(should end with 13/13 checks passed).")
    if llm["configured"]:
        _row(True if llm["reachable"] else False, "Language model",
             f"{llm['model']} at {llm['base_url']} ({llm['detail']})",
             "Start your OpenAI-compatible server, e.g. <code>llama-server -m model.gguf --port 8080</code>.")
    else:
        _row(None, "Language model", "Not configured. Retrieval works; answers need an LLM.",
             "Set <code>LLM_BASE_URL</code> and <code>LLM_MODEL</code> in <code>.env</code>.")
    built = data.bm25_index_built()
    _row(True if built else None, "BM25 index", "Built" if built else "Builds automatically on the first BM25 or hybrid search",
         "Delete <code>retrieval/bm25_index/</code> to rebuild it after re-ingesting.")

    if not neo["ok"]:
        return

    nodes, edges = data.graph_counts()
    node_map = {r["label"]: r["n"] for r in nodes}
    chunks = node_map.get("Chunk", 0)
    theme.stat_strip([
        (f"{node_map.get('WorkOrder', 0):,}", "work orders"),
        (f"{node_map.get('Asset', 0):,}", "assets"),
        (f"{node_map.get('State', 0) + node_map.get('Activity', 0):,}", "faults and activities"),
        (f"{chunks:,}", "searchable chunks"),
        (f"{node_map.get('Document', 0):,}", "manuals"),
    ])
    if chunks == 0:
        theme.note("The graph has no chunks yet. Run the ingest scripts in order: "
                   "<code>load_maintie.py</code>, <code>chunk_work_orders.py</code>, "
                   "<code>chunk_manuals.py</code>, <code>embed_chunks.py</code>.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Nodes")
        st.dataframe(pd.DataFrame(nodes).rename(columns={"label": "Label", "n": "Count"}),
                     hide_index=True, use_container_width=True)
    with c2:
        st.markdown("### Relationships")
        st.dataframe(pd.DataFrame(edges).rename(columns={"rel": "Type", "n": "Count"}),
                     hide_index=True, use_container_width=True)

    st.markdown("### In-memory graph index")
    st.caption("Graph retrieval and the asset explorer use a read-only copy of the work-order graph held in "
               "memory. Reload it after re-running ingestion.")
    if st.button("Reload graph index"):
        data.reload_kg()
        st.success("Graph index reloaded.")
