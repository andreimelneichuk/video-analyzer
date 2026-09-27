from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration settings."""

    APP_NAME: str = "Skycoach Reels Analyzer"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8080

    # Database: SQLite in WAL mode by default, or PostgreSQL
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/skycoach.db",
        description="Async database connection string",
    )

    # Redis connection URL
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for RQ background workers",
    )

    # Multimodal AI settings (Default per ARCHITECTURE.md: Qwen via OpenRouter)
    AI_PROVIDER: Literal["openrouter", "gemini"] = Field(
        default="openrouter",
        description="Primary AI provider: 'openrouter' (Qwen Omni) or 'gemini'",
    )
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API key")
    OPENROUTER_MODEL: str = Field(
        default="qwen/qwen-2.5-omni",
        description="Model slug on OpenRouter (e.g. qwen/qwen-2.5-omni / qwen/qwen-3.8-omni-flash)",
    )
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")

    # Timeouts and business limits
    TASK_TIMEOUT_SECONDS: int = Field(
        default=180,
        description="Timeout in seconds for single video download & analysis",
    )
    MAX_BATCH_URLS: int = Field(
        default=20,
        description="Maximum allowed URLs per batch request (as specified in test task)",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
