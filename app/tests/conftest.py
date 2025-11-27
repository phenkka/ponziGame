import pytest
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
import base58
from nacl.signing import SigningKey
import json

load_dotenv()

# Тестовые данные
TEST_WALLET = "TestWallet1234567890123456789012345678901234567890"
TEST_WALLET_2 = "TestWallet9876543210987654321098765432109876543210"
TEST_MESSAGE = "Gamba Auth: 1234567890"

@pytest.fixture(scope="session")
def db_connection():
    try:
        # Для локального запуска используем localhost, для Docker - db
        host = os.getenv("POSTGRES_HOST", "localhost")
        # Если host = "db", пробуем localhost для локального запуска
        if host == "db":
            try:
                conn = psycopg2.connect(
                    host="localhost",
                    database=os.getenv("POSTGRES_DB", "lab"),
                    user=os.getenv("POSTGRES_USER", "admin"),
                    password=os.getenv("POSTGRES_PASSWORD", "12345"),
                    port=os.getenv("POSTGRES_PORT", "5432")
                )
                yield conn
                conn.close()
                return
            except:
                pass
        
        conn = psycopg2.connect(
            host=host,
            database=os.getenv("POSTGRES_DB", "lab"),
            user=os.getenv("POSTGRES_USER", "admin"),
            password=os.getenv("POSTGRES_PASSWORD", "12345"),
            port=os.getenv("POSTGRES_PORT", "5432")
        )
        yield conn
        conn.close()
    except Exception as e:
        pytest.skip(f"Database connection failed: {e}")

@pytest.fixture(scope="function")
def clean_db(db_connection):
    cursor = db_connection.cursor()
    try:
        # Удаляем в правильном порядке: сначала дочерние таблицы, потом родительские
        # НЕ удаляем Chests и Cards - это справочные данные, которые должны быть всегда
        cursor.execute("DELETE FROM Chest_openings")
        cursor.execute("DELETE FROM Chest_purchases")
        cursor.execute("DELETE FROM Card_User")
        cursor.execute("DELETE FROM Referral_system")
        cursor.execute("DELETE FROM Users")
        # НЕ удаляем Cards и Chests - это статические справочные данные
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
        cursor.execute("DELETE FROM Chest_purchases")
        cursor.execute("DELETE FROM Card_User")
        cursor.execute("DELETE FROM Referral_system")
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

