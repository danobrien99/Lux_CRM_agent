from __future__ import annotations

from app.services.prompts import (
    get_prompt_definitions,
    load_combined_writing_style_instructions,
    load_writing_style_instructions,
    render_prompt,
)


def test_prompt_definitions_include_metadata() -> None:
    prompts = get_prompt_definitions()
    assert prompts
    for prompt in prompts:
        assert prompt.key
        assert prompt.description
        assert prompt.used_by
        assert prompt.template


def test_render_prompt_supports_known_prompt_keys() -> None:
    rendered = render_prompt(
        "draft_email_user",
        context_json='{"objective":"check in"}',
    )
    assert "Generate a single email draft" in rendered
    assert '{"objective":"check in"}' in rendered


def test_writing_style_instructions_loaded() -> None:
    style_text = load_writing_style_instructions()
    assert isinstance(style_text, str)
    assert style_text.strip() != ""


def test_writing_style_instructions_change_by_tone_band() -> None:
    cool_style = load_writing_style_instructions("cool_professional")
    warm_style = load_writing_style_instructions("warm_professional")
    friendly_style = load_writing_style_instructions("friendly_personal")

    assert "low or still developing" in cool_style
    assert "relationship strength is moderate" in warm_style
    assert "relationship strength is high" in friendly_style


def test_combined_writing_style_contains_user_and_relationship_layers() -> None:
    combined = load_combined_writing_style_instructions("friendly_personal")
    assert "User Baseline Style" in combined
    assert "User Writing Style Guide" in combined
    assert "Relationship-Context Style" in combined
    assert "relationship strength is high" in combined
