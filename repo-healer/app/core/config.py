"""Centralised application settings using pydantic-settings."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for all configuration.

    No module may read os.environ directly — all config flows through get_settings().
    """

    # API keys
    grok_api_key: SecretStr = SecretStr("xai-placeholder")
    github_token: SecretStr = SecretStr("ghp-placeholder")
    target_repo_url: str = ""  # optional — user provides via frontend
    target_branch: str = "main"

    # Pipeline tuning
    risk_threshold: float = 0.7
    max_heal_retries: int = 2
    file_extensions: list[str] = [".py", ".js", ".ts", ".tsx", ".html", ".css"]
    llm_temperature: float = 0.2
    llm_model: str = "grok-3"

    # Infrastructure
    redis_url: str | None = None
    log_level: str = "INFO"
    analysis_timeout_secs: int = 300

    # LLM safety
    max_diff_lines: int = 50

    model_config = SettingsConfigDict(env_file=".env", secrets_dir="/run/secrets")

    @field_validator("llm_temperature")
    @classmethod
    def temperature_cap(cls, v: float) -> float:
        if v > 0.4:
            raise ValueError(
                "LLM temperature above 0.4 is not permitted — risk of logic mutation"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached singleton settings instance."""
    return Settings()
