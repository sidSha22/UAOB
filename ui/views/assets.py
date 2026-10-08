"""Asset explorer: one asset's maintenance history from the knowledge graph."""

import altair as alt
import pandas as pd
import streamlit as st

import components
import data
import theme
from theme import esc

# MaintIE State annotations include stray function words; hide them from charts.
NOISE_STATES = {"no", "need", "needs", "requires", "require", "out", "required", "not"}


def _bar(counter, color, title, top=8):
    rows = [(k, v) for k, v in counter.most_common(top + 4) if k not in NOISE_STATES][:top]
    if not rows:
        st.caption(f"No {title.lower()} recorded.")
        return
    df = pd.DataFrame(rows, columns=["name", "work_orders"])
    base = alt.Chart(df).encode(
        y=alt.Y("name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180, labelFontSize=13,
                                                                 labelColor=theme.INK, ticks=False, domain=False)),
        x=alt.X("work_orders:Q", title=None, axis=None),
        tooltip=[alt.Tooltip("name:N", title=title), alt.Tooltip("work_orders:Q", title="Work orders")],
    )
    bars = base.mark_bar(color=color, cornerRadiusEnd=4, height=16)
    labels = base.mark_text(align="left", dx=5, fontSize=12, color=theme.INK_2).encode(text="work_orders:Q")
    chart = (bars + labels).properties(height=max(90, 28 * len(rows)), title=alt.TitleParams(
        title, anchor="start", fontSize=15, font="Barlow Semi Condensed", color=theme.INK))
    st.altair_chart(chart.configure_view(strokeWidth=0).configure(font="Barlow"), use_container_width=True)


def _dot(asset_id, p):
    """Three-row layout: faults above the asset, activities below, parts and
    parent assemblies beside it."""
    def q(s):
        return '"' + s.replace('"', "'") + '"'
    center = q("A:" + asset_id)
    lines = ['graph G {',
             'graph [rankdir=TB, bgcolor="transparent", pad=0.25, nodesep=0.25, ranksep=0.75];',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=12, penwidth=1.3, '
             'margin="0.14,0.06", height=0.36];',
             'edge [color="#B7C1CB", penwidth=1.1, fontname="Helvetica", fontsize=10, fontcolor="#6B7682"];',
             f'{center} [label={q(asset_id)}, fillcolor="{theme.INK}", fontcolor="white", fontsize=15, penwidth=0];']
    states = [(k, v) for k, v in p["states"].most_common(12) if k not in NOISE_STATES][:5]
    acts = p["activities"].most_common(5)
    parts = p["parts"].most_common(3)
    parents = p["part_of"].most_common(2)

    def node(prefix, name, color, fill):
        nid = q(f"{prefix}:{name}")
        lines.append(f'{nid} [label={q(name)}, color="{color}", fillcolor="{fill}", fontcolor="{theme.INK}"];')
        return nid

    for name, n in states:  # drawn above: edge points from fault to asset
        lines.append(f'{node("S", name, theme.FAULT, "#FFF4E3")} -- {center} [label={q(f" {n} WO")}];')
    for name, n in acts:
        lines.append(f'{center} -- {node("V", name, theme.ACTION, "#E3F5F1")} [label={q(f" {n} WO")}];')
    side = []
    for name, _n in parents:
        nid = node("O", name, theme.ASSET, "#E8EEF8")
        side.append(nid)
        lines.append(f'{nid} -- {center} [style=dashed, color="{theme.ASSET}"];')
    for name, _n in parts:
        nid = node("P", name, theme.ASSET, "#E8EEF8")
        side.append(nid)
        lines.append(f'{center} -- {nid} [style=dashed, color="{theme.ASSET}"];')
    if side:
        lines.append("{rank=same; " + " ".join(side[:len(parents)] + [center] + side[len(parents):]) + ";}")
    lines.append("}")
    return "\n".join(lines)


def render():
    st.title("Asset explorer")
    theme.lede("Everything the maintenance record says about one piece of equipment: how often it "
               "appears, what goes wrong with it, what is done to it and what it is part of.")
    components.require_neo4j()
    index = data.kg()

    c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
    with c1:
        term = st.text_input("Find equipment", value=st.session_state.get("asset_term", "pulley"),
                             placeholder="e.g. pump, hose, air conditioner")
    st.session_state["asset_term"] = term
    matches = index.search_entities(term, label="Asset", limit=60)
    if not matches:
        st.info(f"No equipment matches '{term}'. Try a shorter word such as 'pump' or 'hose'.")
        return
    with c2:
        asset_id = st.selectbox("Asset", [m[1] for m in matches],
                                format_func=lambda a: f"{a}  ({index.df(('Asset', a))} work orders)")

    p = index.asset_profile(asset_id)
    st.markdown(f"## {esc(asset_id)}")
    aliases = [a for a in p["aliases"] if a.lower() != asset_id.lower()]
    if aliases:
        st.caption("Also written as: " + ", ".join(aliases[:12]))
    theme.stat_strip([
        (f"{p['n_work_orders']:,}", "work orders mention it"),
        (f"{p['n_gold']:,}", "of them human-annotated"),
        (f"{p['fault_share']:.0%}", "report a fault or condition"),
        (f"{p['action_share']:.0%}", "record a maintenance action"),
    ])

    g1, g2 = st.columns(2)
    with g1:
        _bar(p["states"], theme.FAULT, "Reported faults and conditions")
    with g2:
        _bar(p["activities"], theme.ACTION, "Maintenance activities")

    st.markdown("### Neighbourhood in the knowledge graph")
    st.caption("Faults above, activities below, with work-order counts on each edge. Dashed blue links join the "
               "asset to its parts (right) and the assemblies it belongs to (left).")
    st.graphviz_chart(_dot(asset_id, p), use_container_width=False)

    if p["co_assets"]:
        st.markdown("### Often mentioned together")
        st.markdown(", ".join(f"{esc(a)} <span class='timing'>({n})</span>"
                              for a, n in p["co_assets"].most_common(14)), unsafe_allow_html=True)

    st.markdown(f"### Work orders ({p['n_work_orders']})")
    fault_opts = ["Any"] + [k for k, _ in p["states"].most_common(15) if k not in NOISE_STATES]
    act_opts = ["Any"] + [k for k, _ in p["activities"].most_common(15)]
    f1, f2, f3 = st.columns(3)
    f_state = f1.selectbox("Fault", fault_opts)
    f_act = f2.selectbox("Activity", act_opts)
    f_q = f3.selectbox("Annotation", ["Any", "gold", "silver"])
    rows = []
    for wo in p["work_orders"]:
        rec = index.wo_record(wo)
        groups = {(lab, index.group_name[index.group_of[(lab, eid)]]) for lab, eid in rec["entities"]}
        states = sorted(n for lab, n in groups if lab == "State" and n not in NOISE_STATES)
        acts = sorted(n for lab, n in groups if lab == "Activity")
        if f_state != "Any" and f_state not in states:
            continue
        if f_act != "Any" and f_act not in acts:
            continue
        if f_q != "Any" and rec["quality"] != f_q:
            continue
        rows.append({"Work order": wo, "Text": rec["text"], "Faults": ", ".join(states),
                     "Activities": ", ".join(acts), "Annotation": rec["quality"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(420, 38 + 35 * len(rows)))
