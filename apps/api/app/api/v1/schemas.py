from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SourceSystem = Literal["gmail", "calendar", "sheets", "news", "manual", "transcript"]
InteractionEventType = Literal["email_received", "email_sent", "meeting_transcript", "news_item", "note"]
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
    relationship_score: float
    priority_score: float
    why_now: str
    reasons: list[ScoreReason] = Field(default_factory=list)


class ScoreTodayResponse(BaseModel):
    asof: datetime
    items: list[ContactScoreItem]


class DraftRequest(BaseModel):
    contact_id: str
    objective: str | None = None
    allow_sensitive: bool = False


class DraftResponse(BaseModel):
    draft_id: str
    contact_id: str
    tone_band: str
    draft_text: str
    citations_json: list[dict[str, Any]]
    status: str


class DraftStatusUpdate(BaseModel):
    status: Literal["proposed", "edited", "approved", "discarded"]


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


class ResolveTaskRequest(BaseModel):
    action: Literal["accept_proposed", "reject_proposed", "edit_and_accept"]
    edited_value_json: dict[str, Any] | None = None


class ResolveTaskResponse(BaseModel):
    task_id: str
    status: str


class ReprocessRequest(BaseModel):
    interaction_id: str
