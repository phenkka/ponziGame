"""
Вспомогательные функции для работы с БД, генерации кодов и верификации подписей.
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
import secrets
import string
import base58
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

load_dotenv()


def get_db_connection():
    """
    Создает подключение к БД.
    Автоматически использует localhost, если host="db" недоступен (для локального запуска).
    """
    host = os.getenv("POSTGRES_HOST", "db")
    database = os.getenv("POSTGRES_DB", "lab")
    user = os.getenv("POSTGRES_USER", "admin")
    password = os.getenv("POSTGRES_PASSWORD", "12345")
    port = os.getenv("POSTGRES_PORT", "5432")
    
    # Если host = db, пробуем сначала localhost для локального запуска
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
            # Если localhost не работает, пробуем db
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


def generate_ref_code(length=8):
    """
    Генерация уникального реферального кода.
    """
    characters = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


def verify_solana_signature(wallet: str, message: str, signature: list) -> bool:
    """
    Верификация подписи Solana.
    Проверяет, что подпись соответствует кошельку и сообщению.
    """
    try:
        # Конвертируем wallet в PublicKey
        wallet_bytes = base58.b58decode(wallet)
        
        # Конвертируем signature из list в bytes
        signature_bytes = bytes(signature)
        
        # Создаем VerifyKey из публичного ключа
        verify_key = VerifyKey(wallet_bytes)
        
        # Кодируем сообщение
        message_bytes = message.encode('utf-8')
        
        # Верифицируем подпись
        verify_key.verify(message_bytes, signature_bytes)
        return True
    except (BadSignatureError, Exception) as e:
        print(f"Signature verification failed: {e}")
        return False

