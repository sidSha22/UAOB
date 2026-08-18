"""Load MaintIE gold + silver annotations into Neo4j. No model inference —
parses the corpus's existing entity/relation annotations only."""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "graph"))
from db import run_query  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "maintie" / "data"
GOLD_PATH = DATA_DIR / "gold_release.json"
SILVER_PATH = DATA_DIR / "silver_release.json"

BATCH_SIZE = 500
PIPELINE_VERSION = "v0-annotations"

# MaintIE top-level entity type -> graph node label.
# PhysicalObject/State/Activity are "connected" (get MENTIONS edges + can
# participate in mapped relation edges); Process/Property are not.
TOP_LEVEL_TO_LABEL = {
    "PhysicalObject": "Asset",
    "State": "State",
    "Activity": "Activity",
    "Process": "Process",
    "Property": "Property",
}
CONNECTED_LABELS = {"Asset", "State", "Activity"}

# Gold and silver use different relation-name vocabularies for the same
# semantics (verified by inspection): gold prefixes participant roles with
# "hasParticipant/", silver does not. Both map to the same edge type.
RELATION_MAP = {
    "hasPart": "HAS_PART",
    "isA": "IS_A",
    "contains": "CONTAINS",
    "hasProperty": "HAS_PROPERTY",
    "hasParticipant/hasPatient": "HAS_PARTICIPANT_PATIENT",
    "hasPatient": "HAS_PARTICIPANT_PATIENT",
    "hasParticipant/hasAgent": "HAS_PARTICIPANT_AGENT",
    "hasAgent": "HAS_PARTICIPANT_AGENT",
}

_WHITESPACE_RE = re.compile(r"\s+")
_ASSY_SUFFIX_RE = re.compile(r"\s+(assy|assembly)$")


def normalize_key(surface):
    """lowercase, collapse whitespace, strip trailing assy/assembly."""
    s = _WHITESPACE_RE.sub(" ", surface.lower()).strip()
    return _ASSY_SUFFIX_RE.sub("", s)


def normalize_simple(surface):
    """lowercase, collapse whitespace only (used for unconnected Process/Property)."""
    return _WHITESPACE_RE.sub(" ", surface.lower()).strip()


def load_records(path, quality):
    with open(path, encoding="utf-8") as f:
        raw_records = json.load(f)

    records = []
    for i, rec in enumerate(raw_records):
        tokens = rec["tokens"]
        entities = []
        for e in rec["entities"]:
            surface = " ".join(tokens[e["start"]:e["end"]])
            top_level = e["type"].split("/")[0]
            label = TOP_LEVEL_TO_LABEL[top_level]
            connected = label in CONNECTED_LABELS
            node_id = normalize_key(surface) if connected else normalize_simple(surface)
            entities.append({
                "surface": surface,
                "label": label,
                "id": node_id,
                "maintie_type": e["type"],
            })
        records.append({
            "wo_id": f"{quality}-{i:04d}",
            "text": rec["text"],
            "annotation_quality": quality,
            "entities": entities,
            "relations": rec["relations"],
        })
    return records


def build_graph_data(all_records):
    work_orders = []
    # (label, id) -> aggregated node data
    entity_nodes = {}
    # (wo_id, label, id) dedup set -> list of MENTIONS rows
    mentions_seen = set()
    mentions = []
    # (head_label, tail_label, edge_type) -> list of relation rows
    relation_groups = defaultdict(list)
    unmapped_relation_types = Counter()
    skipped_disconnected_endpoint = 0

    for rec in all_records:
        work_orders.append({
            "id": rec["wo_id"],
            "text": rec["text"],
            "annotation_quality": rec["annotation_quality"],
            "date": None,
            "normalisation": "pre_normalised_by_source",
        })

        for ent in rec["entities"]:
            key = (ent["label"], ent["id"])
            node = entity_nodes.get(key)
            if node is None:
                node = {
                    "id": ent["id"],
                    "aliases": set(),
                    "source_record_id": rec["wo_id"],
                    "maintie_type": ent["maintie_type"],
                }
                entity_nodes[key] = node
            node["aliases"].add(ent["surface"])

            if ent["label"] in CONNECTED_LABELS:
                mkey = (rec["wo_id"], ent["label"], ent["id"])
                if mkey not in mentions_seen:
                    mentions_seen.add(mkey)
                    mentions.append({"wo_id": rec["wo_id"], "label": ent["label"], "entity_id": ent["id"]})

        for rel in rec["relations"]:
            raw_type = rel["type"]
            head_ent = rec["entities"][rel["head"]]
            tail_ent = rec["entities"][rel["tail"]]

            edge_type = RELATION_MAP.get(raw_type)
            if edge_type is None:
                unmapped_relation_types[raw_type] += 1
                continue

            if head_ent["label"] not in CONNECTED_LABELS or tail_ent["label"] not in CONNECTED_LABELS:
                skipped_disconnected_endpoint += 1
                continue

            group_key = (head_ent["label"], tail_ent["label"], edge_type)
            relation_groups[group_key].append({
                "head_id": head_ent["id"],
                "tail_id": tail_ent["id"],
                "maintie_relation": raw_type,
                "head_surface_form": head_ent["surface"],
                "tail_surface_form": tail_ent["surface"],
                "source_record_id": rec["wo_id"],
            })

    # finalize aliases as sorted lists for stable, JSON-serialisable output
    for node in entity_nodes.values():
        node["aliases"] = sorted(node["aliases"])

    return {
        "work_orders": work_orders,
        "entity_nodes": entity_nodes,
        "mentions": mentions,
        "relation_groups": relation_groups,
        "unmapped_relation_types": unmapped_relation_types,
        "skipped_disconnected_endpoint": skipped_disconnected_endpoint,
    }


def run_batched(cypher, rows, batch_size=BATCH_SIZE):
    for i in range(0, len(rows), batch_size):
        run_query(cypher, {"rows": rows[i:i + batch_size]})


def write_work_orders(work_orders):
    cypher = f"""
    UNWIND $rows AS row
    MERGE (w:WorkOrder {{id: row.id}})
    SET w.text = row.text,
        w.annotation_quality = row.annotation_quality,
        w.date = row.date,
        w.normalisation = row.normalisation,
        w.source_record_id = row.id,
        w.pipeline_version = '{PIPELINE_VERSION}',
        w.synthetic = false
    """
    run_batched(cypher, work_orders)
    print(f"Wrote {len(work_orders)} WorkOrder nodes")


def write_entity_nodes(entity_nodes):
    by_label = defaultdict(list)
    for (label, node_id), node in entity_nodes.items():
        by_label[label].append({
            "id": node_id,
            "aliases": node["aliases"],
            "source_record_id": node["source_record_id"],
            "maintie_type": node["maintie_type"],
        })

    for label, rows in by_label.items():
        cypher = f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{id: row.id}})
        ON CREATE SET n.source_record_id = row.source_record_id,
                      n.maintie_type = row.maintie_type,
                      n.pipeline_version = '{PIPELINE_VERSION}',
                      n.synthetic = false
        SET n.aliases = row.aliases
        """
        run_batched(cypher, rows)
        print(f"Wrote {len(rows)} {label} nodes")


def write_mentions(mentions):
    by_label = defaultdict(list)
    for row in mentions:
        by_label[row["label"]].append(row)

    total = 0
    for label, rows in by_label.items():
        cypher = f"""
        UNWIND $rows AS row
        MATCH (w:WorkOrder {{id: row.wo_id}})
        MATCH (n:{label} {{id: row.entity_id}})
        MERGE (w)-[:MENTIONS]->(n)
        """
        run_batched(cypher, rows)
        total += len(rows)
    print(f"Wrote {total} MENTIONS edges")


def write_relations(relation_groups):
    total = 0
    for (head_label, tail_label, edge_type), rows in relation_groups.items():
        cypher = f"""
        UNWIND $rows AS row
        MATCH (h:{head_label} {{id: row.head_id}})
        MATCH (t:{tail_label} {{id: row.tail_id}})
        CREATE (h)-[r:{edge_type} {{
            maintie_relation: row.maintie_relation,
            head_surface_form: row.head_surface_form,
            tail_surface_form: row.tail_surface_form,
            source_record_id: row.source_record_id
        }}]->(t)
        """
        run_batched(cypher, rows)
        print(f"Wrote {len(rows)} ({head_label})-[:{edge_type}]->({tail_label}) edges")
        total += len(rows)
    return total


def report(unmapped_relation_types, skipped_disconnected_endpoint):
    print("\n" + "=" * 70)
    print("REPORT")
    print("=" * 70)

    print("\nNode counts by label:")
    for row in run_query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC"):
        print(f"  {row['label']:15s} {row['n']}")

    print("\nEdge counts by type:")
    for row in run_query("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS n ORDER BY n DESC"):
        print(f"  {row['rel']:25s} {row['n']}")

    print("\nUnmapped relation types (skipped, not in RELATION_MAP):")
    if unmapped_relation_types:
        for rel_type, count in unmapped_relation_types.most_common():
            print(f"  {rel_type:30s} {count}")
    else:
        print("  (none)")

    print(f"\nRelation instances skipped (endpoint is Process/Property, unconnected by design): "
          f"{skipped_disconnected_endpoint}")

    print("\nTop 20 assets by work-order count:")
    top_assets = run_query("""
        MATCH (a:Asset)<-[:MENTIONS]-(w:WorkOrder)
        RETURN a.id AS asset, a.aliases AS aliases, count(DISTINCT w) AS wo_count
        ORDER BY wo_count DESC
        LIMIT 20
    """)
    for row in top_assets:
        print(f"  {row['wo_count']:5d}  {row['asset']}  (aliases: {row['aliases']})")

    return top_assets


def checkpoint(top_assets):
    if not top_assets:
        print("\nCheckpoint skipped: no assets found.")
        return
    asset_id = top_assets[0]["asset"]
    print(f"\nCheckpoint: work orders mentioning top asset {asset_id!r}")
    rows = run_query("""
        MATCH (a:Asset {id: $asset_id})<-[:MENTIONS]-(w:WorkOrder)
        RETURN w.id AS wo_id, w.text AS text
        LIMIT 10
    """, {"asset_id": asset_id})
    for row in rows:
        print(f"  {row['wo_id']:12s} {row['text']}")


def main():
    print("Loading gold_release.json and silver_release.json ...")
    gold = load_records(GOLD_PATH, "gold")
    silver = load_records(SILVER_PATH, "silver")
    all_records = gold + silver
    print(f"Parsed {len(gold)} gold + {len(silver)} silver = {len(all_records)} records")

    data = build_graph_data(all_records)
    print(f"Built {len(data['entity_nodes'])} distinct entity nodes, "
          f"{sum(len(v) for v in data['relation_groups'].values())} mapped relation edges, "
          f"{len(data['mentions'])} WorkOrder-mentions-entity pairs")

    write_work_orders(data["work_orders"])
    write_entity_nodes(data["entity_nodes"])
    write_mentions(data["mentions"])
    write_relations(data["relation_groups"])

    top_assets = report(data["unmapped_relation_types"], data["skipped_disconnected_endpoint"])
    checkpoint(top_assets)


if __name__ == "__main__":
    main()
