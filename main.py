"""
Основной файл запуска бота поддержки
"""
import asyncio
import logging
from telegram.ext import Application

from config import settings
from database import create_tables
from telegram_bot import create_bot_application
from status_sync_service import status_sync_service

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, settings.log_level.upper())
)
logger = logging.getLogger(__name__)


def main():
    """Основная функция запуска бота"""
    logger.info("Запуск бота поддержки...")
    
    try:
        # Создание таблиц базы данных
        create_tables()
        logger.info("База данных инициализирована")
        
        # Создание и запуск бота
        application = create_bot_application()
        
        # Настраиваем сервис синхронизации
        status_sync_service.set_telegram_app(application)
        
        # Запускаем периодическую синхронизацию в фоне
        async def start_sync():
            await asyncio.sleep(30)  # Ждем 30 секунд после запуска бота
            await status_sync_service.start_periodic_sync(interval_minutes=5)
        
        # Добавляем задачу синхронизации
        application.job_queue.run_once(
            lambda context: asyncio.create_task(start_sync()),
            when=1
        )
        
        logger.info("Бот запущен и готов к работе")
        logger.info("Периодическая синхронизация с Битрикс24 будет запущена через 30 секунд")
        
        application.run_polling(
            allowed_updates=["message", "callback_query", "chat_member"]
        )
        
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        raise


if __name__ == "__main__":
    main()
