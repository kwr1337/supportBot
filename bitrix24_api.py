"""
Интеграция с Bitrix24 REST API
"""
import requests
import json
from typing import Dict, Any, Optional, List
from config import settings
from models import TaskType, TaskStatus
import logging

logger = logging.getLogger(__name__)


class Bitrix24API:
    """Класс для работы с Bitrix24 REST API"""
    
    def __init__(self):
        self.domain = settings.bitrix24_domain
        self.access_token = settings.bitrix24_access_token
        # Для входящих вебхуков используем прямой URL
        if hasattr(settings, 'bitrix24_user_id'):
            self.base_url = f"https://{self.domain}/rest/{settings.bitrix24_user_id}/{self.access_token}/"
        else:
            self.base_url = f"https://{self.domain}/rest/"
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict[str, Any]:
        """Выполнение запроса к Bitrix24 API"""
        url = f"{self.base_url}{endpoint}"
        
        # Для входящих вебхуков токен уже в URL, не нужен в параметрах
        params = data.copy() if data else {}
        
        # Только добавляем auth если НЕ используем входящий вебхук
        if not (hasattr(settings, 'bitrix24_user_id') and settings.bitrix24_user_id):
            params["auth"] = self.access_token
        
        try:
            logger.info(f"🔍 Bitrix24 запрос: {method} {url}")
            logger.info(f"🔍 Параметры: {params}")
            
            if method.upper() == "GET":
                response = requests.get(url, params=params)
            else:
                response = requests.post(url, data=params)
            
            logger.info(f"🔍 Ответ статус: {response.status_code}")
            logger.info(f"🔍 Ответ текст: {response.text[:500]}...")
            
            response.raise_for_status()
            result = response.json()
            
            if "error" in result:
                logger.error(f"Ошибка Bitrix24 API: {result['error']}")
                raise Exception(f"Bitrix24 API Error: {result['error']}")
            
            return result.get("result", {})
            
        except requests.RequestException as e:
            logger.error(f"Ошибка HTTP запроса к Bitrix24: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка декодирования JSON ответа Bitrix24: {e}")
            raise
    
    def create_task(self, title: str, description: str, task_type: TaskType, 
                   responsible_user_id: Optional[int] = None, co_executors: Optional[List[int]] = None) -> Dict[str, Any]:
        """Создание задачи в Bitrix24"""
        
        # Определяем приоритет по типу задачи
        priority = "2"  # Обычный приоритет по умолчанию
        
        if task_type == TaskType.BUG:
            priority = "3"  # Высокий приоритет для багов
        elif task_type == TaskType.REQUIREMENT:
            priority = "1"  # Низкий приоритет для требований
        elif task_type == TaskType.CONSULTATION:
            priority = "2"  # Обычный приоритет для консультаций
        
        # Логика назначения:
        # CREATED_BY (постановщик) - всегда ТехАккаунт (1269)
        # RESPONSIBLE_ID (исполнитель) - как указано в параметре или ТехАккаунт по умолчанию
        creator_id = 1269  # ТехАккаунт всегда постановщик
        responsible_id = responsible_user_id or 1269  # Исполнитель по параметру или ТехАккаунт
        
        task_data = {
            "fields[TITLE]": title,
            "fields[DESCRIPTION]": description,
            "fields[PRIORITY]": priority,
            "fields[CREATED_BY]": creator_id,
            "fields[RESPONSIBLE_ID]": responsible_id,
        }
        
        # Добавляем соисполнителей если указаны
        if co_executors:
            for i, co_executor_id in enumerate(co_executors):
                task_data[f"fields[ACCOMPLICES][{i}]"] = co_executor_id
        
        result = self._make_request("POST", "tasks.task.add", task_data)
        logger.info(f"Создана задача в Bitrix24 с ID: {result.get('task', {}).get('id')}")
        
        return result
    
    def update_task_status(self, task_id: int, status: TaskStatus) -> Dict[str, Any]:
        """Обновление статуса задачи в Bitrix24"""
        
        # Маппинг наших статусов на статусы Bitrix24
        bitrix_status_map = {
            TaskStatus.NEW: "2",  # Ждет выполнения
            TaskStatus.IN_PROGRESS: "3",  # Выполняется
            TaskStatus.COMPLETED: "5",  # Завершена
            TaskStatus.CANCELLED: "4"  # Отложена
        }
        
        update_data = {
            "taskId": task_id,
            "fields[STATUS]": bitrix_status_map.get(status, "2")
        }
        
        result = self._make_request("POST", "tasks.task.update", update_data)
        logger.info(f"Обновлен статус задачи {task_id} на {status.value}")
        
        return result
    
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Получение информации о задаче"""
        try:
            data = {
                "taskId": task_id
            }
            
            result = self._make_request("GET", "tasks.task.get", data)
            
            # Обрабатываем случай, когда возвращается пустой список (задача не найдена)
            if isinstance(result, list) and len(result) == 0:
                return None
            elif isinstance(result, dict):
                return result.get("task", {})
            else:
                return result
            
        except Exception as e:
            error_text = str(e).lower()
            
            # Проверяем, является ли ошибка признаком удаленной задачи
            if any(keyword in error_text for keyword in [
                "task not found", 
                "задача не найдена",
                "access denied",
                "404",
                "not found"
            ]):
                logger.info(f"Задача {task_id} не найдена в Битрикс24 (возможно удалена)")
                return None
            else:
                # Для других ошибок пробрасываем исключение
                logger.error(f"Ошибка получения задачи {task_id}: {e}")
                raise
    
    def add_comment_to_task(self, task_id: int, comment: str) -> Dict[str, Any]:
        """Добавление комментария к задаче"""
        comment_data = {
            "taskId": task_id,
            "fields[POST_MESSAGE]": comment
        }
        
        result = self._make_request("POST", "tasks.task.commentitem.add", comment_data)
        logger.info(f"Добавлен комментарий к задаче {task_id}")
        
        return result
    
    def attach_telegram_file_to_task(self, task_id: int, file_info: Dict[str, Any], telegram_file_url: str) -> Dict[str, Any]:
        """Прикрепление информации о файле из Telegram к задаче как ссылка"""
        try:
            filename = file_info.get("filename", "unknown_file")
            file_size = file_info.get("size", 0)
            file_type = file_info.get("type", "file")
            
            # Создаем красивое описание файла
            type_emoji = {
                "photo": "🖼️",
                "document": "📄", 
                "video": "🎥",
                "audio": "🎵",
                "voice": "🎤"
            }
            
            emoji = type_emoji.get(file_type, "📎")
            size_text = f"{round(file_size / (1024 * 1024), 2)} MB" if file_size > 1024*1024 else f"{round(file_size / 1024, 2)} KB"
            
            # Формируем комментарий с файлом как прикрепление
            comment = f"""
{emoji} **ПРИКРЕПЛЕННЫЙ ФАЙЛ**

📋 **Название:** {filename}
📊 **Размер:** {size_text}
🔗 **Ссылка для скачивания:** {telegram_file_url}

💡 *Файл доступен по прямой ссылке из Telegram. Кликните на ссылку для скачивания.*
            """.strip()
            
            # Добавляем комментарий к задаче
            result = self.add_comment_to_task(task_id, comment)
            
            logger.info(f"✅ Информация о файле {filename} добавлена к задаче {task_id} как прикрепление")
            
            return {"success": True, "method": "telegram_link", "filename": filename}
            
        except Exception as e:
            logger.error(f"Ошибка прикрепления файла к задаче: {e}")
            return {"success": False, "error": str(e)}
    
    def _upload_via_disk(self, task_id: int, file_path: str, filename: str) -> Dict[str, Any]:
        """Загрузка файла через диск Битрикс24"""
        try:
            # Сначала получаем доступные хранилища
            storage_url = f"https://{self.domain}/rest/{settings.bitrix24_user_id}/{self.access_token}/disk.storage.getlist"
            
            storage_response = requests.get(storage_url)
            
            if storage_response.status_code == 200:
                storage_result = storage_response.json()
                
                if "result" in storage_result and storage_result["result"]:
                    # Берем первое доступное хранилище
                    storage = storage_result["result"][0]
                    storage_id = storage["ID"]
                    
                    logger.info(f"📂 Используем хранилище: {storage.get('NAME', 'Unknown')} (ID: {storage_id})")
                    
                    # Загружаем файл в хранилище
                    upload_url = f"https://{self.domain}/rest/{settings.bitrix24_user_id}/{self.access_token}/disk.storage.uploadfile"
                    
                    with open(file_path, 'rb') as file_content:
                        files = {
                            'fileContent': (filename, file_content, self._get_mime_type(filename))
                        }
                        
                        data = {
                            'id': storage_id,
                            'data[NAME]': filename
                        }
                        
                        upload_response = requests.post(upload_url, data=data, files=files)
                        
                        logger.info(f"🔍 Загрузка на диск: {upload_response.status_code}")
                        
                        if upload_response.status_code == 200:
                            upload_result = upload_response.json()
                            
                            if "result" in upload_result:
                                file_data = upload_result["result"]
                                file_id = file_data.get("ID")
                                download_url = file_data.get("DOWNLOAD_URL", "")
                                
                                # Добавляем комментарий к задаче с прикрепленным файлом
                                comment = f"""
📎 **Файл прикреплен:** {filename}
💾 **Размер:** {round(os.path.getsize(file_path) / 1024, 2)} KB
🔗 **Скачать:** {download_url}
📋 **ID файла:** {file_id}

*Файл загружен из Telegram и сохранен в Битрикс24*
                                """.strip()
                                
                                self.add_comment_to_task(task_id, comment)
                                
                                logger.info(f"✅ Файл {filename} загружен на диск Битрикс24 и прикреплен к задаче")
                                return {"success": True, "method": "disk_upload", "file_id": file_id}
            
            # Если все способы не сработали, добавляем хотя бы информацию о файле
            self._add_file_info_fallback(task_id, file_path, filename)
            return {"success": False, "method": "info_only"}
            
        except Exception as e:
            logger.error(f"Ошибка загрузки через диск: {e}")
            self._add_file_info_fallback(task_id, file_path, filename)
            return {"success": False, "error": str(e)}
    
    def _add_file_info_fallback(self, task_id: int, file_path: str, filename: str):
        """Добавление информации о файле если загрузка не удалась"""
        try:
            file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
            
            comment = f"""
📎 **Файл из Telegram:** {filename}
📊 **Размер:** {file_size_mb} MB
💾 **Статус:** Сохранен локально на сервере бота

❗ *Файл доступен администратору бота. При необходимости можно запросить отдельно.*
            """.strip()
            
            self.add_comment_to_task(task_id, comment)
            logger.info(f"Добавлена информация о файле {filename} в комментарий к задаче")
            
        except Exception as e:
            logger.error(f"Ошибка добавления информации о файле: {e}")
    
    async def _upload_file_alternative(self, task_id: int, file_path: str, filename: str) -> Dict[str, Any]:
        """Альтернативный способ загрузки через диск и комментарий"""
        try:
            # Способ 1: Загружаем на диск Битрикс24
            disk_url = f"https://{self.domain}/rest/{settings.bitrix24_user_id}/{self.access_token}/disk.storage.uploadfile"
            
            with open(file_path, 'rb') as file_content:
                files = {
                    'fileContent': (filename, file_content, self._get_mime_type(filename))
                }
                
                data = {
                    'id': 1,  # ID хранилища (может потребоваться настройка)
                    'data[NAME]': filename
                }
                
                response = requests.post(disk_url, data=data, files=files)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    if "result" in result:
                        file_id = result["result"]["ID"]
                        download_url = result["result"]["DOWNLOAD_URL"]
                        
                        # Добавляем комментарий с ссылкой на файл
                        comment = f"""
📎 **Прикреплен файл:** {filename}
🔗 **Ссылка для скачивания:** {download_url}
📊 **Размер:** {round(os.path.getsize(file_path) / 1024, 2)} KB
                        """.strip()
                        
                        self.add_comment_to_task(task_id, comment)
                        
                        logger.info(f"✅ Файл {filename} загружен на диск Битрикс24 и прикреплен к задаче")
                        return {"success": True, "filename": filename, "file_id": file_id}
            
            # Способ 2: Если не получилось загрузить, добавляем информацию о файле
            file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
            comment = f"""
📎 **Файл из Telegram:** {filename}
📊 **Размер:** {file_size_mb} MB
💾 **Сохранен локально на сервере бота**

❗ *Для получения файла обратитесь к администратору бота*
            """.strip()
            
            self.add_comment_to_task(task_id, comment)
            
            return {"success": False, "filename": filename, "local_only": True}
            
        except Exception as e:
            logger.error(f"Ошибка альтернативной загрузки файла: {e}")
            return {"success": False, "filename": filename, "error": str(e)}
    
    def _get_mime_type(self, filename: str) -> str:
        """Определение MIME типа файла по расширению"""
        extension = filename.lower().split('.')[-1] if '.' in filename else ''
        
        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg', 
            'png': 'image/png',
            'gif': 'image/gif',
            'pdf': 'application/pdf',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'txt': 'text/plain',
            'mp4': 'video/mp4',
            'mp3': 'audio/mpeg',
            'ogg': 'audio/ogg',
            'wav': 'audio/wav'
        }
        
        return mime_types.get(extension, 'application/octet-stream')
    
    def get_users(self) -> List[Dict[str, Any]]:
        """Получение списка пользователей"""
        try:
            result = self._make_request("GET", "user.get")
            
            if isinstance(result, list):
                return result
            elif isinstance(result, dict) and "result" in result:
                return result["result"]
            else:
                return []
                
        except Exception as e:
            logger.error(f"Ошибка получения пользователей: {e}")
            return []
    
    def get_active_users(self) -> List[Dict[str, Any]]:
        """Получение списка активных пользователей"""
        try:
            # Получаем всех пользователей и фильтруем активных
            all_users = self.get_users()
            
            # Фильтруем только активных пользователей
            active_users = [
                user for user in all_users 
                if user.get("ACTIVE") == "Y" or user.get("ACTIVE") == True
            ]
            
            logger.info(f"Найдено {len(active_users)} активных пользователей из {len(all_users)}")
            return active_users
                
        except Exception as e:
            logger.error(f"Ошибка получения активных пользователей: {e}")
            return []
    
    def search_user_by_name(self, search_term: str) -> List[Dict[str, Any]]:
        """Поиск пользователей по имени"""
        try:
            data = {
                "filter": {
                    "NAME": f"%{search_term}%",
                    "ACTIVE": "Y"
                }
            }
            
            result = self._make_request("GET", "user.get", data)
            
            if isinstance(result, list):
                return result
            elif isinstance(result, dict) and "result" in result:
                return result["result"]
            else:
                return []
                
        except Exception as e:
            logger.error(f"Ошибка поиска пользователей: {e}")
            return []
    
    def get_user_by_telegram_id(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Поиск пользователя по Telegram ID в поле tgID"""
        try:
            # Получаем всех пользователей и ищем среди них
            all_users = self.get_users()
            
            # Список возможных полей для Telegram ID
            possible_fields = ["UF_TELEGRAM_ID", "UF_TG_ID", "UF_TGID", "tgID", "UF_USR_1755866403098"]
            
            for user in all_users:
                for field in possible_fields:
                    field_value = user.get(field, "")
                    # Проверяем точное совпадение как строки
                    if str(field_value).strip() == str(telegram_id).strip():
                        logger.info(f"Найден пользователь по полю {field}: {user.get('NAME')} {user.get('LAST_NAME')} (ID: {user.get('ID')})")
                        return user
            
            logger.info(f"Пользователь с Telegram ID {telegram_id} не найден")
            return None
                
        except Exception as e:
            logger.error(f"Ошибка поиска пользователя по Telegram ID: {e}")
            return None
    
    def update_user_telegram_id(self, user_id: int, telegram_id: str) -> bool:
        """Обновление поля tgID пользователя в Битрикс24"""
        try:
            # Пробуем разные возможные названия поля для Telegram ID
            fields_to_try = ["UF_TELEGRAM_ID", "UF_TG_ID", "UF_TGID"]
            
            for field in fields_to_try:
                try:
                    data = {
                        "ID": user_id,
                        f"fields[{field}]": telegram_id
                    }
                    
                    result = self._make_request("POST", "user.update", data)
                    
                    if result:
                        logger.info(f"Обновлен Telegram ID для пользователя {user_id}: {telegram_id} (поле: {field})")
                        return True
                        
                except Exception as field_error:
                    logger.warning(f"Не удалось обновить поле {field}: {field_error}")
                    continue
            
            logger.error(f"Не удалось найти подходящее поле для Telegram ID у пользователя {user_id}")
            return False
                
        except Exception as e:
            logger.error(f"Ошибка обновления Telegram ID пользователя: {e}")
            return False
    
    def get_users_with_telegram_ids(self) -> List[Dict[str, Any]]:
        """Получение всех пользователей, у которых заполнено поле tgID"""
        try:
            all_users = self.get_users()
            users_with_tg = []
            
            # Список возможных полей для Telegram ID
            possible_fields = ["UF_TELEGRAM_ID", "UF_TG_ID", "UF_TGID", "tgID", "UF_USR_1755866403098"]
            
            for user in all_users:
                # Проверяем различные возможные поля для Telegram ID
                telegram_id = None
                used_field = None
                
                for field in possible_fields:
                    if field in user and user[field] and str(user[field]).strip():
                        telegram_id = str(user[field]).strip()
                        used_field = field
                        break
                
                if telegram_id:
                    user["TELEGRAM_ID"] = telegram_id  # Нормализуем поле
                    user["TELEGRAM_FIELD"] = used_field  # Запоминаем, какое поле использовалось
                    users_with_tg.append(user)
                    logger.debug(f"Пользователь {user.get('NAME')} {user.get('LAST_NAME')} имеет Telegram ID {telegram_id} в поле {used_field}")
            
            logger.info(f"Найдено {len(users_with_tg)} пользователей с Telegram ID")
            return users_with_tg
                
        except Exception as e:
            logger.error(f"Ошибка получения пользователей с Telegram ID: {e}")
            return []
    
    def find_bitrix_user_by_telegram(self, telegram_id: str) -> Optional[int]:
        """Поиск Bitrix24 ID пользователя по Telegram ID"""
        user = self.get_user_by_telegram_id(telegram_id)
        if user:
            return int(user.get("ID", 0))
        return None


# Создаем глобальный экземпляр API
bitrix24_api = Bitrix24API()
