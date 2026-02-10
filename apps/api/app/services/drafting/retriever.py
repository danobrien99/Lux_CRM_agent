from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.pg.models import ContactCache, Interaction
from app.services.embeddings.vector_store import search_chunks


def build_retrieval_bundle(db: Session, contact_id: str, objective: str | None, allow_sensitive: bool) -> dict:
    contact = db.scalar(select(ContactCache).where(ContactCache.contact_id == contact_id))
    all_interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(500)).all()
    interactions = [
        interaction
        for interaction in all_interactions
        if contact_id in (interaction.contact_ids_json or [])
    ][:3]
    query = objective or (contact.display_name if contact else "follow up")
    relevant_chunks = search_chunks(db, query=query, top_k=5, contact_id=contact_id)
    return {
        "contact": {
            "contact_id": contact_id,
            "display_name": contact.display_name if contact else None,
            "primary_email": contact.primary_email if contact else None,
        },
        "recent_interactions": [
            {
                "interaction_id": i.interaction_id,
                "timestamp": i.timestamp.isoformat(),
                "subject": i.subject,
            }
            for i in interactions
        ],
        "relevant_chunks": relevant_chunks,
        "allow_sensitive": allow_sensitive,
        "objective": objective,
    }
