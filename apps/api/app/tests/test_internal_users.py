from __future__ import annotations

from types import SimpleNamespace

from app.services.identity import internal_users


def test_is_internal_email_matches_domain_and_explicit_email(monkeypatch) -> None:
    internal_users.clear_internal_identity_cache()
    monkeypatch.setattr(
        internal_users,
        "get_settings",
        lambda: SimpleNamespace(
            internal_email_domains="luxcrm.ai,corp.example",
            internal_user_emails="danobrien99@gmail.com,Daniel@Obrien-Sustainability.com",
        ),
    )
    internal_users.clear_internal_identity_cache()

    assert internal_users.is_internal_email("person@luxcrm.ai") is True
    assert internal_users.is_internal_email("danobrien99@gmail.com") is True
    assert internal_users.is_internal_email("DANIEL@OBRIEN-SUSTAINABILITY.COM") is True
    assert internal_users.is_internal_email("external@example.com") is False


def test_internal_user_external_id_is_stable() -> None:
    assert internal_users.internal_user_external_id("Test@Example.com") == "internal:test@example.com"
    assert internal_users.internal_user_external_id("") is None

