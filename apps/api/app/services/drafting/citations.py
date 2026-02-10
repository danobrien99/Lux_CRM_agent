from __future__ import annotations


def build_citations_from_bundle(bundle: dict) -> list[dict]:
    citations = []
    for idx, chunk in enumerate(bundle.get("relevant_chunks", [])[:3], start=1):
        citations.append(
            {
                "paragraph": idx,
                "interaction_id": chunk["interaction_id"],
                "chunk_id": chunk["chunk_id"],
                "span_json": chunk.get("span_json", {}),
            }
        )
    return citations
