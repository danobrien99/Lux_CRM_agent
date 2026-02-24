from __future__ import annotations

from app.services.ontology.runtime_contract import ontology_term_to_neo4j_identifier


def _ont_ident(term: str) -> str:
    return ontology_term_to_neo4j_identifier(term) or f"`{term.split(':', 1)[-1]}`"

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT contact_id_unique IF NOT EXISTS FOR (c:Contact) REQUIRE c.contact_id IS UNIQUE",
    "CREATE CONSTRAINT interaction_id_unique IF NOT EXISTS FOR (i:Interaction) REQUIRE i.interaction_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id_unique IF NOT EXISTS FOR (cl:Claim) REQUIRE cl.claim_id IS UNIQUE",
    "CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE INDEX contact_primary_email IF NOT EXISTS FOR (c:Contact) ON (c.primary_email)",
    "CREATE INDEX entity_normalized_name IF NOT EXISTS FOR (e:Entity) ON (e.normalized_name)",
    "CREATE INDEX relation_predicate_norm IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.predicate_norm)",
    # Graph V2 (CRM/Case/Evidence)
    "CREATE CONSTRAINT crm_contact_external_id_unique IF NOT EXISTS FOR (c:CRMContact) REQUIRE c.external_id IS UNIQUE",
    "CREATE CONSTRAINT crm_company_external_id_unique IF NOT EXISTS FOR (c:CRMCompany) REQUIRE c.external_id IS UNIQUE",
    "CREATE CONSTRAINT crm_opportunity_external_id_unique IF NOT EXISTS FOR (o:CRMOpportunity) REQUIRE o.external_id IS UNIQUE",
    "CREATE CONSTRAINT crm_engagement_external_id_unique IF NOT EXISTS FOR (e:CRMEngagement) REQUIRE e.external_id IS UNIQUE",
    "CREATE CONSTRAINT evidence_chunk_id_unique IF NOT EXISTS FOR (c:EvidenceChunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT extraction_event_id_unique IF NOT EXISTS FOR (e:ExtractionEvent) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT kg_assertion_id_unique IF NOT EXISTS FOR (a:KGAssertion) REQUIRE a.assertion_id IS UNIQUE",
    "CREATE CONSTRAINT case_contact_id_unique IF NOT EXISTS FOR (c:CaseContact) REQUIRE c.case_id IS UNIQUE",
    "CREATE CONSTRAINT case_opportunity_id_unique IF NOT EXISTS FOR (c:CaseOpportunity) REQUIRE c.case_id IS UNIQUE",
    "CREATE INDEX case_contact_status_idx IF NOT EXISTS FOR (c:CaseContact) ON (c.status)",
    "CREATE INDEX case_opportunity_status_idx IF NOT EXISTS FOR (c:CaseOpportunity) ON (c.status)",
    # Ontology-native physical projection (dual-written during migration)
    f"CREATE CONSTRAINT hs_contact_external_id_unique IF NOT EXISTS FOR (c:{_ont_ident('hs:Contact')}) REQUIRE c.external_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_company_external_id_unique IF NOT EXISTS FOR (c:{_ont_ident('hs:Company')}) REQUIRE c.external_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_deal_external_id_unique IF NOT EXISTS FOR (o:{_ont_ident('hs:Deal')}) REQUIRE o.external_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_engagement_external_id_unique IF NOT EXISTS FOR (e:{_ont_ident('hs:Engagement')}) REQUIRE e.external_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_score_snapshot_contact_asof_unique IF NOT EXISTS FOR (s:{_ont_ident('hs:ScoreSnapshot')}) REQUIRE (s.contact_id, s.asof) IS UNIQUE",
    f"CREATE CONSTRAINT hs_source_artifact_chunk_id_unique IF NOT EXISTS FOR (s:{_ont_ident('hs:SourceArtifact')}) REQUIRE s.chunk_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_extraction_event_id_unique IF NOT EXISTS FOR (e:{_ont_ident('hs:ExtractionEvent')}) REQUIRE e.event_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_assertion_id_unique IF NOT EXISTS FOR (a:{_ont_ident('hs:Assertion')}) REQUIRE a.assertion_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_case_contact_id_unique IF NOT EXISTS FOR (c:{_ont_ident('hs:CaseContact')}) REQUIRE c.case_id IS UNIQUE",
    f"CREATE CONSTRAINT hs_case_opportunity_id_unique IF NOT EXISTS FOR (c:{_ont_ident('hs:CaseOpportunity')}) REQUIRE c.case_id IS UNIQUE",
    f"CREATE INDEX hs_case_contact_status_idx IF NOT EXISTS FOR (c:{_ont_ident('hs:CaseContact')}) ON (c.status)",
    f"CREATE INDEX hs_case_opportunity_status_idx IF NOT EXISTS FOR (c:{_ont_ident('hs:CaseOpportunity')}) ON (c.status)",
]
