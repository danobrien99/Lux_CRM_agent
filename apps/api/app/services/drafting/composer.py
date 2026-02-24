from __future__ import annotations

import json
import logging
import os

from app.core.config import get_settings
from app.services.prompts import load_combined_writing_style_instructions, render_prompt

logger = logging.getLogger(__name__)


def _first_non_empty(values: list[str | None]) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _sanitize_snippet(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) > 180:
        return f"{normalized[:177].rstrip()}..."
    return normalized


def _subject_from_context(bundle: dict, tone: dict) -> str:
    objective = (bundle.get("objective") or "check in").strip()
    recent_interactions = bundle.get("recent_interactions", [])
    recent_subject = _first_non_empty(
        [interaction.get("subject") if isinstance(interaction, dict) else None for interaction in recent_interactions]
    )
    tone_band = str(tone.get("tone_band") or "")

    if recent_subject:
        if tone_band == "friendly_personal":
            subject = f"Quick follow-up on {recent_subject}"
        elif tone_band == "warm_professional":
            subject = f"Following up on {recent_subject}"
        else:
            subject = f"Follow-up: {recent_subject}"
    else:
        base = objective[0].upper() + objective[1:] if objective else "Checking in"
        if tone_band == "friendly_personal":
            subject = f"Quick check-in: {base}"
        elif tone_band == "warm_professional":
            subject = f"Follow-up: {base}"
        else:
            subject = base

    subject = " ".join(subject.split())
    if len(subject) > 90:
        return f"{subject[:87].rstrip()}..."
    return subject


def _compose_template_draft(bundle: dict, tone: dict) -> str:
    display_name = bundle["contact"].get("display_name") or "there"
    objective = bundle.get("objective") or "check in"
    recent_interactions = bundle.get("recent_interactions", [])
    graph_claim_snippets = bundle.get("graph_claim_snippets", [])
    graph_path_snippets = bundle.get("graph_path_snippets", [])
    email_context_snippets = bundle.get("email_context_snippets", [])
    proposed_next_action = bundle.get("proposed_next_action")
    opportunity_thread = bundle.get("opportunity_thread") if isinstance(bundle.get("opportunity_thread"), dict) else None

    if tone["tone_band"] == "cool_professional":
        opener = f"Hello {display_name},"
        bridge = "I wanted to follow up briefly."
    elif tone["tone_band"] == "warm_professional":
        opener = f"Hi {display_name},"
        bridge = "Hope you are doing well."
    else:
        opener = f"Hey {display_name},"
        bridge = "Hope all is good on your side."

    recent_subject = _first_non_empty(
        [interaction.get("subject") if isinstance(interaction, dict) else None for interaction in recent_interactions]
    )
    primary_claim = _first_non_empty([snippet if isinstance(snippet, str) else None for snippet in graph_claim_snippets])
    primary_path = _first_non_empty([snippet if isinstance(snippet, str) else None for snippet in graph_path_snippets])
    context_snippet = _first_non_empty([snippet if isinstance(snippet, str) else None for snippet in email_context_snippets])

    context_lines: list[str] = []
    context_lines.append(bridge)
    if recent_subject:
        context_lines.append(f"Great connecting on \"{recent_subject}\".")
    if primary_claim:
        context_lines.append(f"I kept in mind: {primary_claim}.")
    if primary_path:
        context_lines.append(f"Relationship context: {_sanitize_snippet(primary_path)}")
    if context_snippet:
        context_lines.append(f"From our recent thread: {_sanitize_snippet(context_snippet)}")
    if opportunity_thread:
        thread_subjects = opportunity_thread.get("recent_subjects") or []
        if thread_subjects and isinstance(thread_subjects[0], str):
            context_lines.append(f"I am picking up our thread on \"{thread_subjects[0]}\".")
    context_lines.append(f"I wanted to {objective}.")
    if isinstance(proposed_next_action, str) and proposed_next_action.strip():
        context_lines.append(f"Suggested next step: {proposed_next_action.strip()}")
    body = " ".join(context_lines)

    closer = "Best,\n[Your Name]"
    return f"{opener}\n\n{body}\n\n{closer}"


def _compose_openai_draft(bundle: dict, tone: dict) -> str | None:
    settings = get_settings()
    if settings.llm_provider.strip().lower() != "openai":
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
    except Exception:
        logger.exception("openai_sdk_missing_for_drafting")
        return None

    contact = bundle.get("contact", {})
    prompt_payload = {
        "contact_display_name": contact.get("display_name"),
        "objective": bundle.get("objective") or "check in",
        "retrieval_asof": bundle.get("retrieval_asof"),
        "tone_band": tone.get("tone_band"),
        "policy_flags": bundle.get("policy_flags") or {},
        "recent_interactions": (bundle.get("recent_interactions") or [])[:3],
        "recent_interactions_global": (bundle.get("recent_interactions_global") or [])[:5],
        "opportunity_thread": bundle.get("opportunity_thread"),
        "proposed_next_action": bundle.get("proposed_next_action"),
        "next_action_rationale": (bundle.get("next_action_rationale") or [])[:6],
        "graph_focus_terms": (bundle.get("graph_focus_terms") or [])[:10],
        "motivator_signals": (bundle.get("motivator_signals") or [])[:8],
        "graph_claim_snippets": (bundle.get("graph_claim_snippets") or [])[:5],
        "graph_path_snippets": (bundle.get("graph_path_snippets") or [])[:5],
        "graph_paths": (bundle.get("graph_paths") or [])[:5],
        "graph_metrics": bundle.get("graph_metrics") or {},
        "assertion_evidence_trace": (bundle.get("assertion_evidence_trace") or [])[:10],
        "internal_assertion_evidence_trace": (bundle.get("internal_assertion_evidence_trace") or [])[:8],
        "email_context_snippets": (bundle.get("email_context_snippets") or [])[:4],
    }
    style_instructions = load_combined_writing_style_instructions(tone.get("tone_band"))
    messages = [
        {
            "role": "system",
            "content": render_prompt(
                "draft_email_system",
                writing_style_instructions=style_instructions,
            ),
        },
        {
            "role": "user",
            "content": render_prompt(
                "draft_email_user",
                context_json=json.dumps(prompt_payload, ensure_ascii=True),
            ),
        },
    ]

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=0.4,
        )
        content = (response.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        logger.exception(
            "openai_draft_generation_failed",
            extra={"llm_model": settings.llm_model},
        )
        return None


def compose_draft(bundle: dict, tone: dict) -> str:
    llm_draft = _compose_openai_draft(bundle, tone)
    if llm_draft:
        return llm_draft
    return _compose_template_draft(bundle, tone)


def compose_subject(bundle: dict, tone: dict) -> str:
    return _subject_from_context(bundle, tone)
