#!/bin/bash

# Универсальный скрипт для применения всех миграций на обе БД (lab и lab_test)
# Использование: 
#   ./apply_migrations.sh              # Локально
#   ./apply_migrations.sh docker       # Через Docker

set -e  # Остановка при ошибке

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Режим работы (local или docker)
MODE=${1:-"local"}

# Параметры подключения (можно переопределить через переменные окружения)
POSTGRES_HOST=${POSTGRES_HOST:-"localhost"}
POSTGRES_USER=${POSTGRES_USER:-"admin"}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-"12345"}
POSTGRES_PORT=${POSTGRES_PORT:-"5432"}

# Имена контейнеров (для Docker режима)
POSTGRES_CONTAINER=${POSTGRES_CONTAINER:-"gamba_db"}
APP_CONTAINER=${APP_CONTAINER:-"gamba_web"}

# Пути
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$SCRIPT_DIR/migrations"
GENERATE_CODES_SCRIPT="$SCRIPT_DIR/utils/generate_daily_codes.py"

echo -e "${BLUE}=========================================="
echo "Применение миграций базы данных"
echo "==========================================${NC}"
echo -e "Режим: ${YELLOW}${MODE}${NC}"
echo ""

# Проверка наличия папки миграций
if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo -e "${RED}Ошибка: папка migrations не найдена: $MIGRATIONS_DIR${NC}"
    exit 1
fi

# Получаем список миграций, отсортированных по номеру
MIGRATIONS=($(ls "$MIGRATIONS_DIR"/*.sql 2>/dev/null | sort -V))

if [ ${#MIGRATIONS[@]} -eq 0 ]; then
    echo -e "${YELLOW}Предупреждение: миграции не найдены в $MIGRATIONS_DIR${NC}"
    exit 0
fi

echo -e "${BLUE}Найдено миграций: ${#MIGRATIONS[@]}${NC}"
for migration in "${MIGRATIONS[@]}"; do
    echo "  - $(basename "$migration")"
done
echo ""

# Функция для применения миграции локально
apply_migration_local() {
    local db_name=$1
    local migration_file=$2
    local migration_name=$(basename "$migration_file")
    
    echo -e "${YELLOW}  → Применение $migration_name на $db_name...${NC}"
    
    export PGPASSWORD="$POSTGRES_PASSWORD"
    local output
    output=$(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$db_name" -f "$migration_file" 2>&1)
    local exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}    ✓ Успешно${NC}"
        return 0
    else
        echo -e "${RED}    ✗ Ошибка${NC}"
        # Показываем только важные ошибки (не предупреждения)
        echo "$output" | grep -iE "(error|fatal)" | sed 's/^/      /' || true
        return 1
    fi
}

# Функция для применения миграции через Docker
apply_migration_docker() {
    local db_name=$1
    local migration_file=$2
    local migration_name=$(basename "$migration_file")
    
    echo -e "${YELLOW}  → Применение $migration_name на $db_name...${NC}"
    
    local output
    output=$(docker exec -i "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$db_name" < "$migration_file" 2>&1)
    local exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}    ✓ Успешно${NC}"
        return 0
    else
        echo -e "${RED}    ✗ Ошибка${NC}"
        echo "$output" | grep -iE "(error|fatal)" | sed 's/^/      /' || true
        return 1
    fi
}

# Функция для генерации кодов локально
generate_codes_local() {
    local db_name=$1
    
    echo -e "${YELLOW}  → Генерация ежедневных кодов для $db_name...${NC}"
    
    export POSTGRES_DB="$db_name"
    export POSTGRES_HOST="$POSTGRES_HOST"
    export POSTGRES_USER="$POSTGRES_USER"
    export POSTGRES_PASSWORD="$POSTGRES_PASSWORD"
    export POSTGRES_PORT="$POSTGRES_PORT"
    
    local output
    output=$(python3 "$GENERATE_CODES_SCRIPT" 2>&1)
    local exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}    ✓ Успешно${NC}"
        echo "$output" | grep -E "(Generated|Skipped|Date range)" | sed 's/^/      /'
        return 0
    else
        echo -e "${RED}    ✗ Ошибка${NC}"
        echo "$output" | head -5 | sed 's/^/      /'
        return 1
    fi
}

# Функция для генерации кодов через Docker
generate_codes_docker() {
    local db_name=$1
    
    echo -e "${YELLOW}  → Генерация ежедневных кодов для $db_name...${NC}"
    
    if docker ps --format '{{.Names}}' | grep -q "^${APP_CONTAINER}$"; then
        local output
        output=$(docker exec -e POSTGRES_DB="$db_name" "$APP_CONTAINER" python3 /app/db/utils/generate_daily_codes.py 2>&1)
        local exit_code=$?
        
        if [ $exit_code -eq 0 ]; then
            echo -e "${GREEN}    ✓ Успешно${NC}"
            echo "$output" | grep -E "(Generated|Skipped|Date range)" | sed 's/^/      /'
            return 0
        else
            echo -e "${RED}    ✗ Ошибка${NC}"
            echo "$output" | head -5 | sed 's/^/      /'
            return 1
        fi
    else
        # Fallback на локальный Python
        echo -e "${YELLOW}    (контейнер не найден, используем локальный Python)${NC}"
        export POSTGRES_DB="$db_name"
        export POSTGRES_HOST="$POSTGRES_HOST"
        export POSTGRES_USER="$POSTGRES_USER"
        export POSTGRES_PASSWORD="$POSTGRES_PASSWORD"
        export POSTGRES_PORT="$POSTGRES_PORT"
        
        local output
        output=$(python3 "$GENERATE_CODES_SCRIPT" 2>&1)
        local exit_code=$?
        
        if [ $exit_code -eq 0 ]; then
            echo -e "${GREEN}    ✓ Успешно (локально)${NC}"
            echo "$output" | grep -E "(Generated|Skipped|Date range)" | sed 's/^/      /'
            return 0
        else
            echo -e "${RED}    ✗ Ошибка${NC}"
            echo "$output" | head -5 | sed 's/^/      /'
            return 1
        fi
    fi
}

# Проверка подключения к БД
check_connection() {
    local db_name=$1
    
    if [ "$MODE" = "docker" ]; then
        if ! docker ps --format '{{.Names}}' | grep -q "^${POSTGRES_CONTAINER}$"; then
            echo -e "${RED}Ошибка: контейнер ${POSTGRES_CONTAINER} не запущен${NC}"
            return 1
        fi
        return 0
    else
        export PGPASSWORD="$POSTGRES_PASSWORD"
        if psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$db_name" -c "SELECT 1;" > /dev/null 2>&1; then
            return 0
        else
            echo -e "${RED}Ошибка: не удалось подключиться к БД $db_name${NC}"
            return 1
        fi
    fi
}

# Применение миграций на БД
apply_migrations_to_db() {
    local db_name=$1
    
    echo -e "${BLUE}Применение миграций на БД: ${db_name}${NC}"
    
    # Проверка подключения
    if ! check_connection "$db_name"; then
        return 1
    fi
    
    # Применяем каждую миграцию
    for migration_file in "${MIGRATIONS[@]}"; do
        if [ "$MODE" = "docker" ]; then
            apply_migration_docker "$db_name" "$migration_file" || return 1
        else
            apply_migration_local "$db_name" "$migration_file" || return 1
        fi
    done
    
    # Если есть миграция 05, генерируем коды
    if ls "$MIGRATIONS_DIR"/05_*.sql > /dev/null 2>&1; then
        echo -e "${BLUE}  Обнаружена миграция 05, требуется генерация кодов...${NC}"
        if [ "$MODE" = "docker" ]; then
            if ! generate_codes_docker "$db_name"; then
                echo -e "${YELLOW}    ⚠ Предупреждение: не удалось сгенерировать коды автоматически${NC}"
                echo -e "${YELLOW}    Вы можете сгенерировать их вручную позже${NC}"
            fi
        else
            if ! generate_codes_local "$db_name"; then
                echo -e "${YELLOW}    ⚠ Предупреждение: не удалось сгенерировать коды автоматически${NC}"
                echo -e "${YELLOW}    Возможные причины:${NC}"
                echo -e "${YELLOW}      - Модуль psycopg2 не установлен (pip install psycopg2-binary)${NC}"
                echo -e "${YELLOW}      - Проблемы с подключением к БД${NC}"
                echo -e "${YELLOW}    Вы можете сгенерировать их вручную:${NC}"
                echo -e "${YELLOW}      export POSTGRES_DB=$db_name && python3 $GENERATE_CODES_SCRIPT${NC}"
            fi
        fi
    fi
    
    echo ""
    return 0
}

# Основная логика
main() {
    local migration_errors=0
    
    # Применяем на основную БД
    if apply_migrations_to_db "lab"; then
        echo -e "${GREEN}✓ Миграции для БД 'lab' применены успешно${NC}"
    else
        echo -e "${RED}✗ Ошибки при применении миграций для БД 'lab'${NC}"
        migration_errors=$((migration_errors + 1))
    fi
    
    # Применяем на тестовую БД
    echo ""
    if apply_migrations_to_db "lab_test"; then
        echo -e "${GREEN}✓ Миграции для БД 'lab_test' применены успешно${NC}"
    else
        echo -e "${RED}✗ Ошибки при применении миграций для БД 'lab_test'${NC}"
        migration_errors=$((migration_errors + 1))
    fi
    
    echo ""
    echo -e "${BLUE}==========================================${NC}"
    if [ $migration_errors -eq 0 ]; then
        echo -e "${GREEN}Все миграции успешно применены!${NC}"
        echo -e "${BLUE}==========================================${NC}"
        return 0
    else
        echo -e "${RED}Ошибки при применении миграций!${NC}"
        echo -e "${BLUE}==========================================${NC}"
        return 1
    fi
}

# Запуск
main
