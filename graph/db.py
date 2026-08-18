import os
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

_driver = None


def _build_driver():
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not uri or not user or not password:
        raise RuntimeError(
            "Missing NEO4J_URI, NEO4J_USER, or NEO4J_PASSWORD environment variable(s). "
            "Set them in the environment or in a .env file (see .env.example)."
        )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        driver.close()
        raise RuntimeError(f"Could not connect to Neo4j at {uri}: {exc}") from exc
    return driver


def get_driver():
    global _driver
    if _driver is None:
        _driver = _build_driver()
    return _driver


@contextmanager
def get_session():
    session = get_driver().session()
    try:
        yield session
    finally:
        session.close()


def run_query(cypher, params=None):
    with get_session() as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]
