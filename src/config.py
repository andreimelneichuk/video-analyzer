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

    # Universal OpenAI-Compatible Multimodal AI settings
    # Compatible with OpenRouter, Alibaba DashScope, SiliconFlow, vLLM, OpenAI, etc.
    AI_PROVIDER: str = Field(
        default="openai_compatible",
        description="Provider mode: 'openai_compatible' (default) or 'gemini'",
    )
    OPENAI_API_KEY: str = Field(
        default="", description="API key for any OpenAI-compatible provider"
    )
    OPENAI_BASE_URL: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for OpenAI-compatible endpoint (e.g. OpenRouter, DashScope, SiliconFlow)",
    )
    OPENAI_MODEL: str = Field(
        default="qwen/qwen3.8-omni-flash",
        description="Model identifier (e.g. qwen/qwen3.8-omni-flash, gpt-4o-mini)",
    )

    # Legacy / Provider-specific aliases (for backward compatibility)
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API key alias")
    OPENROUTER_MODEL: str = Field(
        default="qwen/qwen3.8-omni-flash",
        description="OpenRouter model slug alias",
    )
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API key")

    @property
    def effective_api_key(self) -> str:
        """Returns the active API key across generic and specific configs."""
        return self.OPENAI_API_KEY or self.OPENROUTER_API_KEY or self.GEMINI_API_KEY

    @property
    def effective_base_url(self) -> str:
        """Returns the active base URL for OpenAI-compatible requests."""
        return self.OPENAI_BASE_URL or "https://openrouter.ai/api/v1"

    @property
    def effective_model(self) -> str:
        """Returns the active model slug across generic and specific configs."""
        return self.OPENAI_MODEL or self.OPENROUTER_MODEL or "qwen/qwen3.8-omni-flash"

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
