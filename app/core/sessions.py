from fastapi import Request, HTTPException
from psycopg2.extras import RealDictCursor
import hashlib

from core.utils import get_db_connection


async def verify_session_cookie(request: Request) -> dict:
    auth_token = request.cookies.get("auth_token")
    
    if not auth_token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please connect your wallet."
        )
    
    # Проверяем токен в БД (пока упрощенная версия - проверяем по wallet)
    # В будущем можно создать таблицу Sessions для хранения токенов
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Получаем всех пользователей и проверяем токен
        # (в продакшене нужно создать таблицу Sessions)
        cursor.execute("SELECT wallet, ref_code FROM Users")
        users = cursor.fetchall()
        
        user = None
        for u in users:
            token_data = f"{u['wallet']}:{u['ref_code']}"
            token_hash = hashlib.sha256(token_data.encode()).hexdigest()
            if token_hash == auth_token:
                user = u
                break
        
        cursor.close()
        conn.close()
        
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid session. Please reconnect your wallet."
            )
        
        return {
            "wallet": user['wallet'],
            "ref_code": user['ref_code']
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Session verification error: {e}")
        raise HTTPException(
            status_code=401,
            detail="Session verification failed."
        )

