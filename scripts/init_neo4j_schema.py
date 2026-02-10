from __future__ import annotations

from app.db.neo4j.driver import neo4j_session
from app.db.neo4j.schema import SCHEMA_STATEMENTS


def main() -> None:
    with neo4j_session() as session:
        if session is None:
            print("Neo4j URI not configured; skipping")
            return
        for statement in SCHEMA_STATEMENTS:
            session.run(statement)
            print(f"Applied: {statement}")


if __name__ == "__main__":
    main()
