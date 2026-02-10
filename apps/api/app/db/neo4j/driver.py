from __future__ import annotations

from contextlib import contextmanager

from neo4j import GraphDatabase

from app.core.config import get_settings


def get_driver():
    settings = get_settings()
    if not settings.neo4j_uri:
        return None
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


@contextmanager
def neo4j_session():
    driver = get_driver()
    if driver is None:
        yield None
        return
    with driver.session() as session:
        yield session
