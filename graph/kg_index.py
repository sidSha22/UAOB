"""In-memory projection of the MaintIE knowledge graph.

Graph retrieval and the asset explorer both need fast, repeated traversals
(entity linking against every alias, co-occurrence counts, "which assets have
X and Y" style joins). The connected part of the graph is small (~8k work
orders, ~3.4k entities, ~24k MENTIONS edges, ~16k relation edges), so instead
of issuing many small Cypher round trips per question we pull it once with
four read-only queries and keep it in process memory.

Neo4j stays the single source of truth: nothing here writes, and the index is
rebuilt by calling `get_index(refresh=True)` (the UI exposes a button for it).
"""

import math
import re
import sys
import threading
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import run_query  # noqa: E402

ENTITY_LABELS = ("Asset", "State", "Activity")
RELATION_TYPES = (
    "HAS_PART", "IS_A", "CONTAINS", "HAS_PROPERTY",
    "HAS_PARTICIPANT_PATIENT", "HAS_PARTICIPANT_AGENT",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STEM_SUFFIXES = ("ments", "ment", "ings", "ing", "ions", "ion", "ied", "ies", "ed", "es", "s")

Q_WORK_ORDERS = """
MATCH (w:WorkOrder)
RETURN w.id AS id, w.text AS text, w.annotation_quality AS quality
"""
Q_ENTITIES = """
MATCH (n)
WHERE n:Asset OR n:State OR n:Activity
RETURN labels(n)[0] AS label, n.id AS id, n.aliases AS aliases
"""
Q_MENTIONS = """
MATCH (w:WorkOrder)-[:MENTIONS]->(n)
RETURN w.id AS wo_id, labels(n)[0] AS label, n.id AS entity_id
"""
Q_RELATIONS = """
MATCH (h)-[r]->(t)
WHERE type(r) IN $types
RETURN labels(h)[0] AS head_label, h.id AS head_id, type(r) AS rel,
       labels(t)[0] AS tail_label, t.id AS tail_id,
       r.source_record_id AS wo_id
"""


def tokenize(text):
    return _TOKEN_RE.findall((text or "").lower())


def stem(word):
    """Very light suffix stripper so 'replacement', 'replaced', 'replacing'
    and 'replace' collapse to one key. Deliberately conservative."""
    w = word.lower()
    for suf in _STEM_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            if suf in ("ied", "ies"):
                w += "y"
            break
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def stem_phrase(phrase):
    return " ".join(stem(t) for t in tokenize(phrase))


def chunk_id_for_work_order(wo_id):
    # Same convention as ingest/chunk_work_orders.py
    return f"wochunk::{wo_id}"


class KGIndex:
    """Read-only, in-memory view of WorkOrder / Asset / State / Activity
    nodes and the edges between them."""

    def __init__(self, work_orders, entities, mentions, relations):
        self.work_orders = {}  # wo_id -> {"text", "quality"}
        for row in work_orders:
            self.work_orders[row["id"]] = {
                "text": row.get("text") or "",
                "quality": row.get("quality"),
            }

        self.entities = {}  # (label, id) -> {"aliases": [...]}
        for row in entities:
            self.entities[(row["label"], row["id"])] = {"aliases": list(row.get("aliases") or [])}

        self.wo_entities = defaultdict(set)   # wo_id -> {(label, id)}
        self.entity_wos = defaultdict(set)    # (label, id) -> {wo_id}
        for row in mentions:
            key = (row["label"], row["entity_id"])
            if row["wo_id"] not in self.work_orders:
                continue
            self.wo_entities[row["wo_id"]].add(key)
            self.entity_wos[key].add(row["wo_id"])

        self.relations = []  # (head_key, rel, tail_key, wo_id)
        self.relations_by_entity = defaultdict(list)
        for row in relations:
            h = (row["head_label"], row["head_id"])
            t = (row["tail_label"], row["tail_id"])
            rec = (h, row["rel"], t, row.get("wo_id"))
            self.relations.append(rec)
            self.relations_by_entity[h].append(rec)
            self.relations_by_entity[t].append(rec)

        self._build_lexicon()
        self._build_groups()

    # ------------------------------------------------------------------ build
    def _build_lexicon(self):
        """surface form (exact and stemmed) -> {(label, id)}"""
        self.lexicon = defaultdict(set)
        self.stem_lexicon = defaultdict(set)
        for key, data in self.entities.items():
            forms = {key[1], *data["aliases"]}
            for form in forms:
                norm = " ".join(tokenize(form))
                if not norm:
                    continue
                self.lexicon[norm].add(key)
                self.stem_lexicon[stem_phrase(norm)].add(key)
        self.max_ngram = max((len(k.split()) for k in self.lexicon), default=1)

    def _build_groups(self):
        """Group surface variants of the same concept (replace / replaced /
        replacement) under one canonical key, named after its most frequent
        member. Used for aggregation so counts are not split by spelling."""
        self.group_of = {}
        members = defaultdict(list)
        for key in self.entities:
            gkey = (key[0], stem_phrase(key[1]))
            self.group_of[key] = gkey
            members[gkey].append(key)
        self.group_members = dict(members)
        self.group_name = {}
        for gkey, keys in members.items():
            best = max(keys, key=lambda k: (len(self.entity_wos.get(k, ())), -len(k[1])))
            self.group_name[gkey] = best[1]

    # --------------------------------------------------------------- queries
    @property
    def n_work_orders(self):
        return len(self.work_orders)

    def df(self, key):
        return len(self.entity_wos.get(key, ()))

    def idf(self, key):
        return math.log((1 + self.n_work_orders) / (1 + self.df(key))) + 1.0

    def group_wos(self, gkey):
        wos = set()
        for k in self.group_members.get(gkey, ()):
            wos |= self.entity_wos.get(k, set())
        return wos

    def head_noun_family(self, key):
        """Entities of the same label whose last token matches and which
        contain every token of `key` -- e.g. 'pulley' -> 'tail pulley',
        'head pulley', 'jockey pulley'. Lets 'pulleys' mean all pulleys."""
        label, eid = key
        toks = eid.split()
        if not toks:
            return {key}
        head = stem(toks[-1])
        need = {stem(t) for t in toks}
        fam = {key}
        for other in self.entities:
            if other[0] != label:
                continue
            otoks = other[1].split()
            if otoks and stem(otoks[-1]) == head and need <= {stem(t) for t in otoks}:
                fam.add(other)
        return fam

    def wo_record(self, wo_id):
        wo = self.work_orders.get(wo_id, {})
        return {
            "wo_id": wo_id,
            "text": wo.get("text", ""),
            "quality": wo.get("quality"),
            "entities": sorted(self.wo_entities.get(wo_id, ())),
        }

    def search_entities(self, text, label=None, limit=20):
        """Prefix/substring search over entity ids and aliases, ranked by
        how many work orders mention the entity."""
        q = " ".join(tokenize(text))
        out = []
        for key, data in self.entities.items():
            if label and key[0] != label:
                continue
            forms = [key[1], *data["aliases"]]
            if not q or any(q in " ".join(tokenize(f)) for f in forms):
                out.append(key)
        out.sort(key=lambda k: (-self.df(k), k[1]))
        return out[:limit]

    def stats(self):
        return {
            "work_orders": self.n_work_orders,
            "gold_work_orders": sum(1 for w in self.work_orders.values() if w["quality"] == "gold"),
            "entities": Counter(k[0] for k in self.entities),
            "mentions": sum(len(v) for v in self.wo_entities.values()),
            "relations": Counter(r[1] for r in self.relations),
        }

    # ------------------------------------------------------- asset profile
    def asset_profile(self, asset_id):
        key = ("Asset", asset_id)
        wos = sorted(self.entity_wos.get(key, ()))
        states, activities, co_assets = Counter(), Counter(), Counter()
        with_state = 0
        with_activity = 0
        for wo in wos:
            ents = self.wo_entities.get(wo, ())
            has_state = has_act = False
            for lab, eid in ents:
                if lab == "State":
                    states[self.group_name[self.group_of[(lab, eid)]]] += 1
                    has_state = True
                elif lab == "Activity":
                    activities[self.group_name[self.group_of[(lab, eid)]]] += 1
                    has_act = True
                elif (lab, eid) != key:
                    co_assets[eid] += 1
            with_state += has_state
            with_activity += has_act

        parts, part_of, kinds = Counter(), Counter(), Counter()
        for h, rel, t, _wo in self.relations_by_entity.get(key, ()):
            if rel in ("HAS_PART", "CONTAINS"):
                if h == key and t[0] == "Asset":
                    parts[t[1]] += 1
                elif t == key and h[0] == "Asset":
                    part_of[h[1]] += 1
            elif rel == "IS_A":
                other = t if h == key else h
                if other[0] == "Asset":
                    kinds[other[1]] += 1

        n = len(wos)
        return {
            "asset_id": asset_id,
            "aliases": self.entities.get(key, {}).get("aliases", []),
            "n_work_orders": n,
            "n_gold": sum(1 for w in wos if self.work_orders[w]["quality"] == "gold"),
            "fault_share": (with_state / n) if n else 0.0,
            "action_share": (with_activity / n) if n else 0.0,
            "states": states,
            "activities": activities,
            "co_assets": co_assets,
            "parts": parts,
            "part_of": part_of,
            "related_kinds": kinds,
            "work_orders": wos,
        }


_INDEX = None
_LOCK = threading.Lock()


def build_index():
    return KGIndex(
        run_query(Q_WORK_ORDERS),
        run_query(Q_ENTITIES),
        run_query(Q_MENTIONS),
        run_query(Q_RELATIONS, {"types": list(RELATION_TYPES)}),
    )


def get_index(refresh=False):
    """Process-wide cached index. Thread-safe; first call takes ~1-3 s on
    the full MaintIE graph."""
    global _INDEX
    with _LOCK:
        if _INDEX is None or refresh:
            _INDEX = build_index()
        return _INDEX
