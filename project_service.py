"""
Сервис для работы с проектами (чатами)
"""
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct

from models import Task, TaskStatus
from database import get_db_session
import logging

logger = logging.getLogger(__name__)


class ProjectService:
    """Сервис для управления проектами (чатами) и задачами по проектам"""
    
    def get_user_projects(self, telegram_user_id: str, is_admin: bool = False) -> List[Dict[str, Any]]:
        """Получение списка проектов (чатов) пользователя"""
        db = get_db_session()
        try:
            if is_admin:
                # Админы видят все проекты
                projects_query = db.query(
                    Task.telegram_chat_id,
                    func.max(Task.created_at).label('last_activity'),
                    func.count(Task.id).label('total_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.NEW.value).label('new_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.IN_PROGRESS.value).label('in_progress_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED.value).label('completed_tasks')
                ).group_by(Task.telegram_chat_id)
            else:
                # Клиенты видят только свои проекты
                projects_query = db.query(
                    Task.telegram_chat_id,
                    func.max(Task.created_at).label('last_activity'),
                    func.count(Task.id).label('total_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.NEW.value).label('new_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.IN_PROGRESS.value).label('in_progress_tasks'),
                    func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED.value).label('completed_tasks')
                ).filter(Task.telegram_user_id == telegram_user_id).group_by(Task.telegram_chat_id)
            
            projects_data = projects_query.order_by(func.max(Task.created_at).desc()).all()
            
            projects = []
            for project_data in projects_data:
                chat_id = project_data.telegram_chat_id
                
                # Получаем название чата из последней задачи
                sample_task = db.query(Task).filter(Task.telegram_chat_id == chat_id).first()
                
                project_info = {
                    "chat_id": chat_id,
                    "chat_name": self._get_chat_name_from_task(sample_task),
                    "last_activity": project_data.last_activity,
                    "total_tasks": project_data.total_tasks,
                    "new_tasks": project_data.new_tasks or 0,
                    "in_progress_tasks": project_data.in_progress_tasks or 0,
                    "completed_tasks": project_data.completed_tasks or 0
                }
                
                projects.append(project_info)
            
            return projects
            
        finally:
            db.close()
    
    def get_project_tasks(self, telegram_chat_id: str, telegram_user_id: str, 
                         is_admin: bool = False, page: int = 0, per_page: int = 5) -> Dict[str, Any]:
        """Получение задач проекта с пагинацией"""
        db = get_db_session()
        try:
            if is_admin:
                # Админы видят все задачи проекта
                query = db.query(Task).filter(Task.telegram_chat_id == telegram_chat_id)
            else:
                # Клиенты видят только свои задачи в проекте
                query = db.query(Task).filter(
                    Task.telegram_chat_id == telegram_chat_id,
                    Task.telegram_user_id == telegram_user_id
                )
            
            # Подсчитываем общее количество
            total_tasks = query.count()
            
            # Получаем задачи для текущей страницы
            tasks = query.order_by(Task.created_at.desc()).offset(page * per_page).limit(per_page).all()
            
            # Получаем информацию о чате
            chat_name = self._get_chat_name_from_task(tasks[0]) if tasks else "Неизвестный проект"
            
            return {
                "chat_id": telegram_chat_id,
                "chat_name": chat_name,
                "tasks": tasks,
                "page": page,
                "per_page": per_page,
                "total_tasks": total_tasks,
                "total_pages": (total_tasks + per_page - 1) // per_page,
                "has_next": (page + 1) * per_page < total_tasks,
                "has_prev": page > 0
            }
            
        finally:
            db.close()
    
    def _get_chat_name_from_task(self, task: Optional[Task]) -> str:
        """Извлечение названия чата из описания задачи"""
        if not task or not task.description:
            return "Неизвестный проект"
        
        try:
            # Ищем название чата в расширенном описании
            lines = task.description.split('\n')
            for line in lines:
                if "• Название:" in line:
                    return line.split("• Название:")[-1].strip()
            
            # Если не найдено, возвращаем ID чата
            return f"Проект {task.telegram_chat_id[-4:]}"
            
        except Exception:
            return f"Проект {task.telegram_chat_id[-4:] if task.telegram_chat_id else 'Unknown'}"
    
    def _get_chat_name_from_task_by_chat_id(self, chat_id: str) -> str:
        """Получение названия чата по ID чата"""
        db = get_db_session()
        try:
            # Находим любую задачу из этого чата
            task = db.query(Task).filter(Task.telegram_chat_id == chat_id).first()
            return self._get_chat_name_from_task(task)
        finally:
            db.close()
    
    def get_project_statistics(self, telegram_chat_id: str) -> Dict[str, Any]:
        """Получение статистики по проекту"""
        db = get_db_session()
        try:
            # Общее количество задач в проекте
            total_tasks = db.query(Task).filter(Task.telegram_chat_id == telegram_chat_id).count()
            
            if total_tasks == 0:
                return {"chat_id": telegram_chat_id, "total_tasks": 0}
            
            # Статистика по статусам
            status_stats = {}
            for status in TaskStatus:
                count = db.query(Task).filter(
                    Task.telegram_chat_id == telegram_chat_id,
                    Task.status == status.value
                ).count()
                status_stats[status.value] = count
            
            # Уникальные пользователи в проекте
            unique_users = db.query(distinct(Task.telegram_user_id)).filter(
                Task.telegram_chat_id == telegram_chat_id
            ).count()
            
            # Последняя активность
            last_task = db.query(Task).filter(
                Task.telegram_chat_id == telegram_chat_id
            ).order_by(Task.created_at.desc()).first()
            
            return {
                "chat_id": telegram_chat_id,
                "chat_name": self._get_chat_name_from_task(last_task),
                "total_tasks": total_tasks,
                "status_stats": status_stats,
                "unique_users": unique_users,
                "last_activity": last_task.created_at if last_task else None,
                "completion_rate": round((status_stats.get("completed", 0) / total_tasks) * 100, 2)
            }
            
        finally:
            db.close()


# Создаем глобальный экземпляр сервиса
project_service = ProjectService()
