"""
Основной модуль Telegram бота для интеграции с Битрикс24
"""
import json
import logging
from typing import Optional, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, File
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from telegram.constants import ParseMode
import os
import requests
from datetime import datetime

from config import settings
from models import Task, TaskType, TaskStatus, TaskCreateRequest, UserSession
from database import get_db_session
from bitrix24_api import bitrix24_api
from task_service import TaskService
from status_sync_service import status_sync_service
from user_management_service import user_management
from auth_decorators import admin_only, client_or_admin, log_user_action
from models import UserRole
from project_service import project_service
from employee_service import employee_service
from telegram_bitrix_sync_service import telegram_bitrix_sync

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, settings.log_level.upper())
)
logger = logging.getLogger(__name__)


class SupportBot:
    """Основной класс Telegram бота поддержки"""
    
    # ID Елены Зубатенко - координатор проектов (всегда соисполнитель)
    ELENA_ZUBATENKO_ID = 809
    
    def __init__(self):
        self.task_service = TaskService()

        self.bot_username = None
    
    @client_or_admin
    @log_user_action("start")
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = str(update.effective_user.id)
        telegram_user = update.effective_user
        
        # Получаем роль пользователя
        user_role = user_management.get_user_role(user_id)
        is_admin = user_role == UserRole.ADMIN
        
        welcome_message = f"""
🤖 **Бот поддержки клиентов**

Привет, {telegram_user.first_name}! Ваш Telegram ID: `{user_id}`

🎯 **Как использовать:**
1\\. Упомяните меня в чате с клиентом \\(@{self.bot_username or "supportbot"}\\) после описания проблемы
2\\. Задача автоматически создается в Битрикс24
3\\. Отслеживайте статус через команды

📱 **Ваши команды:**
• `/start` \\- Показать это сообщение
• `/help` \\- Помощь по использованию  
• `/tasks` \\- Мои задачи по проектам
• `/my_role` \\- Информация о роли
        """
        
        if is_admin:
            welcome_message += """

👑 **Команды администратора:**
• `/users` \\- Список пользователей
• `/add_admin` \\- Добавить администратора
• `/remove_admin` \\- Удалить администратора
• `/manage_employees` \\- Управление сотрудниками \\(старая система\\)
• `/sync` \\- Синхронизация с Битрикс24

📱 **Управление связыванием Telegram\\-Битрикс24:**
• `/show_links` \\- Показать все связи
• `/link_telegram` \\- Связать Bitrix24 ID с Telegram ID
• `/unlink_telegram` \\- Удалить связь
• `/sync_bitrix` \\- Синхронизация кеша с Битрикс24
• `/daily_report` \\- Ежедневный отчет
            """
        
        welcome_message += """

🎯 **Типы задач:**
🐛 Баг \\- Ошибка в работе системы
📋 Требование \\- Новая функциональность  
💬 Консультация \\- Вопрос или консультация
        """
        
        await update.message.reply_text(
            welcome_message,
            parse_mode=ParseMode.MARKDOWN
        )
    
    @client_or_admin
    @log_user_action("help")
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
        help_message = """
📖 **Помощь по использованию бота**

**Создание задачи:**
1. В групповом чате опишите проблему
2. Упомяните бота в конце сообщения (@{bot_username})
3. Бот создаст задачу и напишет вам в личку для уточнения типа

**Управление задачами:**
• `/tasks` - Посмотреть активные задачи
• `/task_status <ID> <статус>` - Изменить статус задачи
• `/stats` - Общая статистика задач
• `/analytics [дни]` - Расширенная аналитика за период
• `/my_stats` - Персональная статистика
• `/daily_report [YYYY-MM-DD]` - Ежедневный отчет
• `/sync` - Ручная синхронизация с Битрикс24

**Статусы задач:**
• `new` - Новая задача
• `in_progress` - В работе  
• `completed` - Завершена
• `cancelled` - Отменена

**Примеры:**
• `/task_status 123 in_progress` - Перевести задачу в работу
• `/task_status 123 completed` - Завершить задачу
        """.format(bot_username=self.bot_username or "supportbot")
        
        await update.message.reply_text(
            help_message,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def handle_mention(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка упоминания бота в сообщении"""
        message = update.message
        
        # Отладочная информация
        logger.info(f"Получено сообщение в чате {message.chat.type}: {message.text or 'Медиа файл'}")
        
        # Проверяем, что это групповой чат и бот упомянут
        if message.chat.type in ['group', 'supergroup']:
            bot_username = context.bot.username
            logger.info(f"Имя бота: @{bot_username}")
            
            # Проверяем упоминание в тексте или caption
            text_to_check = message.text or message.caption or ""
            logger.info(f"Текст для проверки: {text_to_check}")
            
            # Проверяем упоминание бота
            mentioned = (f"@{bot_username}" in text_to_check or 
                        any(entity.type == 'mention' and text_to_check[entity.offset:entity.offset + entity.length] == f"@{bot_username}" 
                            for entity in (message.entities or message.caption_entities or [])))
            
            # Проверяем, является ли это reply на сообщение с упоминанием бота
            is_reply_with_bot = False
            reply_original_text = ""
            
            if message.reply_to_message and f"@{bot_username}" in text_to_check:
                is_reply_with_bot = True
                reply_original_text = message.reply_to_message.text or message.reply_to_message.caption or ""
                logger.info(f"Обнаружен reply с ботом на сообщение: {reply_original_text[:100]}")
            
            if mentioned or "@" in text_to_check or is_reply_with_bot:  # Временно принимаем любые @ для отладки
                logger.info(f"Бот упомянут! Создаем задачу... (username: @{bot_username})")
                
                # Извлекаем текст задачи
                if is_reply_with_bot:
                    # Для reply используем оригинальное сообщение как описание задачи
                    task_text = f"Задача по сообщению: {reply_original_text[:200]}"
                    if text_to_check.replace(f"@{bot_username}", "").strip():
                        task_text += f"\n\nКомментарий: {text_to_check.replace(f'@{bot_username}', '').strip()}"
                else:
                    # Обычное упоминание - убираем упоминание бота
                    task_text = text_to_check.replace(f"@{bot_username}", "").strip()
                
                # Если нет текста, но есть медиа - используем заголовок по умолчанию
                if not task_text and (message.photo or message.document or message.video or message.audio):
                    task_text = "Задача с прикрепленными файлами"
                
                if len(task_text) < 5:
                    await message.reply_text(
                        "❌ Слишком короткое описание задачи. Пожалуйста, опишите проблему подробнее."
                    )
                    return
                
                # Создаем расширенное описание с информацией о создателе и чате
                extended_description = await self.create_extended_description(message, task_text)
                
                # Создаем задачу
                try:
                    task_request = TaskCreateRequest(
                        title=task_text[:100] + "..." if len(task_text) > 100 else task_text,
                        description=extended_description,
                        telegram_message_id=message.message_id,
                        telegram_chat_id=str(message.chat_id),
                        telegram_user_id=str(message.from_user.id)
                    )
                    
                    task = self.task_service.create_task(task_request)
                    
                    # Сохраняем файлы если есть
                    await self.save_message_files(message, task.id, context)
                    
                    # Сразу создаем задачу в Битрикс24 и отправляем одно общее уведомление
                    await self.create_bitrix_task_immediately(context, task, message)
                    
                except Exception as e:
                    logger.error(f"Ошибка при создании задачи: {e}")
                    await message.reply_text(
                        "❌ Произошла ошибка при создании задачи. Попробуйте позже."
                    )
    
    async def send_type_clarification(self, context: ContextTypes.DEFAULT_TYPE, task: Task):
        """Отправка сообщения для уточнения типа задачи"""
        keyboard = [
            [
                InlineKeyboardButton("🐛 Баг", callback_data=f"type_{task.id}_bug"),
                InlineKeyboardButton("📋 Требование", callback_data=f"type_{task.id}_requirement")
            ],
            [
                InlineKeyboardButton("💬 Консультация", callback_data=f"type_{task.id}_consultation")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        clarification_message = f"""
🔍 **Уточнение типа задачи #{task.id}**

**Описание:** {task.description[:300]}...

Пожалуйста, выберите тип задачи:

🐛 **Баг** - Ошибка в работе системы
📋 **Требование** - Новая функциональность  
💬 **Консультация** - Вопрос или консультация
        """
        
        try:
            await context.bot.send_message(
                chat_id=int(task.telegram_user_id),
                text=clarification_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Сохраняем состояние пользователя
            self.session_service.create_or_update_session(
                telegram_user_id=task.telegram_user_id,
                current_task_id=task.id,
                state="waiting_type_selection"
            )
            
        except Exception as e:
            logger.error(f"Не удалось отправить личное сообщение пользователю {task.telegram_user_id}: {e}")
    
    async def create_bitrix_task_immediately(self, context: ContextTypes.DEFAULT_TYPE, task: Task, original_message):
        """Немедленное создание задачи в Битрикс24 и отправка единого уведомления"""
        try:
            # Новая логика назначения исполнителя:
            # 1. Если это reply от сотрудника -> исполнитель = этот сотрудник
            # 2. Иначе -> исполнитель = ТехАккаунт (ID 1269)
            
            # Проверяем, есть ли reply_to_message
            if original_message.reply_to_message:
                # Это reply - проверяем, кто отвечает
                replier_user_id = str(original_message.from_user.id)
                replier_bitrix_id = telegram_bitrix_sync.get_bitrix_user_id(replier_user_id)
                
                if replier_bitrix_id:
                    # Reply от сотрудника - назначаем его исполнителем
                    responsible_id = replier_bitrix_id
                    user_info = telegram_bitrix_sync.get_user_info(replier_user_id)
                    user_name = user_info.get('name', f'ID: {replier_bitrix_id}') if user_info else f'ID: {replier_bitrix_id}'
                    executor_text = f"сотрудник {user_name} (ID: {replier_bitrix_id})"
                    logger.info(f"Reply от сотрудника (tgID: {replier_user_id}) - назначаем сотрудника (ID: {responsible_id})")
                else:
                    # Reply от клиента - назначаем ТехАккаунт
                    responsible_id = 1269
                    executor_text = "ТехАккаунт"
                    logger.info(f"Reply от клиента (без tgID) - назначаем ТехАккаунт (ID: 1269)")
            else:
                # Обычное упоминание (не reply) - всегда ТехАккаунт
                responsible_id = 1269
                executor_text = "ТехАккаунт"
                logger.info(f"Обычное упоминание - назначаем ТехАккаунт (ID: 1269)")
            
            # Елена Зубатенко всегда соисполнитель
            elena_id = self.ELENA_ZUBATENKO_ID
            
            # Создаем задачу в Битрикс24 с типом "Требование" по умолчанию
            bitrix_result = bitrix24_api.create_task(
                title=task.title,
                description=task.description,
                task_type=TaskType.REQUIREMENT,  # Тип по умолчанию
                responsible_user_id=responsible_id,
                co_executors=[elena_id]  # Елена Зубатенко всегда соисполнитель
            )
            
            # Обновляем задачу в БД
            bitrix_task_id = None
            if bitrix_result and "task" in bitrix_result:
                bitrix_task_id = bitrix_result["task"]["id"]
                self.task_service.update_bitrix_task_id(task.id, bitrix_task_id)
                
                # Устанавливаем тип по умолчанию
                self.task_service.update_task_type(task.id, TaskType.REQUIREMENT)
                
                logger.info(f"Задача #{task.id} создана в Битрикс24 с ID: {bitrix_task_id}")
            
            # Отправляем ОДНО общее уведомление
            unified_message = f"""
🎯 **Задача создана и отправлена в Битрикс24!**

📝 **Задача #{task.id}:** {task.title[:100]}
👤 **Создатель:** {original_message.from_user.first_name}
🏷️ **Тип:** 📋 Требование (по умолчанию)
👨‍💼 **Исполнитель:** {executor_text}
👥 **Соисполнитель:** Елена Зубатенко (координатор проектов)
🔗 **Bitrix24 ID:** {bitrix_task_id or 'Ошибка создания'}
⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

✅ **Задача готова к выполнению!**
            """
            
            await original_message.reply_text(
                unified_message,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при создании задачи в Битрикс24: {e}")
            await original_message.reply_text(
                "❌ Произошла ошибка при создании задачи в Битрикс24."
            )
    
    async def get_employee_bitrix_id(self, telegram_user_id: str, telegram_chat_id: str = None) -> Optional[int]:
        """Получение Bitrix24 ID сотрудника по Telegram ID через новый сервис синхронизации"""
        try:
            # Используем новый сервис синхронизации для поиска по tgID
            bitrix_id = telegram_bitrix_sync.get_bitrix_user_id(telegram_user_id)
            
            if bitrix_id:
                logger.debug(f"Найден Bitrix ID {bitrix_id} для Telegram ID {telegram_user_id}")
                return bitrix_id
            
            # Fallback: проверяем старые записи в локальной БД
            db = get_db_session()
            try:
                from models import BotUser
                bot_user = db.query(BotUser).filter(
                    BotUser.telegram_user_id == telegram_user_id
                ).first()
                
                if bot_user and bot_user.bitrix24_user_id:
                    logger.debug(f"Найден в локальной БД: Telegram {telegram_user_id} -> Bitrix {bot_user.bitrix24_user_id}")
                    return bot_user.bitrix24_user_id
                
                return None
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Ошибка получения Bitrix24 ID сотрудника: {e}")
            return None
    
    async def send_task_created_notification(self, context: ContextTypes.DEFAULT_TYPE, task: Task, 
                                           task_type: TaskType, bitrix_task_id: Optional[int]):
        """Отправка уведомления в исходный чат о создании задачи в Битрикс24"""
        try:
            type_names = {
                TaskType.BUG: "🐛 Баг",
                TaskType.REQUIREMENT: "📋 Требование", 
                TaskType.CONSULTATION: "💬 Консультация"
            }
            
            notification_message = f"""
🎯 **Задача отправлена в Битрикс24!**

📝 **Задача #{task.id}** успешно создана в системе
🏷️ **Тип:** {type_names[task_type]}
🔗 **Bitrix24 ID:** {bitrix_task_id or 'Создается...'}
⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

✅ Задача назначена ответственному и готова к выполнению.
            """
            
            await context.bot.send_message(
                chat_id=int(task.telegram_chat_id),
                text=notification_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_to_message_id=task.telegram_message_id
            )
            
            logger.info(f"Отправлено уведомление о создании задачи #{task.id} в чат {task.telegram_chat_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о создании задачи в чат: {e}")
    
    async def send_status_change_notification(self, context: ContextTypes.DEFAULT_TYPE, 
                                            task: Task, new_status: TaskStatus):
        """Отправка уведомления в исходный чат об изменении статуса задачи"""
        try:
            status_names = {
                TaskStatus.NEW: "🆕 Новая",
                TaskStatus.IN_PROGRESS: "⏳ В работе",
                TaskStatus.COMPLETED: "✅ Завершена",
                TaskStatus.CANCELLED: "❌ Отменена"
            }
            
            # Определяем иконку и сообщение в зависимости от статуса
            if new_status == TaskStatus.COMPLETED:
                icon = "🎉"
                action_text = "завершена"
                additional_info = "\n🏆 **Отличная работа! Задача выполнена.**"
            elif new_status == TaskStatus.IN_PROGRESS:
                icon = "🚀"
                action_text = "взята в работу"
                additional_info = "\n⚡ **Задача в процессе выполнения.**"
            elif new_status == TaskStatus.CANCELLED:
                icon = "❌"
                action_text = "отменена"
                additional_info = "\n💭 **Задача отменена или не актуальна.**"
            else:
                icon = "🔄"
                action_text = "обновлена"
                additional_info = ""
            
            notification_message = f"""
{icon} **Задача {action_text}!**

📝 **Задача #{task.id}:** {task.title[:100]}
📊 **Новый статус:** {status_names[new_status]}
🔗 **Bitrix24 ID:** {task.bitrix24_task_id or 'N/A'}
⏰ **Обновлено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}{additional_info}
            """
            
            await context.bot.send_message(
                chat_id=int(task.telegram_chat_id),
                text=notification_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_to_message_id=task.telegram_message_id
            )
            
            logger.info(f"Отправлено уведомление об изменении статуса задачи #{task.id} в чат {task.telegram_chat_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении статуса в чат: {e}")
    
    async def handle_type_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора типа задачи"""
        query = update.callback_query
        await query.answer()
        
        # Парсим данные callback
        data_parts = query.data.split("_")
        if len(data_parts) != 3 or data_parts[0] != "type":
            return
        
        task_id = int(data_parts[1])
        task_type = TaskType(data_parts[2])
        
        try:
            # Обновляем задачу
            task = self.task_service.update_task_type(task_id, task_type)
            
            if task:
                # Создаем задачу в Bitrix24 с Еленой как соисполнителем
                elena_id = self.ELENA_ZUBATENKO_ID
                bitrix_result = bitrix24_api.create_task(
                    title=task.title,
                    description=task.description,
                    task_type=task_type,
                    co_executors=[elena_id]
                )
                
                # Обновляем ID задачи в Bitrix24
                if bitrix_result and "task" in bitrix_result:
                    bitrix_task_id = bitrix_result["task"]["id"]
                    self.task_service.update_bitrix_task_id(task_id, bitrix_task_id)
                
                # Отправляем подтверждение
                type_names = {
                    TaskType.BUG: "🐛 Баг",
                    TaskType.REQUIREMENT: "📋 Требование", 
                    TaskType.CONSULTATION: "💬 Консультация"
                }
                
                confirmation_message = f"""
✅ **Тип задачи установлен!**

📝 **Задача #{task.id}**
🏷️ **Тип:** {type_names[task_type]}
🔗 **Bitrix24 ID:** {bitrix_task_id if 'bitrix_task_id' in locals() else 'Создается...'}

Задача создана в Битрикс24 и назначена ответственному.
                """
                
                await query.edit_message_text(
                    confirmation_message,
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Отправляем уведомление в исходный чат
                await self.send_task_created_notification(context, task, task_type, bitrix_task_id if 'bitrix_task_id' in locals() else None)
                
                # Очищаем сессию пользователя
                self.session_service.clear_session(str(query.from_user.id))
                
        except Exception as e:
            logger.error(f"Ошибка при обработке выбора типа: {e}")
            await query.edit_message_text(
                "❌ Произошла ошибка при обработке выбора. Попробуйте позже."
            )
    
    @client_or_admin
    @log_user_action("tasks")
    async def tasks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать проекты пользователя для выбора"""
        user_id = str(update.effective_user.id)
        logger.info(f"🔍 Команда /tasks от пользователя {user_id}")
        
        try:
            # Проверяем роль пользователя
            user_role = user_management.get_user_role(user_id)
            is_admin = user_role == UserRole.ADMIN
            
            # Получаем проекты пользователя
            projects = project_service.get_user_projects(user_id, is_admin)
            
            if not projects:
                await update.message.reply_text(
                    "📂 У вас нет проектов с задачами.\n\n"
                    "Создайте задачу, упомянув бота в групповом чате!"
                )
                return
            
            # Создаем inline клавиатуру с проектами
            keyboard = []
            
            for project in projects:
                # Формируем название кнопки с статистикой
                project_name = project['chat_name'][:25]  # Ограничиваем длину
                
                button_text = f"📁 {project_name} ({project['total_tasks']})"
                
                # Добавляем индикаторы активности
                if project['new_tasks'] > 0:
                    button_text += f" 🆕{project['new_tasks']}"
                if project['in_progress_tasks'] > 0:
                    button_text += f" ⏳{project['in_progress_tasks']}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"project_{project['chat_id']}_0"  # page 0
                    )
                ])
            
            # Добавляем кнопку "Все мои задачи" для быстрого доступа
            keyboard.append([
                InlineKeyboardButton("📋 Все мои задачи", callback_data="all_my_tasks_0")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            role_text = "👑 Администратор" if is_admin else "👤 Клиент"
            
            projects_message = f"""
📂 **Выберите проект** ({role_text})

У вас есть задачи в {len(projects)} проектах:

            """
            
            for project in projects:
                projects_message += f"""
📁 **{project['chat_name']}**
📊 Задач: {project['total_tasks']} (🆕{project['new_tasks']} ⏳{project['in_progress_tasks']} ✅{project['completed_tasks']})
📅 Последняя: {project['last_activity'].strftime('%d.%m.%Y')}

                """
            
            await update.message.reply_text(
                projects_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка в команде /tasks: {e}")
            await update.message.reply_text("❌ Ошибка при получении списка проектов.")
    


    @admin_only
    @log_user_action("my_stats")
    async def my_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать персональную статистику пользователя"""
        try:
            user_id = str(update.effective_user.id)
            user_stats = analytics_service.get_user_statistics(user_id)
            
            if user_stats['total_tasks'] == 0:
                await update.message.reply_text("У вас пока нет созданных задач.")
                return
            
            stats_text = f"""
👤 **Ваша персональная статистика**

**Общая информация:**
📝 Всего задач: {user_stats['total_tasks']}
📅 Первая задача: {user_stats['first_task_date']}
📅 Последняя задача: {user_stats['last_task_date']}
✅ Процент завершения: {user_stats['completion_rate']}%

**По статусам:**
🆕 Новые: {user_stats['by_status'].get('new', 0)}
⏳ В работе: {user_stats['by_status'].get('in_progress', 0)}
✅ Завершенные: {user_stats['by_status'].get('completed', 0)}
❌ Отмененные: {user_stats['by_status'].get('cancelled', 0)}

**По типам:**
🐛 Баги: {user_stats['by_type'].get('bug', 0)}
📋 Требования: {user_stats['by_type'].get('requirement', 0)}
💬 Консультации: {user_stats['by_type'].get('consultation', 0)}
            """
            
            if user_stats['average_resolution_time']:
                stats_text += f"\n⏱️ **Среднее время решения:** {user_stats['average_resolution_time']} ч"
            
            await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка при получении персональной статистики: {e}")
            await update.message.reply_text("❌ Ошибка при получении статистики.")
    
    @admin_only
    @log_user_action("daily_report")
    async def daily_report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать ежедневный отчет"""
        try:
            # Определяем дату (по умолчанию сегодня)
            target_date = None
            if context.args and len(context.args) > 0:
                try:
                    target_date = datetime.strptime(context.args[0], "%Y-%m-%d")
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат даты. Используйте: YYYY-MM-DD"
                    )
                    return
            
            report = analytics_service.generate_daily_report(target_date)
            
            report_text = f"""
📅 **Ежедневный отчет за {report['date']}**

**Активность:**
➕ Создано задач: {report['created_tasks']}
✅ Завершено задач: {report['completed_tasks']}
👥 Новых пользователей: {report['new_users']}
💬 Активных чатов: {report['active_chats']}

**Созданные задачи по типам:**
🐛 Баги: {report['created_by_type'].get('bug', 0)}
📋 Требования: {report['created_by_type'].get('requirement', 0)}
💬 Консультации: {report['created_by_type'].get('consultation', 0)}

**Завершенные задачи по типам:**
🐛 Баги: {report['completed_by_type'].get('bug', 0)}
📋 Требования: {report['completed_by_type'].get('requirement', 0)}
💬 Консультации: {report['completed_by_type'].get('consultation', 0)}

**Интеграция:**
🎯 Подтвержден тип: {report['tasks_with_confirmed_type']} из {report['created_tasks']}
🔗 Отправлено в Bitrix24: {report['tasks_sent_to_bitrix']} из {report['created_tasks']}
            """
            
            await update.message.reply_text(report_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка при генерации ежедневного отчета: {e}")
            await update.message.reply_text("❌ Ошибка при генерации отчета.")
    
    @admin_only
    @log_user_action("sync")
    async def sync_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ручная синхронизация статусов задач с Битрикс24"""
        try:
            await update.message.reply_text("🔄 Начинаю синхронизацию с Битрикс24...")
            
            # Настраиваем сервис синхронизации
            status_sync_service.set_telegram_app(context.application)
            
            # Запускаем синхронизацию
            await status_sync_service.sync_all_active_tasks()
            
            await update.message.reply_text(
                "✅ **Синхронизация завершена!**\n\n"
                "Все активные задачи проверены и обновлены в соответствии со статусами в Битрикс24.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при ручной синхронизации: {e}")
            await update.message.reply_text("❌ Ошибка при синхронизации с Битрикс24.")
    
    @admin_only
    @log_user_action("add_admin")
    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Добавление администратора"""
        if len(context.args) != 1:
            await update.message.reply_text(
                "❌ Неверный формат команды.\n"
                "Используйте: `/add_admin <ID пользователя>`\n\n"
                "Получить ID можно из команды `/users`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            target_user_id = context.args[0]
            admin_user_id = str(update.effective_user.id)
            
            # Проверяем, существует ли пользователь
            target_role = user_management.get_user_role(target_user_id)
            if not target_role:
                await update.message.reply_text("❌ Пользователь не найден в системе.")
                return
            
            if target_role == UserRole.ADMIN:
                await update.message.reply_text("ℹ️ Пользователь уже является администратором.")
                return
            
            # Назначаем роль администратора
            success = user_management.set_user_role(target_user_id, UserRole.ADMIN, admin_user_id)
            
            if success:
                await update.message.reply_text(
                    f"✅ **Пользователь назначен администратором**\n\n"
                    f"🆔 **ID:** {target_user_id}\n"
                    f"👑 **Новая роль:** Администратор\n"
                    f"⏰ **Назначено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text("❌ Ошибка при назначении роли администратора.")
                
        except Exception as e:
            logger.error(f"Ошибка при добавлении администратора: {e}")
            await update.message.reply_text("❌ Ошибка при выполнении команды.")
    
    @admin_only
    @log_user_action("remove_admin")
    async def remove_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Удаление администратора"""
        if len(context.args) != 1:
            await update.message.reply_text(
                "❌ Неверный формат команды.\n"
                "Используйте: `/remove_admin <ID пользователя>`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            target_user_id = context.args[0]
            admin_user_id = str(update.effective_user.id)
            
            # Проверяем, что пользователь не пытается удалить сам себя
            if target_user_id == admin_user_id:
                await update.message.reply_text("❌ Вы не можете удалить права администратора у самого себя.")
                return
            
            # Проверяем, является ли цель администратором
            target_role = user_management.get_user_role(target_user_id)
            if target_role != UserRole.ADMIN:
                await update.message.reply_text("❌ Пользователь не является администратором.")
                return
            
            # Понижаем до клиента
            success = user_management.set_user_role(target_user_id, UserRole.CLIENT, admin_user_id)
            
            if success:
                await update.message.reply_text(
                    f"✅ **Права администратора отозваны**\n\n"
                    f"🆔 **ID:** {target_user_id}\n"
                    f"👤 **Новая роль:** Клиент\n"
                    f"⏰ **Изменено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await update.message.reply_text("❌ Ошибка при изменении роли.")
                
        except Exception as e:
            logger.error(f"Ошибка при удалении администратора: {e}")
            await update.message.reply_text("❌ Ошибка при выполнении команды.")
    
    @admin_only
    @log_user_action("users")
    async def users_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать всех пользователей (только для админов)"""
        try:
            users = user_management.get_all_users()
            
            if not users:
                await update.message.reply_text("👥 Пользователи не найдены.")
                return
            
            users_text = "👥 **Все пользователи системы:**\n\n"
            
            for user in users:
                role_emoji = "👑" if user.role == UserRole.ADMIN.value else "👤"
                role_name = "Администратор" if user.role == UserRole.ADMIN.value else "Клиент"
                
                username = f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}".strip()
                
                users_text += f"""
{role_emoji} **{username}**
🆔 ID: `{user.telegram_user_id}`
🏷️ Роль: {role_name}
📅 Добавлен: {user.created_at.strftime('%d.%m.%Y %H:%M')}
                """
                
                if user.added_by:
                    users_text += f"👤 Добавил: {user.added_by}\n"
                
                users_text += "\n---\n"
            
            users_text += f"\n📊 **Всего пользователей:** {len(users)}"
            
            await update.message.reply_text(users_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка при получении списка пользователей: {e}")
            await update.message.reply_text("❌ Ошибка при получении списка пользователей.")
    
    @client_or_admin
    @log_user_action("my_role")
    async def my_role_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать информацию о своей роли"""
        try:
            user_id = str(update.effective_user.id)
            user_stats = user_management.get_user_stats(user_id)
            
            if not user_stats:
                await update.message.reply_text("❌ Информация о пользователе не найдена.")
                return
            
            user = user_stats["user"]
            role_emoji = "👑" if user.role == UserRole.ADMIN.value else "👤"
            role_name = "Администратор" if user.role == UserRole.ADMIN.value else "Клиент"
            
            username_display = f"@{user.username}" if user.username else "Не указан"
            # Экранируем специальные символы для Markdown
            username_display = username_display.replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("]", "\\]")
            
            info_text = f"""
{role_emoji} **Информация о вашем профиле**

👤 **Имя:** {user.first_name} {user.last_name or ''}
🆔 **ID:** `{user.telegram_user_id}`
📱 **Username:** {username_display}
🏷️ **Роль:** {role_name}
📅 **Регистрация:** {user.created_at.strftime('%d.%m.%Y %H:%M')}

📊 **Статистика:**
📝 Всего задач: {user_stats['total_tasks']}
✅ Завершено: {user_stats['completed_tasks']}
📈 Процент завершения: {user_stats['completion_rate']}%
            """
            
            if user.role == UserRole.ADMIN.value:
                info_text += f"\n👑 **Права администратора:**\n"
                info_text += f"• Просмотр всех задач\n"
                info_text += f"• Управление пользователями\n"
                info_text += f"• Доступ к аналитике\n"
                info_text += f"• Синхронизация с Битрикс24"
            
            await update.message.reply_text(info_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка при получении информации о роли: {e}")
            await update.message.reply_text("❌ Ошибка при получении информации.")
    
    @admin_only
    @log_user_action("add_employee")
    async def add_employee_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Интерактивное добавление сотрудника в проект"""
        try:
            # Получаем все проекты (чаты) где есть задачи
            user_id = str(update.effective_user.id)
            projects = project_service.get_user_projects(user_id, is_admin=True)
            
            if not projects:
                await update.message.reply_text(
                    "📂 Нет проектов для управления сотрудниками.\n\n"
                    "Сначала создайте задачи в групповых чатах."
                )
                return
            
            # Создаем клавиатуру с проектами
            keyboard = []
            
            for project in projects:
                project_name = project['chat_name'][:30]
                employees_count = len(employee_service.get_chat_employees(project['chat_id']))
                
                button_text = f"📁 {project_name} ({employees_count} сотр.)"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"add_emp_project_{project['chat_id']}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            message_text = """
👥 **Добавление сотрудника в проект**

Выберите проект для добавления сотрудника:

            """
            
            for project in projects:
                employees_count = len(employee_service.get_chat_employees(project['chat_id']))
                message_text += f"📁 **{project['chat_name']}** - {employees_count} сотрудников\n"
            
            await update.message.reply_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка в команде add_employee: {e}")
            await update.message.reply_text("❌ Ошибка при загрузке проектов.")
    
    @admin_only
    @log_user_action("chat_employees")
    async def chat_employees_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать сотрудников чата"""
        if len(context.args) != 1:
            await update.message.reply_text(
                "❌ Неверный формат команды.\n"
                "Используйте: `/chat_employees <ID чата>`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            chat_id = context.args[0]
            employees = employee_service.get_chat_employees(chat_id)
            
            if not employees:
                await update.message.reply_text(f"👥 В чате `{chat_id}` нет зарегистрированных сотрудников.")
                return
            
            employees_text = f"👥 **Сотрудники чата** `{chat_id}`\n\n"
            
            for employee in employees:
                employees_text += f"""
👤 **Пользователь:** `{employee.telegram_user_id}`
🔗 **Bitrix24 ID:** {employee.bitrix24_user_id or 'Не указан'}
📅 **Добавлен:** {employee.added_at.strftime('%d.%m.%Y %H:%M')}
👤 **Добавил:** {employee.added_by or 'Система'}

                """
            
            employees_text += f"📊 **Всего сотрудников:** {len(employees)}"
            
            await update.message.reply_text(employees_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка при получении сотрудников чата: {e}")
            await update.message.reply_text("❌ Ошибка при получении списка сотрудников.")
    
    @admin_only
    @log_user_action("manage_employees")
    async def manage_employees_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Интерактивное управление сотрудниками"""
        try:
            # Получаем все проекты
            user_id = str(update.effective_user.id)
            projects = project_service.get_user_projects(user_id, is_admin=True)
            
            if not projects:
                await update.message.reply_text(
                    "📂 Нет проектов для управления сотрудниками."
                )
                return
            
            # Создаем клавиатуру с проектами
            keyboard = []
            
            for project in projects:
                project_name = project['chat_name'][:25]
                employees_count = len(employee_service.get_chat_employees(project['chat_id']))
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📁 {project_name} ({employees_count})",
                        callback_data=f"manage_emp_{project['chat_id']}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "👥 **Управление сотрудниками**\n\n"
                "Выберите проект для управления сотрудниками:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка в команде manage_employees: {e}")
            await update.message.reply_text("❌ Ошибка при загрузке проектов.")
    
    async def handle_chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка добавления/удаления участников чата"""
        try:
            chat_member_update = update.chat_member
            
            if not chat_member_update:
                return
            
            chat = chat_member_update.chat
            user = chat_member_update.new_chat_member.user
            old_status = chat_member_update.old_chat_member.status
            new_status = chat_member_update.new_chat_member.status
            
            # Проверяем, добавили ли бота в чат
            if user.id == context.bot.id and new_status in ['member', 'administrator']:
                await self.handle_bot_added_to_chat(context, chat)
            
            # Проверяем добавление/удаление обычных участников
            elif new_status in ['member', 'administrator'] and old_status in ['left', 'kicked']:
                # Пользователь добавлен в чат
                logger.info(f"Пользователь {user.id} добавлен в чат {chat.id}")
                
            elif new_status in ['left', 'kicked'] and old_status in ['member', 'administrator']:
                # Пользователь удален из чата
                logger.info(f"Пользователь {user.id} удален из чата {chat.id}")
                # Деактивируем сотрудника если был
                employee_service.remove_employee_from_chat(str(chat.id), str(user.id), "chat_leave")
                
        except Exception as e:
            logger.error(f"Ошибка обработки изменения участников чата: {e}")
    
    async def handle_bot_added_to_chat(self, context: ContextTypes.DEFAULT_TYPE, chat):
        """Обработка добавления бота в новый чат"""
        try:
            welcome_message = f"""
🤖 **Бот поддержки добавлен в проект!**

👋 Привет! Я помогу автоматизировать создание задач в Битрикс24.

📂 **Проект:** {chat.title or 'Без названия'}
🆔 **ID чата:** `{chat.id}`

🔧 **Для настройки проекта администратор должен:**
1. Зарегистрировать сотрудников: `/add_employee {chat.id} <user_id> [bitrix24_id]`
2. Проверить список: `/chat_employees {chat.id}`

📝 **Создание задач:**
• Клиенты: упомяните `@{context.bot.username}` - исполнитель будет назначен автоматически
• Сотрудники: упомяните `@{context.bot.username}` - вы станете исполнителем
• Reply: ответьте на сообщение клиента с `@{context.bot.username}` - вы станете исполнителем

🎯 **Готов к работе!**
            """
            
            await context.bot.send_message(
                chat_id=chat.id,
                text=welcome_message,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Бот добавлен в чат {chat.id} ({chat.title})")
            
        except Exception as e:
            logger.error(f"Ошибка при обработке добавления в чат: {e}")
    
    async def handle_project_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка выбора проекта и показ задач с пагинацией"""
        query = update.callback_query
        await query.answer()
        
        try:
            # Парсим данные callback
            data_parts = query.data.split("_")
            
            if len(data_parts) < 3:
                await query.edit_message_text("❌ Неверные данные запроса.")
                return
            
            user_id = str(query.from_user.id)
            user_role = user_management.get_user_role(user_id)
            is_admin = user_role == UserRole.ADMIN
            
            if data_parts[0] == "project":
                # Просмотр задач конкретного проекта
                chat_id = data_parts[1]
                page = int(data_parts[2])
                
                project_data = project_service.get_project_tasks(chat_id, user_id, is_admin, page)
                await self.show_project_tasks(query, project_data)
                
            elif data_parts[0] == "all" and data_parts[1] == "my":
                # Просмотр всех задач пользователя
                page = int(data_parts[3])
                await self.show_all_user_tasks(query, user_id, is_admin, page)
                
        except Exception as e:
            logger.error(f"Ошибка при обработке выбора проекта: {e}")
            await query.edit_message_text("❌ Ошибка при загрузке задач.")
    
    async def show_project_tasks(self, query, project_data: Dict[str, Any]):
        """Показ задач проекта с пагинацией"""
        try:
            tasks = project_data['tasks']
            page = project_data['page']
            total_pages = project_data['total_pages']
            chat_id = project_data['chat_id']
            
            if not tasks:
                await query.edit_message_text(
                    f"📂 **{project_data['chat_name']}**\n\n"
                    "📝 В этом проекте нет задач."
                )
                return
            
            # Формируем текст с задачами
            tasks_text = f"""
📂 **{project_data['chat_name']}**
📄 Страница {page + 1} из {total_pages} | 📊 Всего: {project_data['total_tasks']}

            """
            
            for task in tasks:
                status_emoji = {
                    TaskStatus.NEW: "🆕",
                    TaskStatus.IN_PROGRESS: "⏳", 
                    TaskStatus.COMPLETED: "✅",
                    TaskStatus.CANCELLED: "❌"
                }
                
                type_emoji = {
                    TaskType.BUG: "🐛",
                    TaskType.REQUIREMENT: "📋",
                    TaskType.CONSULTATION: "💬"
                }
                
                task_status = TaskStatus(task.status) if task.status else None
                task_type = TaskType(task.task_type) if task.task_type else None
                
                tasks_text += f"""
**#{task.id}** {status_emoji.get(task_status, '❓')} {type_emoji.get(task_type, '❓')}
**{task.title[:50]}{'...' if len(task.title) > 50 else ''}**
📅 {task.created_at.strftime('%d.%m %H:%M')}
                """
                
                if task.bitrix24_task_id:
                    tasks_text += f" | 🔗 B24:{task.bitrix24_task_id}"
                
                tasks_text += "\n\n"
            
            # Создаем клавиатуру пагинации
            keyboard = []
            
            # Кнопки навигации
            nav_buttons = []
            if project_data['has_prev']:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Назад", callback_data=f"project_{chat_id}_{page-1}")
                )
            
            nav_buttons.append(
                InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
            )
            
            if project_data['has_next']:
                nav_buttons.append(
                    InlineKeyboardButton("Вперед ➡️", callback_data=f"project_{chat_id}_{page+1}")
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Кнопка возврата к списку проектов
            keyboard.append([
                InlineKeyboardButton("🔙 К списку проектов", callback_data="back_to_projects")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                tasks_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при показе задач проекта: {e}")
            await query.edit_message_text("❌ Ошибка при загрузке задач проекта.")
    
    async def show_all_user_tasks(self, query, user_id: str, is_admin: bool, page: int):
        """Показ всех задач пользователя с пагинацией"""
        try:
            per_page = 5
            offset = page * per_page
            
            # Получаем задачи пользователя
            tasks = self.task_service.get_user_tasks(user_id)
            total_tasks = len(tasks)
            
            # Применяем пагинацию
            paginated_tasks = tasks[offset:offset + per_page]
            total_pages = (total_tasks + per_page - 1) // per_page
            
            if not paginated_tasks:
                await query.edit_message_text("📝 У вас нет задач.")
                return
            
            # Формируем текст
            role_text = "👑 Администратор" if is_admin else "👤 Клиент"
            
            tasks_text = f"""
📋 **Все мои задачи** ({role_text})
📄 Страница {page + 1} из {total_pages} | 📊 Всего: {total_tasks}

            """
            
            for task in paginated_tasks:
                status_emoji = {
                    TaskStatus.NEW: "🆕",
                    TaskStatus.IN_PROGRESS: "⏳", 
                    TaskStatus.COMPLETED: "✅",
                    TaskStatus.CANCELLED: "❌"
                }
                
                type_emoji = {
                    TaskType.BUG: "🐛",
                    TaskType.REQUIREMENT: "📋",
                    TaskType.CONSULTATION: "💬"
                }
                
                task_status = TaskStatus(task.status) if task.status else None
                task_type = TaskType(task.task_type) if task.task_type else None
                
                # Получаем название проекта
                project_name = project_service._get_chat_name_from_task(task)
                
                tasks_text += f"""
**#{task.id}** {status_emoji.get(task_status, '❓')} {type_emoji.get(task_type, '❓')}
**{task.title[:40]}{'...' if len(task.title) > 40 else ''}**
📁 {project_name[:20]}
📅 {task.created_at.strftime('%d.%m %H:%M')}

                """
            
            # Создаем клавиатуру пагинации
            keyboard = []
            
            # Кнопки навигации
            nav_buttons = []
            if page > 0:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Назад", callback_data=f"all_my_tasks_{page-1}")
                )
            
            nav_buttons.append(
                InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
            )
            
            if (page + 1) * per_page < total_tasks:
                nav_buttons.append(
                    InlineKeyboardButton("Вперед ➡️", callback_data=f"all_my_tasks_{page+1}")
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Кнопка возврата к списку проектов
            keyboard.append([
                InlineKeyboardButton("🔙 К списку проектов", callback_data="back_to_projects")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                tasks_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при показе всех задач: {e}")
            await query.edit_message_text("❌ Ошибка при загрузке задач.")
    
    async def handle_back_to_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Возврат к списку проектов"""
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        logger.info(f"🔍 Возврат к проектам от пользователя {user_id}")
        
        try:
            # Проверяем роль пользователя
            user_role = user_management.get_user_role(user_id)
            is_admin = user_role == UserRole.ADMIN
            
            # Получаем проекты пользователя
            projects = project_service.get_user_projects(user_id, is_admin)
            
            if not projects:
                await query.edit_message_text(
                    "📂 У вас нет проектов с задачами.\n\n"
                    "Создайте задачу, упомянув бота в групповом чате!"
                )
                return
            
            # Создаем inline клавиатуру с проектами
            keyboard = []
            
            for project in projects:
                # Формируем название кнопки с статистикой
                project_name = project['chat_name'][:25]  # Ограничиваем длину
                
                button_text = f"📁 {project_name} ({project['total_tasks']})"
                
                # Добавляем индикаторы активности
                if project['new_tasks'] > 0:
                    button_text += f" 🆕{project['new_tasks']}"
                if project['in_progress_tasks'] > 0:
                    button_text += f" ⏳{project['in_progress_tasks']}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"project_{project['chat_id']}_0"  # page 0
                    )
                ])
            
            # Добавляем кнопку "Все мои задачи" для быстрого доступа
            keyboard.append([
                InlineKeyboardButton("📋 Все мои задачи", callback_data="all_my_tasks_0")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            role_text = "👑 Администратор" if is_admin else "👤 Клиент"
            
            projects_message = f"""
📂 **Выберите проект** ({role_text})

У вас есть задачи в {len(projects)} проектах:

            """
            
            for project in projects:
                projects_message += f"""
📁 **{project['chat_name']}**
📊 Задач: {project['total_tasks']} (🆕{project['new_tasks']} ⏳{project['in_progress_tasks']} ✅{project['completed_tasks']})
📅 Последняя: {project['last_activity'].strftime('%d.%m.%Y')}

                """
            
            await query.edit_message_text(
                projects_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при возврате к проектам: {e}")
            await query.edit_message_text("❌ Ошибка при загрузке списка проектов.")
    
    async def handle_employee_management(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка управления сотрудниками"""
        query = update.callback_query
        await query.answer()
        
        try:
            data_parts = query.data.split("_")
            
            if data_parts[0] == "add" and data_parts[1] == "emp" and data_parts[2] == "project":
                # Показать доступных сотрудников для добавления
                chat_id = data_parts[3]
                await self.show_available_employees(query, chat_id)
                
            elif data_parts[0] == "emp" and data_parts[1] == "page":
                # Пагинация списка сотрудников
                chat_id = data_parts[2]
                page = int(data_parts[3])
                await self.show_available_employees(query, chat_id, page)
                
            elif data_parts[0] == "manage" and data_parts[1] == "emp":
                # Показать меню управления сотрудниками проекта
                chat_id = data_parts[2]
                await self.show_project_employee_management(query, chat_id)
                
            elif data_parts[0] == "add" and data_parts[1] == "bitrix" and data_parts[2] == "user":
                # Добавить сотрудника из Битрикс24
                chat_id = data_parts[3]
                bitrix_user_id = data_parts[4]
                await self.add_bitrix_employee_to_chat(query, chat_id, bitrix_user_id, context)
                
            elif data_parts[0] == "remove" and data_parts[1] == "emp":
                # Удалить сотрудника из проекта
                chat_id = data_parts[2]
                user_id = data_parts[3]
                await self.remove_employee_from_project(query, chat_id, user_id)
                
        except Exception as e:
            logger.error(f"Ошибка управления сотрудниками: {e}")
            await query.edit_message_text("❌ Ошибка при обработке запроса.")
    
    async def show_available_employees(self, query, chat_id: str, page: int = 0):
        """Показать доступных сотрудников для добавления с пагинацией"""
        try:
            # Получаем всех пользователей из Битрикс24 (включая неактивных, но работающих)
            all_bitrix_users = bitrix24_api.get_users()
            
            # Фильтруем только тех, у кого есть имя и должность (реальные сотрудники)
            bitrix_users = [
                user for user in all_bitrix_users 
                if user.get("NAME") and user.get("NAME").strip() and 
                   user.get("WORK_POSITION") and user.get("WORK_POSITION").strip()
            ]
            
            # Получаем уже добавленных сотрудников
            existing_employees = employee_service.get_chat_employees(chat_id)
            existing_ids = [emp.bitrix24_user_id for emp in existing_employees if emp.bitrix24_user_id]
            
            # Фильтруем доступных для добавления и проверяем связанные Telegram ID
            available_users = []
            for user in bitrix_users:
                user_id = int(user.get("ID", 0))
                if user_id not in existing_ids:
                    # Проверяем, есть ли уже связанный Telegram ID
                    linked_telegram_id = employee_service.find_linked_telegram_id(user_id)
                    user["linked_telegram_id"] = linked_telegram_id
                    available_users.append(user)
            
            if not available_users:
                await query.edit_message_text(
                    "👥 Все доступные сотрудники уже добавлены в проект."
                )
                return
            
            # Пагинация
            page_size = 5
            total_pages = (len(available_users) + page_size - 1) // page_size
            start_idx = page * page_size
            end_idx = start_idx + page_size
            page_users = available_users[start_idx:end_idx]
            
            # Создаем клавиатуру с сотрудниками
            keyboard = []
            
            for user in page_users:
                user_id = user.get("ID")
                user_name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                user_position = user.get("WORK_POSITION", "")
                linked_telegram_id = user.get("linked_telegram_id")
                
                # Добавляем индикатор связывания
                if linked_telegram_id:
                    button_text = f"🔗 {user_name}"
                    if user_position:
                        button_text += f" ({user_position[:20]})"
                else:
                    button_text = f"👤 {user_name}"
                    if user_position:
                        button_text += f" ({user_position[:20]})"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"add_bitrix_user_{chat_id}_{user_id}"
                    )
                ])
            
            # Кнопки навигации
            nav_buttons = []
            if page > 0:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ Назад", callback_data=f"emp_page_{chat_id}_{page-1}")
                )
            
            nav_buttons.append(
                InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop")
            )
            
            if page < total_pages - 1:
                nav_buttons.append(
                    InlineKeyboardButton("Вперед ➡️", callback_data=f"emp_page_{chat_id}_{page+1}")
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Кнопка возврата
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к проектам", callback_data="back_to_manage_employees")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            project_name = project_service._get_chat_name_from_task_by_chat_id(chat_id)
            
            message_text = f"""
👥 **Добавить сотрудника в проект**
📁 **Проект:** {project_name}
📄 **Страница:** {page + 1} из {total_pages}

**Доступные сотрудники из Битрикс24:**

🔗 - уже связан с Telegram
👤 - требует связывания

            """
            
            for user in page_users:
                user_name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                position = user.get("WORK_POSITION", "")
                linked_status = "🔗 Связан" if user.get("linked_telegram_id") else "👤 Требует связывания"
                message_text += f"{linked_status} **{user_name}** - {position}\n"
            
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа доступных сотрудников: {e}")
            await query.edit_message_text("❌ Ошибка загрузки сотрудников из Битрикс24.")
    
    async def show_project_employee_management(self, query, chat_id: str):
        """Показать меню управления сотрудниками проекта"""
        try:
            employees = employee_service.get_chat_employees(chat_id)
            project_name = project_service._get_chat_name_from_task_by_chat_id(chat_id)
            
            if not employees:
                # Если нет сотрудников, предлагаем добавить
                keyboard = [
                    [InlineKeyboardButton("➕ Добавить сотрудника", callback_data=f"add_emp_project_{chat_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_manage_employees")]
                ]
                
                await query.edit_message_text(
                    f"👥 **Управление сотрудниками**\n"
                    f"📁 **Проект:** {project_name}\n\n"
                    "В проекте нет зарегистрированных сотрудников.",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Создаем клавиатуру с сотрудниками
            keyboard = []
            
            # Кнопка добавления нового сотрудника
            keyboard.append([
                InlineKeyboardButton("➕ Добавить сотрудника", callback_data=f"add_emp_project_{chat_id}")
            ])
            
            # Кнопки для удаления существующих сотрудников
            for employee in employees:
                # Получаем информацию о пользователе из Битрикс24
                bitrix_user_name = "Неизвестен"
                if employee.bitrix24_user_id:
                    # Здесь можно добавить получение имени из Битрикс24
                    bitrix_user_name = f"ID: {employee.bitrix24_user_id}"
                
                button_text = f"🗑️ Удалить {bitrix_user_name}"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"remove_emp_{chat_id}_{employee.telegram_user_id}"
                    )
                ])
            
            # Кнопка возврата
            keyboard.append([
                InlineKeyboardButton("🔙 Назад", callback_data="back_to_manage_employees")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            employees_text = f"""
👥 **Управление сотрудниками**
📁 **Проект:** {project_name}

**Текущие сотрудники ({len(employees)}):**

            """
            
            # Получаем список всех пользователей Битрикс24 для отображения ФИО
            all_bitrix_users = bitrix24_api.get_users()
            
            for employee in employees:
                # Ищем ФИО сотрудника в Битрикс24
                user_name = "Неизвестный"
                if employee.bitrix24_user_id:
                    user_info = next((u for u in all_bitrix_users if u.get("ID") == str(employee.bitrix24_user_id)), None)
                    if user_info:
                        user_name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
                
                employees_text += f"""
👤 **ФИО:** {user_name}
📱 **Telegram ID:** `{employee.telegram_user_id}`
🔗 **Bitrix24 ID:** {employee.bitrix24_user_id or 'Не указан'}
📅 **Добавлен:** {employee.added_at.strftime('%d.%m.%Y')}

                """
            
            await query.edit_message_text(
                employees_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка показа управления сотрудниками: {e}")
            await query.edit_message_text("❌ Ошибка загрузки данных проекта.")
    
    async def add_bitrix_employee_to_chat(self, query, chat_id: str, bitrix_user_id: str, context):
        """Добавление сотрудника из Битрикс24 в чат"""
        try:
            admin_id = str(query.from_user.id)
            
            # Получаем информацию о пользователе из Битрикс24
            all_bitrix_users = bitrix24_api.get_users()
            user_info = next((u for u in all_bitrix_users if u.get("ID") == bitrix_user_id), None)
            
            if not user_info:
                await query.edit_message_text("❌ Пользователь не найден в Битрикс24.")
                return
            
            user_name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
            user_email = user_info.get('EMAIL', '')
            user_position = user_info.get('WORK_POSITION', '')
            
            # Проверяем, есть ли уже связанный Telegram ID
            linked_telegram_id = employee_service.find_linked_telegram_id(int(bitrix_user_id))
            
            if linked_telegram_id:
                # Используем уже связанный Telegram ID
                telegram_id_to_use = linked_telegram_id
                logger.info(f"Используем уже связанный Telegram ID {linked_telegram_id} для Bitrix24 ID {bitrix_user_id}")
            else:
                # Создаем pending запись для последующего связывания
                telegram_id_to_use = f"pending_{bitrix_user_id}"
                logger.info(f"Создаем pending запись для Bitrix24 ID {bitrix_user_id}")
            
            # Добавляем сотрудника
            success = employee_service.add_employee_to_chat(
                chat_id, 
                telegram_id_to_use,
                int(bitrix_user_id), 
                admin_id
            )
            
            if success:
                project_name = project_service._get_chat_name_from_task_by_chat_id(chat_id)
                
                if linked_telegram_id:
                    # Сотрудник уже связан - показываем информацию
                    keyboard = [
                        [InlineKeyboardButton(
                            "🔙 Назад к сотрудникам", 
                            callback_data=f"add_emp_project_{chat_id}"
                        )]
                    ]
                    
                    message_text = f"""
✅ **Сотрудник добавлен в проект**

👤 **Имя:** {user_name}
💼 **Должность:** {user_position}
🔗 **Bitrix24 ID:** {bitrix_user_id}
📱 **Telegram ID:** `{linked_telegram_id}`
📁 **Проект:** {project_name}
⏰ **Добавлено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}

✅ **Сотрудник готов к работе!** Telegram ID уже связан.
                    """
                    
                else:
                    # Сразу запрашиваем Telegram ID
                    # Сохраняем данные в сессии для обработки ответа
                    context.user_data['pending_employee'] = {
                        'chat_id': chat_id,
                        'bitrix_id': bitrix_user_id,
                        'user_name': user_name,
                        'user_position': user_position,
                        'user_email': user_email,
                        'project_name': project_name
                    }
                    
                    keyboard = [
                        [InlineKeyboardButton(
                            "❌ Отменить", 
                            callback_data=f"add_emp_project_{chat_id}"
                        )]
                    ]
                    
                    message_text = f"""
🔗 **Связывание с Telegram**

👤 **Сотрудник:** {user_name}
💼 **Должность:** {user_position}
📧 **Email:** {user_email}
🆔 **Bitrix24 ID:** {bitrix_user_id}
📁 **Проект:** {project_name}

📝 **Введите Telegram ID сотрудника:**

📱 **Формат:** Просто ID пользователя (например: 608167496)
🔍 **Как найти ID:** Сотрудник может получить свой ID, написав боту /start

⏳ **Ожидаю ввод Telegram ID...**
                    """
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text("❌ Сотрудник уже добавлен в проект.")
                
        except Exception as e:
            logger.error(f"Ошибка добавления сотрудника из Битрикс24: {e}")
            await query.edit_message_text("❌ Ошибка при добавлении сотрудника.")
    
    async def remove_employee_from_project(self, query, chat_id: str, user_id: str):
        """Удаление сотрудника из проекта"""
        try:
            admin_id = str(query.from_user.id)
            
            logger.info(f"🗑️ Попытка удаления сотрудника {user_id} из чата {chat_id}")
            
            # Получаем информацию о сотруднике перед удалением
            employee = None
            db = get_db_session()
            try:
                from models import ChatEmployee
                employee = db.query(ChatEmployee).filter(
                    ChatEmployee.telegram_chat_id == chat_id,
                    ChatEmployee.telegram_user_id == user_id,
                    ChatEmployee.is_active == True
                ).first()
                
                if not employee:
                    await query.edit_message_text("❌ Сотрудник не найден в проекте.")
                    return
                
            finally:
                db.close()
            
            success = employee_service.remove_employee_from_chat(chat_id, user_id, admin_id)
            
            if success:
                project_name = project_service._get_chat_name_from_task_by_chat_id(chat_id)
                
                # Получаем имя сотрудника из Битрикс24 если есть ID
                employee_name = f"ID: {user_id}"
                if employee and employee.bitrix24_user_id:
                    bitrix_users = bitrix24_api.get_users()
                    user_info = next((u for u in bitrix_users if u.get("ID") == str(employee.bitrix24_user_id)), None)
                    if user_info:
                        employee_name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
                
                # Создаем клавиатуру для возврата к управлению
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад к управлению", callback_data=f"manage_emp_{chat_id}")],
                    [InlineKeyboardButton("🏠 К списку проектов", callback_data="back_to_manage_employees")]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"✅ **Сотрудник удален из проекта**\n\n"
                    f"👤 **Сотрудник:** {employee_name}\n"
                    f"🆔 **Telegram ID:** `{user_id}`\n"
                    f"📁 **Проект:** {project_name}\n"
                    f"⏰ **Удалено:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text("❌ Сотрудник не найден или уже удален.")
                
        except Exception as e:
            logger.error(f"Ошибка удаления сотрудника: {e}")
            await query.edit_message_text("❌ Ошибка при удалении сотрудника.")
    

    
    async def handle_manual_telegram_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ручного связывания Telegram ID"""
        query = update.callback_query
        await query.answer()
        
        try:
            data_parts = query.data.split("_")
            chat_id = data_parts[2]
            bitrix_user_id = data_parts[3]
            
            # Просим ввести Telegram ID
            await query.edit_message_text(
                f"🔗 **Связывание с Telegram**\n\n"
                f"Введите Telegram ID сотрудника или попросите сотрудника написать боту /start\n\n"
                f"**Формат:** Просто ID пользователя (например: 608167496)\n"
                f"**Bitrix24 ID:** {bitrix_user_id}",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Сохраняем состояние для ожидания ввода
            self.session_service.create_or_update_session(
                telegram_user_id=str(query.from_user.id),
                state="waiting_telegram_id",
                context={
                    "chat_id": chat_id,
                    "bitrix_user_id": bitrix_user_id
                }
            )
            
        except Exception as e:
            logger.error(f"Ошибка обработки связывания: {e}")
            await query.edit_message_text("❌ Ошибка при связывании.")
    
    async def handle_back_to_manage_employees(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Возврат к списку проектов для управления сотрудниками"""
        query = update.callback_query
        await query.answer()
        
        try:
            # Получаем все проекты
            user_id = str(query.from_user.id)
            projects = project_service.get_user_projects(user_id, is_admin=True)
            
            if not projects:
                await query.edit_message_text(
                    "📂 Нет проектов для управления сотрудниками."
                )
                return
            
            # Создаем клавиатуру с проектами
            keyboard = []
            
            for project in projects:
                project_name = project['chat_name'][:25]
                employees_count = len(employee_service.get_chat_employees(project['chat_id']))
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"📁 {project_name} ({employees_count})",
                        callback_data=f"manage_emp_{project['chat_id']}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "👥 **Управление сотрудниками**\n\n"
                "Выберите проект для управления сотрудниками:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка возврата к управлению сотрудниками: {e}")
            await query.edit_message_text("❌ Ошибка при загрузке проектов.")
    
    async def handle_employee_telegram_id_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода Telegram ID для добавляемого сотрудника"""
        message = update.message
        user_id = str(message.from_user.id)
        
        # Проверяем, что пользователь в процессе добавления сотрудника
        if 'pending_employee' not in context.user_data:
            return
        
        employee_data = context.user_data['pending_employee']
        
        try:
            # Парсим введенный Telegram ID
            telegram_id = message.text.strip()
            
            # Базовая валидация (должен быть числом)
            if not telegram_id.isdigit():
                await message.reply_text(
                    "❌ **Неверный формат!**\n\n"
                    "📱 Telegram ID должен быть числом (например: 608167496)\n"
                    "🔄 Попробуйте еще раз:",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Проверяем, что этот Telegram ID не используется другим Bitrix ID
            existing_bitrix_id = employee_service.get_bitrix_id_by_telegram_id(telegram_id)
            if existing_bitrix_id and str(existing_bitrix_id) != str(employee_data['bitrix_id']):
                await message.reply_text(
                    f"❌ **Этот Telegram ID уже связан!**\n\n"
                    f"📱 ID `{telegram_id}` уже связан с Bitrix24 ID: {existing_bitrix_id}\n"
                    f"🔄 Введите другой Telegram ID:",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Обновляем глобальную связь
            employee_service.update_global_user_profile(telegram_id, int(employee_data['bitrix_id']))
            
            # Обновляем pending запись в текущем чате
            success = employee_service.update_employee_telegram_id(
                employee_data['chat_id'], 
                f"pending_{employee_data['bitrix_id']}", 
                telegram_id
            )
            
            if success:
                await message.reply_text(
                    f"✅ **Сотрудник успешно добавлен в проект!**\n\n"
                    f"👤 **Имя:** {employee_data['user_name']}\n"
                    f"💼 **Должность:** {employee_data.get('user_position', 'Не указана')}\n"
                    f"📧 **Email:** {employee_data.get('user_email', 'Не указан')}\n"
                    f"🆔 **Bitrix24 ID:** {employee_data['bitrix_id']}\n"
                    f"📱 **Telegram ID:** `{telegram_id}`\n"
                    f"📁 **Проект:** {employee_data.get('project_name', 'Неизвестный')}\n\n"
                    f"🎯 **Теперь этот сотрудник:**\n"
                    f"• Может создавать задачи через бота\n"
                    f"• Автоматически добавится во все будущие проекты\n"
                    f"• Задачи будут назначаться на него",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await message.reply_text("❌ Ошибка при обновлении данных.")
            
            # Очищаем сессию
            del context.user_data['pending_employee']
            
        except Exception as e:
            logger.error(f"Ошибка обработки добавления сотрудника: {e}")
            await message.reply_text("❌ Ошибка при добавлении сотрудника.")
            # Очищаем сессию при ошибке
            if 'pending_employee' in context.user_data:
                del context.user_data['pending_employee']
    
    @admin_only
    @log_user_action("link_telegram")
    async def link_telegram_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Связывание Bitrix24 пользователя с Telegram ID через tgID"""
        if len(context.args) != 2:
            await update.message.reply_text(
                "❌ Неверный формат команды.\n"
                "Используйте: `/link_telegram <Bitrix24_ID> <Telegram_ID>`\n\n"
                "Например: `/link_telegram 123 608167496`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            bitrix_user_id = int(context.args[0])
            telegram_id = context.args[1]
            
            # Проверяем, что Telegram ID является числом
            if not telegram_id.isdigit():
                await update.message.reply_text("❌ Telegram ID должен быть числом.")
                return
            
            # Проверяем, существует ли пользователь в Bitrix24
            all_users = bitrix24_api.get_users()
            bitrix_user = next((u for u in all_users if u.get("ID") == str(bitrix_user_id)), None)
            
            if not bitrix_user:
                await update.message.reply_text(f"❌ Пользователь с Bitrix24 ID {bitrix_user_id} не найден.")
                return
            
            # Проверяем, не связан ли уже этот Telegram ID с другим пользователем
            existing_bitrix_id = telegram_bitrix_sync.get_bitrix_user_id(telegram_id)
            if existing_bitrix_id and existing_bitrix_id != bitrix_user_id:
                await update.message.reply_text(
                    f"❌ Telegram ID {telegram_id} уже связан с Bitrix24 ID {existing_bitrix_id}.\n"
                    "Сначала удалите существующую связь командой `/unlink_telegram`.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
            
            # Выполняем связывание
            success = telegram_bitrix_sync.add_telegram_link(bitrix_user_id, telegram_id)
            
            if success:
                user_name = f"{bitrix_user.get('NAME', '')} {bitrix_user.get('LAST_NAME', '')}".strip()
                user_position = bitrix_user.get('WORK_POSITION', '')
                
                await update.message.reply_text(
                    f"✅ **Связывание выполнено успешно!**\n\n"
                    f"👤 **Сотрудник:** {user_name}\n"
                    f"💼 **Должность:** {user_position}\n"
                    f"🆔 **Bitrix24 ID:** {bitrix_user_id}\n"
                    f"📱 **Telegram ID:** `{telegram_id}`\n\n"
                    f"🎯 **Теперь этот сотрудник:**\n"
                    f"• Автоматически определяется как сотрудник\n"
                    f"• Задачи назначаются на него при создании\n"
                    f"• Не нужно добавлять в проекты вручную",
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Обновляем кеш
                telegram_bitrix_sync.refresh_cache()
                
            else:
                await update.message.reply_text("❌ Ошибка при связывании. Проверьте логи.")
                
        except ValueError:
            await update.message.reply_text("❌ Bitrix24 ID должен быть числом.")
        except Exception as e:
            logger.error(f"Ошибка связывания: {e}")
            await update.message.reply_text("❌ Произошла ошибка при связывании.")
    
    @admin_only
    @log_user_action("unlink_telegram")
    async def unlink_telegram_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Удаление связи Telegram ID с Bitrix24"""
        if len(context.args) != 1:
            await update.message.reply_text(
                "❌ Неверный формат команды.\n"
                "Используйте: `/unlink_telegram <Telegram_ID>`\n\n"
                "Например: `/unlink_telegram 608167496`",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        try:
            telegram_id = context.args[0]
            
            # Проверяем, что связь существует
            user_info = telegram_bitrix_sync.get_user_info(telegram_id)
            if not user_info:
                await update.message.reply_text(f"❌ Связь для Telegram ID {telegram_id} не найдена.")
                return
            
            # Удаляем связь
            success = telegram_bitrix_sync.remove_telegram_link(telegram_id)
            
            if success:
                await update.message.reply_text(
                    f"✅ **Связь удалена успешно!**\n\n"
                    f"👤 **Сотрудник:** {user_info['name']}\n"
                    f"🆔 **Bitrix24 ID:** {user_info['bitrix_id']}\n"
                    f"📱 **Telegram ID:** `{telegram_id}`\n\n"
                    f"⚠️ **Теперь этот пользователь:**\n"
                    f"• Не определяется как сотрудник\n"
                    f"• Задачи будут назначаться на ТехАккаунт",
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Обновляем кеш
                telegram_bitrix_sync.refresh_cache()
                
            else:
                await update.message.reply_text("❌ Ошибка при удалении связи. Проверьте логи.")
                
        except Exception as e:
            logger.error(f"Ошибка удаления связи: {e}")
            await update.message.reply_text("❌ Произошла ошибка при удалении связи.")
    
    @admin_only 
    @log_user_action("show_links")
    async def show_links_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все текущие связи Telegram ID с Bitrix24"""
        try:
            # Получаем все связи
            linked_users = telegram_bitrix_sync.get_all_linked_users()
            
            if not linked_users:
                await update.message.reply_text("📱 Связанные Telegram аккаунты не найдены.")
                return
            
            # Получаем информацию о пользователях из Bitrix24
            all_bitrix_users = bitrix24_api.get_users()
            
            links_text = f"📱 **Связанные Telegram аккаунты** ({len(linked_users)})\n\n"
            
            for telegram_id, bitrix_id in linked_users.items():
                # Ищем пользователя в Bitrix24
                user_info = next((u for u in all_bitrix_users if u.get("ID") == str(bitrix_id)), None)
                
                if user_info:
                    user_name = f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip()
                    user_position = user_info.get('WORK_POSITION', '')
                    active_status = "🟢 Активен" if user_info.get('ACTIVE') == 'Y' else "🔴 Неактивен"
                    
                    links_text += f"""
👤 **{user_name}**
💼 {user_position}
🆔 Bitrix24: {bitrix_id}
📱 Telegram: `{telegram_id}`
📊 Статус: {active_status}

"""
                else:
                    links_text += f"""
❓ **Неизвестный пользователь**
🆔 Bitrix24: {bitrix_id}
📱 Telegram: `{telegram_id}`
⚠️ Не найден в Bitrix24

"""
            
            # Добавляем информацию о пользователях без связи
            unlinked_users = telegram_bitrix_sync.get_unlinked_bitrix_users()
            if unlinked_users:
                links_text += f"\n🔍 **Пользователи без Telegram ID:** {len(unlinked_users)}\n"
                links_text += "Используйте `/link_telegram` для связывания."
            
            await update.message.reply_text(links_text, parse_mode=ParseMode.MARKDOWN)
            
        except Exception as e:
            logger.error(f"Ошибка показа связей: {e}")
            await update.message.reply_text("❌ Ошибка при получении списка связей.")
    
    @admin_only
    @log_user_action("sync_bitrix") 
    async def sync_bitrix_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Синхронизация с Bitrix24 - обновление кеша и связей"""
        try:
            await update.message.reply_text("🔄 Начинаю синхронизацию с Bitrix24...")
            
            # Обновляем кеш связей
            telegram_bitrix_sync.refresh_cache()
            
            # Синхронизируем с локальной БД
            synced_count = telegram_bitrix_sync.sync_with_local_database()
            
            # Получаем статистику
            linked_users = telegram_bitrix_sync.get_all_linked_users()
            unlinked_users = telegram_bitrix_sync.get_unlinked_bitrix_users()
            
            await update.message.reply_text(
                f"✅ **Синхронизация завершена!**\n\n"
                f"📊 **Статистика:**\n"
                f"🔗 Связанных пользователей: {len(linked_users)}\n"
                f"❌ Без Telegram ID: {len(unlinked_users)}\n"
                f"💾 Синхронизировано с локальной БД: {synced_count}\n\n"
                f"💡 Используйте `/show_links` для просмотра всех связей.",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации с Bitrix24: {e}")
            await update.message.reply_text("❌ Ошибка при синхронизации с Bitrix24.")
    
    async def debug_all_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отладочный обработчик всех сообщений"""
        message = update.message
        logger.info(f"🔍 DEBUG: Получено сообщение в чате {message.chat.type} (ID: {message.chat.id})")
        logger.info(f"🔍 DEBUG: Текст: {message.text}")
        logger.info(f"🔍 DEBUG: От пользователя: {message.from_user.id} ({message.from_user.first_name})")
        
        # Не отвечаем на сообщения, просто логируем
    
    async def create_extended_description(self, message: Message, task_text: str) -> str:
        """Создание расширенного описания задачи с информацией о создателе и чате"""
        
        # Получаем информацию о пользователе
        user = message.from_user
        username = f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}".strip()
        user_id = user.id
        
        # Получаем информацию о чате
        chat = message.chat
        chat_name = chat.title or "Личный чат"
        chat_id = chat.id
        
        # Форматируем дату создания
        created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # Создаем расширенное описание
        extended_description = f"""
📋 **Описание задачи:**
{task_text}

👤 **Информация о создателе:**
• Имя: {username}
• ID: {user_id}
• Telegram: {f"@{user.username}" if user.username else "Нет username"}

💬 **Информация о чате:**
• Название: {chat_name}
• ID чата: {chat_id}
• Тип чата: {chat.type}

📅 **Дата создания:** {created_at}

🔗 **Ссылка на сообщение:** https://t.me/c/{str(chat_id)[4:]}/{message.message_id}
        """.strip()
        
        return extended_description
    
    async def save_message_files(self, message: Message, task_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Сохранение файлов из сообщения и прикрепление к задаче в Битрикс24"""
        files_info = []
        
        try:
            # Создаем папку для файлов если не существует
            files_dir = f"task_files/{task_id}"
            os.makedirs(files_dir, exist_ok=True)
            
            # Обрабатываем фотографии
            if message.photo:
                photo = message.photo[-1]  # Берем фото наибольшего размера
                file_info = await self.download_file(context, photo.file_id, files_dir, "photo.jpg")
                if file_info:
                    file_info["type"] = "photo"
                    files_info.append(file_info)
            
            # Обрабатываем документы
            if message.document:
                doc = message.document
                filename = doc.file_name or f"document_{doc.file_id[:8]}"
                file_info = await self.download_file(context, doc.file_id, files_dir, filename)
                if file_info:
                    file_info["type"] = "document"
                    files_info.append(file_info)
            
            # Обрабатываем видео
            if message.video:
                video = message.video
                filename = f"video_{video.file_id[:8]}.mp4"
                file_info = await self.download_file(context, video.file_id, files_dir, filename)
                if file_info:
                    file_info["type"] = "video"
                    files_info.append(file_info)
            
            # Обрабатываем аудио
            if message.audio:
                audio = message.audio
                filename = audio.file_name or f"audio_{audio.file_id[:8]}.mp3"
                file_info = await self.download_file(context, audio.file_id, files_dir, filename)
                if file_info:
                    file_info["type"] = "audio"
                    files_info.append(file_info)
            
            # Обрабатываем голосовые сообщения
            if message.voice:
                voice = message.voice
                filename = f"voice_{voice.file_id[:8]}.ogg"
                file_info = await self.download_file(context, voice.file_id, files_dir, filename)
                if file_info:
                    file_info["type"] = "voice"
                    files_info.append(file_info)
            
            # Если есть файлы, загружаем их в Битрикс24
            if files_info:
                # Получаем задачу из БД для получения bitrix24_task_id
                task = self.task_service.get_task(task_id)
                if task and task.bitrix24_task_id:
                    uploaded_files = []
                    
                    for file_info in files_info:
                        try:
                            # Прикрепляем файл к задаче в Битрикс24 через ссылку Telegram
                            telegram_file_url = file_info.get('telegram_file_url', '')
                            
                            if telegram_file_url:
                                upload_result = bitrix24_api.attach_telegram_file_to_task(
                                    task.bitrix24_task_id, 
                                    file_info,
                                    telegram_file_url
                                )
                                
                                if upload_result.get("success"):
                                    uploaded_files.append(file_info['filename'])
                                    logger.info(f"✅ Файл {file_info['filename']} прикреплен к задаче")
                                else:
                                    logger.warning(f"⚠️ Не удалось прикрепить файл {file_info['filename']}")
                            else:
                                logger.warning(f"⚠️ Нет URL для файла {file_info['filename']}")
                            
                        except Exception as e:
                            logger.error(f"Ошибка прикрепления файла {file_info['filename']}: {e}")
                    
                    # Добавляем общий комментарий о файлах
                    if uploaded_files:
                        files_comment = f"📎 **Загружены файлы из Telegram:**\n" + "\n".join([
                            f"• {filename}" for filename in uploaded_files
                        ])
                    else:
                        files_comment = f"📎 **Файлы из Telegram:**\n" + "\n".join([
                            f"• {info['filename']} ({info['size']} байт) - сохранен локально" 
                            for info in files_info
                        ])
                    
                    try:
                        bitrix24_api.add_comment_to_task(task.bitrix24_task_id, files_comment)
                        logger.info(f"Добавлен комментарий с файлами к задаче {task.bitrix24_task_id}")
                    except Exception as e:
                        logger.error(f"Ошибка добавления комментария о файлах: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка при сохранении файлов: {e}")
    
    async def download_file(self, context: ContextTypes.DEFAULT_TYPE, file_id: str, 
                           files_dir: str, filename: str) -> Optional[Dict[str, Any]]:
        """Скачивание файла из Telegram"""
        try:
            file = await context.bot.get_file(file_id)
            file_path = os.path.join(files_dir, filename)
            
            # Скачиваем файл локально
            await file.download_to_drive(file_path)
            
            # Получаем размер файла
            file_size = os.path.getsize(file_path)
            
            # Формируем прямую ссылку на файл в Telegram
            bot_token = settings.telegram_bot_token
            telegram_file_url = f"https://api.telegram.org/file/bot{bot_token}/{file.file_path}"
            
            logger.info(f"Файл {filename} скачан в {file_path}")
            logger.info(f"Прямая ссылка Telegram: {telegram_file_url}")
            
            return {
                "filename": filename,
                "path": file_path,
                "size": file_size,
                "file_id": file_id,
                "telegram_file_url": telegram_file_url,
                "type": "file"  # Будет переопределено в вызывающем коде
            }
            
        except Exception as e:
            logger.error(f"Ошибка при скачивании файла {file_id}: {e}")
            return None
    
    def setup_handlers(self, application: Application):
        """Настройка обработчиков команд и сообщений"""
        
        # КОМАНДЫ ИМЕЮТ ВЫСШИЙ ПРИОРИТЕТ (group=-1)
        application.add_handler(CommandHandler("start", self.start_command), group=-1)
        application.add_handler(CommandHandler("help", self.help_command), group=-1)
        application.add_handler(CommandHandler("tasks", self.tasks_command), group=-1)

        application.add_handler(CommandHandler("my_stats", self.my_stats_command), group=-1)
        application.add_handler(CommandHandler("daily_report", self.daily_report_command), group=-1)
        application.add_handler(CommandHandler("sync", self.sync_command), group=-1)
        
        # Команды управления пользователями (только для админов)
        application.add_handler(CommandHandler("users", self.users_command), group=-1)
        application.add_handler(CommandHandler("add_admin", self.add_admin_command), group=-1)
        application.add_handler(CommandHandler("remove_admin", self.remove_admin_command), group=-1)
        application.add_handler(CommandHandler("my_role", self.my_role_command), group=-1)
        
        # Команды управления сотрудниками в чатах
        application.add_handler(CommandHandler("add_employee", self.add_employee_command), group=-1)
        application.add_handler(CommandHandler("chat_employees", self.chat_employees_command), group=-1)
        application.add_handler(CommandHandler("manage_employees", self.manage_employees_command), group=-1)
        
        # Команды управления связыванием tgID
        application.add_handler(CommandHandler("link_telegram", self.link_telegram_command), group=-1)
        application.add_handler(CommandHandler("unlink_telegram", self.unlink_telegram_command), group=-1)
        application.add_handler(CommandHandler("show_links", self.show_links_command), group=-1)
        application.add_handler(CommandHandler("sync_bitrix", self.sync_bitrix_command), group=-1)
        
        # Обработка упоминаний в группах (средний приоритет)
        application.add_handler(MessageHandler(
            filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & filters.Regex(r'@\w+'), 
            self.handle_mention
        ), group=0)
        
        # Обработка медиафайлов с упоминанием бота
        application.add_handler(MessageHandler(
            (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE) & 
            (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), 
            self.handle_mention
        ), group=0)
        
        # ВРЕМЕННО: обработка упоминаний в личных сообщениях для теста
        application.add_handler(MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & filters.Regex(r'задача|task'), 
            self.handle_mention
        ), group=1)
        
        # Обработка ввода Telegram ID для связывания сотрудников
        application.add_handler(MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & filters.Regex(r'^\d+$'),
            self.handle_employee_telegram_id_input
        ), group=2)
        

        
        # Временно: отладка всех сообщений (самый низкий приоритет)
        application.add_handler(MessageHandler(
            filters.TEXT,
            self.debug_all_messages
        ), group=10)
        
        # Обработка callback запросов
        application.add_handler(CallbackQueryHandler(
            self.handle_type_selection, 
            pattern="^type_"
        ))
        
        # Обработка выбора проектов и пагинации
        application.add_handler(CallbackQueryHandler(
            self.handle_project_selection,
            pattern="^(project_|all_my_tasks_)"
        ))
        
        # Обработка возврата к списку проектов
        application.add_handler(CallbackQueryHandler(
            self.handle_back_to_projects,
            pattern="^back_to_projects$"
        ))
        
        # Обработка управления сотрудниками
        application.add_handler(CallbackQueryHandler(
            self.handle_employee_management,
            pattern="^(add_emp_project_|manage_emp_|add_bitrix_user_|remove_emp_|emp_page_)"
        ))
        
        # Обработка связывания Telegram ID
        application.add_handler(CallbackQueryHandler(
            self.handle_manual_telegram_link,
            pattern="^link_telegram_"
        ))
        
        # Обработка возврата к управлению сотрудниками
        application.add_handler(CallbackQueryHandler(
            self.handle_back_to_manage_employees,
            pattern="^back_to_manage_employees$"
        ))
        
        # Обработка изменений участников чата
        from telegram.ext import ChatMemberHandler
        application.add_handler(ChatMemberHandler(
            self.handle_chat_member_update,
            ChatMemberHandler.CHAT_MEMBER
        ))
    
    async def post_init(self, application: Application):
        """Инициализация после запуска бота"""
        bot_info = await application.bot.get_me()
        self.bot_username = bot_info.username
        logger.info(f"Бот запущен: @{self.bot_username}")
        
        # Инициализация кеша связей Telegram-Bitrix24
        logger.info("Инициализация кеша связей Telegram-Bitrix24...")
        telegram_bitrix_sync.load_cache()
        logger.info("Кеш связей загружен.")


def create_bot_application() -> Application:
    """Создание и настройка приложения бота"""
    application = Application.builder().token(settings.telegram_bot_token).build()
    
    support_bot = SupportBot()
    support_bot.setup_handlers(application)
    
    # Добавляем post_init
    application.job_queue.run_once(
        lambda context: support_bot.post_init(application), 
        when=1
    )
    
    return application
