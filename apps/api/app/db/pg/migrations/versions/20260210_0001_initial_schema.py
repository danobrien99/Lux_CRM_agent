"""initial schema

Revision ID: 20260210_0001
Revises:
Create Date: 2026-02-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "20260210_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.create_table(
        "raw_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_system", "external_id", name="uq_raw_events_source_external"),
    )

    op.create_table(
        "interactions",
        sa.Column("interaction_id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("participants_json", sa.JSON(), nullable=False),
        sa.Column("contact_ids_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("interaction_id"),
        sa.UniqueConstraint("source_system", "external_id", name="uq_interactions_source_external"),
    )

    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("interaction_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_type", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("span_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.interaction_id"]),
        sa.PrimaryKeyConstraint("chunk_id"),
    )

    op.create_table(
        "embeddings",
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"]),
        sa.PrimaryKeyConstraint("chunk_id"),
    )
    op.execute("CREATE INDEX IF NOT EXISTS embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "drafts",
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("prompt_json", sa.JSON(), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=False),
        sa.Column("tone_band", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("draft_id"),
    )

    op.create_table(
        "resolution_tasks",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("proposed_claim_id", sa.String(length=64), nullable=False),
        sa.Column("current_claim_id", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )

    op.create_table(
        "contact_cache",
        sa.Column("contact_id", sa.String(length=36), nullable=False),
        sa.Column("primary_email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("use_sensitive_in_drafts", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("contact_id"),
        sa.UniqueConstraint("primary_email"),
    )


def downgrade() -> None:
    op.drop_table("contact_cache")
    op.drop_table("resolution_tasks")
    op.drop_table("drafts")
    op.execute("DROP INDEX IF EXISTS embeddings_hnsw")
    op.drop_table("embeddings")
    op.drop_table("chunks")
    op.drop_table("interactions")
    op.drop_table("raw_events")
