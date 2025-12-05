#!/usr/bin/env python3
"""
Скрипт для создания и инициализации тестовой БД lab_test
Выполняет те же операции, что и для основной БД lab
"""
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def init_test_db():
    """Создает тестовую БД lab_test и инициализирует её структуру"""
    # Подключаемся к postgres БД для создания новой БД
    host = os.getenv("POSTGRES_HOST", "localhost")
    user = os.getenv("POSTGRES_USER", "admin")
    password = os.getenv("POSTGRES_PASSWORD", "12345")
    port = os.getenv("POSTGRES_PORT", "5432")
    
    # Если host = "db", пробуем сначала localhost
    if host == "db":
        try:
            conn = psycopg2.connect(
                host="localhost",
                database="postgres",
                user=user,
                password=password,
                port=port
            )
        except:
            conn = psycopg2.connect(
                host=host,
                database="postgres",
                user=user,
                password=password,
                port=port
            )
    else:
        conn = psycopg2.connect(
            host=host,
            database="postgres",
            user=user,
            password=password,
            port=port
        )
    
    conn.autocommit = True
    cursor = conn.cursor()
    
    # Создаем БД lab_test, если её нет
    try:
        cursor.execute("CREATE DATABASE lab_test")
        print("Database lab_test created successfully")
    except psycopg2.errors.DuplicateDatabase:
        print("Database lab_test already exists")
    except Exception as e:
        print(f"Error creating database: {e}")
        cursor.close()
        conn.close()
        return
    
    cursor.close()
    conn.close()
    
    # Теперь подключаемся к lab_test и создаем таблицы
    if host == "db":
        try:
            test_conn = psycopg2.connect(
                host="localhost",
                database="lab_test",
                user=user,
                password=password,
                port=port
            )
        except:
            test_conn = psycopg2.connect(
                host=host,
                database="lab_test",
                user=user,
                password=password,
                port=port
            )
    else:
        test_conn = psycopg2.connect(
            host=host,
            database="lab_test",
            user=user,
            password=password,
            port=port
        )
    
    test_cursor = test_conn.cursor()
    
    # Читаем и выполняем init.sql
    # Пробуем разные пути: в Docker контейнере и локально
    possible_init_paths = [
        "/docker-entrypoint-initdb.d/01_init.sql",  # В Docker
        os.path.join(os.path.dirname(__file__), "init.sql"),  # Локально
        "init.sql"  # Текущая директория
    ]
    
    init_sql = None
    for init_sql_path in possible_init_paths:
        if os.path.exists(init_sql_path):
            with open(init_sql_path, 'r', encoding='utf-8') as f:
                init_sql = f.read()
            break
    
    if init_sql:
        test_cursor.execute(init_sql)
        test_conn.commit()
        print("Tables created in lab_test")
    else:
        print("Warning: init.sql not found, skipping table creation")
    
    # Читаем и выполняем insert.sql
    possible_insert_paths = [
        "/docker-entrypoint-initdb.d/02_inserts.sql",  # В Docker
        os.path.join(os.path.dirname(__file__), "insert.sql"),  # Локально
        "insert.sql"  # Текущая директория
    ]
    
    insert_sql = None
    for insert_sql_path in possible_insert_paths:
        if os.path.exists(insert_sql_path):
            with open(insert_sql_path, 'r', encoding='utf-8') as f:
                insert_sql = f.read()
            break
    
    if insert_sql:
        # Выполняем каждую команду отдельно
        for statement in insert_sql.split(';'):
            statement = statement.strip()
            if statement:
                try:
                    test_cursor.execute(statement)
                except Exception as e:
                    # Игнорируем ошибки типа "already exists" для ON CONFLICT
                    if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                        print(f"Warning executing statement: {e}")
        test_conn.commit()
        print("Initial data inserted into lab_test")
    else:
        print("Warning: insert.sql not found, skipping initial data insertion")
    
    test_cursor.close()
    test_conn.close()
    print("Test database lab_test initialized successfully!")

if __name__ == "__main__":
    init_test_db()

