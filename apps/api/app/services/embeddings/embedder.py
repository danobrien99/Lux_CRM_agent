from __future__ import annotations

import hashlib

from app.core.config import get_settings


def _hash_to_vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [b / 255.0 for b in digest]
    result = []
    for i in range(dim):
        result.append(values[i % len(values)])
    return result


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    # Deterministic local fallback vector. Replace with provider call in production.
    return [_hash_to_vector(text, settings.embedding_dim) for text in texts]
