"""Reusable UI pieces built on theme.py + data.py."""

import streamlit as st

import data
import theme
from theme import esc

SOURCE_LABELS = {"work_order": "Work order", "manual": "Manual", "graph": "Graph fact"}


def _ranks_text(fused_from):
    if not fused_from:
        return ""
    parts = [f"{theme.MODE_NAMES.get(n, n)} #{r}" for n, r in sorted(fused_from.items(), key=lambda x: x[1])]
    return "Ranked " + ", ".join(parts)


def evidence_card(eid, ev, cited=None):
    """One evidence item. cited: True / False / None (no answer yet)."""
    stype = ev.get("source_type", "")
    sid = ev.get("source_id", "")
    head = [f'<span class="eid">{esc(eid)}</span>',
            f'<span class="src src-{esc(stype)}">{SOURCE_LABELS.get(stype, esc(stype))}</span>']
    if stype == "work_order":
        quality = ev.get("annotation_quality")
        head.append(f'<span class="num">{esc(sid)}</span>')
        if quality == "gold":
            head.append('<span class="q-gold" title="Human-annotated in MaintIE">gold annotation</span>')
        elif quality:
            head.append(f'<span>{esc(quality)} annotation</span>')
        body = theme.annotate(ev.get("text", ""), data.wo_entities(sid))
        body = body.replace('class="annot"', 'class="annot ev-text"', 1)
    elif stype == "manual":
        head.append(f"<span>{esc(data.manual_section(sid))}</span>")
        body = f'<div class="ev-text manual">{esc(ev.get("text", ""))}</div>'
    else:
        body = f'<div class="ev-text graph">{esc(ev.get("text", ""))}</div>'
    ranks = _ranks_text(ev.get("fused_from"))
    if ranks:
        head.append(f'<span class="ranks">{esc(ranks)}</span>')
    elif ev.get("score") is not None:
        head.append(f'<span class="ranks num">score {ev["score"]:.3f}</span>')
    cls = "ev" + (" cited" if cited else "") + (" muted" if cited is False else "")
    st.markdown(f'<div class="{cls}" id="ev-{esc(eid)}"><div class="ev-head">{"".join(head)}</div>{body}</div>',
                unsafe_allow_html=True)


def query_banner(query, info):
    """The question, annotated with the entities the graph linked, plus the
    planner's decision."""
    ents = [(e["label"], e["span"]) for e in info["links"]]
    st.markdown(theme.annotate(query, ents, big=True), unsafe_allow_html=True)
    plan = info["plan"]
    linked = ", ".join(f"{e['id']} ({e['df']})" for e in info["links"]) or "no entities linked"
    st.markdown(
        f'<div class="plan"><b>{esc(theme.PLAN_DESCRIPTIONS.get(plan, plan))}</b>'
        f'<span>Linked: {esc(linked)}</span></div>',
        unsafe_allow_html=True)


def require_neo4j():
    """Stop the page with setup guidance when Neo4j is unreachable."""
    status = data.neo4j_status()
    if status["ok"]:
        return True
    theme.note(
        "<b>Can't reach Neo4j.</b> Retrieval and the knowledge graph need the database running.<br>"
        "Start it with <code>docker compose up -d</code>, wait about 30 seconds, then reload this page. "
        "If it is running, check <code>NEO4J_URI</code> and <code>NEO4J_PASSWORD</code> in <code>.env</code>."
        f"<br><span style='color:var(--ink-3)'>Details: {esc(status['detail'][:300])}</span>",
        bad=True)
    if st.button("Check again"):
        data.neo4j_status.clear()
        st.rerun()
    st.stop()


def mode_picker(key, default="hybrid", include_help=True):
    options = list(theme.MODE_NAMES)
    help_text = ("Hybrid fuses BM25, dense and graph results with reciprocal rank fusion. "
                 "Graph uses entity linking and counts over the knowledge graph.") if include_help else None
    picked = st.segmented_control("Retriever", options, default=default, key=key,
                                  format_func=lambda m: theme.MODE_NAMES[m], help=help_text)
    return picked or default
