from __future__ import annotations


def summarize_news(title: str, body_plain: str) -> str:
    preview = body_plain[:300].strip()
    return f"{title}: {preview}"
