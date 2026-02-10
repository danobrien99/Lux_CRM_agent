from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import Settings


def verify_webhook_secret(settings: Settings, secret_header: str | None) -> None:
    if not settings.n8n_webhook_secret:
        return
    if not secret_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook secret",
        )
    if secret_header != settings.n8n_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )


def webhook_secret_header(x_webhook_secret: str | None = Header(default=None)) -> str | None:
    return x_webhook_secret
