from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "CampuSupport"
    secret_key: str = "dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    database_url: str = "sqlite:///./campusupport.db"
    ai_api_key: str | None = None
    ai_api_base: str | None = None
    notify_webhook_url: str | None = None
    internal_secret: str = "dev-internal-secret"

    class Config:
        env_file = ".env"


settings = Settings()
