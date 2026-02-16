from __future__ import annotations

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT contact_id_unique IF NOT EXISTS FOR (c:Contact) REQUIRE c.contact_id IS UNIQUE",
    "CREATE CONSTRAINT interaction_id_unique IF NOT EXISTS FOR (i:Interaction) REQUIRE i.interaction_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id_unique IF NOT EXISTS FOR (cl:Claim) REQUIRE cl.claim_id IS UNIQUE",
    "CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE INDEX contact_primary_email IF NOT EXISTS FOR (c:Contact) ON (c.primary_email)",
    "CREATE INDEX entity_normalized_name IF NOT EXISTS FOR (e:Entity) ON (e.normalized_name)",
    "CREATE INDEX relation_predicate_norm IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.predicate_norm)",
]
