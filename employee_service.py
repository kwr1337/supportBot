"""
Сервис управления сотрудниками в чатах
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

from models import ChatEmployee, BotUser, UserRole
from database import get_db_session
import logging

logger = logging.getLogger(__name__)


class EmployeeService:
    """Сервис для управления сотрудниками в чатах"""
    
    def add_employee_to_chat(self, telegram_chat_id: str, telegram_user_id: str, 
                           bitrix24_user_id: Optional[int] = None, added_by: str = "system") -> bool:
        """Добавление сотрудника в чат"""
        db = get_db_session()
        try:
            # Проверяем, не добавлен ли уже
            existing = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == telegram_user_id
            ).first()
            
            if existing:
                if not existing.is_active:
                    # Реактивируем сотрудника
                    existing.is_active = True
                    existing.added_by = added_by
                    db.commit()
                    logger.info(f"Реактивирован сотрудник {telegram_user_id} в чате {telegram_chat_id}")
                    return True
                else:
                    logger.info(f"Сотрудник {telegram_user_id} уже добавлен в чат {telegram_chat_id}")
                    return False
            
            # Добавляем нового сотрудника
            employee = ChatEmployee(
                telegram_chat_id=telegram_chat_id,
                telegram_user_id=telegram_user_id,
                bitrix24_user_id=bitrix24_user_id,
                added_by=added_by
            )
            
            db.add(employee)
            db.commit()
            
            logger.info(f"Добавлен сотрудник {telegram_user_id} в чат {telegram_chat_id}")
            return True
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка добавления сотрудника: {e}")
            return False
        finally:
            db.close()
    
    def get_chat_employees(self, telegram_chat_id: str) -> List[ChatEmployee]:
        """Получение списка сотрудников чата"""
        db = get_db_session()
        try:
            employees = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.is_active == True
            ).all()
            
            return employees
            
        finally:
            db.close()
    
    def get_employee_bitrix_id(self, telegram_chat_id: str, telegram_user_id: str) -> Optional[int]:
        """Получение Bitrix24 ID сотрудника в конкретном чате"""
        db = get_db_session()
        try:
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == telegram_user_id,
                ChatEmployee.is_active == True
            ).first()
            
            if employee:
                return employee.bitrix24_user_id
            
            return None
            
        finally:
            db.close()
    
    def is_employee_in_chat(self, telegram_chat_id: str, telegram_user_id: str) -> bool:
        """Проверка, является ли пользователь сотрудником в чате"""
        db = get_db_session()
        try:
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == telegram_user_id,
                ChatEmployee.is_active == True
            ).first()
            
            return employee is not None
            
        finally:
            db.close()
    
    def remove_employee_from_chat(self, telegram_chat_id: str, telegram_user_id: str, 
                                removed_by: str = "system") -> bool:
        """Удаление сотрудника из чата"""
        db = get_db_session()
        try:
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == telegram_user_id
            ).first()
            
            if employee:
                employee.is_active = False
                employee.added_by = f"Удален: {removed_by}"
                db.commit()
                
                logger.info(f"Удален сотрудник {telegram_user_id} из чата {telegram_chat_id}")
                return True
            
            return False
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка удаления сотрудника: {e}")
            return False
        finally:
            db.close()
    
    def update_employee_bitrix_id(self, telegram_chat_id: str, telegram_user_id: str, 
                                bitrix24_user_id: int) -> bool:
        """Обновление Bitrix24 ID сотрудника"""
        db = get_db_session()
        try:
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == telegram_user_id,
                ChatEmployee.is_active == True
            ).first()
            
            if employee:
                employee.bitrix24_user_id = bitrix24_user_id
                db.commit()
                
                logger.info(f"Обновлен Bitrix24 ID сотрудника {telegram_user_id}: {bitrix24_user_id}")
                return True
            
            return False
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка обновления Bitrix24 ID сотрудника: {e}")
            return False
        finally:
            db.close()
    
    def find_linked_telegram_id(self, bitrix24_user_id: int) -> Optional[str]:
        """Поиск связанного Telegram ID для Bitrix24 пользователя"""
        db = get_db_session()
        try:
            # Сначала ищем в глобальной таблице BotUser
            bot_user = db.query(BotUser).filter(
                BotUser.bitrix24_user_id == bitrix24_user_id
            ).first()
            
            if bot_user:
                return bot_user.telegram_user_id
            
            # Если в глобальной таблице нет, ищем в ChatEmployee
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.bitrix24_user_id == bitrix24_user_id,
                ChatEmployee.is_active == True,
                ~ChatEmployee.telegram_user_id.like('pending_%')
            ).first()
            
            if employee:
                return employee.telegram_user_id
            
            return None
            
        finally:
            db.close()
    
    def get_bitrix_id_by_telegram_id(self, telegram_id: str) -> Optional[int]:
        """Получение Bitrix24 ID по Telegram ID"""
        db = get_db_session()
        try:
            # Сначала ищем в глобальной таблице
            bot_user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_id
            ).first()
            
            if bot_user and bot_user.bitrix24_user_id:
                return bot_user.bitrix24_user_id
            
            # Если в глобальной таблице нет, ищем в ChatEmployee
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_user_id == telegram_id,
                ChatEmployee.is_active == True
            ).first()
            
            if employee:
                return employee.bitrix24_user_id
            
            return None
            
        finally:
            db.close()
    
    def update_global_user_profile(self, telegram_id: str, bitrix24_user_id: int) -> bool:
        """Обновление глобального профиля пользователя с Bitrix24 ID"""
        db = get_db_session()
        try:
            # Ищем существующего пользователя
            bot_user = db.query(BotUser).filter(
                BotUser.telegram_user_id == telegram_id
            ).first()
            
            if bot_user:
                # Обновляем существующего
                bot_user.bitrix24_user_id = bitrix24_user_id
            else:
                # Создаем нового с ролью клиента по умолчанию
                bot_user = BotUser(
                    telegram_user_id=telegram_id,
                    role=UserRole.CLIENT,
                    bitrix24_user_id=bitrix24_user_id
                )
                db.add(bot_user)
            
            db.commit()
            logger.info(f"Обновлен глобальный профиль: Telegram {telegram_id} -> Bitrix24 {bitrix24_user_id}")
            return True
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка обновления глобального профиля: {e}")
            return False
        finally:
            db.close()
    
    def update_employee_telegram_id(self, telegram_chat_id: str, old_telegram_id: str, 
                                  new_telegram_id: str) -> bool:
        """Обновление Telegram ID сотрудника в чате"""
        db = get_db_session()
        try:
            employee = db.query(ChatEmployee).filter(
                ChatEmployee.telegram_chat_id == telegram_chat_id,
                ChatEmployee.telegram_user_id == old_telegram_id,
                ChatEmployee.is_active == True
            ).first()
            
            if employee:
                employee.telegram_user_id = new_telegram_id
                db.commit()
                
                logger.info(f"Обновлен Telegram ID сотрудника: {old_telegram_id} -> {new_telegram_id}")
                return True
            
            return False
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка обновления Telegram ID сотрудника: {e}")
            return False
        finally:
            db.close()


# Создаем глобальный экземпляр сервиса
employee_service = EmployeeService()
