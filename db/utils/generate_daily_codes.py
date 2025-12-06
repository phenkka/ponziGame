#!/usr/bin/env python3
"""
Скрипт для генерации ежедневных кодов на год вперед.
Запускать один раз для инициализации или для обновления кодов.
"""

import os
import sys
from datetime import date, timedelta, datetime, timezone
import secrets
import string

# Проверка зависимостей
try:
    import psycopg2
except ImportError:
    print("Ошибка: модуль psycopg2 не установлен.")
    print("Установите его командой: pip install psycopg2-binary")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv не обязателен, если переменные окружения заданы иначе
    pass

def get_db_connection():
    host = os.getenv("POSTGRES_HOST", "db")
    database = os.getenv("POSTGRES_DB", "lab")
    user = os.getenv("POSTGRES_USER", "admin")
    password = os.getenv("POSTGRES_PASSWORD", "12345")
    port = os.getenv("POSTGRES_PORT", "5432")
    
    # Если хост "db" (имя контейнера), но мы запускаемся локально, используем localhost
    if host == "db" and not os.path.exists("/.dockerenv"):
        host = "localhost"
    
    if host == "db":
        try:
            conn = psycopg2.connect(
                host="localhost",
                database=database,
                user=user,
                password=password,
                port=port,
                connect_timeout=2
            )
            return conn
        except Exception:
            pass
    
    try:
        conn = psycopg2.connect(
            host=host,
            database=database,
            user=user,
            password=password,
            port=port
        )
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

def generate_daily_code():
    """Генерирует случайный код из 8 символов"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(characters) for _ in range(8))

def generate_codes_for_year():
    """Генерирует коды на год вперед от сегодняшней даты (UTC)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Используем UTC для генерации кодов
    today = datetime.now(timezone.utc).date()
    end_date = today + timedelta(days=365)
    
    codes_generated = 0
    codes_skipped = 0
    
    current_date = today
    while current_date <= end_date:
        # Проверяем, существует ли уже код для этой даты
        cursor.execute("SELECT id_code FROM Daily_codes WHERE code_date = %s", (current_date,))
        existing = cursor.fetchone()
        
        if existing:
            codes_skipped += 1
            current_date += timedelta(days=1)
            continue
        
        # Генерируем уникальный код
        code = generate_daily_code()
        
        # Проверяем уникальность кода
        cursor.execute("SELECT id_code FROM Daily_codes WHERE daily_code = %s", (code,))
        while cursor.fetchone():
            code = generate_daily_code()
            cursor.execute("SELECT id_code FROM Daily_codes WHERE daily_code = %s", (code,))
        
        # Вставляем код
        try:
            cursor.execute(
                "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s)",
                (current_date, code)
            )
            codes_generated += 1
        except Exception as e:
            print(f"Error inserting code for {current_date}: {e}")
        
        current_date += timedelta(days=1)
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print(f"Generated {codes_generated} new daily codes")
    print(f"Skipped {codes_skipped} existing codes")
    print(f"Date range: {today} to {end_date}")

if __name__ == "__main__":
    print("Generating daily codes for the next year...")
    generate_codes_for_year()
    print("Done!")
