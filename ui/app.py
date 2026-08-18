"""Streamlit retrieval inspector. Streamlit only -- no API, no generation.
Calls retrieval/bm25.py and retrieval/dense.py directly; retrieval/ itself
is untouched."""

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "graph"))
sys.path.insert(0, str(_ROOT / "retrieval"))
from db import run_query  # noqa: E402
import bm25 as bm25_mod  # noqa: E402
import dense as dense_mod  # noqa: E402

st.set_page_config(page_title="Retrieval Inspector", layout="wide")


@st.cache_resource
def get_bm25_retriever():
    return bm25_mod.get_retriever()


@st.cache_data(ttl=60)
def get_graph_stats():
    node_counts = run_query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC"
    )
    edge_counts = run_query(
        "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n ORDER BY n DESC"
    )
    return node_counts, edge_counts


def render_sidebar():
    st.sidebar.header("Graph stats (Neo4j)")
    try:
        node_counts, edge_counts = get_graph_stats()
    except Exception as exc:
        st.sidebar.error(f"Could not reach Neo4j: {exc}")
        return

    st.sidebar.subheader("Nodes by label")
    for row in node_counts:
        st.sidebar.text(f"{row['label']:<15} {row['n']:>6}")

    st.sidebar.subheader("Edges by type")
    for row in edge_counts:
        st.sidebar.text(f"{row['rel']:<20} {row['n']:>6}")


def render_result(rank, result):
    quality = result.get("annotation_quality")
    quality_str = f" · {quality}" if quality else ""
    with st.container(border=True):
        st.markdown(f"**{rank}.** score = `{result['score']:.4f}`  "
                    f"({result['source_type']}: `{result['source_id']}`{quality_str})")
        st.write(result["text"])


def main():
    render_sidebar()

    st.title("Retrieval Inspector")
    st.caption("Dense kNN / BM25 lexical search over Chunk nodes. Retrieval only -- no generation.")

    query = st.text_input("Query", value="seal leak on pump")
    col1, col2 = st.columns([1, 2])
    with col1:
        mode = st.radio("Retriever", ["bm25", "dense"], horizontal=True)
    with col2:
        k = st.slider("k", min_value=1, max_value=25, value=10)

    if not query.strip():
        st.info("Enter a query above.")
        return

    if mode == "bm25":
        get_bm25_retriever()
        results = bm25_mod.search(query, k=k)
    else:
        results = dense_mod.search(query, k=k)

    st.subheader(f"{len(results)} results ({mode}, k={k})")
    for i, r in enumerate(results, 1):
        render_result(i, r)


if __name__ == "__main__":
    main()
