from fastapi import HTTPException, Header, Request
from fastapi.responses import JSONResponse
from typing import Optional
import json
import os
import re
import time
from psycopg2.extras import RealDictCursor

from core.utils import get_db_connection, verify_solana_signature


# Публичные API эндпоинты (не требуют авторизации)
PUBLIC_API_ENDPOINTS = [
    "/api/auth",
    "/api/auth/challenge",
    "/api/whitelist",
    "/api/config",
    "/api/cards",  # Публичный просмотр карт
    "/api/chests",  # Публичный просмотр паков
    "/api/jackpot",  # Публичный просмотр джекпота
    "/api/jackpot/last",
    "/api/jackpot/draw",  # Публичный розыгрыш джекпота
    "/api/super-jackpot",
    "/api/predictions/markets",  # Публичный просмотр активных пари
    "/api/clash/matches",  # Публичный просмотр матчей
    "/api/clash/matches/",  # Публичный просмотр деталей матча
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


_GAMBA_AUTH_RE = re.compile(r"^Gamba Auth:\s*(\d{10,})\s*$")
_GAMBA_CHALLENGE_RE = re.compile(
    r"^Gamba Auth\s*\nWallet:\s*(\S+)\s*\nNonce:\s*([A-Za-z0-9_\-]+)\s*\nIssuedAt:\s*(\d{10,})\s*$"
)


def _validate_auth_message(message: str) -> None:
    require_ts = os.getenv("AUTH_REQUIRE_TIMESTAMP", "1").lower() in ("1", "true", "yes")
    max_age_seconds = int(os.getenv("AUTH_MESSAGE_MAX_AGE_SECONDS", "300"))
    max_future_skew_seconds = int(os.getenv("AUTH_MESSAGE_MAX_FUTURE_SKEW_SECONDS", "30"))

    msg = (message or "").strip()
    m = _GAMBA_AUTH_RE.match(msg)
    if m:
        ts_ms = int(m.group(1))
    else:
        cm = _GAMBA_CHALLENGE_RE.match(msg)
        if not cm:
            if require_ts:
                raise HTTPException(status_code=401, detail="Invalid auth message format")
            return
        ts_ms = int(cm.group(3))

    now_ms = int(time.time() * 1000)
    age_ms = now_ms - ts_ms

    if age_ms < -(max_future_skew_seconds * 1000):
        raise HTTPException(status_code=401, detail="Auth message timestamp is in the future")
    if age_ms > (max_age_seconds * 1000):
        raise HTTPException(status_code=401, detail="Auth message expired")


def _csrf_check_cookie_auth(request: Request) -> None:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    host = request.headers.get("host")

    trusted = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
    if host:
        scheme = request.url.scheme or "http"
        trusted.append(f"{scheme}://{host}")

    def _is_trusted(value: str) -> bool:
        return any(value.startswith(t) for t in trusted)

    if origin:
        if not _is_trusted(origin):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        return

    if referer:
        if not _is_trusted(referer):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        return

    raise HTTPException(status_code=403, detail="CSRF check failed")


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

        _validate_auth_message(x_message)
        
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
                    _csrf_check_cookie_auth(request)
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

            _validate_auth_message(x_message)
            
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

