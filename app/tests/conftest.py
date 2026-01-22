import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import time
from dotenv import load_dotenv
import base58
from nacl.signing import SigningKey
import json

load_dotenv()

# ВАЖНО: Устанавливаем переменную окружения ДО импорта модулей приложения
# Это гарантирует, что приложение будет использовать тестовую БД lab_test
# вместо основной БД lab. ОСНОВНАЯ БД lab НЕ БУДЕТ ЗАТРОНУТА ТЕСТАМИ!
os.environ["POSTGRES_DB"] = "lab_test"

# Для тестов по умолчанию выключаем строгие прод-флаги.
# Отдельные тесты могут включать их через monkeypatch.setenv.
os.environ["AUTH_CHALLENGE_REQUIRED"] = "0"
os.environ["TX_REQUIRE_CONFIRMATION_STATUS"] = "0"

# Тестовые данные
TEST_WALLET = "TestWallet1234567890123456789012345678901234567890"
TEST_WALLET_2 = "TestWallet9876543210987654321098765432109876543210"
os.environ["AUTH_MESSAGE_MAX_AGE_SECONDS"] = "9999999999"
TEST_MESSAGE = f"Gamba Auth: {int(time.time() * 1000)}"

@pytest.fixture(scope="session")
def db_connection():
    try:
        # Для тестов используем отдельную БД lab_test, чтобы не затронуть основную БД lab
        # Для локального запуска используем localhost, для Docker - db
        host = os.getenv("POSTGRES_HOST", "localhost")
        # Если host = "db", пробуем localhost для локального запуска
        if host == "db":
            try:
                conn = psycopg2.connect(
                    host="localhost",
                    database="lab_test",  # Используем тестовую БД
                    user=os.getenv("POSTGRES_USER", "admin"),
                    password=os.getenv("POSTGRES_PASSWORD", "12345"),
                    port=os.getenv("POSTGRES_PORT", "5432")
                )
                _apply_migrations(conn)
                yield conn
                conn.close()
                return
            except:
                pass
        
        conn = psycopg2.connect(
            host=host,
            database="lab_test",  # Используем тестовую БД
            user=os.getenv("POSTGRES_USER", "admin"),
            password=os.getenv("POSTGRES_PASSWORD", "12345"),
            port=os.getenv("POSTGRES_PORT", "5432")
        )
        _apply_migrations(conn)
        yield conn
        conn.close()
    except Exception as e:
        pytest.skip(f"Database connection failed: {e}")

def _apply_migrations(conn):
    """Применяет миграции к тестовой БД"""
    import pathlib
    cursor = conn.cursor()
    try:
        # Получаем путь к папке миграций
        project_root = pathlib.Path(__file__).parent.parent.parent
        migrations_dir = project_root / "db" / "migrations"
        
        if not migrations_dir.exists():
            # Если папка миграций не найдена, просто продолжаем
            return
        
        # Получаем список миграций, отсортированных по номеру
        migration_files = sorted(migrations_dir.glob("*.sql"), key=lambda x: x.name)
        
        for migration_file in migration_files:
            try:
                # Читаем SQL из файла
                with open(migration_file, 'r', encoding='utf-8') as f:
                    sql = f.read()
                
                # Применяем миграцию целиком (PostgreSQL обработает IF NOT EXISTS)
                try:
                    cursor.execute(sql)
                    conn.commit()
                except (psycopg2.errors.DuplicateTable, 
                        psycopg2.errors.DuplicateColumn,
                        psycopg2.errors.DuplicateObject,
                        psycopg2.errors.UndefinedObject) as e:
                    # Уже существует или объект не определен - это нормально
                    conn.rollback()
                    continue
                except psycopg2.errors.SyntaxError as e:
                    # Синтаксическая ошибка - пропускаем эту миграцию
                    conn.rollback()
                    continue
            except Exception as e:
                # Другие ошибки - возможно миграция уже применена
                conn.rollback()
                # Игнорируем ошибки для совместимости
                continue
    except Exception as e:
        # Если не удалось применить миграции, просто продолжаем
        # (миграции могут быть уже применены вручную)
        try:
            conn.rollback()
        except:
            pass
    finally:
        cursor.close()

@pytest.fixture(scope="function")
def clean_db(db_connection):
    cursor = db_connection.cursor()
    try:
        # Удаляем в правильном порядке: сначала дочерние таблицы, потом родительские
        # НЕ удаляем Chests и Cards - это справочные данные, которые должны быть всегда
        cursor.execute("DELETE FROM Chest_openings")
        cursor.execute("DELETE FROM Referral_rewards")
        cursor.execute("DELETE FROM Chest_purchases")
        cursor.execute("DELETE FROM Card_trades")  # Очищаем историю трейдов
        cursor.execute("DELETE FROM Card_User")
        cursor.execute("DELETE FROM Referral_system")
        cursor.execute("DELETE FROM Jackpot_tickets_snapshot")  # Очищаем snapshot tickets
        cursor.execute("DELETE FROM Jackpot_rounds")  # Очищаем раунды джекпота
        cursor.execute("DELETE FROM Super_jackpot_rounds")  # Очищаем раунды супер джекпота
        cursor.execute("DELETE FROM User_boost")  # Очищаем boost
        cursor.execute("DELETE FROM Daily_checkins")  # Очищаем чекины
        cursor.execute("DELETE FROM Daily_codes")  # Очищаем коды (будут пересозданы при необходимости)
        cursor.execute("DELETE FROM Battles")  # Очищаем батлы
        cursor.execute("DELETE FROM User_bets")  # Очищаем ставки на пари
        cursor.execute("DELETE FROM predictions")  # Очищаем пари
        cursor.execute("DELETE FROM Users")
        # Удаляем тестовые карты (с image_key, начинающимся с 'TEST_')
        # Сначала удаляем связанные записи в Card_User
        cursor.execute("""
            DELETE FROM Card_User
            WHERE id_card IN (
                SELECT id_card FROM Cards WHERE image_key LIKE 'TEST_%'
            )
        """)
        # Затем удаляем сами тестовые карты
        cursor.execute("DELETE FROM Cards WHERE image_key LIKE 'TEST_%'")
        # НЕ удаляем оригинальные Cards и Chests - это статические справочные данные
        db_connection.commit()
    except Exception as e:
        db_connection.rollback()
        print(f"Error cleaning DB: {e}")
    finally:
        cursor.close()
    
    yield
    
    # Очистка после теста
    cursor = db_connection.cursor()
    try:
        cursor.execute("DELETE FROM Chest_openings")
        cursor.execute("DELETE FROM Referral_rewards")
        cursor.execute("DELETE FROM Chest_purchases")
        cursor.execute("DELETE FROM Card_trades")  # Очищаем историю трейдов
        cursor.execute("DELETE FROM Card_User")
        cursor.execute("DELETE FROM Referral_system")
        cursor.execute("DELETE FROM Jackpot_tickets_snapshot")  # Очищаем snapshot tickets
        cursor.execute("DELETE FROM Jackpot_rounds")  # Очищаем раунды джекпота
        cursor.execute("DELETE FROM Super_jackpot_rounds")  # Очищаем раунды супер джекпота
        cursor.execute("DELETE FROM User_boost")  # Очищаем boost
        cursor.execute("DELETE FROM Daily_checkins")  # Очищаем чекины
        cursor.execute("DELETE FROM Daily_codes")  # Очищаем коды (будут пересозданы при необходимости)
        cursor.execute("DELETE FROM Battles")  # Очищаем батлы
        cursor.execute("DELETE FROM Users")
        # Удаляем тестовые паки - дубликаты по параметрам (оставляем только первые 5 оригинальных)
        # Удаляем все паки, кроме первых 5 (оригинальные из insert.sql)
        cursor.execute("""
            DELETE FROM Chests 
            WHERE id_chest NOT IN (
                SELECT id_chest 
                FROM Chests 
                ORDER BY id_chest 
                LIMIT 5
            )
        """)
        # Удаляем тестовые карты (с image_key, начинающимся с 'TEST_')
        # Сначала удаляем связанные записи в Card_User
        cursor.execute("""
            DELETE FROM Card_User
            WHERE id_card IN (
                SELECT id_card FROM Cards WHERE image_key LIKE 'TEST_%'
            )
        """)
        # Затем удаляем сами тестовые карты
        cursor.execute("DELETE FROM Cards WHERE image_key LIKE 'TEST_%'")
        db_connection.commit()
    except Exception as e:
        db_connection.rollback()
        print(f"Error cleaning DB after test: {e}")
    finally:
        cursor.close()

@pytest.fixture
def test_user(db_connection, clean_db):
    cursor = db_connection.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING *",
        (TEST_WALLET, "TESTCODE1")
    )
    user = cursor.fetchone()
    db_connection.commit()
    cursor.close()
    return user

@pytest.fixture
def test_user_2(db_connection, clean_db):
    cursor = db_connection.cursor(cursor_factory=RealDictCursor)
    cursor.execute(
        "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING *",
        (TEST_WALLET_2, "TESTCODE2")
    )
    user = cursor.fetchone()
    db_connection.commit()
    cursor.close()
    return user

@pytest.fixture
def generate_signature():
    def _generate(wallet: str, message: str):
        # Для тестов создаем подпись используя приватный ключ
        # В реальности это делается на клиенте через Phantom
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        
        # Кодируем публичный ключ в base58 (как Solana адрес)
        wallet_bytes = verify_key.encode()
        
        # Подписываем сообщение
        signed = signing_key.sign(message.encode('utf-8'))
        signature_bytes = signed.signature
        
        # Конвертируем в формат, который ожидает API
        signature_list = list(signature_bytes)
        
        return {
            "wallet": base58.b58encode(wallet_bytes).decode('utf-8'),
            "signature": signature_list,
            "message": message,
            "verify_key": verify_key  # Для проверки
        }
    return _generate

@pytest.fixture
def auth_headers(test_user):
    message = TEST_MESSAGE
    
    # Генерируем валидную подпись с реальным Solana адресом
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    
    # Создаем wallet адрес из verify_key (реальный Solana адрес)
    wallet_bytes = verify_key.encode()
    wallet_address = base58.b58encode(wallet_bytes).decode('utf-8')
    
    # Подписываем сообщение
    signed = signing_key.sign(message.encode('utf-8'))
    signature_list = list(signed.signature)
    
    return {
        "X-Wallet": wallet_address,  # Реальный Solana адрес
        "X-Signature": json.dumps(signature_list),
        "X-Message": message
    }

@pytest.fixture
def auth_headers_for_wallet():
    def _create_headers(wallet: str, message: str = TEST_MESSAGE):
        # Для существующего wallet нужно декодировать его в bytes
        try:
            wallet_bytes = base58.b58decode(wallet)
            # Проверяем, что это валидный адрес (32 байта)
            if len(wallet_bytes) != 32:
                # Если не валидный адрес, создаем новый
                signing_key = SigningKey.generate()
                verify_key = signing_key.verify_key
                wallet_bytes = verify_key.encode()
                wallet = base58.b58encode(wallet_bytes).decode('utf-8')
            else:
                # Используем существующий wallet, но нужен приватный ключ для подписи
                # Для тестов создаем новый ключ и обновляем wallet
                signing_key = SigningKey.generate()
                verify_key = signing_key.verify_key
                wallet_bytes = verify_key.encode()
                wallet = base58.b58encode(wallet_bytes).decode('utf-8')
        except:
            # Если не удалось декодировать, создаем новый
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            wallet_bytes = verify_key.encode()
            wallet = base58.b58encode(wallet_bytes).decode('utf-8')
        
        # Подписываем сообщение
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        return {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }, wallet
    return _create_headers

@pytest.fixture
def invalid_auth_headers():
    return {
        "X-Wallet": "InvalidWallet",
        "X-Signature": json.dumps([1, 2, 3, 4, 5]),
        "X-Message": "Invalid message"
    }

@pytest.fixture
def missing_auth_headers():
    return {}

