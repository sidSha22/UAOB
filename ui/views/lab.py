"""Retrieval Lab: run all four retrievers on one question side by side."""

from collections import defaultdict

import streamlit as st

import components
import data
import theme
from theme import esc

MODES = ["bm25", "dense", "graph", "hybrid"]


def render():
    st.title("Retrieval lab")
    theme.lede("Run one question through every retriever and compare what each finds. "
               "Coloured dots mark results that other retrievers also returned.")
    components.require_neo4j()

    with st.form("lab_form", border=False):
        c1, c2, c3 = st.columns([6, 1.4, 1.2], vertical_alignment="bottom")
        q = c1.text_input("Question", value=st.session_state.get("lab_last", "seal leak on pump"),
                          label_visibility="collapsed")
        k = c2.number_input("Top k", 3, 25, 8)
        go = c3.form_submit_button("Compare", type="primary", use_container_width=True)
    if go and q.strip():
        st.session_state["lab_last"] = q.strip()
    query = st.session_state.get("lab_last", q).strip()
    if not query:
        return

    components.query_banner(query, data.explain(query))
    st.write("")

    runs, errors = {}, {}
    with st.spinner("Running four retrievers..."):
        for m in MODES:
            try:
                runs[m] = data.retrieve(query, m, int(k), "gold")
            except Exception as exc:
                errors[m] = str(exc)

    seen_by = defaultdict(set)
    for m, out in runs.items():
        for r in out["results"]:
            seen_by[r["chunk_id"]].add(m)

    cols = st.columns(4)
    for col, m in zip(cols, MODES):
        with col:
            color = theme.MODE_COLORS[m]
            st.markdown(f'<h3 style="border-bottom:3px solid {color};padding-bottom:.25rem">'
                        f'{theme.MODE_NAMES[m]}</h3>', unsafe_allow_html=True)
            if m in errors:
                st.markdown(f'<div class="note bad" style="font-size:.85rem">Not available: '
                            f'{esc(errors[m][:220])}</div>', unsafe_allow_html=True)
                continue
            out = runs[m]
            t = sum(v for v in out["timings_ms"].values())
            st.markdown(f'<p class="timing">{len(out["results"])} results in {t:.0f} ms</p>',
                        unsafe_allow_html=True)
            items = []
            for i, r in enumerate(out["results"], 1):
                others = sorted(seen_by[r["chunk_id"]] - {m}, key=MODES.index)
                dots = "".join(f'<span class="shared" style="background:{theme.MODE_COLORS[o]}" '
                               f'title="Also found by {theme.MODE_NAMES[o]}"></span>' for o in others)
                src = r["source_type"]
                label = {"work_order": r["source_id"], "manual": data.manual_section(r["source_id"]),
                         "graph": "graph fact"}.get(src, src)
                text = r["text"] if len(r["text"]) <= 170 else r["text"][:167] + "..."
                items.append(f'<div class="lab-item"><span class="r">{i}</span>{esc(text)}'
                             f'<div class="meta">{dots}{esc(label)}</div></div>')
            st.markdown("".join(items), unsafe_allow_html=True)

    if len(runs) >= 2:
        st.markdown("### Agreement")
        st.caption("How many results each pair of retrievers have in common.")
        names = [m for m in MODES if m in runs]
        sets = {m: {r["chunk_id"] for r in runs[m]["results"]} for m in names}
        rows = []
        for a in names:
            rows.append({"": theme.MODE_NAMES[a], **{theme.MODE_NAMES[b]: len(sets[a] & sets[b]) for b in names}})
        st.dataframe(rows, hide_index=True, use_container_width=False)
