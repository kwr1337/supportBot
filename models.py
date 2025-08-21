"""
Модели данных для бота поддержки
"""
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, create_engine, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel

Base = declarative_base()


class TaskType(str, Enum):
    """Типы задач"""
    BUG = "bug"
    REQUIREMENT = "requirement" 
    CONSULTATION = "consultation"


class TaskStatus(str, Enum):
    """Статусы задач"""
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class UserRole(str, Enum):
    """Роли пользователей"""
    ADMIN = "admin"
    CLIENT = "client"


class Task(Base):
    """Модель задачи в базе данных"""
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_message_id = Column(Integer, nullable=False)
    telegram_chat_id = Column(String, nullable=False)
    telegram_user_id = Column(String, nullable=False)
    bitrix24_task_id = Column(Integer, nullable=True)
    
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(String(50), nullable=True)  # TaskType enum
    status = Column(String(50), default=TaskStatus.NEW.value)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    is_type_confirmed = Column(Boolean, default=False)


class TaskCreateRequest(BaseModel):
    """Запрос на создание задачи"""
    title: str
    description: Optional[str] = None
    telegram_message_id: int
    telegram_chat_id: str
    telegram_user_id: str
    task_type: Optional[TaskType] = None


class TaskUpdateRequest(BaseModel):
    """Запрос на обновление задачи"""
    status: Optional[TaskStatus] = None
    task_type: Optional[TaskType] = None
    bitrix24_task_id: Optional[int] = None


class UserSession(Base):
    """Модель пользовательской сессии для диалогов"""
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, nullable=False, unique=True)
    current_task_id = Column(Integer, nullable=True)
    state = Column(String(100), nullable=True)  # current dialog state
    context = Column(Text, nullable=True)  # JSON context data
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotUser(Base):
    """Модель пользователя бота"""
    __tablename__ = "bot_users"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, nullable=False, unique=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    role = Column(String(50), default=UserRole.CLIENT.value)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Дополнительные поля
    added_by = Column(String, nullable=True)  # Кто добавил пользователя
    notes = Column(Text, nullable=True)  # Заметки об пользователе
    bitrix24_user_id = Column(Integer, nullable=True)  # ID в Битрикс24


class ChatEmployee(Base):
    """Модель сотрудников в чатах"""
    __tablename__ = "chat_employees"
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_chat_id = Column(String, nullable=False)
    telegram_user_id = Column(String, nullable=False)
    bitrix24_user_id = Column(Integer, nullable=True)
    
    is_active = Column(Boolean, default=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    added_by = Column(String, nullable=True)  # Кто добавил сотрудника
    
    # Уникальный индекс: один пользователь может быть сотрудником только один раз в чате
    __table_args__ = (
        UniqueConstraint('telegram_chat_id', 'telegram_user_id', name='unique_employee_chat'),
    )
