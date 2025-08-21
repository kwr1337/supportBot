#!/usr/bin/env python3
"""
Простой скрипт для назначения администратора
"""
from user_management_service import user_management
from models import UserRole
from database import create_tables

def make_admin():
    """Назначение администратора"""
    print("👑 Назначение администратора")
    print("=" * 30)
    
    # Создаем таблицы
    create_tables()
    
    # Ваш Telegram ID (из логов)
    your_telegram_id = "608167496"
    
    try:
        # Получаем или создаем пользователя
        from models import BotUser
        from database import get_db_session
        
        db = get_db_session()
        try:
            # Проверяем, существует ли пользователь
            user = db.query(BotUser).filter(
                BotUser.telegram_user_id == your_telegram_id
            ).first()
            
            if user:
                # Обновляем роль
                user.role = UserRole.ADMIN.value
                user.notes = "Назначен администратором через скрипт"
                db.commit()
                print(f"✅ Пользователь {your_telegram_id} назначен администратором!")
            else:
                # Создаем нового администратора
                admin_user = BotUser(
                    telegram_user_id=your_telegram_id,
                    role=UserRole.ADMIN.value,
                    first_name="Администратор",
                    username="kurro7",
                    added_by="system",
                    notes="Первый администратор системы"
                )
                
                db.add(admin_user)
                db.commit()
                print(f"✅ Создан новый администратор: {your_telegram_id}")
                
        finally:
            db.close()
            
        print("\n🎯 Теперь вы можете использовать команды администратора:")
        print("• /users - список пользователей")
        print("• /add_admin <user_id> - добавить админа")
        print("• /stats - статистика")
        print("• /analytics - аналитика")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    make_admin()
