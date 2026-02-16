from __future__ import annotations

from sqlalchemy import select

from app.db.pg.base import Base
from app.db.pg.models import Draft
from app.db.pg.session import SessionLocal, engine
from app.services.prompts import style_learning


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_update_writing_style_guide_from_draft_writes_file(monkeypatch, tmp_path) -> None:
    reset_db()
    db = SessionLocal()
    try:
        draft = Draft(
            contact_id="contact-1",
            prompt_json={"draft_subject": "Checking in"},
            draft_text="Hi Alex,\n\nSharing a quick update.\n\nBest,\nSam",
            citations_json=[],
            tone_band="warm_professional",
            status="edited",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        style_path = tmp_path / "writing_style.md"
        style_path.write_text("# Existing Style\n- concise\n", encoding="utf-8")
        monkeypatch.setattr(style_learning, "STYLE_GUIDE_PATH", style_path)
        monkeypatch.setattr(
            style_learning,
            "_generate_style_guide_markdown",
            lambda *_args, **_kwargs: "# User Writing Style Guide\n- updated",
        )

        result = style_learning.update_writing_style_guide_from_draft(db, draft, max_samples=10)

        assert result["updated"] is True
        assert result["samples_used"] >= 1
        assert style_path.read_text(encoding="utf-8").startswith("# User Writing Style Guide")
    finally:
        db.close()


def test_collect_draft_samples_prefers_edited_and_approved() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add_all(
            [
                Draft(
                    contact_id="contact-1",
                    prompt_json={"draft_subject": "One"},
                    draft_text="Body one",
                    citations_json=[],
                    tone_band="cool_professional",
                    status="proposed",
                ),
                Draft(
                    contact_id="contact-1",
                    prompt_json={"draft_subject": "Two"},
                    draft_text="Body two",
                    citations_json=[],
                    tone_band="warm_professional",
                    status="approved",
                ),
            ]
        )
        db.commit()

        current = db.scalar(select(Draft).where(Draft.status == "proposed"))
        assert current is not None

        samples = style_learning._collect_draft_samples(db, current, max_samples=10)
        statuses = {item.status for item in samples}
        assert "approved" in statuses
        assert current in samples
    finally:
        db.close()
