from __future__ import annotations

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT contact_id_unique IF NOT EXISTS FOR (c:Contact) REQUIRE c.contact_id IS UNIQUE",
    "CREATE CONSTRAINT interaction_id_unique IF NOT EXISTS FOR (i:Interaction) REQUIRE i.interaction_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id_unique IF NOT EXISTS FOR (cl:Claim) REQUIRE cl.claim_id IS UNIQUE",
    "CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_id IS UNIQUE",
    "CREATE INDEX contact_primary_email IF NOT EXISTS FOR (c:Contact) ON (c.primary_email)",
]
