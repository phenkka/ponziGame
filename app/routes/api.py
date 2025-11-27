from fastapi import HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
import hashlib
import json

from core.models import AuthRequest
from core.utils import get_db_connection, generate_ref_code, verify_solana_signature
from core.auth import verify_auth


def setup_api_routes(app):
    @app.get("/api/whitelist/{wallet}")
    async def check_whitelist(wallet: str):
        try:
            # Пока whitelist отключен - все имеют доступ
            return {
                "success": True,
                "hasAccess": True,
                "entryRequired": False
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @app.post("/api/auth")
    async def authenticate(request: AuthRequest):
        try:
            # Логируем запрос для отладки
            print(f"Auth request: wallet={request.wallet[:10]}..., message={request.message}, signature_length={len(request.signature)}")
            
            # Верифицируем подпись перед созданием/получением пользователя
            signature_valid = verify_solana_signature(request.wallet, request.message, request.signature)
            print(f"Signature verification result: {signature_valid}")
            
            # Проверка подписи ВКЛЮЧЕНА
            if not signature_valid:
                print(f"Signature verification failed for wallet: {request.wallet[:10]}...")
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": False,
                        "error": "Invalid signature. Authentication failed."
                    }
                )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем, существует ли пользователь
            cursor.execute("SELECT * FROM Users WHERE wallet = %s", (request.wallet,))
            user = cursor.fetchone()
            
            if user:
                # Пользователь существует, возвращаем его данные
                ref_code = user['ref_code']
            else:
                # Создаем нового пользователя
                ref_code = generate_ref_code()
                
                # Проверяем уникальность ref_code (на случай коллизии)
                while True:
                    cursor.execute("SELECT id_user FROM Users WHERE ref_code = %s", (ref_code,))
                    if cursor.fetchone() is None:
                        break
                    ref_code = generate_ref_code()
                
                # Вставляем нового пользователя
                cursor.execute(
                    "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING ref_code",
                    (request.wallet, ref_code)
                )
                conn.commit()
            
            cursor.close()
            conn.close()
            
            # Генерируем токен для cookie
            # Для простоты используем хеш wallet + ref_code как токен
            token_data = f"{request.wallet}:{ref_code}"
            token_hash = hashlib.sha256(token_data.encode()).hexdigest()
            
            print(f"Auth successful: wallet={request.wallet}, ref_code={ref_code}, token_hash={token_hash[:20]}...")
            
            # Создаем ответ с cookie
            response_data = {
                "success": True,
                "refCode": ref_code
            }
            
            print(f"Returning response: {response_data}")
            
            # Создаем JSONResponse с cookie
            response = JSONResponse(content=response_data)
            response.set_cookie(
                key="auth_token",
                value=token_hash,
                max_age=86400 * 7,  # 7 дней
                httponly=True,
                samesite="lax"
            )
            
            print(f"Cookie set: auth_token={token_hash[:20]}...")
            return response
        except Exception as e:
            print(f"Auth error: {e}")
            import traceback
            traceback.print_exc()
            error_response = {
                "success": False,
                "error": str(e)
            }
            print(f"Returning error response: {error_response}")
            return JSONResponse(status_code=200, content=error_response)
    
    @app.get("/api/user/{wallet}")
    async def get_user(wallet: str, request: Request):
        # Проверяем авторизацию через заголовки или cookie
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        # Если заголовки не предоставлены, проверяем cookie
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                try:
                    from core.sessions import verify_session_cookie
                    auth_data = await verify_session_cookie(request)
                    # Проверяем, что пользователь запрашивает свои данные
                    if auth_data["wallet"] != wallet:
                        raise HTTPException(
                            status_code=403,
                            detail="Access denied. You can only access your own data."
                        )
                    # Используем wallet из auth_data
                    wallet = auth_data["wallet"]
                except HTTPException:
                    raise
                except Exception as e:
                    print(f"Cookie auth failed in get_user: {e}")
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid session. Please reconnect your wallet."
                    )
            else:
                raise HTTPException(
                    status_code=401,
                    detail="Missing authentication headers or cookie."
                )
        else:
            # Проверяем через заголовки
            try:
                auth_data = await verify_auth(
                    x_wallet=x_wallet,
                    x_signature=x_signature,
                    x_message=x_message
                )
                if auth_data["wallet"] != wallet:
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied. You can only access your own data."
                    )
            except HTTPException:
                raise
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT * FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if user:
                return {
                    "success": True,
                    "user": {
                        "id_user": user['id_user'],
                        "wallet": user['wallet'],
                        "ref_code": user['ref_code'],
                        "create_at": str(user['create_at']) if user['create_at'] else None
                    }
                }
            else:
                return {
                    "success": False,
                    "error": "User not found"
                }
        except Exception as e:
            print(f"Get user error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    @app.get("/api/jackpot")
    async def get_jackpot():
        return {
            "success": True,
            "amount": 0,
            "timeLeft": 86400  # 24 часа в секундах
        }
    
    @app.get("/api/jackpot/last")
    async def get_last_jackpot():
        return {
            "success": True,
            "amount": 0,
            "winner": None
        }
    
    @app.get("/api/chests")
    async def get_chests():
        return {
            "success": True,
            "chests": []
        }
    
    @app.get("/api/config")
    async def get_config():
        return {
            "success": True,
            "rpcUrl": "https://api.mainnet-beta.solana.com",
            "merchant": "11111111111111111111111111111111"  # Заглушка
        }
    
    @app.get("/api/super-jackpot")
    async def get_super_jackpot():
        return {
            "success": True,
            "amount": 0
        }
    
    @app.get("/health")
    async def health_check():
        try:
            conn = get_db_connection()
            conn.close()
            return {"status": "ok", "database": "connected"}
        except Exception as e:
            return {"status": "ok", "database": "disconnected", "error": str(e)}

