"""Evaluation dashboard over artifacts/eval_*.json written by eval/harness.py."""

from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st

import data
import theme
from theme import esc

ORDER = ["bm25", "dense", "graph", "hybrid"]
STRATA = [("single_hop", "Single-hop"), ("multi_hop", "Multi-hop"), ("overall", "Overall")]
METRICS = [("recall_at_10", "Recall@10", True), ("ndcg_at_10", "nDCG@10", False),
           ("hit_rate", "Hit rate", True), ("mean_rank_of_first_hit", "Mean rank of first hit", False)]


def _label(path):
    ts = path.stem.replace("eval_", "")
    try:
        dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%d %b %Y, %H:%M UTC")
    except ValueError:
        return ts


def _fmt(v, pct):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v:.3f}"


def render():
    st.title("Evaluation")
    theme.lede("Retrieval quality on the 24-question test set, by question type. Gold evidence was "
               "taken from human-annotated work orders. Run python eval/harness.py to add a new run.")

    runs = data.eval_runs()
    if not runs:
        st.info("No evaluation runs yet. Run python eval/harness.py from the repo root.")
        return
    path = st.selectbox("Run", runs, format_func=_label)
    run = data.load_eval(str(path), path.stat().st_mtime)
    agg = run["aggregate"]
    modes = [m for m in ORDER if m in agg]
    missing = [theme.MODE_NAMES[m] for m in ORDER if m not in agg]
    if missing:
        st.caption(f"This run has no results for {', '.join(missing)}. "
                   "Runs made before graph retrieval was added only cover BM25 and dense.")
    if run.get("errors"):
        st.caption("Skipped during this run: " + "; ".join(f"{k}: {v[:100]}" for k, v in run["errors"].items()))

    metric_key, metric_name, pct = METRICS[
        [m[1] for m in METRICS].index(st.segmented_control(
            "Metric", [m[1] for m in METRICS], default="Recall@10", key="eval_metric") or "Recall@10")]

    rows = []
    for m in modes:
        for sk, sname in STRATA:
            v = agg[m].get(sk, {}).get(metric_key)
            rows.append({"Retriever": theme.MODE_NAMES[m], "mode": m, "Questions": sname, "value": v,
                         "label": _fmt(v, pct)})
    df = pd.DataFrame(rows)
    plot = df.dropna(subset=["value"])
    if not plot.empty:
        domain = [theme.MODE_NAMES[m] for m in modes]
        colors = [theme.MODE_COLORS[m] for m in modes]
        y_scale = alt.Scale(domain=[0, 1]) if (pct or metric_key == "ndcg_at_10") else alt.Scale(zero=True)
        base = alt.Chart().encode(
            x=alt.X("Retriever:N", sort=domain, title=None, axis=None),
            y=alt.Y("value:Q", title=metric_name, scale=y_scale,
                    axis=alt.Axis(format=".0%" if pct else ".2f", gridColor="#E6EBF0", domain=False,
                                  ticks=False, labelColor=theme.INK_3, titleColor=theme.INK_3)),
            color=alt.Color("Retriever:N", sort=domain, scale=alt.Scale(domain=domain, range=colors),
                            legend=alt.Legend(orient="top", title=None, labelFontSize=13)),
            tooltip=["Retriever", "Questions", alt.Tooltip("label:N", title=metric_name)],
        )
        bars = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke="white", strokeWidth=2)
        text = base.mark_text(dy=-7, fontSize=11).encode(text="label:N", color=alt.value(theme.INK_2))
        chart = alt.layer(bars, text, data=plot).properties(width=alt.Step(46), height=300).facet(
            column=alt.Column("Questions:N", sort=[s_[1] for s_ in STRATA], title=None,
                              header=alt.Header(labelFontSize=14, labelColor=theme.INK, labelOrient="bottom")),
            spacing=36,
        )
        st.altair_chart(chart.configure_view(strokeWidth=0).configure(font="Barlow"),
                        use_container_width=False)
        if metric_key == "mean_rank_of_first_hit":
            st.caption("Lower is better. Averaged only over questions with at least one hit.")

    st.markdown("### All metrics")
    table = []
    for m in modes:
        for sk, sname in STRATA:
            a = agg[m].get(sk, {})
            table.append({"Retriever": theme.MODE_NAMES[m], "Questions": sname,
                          "n": a.get("n_questions"),
                          **{name: _fmt(a.get(key), p) for key, name, p in METRICS}})
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True,
                 height=38 + 35 * len(table))

    un = [(theme.MODE_NAMES[m], agg[m].get("unanswerable", {})) for m in modes]
    if any(u for _, u in un):
        with st.expander("Unanswerable questions: retrieval scores"):
            st.caption("These questions have no answer in the data (cost, supplier, dates). Scores here are "
                       "inputs for a future confidence threshold, not correctness metrics. Scales differ per retriever.")
            st.dataframe(pd.DataFrame([{"Retriever": n, "Questions": u.get("n_questions"),
                                        "Mean top-1 score": u.get("mean_top1_score"),
                                        "Mean top-10 score": u.get("mean_top10_score")} for n, u in un]),
                         hide_index=True)

    st.markdown("### Question by question")
    pq = run["per_question"]
    qids = [r["id"] for r in pq[modes[0]]]
    texts = {r["id"]: r for r in pq[modes[0]]}
    grid = []
    for qid in qids:
        row = {"Question": f"{qid}  {texts[qid]['text']}", "Type": texts[qid]["stratum"].replace("_", "-")}
        for m in modes:
            r = next(x for x in pq[m] if x["id"] == qid)
            if r["stratum"] == "unanswerable":
                row[theme.MODE_NAMES[m]] = "n/a"
            else:
                rank = r.get("rank_of_first_hit")
                row[theme.MODE_NAMES[m]] = f"hit at {rank}" if rank else "miss"
        grid.append(row)
    st.dataframe(pd.DataFrame(grid), hide_index=True, use_container_width=True, height=38 + 35 * len(grid))

    qid = st.selectbox("Inspect a question", qids, format_func=lambda i: f"{i}  {texts[i]['text']}")
    gold = set(texts[qid]["gold_evidence"])
    st.caption(f"Gold evidence: {', '.join(sorted(gold)) or 'none (unanswerable)'}")
    cols = st.columns(len(modes))
    for col, m in zip(cols, modes):
        r = next(x for x in pq[m] if x["id"] == qid)
        with col:
            st.markdown(f'<h4 style="border-bottom:3px solid {theme.MODE_COLORS[m]}">{theme.MODE_NAMES[m]}</h4>',
                        unsafe_allow_html=True)
            items = []
            for i, x in enumerate(r["retrieved"], 1):
                hit = x["source_id"] in gold
                mark = f'<b style="color:{theme.ACTION}">hit</b> ' if hit else ""
                items.append(f'<div class="lab-item"><span class="r">{i}</span>{mark}{esc(x["source_id"])}</div>')
            st.markdown("".join(items), unsafe_allow_html=True)
