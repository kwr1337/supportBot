"""
Сервис для синхронизации Telegram ID с Bitrix24 пользователями по полю tgID
"""
from typing import Optional, Dict, Any, List
import logging
from bitrix24_api import bitrix24_api
from employee_service import employee_service

logger = logging.getLogger(__name__)


class TelegramBitrixSyncService:
    """Сервис для автоматического связывания Telegram пользователей с Bitrix24"""
    
    def __init__(self):
        self._cached_users: Dict[str, int] = {}  # telegram_id -> bitrix_id
        self._cache_loaded = False
    
    def load_cache(self) -> None:
        """Загрузка кеша пользователей с заполненным tgID из Bitrix24"""
        try:
            logger.info("Загружаем кеш пользователей с Telegram ID из Bitrix24...")
            
            users_with_tg = bitrix24_api.get_users_with_telegram_ids()
            self._cached_users.clear()
            
            for user in users_with_tg:
                telegram_id = user.get("TELEGRAM_ID")
                bitrix_id = int(user.get("ID", 0))
                
                if telegram_id and bitrix_id:
                    self._cached_users[str(telegram_id)] = bitrix_id
                    logger.debug(f"Кеширован: Telegram {telegram_id} -> Bitrix {bitrix_id}")
            
            self._cache_loaded = True
            logger.info(f"Загружено {len(self._cached_users)} пользователей с Telegram ID в кеш")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки кеша пользователей: {e}")
            self._cached_users.clear()
            self._cache_loaded = False
    
    def refresh_cache(self) -> None:
        """Принудительное обновление кеша"""
        self._cache_loaded = False
        self.load_cache()
    
    def get_bitrix_user_id(self, telegram_id: str) -> Optional[int]:
        """Получение Bitrix24 ID пользователя по Telegram ID"""
        try:
            # Загружаем кеш если не загружен
            if not self._cache_loaded:
                self.load_cache()
            
            # Проверяем кеш
            if telegram_id in self._cached_users:
                bitrix_id = self._cached_users[telegram_id]
                logger.debug(f"Найден в кеше: Telegram {telegram_id} -> Bitrix {bitrix_id}")
                return bitrix_id
            
            # Если не найдено в кеше, проверяем API Bitrix24 напрямую
            logger.debug(f"Поиск в Bitrix24 API для Telegram ID: {telegram_id}")
            bitrix_id = bitrix24_api.find_bitrix_user_by_telegram(telegram_id)
            
            if bitrix_id:
                # Добавляем в кеш
                self._cached_users[telegram_id] = bitrix_id
                logger.info(f"Найден и добавлен в кеш: Telegram {telegram_id} -> Bitrix {bitrix_id}")
                return bitrix_id
            
            logger.debug(f"Пользователь с Telegram ID {telegram_id} не найден в Bitrix24")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения Bitrix ID для Telegram {telegram_id}: {e}")
            return None
    
    def is_employee(self, telegram_id: str) -> bool:
        """Проверка, является ли пользователь сотрудником (есть ли он в Bitrix24 с tgID)"""
        return self.get_bitrix_user_id(telegram_id) is not None
    
    def add_telegram_link(self, bitrix_user_id: int, telegram_id: str) -> bool:
        """Добавление связи Telegram ID с пользователем Bitrix24"""
        try:
            # Обновляем поле tgID в Bitrix24
            success = bitrix24_api.update_user_telegram_id(bitrix_user_id, telegram_id)
            
            if success:
                # Обновляем кеш
                self._cached_users[telegram_id] = bitrix_user_id
                
                # Также обновляем глобальный профиль в локальной БД
                employee_service.update_global_user_profile(telegram_id, bitrix_user_id)
                
                logger.info(f"Успешно связан Telegram {telegram_id} с Bitrix {bitrix_user_id}")
                return True
            else:
                logger.error(f"Не удалось обновить поле tgID в Bitrix24 для пользователя {bitrix_user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка связывания Telegram {telegram_id} с Bitrix {bitrix_user_id}: {e}")
            return False
    
    def remove_telegram_link(self, telegram_id: str) -> bool:
        """Удаление связи Telegram ID с Bitrix24"""
        try:
            bitrix_id = self.get_bitrix_user_id(telegram_id)
            if not bitrix_id:
                logger.warning(f"Связь для Telegram {telegram_id} не найдена")
                return False
            
            # Очищаем поле в Bitrix24
            success = bitrix24_api.update_user_telegram_id(bitrix_id, "")
            
            if success:
                # Удаляем из кеша
                if telegram_id in self._cached_users:
                    del self._cached_users[telegram_id]
                
                logger.info(f"Удалена связь Telegram {telegram_id} с Bitrix {bitrix_id}")
                return True
            else:
                logger.error(f"Не удалось очистить поле tgID в Bitrix24 для пользователя {bitrix_id}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка удаления связи для Telegram {telegram_id}: {e}")
            return False
    
    def get_all_linked_users(self) -> Dict[str, int]:
        """Получение всех связанных пользователей"""
        if not self._cache_loaded:
            self.load_cache()
        return self._cached_users.copy()
    
    def get_user_info(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Получение полной информации о пользователе из Bitrix24 по Telegram ID"""
        try:
            bitrix_id = self.get_bitrix_user_id(telegram_id)
            if not bitrix_id:
                return None
            
            # Получаем всех пользователей и ищем нужного
            all_users = bitrix24_api.get_users()
            user_info = next((u for u in all_users if u.get("ID") == str(bitrix_id)), None)
            
            if user_info:
                # Нормализуем информацию
                return {
                    "bitrix_id": bitrix_id,
                    "telegram_id": telegram_id,
                    "name": f"{user_info.get('NAME', '')} {user_info.get('LAST_NAME', '')}".strip(),
                    "email": user_info.get('EMAIL', ''),
                    "position": user_info.get('WORK_POSITION', ''),
                    "active": user_info.get('ACTIVE') == 'Y',
                    "full_info": user_info
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка получения информации о пользователе {telegram_id}: {e}")
            return None
    
    def sync_with_local_database(self) -> int:
        """Синхронизация с локальной базой данных - обновление BotUser записей"""
        try:
            if not self._cache_loaded:
                self.load_cache()
            
            synced_count = 0
            
            for telegram_id, bitrix_id in self._cached_users.items():
                try:
                    success = employee_service.update_global_user_profile(telegram_id, bitrix_id)
                    if success:
                        synced_count += 1
                except Exception as e:
                    logger.error(f"Ошибка синхронизации записи {telegram_id}: {e}")
            
            logger.info(f"Синхронизировано {synced_count} записей с локальной БД")
            return synced_count
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации с локальной БД: {e}")
            return 0
    
    def get_unlinked_bitrix_users(self) -> List[Dict[str, Any]]:
        """Получение пользователей Bitrix24 без заполненного tgID"""
        try:
            all_users = bitrix24_api.get_users()
            users_with_tg = bitrix24_api.get_users_with_telegram_ids()
            
            # Получаем ID пользователей с заполненным tgID
            linked_ids = set(user.get("ID") for user in users_with_tg)
            
            # Фильтруем пользователей без tgID
            unlinked_users = []
            for user in all_users:
                user_id = user.get("ID")
                
                # Проверяем что это активный пользователь с именем и должностью
                if (user_id not in linked_ids and 
                    user.get("ACTIVE") == "Y" and
                    user.get("NAME") and user.get("NAME").strip() and
                    user.get("WORK_POSITION") and user.get("WORK_POSITION").strip()):
                    
                    unlinked_users.append({
                        "id": user_id,
                        "name": f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip(),
                        "email": user.get('EMAIL', ''),
                        "position": user.get('WORK_POSITION', ''),
                        "full_info": user
                    })
            
            logger.info(f"Найдено {len(unlinked_users)} пользователей без Telegram ID")
            return unlinked_users
            
        except Exception as e:
            logger.error(f"Ошибка получения пользователей без tgID: {e}")
            return []


# Создаем глобальный экземпляр сервиса
telegram_bitrix_sync = TelegramBitrixSyncService()
