from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """AI Service 运行配置。"""

    ai_database_url: str = (
        "postgresql+psycopg://customer_service:customer_service"
        "@127.0.0.1:5432/ai_customer_service"
    )
    api_host: str = "0.0.0.0"
    api_port: int = 8002
    internal_service_token: str = "customer-internal-token"
    jwt_secret: str = "ecommerce-secret"
    jwt_algorithm: str = "HS256"
    llm_provider: Literal["openai_compatible", "deepseek", "qwen"] = "openai_compatible"
    ecommerce_base_url: str = "http://127.0.0.1:8001/api/v1"
    ecommerce_timeout_seconds: float = 8.0
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str
    llm_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    llm_timeout_seconds: float = Field(default=30, gt=0)
    history_message_limit: int = Field(default=30, ge=1, le=60)
    history_character_budget: int = Field(default=12000, ge=1)
    prompt_version: str = "welfare-support-v1"
    agent_run_timeout_seconds: float = Field(default=120, gt=0)
    agent_mode: Literal["single", "multi"] = "single"
    customer_service_base_url: str = "http://127.0.0.1:8000"
    multi_task_timeout_seconds: float = Field(default=45, gt=0)
    multi_max_tasks: int = Field(default=8, ge=1, le=20)
    multi_max_tools: int = Field(default=24, ge=1)
    multi_task_max_tools: int = Field(default=6, ge=1)
    multi_max_model_calls: int = Field(default=24, ge=1)
    multi_token_budget: int = Field(default=60000, ge=1000)
    multi_model_output_tokens: int = Field(default=2000, ge=256)
    multi_summary_output_tokens: int = Field(default=4000, ge=256)
    multi_concurrency: int = Field(default=2, ge=1, le=6)
    multi_process_concurrency: int = Field(default=6, ge=1, le=32)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
