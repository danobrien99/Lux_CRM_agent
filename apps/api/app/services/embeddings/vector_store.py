from __future__ import annotations

from math import sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.pg.models import Chunk, Embedding, Interaction
from app.services.embeddings.embedder import embed_texts


def insert_chunk_embeddings(db: Session, chunk_records: list[Chunk], embedding_model: str) -> None:
    vectors = embed_texts([chunk.text for chunk in chunk_records])
    for chunk, vector in zip(chunk_records, vectors, strict=True):
        db.merge(
            Embedding(
                chunk_id=chunk.chunk_id,
                embedding=vector,
                embedding_model=embedding_model,
            )
        )
    db.commit()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _contact_match(contact_ids_json: list[str] | None, contact_id: str | None) -> bool:
    if contact_id is None:
        return True
    return bool(contact_ids_json and contact_id in contact_ids_json)


def _fallback_text_search(db: Session, query: str, top_k: int, contact_id: str | None) -> list[dict]:
    query_vector = embed_texts([query])[0]
    rows = db.execute(
        select(Chunk, Interaction.contact_ids_json)
        .join(Interaction, Interaction.interaction_id == Chunk.interaction_id)
        .order_by(Chunk.created_at.desc())
        .limit(max(top_k * 20, 200))
    ).all()

    scored = []
    for chunk, contact_ids_json in rows:
        if not _contact_match(contact_ids_json, contact_id):
            continue
        candidate_vector = embed_texts([chunk.text])[0]
        similarity = _cosine_similarity(query_vector, candidate_vector)
        scored.append(
            {
                "chunk_id": chunk.chunk_id,
                "interaction_id": chunk.interaction_id,
                "text": chunk.text,
                "span_json": chunk.span_json,
                "score": round(max(similarity, 0.0), 6),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


def search_chunks(db: Session, query: str, top_k: int = 10, contact_id: str | None = None) -> list[dict]:
    query_vector = embed_texts([query])[0]
    fetch_limit = max(top_k * 20, 200)

    try:
        distance = Embedding.embedding.cosine_distance(query_vector).label("distance")
        rows = db.execute(
            select(Chunk, Interaction.contact_ids_json, distance)
            .join(Embedding, Embedding.chunk_id == Chunk.chunk_id)
            .join(Interaction, Interaction.interaction_id == Chunk.interaction_id)
            .order_by(distance.asc())
            .limit(fetch_limit)
        ).all()
    except Exception:
        # SQLite and partially-initialized environments do not support pgvector operators.
        return _fallback_text_search(db, query, top_k, contact_id)

    ranked = []
    for chunk, contact_ids_json, distance_value in rows:
        if not _contact_match(contact_ids_json, contact_id):
            continue
        score = max(0.0, min(1.0, 1.0 - float(distance_value)))
        ranked.append(
            {
                "chunk_id": chunk.chunk_id,
                "interaction_id": chunk.interaction_id,
                "text": chunk.text,
                "span_json": chunk.span_json,
                "score": round(score, 6),
            }
        )
        if len(ranked) >= top_k:
            break
    return ranked
