from __future__ import annotations


def chunk_transcript_text(text: str, max_chars: int = 3200) -> list[dict]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    chunks: list[dict] = []
    current: list[str] = []
    start_line = 0
    current_len = 0

    for idx, line in enumerate(lines):
        line_len = len(line)
        if current and current_len + line_len > max_chars:
            chunks.append(
                {
                    "chunk_type": "transcript_segment",
                    "text": "\n".join(current),
                    "span_json": {"line_start": start_line, "line_end": idx - 1},
                }
            )
            current = [line]
            current_len = line_len
            start_line = idx
            continue
        current.append(line)
        current_len += line_len

    if current:
        chunks.append(
            {
                "chunk_type": "transcript_segment",
                "text": "\n".join(current),
                "span_json": {"line_start": start_line, "line_end": len(lines) - 1},
            }
        )
    return chunks
