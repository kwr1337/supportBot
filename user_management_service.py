"""
Сервис управления пользователями и ролями
"""
from typing import Optional, List
from sqlalchemy.orm import Session
from telegram import User as TelegramUser

from models import BotUser, UserRole
from database import get_db_session
import logging

logger = logging.getLogger(__name__)


class UserManagementService:
    """Сервис для управления пользователями и их ролями"""
    
    def get_or_create_user(self, telegram_user: TelegramUser, 
                          added_by: Optional[str] = None) -> BotUser:
        """Получение или создание пользователя"""
        db = get_db_session()
        try:
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == str(telegram_user.id)
            ).first()
            
            if user:
                # Обновляем информацию о пользователе
                user.username = telegram_user.username
                user.first_name = telegram_user.first_name
                user.last_name = telegram_user.last_name
                db.commit()
                db.refresh(user)
                return user
            else:
                # Создаем нового пользователя
                user = BotUser(
                    telegram_user_id=str(telegram_user.id),
                    username=telegram_user.username,
                    first_name=telegram_user.first_name,
                    last_name=telegram_user.last_name,
                    role=UserRole.CLIENT.value,  # По умолчанию клиент
                    added_by=added_by
                )
                
                db.add(user)
                db.commit()
                db.refresh(user)
                
                logger.info(f"Создан новый пользователь: {telegram_user.id} ({telegram_user.first_name})")
                return user
                
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при работе с пользователем: {e}")
            raise
        finally:
            db.close()
    
    def get_user_role(self, telegram_user_id: str) -> Optional[UserRole]:
        """Получение роли пользователя"""
        db = get_db_session()
        try:
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_user_id,
                BotUser.is_active == True
            ).first()
            
            if user:
                return UserRole(user.role)
            return None
            
        finally:
            db.close()
    
    def is_admin(self, telegram_user_id: str) -> bool:
        """Проверка, является ли пользователь администратором"""
        role = self.get_user_role(telegram_user_id)
        return role == UserRole.ADMIN
    
    def is_client(self, telegram_user_id: str) -> bool:
        """Проверка, является ли пользователь клиентом"""
        role = self.get_user_role(telegram_user_id)
        return role == UserRole.CLIENT
    
    def set_user_role(self, telegram_user_id: str, new_role: UserRole, 
                     changed_by: str) -> bool:
        """Изменение роли пользователя"""
        db = get_db_session()
        try:
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_user_id
            ).first()
            
            if user:
                old_role = user.role
                user.role = new_role.value
                db.commit()
                
                logger.info(f"Роль пользователя {telegram_user_id} изменена с {old_role} на {new_role.value} пользователем {changed_by}")
                return True
            
            return False
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка изменения роли пользователя: {e}")
            raise
        finally:
            db.close()
    
    def get_all_users(self, active_only: bool = True) -> List[BotUser]:
        """Получение всех пользователей"""
        db = get_db_session()
        try:
            query = db.query(BotUser)
            
            if active_only:
                query = query.filter(BotUser.is_active == True)
            
            users = query.order_by(BotUser.created_at.desc()).all()
            return users
            
        finally:
            db.close()
    
    def get_admins(self) -> List[BotUser]:
        """Получение всех администраторов"""
        db = get_db_session()
        try:
            admins = db.query(BotUser).filter(
                BotUser.role == UserRole.ADMIN.value,
                BotUser.is_active == True
            ).all()
            return admins
            
        finally:
            db.close()
    
    def deactivate_user(self, telegram_user_id: str, deactivated_by: str) -> bool:
        """Деактивация пользователя"""
        db = get_db_session()
        try:
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_user_id
            ).first()
            
            if user:
                user.is_active = False
                user.notes = f"Деактивирован {deactivated_by}"
                db.commit()
                
                logger.info(f"Пользователь {telegram_user_id} деактивирован пользователем {deactivated_by}")
                return True
            
            return False
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка деактивации пользователя: {e}")
            raise
        finally:
            db.close()
    
    def get_user_stats(self, telegram_user_id: str) -> dict:
        """Получение статистики пользователя"""
        db = get_db_session()
        try:
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_user_id
            ).first()
            
            if not user:
                return {}
            
            # Подсчитываем задачи пользователя
            from models import Task
            
            total_tasks = db.query(Task).filter(
                Task.telegram_user_id == telegram_user_id
            ).count()
            
            completed_tasks = db.query(Task).filter(
                Task.telegram_user_id == telegram_user_id,
                Task.status == "completed"
            ).count()
            
            return {
                "user": user,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "completion_rate": round((completed_tasks / total_tasks) * 100, 2) if total_tasks > 0 else 0
            }
            
        finally:
            db.close()
    
    def is_first_user(self) -> bool:
        """Проверка, является ли это первым пользователем (автоматически делаем админом)"""
        db = get_db_session()
        try:
            user_count = db.query(BotUser).count()
            return user_count == 0
        finally:
            db.close()


# Создаем глобальный экземпляр сервиса
user_management = UserManagementService()
