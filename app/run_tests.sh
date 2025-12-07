#!/bin/bash

# Скрипт для запуска тестов
# Использование:
#   ./run_tests.sh                    # Все тесты

echo "🧪 Запуск тестов..."
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

# Определяем, какие тесты запускать
TEST_FILE=""
if [ -n "$1" ]; then
    case "$1" in
        polymarket_sync)
            TEST_FILE="tests/test_polymarket_sync.py"
            echo "📋 Запуск тестов синхронизации Polymarket..."
            ;;
        predictions)
            TEST_FILE="tests/test_predictions.py tests/test_predictions_rewards.py"
            echo "📋 Запуск тестов предсказаний (включая награды)..."
            ;;
        auth)
            TEST_FILE="tests/test_auth.py"
            echo "📋 Запуск тестов авторизации..."
            ;;
        api)
            TEST_FILE="tests/test_api.py"
            echo "📋 Запуск тестов API..."
            ;;
        battle)
            TEST_FILE="tests/test_battle.py"
            echo "📋 Запуск тестов батлов..."
            ;;
        jackpot)
            TEST_FILE="tests/test_jackpot.py"
            echo "📋 Запуск тестов джекпота..."
            ;;
        *)
            echo "❌ Неизвестный тип тестов: $1"
            echo "Доступные опции: polymarket_sync, predictions, auth, api, battle, jackpot"
            exit 1
            ;;
    esac
else
    echo "📋 Запуск всех тестов..."
fi

# Запускаем тесты
if [ -n "$TEST_FILE" ]; then
    pytest -v --tb=short "$TEST_FILE"
else
    pytest -v --tb=short
fi

# Показываем покрытие, если установлен pytest-cov
if python -c "import pytest_cov" 2>/dev/null; then
    echo ""
    echo "📊 Запуск тестов с покрытием..."
    if [ -n "$TEST_FILE" ]; then
        pytest --cov=app --cov-report=term-missing --cov-report=html "$TEST_FILE"
    else
        pytest --cov=app --cov-report=term-missing --cov-report=html
    fi
    echo ""
    echo "✅ Отчет о покрытии сохранен в htmlcov/index.html"
fi

echo ""
echo "✅ Тесты завершены!"

