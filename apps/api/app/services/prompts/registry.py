from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptDefinition:
    key: str
    description: str
    used_by: str
    template: str


_PROMPTS: dict[str, PromptDefinition] = {
    "draft_email_system": PromptDefinition(
        key="draft_email_system",
        description=(
            "System instructions for outbound email draft generation. "
            "Defines safety constraints and injects combined writing-style guidance "
            "(user baseline + relationship-context style)."
        ),
        used_by="app/services/drafting/composer.py::_compose_openai_draft",
        template=(
            "You are an executive communication assistant. Write a concise outbound email draft "
            "that sounds natural, follows the requested tone, and does not invent facts. "
            "Return plain text only.\n\n"
            "Apply the writing-style instructions below. "
            "The user baseline style always applies; relationship-context style adjusts tone based on relationship maturity.\n"
            "{writing_style_instructions}"
        ),
    ),
    "draft_email_user": PromptDefinition(
        key="draft_email_user",
        description=(
            "User prompt carrying structured context for a single outbound message. "
            "The model should produce one complete email draft with greeting and sign-off."
        ),
        used_by="app/services/drafting/composer.py::_compose_openai_draft",
        template=(
            "Generate a single email draft using this JSON context. "
            "Include greeting and sign-off with [Your Name].\n\n"
            "{context_json}"
        ),
    ),
    "warmth_depth_system": PromptDefinition(
        key="warmth_depth_system",
        description=(
            "System instructions for relationship-signal scoring from message content. "
            "Forces strict JSON output for warmth and depth inputs to relationship scoring."
        ),
        used_by="app/services/scoring/content_signals.py::_score_with_openai",
        template=(
            "You assess relationship signals from communication content. "
            "Return strict JSON with keys warmth_delta and depth_count only. "
            "warmth_delta range: -10 to 10 (higher means warmer/reciprocal). "
            "depth_count range: 0 to 10 (higher means substantive/nuanced communication depth)."
        ),
    ),
    "warmth_depth_user": PromptDefinition(
        key="warmth_depth_user",
        description=(
            "User prompt providing recent interaction excerpts for LLM-based warmth/depth scoring."
        ),
        used_by="app/services/scoring/content_signals.py::_score_with_openai",
        template=(
            "Analyze these recent interactions and score warmth/depth for CRM relationship scoring.\n"
            "{context_json}"
        ),
    ),
    "interaction_summary_system": PromptDefinition(
        key="interaction_summary_system",
        description=(
            "System instructions for contact-level interaction summarization. "
            "Requires JSON output with a concise summary, topical bullets, and a stub next-step recommendation."
        ),
        used_by="app/api/v1/routes/scores.py::_summarize_recent_interactions_with_openai",
        template=(
            "You summarize CRM interaction context using only email/message excerpt content provided by the user. "
            "Do not use or infer from email subject lines. "
            "Return strict JSON with keys: summary, recent_topics, priority_next_step.\n"
            "- summary: <= 420 characters, plain text.\n"
            "- recent_topics: array of 1-4 concise topic phrases.\n"
            "- priority_next_step: <= 260 characters, plain text and MUST begin with 'Stub:'. "
            "Treat this as a provisional recommendation until HubSpot opportunity sync is available."
        ),
    ),
    "interaction_summary_user": PromptDefinition(
        key="interaction_summary_user",
        description=(
            "User prompt providing structured recent interaction excerpts and contact context "
            "for LLM-based interaction summary generation."
        ),
        used_by="app/api/v1/routes/scores.py::_summarize_recent_interactions_with_openai",
        template=(
            "Generate CRM interaction insights from this JSON payload:\n"
            "{context_json}"
        ),
    ),
    "mem0_relationship_updates_user": PromptDefinition(
        key="mem0_relationship_updates_user",
        description=(
            "Prompt sent to Mem0 to propose memory operations from an interaction summary, "
            "candidate claims, and existing accepted claims."
        ),
        used_by="app/integrations/mem0_oss_adapter.py::_compose_messages",
        template=(
            "Summarize factual relationship memory updates from this interaction.\n\n"
            "Interaction summary:\n"
            "{interaction_summary}\n\n"
            "Candidate claims:\n"
            "{candidates_json}\n\n"
            "Recent accepted claims:\n"
            "{recent_claims_json}"
        ),
    ),
    "cognee_extraction_query": PromptDefinition(
        key="cognee_extraction_query",
        description=(
            "Query prompt used when asking Cognee to extract entities, relations, and topics "
            "from an interaction payload."
        ),
        used_by="app/integrations/cognee_oss_adapter.py::_extract_with_cognee",
        template=(
            "Extract structured entities, relations, and topics from the interaction. "
            "Return JSON with keys: entities, relations, topics.\n"
            "interaction_id={interaction_id}\n"
            "text={interaction_text}"
        ),
    ),
    "writing_style_update_system": PromptDefinition(
        key="writing_style_update_system",
        description=(
            "System prompt for updating the global user writing style guide based on revised/approved drafts."
        ),
        used_by="app/services/prompts/style_learning.py::_generate_style_guide_markdown",
        template=(
            "You are a writing-style analyst for CRM email drafting. "
            "Given sample drafts and an existing style guide, produce an updated writing-style guide in markdown. "
            "Keep it concise, practical, and grounded in observed writing behavior. "
            "Do not include explanations outside the markdown guide."
        ),
    ),
    "writing_style_update_user": PromptDefinition(
        key="writing_style_update_user",
        description=(
            "User prompt containing existing style guide plus sample revised drafts for style-learning updates."
        ),
        used_by="app/services/prompts/style_learning.py::_generate_style_guide_markdown",
        template=(
            "Update the user writing style guide using the samples below.\n\n"
            "Requirements:\n"
            "- Preserve clear sections and actionable bullets.\n"
            "- Capture stable style tendencies (voice, structure, tone, phrasing).\n"
            "- Avoid contact-specific facts; this is a reusable global style guide.\n"
            "- Keep markdown under ~220 lines.\n\n"
            "Existing style guide markdown:\n"
            "{existing_style_markdown}\n\n"
            "Sample revised/approved drafts:\n"
            "{sample_drafts}"
        ),
    ),
}


def get_prompt_definitions() -> list[PromptDefinition]:
    return list(_PROMPTS.values())


def render_prompt(key: str, **variables: str) -> str:
    prompt = _PROMPTS.get(key)
    if prompt is None:
        raise KeyError(f"Unknown prompt key: {key}")

    try:
        return prompt.template.format(**variables)
    except KeyError as exc:
        missing_key = str(exc).strip("'")
        raise ValueError(f"Missing variable '{missing_key}' for prompt '{key}'") from exc


def _default_writing_style() -> str:
    return (
        "Use a clear, direct, professional style. Prefer short paragraphs, concrete requests, "
        "and a friendly but not overly casual tone."
    )


def _style_file_for_tone_band(tone_band: str | None) -> str | None:
    normalized = (tone_band or "").strip().lower()
    mapping = {
        "cool_professional": "writing_style_cool_professional.md",
        "warm_professional": "writing_style_warm_professional.md",
        "friendly_personal": "writing_style_friendly_personal.md",
    }
    return mapping.get(normalized)


def _read_style_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def load_user_writing_style_instructions() -> str:
    style_path = Path(__file__).resolve().parent / "writing_style.md"
    return _read_style_file(style_path) or _default_writing_style()


def load_relationship_writing_style_instructions(tone_band: str | None = None) -> str:
    style_file = _style_file_for_tone_band(tone_band)
    if not style_file:
        return (
            "Match tone to relationship maturity: more formal/professional for lower relationship strength, "
            "warmer and more personal for higher relationship strength."
        )
    style_path = Path(__file__).resolve().parent / style_file
    return _read_style_file(style_path) or (
        "Match tone to relationship maturity: more formal/professional for lower relationship strength, "
        "warmer and more personal for higher relationship strength."
    )


def load_combined_writing_style_instructions(tone_band: str | None = None) -> str:
    user_style = load_user_writing_style_instructions()
    relationship_style = load_relationship_writing_style_instructions(tone_band)
    return (
        "## User Baseline Style\n"
        f"{user_style}\n\n"
        "## Relationship-Context Style\n"
        f"{relationship_style}"
    )


def load_writing_style_instructions(tone_band: str | None = None) -> str:
    # Backward-compatible alias for existing imports.
    return load_combined_writing_style_instructions(tone_band)
