from __future__ import annotations

from pathlib import Path

from app.db.neo4j.driver import neo4j_session


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ontology_ttl = _read(repo_root / ".CODEX" / "CRM_ontoology_spec.ttl")
    shacl_gatekeeper = _read(repo_root / ".CODEX" / "SHACL_gatekeeper.ttl")
    shacl_shapes = _read(repo_root / ".CODEX" / "lux_CRM_shapes.ttl")

    with neo4j_session() as session:
        if session is None:
            print("Neo4j URI not configured; skipping")
            return

        try:
            session.run(
                """
                CALL n10s.graphconfig.init({
                    handleVocabUris: 'SHORTEN',
                    handleMultival: 'ARRAY'
                })
                """
            ).consume()
            print("Initialized n10s graph config")
        except Exception as exc:
            print(f"n10s graph config init skipped/failed: {exc}")

        for prefix, ns in [
            ("hs", "https://luxcrm.ai/ontologies/hubspot-crm#"),
            ("sh", "http://www.w3.org/ns/shacl#"),
            ("xsd", "http://www.w3.org/2001/XMLSchema#"),
        ]:
            try:
                session.run("CALL n10s.nsprefixes.add($prefix, $ns)", prefix=prefix, ns=ns).consume()
            except Exception:
                # Prefix may already exist; proceed.
                pass

        try:
            result = session.run(
                "CALL n10s.onto.import.inline($ttl, 'Turtle')",
                ttl=ontology_ttl,
            ).data()
            print(f"Ontology import result rows: {len(result)}")
        except Exception as exc:
            print(f"Ontology import failed: {exc}")

        try:
            session.run(
                "CALL n10s.validation.shacl.import.inline($ttl, 'Turtle')",
                ttl=shacl_gatekeeper,
            ).consume()
            print("Imported SHACL gatekeeper")
        except Exception as exc:
            print(f"SHACL gatekeeper import failed: {exc}")

        try:
            session.run(
                "CALL n10s.validation.shacl.import.inline($ttl, 'Turtle')",
                ttl=shacl_shapes,
            ).consume()
            print("Imported SHACL shapes")
        except Exception as exc:
            print(f"SHACL shapes import failed: {exc}")


if __name__ == "__main__":
    main()
