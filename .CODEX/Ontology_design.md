
# Lux CRM KG V2 Ontology Contract (Authoritative)

This file is the authoritative implementation contract for the V2 three-layer model used by the runtime:

1. `CRM Graph`: canonical contacts/companies/opportunities/engagements.
2. `Case Graph`: provisional contact/opportunity discoveries and promotion workflow.
3. `Evidence Graph`: assertions, evidence chunks, extraction events, and gate results.

## Predicate Decision Table (Extractor -> Graph Target)

| Extracted predicate / claim type | CRM Graph write | Case Graph write | Evidence Graph write | Gate requirements |
| --- | --- | --- | --- | --- |
| `works_at` / `employment` | Yes (verified only) | Yes (provisional company context) | Yes | `status in {verified,accepted}` AND `confidence >= 0.85` AND recent |
| `has_opportunity` / `opportunity` | Yes (only when promoted) | Yes (default) | Yes | matcher score >= threshold for canonical attach; else create `CaseOpportunity` |
| `has_preference` / `preference` | No direct canonical edge by default | optional contextual case enrichment | Yes | evidence required, non-rejected |
| `committed_to` / `commitment` | No direct canonical edge by default | optional contextual case enrichment | Yes | evidence required, non-rejected |
| `personal_detail` / `family` / `education` | No direct canonical edge | optional contextual case enrichment | Yes (sensitivity-aware) | evidence required + sensitivity flagging |
| `topic` / `discussed_topic` | No direct canonical edge | optional contextual case enrichment | Yes | evidence required |

## Mandatory Evidence Rules

All assertion writes MUST include:
- `interaction_id`
- `chunk_id`
- `span_json`

If any are missing, the assertion is rejected and a `missing_evidence` resolution task is created.

## Promotion Defaults

- Unknown external participants from interactions are auto-created as `CaseContact`.
- Unknown opportunity context is auto-created as `CaseOpportunity`.
- Promotion to canonical CRM entities requires explicit promotion gates and stores:
  - `entity_status`
  - `promotion_reason`
  - `gate_results`

---

I need to develop the ontology for the contact knowledge graph. I want it to represent basic entities and relationships like employee->works_at->company or contact->is_connected_to->opportunity. Research existing CRM ontologies and how CRMs like hubspot and salesforce characterize contacts, clients, opportunities and provide a comprehensive list of entity types and organize hierarchically by class/subclass. Also provide a comprehensive list of relationships between these entities. Consider this in the context of contact and lead management system that will extract triples from text based info (emails and meeting notes, chat history) and then find logical relationships between these triples - to enrich agent behavior for prioritization and email drafts with appropriate and relevant context
Please turn the above into an OWL schema draft I can load into webprotege. Use hubspot terms for now (include SF terms as aliases).
Explain this to me: SWRL rules (or SHACL shapes) for your “logical relationship” inference layer (e.g., infer engagedCompany from worksAt + engagedWith)
Give me both with a gating strategy. I’m using Neo4j. 
Does COGNEE handle SWRL and SHACL?
Give me a decision tree
Refer back to the TTL file above for the label/relationships in Neo4J. Then produce the SHACL and Cypher rule packs 
Below is a pragmatic ontology scaffold for a **contact/lead management KG** that (a) maps cleanly onto how **Salesforce** and **HubSpot** model CRM data, (b) is **extractable from text** (emails/notes) as triples, and (c) supports **agent behaviors** (prioritization + context-aware drafting) by making provenance, temporality, and evidence first-class.

I’m going to be explicit about two layers:

1. **Core CRM domain ontology** (entities + relationships that correspond to CRM objects like Contact/Company/Deal/Activity)
2. **Evidence & inference layer** (how extracted triples are stored with source, time, confidence, and how “logical relationships” are inferred without polluting your ground-truth CRM layer)

---

## 1) What Salesforce & HubSpot “mean” by contacts/companies/deals/activities

### Salesforce (high-signal takeaways)

* Salesforce is built around standard objects like **Account, Contact, Lead, Opportunity**, plus “activities” (**Task** and **Event**) and marketing objects like **Campaign**. ([Developer][1])
* Opportunities commonly relate to an **Account** via a relationship field (i.e., the opportunity’s “customer / target org”). ([Developer][2])
* “Activities” are **Task + Event** (Salesforce uses that term in UI and data model). ([Developer][3])
* A **Task** can be related to *one Lead or up to many Contacts* and can also relate to other objects (Account, Opportunity, Case, etc.) via TaskRelation. This matters for ontology design: an “interaction” can legitimately be linked to multiple people + a deal. ([Developer][4])

### HubSpot (high-signal takeaways)

* HubSpot calls records “**objects**” (contacts, companies, deals, tickets, custom objects). Each object has **properties**, and objects connect via **associations**. ([HubSpot Developers][5])
* HubSpot explicitly frames “activities” as how interactions are tracked on records; and they provide a “model of object and activity relationships”. ([knowledge.hubspot.com][6])
* Associations are first-class, with APIs (v3/v4) and labeled association types. ([HubSpot Developers][7])
* Companies API docs note you can create a company and associate it to **contacts/deals** and also to **activities (meetings, notes)**—again reinforcing that activities are linkable to multiple entities. ([HubSpot Developers][8])

**Implication for your KG:** don’t treat “email/meeting/note” as just text; treat it as an **Activity/Engagement node** with links to Person(s), Organization(s), Deal(s), and extracted claims.

---

## 2) Ontology design principles for a CRM KG that ingests triples from text

### Principle A — Separate *record* truth from *extracted* assertions

If you blend “CRM truth” with “LLM-extracted statements” you will poison your own system.

Use two parallel constructs:

* **crm:** canonical entities synced from HubSpot/Salesforce (Contacts, Companies/Accounts, Deals/Opportunities, etc.)
* **assertion:** extracted triples with provenance (source doc, timestamp, confidence, speaker, etc.)

Then define **inference rules** that can propose updates / enrichments *without overwriting* CRM truth.

### Principle B — Make time, provenance, and confidence mandatory for extracted knowledge

Your agent behaviors (prioritization, drafting) depend on *recency*, *reliability*, and *who said it*. Without this, you get “confident nonsense with no audit trail.”

### Principle C — Model “relationship strength” explicitly

You want prioritization. That means you need computable features:

* interaction frequency, recency, directionality
* deal stage proximity
* role seniority / influence
* explicit intents (“let’s meet next week”, “send proposal”)

Those are not properties of “Contact” alone; they’re properties of **Person–Person**, **Person–Org**, and **Person–Deal** relationships over time.

---

## 3) Entity types (classes) — hierarchical class/subclass list

This is a comprehensive but still implementable class tree. Bold items are the “must have” for CRM parity + agent use.

### 3.1 Agents & parties

* **crm:Agent**

  * **crm:Person**

    * **crm:InternalUser** (your employee / seat user)

      * crm:SalesRep
      * crm:CSM
      * crm:ExecSponsor
    * **crm:ExternalPerson**

      * **crm:Lead** (pre-qualification person record)
      * **crm:Contact** (qualified person record)
      * crm:DecisionMaker (role tag; can be inferred)
      * crm:Influencer (role tag)
      * crm:Champion (role tag)
      * crm:Blocker (role tag)
  * **crm:Organization**

    * **crm:Account** (Salesforce “Account”; HubSpot “Company” analog)

      * **crm:Company**

        * crm:Customer
        * crm:Prospect
        * crm:Partner
        * crm:Vendor
        * crm:Competitor
      * crm:BusinessUnit / crm:Division / crm:Department (org structure)
    * crm:Household (optional if B2C)

**Alignment note:** Salesforce: Lead/Contact/Account are standard objects; HubSpot: Contact/Company are core objects, and “lead” is usually a lifecycle stage rather than a separate object, but you still want the class for semantic clarity. ([Developer][1])

### 3.2 Commercial objects (pipeline)

* **crm:CommercialRecord**

  * **crm:Opportunity** (Salesforce) / **crm:Deal** (HubSpot)

    * crm:NewBusinessOpportunity
    * crm:RenewalOpportunity
    * crm:UpsellOpportunity
  * crm:Quote
  * crm:Order / crm:Invoice (optional; more CPQ/commerce)
  * crm:Contract / crm:MSA / crm:SOW
  * crm:Product
  * crm:ServiceOffering
  * crm:LineItem

**Alignment note:** HubSpot’s CRM object set explicitly includes deals and supports associations among deals/contacts/companies/tickets and other objects. ([HubSpot Developers][9])

### 3.3 Marketing & growth

* crm:MarketingRecord

  * crm:Campaign (Salesforce standard object; HubSpot has campaign tooling but object model differs)
  * crm:List / crm:Segment
  * crm:SubscriptionPreference
  * crm:LifecycleStage (lead/MQL/SQL/customer—often a property but useful as a class/value set)

### 3.4 Service & support

* crm:SupportRecord

  * crm:Case (Salesforce)
  * crm:Ticket (HubSpot)
  * crm:Issue
  * crm:SLA

**Alignment note:** HubSpot explicitly includes tickets as a core CRM object category. ([HubSpot Developers][9])

### 3.5 Activities / engagements (critical for text ingestion)

* **crm:Engagement** (a.k.a. Activity)

  * **crm:Email**
  * **crm:Meeting**
  * **crm:Call**
  * **crm:Note**
  * **crm:Task** (to-do)
  * **crm:Event** (calendar event)
  * crm:ChatMessage (Slack/Teams/etc.)
  * crm:Attachment / crm:File (or link to document store)

**Alignment note:** Salesforce: “Task and Event records are collectively referred to as activities.” ([Developer][3])
HubSpot: records have activities tracked through them; their relationship model explicitly ties activities to objects. ([knowledge.hubspot.com][6])

### 3.6 “Reference” entities (useful for extraction + routing)

* crm:Role (job function, buying role, project role)
* crm:TitleSeniority (VP, Director, Manager…)
* crm:Industry / crm:Sector
* crm:Geography (Region, Country, City)
* crm:TechnologyStackItem (if you sell into tech ecosystems)
* crm:Topic (meeting topic, capability area)
* crm:Need / crm:PainPoint
* crm:UseCase
* crm:CompetitorProduct

### 3.7 Evidence, provenance, and reasoning layer (non-CRM truth)

* **kg:SourceArtifact**

  * kg:EmailMessage (raw)
  * kg:MeetingTranscript
  * kg:MeetingNotes
  * kg:ChatThread
  * kg:Document
* **kg:ExtractionEvent**

  * kg:ExtractorModel (which LLM / version)
  * kg:ExtractionPrompt (optional)
* **kg:Assertion** (reified statement)

  * kg:EntityMention
  * kg:RelationMention
  * kg:Claim (a normalized assertion about the world)
* kg:ConfidenceScore
* kg:TemporalScope (start/end validity)
* kg:Attribution (speaker/author)

This is what makes your KG “audit-ready” and lets you improve extraction without corrupting the CRM layer.

---

## 4) Relationship types (object properties) — comprehensive list

I’m grouping these into: identity/core, pipeline, activity, marketing, service, and inference/prioritization.

### 4.1 Identity & org structure

* **crm:worksAt** (Person → Organization)
* **crm:employs** (Organization → Person) [inverse of worksAt]
* crm:reportsTo (InternalUser → InternalUser)
* crm:memberOfDepartment (Person → Department)
* crm:subOrganizationOf (Organization → Organization) (subsidiary/parent) (Schema.org has patterns like parent/subOrganization) ([Schema.org][10])
* crm:hasContactPoint (Person/Organization → ContactPoint) (Schema.org ContactPoint is a solid reference pattern) ([Schema.org][11])
* crm:hasAddress / crm:locatedIn (Organization → Geography)

### 4.2 CRM qualification & lifecycle

* **crm:convertedTo** (Lead → Contact/Account/Opportunity) (Salesforce concept)
* crm:hasLifecycleStage (Person/Organization → LifecycleStage)
* crm:hasLeadStatus (Lead → StatusValue)
* crm:hasContactStatus (Contact → StatusValue)

### 4.3 Relationships between people (social graph)

* **crm:knows** / **crm:isConnectedTo** (Person ↔ Person)
* crm:introducedBy (Person → Person)
* crm:recommendedBy (Person → Person)
* crm:influences (Person → Person or Decision)
* crm:hasRelationshipStrength (Person↔Person) → RelationshipScore node
* crm:hasTrustLevel (Person↔Person) → TrustScore node

### 4.4 Account/contact affiliation & roles (buying committee model)

* **crm:hasRoleAt** (Person → RoleAssignment) where RoleAssignment links to Organization and Role
* crm:isDecisionMakerFor (Person → Opportunity/Deal)
* crm:isChampionFor (Person → Opportunity/Deal)
* crm:isBlockerFor (Person → Opportunity/Deal)
* crm:ownsBudgetFor (Person → Opportunity/Deal)
* crm:evaluatesSolutionFor (Person → Opportunity/Deal)

(These are typically inferred from text; store as **Assertions** until verified.)

### 4.5 Opportunity/Deal relationships (pipeline core)

* **crm:opportunityForAccount** (Opportunity → Account) (Salesforce explicitly links Opportunity to Account) ([Developer][2])
* crm:hasPrimaryContact (Opportunity → Contact)
* crm:involvesContact (Opportunity ↔ Contact) (many-to-many)
* crm:ownedBy (Opportunity → InternalUser)
* crm:coOwnedBy (Opportunity → InternalUser)
* crm:hasStage (Opportunity → Stage)
* crm:hasAmount / crm:hasCurrency
* crm:hasCloseDate
* crm:hasProbability
* crm:competesWith (Opportunity → CompetitorProduct/CompetitorCompany)
* crm:hasNextStep (Opportunity → Task)
* crm:hasLineItem (Opportunity → LineItem → Product/ServiceOffering)
* crm:governedByContract (Opportunity → Contract)

### 4.6 Activity/engagement relationships (your ingestion backbone)

Model engagements as nodes so you can attach metadata and multiple participants.

* **crm:engagedWith** (Engagement → Person) (participants/attendees)
* crm:engagedOrganization (Engagement → Organization)
* crm:engagedOpportunity (Engagement → Opportunity)
* crm:authoredBy (Email/Note → Person/InternalUser)
* crm:sentTo (Email → Person)
* crm:ccTo / crm:bccTo (Email → Person)
* crm:occurredAt (Engagement → datetime)
* crm:hasSubject / crm:hasSummary
* crm:hasOutcome (Meeting/Call → OutcomeValue)
* crm:createsTask (Meeting/Email → Task)
* crm:referencesDocument (Engagement → Document)
* crm:mentionsEntity (Engagement → EntityMention) (links into assertion layer)

**Why this matters:** Salesforce supports Tasks related to one lead or multiple contacts and also related to accounts/opportunities/cases etc. That is exactly the multi-link pattern you want. ([Developer][4])
HubSpot similarly treats associations between objects and activities as central. ([HubSpot Developers][7])

### 4.7 Marketing relationships

* crm:memberOfCampaign (Person/Contact/Lead → Campaign)
* crm:targetedByCampaign (Organization → Campaign)
* crm:respondedToCampaign (Person → Campaign)
* crm:hasEngagementWithCampaign (Engagement → Campaign)

### 4.8 Service/support relationships

* crm:openedCase (Person → Case/Ticket)
* crm:caseForAccount (Case/Ticket → Account)
* crm:caseAboutProduct (Case/Ticket → Product)
* crm:assignedTo (Case/Ticket → InternalUser)
* crm:resolvedBy (Case/Ticket → Engagement/Action)

### 4.9 “Semantic” relationships extracted from text (store as Assertions first)

These are the ones your triple extraction will generate routinely:

* kg:statesNeed (Person/Org → Need)
* kg:hasPainPoint (Person/Org → PainPoint)
* kg:interestedIn (Person/Org → Topic/Product/Service)
* kg:plansTo (Person/Org → Action/Project)
* kg:committedTo (Person/Org → Commitment)
* kg:requested (Person → Deliverable)
* kg:agreedTo (Person → ActionItem)
* kg:blockedBy (Opportunity/Action → Constraint)
* kg:prefers (Person → Preference)
* kg:budgetRange (Opportunity → MoneyRange)
* kg:timeline (Opportunity → TimeRange)
* kg:nextMeetingOn (Person/Opportunity → DateTime)
* kg:riskConcern (Person/Org → RiskTopic)

---

## 5) The “evidence & inference” pattern you should use (or your KG will rot)

### 5.1 Reified assertions (minimum viable shape)

Instead of writing:

> ContactA — interestedIn — ProductX

Write:

* Assertion123 a kg:Assertion

  * kg:subject = ContactA
  * kg:predicate = kg:interestedIn
  * kg:object = ProductX
  * kg:sourceArtifact = Email456
  * kg:extractedFromEngagement = EmailEngagement789
  * kg:attributedTo = ContactA (speaker)
  * kg:assertedAt = 2026-02-18T…
  * kg:confidence = 0.74
  * kg:status = {proposed|verified|rejected}
  * kg:validTime = optional (start/end)

This lets you:

* compute relationship strength on *verified* vs *proposed*
* keep the raw source attached
* re-run extraction models later without losing history

### 5.2 Inference rules (examples that actually help agent behavior)

You said: “find logical relationships between these triples.”

Good. Do it with explicit rules so the agent is predictable:

**Rule: implied account association**

* If a Person worksAt Org, and Engagement includes Person, then Engagement engagedOrganization Org (unless contradicted)

**Rule: buying committee enrichment**

* If Assertion: “I’m not the decision maker, talk to X”
  → mark Person role = Influencer; mark X role = DecisionMaker candidate; link introducedBy.

**Rule: opportunity activation**

* If a contact requests proposal/pricing/demo AND timeframe <= 90 days
  → propose Opportunity with stage = Discovery, and create Task = “Schedule demo”.

**Rule: relationship strength bump**

* If Engagement count(Person↔InternalUser) in last 30 days ≥ N, and at least one Meeting
  → increase relationship strength score; prioritize outreach.

None of that belongs as raw triples without provenance. It belongs as *derived facts* with traceability to the assertions/engagements that triggered them.

---

## 6) Practical mapping: your ontology vs HubSpot/Salesforce fields

Don’t over-model vendor schemas. Map to *concepts* and keep a connector layer.

### Canonical concept → Salesforce / HubSpot anchor

* crm:Account / crm:Company

  * Salesforce: Account (standard object) ([Developer][1])
  * HubSpot: Company object ([HubSpot Developers][8])
* crm:Contact

  * Salesforce: Contact ([Developer][1])
  * HubSpot: Contact object ([HubSpot Developers][9])
* crm:Lead

  * Salesforce: Lead ([Developer][1])
  * HubSpot: often lifecycle stage/property; still keep class in KG for extraction semantics ([knowledge.hubspot.com][6])
* crm:Opportunity / crm:Deal

  * Salesforce: Opportunity (linked to Account) ([Developer][2])
  * HubSpot: Deal object ([HubSpot Developers][9])
* crm:Engagement (Task/Event/Email/Meeting/Note/Call)

  * Salesforce: Task+Event = Activities; TaskRelation supports multiple links ([Developer][3])
  * HubSpot: activities tracked and associated; associations APIs ([knowledge.hubspot.com][6])

---

## 7) What you should implement first (and what to postpone)

### Phase 1: MVP ontology (2–3 days to implement cleanly)

**Classes**

* Person (InternalUser, Contact, Lead)
* Organization (Account/Company)
* Opportunity/Deal
* Engagement (Email, Meeting, Call, Note, Task)
* SourceArtifact, Assertion, ExtractionEvent

**Relationships**

* worksAt, subOrganizationOf
* opportunityForAccount, involvesContact, ownedBy, hasStage
* engagedWith, engagedOrganization, engagedOpportunity, occurredAt
* Assertion reification properties (subject/predicate/object/source/confidence/time/status)

This gets you:

* reliable ingestion from notes/emails
* agent-ready context assembly
* safe enrichment without corrupting CRM

### Phase 2: Buying committee + relationship strength (next)

* RoleAssignment model
* DecisionMaker/Champion/Influencer/Blocker tags (as derived assertions)
* RelationshipStrength node updated from engagement features

### Phase 3: Marketing/service objects (only if you truly use them)

Campaign/Ticket/Case become worth it if you want end-to-end lifecycle automation. Otherwise: distraction.

---

## 8) Brutal design warning (so you don’t build a beautiful useless ontology)

If you try to be “comprehensive” by importing every CRM concept up front, you will:

* slow down extraction (more predicate choices → lower precision)
* increase entity resolution errors (more node types → more ambiguity)
* make agent behavior unpredictable (too many weak signals)

The winning move is: **small canonical CRM layer + rich evidence layer + explicit derivation rules**.

---

If you want, I can turn the above into:

* an OWL/RDF (or Neo4j LPG) schema draft (classes + object properties + constraints),
* plus a “triple extraction spec” that constrains the LLM to only emit predicates from your approved set, and
* a derivation rule list (Cypher or Python) for “logical relationships” and relationship-strength scoring.

[1]: https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_list.htm?utm_source=chatgpt.com "Object Reference for the Salesforce Platform"
[2]: https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_opportunity.htm?utm_source=chatgpt.com "Opportunity | Object Reference for the Salesforce Platform"
[3]: https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_task.htm?utm_source=chatgpt.com "Task | Object Reference for the Salesforce Platform"
[4]: https://developer.salesforce.com/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_taskrelation.htm?utm_source=chatgpt.com "TaskRelation | Object Reference for the Salesforce Platform"
[5]: https://developers.hubspot.com/docs/guides/crm/understanding-the-crm?utm_source=chatgpt.com "Understanding the CRM APIs - HubSpot docs"
[6]: https://knowledge.hubspot.com/data-management/view-a-model-of-your-crm-object-and-activity-relationships?utm_source=chatgpt.com "View a model of your CRM object and activity relationships"
[7]: https://developers.hubspot.com/docs/api-reference/crm-associations-v4/guide?utm_source=chatgpt.com "CRM API | Associations v4 - HubSpot docs"
[8]: https://developers.hubspot.com/docs/api-reference/crm-companies-v3/guide?utm_source=chatgpt.com "CRM API | Companies - HubSpot docs"
[9]: https://developers.hubspot.com/docs/api-reference/overview?utm_source=chatgpt.com "API Reference Overview - HubSpot docs"
[10]: https://schema.org/Organization?utm_source=chatgpt.com "Organization - Schema.org Type"
[11]: https://schema.org/ContactPoint?utm_source=chatgpt.com "ContactPoint - Schema.org Type"

You’re on Neo4j (property graph), so the clean way to do this is:

SHACL = gatekeeper (block/flag bad writes; ensure minimum viable graph integrity before you let agents act). Neo4j can run SHACL validation via neosemantics (n10s), including transaction-level validation via procedures designed for this.

SWRL = portable rule spec (express your intended inference logic in a standard semantic form), but Neo4j does not run SWRL natively. You implement the same rules operationally with Cypher/APOC (or a middleware reasoner) and write derived relationships back into Neo4j. n10s supports “basic inferencing,” but not full SWRL forward-chaining.

Below I’m giving you both artifacts (SHACL + SWRL) and a gating strategy that prevents “LLM-extracted uncertainty” from contaminating derived facts.

1) Gating strategy for Neo4j (so inference doesn’t amplify extraction errors)
Data tiers (three-lane model)

Lane A — Canonical CRM facts (HubSpot-synced)

Node labels: :HSContact, :HSCompany, :HSDeal, :HSEngagement

Relationships: :WORKS_AT, :DEAL_FOR_COMPANY, :INVOLVES_CONTACT, :ENGAGED_WITH, etc.

Rule: only CRM sync or explicit human verification can write to these.

Lane B — Extracted assertions (LLM output)

Nodes: :Assertion (reified statement)

Relates to canonical nodes via (:Assertion)-[:SUBJECT]->(:Entity) etc.

Properties: status ∈ {proposed,verified,rejected}, confidence, assertedAt, sourceId, attributedTo

Rule: LLM writes here, never directly to canonical relationships.

Lane C — Derived facts (inference output)

Relationships created by inference: :ENGAGED_COMPANY_DERIVED, :LIKELY_PRIMARY_CONTACT_DERIVED, etc.

Properties: derived=true, ruleId, derivedAt, evidenceAssertionIds[] (or evidenceEngagementId)

Rule: Derived edges must be reproducible; you can drop and recompute them.

Gating rules (what’s allowed to trigger inference)

Run inference only if inputs are “trusted enough.” In practice:

Use canonical WORKS_AT only (Lane A), or

Use an assertion-backed WORKS_AT only if status='verified' and confidence >= 0.85 and assertedAt is recent enough (e.g., within 180 days), or the person’s email domain matches the company domain.

This is the whole point: SWRL/Cypher rules should read from a “trusted view” of the graph.

When to run what

On every write: run SHACL transaction validation on touched nodes/edges. This prevents corrupt structures from entering your operational graph. n10s exposes transaction/set validation procedures; people commonly wire these into APOC triggers.

On schedule / batch (or after sync/import): run inference jobs that materialize derived edges (Lane C). Keep it deterministic and idempotent.

2) SHACL shapes (Neo4j n10s-compatible) — the gatekeeper

n10s supports validating Neo4j graphs against SHACL constraints.

What these shapes enforce (MVP)

Every Deal must link to exactly one Company

Every Engagement must have:

occurredAt (datetime)

at least 1 participant (ENGAGED_WITH → Person)

Every Contact should have:

externalId

(optional) email format constraints if you store email

Optional: if a canonical WORKS_AT exists, it must point to a Company.

SHACL (Turtle) draft

This assumes you are using n10s’ RDF mapping conventions or have a parallel RDF view. Even if you’re not “full RDF,” n10s validation works by mapping Neo4j elements to RDF-ish constructs during validation.


Rules for Use:

SHACL for validation

Cypher for inference

OWL only for schema documentation and conceptual alignment

Recommended gating policy (operational)

SHACL hard gates run on import / sync writes (block bad structure).

LLM extraction writes Assertions only (never canonical edges).

“Verification” step (human-in-loop or deterministic checks) promotes certain assertions to verified.

Cypher inference jobs run on a schedule (or after imports) and only materialize derived edges.

This keeps you out of the failure mode where one wrong extraction (“Alice works at ACME”) silently infects every downstream context/draft.

What this gives you (agent-impact)

R1 lets the agent reliably answer “which company is this engagement about?” even when only participants are known.

R3 links “floating” engagements back to the most likely deal, which is essential for drafting follow-ups with correct pipeline context.

R2 provides candidate company associations for deals (useful for triage dashboards), without corrupting the canonical model.

Optional R4/R5 improve completeness and query ergonomics.
