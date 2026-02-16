from __future__ import annotations

import json
import os
import re
from typing import Any

from app.core.config import get_settings

try:
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
except ImportError:  # pragma: no cover - optional dependency during local bootstrap
    Credentials = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]


_TRUTHY_VALUES = {"1", "true", "yes", "y", "on"}


def _normalize_header(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY_VALUES


def _first_non_empty(payload: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        raw = payload.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return ""


def _load_service_account_info(raw_value: str) -> dict[str, Any]:
    raw = raw_value.strip()
    if not raw:
        raise ValueError("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON is empty")

    if os.path.exists(raw):
        with open(raw, encoding="utf-8") as handle:
            return json.load(handle)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Common env representation keeps newlines escaped.
        return json.loads(raw.replace("\\n", "\n"))


def _parse_rows(values: list[list[str]]) -> list[dict[str, Any]]:
    if not values:
        return []

    headers = [_normalize_header(cell) for cell in values[0]]
    if not headers:
        return []

    rows: list[dict[str, Any]] = []
    for row in values[1:]:
        if not any(str(cell).strip() for cell in row):
            continue

        payload: dict[str, Any] = {}
        for idx, header in enumerate(headers):
            if not header:
                continue
            payload[header] = row[idx].strip() if idx < len(row) else ""

        primary_email = _first_non_empty(payload, ["primary_email", "email"]).lower()
        contact_id = _first_non_empty(payload, ["contact_id", "contactid", "id"])
        if not primary_email or not contact_id:
            continue

        first_name = _first_non_empty(payload, ["first_name", "firstname"]) or None
        last_name = _first_non_empty(payload, ["last_name", "lastname"]) or None
        display_name = _first_non_empty(payload, ["display_name", "full_name", "name"]) or None
        if not display_name:
            joined = " ".join(part for part in [first_name, last_name] if part)
            display_name = joined or None

        company = _first_non_empty(payload, ["company", "organization", "org"]) or None
        owner_user_id = _first_non_empty(payload, ["owner_user_id", "owner", "owner_user"]) or None
        notes = _first_non_empty(payload, ["notes", "note"]) or None

        rows.append(
            {
                "contact_id": contact_id,
                "primary_email": primary_email,
                "display_name": display_name,
                "first_name": first_name,
                "last_name": last_name,
                "company": company,
                "owner_user_id": owner_user_id,
                "notes": notes,
                "use_sensitive_in_drafts": _coerce_bool(payload.get("use_sensitive_in_drafts")),
            }
        )
    return rows


def fetch_sheet_rows() -> list[dict]:
    settings = get_settings()

    if not settings.google_sheets_id:
        raise ValueError("GOOGLE_SHEETS_ID is not configured")
    if not settings.google_sheets_service_account_json:
        raise ValueError("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON is not configured")
    if Credentials is None or build is None:
        raise RuntimeError(
            "Google Sheets client dependencies are missing. Install google-auth and google-api-python-client."
        )

    info = _load_service_account_info(settings.google_sheets_service_account_json)
    credentials = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheets_id, range=settings.google_sheets_range)
        .execute()
    )
    return _parse_rows(result.get("values", []))
