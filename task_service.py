"""
Сервис для работы с задачами
"""
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_

from models import Task, TaskCreateRequest, TaskUpdateRequest, TaskType, TaskStatus
from database import get_db_session
import logging

logger = logging.getLogger(__name__)


class TaskService:
    """Сервис для управления задачами"""
    
    def create_task(self, task_request: TaskCreateRequest) -> Task:
        """Создание новой задачи"""
        db = get_db_session()
        try:
            task = Task(
                title=task_request.title,
                description=task_request.description,
                telegram_message_id=task_request.telegram_message_id,
                telegram_chat_id=task_request.telegram_chat_id,
                telegram_user_id=task_request.telegram_user_id,
                task_type=task_request.task_type.value if task_request.task_type else None,
                status=TaskStatus.NEW.value
            )
            
            db.add(task)
            db.commit()
            db.refresh(task)
            
            logger.info(f"Создана задача #{task.id}")
            return task
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при создании задачи: {e}")
            raise
        finally:
            db.close()
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """Получение задачи по ID"""
        db = get_db_session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            return task
        finally:
            db.close()
    
    def update_task_type(self, task_id: int, task_type: TaskType) -> Optional[Task]:
        """Обновление типа задачи"""
        db = get_db_session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.task_type = task_type.value
                task.is_type_confirmed = True
                db.commit()
                db.refresh(task)
                logger.info(f"Обновлен тип задачи #{task_id} на {task_type.value}")
            return task
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при обновлении типа задачи: {e}")
            raise
        finally:
            db.close()
    
    def update_task_status(self, task_id: int, status: TaskStatus) -> Optional[Task]:
        """Обновление статуса задачи"""
        db = get_db_session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.status = status.value
                db.commit()
                db.refresh(task)
                logger.info(f"Обновлен статус задачи #{task_id} на {status.value}")
            return task
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при обновлении статуса задачи: {e}")
            raise
        finally:
            db.close()
    
    def update_bitrix_task_id(self, task_id: int, bitrix_task_id: int) -> Optional[Task]:
        """Обновление ID задачи в Bitrix24"""
        db = get_db_session()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                task.bitrix24_task_id = bitrix_task_id
                db.commit()
                db.refresh(task)
                logger.info(f"Обновлен Bitrix24 ID для задачи #{task_id}: {bitrix_task_id}")
            return task
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при обновлении Bitrix24 ID: {e}")
            raise
        finally:
            db.close()
    
    def get_user_tasks(self, telegram_user_id: str, 
                      status_filter: Optional[List[TaskStatus]] = None) -> List[Task]:
        """Получение задач пользователя"""
        db = get_db_session()
        try:
            query = db.query(Task).filter(Task.telegram_user_id == telegram_user_id)
            
            if status_filter:
                status_values = [status.value for status in status_filter]
                query = query.filter(Task.status.in_(status_values))
            else:
                # По умолчанию показываем только активные задачи
                query = query.filter(Task.status.in_([
                    TaskStatus.NEW.value,
                    TaskStatus.IN_PROGRESS.value
                ]))
            
            tasks = query.order_by(Task.created_at.desc()).all()
            return tasks
        finally:
            db.close()
    
    def get_tasks_by_chat(self, telegram_chat_id: str) -> List[Task]:
        """Получение задач из определенного чата"""
        db = get_db_session()
        try:
            tasks = db.query(Task).filter(
                Task.telegram_chat_id == telegram_chat_id
            ).order_by(Task.created_at.desc()).all()
            return tasks
        finally:
            db.close()
    
    def get_all_tasks(self, limit: int = 100, offset: int = 0) -> List[Task]:
        """Получение всех задач с пагинацией"""
        db = get_db_session()
        try:
            tasks = db.query(Task).order_by(
                Task.created_at.desc()
            ).offset(offset).limit(limit).all()
            return tasks
        finally:
            db.close()
    
    def get_tasks_stats(self) -> dict:
        """Получение статистики по задачам"""
        db = get_db_session()
        try:
            stats = {}
            
            # Статистика по статусам
            for status in TaskStatus:
                count = db.query(Task).filter(Task.status == status.value).count()
                stats[f"status_{status.value}"] = count
            
            # Статистика по типам
            for task_type in TaskType:
                count = db.query(Task).filter(Task.task_type == task_type.value).count()
                stats[f"type_{task_type.value}"] = count
            
            # Общее количество задач
            stats["total_tasks"] = db.query(Task).count()
            
            # Задачи с подтвержденным типом
            stats["confirmed_type"] = db.query(Task).filter(Task.is_type_confirmed == True).count()
            
            return stats
        finally:
            db.close()
