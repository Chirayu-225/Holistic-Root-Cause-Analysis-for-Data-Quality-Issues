from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://rca:rca_dev_password@localhost:5432/rca_framework"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    environment: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()
