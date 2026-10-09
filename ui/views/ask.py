"""Ask: question -> annotated query -> cited answer -> evidence."""

import streamlit as st

import components
import data
import theme
from theme import esc


def _set_question(q):
    st.session_state["ask_q"] = q
    st.session_state["ask_submitted"] = q


def render():
    st.title("Ask the maintenance record")
    theme.lede("Questions are answered only from work orders, equipment manuals and facts counted "
               "over the knowledge graph. Every sentence in an answer cites its evidence.")
    components.require_neo4j()

    with st.form("ask_form", border=False):
        q = st.text_input("Question", key="ask_q", placeholder="e.g. Which components are changed out most often?",
                          label_visibility="collapsed")
        c1, c2 = st.columns([5, 1.2], vertical_alignment="bottom")
        with c1:
            with st.expander("Retrieval settings"):
                s1, s2 = st.columns([2, 1])
                with s1:
                    mode = components.mode_picker("ask_mode")
                    scope = st.radio(
                        "Graph counts over", ["gold", "all"], horizontal=True, key="ask_scope",
                        format_func=lambda s: {"gold": "Gold-annotated work orders (1,076)",
                                               "all": "All work orders (8,076)"}[s],
                        help="Gold work orders were annotated by people; silver ones automatically. "
                             "Counts over gold are more reliable. Lookups always search everything.")
                with s2:
                    k = st.slider("Evidence items", 3, 20, 10, key="ask_k")
                    generate = st.toggle("Write an answer", value=True, key="ask_gen",
                                         help="Turn off to see retrieved evidence without calling the LLM.")
        with c2:
            submitted = st.form_submit_button("Ask", type="primary", use_container_width=True)
    if submitted and q.strip():
        st.session_state["ask_submitted"] = q.strip()

    question = st.session_state.get("ask_submitted")
    if not question:
        st.markdown("#### Try one of these")
        cols = st.columns(2)
        for i, ex in enumerate(data.EXAMPLE_QUESTIONS):
            cols[i % 2].button(ex, key=f"ex{i}", on_click=_set_question, args=(ex,), use_container_width=True)
        return

    mode = st.session_state.get("ask_mode") or "hybrid"
    k = st.session_state.get("ask_k", 10)
    scope = st.session_state.get("ask_scope", "gold")
    generate = st.session_state.get("ask_gen", True)

    st.write("")
    components.query_banner(question, data.explain(question))
    st.write("")

    with st.spinner("Retrieving evidence" + (" and writing the answer..." if generate else "...")):
        try:
            out = data.ask(question, mode, k, scope, generate)
        except Exception as exc:
            theme.note(f"<b>Retrieval failed in {esc(theme.MODE_NAMES[mode])} mode.</b> {esc(exc)}", bad=True)
            if mode == "dense":
                st.caption("Dense retrieval needs the bge-small embedding model and embedded chunks "
                           "(python ingest/embed_chunks.py). Hybrid mode keeps working without it.")
            return

    if out["generation_error"]:
        data.ask.clear()  # don't cache a transient LLM outage
        theme.note(
            "<b>No answer was written.</b> The language model isn't reachable, so only the retrieved "
            "evidence is shown below. Start your OpenAI-compatible server (for example "
            "<code>llama-server</code>) and check <code>LLM_BASE_URL</code> and <code>LLM_MODEL</code> in "
            f"<code>.env</code>.<br><span style='color:var(--ink-3)'>{esc(out['generation_error'][:280])}</span>")
    elif out["answer"]:
        st.markdown(theme.answer_html(out["answer"]), unsafe_allow_html=True)
        bits = []
        if out["dropped_sentences"]:
            bits.append(f"{out['dropped_sentences']} unsupported sentence(s) removed by the citation check")
        if out["retried"]:
            bits.append("regenerated once to fix citations")
        if bits:
            st.caption("; ".join(bits).capitalize() + ".")

    warn = out["retrieval"]["errors"]
    if warn:
        names = ", ".join(theme.MODE_NAMES.get(n, n) for n in warn)
        st.caption(f"Hybrid ran without {names}: " + "; ".join(v[:140] for v in warn.values()))

    cited = set(out["cited_evidence_ids"])
    ev_map = out["evidence_map"]
    st.markdown(f"### Evidence ({len(ev_map)})")
    if not ev_map:
        st.info("Nothing matched. Try naming the equipment, e.g. 'hose', 'pulley' or 'air conditioner'.")
    have_answer = bool(out["answer"]) and not out["generation_error"]
    ordered = sorted(ev_map.items(), key=lambda kv: (have_answer and kv[0] not in cited, int(kv[0][1:])))
    for eid, ev in ordered:
        components.evidence_card(eid, ev, cited=(eid in cited) if have_answer else None)

    t = {**out["retrieval"]["timings_ms"], **out["timings_ms"]}
    parts = [f"{theme.MODE_NAMES.get(k_, k_.capitalize())} {v:.0f} ms" for k_, v in t.items()
             if k_ not in ("retrieval",)]
    st.markdown(f'<p class="timing">{esc(", ".join(parts))}. Prompt {esc(out["prompt_version"])}.</p>',
                unsafe_allow_html=True)
