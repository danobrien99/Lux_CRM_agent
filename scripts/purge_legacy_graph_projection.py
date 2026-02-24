from __future__ import annotations

import argparse
from typing import Any

from app.db.neo4j.driver import neo4j_session


LEGACY_LABELS = ["Contact", "Interaction", "Entity", "Claim", "Evidence"]


def _label_counts(session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in LEGACY_LABELS:
        row = session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()
        counts[label] = int((row or {}).get("c", 0))
    orphan = session.run(
        """
        MATCH (s:ScoreSnapshot)
        WHERE NOT EXISTS { MATCH (:CRMContact)-[:HAS_SCORE]->(s) }
        RETURN count(s) AS c
        """
    ).single()
    counts["OrphanScoreSnapshot"] = int((orphan or {}).get("c", 0))
    return counts


def _delete_label(session, label: str) -> int:
    result = session.run(
        f"""
        MATCH (n:{label})
        WITH collect(n) AS nodes
        CALL {{
          WITH nodes
          UNWIND nodes AS n
          DETACH DELETE n
          RETURN count(*) AS deleted_count
        }}
        RETURN deleted_count
        """
    ).single()
    return int((result or {}).get("deleted_count", 0))


def _delete_orphan_score_snapshots(session) -> int:
    row = session.run(
        """
        MATCH (s:ScoreSnapshot)
        WHERE NOT EXISTS { MATCH (:CRMContact)-[:HAS_SCORE]->(s) }
        WITH collect(s) AS nodes
        CALL {
          WITH nodes
          UNWIND nodes AS s
          DETACH DELETE s
          RETURN count(*) AS deleted_count
        }
        RETURN deleted_count
        """
    ).single()
    return int((row or {}).get("deleted_count", 0))


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge legacy Neo4j graph projection labels/edges after V2 cutover.")
    parser.add_argument("--apply", action="store_true", help="Delete legacy projection nodes. Default is dry-run counts.")
    args = parser.parse_args()

    with neo4j_session() as session:
        if session is None:
            print("Neo4j unavailable")
            return

        before = _label_counts(session)
        print({"mode": "apply" if args.apply else "dry_run", "before": before})

        if not args.apply:
            return

        deleted: dict[str, Any] = {}
        for label in LEGACY_LABELS:
            deleted[label] = _delete_label(session, label)
        deleted["OrphanScoreSnapshot"] = _delete_orphan_score_snapshots(session)

        after = _label_counts(session)
        print({"deleted": deleted, "after": after})


if __name__ == "__main__":
    main()
