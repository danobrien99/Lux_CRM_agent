"""Route package marker.

Keep this module import-light to avoid circular imports when service/worker
code imports a specific route module (for shared helpers).
"""

__all__ = [
    "admin",
    "cases",
    "contacts",
    "drafts",
    "health",
    "ingest",
    "news",
    "resolution",
    "scores",
]
