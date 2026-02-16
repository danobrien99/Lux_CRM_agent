# Prompt Catalog
Central catalog for prompts used by extraction, memory, scoring, and drafting.

## `draft_email_system`
- Where used: `app/services/drafting/composer.py::_compose_openai_draft`
- How used: System prompt for outbound draft generation; injects combined style rules:
  - user baseline style from `writing_style.md` (applies to all drafts)
  - relationship-context style from tone-band-specific files:
  - `writing_style_cool_professional.md`
  - `writing_style_warm_professional.md`
  - `writing_style_friendly_personal.md`
  - fallback: generic relationship-style guidance if a specific file is unavailable

## `draft_email_user`
- Where used: `app/services/drafting/composer.py::_compose_openai_draft`
- How used: User prompt that passes structured draft context as JSON.

## `warmth_depth_system`
- Where used: `app/services/scoring/content_signals.py::_score_with_openai`
- How used: System prompt for LLM-based relationship warmth/depth scoring with strict JSON output requirements.

## `warmth_depth_user`
- Where used: `app/services/scoring/content_signals.py::_score_with_openai`
- How used: User prompt that passes recent interaction excerpts for scoring.

## `interaction_summary_system`
- Where used: `app/api/v1/routes/scores.py::_summarize_recent_interactions_with_openai`
- How used: System prompt for LLM-generated contact interaction summaries; enforces JSON output with `summary`, `recent_topics`, and stubbed `priority_next_step`.

## `interaction_summary_user`
- Where used: `app/api/v1/routes/scores.py::_summarize_recent_interactions_with_openai`
- How used: User prompt that passes recent interaction excerpts plus contact context as JSON for summarization.

## `mem0_relationship_updates_user`
- Where used: `app/integrations/mem0_oss_adapter.py::_compose_messages`
- How used: User prompt to request memory operations from interaction summary + candidate claims + recent claims.

## `cognee_extraction_query`
- Where used: `app/integrations/cognee_oss_adapter.py::_extract_with_cognee`
- How used: Extraction query requesting entities, relations, and topics in JSON form.

## `writing_style_update_system`
- Where used: `app/services/prompts/style_learning.py::_generate_style_guide_markdown`
- How used: System prompt that instructs the LLM to rewrite the global user style guide from revised draft samples.

## `writing_style_update_user`
- Where used: `app/services/prompts/style_learning.py::_generate_style_guide_markdown`
- How used: User prompt that provides existing style guide markdown and revised/approved draft samples.
