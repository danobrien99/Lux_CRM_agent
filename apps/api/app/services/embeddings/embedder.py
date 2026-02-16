from __future__ import annotations

import hashlib
import logging
import os

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _hash_to_vector(text: str, dim: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [b / 255.0 for b in digest]
    result = []
    for i in range(dim):
        result.append(values[i % len(values)])
    return result


def _fit_dimension(vector: list[float], dim: int) -> list[float]:
    cast = [float(value) for value in vector]
    if len(cast) == dim:
        return cast
    if len(cast) > dim:
        return cast[:dim]
    return cast + [0.0] * (dim - len(cast))


def _embed_with_openai(texts: list[str], *, model: str, dim: int, api_key: str) -> list[list[float]]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    request_payload: dict = {"model": model, "input": texts}
    if model.startswith("text-embedding-3"):
        request_payload["dimensions"] = dim

    response = client.embeddings.create(**request_payload)
    return [_fit_dimension(item.embedding, dim) for item in response.data]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    settings = get_settings()
    provider = settings.embedding_provider.strip().lower()
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            try:
                return _embed_with_openai(
                    texts,
                    model=settings.embedding_model,
                    dim=settings.embedding_dim,
                    api_key=api_key,
                )
            except Exception:
                logger.exception(
                    "openai_embeddings_failed_fallback_hash",
                    extra={"embedding_model": settings.embedding_model},
                )
        else:
            logger.warning("openai_embeddings_missing_api_key_fallback_hash")

    # Deterministic local fallback vector.
    return [_hash_to_vector(text, settings.embedding_dim) for text in texts]
