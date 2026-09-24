from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "myapp-ge"
    app_env: str = "development"
    
    # Pydantic searchs .env 
    database_url: str 

    model_config = SettingsConfigDict(
        # Agregamos un fallback de ruta por si ejecutas uvicorn desde una subcarpeta
        env_file=(".env", "../.env"), 
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()