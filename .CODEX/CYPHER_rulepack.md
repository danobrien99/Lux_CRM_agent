Key conventions used in this pack

Derived edges are written as the same relationship type as the ontology property (e.g., :engagedCompany) but marked with:

derived = true

ruleId = 'R1' (etc.)

derivedAt = datetime()

evidence = 'canonical' | 'assertion'

The job is idempotent by deleting existing derived edges for a rule before recomputing.

Gating strategy:

Prefer canonical (:Person)-[:worksAt]->(:Company) relationships

Only use assertion-derived worksAt if:

assertion has hasVerificationStatus -> (:VerificationStatus {id:'verified' or uri ends with '#verified'}) OR property verificationStatus='verified'

and confidenceValue >= $minConfidence

and (optional) recency assertedAt >= datetime() - duration({days:$maxAgeDays})

Save as luxcrm-inference.cypher


///////////////////////////////////////////////////////////////////////////
// PARAMETERS (tune these)
///////////////////////////////////////////////////////////////////////////
:param minConfidence => 0.85;
:param maxAgeDays    => 180;

///////////////////////////////////////////////////////////////////////////
// HELPER: Identify "verified" assertions in two possible encodings:
// A) relationship to VerificationStatus node via :hasVerificationStatus
// B) property on assertion: verificationStatus = 'verified'
///////////////////////////////////////////////////////////////////////////

///////////////////////////////////////////////////////////////////////////
// RULE R1
// Infer engagedCompany(e, c) from engagedWith(e, p) AND worksAt(p, c)
// Gating: use canonical worksAt OR verified assertion-backed worksAt.
//
// Output: (e)-[:engagedCompany {derived:true, ruleId:'R1', ...}]->(c)
//
// Notes:
// - We write engagedCompany only if it does not already exist as non-derived.
// - We rebuild derived edges each run.
///////////////////////////////////////////////////////////////////////////

// 1) Delete previous derived edges for R1
MATCH (:Engagement)-[r:engagedCompany {derived:true, ruleId:'R1'}]->(:Company)
DELETE r;

// 2) Canonical path inference
MATCH (e:Engagement)-[:engagedWith]->(p:Person)-[:worksAt]->(c:Company)
WHERE NOT (e)-[:engagedCompany {derived:false}]->(c) // keep canonical untouched if present
  AND NOT (e)-[:engagedCompany]->(c)                // avoid duplicates of any kind
MERGE (e)-[r:engagedCompany]->(c)
ON CREATE SET r.derived = true,
              r.ruleId = 'R1',
              r.evidence = 'canonical',
              r.derivedAt = datetime();

// 3) Assertion-backed worksAt (only if no canonical worksAt exists for that person)
MATCH (e:Engagement)-[:engagedWith]->(p:Person)
WHERE NOT (p)-[:worksAt]->(:Company)
MATCH (a:Assertion)-[:assertionSubject]->(p)
MATCH (a)-[:assertionPredicate]->(pred)
WHERE (pred:owl_ObjectProperty AND pred.name = 'worksAt') OR pred.name = 'hs:worksAt' OR pred.localName = 'worksAt'
MATCH (a)-[:assertionObject]->(c:Company)
WHERE coalesce(a.confidenceValue, 0.0) >= $minConfidence
  AND (a.assertedAt IS NULL OR a.assertedAt >= datetime() - duration({days:$maxAgeDays}))
  AND (
        exists( (a)-[:hasVerificationStatus]->(:VerificationStatus {id:'verified'}) ) OR
        exists( (a)-[:hasVerificationStatus]->(:VerificationStatus) ) AND any(x IN labels(head([(a)-[:hasVerificationStatus]->(vs) | vs])) WHERE x IS NOT NULL) OR
        toLower(coalesce(a.verificationStatus,'')) = 'verified'
      )
  AND NOT (e)-[:engagedCompany]->(c)
MERGE (e)-[r:engagedCompany]->(c)
ON CREATE SET r.derived = true,
              r.ruleId = 'R1',
              r.evidence = 'assertion',
              r.evidenceAssertionId = coalesce(a.externalId, toString(id(a))),
              r.derivedAt = datetime();

///////////////////////////////////////////////////////////////////////////
// RULE R2 (CANDIDATE ONLY)
// Infer dealForCompany candidate from involvesContact(d,p) AND worksAt(p,c)
// BUT do NOT overwrite canonical dealForCompany.
// Output: (d)-[:dealForCompany {derived:true, ruleId:'R2', candidate:true}]->(c)
//
// Gating: canonical worksAt only (recommended), because this rule is high-risk.
// If you want assertion-backed worksAt, copy the R1 assertion block with stricter thresholds.
///////////////////////////////////////////////////////////////////////////

// Delete previous derived edges for R2
MATCH (:Deal)-[r:dealForCompany {derived:true, ruleId:'R2'}]->(:Company)
DELETE r;

// Create candidate dealForCompany only where missing
MATCH (d:Deal)-[:involvesContact]->(p:Contact)-[:worksAt]->(c:Company)
WHERE NOT (d)-[:dealForCompany]->(:Company) // only if missing canonical
MERGE (d)-[r:dealForCompany]->(c)
ON CREATE SET r.derived = true,
              r.ruleId = 'R2',
              r.candidate = true,
              r.evidence = 'canonical',
              r.derivedAt = datetime();

///////////////////////////////////////////////////////////////////////////
// RULE R3
// Infer engagedDeal(e,d) when engagement participants overlap with deal contacts.
// This helps context assembly when engagement wasn’t explicitly associated to a deal.
//
// Pattern:
// (e)-[:engagedWith]->(p) AND (d)-[:involvesContact]->(p)
// Gating:
// - Only infer if engagement has no engagedDeal already
// - If multiple candidate deals exist, select the "best" via simple heuristic:
//   prefer deals ownedBy same internal user as engagement author (if present),
//   else prefer most recently updated deal (updatedAt), else arbitrary.
//
// Output: (e)-[:engagedDeal {derived:true, ruleId:'R3', ...}]->(d)
///////////////////////////////////////////////////////////////////////////

// Delete previous derived edges for R3
MATCH (:Engagement)-[r:engagedDeal {derived:true, ruleId:'R3'}]->(:Deal)
DELETE r;

// Build candidate links
MATCH (e:Engagement)-[:engagedWith]->(p:Person)<-[:involvesContact]-(d:Deal)
WHERE NOT (e)-[:engagedDeal]->(:Deal)
WITH e, d, count(DISTINCT p) AS overlapCount
// optional heuristic signals
OPTIONAL MATCH (e)-[:authoredBy]->(iu:InternalUser)
OPTIONAL MATCH (d)-[:ownedBy]->(iu2:InternalUser)
WITH e, d, overlapCount,
     (CASE WHEN iu IS NOT NULL AND iu2 IS NOT NULL AND id(iu)=id(iu2) THEN 1 ELSE 0 END) AS ownerMatch,
     coalesce(d.updatedAt, datetime('1900-01-01T00:00:00Z')) AS dealUpdated
ORDER BY e, ownerMatch DESC, overlapCount DESC, dealUpdated DESC
WITH e, head(collect(d)) AS bestDeal
MERGE (e)-[r:engagedDeal]->(bestDeal)
ON CREATE SET r.derived = true,
              r.ruleId = 'R3',
              r.evidence = 'contactOverlap',
              r.derivedAt = datetime();

///////////////////////////////////////////////////////////////////////////
// RULE R4 (Optional, but usually worth it)
// Infer involvesContact(d,p) from engagedDeal(e,d) + engagedWith(e,p)
// Only if deal has zero involvesContact (i.e., incomplete CRM linkage)
//
// Output: (d)-[:involvesContact {derived:true, ruleId:'R4'}]->(p)
///////////////////////////////////////////////////////////////////////////

// Delete previous derived edges for R4
MATCH (:Deal)-[r:involvesContact {derived:true, ruleId:'R4'}]->(:Contact)
DELETE r;

MATCH (e:Engagement)-[:engagedDeal]->(d:Deal)
WHERE NOT (d)-[:involvesContact]->(:Contact)
MATCH (e)-[:engagedWith]->(p:Contact)
MERGE (d)-[r:involvesContact]->(p)
ON CREATE SET r.derived = true,
              r.ruleId = 'R4',
              r.evidence = 'engagedDeal',
              r.derivedAt = datetime();

///////////////////////////////////////////////////////////////////////////
// RULE R5 (Optional “hygiene”)
// Infer employs(c,p) inverse of worksAt(p,c) for easier querying
// Output: (c)-[:employs {derived:true, ruleId:'R5'}]->(p)
///////////////////////////////////////////////////////////////////////////

// Delete previous derived edges for R5
MATCH (:Company)-[r:employs {derived:true, ruleId:'R5'}]->(:Person)
DELETE r;

MATCH (p:Person)-[:worksAt]->(c:Company)
MERGE (c)-[r:employs]->(p)
ON CREATE SET r.derived = true,
              r.ruleId = 'R5',
              r.evidence = 'inverse',
              r.derivedAt = datetime();
