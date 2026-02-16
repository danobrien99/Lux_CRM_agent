from __future__ import annotations

from types import SimpleNamespace

from app.services.embeddings import embedder


def test_embed_texts_uses_openai_provider_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        embedder,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            embedding_dim=3,
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    captured: dict[str, object] = {}

    def fake_openai_embed(texts: list[str], *, model: str, dim: int, api_key: str) -> list[list[float]]:
        captured["texts"] = texts
        captured["model"] = model
        captured["dim"] = dim
        captured["api_key"] = api_key
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(embedder, "_embed_with_openai", fake_openai_embed)

    vectors = embedder.embed_texts(["first", "second"])

    assert vectors == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert captured["texts"] == ["first", "second"]
    assert captured["model"] == "text-embedding-3-small"
    assert captured["dim"] == 3
    assert captured["api_key"] == "test-key"


def test_embed_texts_falls_back_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        embedder,
        "get_settings",
        lambda: SimpleNamespace(
            embedding_provider="openai",
            embedding_model="text-embedding-3-small",
            embedding_dim=4,
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(embedder, "_embed_with_openai", lambda *_args, **_kwargs: [[9.0, 9.0, 9.0, 9.0]])

    vectors = embedder.embed_texts(["hello"])

    assert vectors == [embedder._hash_to_vector("hello", 4)]
