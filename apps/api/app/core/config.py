from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Lux CRM API"
    environment: str = "dev"
    api_prefix: str = "/v1"

    neon_pg_dsn: str = "sqlite:///./luxcrm.db"
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""

    redis_url: str = "redis://redis:6379/0"
    queue_mode: str = "redis"

    n8n_webhook_secret: str = ""

    google_sheets_id: str = ""
    google_sheets_service_account_json: str = ""
    google_sheets_range: str = "Contacts!A:Z"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    cognee_backend: str = "local"
    cognee_local_module: str = "app.integrations.cognee_oss_adapter"
    cognee_local_function: str = "extract_candidates"
    cognee_endpoint: str = ""
    cognee_repo_path: str = "../Third_Party/cognee"
    cognee_dataset_name: str = "lux_crm"
    cognee_search_type: str = "GRAPH_COMPLETION"
    cognee_search_top_k: int = 8
    cognee_enable_heuristic_fallback: bool = False

    mem0_backend: str = "local"
    mem0_local_module: str = "app.integrations.mem0_oss_adapter"
    mem0_local_function: str = "propose_memory_ops"
    mem0_endpoint: str = ""
    mem0_repo_path: str = "../Third_Party/mem0"
    mem0_collection_name: str = "lux_crm_memories"
    mem0_graph_database: str = "neo4j"
    mem0_agent_id: str = "lux_crm_agent"
    mem0_search_limit: int = 25
    mem0_enable_rules_fallback: bool = False

    data_cleanup_enabled: bool = True
    data_cleanup_schedule_cron: str = "0 3 * * *"
    data_retention_raw_days: int = 180
    data_retention_chunks_days: int = 365
    data_retention_drafts_days: int = 365

    auto_accept_threshold: float = Field(default=0.90, ge=0.0, le=1.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
