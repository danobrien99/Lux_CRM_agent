from __future__ import annotations


def chunk_email_text(text: str, max_chars: int = 4200) -> list[dict]:
    stripped = "\n".join(line for line in text.splitlines() if line.strip())
    paragraphs = [p.strip() for p in stripped.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[dict] = []
    current = ""
    start_para = 0
    for idx, para in enumerate(paragraphs):
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        chunks.append(
            {
                "chunk_type": "email_body",
                "text": current,
                "span_json": {"paragraph_start": start_para, "paragraph_end": idx - 1},
            }
        )
        current = para
        start_para = idx

    if current:
        chunks.append(
            {
                "chunk_type": "email_body",
                "text": current,
                "span_json": {"paragraph_start": start_para, "paragraph_end": len(paragraphs) - 1},
            }
        )
    return chunks
