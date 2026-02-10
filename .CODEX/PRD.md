# Lux CRM Agent - Product Requirements Document

## 1. Executive Summary
Lux CRM Agent is a relationship intelligence layer that augments a CRM contact registry with a temporal knowledge graph and evidence-backed reasoning. The system ingests communications and news, extracts claims with provenance, maintains temporal memory, scores relationships and priorities, and generates draft communications with citations and sensitive data controls.

**MVP Goal:** Deliver a working pipeline that ingests interactions, builds a contextual graph with evidence, produces daily priority contacts with explanations, matches news to contacts, and generates tone-matched drafts with citations and resolution workflows for contradictions.

## 2. Mission
Provide a lightweight but rigorous relationship intelligence system that helps users decide who to contact, when, and why, with defensible evidence.

## 3. Target Users
- Relationship managers and founders managing many warm contacts
- Sales or partnerships teams needing a prioritized daily outreach list
- Operators who want evidence-based context for outreach

## 4. MVP Scope
**In Scope**
- Google Sheets contact registry sync
- Ingestion from Gmail, transcripts, and news
- Chunking, embeddings, and extraction (Cognee)
- Temporal memory updates (Mem0) with contradiction detection
- Relationship and priority scoring with evidence
- News-to-contact matching using GraphRAG
- Draft email generation with tone bands and citations
- Resolution workflow for contradictory facts
- Next.js UI for priority list, contact view, news match, and drafts

**Out of Scope**
- HubSpot integration (post-MVP)
- Multi-tenant auth and user management
- Automated sending of emails
- Fully automated claim acceptance for all types

## 5. Architecture Overview
- FastAPI backend for ingestion and business logic
- Neo4j Aura for the contextual knowledge graph
- Neon Postgres + pgvector for chunks, embeddings, and evidence
- Cognee and Mem0 integrated via open-source local deployments/adapters
- Redis + RQ for background processing
- n8n for ingestion triggers and routing
- Next.js UI for user-facing workflows

## 6. Principles and Constraints
- Every claim must have provenance and evidence pointers
- Sensitive facts are stored but excluded from drafts by default
- Contradictions create resolution tasks, not silent overwrites
- News matching results are computed on request and not persisted
- Data cleanup and retention are configurable and optional

## 7. Success Criteria
- Daily priority list with reasons and evidence
- Drafts contain citations and respect tone bands
- Contradictions create resolution tasks and can be resolved in UI
- News matching returns relevant contacts with explainable reasons
