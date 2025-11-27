"""
API роуты для работы с данными.
"""
from fastapi import HTTPException, Depends
from psycopg2.extras import RealDictCursor

from core.models import AuthRequest
from core.utils import get_db_connection, generate_ref_code
from core.auth import verify_auth


def setup_api_routes(app):
    """
    Настраивает API роуты.
    """
    
    @app.get("/api/whitelist/{wallet}")
    async def check_whitelist(wallet: str):
        """
        Проверка whitelist статуса пользователя.
        Пока возвращаем hasAccess=True для всех (whitelist отключен).
        """
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
        """
        Аутентификация пользователя по кошельку.
        Создает пользователя если его нет, возвращает реферальный код.
        """
        try:
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
            
            return {
                "success": True,
                "refCode": ref_code
            }
        except Exception as e:
            print(f"Auth error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    @app.get("/api/user/{wallet}")
    async def get_user(wallet: str, auth: dict = Depends(verify_auth)):
        """
        Получение информации о пользователе.
        ЗАЩИЩЕНО: только авторизованные пользователи могут получать данные.
        """
        # Дополнительная проверка: пользователь может получать только свои данные
        if auth["wallet"] != wallet:
            raise HTTPException(
                status_code=403,
                detail="Access denied. You can only access your own data."
            )
        
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
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        try:
            conn = get_db_connection()
            conn.close()
            return {"status": "ok", "database": "connected"}
        except Exception as e:
            return {"status": "ok", "database": "disconnected", "error": str(e)}

