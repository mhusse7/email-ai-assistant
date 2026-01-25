"""Configuration settings for Email AI Assistant."""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Email Configuration
    imap_host: str = Field(default="imap.mail.me.com")
    imap_port: int = Field(default=993)
    imap_user: str
    imap_password: str

    smtp_host: str = Field(default="smtp.mail.me.com")
    smtp_port: int = Field(default=587)
    smtp_user: str
    smtp_password: str

    # Whitelist
    allowed_senders: str = Field(default="")

    # Gemini
    gemini_api_key: str

    # SerpAPI
    serpapi_key: str

    # PostgreSQL
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="email_assistant")
    postgres_user: str = Field(default="postgres")
    postgres_password: str

    # Qdrant
    qdrant_host: str = Field(default="qdrant")
    qdrant_port: int = Field(default=6333)
    qdrant_api_key: str = Field(default="")
    qdrant_collection: str = Field(default="email_conversations")

    # App Settings
    polling_interval_seconds: int = Field(default=30)
    log_level: str = Field(default="INFO")
    error_notification_email: str = Field(default="")

    @property
    def allowed_senders_list(self) -> list[str]:
        """Parse allowed senders into a list."""
        if not self.allowed_senders:
            return []
        return [s.strip().lower() for s in self.allowed_senders.split(",") if s.strip()]

    @property
    def postgres_url(self) -> str:
        """Build PostgreSQL connection URL."""
        return f"postgresql://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def postgres_async_url(self) -> str:
        """Build async PostgreSQL connection URL."""
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
