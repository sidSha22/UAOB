"""Visual system for the UAOB Streamlit app: CSS tokens, HTML helpers and
the MaintIE-style entity annotation renderer."""

import html
import re

import streamlit as st

# Colour tokens. Entity colours encode MaintIE entity types everywhere in
# the app (query annotation, evidence, charts, graph view). Validated for
# colour-vision deficiency with the dataviz palette checker.
PAPER = "#F4F6F8"
SURFACE = "#FFFFFF"
INK = "#17202A"
INK_2 = "#4A5663"
INK_3 = "#6B7682"
RULE = "#D3DAE1"
ASSET = "#1F5FB4"
FAULT = "#B86E00"
ACTION = "#008A73"
GRAPH = "#7B4FC4"

ENTITY_COLORS = {"Asset": ASSET, "State": FAULT, "Activity": ACTION}
ENTITY_NAMES = {"Asset": "asset", "State": "fault / state", "Activity": "activity"}
MODE_COLORS = {"bm25": FAULT, "dense": GRAPH, "graph": ACTION, "hybrid": ASSET}
MODE_NAMES = {"hybrid": "Hybrid", "bm25": "BM25", "dense": "Dense", "graph": "Graph"}

PLAN_DESCRIPTIONS = {
    "lookup": "Lookup: work orders that mention the linked entities",
    "rank": "Ranking: counts over the graph, with example work orders",
    "both": "Join: entities that have every linked condition",
    "pair": "Co-occurrence: what appears together in the same work order",
    "recurrence": "Recurrence: the same fault on the same asset more than once",
    "absence": "Absence: a fault was recorded with no activity",
    "part_of": "Structure: HAS_PART edges between assets",
}

CSS = f"""
<style>
:root {{
  --paper: {PAPER}; --surface: {SURFACE}; --ink: {INK}; --ink-2: {INK_2}; --ink-3: {INK_3};
  --rule: {RULE}; --asset: {ASSET}; --fault: {FAULT}; --action: {ACTION}; --graph: {GRAPH};
}}
html, body, .stApp, .stApp p, .stApp div, .stApp label, .stApp li, .stApp a, .stApp td, .stApp th,
.stApp input, .stApp textarea, .stApp button, .stApp select,
.stApp span:not([data-testid="stIconMaterial"]):not(.material-symbols-rounded) {{
  font-family: 'Barlow', system-ui, -apple-system, 'Segoe UI', sans-serif;
}}
.stApp code {{ font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; }}
section.main .stButton > button[kind="secondary"], [data-testid="stMain"] .stButton > button[kind="secondary"] {{
  justify-content: flex-start; text-align: left; background: var(--surface); }}
[data-testid="stMain"] .stButton > button[kind="secondary"] p {{ text-align: left; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] a {{ border-radius: 8px; }}
[data-testid="stSidebar"] [data-testid="stPageLink"] p {{ font-size: 1rem; }}
.stApp {{ background: var(--paper); color: var(--ink); }}
.block-container {{ max-width: 1120px; padding-top: 2.2rem; padding-bottom: 4rem; }}
h1, h2, h3, h4 {{ font-family: 'Barlow Semi Condensed', 'Barlow', sans-serif !important;
  color: var(--ink) !important; letter-spacing: -0.005em; }}
h1 {{ font-weight: 700 !important; font-size: 2.15rem !important; line-height: 1.1 !important; }}
h2 {{ font-weight: 600 !important; font-size: 1.45rem !important; }}
h3 {{ font-weight: 600 !important; font-size: 1.15rem !important; }}
[data-testid="stSidebar"] {{ background: var(--surface); border-right: 1px solid var(--rule); }}
[data-testid="stCaptionContainer"], .stCaption {{ color: var(--ink-3) !important; }}
:focus-visible {{ outline: 2px solid var(--asset) !important; outline-offset: 2px; }}
.num {{ font-variant-numeric: tabular-nums; }}

/* brand block in the sidebar */
.brand {{ display: flex; gap: .7rem; align-items: center; margin: .2rem 0 1rem; }}
.brand-mark {{ width: 38px; height: 38px; border-radius: 9px; background: var(--ink);
  display: grid; place-items: center; flex: none; }}
.brand-name {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow', system-ui, sans-serif; font-weight: 700; font-size: 1.35rem; line-height: 1; }}
.brand-sub {{ color: var(--ink-3); font-size: .8rem; line-height: 1.2; margin-top: .15rem; }}

/* status lines */
.status {{ display: flex; align-items: baseline; gap: .5rem; font-size: .88rem; color: var(--ink-2); margin: .15rem 0; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; flex: none; transform: translateY(-1px); }}
.dot.ok {{ background: var(--action); }} .dot.warn {{ background: var(--fault); }} .dot.off {{ background: #B3261E; }}

/* page intro */
.lede {{ color: var(--ink-2); font-size: 1.05rem; max-width: 68ch; margin: -.35rem 0 1.2rem; line-height: 1.5; }}

/* annotated text: the MaintIE look */
.annot {{ font-size: 1.02rem; line-height: 2.05; color: var(--ink); }}
.annot.big {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow', system-ui, sans-serif; font-weight: 500; font-size: 1.6rem; line-height: 1.9; }}
.ent {{ padding: .05em .18em .1em; border-radius: 3px; border-bottom: 2.5px solid currentColor;
  white-space: nowrap; position: relative; }}
.ent-Asset {{ color: var(--asset); background: rgba(31,95,180,.08); }}
.ent-State {{ color: var(--fault); background: rgba(184,110,0,.09); }}
.ent-Activity {{ color: var(--action); background: rgba(0,138,115,.08); }}
.ent .tag {{ font-family: 'Barlow', system-ui, -apple-system, 'Segoe UI', sans-serif; font-size: .62rem; font-weight: 600; position: absolute;
  left: .18em; bottom: -1.45em; line-height: 1; white-space: nowrap; opacity: .9; }}
.annot.big .ent .tag {{ font-size: .7rem; bottom: -1.3em; }}
.annot.big .ent span.inner {{ color: var(--ink); }}

.legend {{ display: flex; flex-wrap: wrap; gap: 1rem; font-size: .82rem; color: var(--ink-2); margin: .3rem 0 0; }}
.legend i {{ display: inline-block; width: 11px; height: 11px; border-radius: 2px; margin-right: .35rem; vertical-align: -1px; }}

/* plan line under the question */
.plan {{ display: inline-flex; flex-wrap: wrap; gap: .25rem .6rem; align-items: center; margin-top: .9rem; padding: .35rem .7rem;
  border: 1px solid var(--rule); border-radius: 14px; background: var(--surface); font-size: .86rem; color: var(--ink-2); }}
.plan b {{ color: var(--ink); font-weight: 600; }}

/* answer */
.answer {{ background: var(--surface); border: 1px solid var(--rule); border-left: 4px solid var(--ink);
  border-radius: 10px; padding: 1.15rem 1.35rem; font-size: 1.08rem; line-height: 1.65; max-width: 78ch; }}
.cite {{ display: inline-block; font-size: .72rem; font-weight: 600; color: var(--surface) !important;
  background: var(--ink); border-radius: 4px; padding: .02rem .32rem; margin: 0 .08rem; text-decoration: none !important;
  vertical-align: .12em; }}
.cite:hover {{ background: var(--asset); }}
.note {{ border: 1px solid var(--rule); border-left: 4px solid var(--fault); background: #FFF9EF;
  border-radius: 10px; padding: .85rem 1.1rem; color: var(--ink); max-width: 78ch; font-size: .95rem; line-height: 1.5; }}
.note.bad {{ border-left-color: #B3261E; background: #FDF3F2; }}
.note code, .ev code {{ font-size: .85em; color: var(--ink); background: rgba(23,32,42,.06); }}

/* evidence */
.ev {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 10px;
  padding: .75rem 1rem .85rem; margin-bottom: .55rem; scroll-margin-top: 4rem; }}
.ev.cited {{ border-color: #9AA7B4; box-shadow: inset 4px 0 0 var(--ink); }}
.ev.muted {{ opacity: .82; }}
.ev-head {{ display: flex; flex-wrap: wrap; gap: .45rem .7rem; align-items: center; font-size: .82rem; color: var(--ink-3); margin-bottom: .3rem; }}
.eid {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow', system-ui, sans-serif; font-weight: 700; font-size: .95rem; color: var(--ink); min-width: 1.9rem; }}
.src {{ font-weight: 600; border-radius: 4px; padding: .05rem .4rem; font-size: .76rem; }}
.src-work_order {{ background: #E8EEF6; color: #1B4F94; }}
.src-manual {{ background: #EEE9F7; color: #5B3A99; }}
.src-graph {{ background: #E3F3EF; color: #00664F; }}
.q-gold {{ color: #7A4A00; font-weight: 600; }}
.ev-text {{ color: var(--ink); line-height: 1.85; font-size: 1rem; }}
.ev-text.manual {{ font-size: .93rem; line-height: 1.55; max-height: 9.5em; overflow: hidden;
  -webkit-mask-image: linear-gradient(180deg, #000 70%, transparent); }}
.ev-text.graph {{ line-height: 1.55; }}
.ranks {{ color: var(--ink-3); }}

/* stat strip */
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .6rem; margin: .4rem 0 1.2rem; }}
.stat {{ background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: .7rem .9rem; }}
.stat .v {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow', system-ui, sans-serif; font-weight: 700; font-size: 1.7rem; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.stat .l {{ color: var(--ink-3); font-size: .84rem; margin-top: .1rem; }}

/* lab columns */
.lab-item {{ border-top: 1px solid var(--rule); padding: .45rem 0 .5rem; font-size: .9rem; line-height: 1.45; }}
.lab-item .r {{ font-family: 'Barlow Semi Condensed', 'Arial Narrow', system-ui, sans-serif; font-weight: 700; color: var(--ink-3); margin-right: .3rem; }}
.lab-item .meta {{ color: var(--ink-3); font-size: .78rem; margin-top: .1rem; }}
.shared {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 3px; vertical-align: 0; }}
.timing {{ color: var(--ink-3); font-size: .8rem; }}

.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {{ background: var(--ink); border-color: var(--ink); }}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {{ background: var(--asset); border-color: var(--asset); }}

@media (max-width: 640px) {{
  .annot.big {{ font-size: 1.25rem; }}
  .block-container {{ padding-left: 1rem; padding-right: 1rem; }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""

BRAND_SVG = (
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
    f'<circle cx="6" cy="6" r="2.6" fill="{ASSET}"/><circle cx="18" cy="7" r="2.6" fill="{FAULT}"/>'
    f'<circle cx="12" cy="18" r="2.6" fill="{ACTION}"/>'
    '<path d="M8.3 6.3 15.6 6.8M7.2 8.2 10.8 15.7M16.8 9.3 13.3 15.8" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>'
    '</svg>'
)


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(text):
    return html.escape(str(text if text is not None else ""))


def legend_html():
    items = "".join(f'<span><i style="background:{ENTITY_COLORS[k]}"></i>{ENTITY_NAMES[k]}</span>'
                    for k in ("Asset", "State", "Activity"))
    return f'<div class="legend" aria-label="Entity colour key">{items}</div>'


def annotate(text, entities, big=False, tags=True):
    """Render text with entity spans highlighted MaintIE-style.
    entities: iterable of (label, surface_form). Longest match wins;
    matching is case-insensitive on word boundaries. Returns HTML."""
    text = text or ""
    forms = []
    for label, surface in entities:
        if label in ENTITY_COLORS and surface and len(surface.strip()) >= 2:
            forms.append((label, surface.strip()))
    forms.sort(key=lambda f: -len(f[1]))
    taken = [None] * len(text)
    spans = []
    lower = text.lower()
    for label, surface in forms:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(surface.lower()) + r"(?![a-z0-9])")
        for m in pat.finditer(lower):
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = label
            spans.append((m.start(), m.end(), label))
    spans.sort()
    out, pos = [], 0
    for s, e, label in spans:
        out.append(esc(text[pos:s]))
        tag = f'<span class="tag">{ENTITY_NAMES[label]}</span>' if tags else ""
        out.append(f'<span class="ent ent-{label}" title="{ENTITY_NAMES[label]}">'
                   f'<span class="inner">{esc(text[s:e])}</span>{tag}</span>')
        pos = e
    out.append(esc(text[pos:]))
    cls = "annot big" if big else "annot"
    return f'<div class="{cls}">{"".join(out)}</div>'


_CITE_RE = re.compile(r"\(\s*(E\d+(?:\s*,\s*E\d+)*)\s*\)")


def answer_html(answer):
    """Turn '(E1, E3)' citation groups into anchor chips that jump to the
    evidence card."""
    def repl(m):
        ids = [x.strip() for x in m.group(1).split(",")]
        return "".join(f'<a class="cite" href="#ev-{i}" title="Jump to evidence {i}">{i}</a>' for i in ids)
    parts = []
    last = 0
    for m in _CITE_RE.finditer(answer):
        parts.append(esc(answer[last:m.start()]))
        parts.append(repl(m))
        last = m.end()
    parts.append(esc(answer[last:]))
    return f'<div class="answer">{"".join(parts)}</div>'


def stat_strip(items):
    """items: list of (value, label)."""
    cells = "".join(f'<div class="stat"><div class="v">{esc(v)}</div><div class="l">{esc(l)}</div></div>'
                    for v, l in items)
    st.markdown(f'<div class="stats">{cells}</div>', unsafe_allow_html=True)


def note(html_body, bad=False):
    st.markdown(f'<div class="note{" bad" if bad else ""}">{html_body}</div>', unsafe_allow_html=True)


def lede(text):
    st.markdown(f'<p class="lede">{esc(text)}</p>', unsafe_allow_html=True)
