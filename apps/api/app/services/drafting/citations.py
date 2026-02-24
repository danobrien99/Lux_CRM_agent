from __future__ import annotations

import re


def _snippet(text: str, max_chars: int = 220) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-zA-Z0-9]{3,}", text.lower()) if tok}


def build_citations_from_bundle(bundle: dict, draft_text: str | None = None) -> list[dict]:
    chunks = [chunk for chunk in (bundle.get("relevant_chunks") or []) if isinstance(chunk, dict)]
    if not chunks:
        return []

    paragraphs: list[str] = []
    if isinstance(draft_text, str) and draft_text.strip():
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", draft_text) if p.strip()]

    citations: list[dict] = []
    if not paragraphs:
        for idx, chunk in enumerate(chunks[:3], start=1):
            citations.append(
                {
                    "paragraph": idx,
                    "interaction_id": chunk["interaction_id"],
                    "chunk_id": chunk["chunk_id"],
                    "span_json": chunk.get("span_json", {}),
                    "snippet": _snippet(str(chunk.get("text", ""))),
                    "support_type": "chunk_support",
                }
            )
        return citations

    chunk_rows = []
    for chunk in chunks[:8]:
        chunk_text = str(chunk.get("text", ""))
        chunk_rows.append((chunk, _tokens(chunk_text)))

    for para_idx, paragraph in enumerate(paragraphs, start=1):
        para_tokens = _tokens(paragraph)
        if not para_tokens:
            continue
        ranked: list[tuple[int, dict]] = []
        for chunk, chunk_tokens in chunk_rows:
            overlap = len(para_tokens & chunk_tokens)
            if overlap <= 0:
                continue
            ranked.append((overlap, chunk))
        ranked.sort(key=lambda item: (-item[0], str(item[1].get("chunk_id") or "")))
        if not ranked:
            # Do not attach arbitrary evidence to unsupported paragraphs.
            continue
        for overlap, chunk in ranked[:2]:
            citations.append(
                {
                    "paragraph": para_idx,
                    "interaction_id": chunk.get("interaction_id"),
                    "chunk_id": chunk.get("chunk_id"),
                    "span_json": chunk.get("span_json", {}),
                    "snippet": _snippet(str(chunk.get("text", ""))),
                    "support_type": "chunk_support",
                    "overlap_terms": overlap,
                }
            )
    return citations
