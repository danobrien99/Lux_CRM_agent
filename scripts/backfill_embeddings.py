from __future__ import annotations

from sqlalchemy import select

from app.core.config import get_settings
from app.db.pg.models import Chunk
from app.db.pg.session import SessionLocal
from app.services.embeddings.vector_store import insert_chunk_embeddings


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        chunks = db.scalars(select(Chunk)).all()
        if not chunks:
            print("No chunks found")
            return
        insert_chunk_embeddings(db, chunks, settings.embedding_model)
        print(f"Backfilled {len(chunks)} chunks")
    finally:
        db.close()


if __name__ == "__main__":
    main()
