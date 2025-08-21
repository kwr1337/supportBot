"""
Конфигурация для Telegram бота с интеграцией Битрикс24
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram Bot Configuration
    telegram_bot_token: str
    
    # Bitrix24 Configuration
    bitrix24_domain: str
    bitrix24_access_token: str
    bitrix24_user_id: Optional[str] = None
    
    # Database Configuration
    database_url: str = "sqlite:///./support_bot.db"
    
    # Redis Configuration
    redis_url: Optional[str] = None
    
    # Logging
    log_level: str = "INFO"
    
    class Config:
        env_file = ".env"


settings = Settings()
