"""Graph-based retrieval over the MaintIE knowledge graph (L3 "Graph" path).

Pipeline:  entity linking -> query planning -> subgraph expansion /
aggregation -> path serialisation.

1. Entity linking: longest-match n-gram lookup of the question against every
   Asset / State / Activity id and alias (exact, then lightly stemmed), so
   "pulleys" links to `pulley` and "replacement" links to `replace`.
2. Query planning: a small rule-based router picks one plan from the
   question's wording (no LLM involved, fully deterministic):
     lookup      - "What was reported about the tail pulley?"
     rank        - "Which components are changed out most often?"
     both        - "Which assets have both a replacement and a repair?"
     pair        - "Which activities are most commonly paired with faults?"
     recurrence  - "Do any assets show the same fault more than once?"
     absence     - "...a fault was reported but no corrective activity..."
     part_of     - "Which components are parts of larger assemblies?"
3. Each plan returns (a) a serialised graph fact -- a short, human-readable
   summary of the aggregation with counts -- and (b) the supporting work
   orders, ranked, as ordinary evidence chunks.

Results have the same shape as retrieval/bm25.py and retrieval/dense.py, so
the generator, API, fusion and eval harness treat them uniformly. The graph
fact is returned with source_type 'graph' and a `graph::...` source id.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "graph"))
from kg_index import chunk_id_for_work_order, get_index, stem, tokenize  # noqa: E402

# Words that describe *what kind of thing* the user wants back. They are
# never linked as entities themselves.
TARGET_WORDS = {
    "Asset": {"asset", "assets", "component", "components", "part", "parts", "equipment",
              "item", "items", "machine", "machines", "unit", "units"},
    "Activity": {"activity", "activities", "action", "actions", "task", "tasks", "work"},
    "State": {"fault", "faults", "problem", "problems", "failure", "failures", "issue",
              "issues", "defect", "defects", "condition", "conditions"},
}
_META_WORDS = set().union(*TARGET_WORDS.values()) | {
    "maintenance", "record", "records", "recorded", "fleet", "data", "assembly",
    "assemblies", "report", "reported", "reports", "same", "pattern", "number",
    "corrective", "most", "often", "common", "commonly", "frequently", "range",
    "order", "orders", "work order", "fleet",
}
STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "to", "for", "with", "and", "or", "was", "were",
    "is", "are", "be", "been", "what", "which", "who", "where", "when", "how", "why",
    "do", "does", "did", "any", "there", "have", "has", "had", "about", "against", "them",
    "their", "it", "its", "this", "that", "these", "those", "by", "at", "from", "as",
    "show", "shown", "all", "across", "than", "more", "once", "but", "no", "not", "same",
    "been", "being", "done", "performed", "applied", "out", "up", "most", "widest",
    "different", "larger", "together", "paired", "being", "much", "many", "date",
}

_AGG_RE = re.compile(
    r"\b(which|most|often|frequent\w*|common\w*|widest|across|recurring|repeated|"
    r"more than once|pattern|both|top|rank\w*|how many|are there|do any|is there)\b")
_BOTH_RE = re.compile(r"\bboth\b")
_PAIR_RE = re.compile(r"\b(paired|pair|together|alongside|co-?occur\w*|combined|accompan\w*)\b")
_RECUR_RE = re.compile(r"\b(recurring|recurrent|repeated\w*|more than once|again|repeat\w*)\b")
_ABSENCE_RE = re.compile(r"\b(no|without|never|not)\b(?:\s+\w+){0,2}?\s+"
                         r"(corrective\s+)?(activit\w*|action\w*|work|fix\w*|repair\w*)")
_PART_RE = re.compile(r"\b(parts? of|part-of|sub-?components?|assembl\w+|made up of|consist\w* of)\b")
_SPREAD_RE = re.compile(r"\b(widest|range|different|variety|many different|diverse|spread)\b")

GOLD_BONUS = 0.05  # tie-breaker: prefer human-verified (gold) annotations

# Aggregation scope. MaintIE ships 1,076 human-annotated ("gold") and 7,000
# weakly-supervised ("silver") work orders. Counting over silver adds noise
# from automatic entity labels, so aggregate plans default to gold and fall
# back to all records only when gold has nothing. Lookups always use all.
SCOPES = ("gold", "all")
DEFAULT_SCOPE = "gold"


# --------------------------------------------------------------- linking
def _variants(phrase):
    yield phrase
    toks = phrase.split()
    if toks:
        last = toks[-1]
        for alt in (last[:-1] if last.endswith("s") else None,
                    last[:-2] if last.endswith("es") else None,
                    last + "s", last + "d", last + "ed"):
            if alt:
                yield " ".join(toks[:-1] + [alt])


def link_entities(query, index=None):
    """Greedy longest-match entity linking. Returns a list of
    {span, key, label, id, df} in query order."""
    index = index or get_index()
    toks = tokenize(query)
    used = [False] * len(toks)
    found = []
    for n in range(min(index.max_ngram, 6), 0, -1):
        for i in range(0, len(toks) - n + 1):
            if any(used[i:i + n]):
                continue
            span_toks = toks[i:i + n]
            if all(t in STOPWORDS or t in _META_WORDS for t in span_toks):
                continue
            if n == 1 and (len(span_toks[0]) < 3 or span_toks[0].isdigit()):
                continue
            span = " ".join(span_toks)
            keys = set()
            for v in _variants(span):
                keys = index.lexicon.get(v, set())
                if keys:
                    break
            if not keys:
                keys = index.stem_lexicon.get(" ".join(stem(t) for t in span_toks), set())
            if not keys:
                continue
            # Prefer the reading with the most work-order support.
            key = max(keys, key=lambda k: (index.df(k), k[0] == "Asset", k[0], k[1]))
            for j in range(i, i + n):
                used[j] = True
            found.append({"pos": i, "span": span, "key": key, "label": key[0],
                          "id": key[1], "df": len(index.group_wos(index.group_of[key]))})
    found.sort(key=lambda e: e["pos"])
    return found


def detect_targets(query):
    """Entity types the question asks for, in order of appearance."""
    toks = tokenize(query)
    order = []
    for t in toks:
        for label, words in TARGET_WORDS.items():
            if t in words and label not in order:
                order.append(label)
    return order


# --------------------------------------------------------------- planning
def plan_query(query, links):
    q = query.lower()
    targets = detect_targets(query)
    aggregate = bool(_AGG_RE.search(q)) and (bool(targets) or "most" in q)
    if not aggregate:
        return "lookup", targets
    if _PART_RE.search(q) and ("Asset" in targets or not targets):
        return "part_of", targets
    if _ABSENCE_RE.search(q):
        return "absence", targets
    if _RECUR_RE.search(q):
        return "recurrence", targets
    if _BOTH_RE.search(q) and sum(1 for e in links if e["label"] != "Asset") >= 2:
        return "both", targets
    if _PAIR_RE.search(q) and len(targets) >= 2:
        return "pair", targets
    return "rank", targets


# --------------------------------------------------------------- helpers
def _result(index, wo_id, score, why):
    wo = index.work_orders[wo_id]
    return {
        "chunk_id": chunk_id_for_work_order(wo_id),
        "text": wo["text"],
        "score": float(score),
        "source_type": "work_order",
        "source_id": wo_id,
        "annotation_quality": wo["quality"],
        "graph_path": why,
    }


def _fact(text, source_id, score):
    return {
        "chunk_id": source_id,
        "text": text,
        "score": float(score),
        "source_type": "graph",
        "source_id": source_id,
        "annotation_quality": None,
        "graph_path": "aggregation",
    }


def _wo_sort_key(index, wo_id):
    return (index.work_orders[wo_id]["quality"] != "gold", len(index.work_orders[wo_id]["text"]), wo_id)


def _scope_wos(index, scope):
    if scope == "gold":
        return {w for w, d in index.work_orders.items() if d["quality"] == "gold"}
    return set(index.work_orders)


def _constraint_wos(index, links, expand=True, scope="all"):
    """Work orders matching every linked entity (each entity widened to
    its spelling group and, for assets, its head-noun family), restricted
    to the annotation scope."""
    base = _scope_wos(index, scope)
    sets = []
    for e in links:
        keys = index.head_noun_family(e["key"]) if (expand and e["label"] == "Asset") else {e["key"]}
        wos = set()
        for k in keys:
            wos |= index.group_wos(index.group_of[k])
        sets.append(wos)
    out = base
    for s in sets:
        out = out & s
    return out


def _excluded_groups(index, links):
    groups = set()
    for e in links:
        for k in index.head_noun_family(e["key"]) if e["label"] == "Asset" else {e["key"]}:
            groups.add(index.group_of[k])
    return groups


def _round_robin(index, ranked_groups, group_wos, k, score_of):
    """Evidence for an aggregate answer: the top-ranked answer gets about
    half the slots (deep support for the headline claim), then example work
    orders from the next answers are interleaved so the runner-ups are
    supported too."""
    pools = [(g, sorted(group_wos[g], key=lambda w: _wo_sort_key(index, w))) for g in ranked_groups]
    out, seen, depth = [], set(), 0
    if pools:
        g0, pool0 = pools[0]
        for w in pool0[: max(1, (k + 1) // 2)]:
            seen.add(w)
            out.append((w, score_of(g0), g0))
    while len(out) < k and any(depth < len(p) for _, p in pools):
        for g, pool in pools:
            if depth < len(pool) and pool[depth] not in seen:
                seen.add(pool[depth])
                out.append((pool[depth], score_of(g), g))
                if len(out) >= k:
                    break
        depth += 1
    return out


def _gname(index, g):
    return index.group_name.get(g, g[1])


def _fmt_rank(index, ranked, counts, unit="work orders", top=8):
    return "; ".join(f"{_gname(index, g)} ({counts[g]} {unit})" for g in ranked[:top])


# --------------------------------------------------------------- plans
def _plan_lookup(index, links, k):
    if not links:
        return None, []
    scores = defaultdict(float)
    why = defaultdict(list)
    for e in links:
        idf = index.idf(e["key"])
        for wo in index.entity_wos.get(e["key"], ()):
            scores[wo] += idf
            why[wo].append(e["id"])
        if e["label"] == "Asset":  # weaker credit for the wider family
            for fk in index.head_noun_family(e["key"]) - {e["key"]}:
                for wo in index.entity_wos.get(fk, ()):
                    if e["id"] not in why[wo]:
                        scores[wo] += 0.5 * idf
                        why[wo].append(fk[1])
    for wo in scores:
        if index.work_orders[wo]["quality"] == "gold":
            scores[wo] += GOLD_BONUS
    ranked = sorted(scores, key=lambda w: (-scores[w], _wo_sort_key(index, w)))[:k]
    results = [_result(index, w, scores[w], "MENTIONS " + ", ".join(why[w])) for w in ranked]
    names = ", ".join(f"{e['label']} '{e['id']}' ({e['df']} work orders)" for e in links)
    fact = _fact(f"Graph lookup: linked {names}.", "graph::lookup", max(scores.values(), default=0))
    return fact, results


def _plan_rank(index, links, targets, query, k, scope):
    target = targets[0] if targets else "Asset"
    constraints = links
    pool = _constraint_wos(index, constraints, scope=scope)
    exclude = _excluded_groups(index, constraints)
    spread = bool(_SPREAD_RE.search(query.lower()))

    group_wos = defaultdict(set)
    group_assets = defaultdict(set)
    for wo in pool:
        ents = index.wo_entities.get(wo, ())
        for key in ents:
            if key[0] != target:
                continue
            g = index.group_of[key]
            if g in exclude:
                continue
            group_wos[g].add(wo)
            if spread:
                group_assets[g] |= {a for a in ents if a[0] == "Asset"}

    if spread:
        counts = {g: len(group_assets[g]) for g in group_wos}
        unit = "distinct assets"
    else:
        counts = {g: len(group_wos[g]) for g in group_wos}
        unit = "work orders"
    ranked = sorted(counts, key=lambda g: (-counts[g], g[1]))
    if not ranked:
        return None, []
    cons = " and ".join(f"{e['label']} '{e['id']}'" for e in constraints) or "the whole fleet"
    fact = _fact(
        f"Graph aggregation -- {target} ranked by {unit} (scope: {cons}, {len(pool)} work orders): "
        + _fmt_rank(index, ranked, counts, unit) + ".",
        f"graph::rank::{target}", counts[ranked[0]])
    picks = _round_robin(index, ranked[:5], group_wos, k, lambda g: counts[g])
    results = [_result(index, w, s, f"{target} '{_gname(index, g)}'") for w, s, g in picks]
    return fact, results


def _plan_both(index, links, targets, k, scope):
    target = next((t for t in targets if t == "Asset"), "Asset")
    conds = [e for e in links if e["label"] != target]
    allowed = _scope_wos(index, scope)
    per_cond = []
    for e in conds:
        wos = index.group_wos(index.group_of[e["key"]]) & allowed
        by_target = defaultdict(set)
        for wo in wos:
            for key in index.wo_entities.get(wo, ()):
                if key[0] == target:
                    by_target[index.group_of[key]].add(wo)
        per_cond.append(by_target)
    common = set(per_cond[0])
    for d in per_cond[1:]:
        common &= set(d)
    if not common:
        return None, []
    counts = {g: min(len(d[g]) for d in per_cond) for g in common}
    ranked = sorted(common, key=lambda g: (-counts[g], g[1]))
    # Evidence: for each top asset, alternate one work order per condition.
    picks, seen = [], set()
    for depth in range(50):
        for g in ranked[:6]:
            for d in per_cond:
                pool = sorted(d[g], key=lambda w: _wo_sort_key(index, w))
                if depth < len(pool) and pool[depth] not in seen and len(picks) < k:
                    seen.add(pool[depth])
                    picks.append((pool[depth], counts[g], g))
        if len(picks) >= k:
            break
    cond_names = " AND ".join(f"'{e['id']}'" for e in conds)
    detail = "; ".join(
        f"{_gname(index, g)} (" + ", ".join(f"{len(d[g])} {e['id']}" for e, d in zip(conds, per_cond)) + ")"
        for g in ranked[:8])
    fact = _fact(
        f"Graph join -- {len(common)} {target.lower()}s have work orders for both {cond_names}. "
        f"Strongest: {detail}.",
        "graph::both", len(common))
    results = [_result(index, w, s, f"{target} '{_gname(index, g)}' has {cond_names}") for w, s, g in picks]
    return fact, results


def _plan_pair(index, links, targets, k, scope):
    a, b = targets[0], targets[1]
    pool = _constraint_wos(index, links, scope=scope)
    pair_wos = defaultdict(set)
    for wo in pool:
        ents = index.wo_entities.get(wo, ())
        ga = {index.group_of[x] for x in ents if x[0] == a}
        gb = {index.group_of[x] for x in ents if x[0] == b}
        for x in ga:
            for y in gb:
                pair_wos[(x, y)].add(wo)
    # Rank first-target groups by how many work orders pair them with any b.
    a_wos = defaultdict(set)
    for (x, _y), wos in pair_wos.items():
        a_wos[x] |= wos
    counts = {g: len(w) for g, w in a_wos.items()}
    ranked = sorted(counts, key=lambda g: (-counts[g], g[1]))
    if not ranked:
        return None, []
    top_pairs = sorted(pair_wos, key=lambda p: -len(pair_wos[p]))[:6]
    pair_txt = "; ".join(f"{_gname(index, x)} + {_gname(index, y)} ({len(pair_wos[(x, y)])})" for x, y in top_pairs)
    fact = _fact(
        f"Graph co-occurrence -- {a} paired with {b} in the same work order: "
        + _fmt_rank(index, ranked, counts) + f". Most frequent pairs: {pair_txt}.",
        f"graph::pair::{a}-{b}", counts[ranked[0]])
    picks = _round_robin(index, ranked[:5], a_wos, k, lambda g: counts[g])
    results = [_result(index, w, s, f"{a} '{_gname(index, g)}' with {b}") for w, s, g in picks]
    return fact, results


def _plan_recurrence(index, links, k, scope):
    pool = _constraint_wos(index, links, scope=scope)
    pair_wos = defaultdict(set)
    for wo in pool:
        ents = index.wo_entities.get(wo, ())
        assets = {x for x in ents if x[0] == "Asset"}
        states = {index.group_of[x] for x in ents if x[0] == "State"}
        for a in assets:
            for s in states:
                pair_wos[(a, s)].add(wo)
    recurring = {p: w for p, w in pair_wos.items() if len(w) >= 2}
    if not recurring:
        return None, []
    ranked = sorted(recurring, key=lambda p: (-len(recurring[p]), p[0][1]))
    txt = "; ".join(f"{a[1]} -- '{_gname(index, s)}' x{len(recurring[(a, s)])}" for a, s in ranked[:8])
    fact = _fact(f"Graph recurrence -- {len(recurring)} asset/fault pairs reported in 2+ work orders. "
                 f"Top: {txt}.", "graph::recurrence", len(recurring[ranked[0]]))
    picks = _round_robin(index, ranked[:4], recurring, k, lambda p: len(recurring[p]))
    results = [_result(index, w, s, f"'{p[0][1]}' -- '{_gname(index, p[1])}' recurring") for w, s, p in picks]
    return fact, results


def _plan_absence(index, links, k, scope):
    pool = _constraint_wos(index, links, scope=scope)
    hits = []
    for wo in pool:
        labels = {x[0] for x in index.wo_entities.get(wo, ())}
        if "State" in labels and "Asset" in labels and "Activity" not in labels:
            hits.append(wo)
    if not hits:
        return None, []
    by_asset = Counter()
    for wo in hits:
        for x in index.wo_entities[wo]:
            if x[0] == "Asset":
                by_asset[x[1]] += 1
    hits.sort(key=lambda w: _wo_sort_key(index, w))
    fact = _fact(
        f"Graph absence check -- {len(hits)} work orders report an asset fault (State) with no "
        f"Activity recorded. Most affected assets: "
        + "; ".join(f"{a} ({n})" for a, n in by_asset.most_common(8)) + ".",
        "graph::absence", len(hits))
    results = [_result(index, w, 1.0, "Asset + State, no Activity") for w in hits[:k]]
    return fact, results


def _plan_part_of(index, links, k, scope):
    focus = {e["key"] for e in links if e["label"] == "Asset"}
    allowed = _scope_wos(index, scope)
    edges = [r for r in index.relations
             if r[1] in ("HAS_PART", "CONTAINS") and r[0][0] == "Asset" and r[2][0] == "Asset"
             and r[3] in allowed
             and (not focus or r[0] in focus or r[2] in focus)]
    if not edges:
        return None, []
    part_wos = defaultdict(set)
    parents = defaultdict(set)
    for h, _rel, t, wo in edges:
        if wo in index.work_orders:
            part_wos[t].add(wo)
            parents[t].add(h[1])
    ranked = sorted(part_wos, key=lambda p: (-len(part_wos[p]), p[1]))
    parent_counts = Counter(h[1] for h, _r, _t, _w in edges)
    txt = "; ".join(f"{p[1]} (part of {', '.join(sorted(parents[p])[:3])})" for p in ranked[:8])
    fact = _fact(
        f"Graph HAS_PART edges -- {len(part_wos)} components recorded as parts of larger assemblies. "
        f"Assemblies with most parts: " + "; ".join(f"{a} ({n})" for a, n in parent_counts.most_common(5))
        + f". Examples: {txt}.", "graph::part_of", len(edges))
    # Group evidence by parent assembly so examples cluster coherently.
    by_parent = defaultdict(set)
    for h, _rel, _t, wo in edges:
        if wo in index.work_orders:
            by_parent[h].add(wo)
    pr = sorted(by_parent, key=lambda p: (-len(by_parent[p]), p[1]))
    picks = _round_robin(index, pr[:3], by_parent, k, lambda p: len(by_parent[p]))
    results = [_result(index, w, s, f"'{p[1]}' HAS_PART") for w, s, p in picks]
    return fact, results


# --------------------------------------------------------------- public API
def explain(query):
    """Linking + plan, for the UI's 'how was this retrieved' panel."""
    index = get_index()
    links = link_entities(query, index)
    plan, targets = plan_query(query, links)
    return {"links": links, "plan": plan, "targets": targets}


def search(query: str, k: int = 10, include_fact: bool = True, scope: str = DEFAULT_SCOPE) -> list[dict]:
    """Graph retrieval. Returns up to k results (graph fact first when the
    plan produced one), same shape as bm25.search / dense.search.
    `scope` ('gold' | 'all') applies to aggregate plans only."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    index = get_index()
    links = link_entities(query, index)
    plan, targets = plan_query(query, links)

    planners = {
        "rank": lambda sc: _plan_rank(index, links, targets, query, k, sc),
        "both": lambda sc: _plan_both(index, links, targets, k, sc),
        "pair": lambda sc: _plan_pair(index, links, targets, k, sc),
        "recurrence": lambda sc: _plan_recurrence(index, links, k, sc),
        "absence": lambda sc: _plan_absence(index, links, k, sc),
        "part_of": lambda sc: _plan_part_of(index, links, k, sc),
    }
    fact, results, used_scope = None, [], scope
    if plan in planners:
        fact, results = planners[plan](scope)
        if not results and scope != "all":
            used_scope = "all"
            fact, results = planners[plan]("all")
    if not results:  # every non-lookup plan falls back to plain lookup
        plan, used_scope = "lookup", "all"
        fact, results = _plan_lookup(index, links, k)

    if fact is not None and plan != "lookup":
        label = "gold-annotated work orders only" if used_scope == "gold" else "gold + silver work orders"
        fact["text"] += f" [scope: {label}]"
    for r in results:
        r["graph_plan"] = plan
    out = results
    if include_fact and fact is not None:
        fact["graph_plan"] = plan
        out = [fact] + results[: max(0, k - 1)]
    return out[:k]


def _print_results(query, results):
    info = explain(query)
    print(f"\nQuery: {query!r}  plan={info['plan']}  targets={info['targets']}")
    print("  links:", [(e['label'], e['id']) for e in info["links"]])
    print("-" * 70)
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. score={r['score']:.3f}  ({r['source_type']}:{r['source_id']})")
        print(f"    {r['text'][:160]}")


if __name__ == "__main__":
    queries = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else [
        "What was reported about the tail pulley?",
        "Which components are changed out most often across the fleet?",
    ]
    for q in queries:
        _print_results(q, search(q, k=10))
