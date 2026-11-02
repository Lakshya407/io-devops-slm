"""Runtime configuration for the IO Coding Agent.

Every value can be supplied through environment variables. Model names are
never hardcoded in the application logic; they come from ``AGENT_MODEL``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- Model access -----------------------------------------------------
    agent_model: str = Field(default="qwen2.5-coder:1.5b", alias="AGENT_MODEL")
    agent_public_model_id: str = Field(default="io-coding-agent", alias="AGENT_PUBLIC_MODEL_ID")
    litellm_base_url: str = Field(default="http://litellm:4000/v1", alias="LITELLM_BASE_URL")
    litellm_api_key: str = Field(default="", alias="LITELLM_API_KEY")
    llm_timeout_seconds: int = Field(default=300, alias="AGENT_LLM_TIMEOUT_SECONDS")
    model_num_ctx: int = Field(default=8192, alias="AGENT_MODEL_NUM_CTX")
    model_is_local: bool = Field(default=True, alias="AGENT_MODEL_IS_LOCAL")
    allow_cloud_code_context: bool = Field(default=False, alias="AGENT_ALLOW_CLOUD_CODE_CONTEXT")

    # --- Public API -------------------------------------------------------
    agent_api_key: str = Field(default="", alias="AGENT_API_KEY")
    trust_forwarded_user: bool = Field(default=False, alias="AGENT_TRUST_FORWARDED_USER")

    # --- Persistence ------------------------------------------------------
    database_url: str = Field(default="", alias="AGENT_DATABASE_URL")

    # --- Workspaces -------------------------------------------------------
    workspaces_dir: str = Field(default="/workspaces", alias="AGENT_WORKSPACES_DIR")
    repos_dir: str = Field(default="/repos", alias="AGENT_REPOS_DIR")
    max_repo_size_mb: int = Field(default=200, alias="AGENT_MAX_REPO_SIZE_MB")
    allow_any_https_repo: bool = Field(default=True, alias="AGENT_ALLOW_ANY_HTTPS_REPO")
    repo_allowlist: str = Field(default="", alias="AGENT_REPO_ALLOWLIST")
    allow_insecure_http: bool = Field(default=False, alias="AGENT_ALLOW_INSECURE_HTTP")
    clone_timeout_seconds: int = Field(default=300, alias="AGENT_CLONE_TIMEOUT_SECONDS")
    protect_tests: bool = Field(default=True, alias="AGENT_PROTECT_TESTS")

    # --- Sandbox ----------------------------------------------------------
    sandbox_url: str = Field(default="http://sandbox-runner:8090", alias="AGENT_SANDBOX_URL")
    sandbox_token: str = Field(default="", alias="SANDBOX_TOKEN")
    sandbox_image: str = Field(default="io-agent-sandbox:latest", alias="AGENT_SANDBOX_IMAGE")
    command_timeout_seconds: int = Field(default=300, alias="AGENT_COMMAND_TIMEOUT_SECONDS")
    max_output_bytes: int = Field(default=200_000, alias="AGENT_MAX_OUTPUT_BYTES")
    enable_docker_build: bool = Field(default=False, alias="AGENT_ENABLE_DOCKER_BUILD")
    enable_kubernetes: bool = Field(default=False, alias="AGENT_ENABLE_KUBERNETES")

    # --- Agent loop -------------------------------------------------------
    max_attempts: int = Field(default=3, alias="AGENT_MAX_ATTEMPTS")
    max_repairs: int = Field(default=2, alias="AGENT_MAX_REPAIRS")
    max_inspect_steps: int = Field(default=3, alias="AGENT_MAX_INSPECT_STEPS")
    max_context_files: int = Field(default=6, alias="AGENT_MAX_CONTEXT_FILES")
    max_file_bytes: int = Field(default=200_000, alias="AGENT_MAX_FILE_BYTES")
    max_prompt_file_chars: int = Field(default=24_000, alias="AGENT_MAX_PROMPT_FILE_CHARS")
    task_timeout_seconds: int = Field(default=1800, alias="AGENT_TASK_TIMEOUT_SECONDS")
    max_concurrent_tasks: int = Field(default=2, alias="AGENT_MAX_CONCURRENT_TASKS")

    # --- Operations -------------------------------------------------------
    log_level: str = Field(default="INFO", alias="AGENT_LOG_LEVEL")
    store_raw_output: bool = Field(default=False, alias="AGENT_STORE_RAW_OUTPUT")
    dataset_export_dir: str = Field(default="/data/exports", alias="AGENT_DATASET_EXPORT_DIR")
    approval_ttl_minutes: int = Field(default=60, alias="AGENT_APPROVAL_TTL_MINUTES")

    @field_validator("litellm_base_url", "sandbox_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return str(value).rstrip("/")

    @property
    def repo_allowlist_hosts(self) -> List[str]:
        return [h.strip().lower() for h in self.repo_allowlist.split(",") if h.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
