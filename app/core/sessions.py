from fastapi import Request, HTTPException
from psycopg2.extras import RealDictCursor
import hashlib
import json

from core.utils import get_db_connection, verify_solana_signature


async def verify_session_cookie(request: Request) -> dict:
    x_wallet = request.headers.get("X-Wallet")
    x_signature = request.headers.get("X-Signature")
    x_message = request.headers.get("X-Message")
    
    # Приоритет: проверка подписи из заголовков
    if x_wallet and x_signature and x_message:
        try:
            # Парсим signature из строки (формат: "[1,2,3,...]")
            signature_list = json.loads(x_signature) if isinstance(x_signature, str) else x_signature
            
            # Верифицируем подпись
            if not verify_solana_signature(x_wallet, x_message, signature_list):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid signature. Authentication failed."
                )
            
            # Проверяем, что пользователь существует в БД
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT * FROM Users WHERE wallet = %s", (x_wallet,))
            user = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="User not found. Please authenticate first."
                )
            
            return {
                "wallet": x_wallet,
                "user_id": user['id_user'],
                "ref_code": user['ref_code']
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Signature verification error: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Authentication failed: {str(e)}"
            )
    
    # Fallback: проверка cookie (для обратной совместимости, но менее безопасно)
    auth_token = request.cookies.get("auth_token")
    
    if not auth_token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Please connect your wallet and provide signature headers (X-Wallet, X-Signature, X-Message)."
        )
    
    # Проверяем токен в БД (упрощенная версия - проверяем по wallet)
    # В будущем можно создать таблицу Sessions для хранения токенов
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Получаем всех пользователей и проверяем токен
        # (в продакшене нужно создать таблицу Sessions)
        cursor.execute("SELECT wallet, ref_code, id_user FROM Users")
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
            "user_id": user['id_user'],
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

