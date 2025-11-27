from fastapi import HTTPException, Header, Request
from fastapi.responses import JSONResponse
from typing import Optional
import json
from psycopg2.extras import RealDictCursor

from core.utils import get_db_connection, verify_solana_signature


# Публичные API эндпоинты (не требуют авторизации)
PUBLIC_API_ENDPOINTS = [
    "/api/auth",
    "/api/whitelist",
    "/api/config",
    "/api/cards",  # Публичный просмотр карт
    "/api/jackpot",  # Публичный просмотр джекпота
    "/api/jackpot/last",
    "/api/jackpot/draw",  # Публичный розыгрыш джекпота
    "/api/super-jackpot",
    "/health",
]


def is_public_endpoint(path: str) -> bool:
    """Проверяет, является ли эндпоинт публичным."""
    if path in PUBLIC_API_ENDPOINTS:
        return True
    
    # Проверяем паттерны (например, /api/whitelist/{wallet})
    for public_path in PUBLIC_API_ENDPOINTS:
        if path.startswith(public_path):
            return True
    
    return False


async def verify_auth(
    x_wallet: Optional[str] = Header(None, alias="X-Wallet"),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    x_message: Optional[str] = Header(None, alias="X-Message")
) -> dict:
    # Проверка наличия заголовков
    if not x_wallet or not x_signature or not x_message:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication headers. Wallet, signature and message required."
        )
    
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
        
        # Возвращаем данные пользователя
        return {
            "wallet": x_wallet,
            "user_id": user['id_user'],
            "ref_code": user['ref_code']
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Auth verification error: {e}")
        raise HTTPException(
            status_code=401,
            detail=f"Authentication failed: {str(e)}"
        )


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    
    # Проверяем только API эндпоинты
    if path.startswith("/api/"):
        # Пропускаем публичные эндпоинты
        if is_public_endpoint(path):
            response = await call_next(request)
            return response
        
        # Для всех остальных API эндпоинтов требуем авторизацию
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        # Если заголовки не предоставлены, проверяем cookie
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                # Проверяем cookie и получаем wallet из БД
                try:
                    from core.sessions import verify_session_cookie
                    user_data = await verify_session_cookie(request)
                    # Добавляем информацию о пользователе в request state
                    request.state.user = user_data
                    # Продолжаем выполнение запроса
                    response = await call_next(request)
                    return response
                except Exception as e:
                    print(f"Cookie auth failed: {e}")
                    return JSONResponse(
                        status_code=401,
                        content={
                            "success": False,
                            "error": "Unauthorized. Invalid session. Please reconnect your wallet."
                        }
                    )
            else:
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": "Unauthorized. Authentication required. Missing X-Wallet, X-Signature, or X-Message headers, or auth_token cookie."
                    }
                )
        
        # Верифицируем подпись
        try:
            signature_list = json.loads(x_signature) if isinstance(x_signature, str) else x_signature
            
            if not verify_solana_signature(x_wallet, x_message, signature_list):
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": "Unauthorized. Invalid signature."
                    }
                )
            
            # Проверяем пользователя в БД
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM Users WHERE wallet = %s", (x_wallet,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not user:
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": "Unauthorized. User not found. Please authenticate first."
                    }
                )
            
            # Добавляем информацию о пользователе в request state
            request.state.user = {
                "wallet": x_wallet,
                "user_id": user['id_user'],
                "ref_code": user['ref_code']
            }
        except Exception as e:
            print(f"Auth middleware error: {e}")
            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": f"Unauthorized. Authentication failed: {str(e)}"
                }
            )
    
    response = await call_next(request)
    return response

