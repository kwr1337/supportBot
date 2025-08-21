#!/bin/bash

# Скрипт для запуска Telegram бота поддержки

echo "🤖 Запуск Telegram бота поддержки..."

# Проверка наличия виртуального окружения
if [ ! -d "venv" ]; then
    echo "❌ Виртуальное окружение не найдено. Создаем..."
    python3 -m venv venv
    echo "✅ Виртуальное окружение создано"
fi

# Активация виртуального окружения
source venv/bin/activate

# Проверка наличия зависимостей
if [ ! -f "venv/pyvenv.cfg" ] || [ ! -d "venv/lib" ]; then
    echo "📦 Установка зависимостей..."
    pip install -r requirements.txt
    echo "✅ Зависимости установлены"
fi

# Проверка наличия файла конфигурации
if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    echo "Создайте файл .env на основе .env.example"
    echo "cp .env.example .env"
    echo "Затем отредактируйте его, добавив ваши токены"
    exit 1
fi

# Запуск бота
echo "🚀 Запуск бота..."
python main.py
