"""
Управление базой данных
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.declarative import declarative_base
from config import settings
from models import Base
import logging

logger = logging.getLogger(__name__)

# Создание движка базы данных
engine = create_engine(settings.database_url, echo=False)

# Создание сессии
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables():
    """Создание всех таблиц в базе данных"""
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Таблицы базы данных успешно созданы")
    except Exception as e:
        logger.error(f"Ошибка при создании таблиц: {e}")
        raise


def get_db() -> Session:
    """Получение сессии базы данных"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Получение сессии базы данных (синхронная версия)"""
    return SessionLocal()
