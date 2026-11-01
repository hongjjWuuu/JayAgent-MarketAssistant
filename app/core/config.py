from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "JayAgent Market Assistant"
    app_env: str = "dev"
    auth_mode: str = "development_headers"
    allow_dev_auth: bool = True
    persistence_backend: str = "memory"
    database_url: str = "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent"
    redis_url: str = "redis://localhost:6380/0"
    oidc_issuer: str = "http://localhost:8080/realms/market-assistant"
    oidc_audience: str = "market-assistant-api"
    oidc_jwks_url: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_provider: str = "openai_compatible"
    llm_prompt_version: str = "market-agent-v1"
    agent_max_steps: int = 12
    agent_max_llm_calls: int = 2
    agent_max_tool_calls: int = 3
    agent_total_timeout_seconds: float = 15.0
    llm_connect_timeout_seconds: float = 3.0
    llm_timeout_seconds: float = 8.0
    llm_max_output_tokens: int = 800
    llm_input_cost_per_million: float = 0.0
    llm_output_cost_per_million: float = 0.0
    inline_worker: bool = False
    worker_id: str = ""
    task_lease_seconds: int = 30
    task_max_attempts: int = 3
    reconciliation_delay_seconds: int = 30
    reconciliation_max_attempts: int = 5
    answerability_threshold: float = 0.015
    embedding_provider: str = "hash"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 1536
    retrieval_profile_id: str = "hash-v1"
    business_connector_mode: str = "repository"
    business_connector_base_url: str = ""
    business_connector_api_key: str = ""
    business_connector_timeout_seconds: float = 5.0
    worker_metrics_port: int = 9100
    metrics_tenant_id: str = "tenant_demo"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:5174"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def validate_settings(value: Settings) -> None:
    if value.auth_mode not in {"oidc", "development_headers"}:
        raise RuntimeError("AUTH_MODE must be oidc or development_headers")
    if value.app_env.lower() in {"prod", "production"} and (
        value.auth_mode == "development_headers" or value.allow_dev_auth
    ):
        raise RuntimeError("Production must use OIDC and must disable development authentication")
    if value.persistence_backend not in {"memory", "postgres"}:
        raise RuntimeError("PERSISTENCE_BACKEND must be memory or postgres")
    if value.business_connector_mode not in {"repository", "http"}:
        raise RuntimeError("BUSINESS_CONNECTOR_MODE must be repository or http")
    if value.business_connector_mode == "http" and not value.business_connector_base_url:
        raise RuntimeError("HTTP business connector requires BUSINESS_CONNECTOR_BASE_URL")
    if value.agent_max_steps < 3 or value.agent_max_llm_calls < 0 or value.agent_max_tool_calls < 1:
        raise RuntimeError("Agent execution budgets are invalid")
    if value.auth_mode == "oidc" and not (
        value.oidc_issuer and value.oidc_audience and value.oidc_jwks_url
    ):
        raise RuntimeError("OIDC mode requires issuer, audience and JWKS URL")
    origins = [item.strip() for item in value.cors_origins.split(",") if item.strip()]
    if any(origin == "*" for origin in origins):
        raise RuntimeError("CORS origins must be explicit")
