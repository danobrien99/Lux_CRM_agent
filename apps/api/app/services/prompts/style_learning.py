from __future__ import annotations

import os
from pathlib import Path

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.pg.models import Draft
from app.services.prompts import load_user_writing_style_instructions, render_prompt

STYLE_GUIDE_PATH = Path(__file__).resolve().parent / "writing_style.md"


def _draft_subject(draft: Draft) -> str:
    prompt_json = draft.prompt_json or {}
    value = prompt_json.get("draft_subject")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "No subject"


def _format_draft_sample(draft: Draft) -> str:
    subject = _draft_subject(draft)
    body = (draft.draft_text or "").strip()
    status = (draft.status or "unknown").strip()
    return (
        f"Draft ID: {draft.draft_id}\n"
        f"Status: {status}\n"
        f"Subject: {subject}\n"
        "Body:\n"
        f"{body}\n"
    )


def _collect_draft_samples(db: Session, current_draft: Draft, max_samples: int) -> list[Draft]:
    rows = db.scalars(
        select(Draft)
        .where(Draft.status.in_(["edited", "approved"]))
        .order_by(Draft.created_at.desc())
        .limit(max_samples)
    ).all()
    if not rows:
        return [current_draft]

    by_id = {row.draft_id: row for row in rows}
    by_id[current_draft.draft_id] = current_draft
    return list(by_id.values())[:max_samples]


def _generate_style_guide_markdown(existing_style: str, sample_text: str) -> str:
    settings = get_settings()
    if settings.llm_provider.strip().lower() != "openai":
        raise RuntimeError("Writing style guide update currently supports only LLM_PROVIDER=openai.")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required to update writing style guide.")

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": render_prompt("writing_style_update_system"),
        },
        {
            "role": "user",
            "content": render_prompt(
                "writing_style_update_user",
                existing_style_markdown=existing_style,
                sample_drafts=sample_text,
            ),
        },
    ]

    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        temperature=0.2,
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("LLM returned empty writing style guide content.")
    return content


def update_writing_style_guide_from_draft(db: Session, draft: Draft, max_samples: int = 24) -> dict:
    samples = _collect_draft_samples(db, draft, max_samples=max_samples)
    sample_text = "\n\n---\n\n".join(_format_draft_sample(item) for item in samples if (item.draft_text or "").strip())
    if not sample_text.strip():
        raise RuntimeError("No draft text samples available for writing style guide update.")

    existing_style = load_user_writing_style_instructions()
    updated_markdown = _generate_style_guide_markdown(existing_style, sample_text)
    STYLE_GUIDE_PATH.write_text(updated_markdown.strip() + "\n", encoding="utf-8")
    return {
        "updated": True,
        "samples_used": len(samples),
        "guide_path": str(STYLE_GUIDE_PATH),
    }
