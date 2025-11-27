#!/bin/bash

# Скрипт для запуска тестов

echo "🧪 Запуск тестов системы авторизации..."
echo ""

# Проверяем, что мы в правильной директории
if [ ! -f "main.py" ]; then
    echo "❌ Ошибка: запустите скрипт из директории app/"
    exit 1
fi

# Проверяем наличие pytest
if ! command -v pytest &> /dev/null; then
    echo "❌ pytest не установлен. Установите зависимости: pip install -r requirements.txt"
    exit 1
fi

# Запускаем тесты
echo "📋 Запуск тестов..."
pytest -v --tb=short

# Показываем покрытие, если установлен pytest-cov
if python -c "import pytest_cov" 2>/dev/null; then
    echo ""
    echo "📊 Запуск тестов с покрытием..."
    pytest --cov=app --cov-report=term-missing --cov-report=html
    echo ""
    echo "✅ Отчет о покрытии сохранен в htmlcov/index.html"
fi

echo ""
echo "✅ Тесты завершены!"

