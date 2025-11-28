#!/bin/bash
# Скрипт для создания и инициализации тестовой БД lab_test
# Выполняется после инициализации основной БД lab

set -e

echo "Creating test database lab_test..."

# Создаем БД lab_test
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    SELECT 'CREATE DATABASE lab_test'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'lab_test')\gexec
EOSQL

echo "Database lab_test created or already exists"

# Применяем init.sql к lab_test
echo "Creating tables in lab_test..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "lab_test" -f /docker-entrypoint-initdb.d/01_init.sql

# Применяем insert.sql к lab_test
echo "Inserting initial data into lab_test..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "lab_test" -f /docker-entrypoint-initdb.d/02_inserts.sql

echo "Test database lab_test initialized successfully!"

