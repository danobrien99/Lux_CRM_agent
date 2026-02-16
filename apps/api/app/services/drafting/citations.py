from __future__ import annotations


def _snippet(text: str, max_chars: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def build_citations_from_bundle(bundle: dict) -> list[dict]:
    citations = []
    for idx, chunk in enumerate(bundle.get("relevant_chunks", [])[:3], start=1):
        citations.append(
            {
                "paragraph": idx,
                "interaction_id": chunk["interaction_id"],
                "chunk_id": chunk["chunk_id"],
                "span_json": chunk.get("span_json", {}),
                "snippet": _snippet(str(chunk.get("text", ""))),
            }
        )
    return citations
