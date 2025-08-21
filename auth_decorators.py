"""
Декораторы для проверки прав доступа
"""
import functools
from typing import Callable, Any
from telegram import Update
from telegram.ext import ContextTypes

from models import UserRole
from user_management_service import user_management
import logging

logger = logging.getLogger(__name__)


def require_role(required_role: UserRole):
    """Декоратор для проверки роли пользователя"""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = str(update.effective_user.id)
            telegram_user = update.effective_user
            
            # Получаем или создаем пользователя
            bot_user = user_management.get_or_create_user(telegram_user)
            
            # Первый пользователь автоматически становится админом
            if user_management.is_first_user():
                user_management.set_user_role(user_id, UserRole.ADMIN, "system")
                logger.info(f"Первый пользователь {user_id} автоматически назначен администратором")
            
            # Проверяем права доступа
            user_role = user_management.get_user_role(user_id)
            
            if not user_role:
                await update.message.reply_text(
                    "❌ Доступ запрещен. Обратитесь к администратору для получения прав."
                )
                return
            
            # Админы имеют доступ ко всем командам
            if user_role == UserRole.ADMIN:
                return await func(self, update, context, *args, **kwargs)
            
            # Проверяем соответствие требуемой роли
            if user_role == required_role:
                return await func(self, update, context, *args, **kwargs)
            
            # Доступ запрещен
            role_names = {
                UserRole.ADMIN: "👑 Администратор",
                UserRole.CLIENT: "👤 Клиент"
            }
            
            await update.message.reply_text(
                f"❌ **Недостаточно прав**\n\n"
                f"Ваша роль: {role_names.get(user_role, 'Неизвестно')}\n"
                f"Требуется: {role_names.get(required_role, 'Неизвестно')}\n\n"
                f"Обратитесь к администратору для получения необходимых прав.",
                parse_mode="Markdown"
            )
            
        return wrapper
    return decorator


def admin_only(func: Callable):
    """Декоратор для команд только для администраторов"""
    return require_role(UserRole.ADMIN)(func)


def client_or_admin(func: Callable):
    """Декоратор для команд доступных клиентам и админам"""
    @functools.wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = str(update.effective_user.id)
        telegram_user = update.effective_user
        
        # Получаем или создаем пользователя
        bot_user = user_management.get_or_create_user(telegram_user)
        
        # Первый пользователь автоматически становится админом
        if user_management.is_first_user():
            user_management.set_user_role(user_id, UserRole.ADMIN, "system")
        
        # Проверяем, что пользователь активен
        user_role = user_management.get_user_role(user_id)
        
        if not user_role:
            await update.message.reply_text(
                "❌ Доступ запрещен. Обратитесь к администратору для получения прав."
            )
            return
        
        return await func(self, update, context, *args, **kwargs)
    
    return wrapper


def log_user_action(action_name: str):
    """Декоратор для логирования действий пользователей"""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = str(update.effective_user.id)
            username = update.effective_user.username or update.effective_user.first_name
            
            logger.info(f"👤 Пользователь {username} ({user_id}) выполняет действие: {action_name}")
            
            return await func(self, update, context, *args, **kwargs)
        
        return wrapper
    return decorator
