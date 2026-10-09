"""UAOB Streamlit app.

    python -m streamlit run ui/app.py

Pages: Ask (cited answers), Retrieval lab (compare retrievers), Asset
explorer (per-asset history and graph neighbourhood), Evaluation (eval
harness results) and System (status and setup).
"""

import sys
from pathlib import Path

import streamlit as st

_UI = Path(__file__).resolve().parent
if str(_UI) not in sys.path:
    sys.path.insert(0, str(_UI))

st.set_page_config(page_title="UAOB: maintenance knowledge", page_icon=":material/hub:",
                   layout="wide", initial_sidebar_state="auto")

import data  # noqa: E402
import theme  # noqa: E402
from views import ask, assets, evaluation, lab, system  # noqa: E402

theme.inject_css()
data.warm_all()

pages = {
    "ask": st.Page(ask.render, title="Ask", icon=":material/forum:", url_path="ask", default=True),
    "lab": st.Page(lab.render, title="Retrieval lab", icon=":material/compare_arrows:", url_path="lab"),
    "assets": st.Page(assets.render, title="Asset explorer", icon=":material/precision_manufacturing:",
                      url_path="assets"),
    "evaluation": st.Page(evaluation.render, title="Evaluation", icon=":material/monitoring:",
                          url_path="evaluation"),
    "system": st.Page(system.render, title="System", icon=":material/dns:", url_path="system"),
}
nav = st.navigation(list(pages.values()), position="hidden")

with st.sidebar:
    st.markdown(
        f'<div class="brand"><div class="brand-mark">{theme.BRAND_SVG}</div><div>'
        '<div class="brand-name">UAOB</div>'
        '<div class="brand-sub">Unified Asset &amp; Operations Brain</div></div></div>',
        unsafe_allow_html=True)
    for page in pages.values():
        st.page_link(page)
    st.divider()
    neo = data.neo4j_status()
    llm = data.llm_status()
    lines = [
        ("ok" if neo["ok"] else "off", "Neo4j " + ("connected" if neo["ok"] else "not reachable")),
        ("ok" if llm["reachable"] else ("warn" if not llm["configured"] else "off"),
         "LLM " + ("ready" if llm["reachable"] else ("not configured" if not llm["configured"] else "not reachable"))),
    ]
    st.markdown("".join(f'<div class="status"><span class="dot {c}"></span>{theme.esc(t)}</div>'
                        for c, t in lines), unsafe_allow_html=True)
    st.markdown(theme.legend_html(), unsafe_allow_html=True)

nav.run()
