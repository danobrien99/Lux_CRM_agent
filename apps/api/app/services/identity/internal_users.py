from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


@lru_cache(maxsize=1)
def internal_email_domains() -> frozenset[str]:
    settings = get_settings()
    values = [part.strip().lower() for part in settings.internal_email_domains.split(",") if part.strip()]
    return frozenset(values)


@lru_cache(maxsize=1)
def internal_user_emails() -> frozenset[str]:
    settings = get_settings()
    values = [_normalize_email(part) for part in settings.internal_user_emails.split(",") if part.strip()]
    return frozenset(v for v in values if v)


def clear_internal_identity_cache() -> None:
    internal_email_domains.cache_clear()
    internal_user_emails.cache_clear()


def is_internal_email(email: str | None) -> bool:
    normalized = _normalize_email(email)
    if not normalized or "@" not in normalized:
        return False
    if normalized in internal_user_emails():
        return True
    domain = normalized.rsplit("@", 1)[-1]
    return domain in internal_email_domains()


def internal_user_external_id(email: str | None) -> str | None:
    normalized = _normalize_email(email)
    if not normalized:
        return None
    return f"internal:{normalized}"

