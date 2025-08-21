"""
Сервис синхронизации статусов задач с Битрикс24 (без webhook)
"""
import asyncio
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta

from models import Task, TaskStatus
from database import get_db_session
from bitrix24_api import bitrix24_api
from task_service import TaskService

logger = logging.getLogger(__name__)


class StatusSyncService:
    """Сервис для синхронизации статусов задач с Битрикс24"""
    
    def __init__(self):
        self.task_service = TaskService()
        self.telegram_app = None
    
    def set_telegram_app(self, app):
        """Установка экземпляра Telegram приложения"""
        self.telegram_app = app
    
    async def sync_all_active_tasks(self):
        """Синхронизация всех активных задач с Битрикс24"""
        try:
            # Получаем все задачи с Bitrix24 ID, которые не завершены
            db = get_db_session()
            try:
                active_tasks = db.query(Task).filter(
                    Task.bitrix24_task_id.isnot(None),
                    Task.status.in_([TaskStatus.NEW.value, TaskStatus.IN_PROGRESS.value])
                ).all()
                
                logger.info(f"Синхронизируем {len(active_tasks)} активных задач")
                
                for task in active_tasks:
                    await self.sync_single_task(task)
                    # Небольшая пауза между запросами
                    await asyncio.sleep(0.5)
                    
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Ошибка при синхронизации задач: {e}")
    
    async def sync_single_task(self, task: Task):
        """Синхронизация одной задачи с Битрикс24"""
        try:
            # Получаем актуальную информацию о задаче из Битрикс24
            bitrix_task = bitrix24_api.get_task(task.bitrix24_task_id)
            
            if not bitrix_task:
                logger.warning(f"Задача {task.bitrix24_task_id} не найдена в Битрикс24 - возможно удалена")
                # Помечаем задачу как удаленную и уведомляем чат
                await self.handle_deleted_task(task)
                return
            
            # Получаем статус из Битрикс24
            bitrix_status = bitrix_task.get("status")
            
            # Маппинг статусов Битрикс24 на наши статусы
            status_map = {
                "1": TaskStatus.NEW,        # Новая
                "2": TaskStatus.NEW,        # Ждет выполнения  
                "3": TaskStatus.IN_PROGRESS, # Выполняется
                "4": TaskStatus.CANCELLED,   # Отложена
                "5": TaskStatus.COMPLETED,   # Завершена
                "6": TaskStatus.COMPLETED,   # Закрыта
                "7": TaskStatus.CANCELLED,   # Отклонена
            }
            
            mapped_status = status_map.get(bitrix_status)
            if not mapped_status:
                logger.warning(f"Неизвестный статус Битрикс24: {bitrix_status}")
                return
            
            # Проверяем, изменился ли статус
            if task.status != mapped_status.value:
                old_status = task.status
                
                # Обновляем статус в нашей БД
                self.task_service.update_task_status(task.id, mapped_status)
                
                logger.info(f"Синхронизирован статус задачи #{task.id}: {old_status} → {mapped_status.value}")
                
                # Отправляем уведомление пользователю
                await self.send_sync_notification(task, old_status, mapped_status.value)
                
                # Отправляем уведомление в исходный чат
                await self.send_chat_sync_notification(task, old_status, mapped_status.value)
                
        except Exception as e:
            logger.error(f"Ошибка синхронизации задачи #{task.id}: {e}")
    
    async def send_sync_notification(self, task: Task, old_status: str, new_status: str):
        """Отправка уведомления о синхронизации статуса"""
        if not self.telegram_app:
            logger.warning("Telegram приложение не установлено для отправки уведомлений")
            return
        
        status_names = {
            "new": "🆕 Новая",
            "in_progress": "⏳ В работе",
            "completed": "✅ Завершена",
            "cancelled": "❌ Отменена"
        }
        
        message = f"""
🔄 **Статус задачи обновлен из Битрикс24**

📝 **Задача #{task.id}:** {task.title}
📊 **Статус изменен:** {status_names.get(old_status, old_status)} → {status_names.get(new_status, new_status)}
🔗 **Bitrix24 ID:** {task.bitrix24_task_id}
⏰ **Время синхронизации:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

Изменение было внесено в Битрикс24 и автоматически синхронизировано с ботом.
        """
        
        try:
            await self.telegram_app.bot.send_message(
                chat_id=int(task.telegram_user_id),
                text=message,
                parse_mode="Markdown"
            )
            logger.info(f"Отправлено уведомление о синхронизации пользователю {task.telegram_user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о синхронизации: {e}")
    
    async def send_chat_sync_notification(self, task: Task, old_status: str, new_status: str):
        """Отправка уведомления в исходный чат об изменении статуса из Битрикс24"""
        if not self.telegram_app:
            logger.warning("Telegram приложение не установлено для отправки уведомлений в чат")
            return
        
        status_names = {
            "new": "🆕 Новая",
            "in_progress": "⏳ В работе",
            "completed": "✅ Завершена",
            "cancelled": "❌ Отменена"
        }
        
        # Определяем иконку и текст в зависимости от статуса
        if new_status == "completed":
            icon = "🎉"
            action_text = "завершена в Битрикс24"
            additional_info = "\n🏆 **Задача выполнена! Отличная работа команды.**"
        elif new_status == "in_progress":
            icon = "🚀"
            action_text = "взята в работу в Битрикс24"
            additional_info = "\n⚡ **Задача в процессе выполнения.**"
        elif new_status == "cancelled":
            icon = "❌"
            action_text = "отменена в Битрикс24"
            additional_info = "\n💭 **Задача отменена или не актуальна.**"
        else:
            icon = "🔄"
            action_text = "обновлена в Битрикс24"
            additional_info = ""
        
        message = f"""
{icon} **Задача {action_text}!**

📝 **Задача #{task.id}:** {task.title[:100]}
📊 **Статус изменен:** {status_names.get(old_status, old_status)} → {status_names.get(new_status, new_status)}
🔗 **Bitrix24 ID:** {task.bitrix24_task_id}
⏰ **Обновлено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}{additional_info}

🔄 *Изменение было внесено в Битрикс24 и автоматически синхронизировано.*
        """
        
        try:
            await self.telegram_app.bot.send_message(
                chat_id=int(task.telegram_chat_id),
                text=message,
                parse_mode="Markdown",
                reply_to_message_id=task.telegram_message_id
            )
            logger.info(f"Отправлено уведомление о синхронизации в чат {task.telegram_chat_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления синхронизации в чат: {e}")
    
    async def start_periodic_sync(self, interval_minutes: int = 5):
        """Запуск периодической синхронизации"""
        logger.info(f"Запуск периодической синхронизации каждые {interval_minutes} минут")
        
        while True:
            try:
                await self.sync_all_active_tasks()
                await asyncio.sleep(interval_minutes * 60)  # Конвертируем минуты в секунды
                
            except Exception as e:
                logger.error(f"Ошибка в периодической синхронизации: {e}")
                await asyncio.sleep(60)  # Пауза 1 минута при ошибке
    
    async def handle_deleted_task(self, task: Task):
        """Обработка удаленной задачи из Битрикс24"""
        try:
            # Помечаем задачу как удаленную в нашей БД
            db = get_db_session()
            try:
                task.status = TaskStatus.CANCELLED.value
                db.commit()
                
                logger.info(f"Задача #{task.id} помечена как отмененная (удалена в Битрикс24)")
                
                # Отправляем уведомление в исходный чат
                await self.send_deletion_notification_to_chat(task)
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Ошибка при обработке удаленной задачи: {e}")
    
    async def send_deletion_notification_to_chat(self, task: Task):
        """Отправка уведомления об удалении задачи в исходный чат"""
        if not self.telegram_app:
            logger.warning("Telegram приложение не установлено для отправки уведомлений")
            return
        
        try:
            notification_message = f"""
🗑️ **Задача удалена из Битрикс24**

📝 **Задача #{task.id}:** {task.title[:100]}
❌ **Статус:** Отменена (удалена в системе)
🔗 **Bitrix24 ID:** {task.bitrix24_task_id}
⏰ **Обновлено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

💭 **Задача была удалена из Битрикс24 и автоматически отменена в боте.**
            """
            
            await self.telegram_app.bot.send_message(
                chat_id=int(task.telegram_chat_id),
                text=notification_message,
                parse_mode="Markdown",
                reply_to_message_id=task.telegram_message_id
            )
            
            logger.info(f"Отправлено уведомление об удалении задачи #{task.id} в чат {task.telegram_chat_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об удалении в чат: {e}")


# Создаем глобальный экземпляр сервиса
status_sync_service = StatusSyncService()
