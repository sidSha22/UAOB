import sys

from db import get_driver, run_query
from schema import CONSTRAINT_LABELS, VECTOR_INDEX_NAME, apply_schema

results = []


def check(label, passed):
    print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    results.append(passed)
    return passed


def main():
    # 1. Connect
    try:
        get_driver()
        check("Connect to Neo4j", True)
    except Exception as exc:
        check(f"Connect to Neo4j ({exc})", False)
        report_and_exit()

    # 2. RETURN 1
    try:
        rows = run_query("RETURN 1 AS value")
        check("RETURN 1", rows == [{"value": 1}])
    except Exception as exc:
        check(f"RETURN 1 ({exc})", False)

    # 3. Create test node, read it back, delete it
    test_id = "smoke-test-node"
    try:
        run_query("CREATE (n:SmokeTest {id: $id})", {"id": test_id})
        rows = run_query(
            "MATCH (n:SmokeTest {id: $id}) RETURN n.id AS id", {"id": test_id}
        )
        check("Create + read back test node", rows == [{"id": test_id}])

        run_query("MATCH (n:SmokeTest {id: $id}) DELETE n", {"id": test_id})
        rows = run_query(
            "MATCH (n:SmokeTest {id: $id}) RETURN n.id AS id", {"id": test_id}
        )
        check("Delete test node", rows == [])
    except Exception as exc:
        check(f"Create/read/delete test node ({exc})", False)

    # 4. Apply schema (idempotent) so constraints/index are there to verify
    try:
        apply_schema()
        check("Apply schema (constraints + vector index)", True)
    except Exception as exc:
        check(f"Apply schema ({exc})", False)

    # 5. Confirm constraints exist
    try:
        constraints = run_query("SHOW CONSTRAINTS YIELD labelsOrTypes, properties")
        for label in CONSTRAINT_LABELS:
            found = any(
                label in (c.get("labelsOrTypes") or [])
                and "id" in (c.get("properties") or [])
                for c in constraints
            )
            check(f"Constraint exists: {label}.id UNIQUE", found)
    except Exception as exc:
        check(f"SHOW CONSTRAINTS ({exc})", False)

    # 6. Confirm vector index exists
    try:
        indexes = run_query("SHOW INDEXES YIELD name, type")
        found = any(
            i.get("name") == VECTOR_INDEX_NAME and i.get("type") == "VECTOR"
            for i in indexes
        )
        check(f"Vector index exists: {VECTOR_INDEX_NAME}", found)
    except Exception as exc:
        check(f"SHOW INDEXES ({exc})", False)

    report_and_exit()


def report_and_exit():
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    sys.exit(0 if passed == total and total > 0 else 1)


if __name__ == "__main__":
    main()
