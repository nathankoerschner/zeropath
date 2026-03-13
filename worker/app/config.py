from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Worker settings loaded from environment variables."""

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/zeropath"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Scanner
    clone_base_dir: str = "/tmp/zeropath-clones"
    max_file_retries: int = 2
    max_concurrent_files: int = 5

    # General
    environment: str = "development"
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
