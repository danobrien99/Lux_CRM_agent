from __future__ import annotations

from app.db.pg.base import Base
from types import SimpleNamespace

from sqlalchemy import select

from app.db.pg.models import ContactCache, ResolutionTask
from app.db.pg.session import SessionLocal, engine
from app.workers.jobs import _enqueue_speaker_resolution_tasks, _resolve_contact_ids


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_resolve_contact_ids_matches_name_only_speaker_exact_display_name() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-1",
                primary_email="alex@example.com",
                display_name="Alex Johnson",
                owner_user_id=None,
                use_sensitive_in_drafts=False,
            )
        )
        db.commit()

        (
            matched_ids,
            unresolved_emails,
            _participant_names,
            _internal_names,
            provenance,
            speaker_suggestions,
        ) = _resolve_contact_ids(
            db,
            {
                "from": [{"name": "Alex Johnson"}],
                "to": [],
                "cc": [],
            },
        )

        assert matched_ids == ["contact-1"]
        assert unresolved_emails == []
        assert provenance["contact-1"]["match_method"] == "name_exact"
        assert speaker_suggestions == []
    finally:
        db.close()


def test_resolve_contact_ids_emits_ambiguous_name_only_speaker_candidates() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add_all(
            [
                ContactCache(
                    contact_id="contact-a",
                    primary_email="alexa@example.com",
                    display_name="Alex Smith",
                    owner_user_id=None,
                    use_sensitive_in_drafts=False,
                ),
                ContactCache(
                    contact_id="contact-b",
                    primary_email="alex.b@example.com",
                    display_name="Alex B Smith",
                    owner_user_id=None,
                    use_sensitive_in_drafts=False,
                ),
            ]
        )
        db.commit()

        result = _resolve_contact_ids(
            db,
            {
                "from": [{"name": "Alex Smith"}],
                "to": [],
                "cc": [],
            },
        )
        matched_ids, _unresolved_emails, _participant_names, _internal_names, provenance, speaker_suggestions = result

        assert matched_ids == ["contact-a"]  # exact name still wins for exact single match
        assert provenance["contact-a"]["match_method"] == "name_exact"
        assert speaker_suggestions == []

        result_ambiguous = _resolve_contact_ids(
            db,
            {
                "from": [{"name": "Alex S Smith"}],
                "to": [],
                "cc": [],
            },
        )
        (
            matched_ids_ambig,
            _unresolved_emails_ambig,
            _participant_names_ambig,
            _internal_names_ambig,
            _provenance_ambig,
            speaker_suggestions_ambig,
        ) = result_ambiguous

        assert matched_ids_ambig == []
        assert len(speaker_suggestions_ambig) == 1
        assert speaker_suggestions_ambig[0]["match_method"] == "name_ambiguous"
        assert len(speaker_suggestions_ambig[0]["candidates"]) >= 2
        confidences = [candidate["confidence"] for candidate in speaker_suggestions_ambig[0]["candidates"]]
        assert all(0.0 <= confidence <= 1.0 for confidence in confidences)
        assert confidences == sorted(confidences, reverse=True)
    finally:
        db.close()


def test_resolve_contact_ids_returns_no_match_speaker_suggestion() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-1",
                primary_email="alex@example.com",
                display_name="Alex Johnson",
                owner_user_id=None,
                use_sensitive_in_drafts=False,
            )
        )
        db.commit()

        result = _resolve_contact_ids(
            db,
            {
                "from": [{"name": "Taylor Unknown"}],
                "to": [],
                "cc": [],
            },
        )
        (
            matched_ids,
            _unresolved_emails,
            _participant_names,
            _internal_names,
            provenance,
            speaker_suggestions,
        ) = result

        assert matched_ids == []
        assert provenance == {}
        assert len(speaker_suggestions) == 1
        assert speaker_suggestions[0]["match_method"] == "name_no_match"
        assert speaker_suggestions[0]["candidates"] == []
    finally:
        db.close()


def test_enqueue_speaker_resolution_tasks_creates_and_dedupes_tasks() -> None:
    reset_db()
    db = SessionLocal()
    try:
        interaction = SimpleNamespace(interaction_id="int-1", source_system="meeting")
        suggestions = [
            {
                "speaker_name": "Taylor Unknown",
                "match_method": "name_no_match",
                "candidates": [],
            },
            {
                "speaker_name": "Alex S Smith",
                "match_method": "name_ambiguous",
                "candidates": [
                    {"contact_id": "contact-a", "display_name": "Alex Smith", "primary_email": "a@example.com", "confidence": 0.55},
                    {"contact_id": "contact-b", "display_name": "Alex B Smith", "primary_email": "b@example.com", "confidence": 0.55},
                ],
            },
        ]

        created_first = _enqueue_speaker_resolution_tasks(db, interaction, suggestions)
        created_second = _enqueue_speaker_resolution_tasks(db, interaction, suggestions)

        assert created_first == 2
        assert created_second == 0
        tasks = db.scalars(select(ResolutionTask).where(ResolutionTask.task_type == "speaker_identity_resolution")).all()
        assert len(tasks) == 2
        payloads = {task.payload_json["speaker_name"]: task.payload_json for task in tasks}
        assert payloads["Taylor Unknown"]["gate_results"]["match_method"] == "name_no_match"
        assert payloads["Alex S Smith"]["gate_results"]["match_method"] == "name_ambiguous"
        assert payloads["Alex S Smith"]["gate_results"]["candidate_count"] == 2
    finally:
        db.close()
