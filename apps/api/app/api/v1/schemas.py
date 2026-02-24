from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SourceSystem = Literal["gmail", "calendar", "sheets", "news", "manual", "transcript", "slack"]
InteractionEventType = Literal[
    "email_received",
    "email_sent",
    "meeting_transcript",
    "news_item",
    "note",
    "chat_message",
    "meeting_notes",
]
Direction = Literal["in", "out", "na"]


class Participant(BaseModel):
    email: str
    name: str | None = None


class Participants(BaseModel):
    from_: list[Participant] = Field(default_factory=list, alias="from")
    to: list[Participant] = Field(default_factory=list)
    cc: list[Participant] = Field(default_factory=list)


class InteractionEventIn(BaseModel):
    source_system: SourceSystem
    event_type: InteractionEventType
    external_id: str
    timestamp: datetime
    thread_id: str | None = None
    direction: Direction = "na"
    subject: str | None = None
    participants: Participants
    body_plain: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    contact_id: str | None = None
    primary_email: str | None = None
    contact_display_name: str | None = None
    contact_company: str | None = None
    backfill_contact_mode: Literal["skip_previously_processed", "reprocess_all"] | None = None


class NewsItemIn(BaseModel):
    title: str
    url: str | None = None
    published_at: datetime | None = None
    body_plain: str


class IngestResponse(BaseModel):
    raw_event_id: str
    interaction_id: str
    status: str


class ContactRow(BaseModel):
    contact_id: str
    primary_email: str
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    owner_user_id: str | None = None
    notes: str | None = None
    use_sensitive_in_drafts: bool = False


class ContactsSyncRequest(BaseModel):
    mode: Literal["pull", "push"] = "pull"
    sheet_revision: str | None = None
    rows: list[ContactRow] = Field(default_factory=list)


class ContactLookupResponse(BaseModel):
    contact_id: str | None = None
    primary_email: str
    display_name: str | None = None
    resolution_task_id: str | None = None


class ScoreReason(BaseModel):
    summary: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class ContactScoreItem(BaseModel):
    contact_id: str
    display_name: str | None = None
    primary_email: str | None = None
    company: str | None = None
    relationship_score: float
    priority_score: float
    why_now: str
    reasons: list[ScoreReason] = Field(default_factory=list)


class ScoreTodayResponse(BaseModel):
    asof: datetime
    items: list[ContactScoreItem]


class ContactProfile(BaseModel):
    contact_id: str
    display_name: str | None = None
    primary_email: str | None = None
    owner_user_id: str | None = None
    company: str | None = None


class NextStepSuggestion(BaseModel):
    summary: str
    type: str
    source: str
    confidence: float = 0.0
    due_at: str | None = None
    freshness_score: float | None = None
    priority_score: float | None = None
    contact_id: str | None = None
    opportunity_id: str | None = None
    case_id: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class ContactInteractionTimelineItem(BaseModel):
    interaction_id: str
    timestamp: datetime
    direction: str | None = None
    subject: str | None = None
    thread_id: str | None = None
    source_system: str | None = None


class ContactClaimSummaryItem(BaseModel):
    claim_id: str
    claim_type: str
    predicate: str | None = None
    object_name: str | None = None
    status: str
    confidence: float
    sensitive: bool = False
    evidence_count: int = 0
    updated_at: str | None = None


class InteractionSummary(BaseModel):
    total_interactions: int
    interaction_count_30d: int
    interaction_count_90d: int
    inbound_count: int
    outbound_count: int
    last_interaction_at: datetime | None = None
    last_subject: str | None = None
    recent_subjects: list[str] = Field(default_factory=list)
    recent_topics: list[str] = Field(default_factory=list)
    priority_next_step: str | None = None
    next_step: NextStepSuggestion | None = None
    summary_source: str | None = None
    priority_next_step_source: str | None = None
    brief: str


class ScoreComponentBreakdown(BaseModel):
    relationship: dict[str, Any] = Field(default_factory=dict)
    priority: dict[str, Any] = Field(default_factory=dict)


class ScoreTrendPoint(BaseModel):
    asof: str
    relationship_score: float
    priority_score: float
    components: list[dict[str, Any]] = Field(default_factory=list)


class ContactScoreDetailResponse(BaseModel):
    contact_id: str
    profile: ContactProfile | None = None
    interaction_summary: InteractionSummary | None = None
    score_components: ScoreComponentBreakdown | None = None
    trend: list[ScoreTrendPoint] = Field(default_factory=list)
    current: ContactScoreItem | None = None
    recent_interactions: list[ContactInteractionTimelineItem] = Field(default_factory=list)
    claims_summary: list[ContactClaimSummaryItem] = Field(default_factory=list)
    review_summary: ContactReviewSummary | None = None


class InteractionSummaryRefreshResponse(BaseModel):
    contact_id: str
    refreshed: bool
    interaction_summary: InteractionSummary


class DraftRequest(BaseModel):
    contact_id: str
    objective: str | None = None
    opportunity_id: str | None = None
    allow_sensitive: bool = False
    allow_uncertain_context: bool = False
    allow_proposed_changes_in_external_text: bool = False
    overwrite_draft_id: str | None = None


class DraftResponse(BaseModel):
    draft_id: str
    contact_id: str
    tone_band: str
    draft_subject: str
    draft_text: str
    citations_json: list[dict[str, Any]]
    status: str
    objective: str | None = None
    retrieval_trace: dict[str, Any] | None = None
    context_summary: dict[str, Any] | None = None


class DraftStatusUpdate(BaseModel):
    status: Literal["proposed", "edited", "approved", "discarded"]


class DraftRevisionRequest(BaseModel):
    draft_subject: str
    draft_body: str
    status: Literal["edited", "approved"] = "edited"


class DraftStyleGuideUpdateResponse(BaseModel):
    draft_id: str
    updated: bool
    samples_used: int
    guide_path: str
    status: str


class DraftObjectiveSuggestionResponse(BaseModel):
    contact_id: str
    objective: str
    source_summary: dict[str, Any] = Field(default_factory=dict)


class ResolutionTaskItem(BaseModel):
    task_id: str
    contact_id: str
    task_type: str
    proposed_claim_id: str
    current_claim_id: str | None = None
    payload_json: dict[str, Any]
    status: str


class ResolutionTaskListResponse(BaseModel):
    tasks: list[ResolutionTaskItem]


class ContactReviewSummary(BaseModel):
    open_resolution_task_count: int = 0
    open_case_contact_count: int = 0
    open_case_opportunity_count: int = 0
    open_resolution_tasks: list[ResolutionTaskItem] = Field(default_factory=list)


class ResolveTaskRequest(BaseModel):
    action: Literal["accept_proposed", "reject_proposed", "edit_and_accept"]
    edited_value_json: dict[str, Any] | None = None


class ResolveTaskResponse(BaseModel):
    task_id: str
    status: str


class ReprocessRequest(BaseModel):
    interaction_id: str


class BackfillContactStatusResponse(BaseModel):
    asof: datetime
    total_contact_count: int
    processed_contact_count: int
    processed_contact_ids: list[str] = Field(default_factory=list)
    processed_primary_emails: list[str] = Field(default_factory=list)


EntityStatus = Literal["canonical", "provisional", "rejected"]


class RankedOpportunityItem(BaseModel):
    opportunity_id: str | None = None
    case_id: str | None = None
    title: str
    company_name: str | None = None
    status: str
    entity_status: EntityStatus
    kind: Literal["opportunity", "case_opportunity"]
    priority_score: float
    next_step: NextStepSuggestion | None = None
    linked_contacts: list[ContactProfile] = Field(default_factory=list)
    reason_chain: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    last_engagement_at: str | None = None
    thread_id: str | None = None


class RankedOpportunitiesResponse(BaseModel):
    asof: datetime
    items: list[RankedOpportunityItem] = Field(default_factory=list)


class CasePromotionRequest(BaseModel):
    promotion_reason: str = Field(default="manual_promotion")
    gate_results: dict[str, Any] = Field(default_factory=dict)


class CaseContactItem(BaseModel):
    case_id: str
    email: str
    display_name: str | None = None
    status: str
    entity_status: EntityStatus
    interaction_id: str | None = None
    provisional_contact_id: str | None = None
    promotion_reason: str | None = None
    gate_results: dict[str, Any] = Field(default_factory=dict)
    evidence_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class CaseOpportunityItem(BaseModel):
    case_id: str
    title: str
    company_name: str | None = None
    thread_id: str | None = None
    status: str
    entity_status: EntityStatus
    interaction_id: str | None = None
    promotion_reason: str | None = None
    gate_results: dict[str, Any] = Field(default_factory=dict)
    motivators: list[str] = Field(default_factory=list)
    contact_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class CaseContactsListResponse(BaseModel):
    items: list[CaseContactItem] = Field(default_factory=list)


class CaseOpportunitiesListResponse(BaseModel):
    items: list[CaseOpportunityItem] = Field(default_factory=list)


class CasePromotionResponse(BaseModel):
    case_id: str
    status: str
    entity_status: EntityStatus
    promoted_id: str | None = None


class BackfillRunReportIn(BaseModel):
    workflow: str
    run_id: str
    status: Literal["success", "warning", "error"] = "success"
    mode: str | None = None
    total_items: int = 0
    success_items: int = 0
    dead_letter_items: int = 0
    dead_letter_ratio: float = 0.0
    generated_at: datetime
    error_samples: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None


class BackfillRunReportResponse(BaseModel):
    raw_event_id: str
    created: bool
    status: str
