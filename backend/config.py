from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    BASE_URL: Optional[str] = None
    API_TOKEN: Optional[str] = None
    MODEL: Optional[str] = None
    FUNCTIONCALL_MODEL: Optional[str] = None
    SERPER_API_KEY: Optional[str] = None
    SERPER_API_URL: str = "https://google.serper.dev/search"
    MAX_CONTENT_LENGTH: int = 2000  # 网页内容最大长度限制

    class Config:
        env_file = ".env"

settings = Settings()
