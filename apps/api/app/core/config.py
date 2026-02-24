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
    db_pool_size: int = Field(default=20, ge=1, le=200)
    db_max_overflow: int = Field(default=40, ge=0, le=400)
    db_pool_timeout_seconds: int = Field(default=60, ge=1, le=600)
    db_pool_recycle_seconds: int = Field(default=1800, ge=30, le=86400)
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""

    redis_url: str = "redis://redis:6379/0"
    queue_mode: str = "redis"
    queue_retry_max: int = Field(default=2, ge=0, le=10)
    queue_retry_interval_seconds: int = Field(default=60, ge=5, le=3600)

    n8n_webhook_secret: str = ""
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

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
    cognee_local_timeout_seconds: int = Field(default=120, ge=5, le=900)

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
    mem0_local_timeout_seconds: int = Field(default=120, ge=5, le=900)
    ontology_config_path: str = "app/services/ontology/ontology_config.json"
    graph_v2_enabled: bool = True
    graph_v2_dual_write: bool = False
    graph_v2_read_v2: bool = True
    graph_v2_case_opportunity_threshold: float = Field(default=0.68, ge=0.0, le=1.0)
    graph_v2_case_contact_promotion_min_evidence: int = Field(default=2, ge=1, le=100)
    graph_v2_case_contact_promotion_max_age_days: int = Field(default=180, ge=1, le=3650)
    graph_v2_inference_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    graph_v2_inference_max_age_days: int = Field(default=180, ge=1, le=3650)
    shacl_validation_enabled: bool = True
    shacl_validation_on_write: bool = True
    internal_email_domains: str = "luxcrm.ai"
    internal_user_emails: str = ""

    data_cleanup_enabled: bool = True
    data_cleanup_schedule_cron: str = "0 3 * * *"
    data_retention_raw_days: int = 180
    data_retention_chunks_days: int = 365
    data_retention_drafts_days: int = 365

    auto_accept_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    scoring_use_llm_warmth_depth: bool = False
    scoring_llm_max_interactions: int = Field(default=8, ge=1, le=25)
    scoring_llm_snippet_chars: int = Field(default=280, ge=80, le=2000)
    interaction_summary_cache_enabled: bool = True
    interaction_summary_cache_ttl_seconds: int = Field(default=21600, ge=60, le=604800)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
