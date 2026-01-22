from fastapi import HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
import psycopg2
import hashlib
import json
import os
import requests
import secrets
import re
from datetime import datetime, timedelta

from core.models import AuthRequest
from core.utils import get_db_connection, generate_ref_code, verify_solana_signature, verify_solana_transaction, HELIUS_RPC_URL, determine_card_rarity, get_random_card_by_rarity, get_or_create_active_round, add_to_jackpot, draw_jackpot, add_to_super_jackpot, claim_super_jackpot, get_or_create_active_super_jackpot_round, get_today_daily_code, get_user_checkin_status, process_daily_checkin, get_user_active_boost, issue_prediction_reward
from core.auth import verify_auth
from pydantic import BaseModel


def setup_api_routes(app):
    async def _ensure_authorized_user(request: Request, wallet: str):
        """
        Проверяет, что запрос авторизован и принадлежит указанному кошельку.
        Использует заголовки подписи или auth_token cookie.
        """
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                try:
                    from core.sessions import verify_session_cookie
                    auth_data = await verify_session_cookie(request)
                    if auth_data["wallet"] != wallet:
                        raise HTTPException(
                            status_code=403,
                            detail="Access denied. You can only access your own data."
                        )
                except HTTPException:
                    raise
                except Exception:
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

            require_challenge = os.getenv("AUTH_CHALLENGE_REQUIRED", "0").lower() in ("1", "true", "yes")
            challenge_re = re.compile(
                r"^Gamba Auth\s*\nWallet:\s*(\S+)\s*\nNonce:\s*([A-Za-z0-9_\-]+)\s*\nIssuedAt:\s*(\d{10,})\s*$"
            )
            m = challenge_re.match((request.message or "").strip())
            if require_challenge and not m:
                return JSONResponse(
                    status_code=200,
                    content={"success": False, "error": "Challenge required"}
                )

            from core.auth import _validate_auth_message
            _validate_auth_message(request.message)
            
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

            if m:
                msg_wallet = m.group(1)
                nonce = m.group(2)
                if msg_wallet != request.wallet:
                    cursor.close()
                    conn.close()
                    return JSONResponse(status_code=200, content={"success": False, "error": "Invalid challenge"})

                cursor.execute("DELETE FROM Auth_challenges WHERE expires_at < now()")
                cursor.execute(
                    "DELETE FROM Auth_challenges WHERE nonce = %s AND wallet = %s AND message = %s AND expires_at > now() RETURNING nonce",
                    (nonce, request.wallet, request.message.strip())
                )
                consumed = cursor.fetchone()
                if not consumed:
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    return JSONResponse(status_code=200, content={"success": False, "error": "Challenge expired or already used"})
                conn.commit()
            
            # Проверяем, существует ли пользователь
            cursor.execute("SELECT * FROM Users WHERE wallet = %s", (request.wallet,))
            user = cursor.fetchone()
            user_id = None
            is_new_user = False
            
            if user:
                # Пользователь существует, возвращаем его данные
                ref_code = user['ref_code']
                user_id = user['id_user']
            else:
                # Создаем нового пользователя
                is_new_user = True
                ref_code = generate_ref_code()
                
                # Проверяем уникальность ref_code (на случай коллизии)
                while True:
                    cursor.execute("SELECT id_user FROM Users WHERE ref_code = %s", (ref_code,))
                    if cursor.fetchone() is None:
                        break
                    ref_code = generate_ref_code()
                
                # Вставляем нового пользователя
                cursor.execute(
                    "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user, ref_code",
                    (request.wallet, ref_code)
                )
                new_user = cursor.fetchone()
                if new_user:
                    user_id = new_user['id_user']
                    ref_code = new_user['ref_code']
                conn.commit()
            
            # Фиксируем реферала, если новый пользователь пришел по ссылке
            if is_new_user and request.referrerCode:
                referral_code = request.referrerCode.strip().upper()
                if referral_code and referral_code != ref_code and user_id is not None:
                    cursor.execute("SELECT id_user FROM Users WHERE ref_code = %s", (referral_code,))
                    referrer = cursor.fetchone()
                    if referrer and referrer['id_user'] != user_id:
                        cursor.execute(
                            """
                            INSERT INTO Referral_system (id_referrer, id_referred)
                            VALUES (%s, %s)
                            ON CONFLICT (id_referred) DO NOTHING
                            """,
                            (referrer['id_user'], user_id)
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
                samesite=os.getenv("AUTH_COOKIE_SAMESITE", "lax"),
                secure=os.getenv("AUTH_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
            )
            
            print(f"Cookie set: auth_token={token_hash[:20]}...")
            return response
        except HTTPException as e:
            return JSONResponse(
                status_code=200,
                content={"success": False, "error": str(getattr(e, 'detail', 'Authentication failed'))}
            )
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

    class AuthChallengeRequest(BaseModel):
        wallet: str

    @app.post("/api/auth/challenge")
    async def auth_challenge(payload: AuthChallengeRequest):
        try:
            wallet = payload.wallet
            if not wallet:
                return JSONResponse(status_code=400, content={"success": False, "error": "Missing wallet"})

            ttl = int(os.getenv("AUTH_CHALLENGE_TTL_SECONDS", "120"))
            issued_at_ms = int(datetime.utcnow().timestamp() * 1000)
            nonce = secrets.token_urlsafe(16)
            message = f"Gamba Auth\nWallet: {wallet}\nNonce: {nonce}\nIssuedAt: {issued_at_ms}"
            expires_at = datetime.utcnow() + timedelta(seconds=ttl)

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("DELETE FROM Auth_challenges WHERE expires_at < now()")
            cursor.execute(
                "INSERT INTO Auth_challenges (nonce, wallet, message, expires_at) VALUES (%s, %s, %s, %s)",
                (nonce, wallet, message, expires_at)
            )
            conn.commit()
            cursor.close()
            conn.close()

            return {"success": True, "nonce": nonce, "message": message, "expiresAt": int(expires_at.timestamp() * 1000)}
        except Exception as e:
            print(f"Auth challenge error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"success": False, "error": "Failed to issue challenge"})
    
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
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем или создаем активный раунд
            round_data = get_or_create_active_round(cursor, conn)
            
            # Вычисляем оставшееся время
            from datetime import datetime
            now = datetime.now()
            ends_at = round_data['ends_at']
            if isinstance(ends_at, str):
                # Парсим строку в datetime
                try:
                    ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
                except:
                    # Fallback для других форматов
                    from datetime import datetime as dt
                    ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S.%f')
            
            time_left = max(0, int((ends_at - now).total_seconds()))
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "jackpot": float(round_data['total_amount']),
                "amount": float(round_data['total_amount']),
                "timeLeft": time_left,
                "endsAt": ends_at.isoformat() if hasattr(ends_at, 'isoformat') else str(ends_at)
            }
        except Exception as e:
            print(f"Get jackpot error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "jackpot": 0,
                "amount": 0,
                "timeLeft": 86400
            }
    
    @app.get("/api/jackpot/last")
    async def get_last_jackpot():
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем последний завершенный раунд
            cursor.execute("""
                SELECT jr.*, u.wallet
                FROM Jackpot_rounds jr
                LEFT JOIN Users u ON jr.winner_user_id = u.id_user
                WHERE jr.status = 'completed'
                ORDER BY jr.completed_at DESC
                LIMIT 1
            """)
            last_round = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            if last_round:
                return {
                    "success": True,
                    "lastJackpot": {
                        "amount": float(last_round['prize_amount']) if last_round['prize_amount'] else 0.0,
                        "winner": last_round['wallet'] if last_round['wallet'] else None,
                        "date": str(last_round['completed_at']) if last_round['completed_at'] else None
                    }
                }
            else:
                return {
                    "success": True,
                    "lastJackpot": None
                }
        except Exception as e:
            print(f"Get last jackpot error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "lastJackpot": None
            }
    
    @app.post("/api/jackpot/draw")
    async def draw_jackpot_endpoint():
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            drawn_rounds = draw_jackpot(cursor, conn)
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "drawn_rounds": drawn_rounds,
                "message": f"Processed {len(drawn_rounds)} round(s)"
            }
        except Exception as e:
            print(f"Draw jackpot error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "drawn_rounds": []
            }
    
    @app.get("/api/chests")
    async def get_chests():
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT DISTINCT ON (id_chest) id_chest, prob_common, prob_rare, prob_epic, prob_legendary, 
                       chance_loss, price, update_time
                FROM Chests
                ORDER BY id_chest, update_time DESC
            """)
            chests = cursor.fetchall()
            
            print(f"Found {len(chests)} chests in database")
            if len(chests) == 0:
                print("WARNING: No chests found in database! Check if insert.sql was executed.")
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "chests": [dict(chest) for chest in chests]
            }
        except Exception as e:
            print(f"Get chests error: {e}")
            return {
                "success": False,
                "error": str(e),
                "chests": []
            }
    
    @app.get("/api/user/{wallet}/chests")
    async def get_user_chests(wallet: str, request: Request):
        # Проверяем авторизацию
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                try:
                    from core.sessions import verify_session_cookie
                    auth_data = await verify_session_cookie(request)
                    if auth_data["wallet"] != wallet:
                        raise HTTPException(status_code=403, detail="Access denied")
                except Exception as e:
                    raise HTTPException(status_code=401, detail="Invalid session")
            else:
                raise HTTPException(status_code=401, detail="Authentication required")
        else:
            try:
                auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                if auth_data["wallet"] != wallet:
                    raise HTTPException(status_code=403, detail="Access denied")
            except HTTPException:
                raise
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем id_user
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return {"success": False, "error": "User not found", "chests": []}
            
            # Получаем паки пользователя
            cursor.execute("""
                SELECT cp.id_purchase, cp.id_chest, cp.created_at, cp.is_opened, cp.opened_at,
                       c.prob_common, c.prob_rare, c.prob_epic, c.prob_legendary, c.price
                FROM Chest_purchases cp
                JOIN Chests c ON cp.id_chest = c.id_chest
                WHERE cp.id_user = %s
                ORDER BY cp.created_at DESC
            """, (user['id_user'],))
            chests = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "chests": [dict(chest) for chest in chests]
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Get user chests error: {e}")
            return {
                "success": False,
                "error": str(e),
                "chests": []
            }
    
    @app.get("/api/balance/{wallet}")
    async def get_balance(wallet: str, request: Request):
        # Проверяем авторизацию
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                try:
                    from core.sessions import verify_session_cookie
                    auth_data = await verify_session_cookie(request)
                    if auth_data["wallet"] != wallet:
                        raise HTTPException(status_code=403, detail="Access denied")
                except Exception as e:
                    raise HTTPException(status_code=401, detail="Invalid session")
            else:
                raise HTTPException(status_code=401, detail="Authentication required")
        else:
            try:
                auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                if auth_data["wallet"] != wallet:
                    raise HTTPException(status_code=403, detail="Access denied")
            except HTTPException:
                raise
        
        # Получаем баланс токенов из Solana блокчейна
        from core.utils import HELIUS_RPC_URL
        
        mint = os.getenv("TOKEN_MINT", "")
        rpc_url = HELIUS_RPC_URL
        
        if not mint:
            # Если mint не настроен, возвращаем нулевой баланс
            return {
                "success": True,
                "balance": {
                    "amount": 0,
                    "decimals": 9,
                    "symbol": "TOKENS"
                }
            }
        
        try:
            # Получаем все токен аккаунты пользователя
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    wallet,
                    {
                        "mint": mint
                    },
                    {
                        "encoding": "jsonParsed"
                    }
                ]
            }
            
            response = requests.post(rpc_url, json=payload, timeout=10)
            if response.status_code != 200:
                raise Exception(f"RPC request failed: {response.status_code}")
            
            data = response.json()
            if "error" in data:
                raise Exception(f"RPC error: {data['error']}")
            
            # Извлекаем баланс из ответа
            balance_amount = 0
            decimals = 9  # Дефолтное значение
            
            if "result" in data and data["result"] and "value" in data["result"]:
                accounts = data["result"]["value"]
                if accounts and len(accounts) > 0:
                    # Берем первый аккаунт (обычно он один)
                    account_info = accounts[0].get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    if account_info:
                        # Получаем баланс в минимальных единицах (lamports для токенов)
                        token_amount = account_info.get("tokenAmount", {})
                        ui_amount = token_amount.get("uiAmount", 0)
                        decimals = token_amount.get("decimals", 9)
                        balance_amount = float(ui_amount) if ui_amount else 0
            
            return {
                "success": True,
                "balance": {
                    "amount": balance_amount,
                    "decimals": decimals,
                    "symbol": "TOKENS"
                }
            }
        except Exception as e:
            print(f"Error fetching token balance: {e}")
            # В случае ошибки возвращаем нулевой баланс
            return {
                "success": True,
                "balance": {
                    "amount": 0,
                    "decimals": 9,
                    "symbol": "TOKENS"
                }
            }
    
    @app.get("/api/user/{wallet}/cards")
    async def get_user_cards(wallet: str, request: Request):
        # Проверяем авторизацию
        x_wallet = request.headers.get("X-Wallet")
        x_signature = request.headers.get("X-Signature")
        x_message = request.headers.get("X-Message")
        
        if not x_wallet or not x_signature or not x_message:
            auth_token = request.cookies.get("auth_token")
            if auth_token:
                try:
                    from core.sessions import verify_session_cookie
                    auth_data = await verify_session_cookie(request)
                    if auth_data["wallet"] != wallet:
                        raise HTTPException(status_code=403, detail="Access denied")
                except Exception as e:
                    raise HTTPException(status_code=401, detail="Invalid session")
            else:
                raise HTTPException(status_code=401, detail="Authentication required")
        else:
            try:
                auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                if auth_data["wallet"] != wallet:
                    raise HTTPException(status_code=403, detail="Access denied")
            except HTTPException:
                raise
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем id_user
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return {"success": False, "error": "User not found", "cards": []}
            
            # Получаем карты пользователя
            cursor.execute("""
                SELECT c.id_card, c.rarity, c.start_bounty, c.name, c.image_url, c.image_key,
                       COALESCE(cu.quantity, 0) as quantity
                FROM Cards c
                LEFT JOIN Card_User cu ON c.id_card = cu.id_card AND cu.id_user = %s
                WHERE cu.quantity > 0 OR cu.id_user IS NULL
                ORDER BY c.rarity, c.id_card
            """, (user['id_user'],))
            cards = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "cards": [dict(card) for card in cards if card['quantity'] > 0]
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Get user cards error: {e}")
            return {
                "success": False,
                "error": str(e),
                "cards": []
            }
    
    @app.get("/api/cards")
    async def get_cards(rarity: str = None, hasImage: bool = None):
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = "SELECT id_card, rarity, start_bounty, name, image_url, image_key FROM Cards WHERE 1=1"
            params = []
            
            if rarity:
                query += " AND rarity = %s"
                params.append(rarity)
            
            if hasImage:
                query += " AND image_url IS NOT NULL AND image_url != ''"
            
            query += " ORDER BY rarity, id_card"
            
            cursor.execute(query, params)
            cards = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "cards": [dict(card) for card in cards]
            }
        except Exception as e:
            print(f"Get cards error: {e}")
            return {
                "success": False,
                "error": str(e),
                "cards": []
            }
    
    @app.get("/api/config")
    async def get_config():
        """Получить конфигурацию для клиента"""
        import os
        merchant = os.getenv("MERCHANT_WALLET", "")
        mint = os.getenv("TOKEN_MINT", "")
        rpc_url = HELIUS_RPC_URL
        
        # Проверяем, что все необходимые переменные установлены
        if not merchant or merchant == "11111111111111111111111111111111":
            print("WARNING: MERCHANT_WALLET not configured or using default value")
        if not mint:
            print("WARNING: TOKEN_MINT not configured")
        if not rpc_url or rpc_url == "https://api.mainnet-beta.solana.com":
            print("WARNING: HELIUS_RPC_URL not configured, using default Solana RPC")
        
        return {
            "success": True,
            "rpcUrl": rpc_url,  # Используем Helius RPC
            "merchant": merchant if merchant else "11111111111111111111111111111111",
            "mint": mint  # Адрес токена TOKENS
        }
    
    @app.get("/api/super-jackpot")
    async def get_super_jackpot():
        """Получить информацию о текущем супер джекпоте"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            round_data = get_or_create_active_super_jackpot_round(cursor, conn)
            
            # Проверяем, есть ли уже победитель
            winner_info = None
            if round_data['winner_user_id']:
                cursor.execute("""
                    SELECT id_user, wallet
                    FROM Users
                    WHERE id_user = %s
                """, (round_data['winner_user_id'],))
                winner = cursor.fetchone()
                if winner:
                    winner_info = {
                        "wallet": winner['wallet']
                    }
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "amount": float(round_data['total_amount']) if round_data['total_amount'] else 0.0,
                "winner": winner_info,
                "round_id": round_data['id_round']
            }
        except Exception as e:
            print(f"Get super jackpot error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "amount": 0
            }
    
    @app.post("/api/cards/trade")
    async def trade_cards(request: Request):
        """Обмен карт: несколько карт одной редкости на одну карту той же редкости"""
        try:
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        x_wallet = auth_data["wallet"]
                    except Exception as e:
                        return JSONResponse(
                            status_code=401,
                            content={"success": False, "error": "Invalid session"}
                        )
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "Authentication required"}
                    )
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                except HTTPException as e:
                    return JSONResponse(
                        status_code=e.status_code,
                        content={"success": False, "error": e.detail}
                    )
            
            body = await request.json()
            wallet = body.get("wallet")
            cards = body.get("cards", [])  # [{ id_card, quantity }, ...]
            rarity = body.get("rarity")  # basic, rare, epic
            
            if not wallet or not cards or not rarity:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, cards, rarity"}
                )
            
            if rarity not in ['basic', 'rare', 'epic']:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Invalid rarity. Only basic, rare, and epic are allowed"}
                )
            
            # Правила обмена
            req_counts = {'basic': 4, 'rare': 3, 'epic': 2}
            required_count = req_counts.get(rarity)
            
            if not required_count:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Invalid rarity for trading"}
                )
            
            # Проверяем общее количество карт
            total_quantity = sum(c.get('quantity', 0) for c in cards)
            if total_quantity != required_count:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": f"Need exactly {required_count} cards of {rarity} rarity, got {total_quantity}"}
                )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем пользователя
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "User not found"}
                )
            
            user_id = user['id_user']
            
            # Проверяем, что у пользователя достаточно карт и они правильной редкости
            for card_data in cards:
                id_card = card_data.get('id_card')
                quantity = card_data.get('quantity', 0)
                
                if not id_card or quantity <= 0:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Invalid card data"}
                    )
                
                # Проверяем карту и её редкость
                cursor.execute("""
                    SELECT c.rarity, COALESCE(cu.quantity, 0) as user_quantity
                    FROM Cards c
                    LEFT JOIN Card_User cu ON c.id_card = cu.id_card AND cu.id_user = %s
                    WHERE c.id_card = %s
                """, (user_id, id_card))
                card_info = cursor.fetchone()
                
                if not card_info:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=404,
                        content={"success": False, "error": f"Card {id_card} not found"}
                    )
                
                if card_info['rarity'] != rarity:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": f"Card {id_card} is not {rarity} rarity"}
                    )
                
                if card_info['user_quantity'] < quantity:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": f"Not enough cards. You have {card_info['user_quantity']}, need {quantity}"}
                    )
            
            # Удаляем карты (уменьшаем quantity)
            for card_data in cards:
                id_card = card_data.get('id_card')
                quantity = card_data.get('quantity', 0)
                
                cursor.execute("""
                    UPDATE Card_User
                    SET quantity = quantity - %s
                    WHERE id_user = %s AND id_card = %s
                """, (quantity, user_id, id_card))
                
                # Если quantity стала 0, удаляем запись
                cursor.execute("""
                    DELETE FROM Card_User
                    WHERE id_user = %s AND id_card = %s AND quantity <= 0
                """, (user_id, id_card))
            
            # Получаем список id_card, которые обмениваются (исключаем их из выборки)
            traded_card_ids = [c.get('id_card') for c in cards]
            
            # Получаем случайную карту той же редкости, исключая обмениваемые карты
            new_card = get_random_card_by_rarity(rarity, cursor, exclude_card_ids=traded_card_ids)
            
            if not new_card:
                conn.rollback()
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"No {rarity} cards available in the system (excluding traded cards)"}
                )
            
            new_card_id = new_card['id_card']
            
            # Добавляем новую карту пользователю (гарантированно другая карта)
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
            """, (user_id, new_card_id))
            
            # Сохраняем историю трейда
            import json
            cursor.execute("""
                INSERT INTO Card_trades (id_user, traded_cards, received_card_id, rarity)
                VALUES (%s, %s, %s, %s)
            """, (user_id, json.dumps(cards), new_card_id, rarity))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "card": {
                    "id_card": new_card['id_card'],
                    "name": new_card.get('name'),
                    "rarity": new_card.get('rarity'),
                    "image_url": new_card.get('image_url')
                },
                "message": f"Successfully traded {required_count} {rarity} cards for 1 {rarity} card"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Trade cards error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )

    @app.post("/api/predictions/claim/{bet_id}")
    async def claim_prediction_reward(bet_id: int, request: Request):
        """Отметить награду по выигранной ставке как забранную (идемпотентно)."""
        try:
            # Берем wallet из middleware (или fallback на заголовки)
            auth_wallet = None
            if request and hasattr(request.state, 'user'):
                auth_wallet = request.state.user.get('wallet')

            if not auth_wallet:
                x_wallet = request.headers.get("X-Wallet")
                x_signature = request.headers.get("X-Signature")
                x_message = request.headers.get("X-Message")
                if x_wallet and x_signature and x_message:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    auth_wallet = auth_data.get("wallet")

            if not auth_wallet:
                return JSONResponse(status_code=401, content={"success": False, "error": "Unauthorized"})

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Получаем id_user по wallet
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (auth_wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(status_code=401, content={"success": False, "error": "Unauthorized"})
            user_id = user['id_user']

            # Проверяем ставку и ownership
            cursor.execute("""
                SELECT id_bet, id_user, status, reward_issued, reward_claimed
                FROM public.user_bets
                WHERE id_bet = %s
            """, (bet_id,))
            bet = cursor.fetchone()
            if not bet:
                cursor.close()
                conn.close()
                return JSONResponse(status_code=404, content={"success": False, "error": "Bet not found"})

            if bet['id_user'] != user_id:
                cursor.close()
                conn.close()
                return JSONResponse(status_code=403, content={"success": False, "error": "Forbidden"})

            # Разрешаем claim только если ставка выиграна и награда была выдана
            if bet.get('status') != 'won' or not bet.get('reward_issued'):
                cursor.close()
                conn.close()
                return JSONResponse(status_code=400, content={"success": False, "error": "Reward not available"})

            already_claimed = bool(bet.get('reward_claimed'))
            if not already_claimed:
                cursor.execute("""
                    UPDATE public.user_bets
                    SET reward_claimed = TRUE,
                        reward_claimed_at = now()
                    WHERE id_bet = %s
                """, (bet_id,))
                conn.commit()

            cursor.close()
            conn.close()

            return {
                "success": True,
                "bet_id": bet_id,
                "already_claimed": already_claimed
            }

        except HTTPException:
            raise
        except Exception as e:
            print(f"Claim prediction reward error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"success": False, "error": f"Internal error: {str(e)}"})
    
    @app.post("/api/chests/buy")
    async def buy_chest(request: Request):
        """Покупка пака с проверкой транзакции"""
        try:
            # Получаем данные из запроса
            body = await request.json()
            wallet = body.get("wallet")
            id_chest = body.get("id_chest")
            tx_signature = body.get("txSignature")
            quantity = body.get("quantity", 1)  # По умолчанию 1 пак
            
            # Валидация количества
            try:
                quantity = int(quantity)
                if quantity < 1 or quantity > 100:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Quantity must be between 1 and 100"}
                    )
            except (ValueError, TypeError):
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Invalid quantity"}
                )
            
            if not wallet or not id_chest or not tx_signature:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, id_chest, txSignature"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(
                                status_code=403,
                                content={"success": False, "error": "Access denied"}
                            )
                    except Exception as e:
                        return JSONResponse(
                            status_code=401,
                            content={"success": False, "error": "Invalid session"}
                        )
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "Authentication required"}
                    )
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(
                            status_code=403,
                            content={"success": False, "error": "Access denied"}
                        )
                except HTTPException as e:
                    return JSONResponse(
                        status_code=e.status_code,
                        content={"success": False, "error": e.detail}
                    )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем, что транзакция не использовалась ранее
            cursor.execute(
                "SELECT id_purchase FROM Chest_purchases WHERE tx_signature = %s OR tx_signature LIKE %s",
                (tx_signature, f"{tx_signature}_%")
            )
            existing = cursor.fetchone()
            if existing:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Transaction already used"}
                )
            
            # Получаем информацию о паке
            cursor.execute("SELECT * FROM Chests WHERE id_chest = %s", (id_chest,))
            chest = cursor.fetchone()
            if not chest:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Chest not found"}
                )
            
            # Получаем пользователя
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "User not found"}
                )
            
            # Получаем конфигурацию для проверки транзакции
            import os
            merchant = os.getenv("MERCHANT_WALLET", "11111111111111111111111111111111")  # Fallback для тестов
            mint = os.getenv("TOKEN_MINT", "")
            
            # Верифицируем транзакцию на блокчейне через Helius RPC
            price = float(chest['price'])
            total_price = price * quantity  # Общая сумма за все паки
            
            tx_verification = verify_solana_transaction(
                tx_signature=tx_signature,
                expected_sender=wallet,
                expected_receiver=merchant,
                expected_amount=total_price,  # Проверяем общую сумму
                rpc_url=HELIUS_RPC_URL,  # Используем Helius RPC
                mint_address=mint if mint else None
            )
            
            if not tx_verification.get("valid"):
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": f"Transaction verification failed: {tx_verification.get('error', 'Unknown error')}"
                    }
                )
            
            # Создаем записи о покупке для каждого пака
            purchase_ids = []
            for i in range(quantity):
                # Для нескольких паков используем уникальный tx_signature с индексом
                # Это позволяет отслеживать каждую покупку отдельно
                unique_tx_sig = f"{tx_signature}_{i}" if quantity > 1 else tx_signature

                try:
                    cursor.execute("""
                        INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
                        VALUES (%s, %s, %s)
                        RETURNING id_purchase
                    """, (user['id_user'], id_chest, unique_tx_sig))
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Transaction already used"}
                    )

                purchase = cursor.fetchone()
                if purchase:
                    purchase_ids.append(purchase['id_purchase'])
            
            # Добавляем 40% от общей суммы в джекпот
            jackpot_contribution = total_price * 0.4
            add_to_jackpot(cursor, conn, jackpot_contribution)
            
            # Добавляем 10% от общей суммы в супер джекпот
            super_jackpot_contribution = total_price * 0.1
            add_to_super_jackpot(cursor, conn, super_jackpot_contribution)

            # Реферальная программа: 10% от суммы покупок приглашенного пользователя
            # (начисляем как запись в Referral_rewards, если пользователь был приглашен)
            cursor.execute("""
                SELECT id_referrer
                FROM Referral_system
                WHERE id_referred = %s
            """, (user['id_user'],))
            referral_row = cursor.fetchone()
            if referral_row and purchase_ids:
                referrer_id = referral_row['id_referrer']
                referral_per_purchase = price * 0.1
                for pid in purchase_ids:
                    cursor.execute("""
                        INSERT INTO Referral_rewards (id_referrer, id_referred, id_purchase, amount)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id_purchase) DO NOTHING
                    """, (referrer_id, user['id_user'], pid, referral_per_purchase))
            
            conn.commit()
            
            cursor.close()
            conn.close()
            
            result = {
                "success": True,
                "purchase_ids": purchase_ids,
                "quantity": quantity,
                "message": f"Successfully purchased {quantity} pack{'s' if quantity > 1 else ''}"
            }
            
            # Для обратной совместимости: если quantity = 1, добавляем purchase_id
            if quantity == 1 and len(purchase_ids) > 0:
                result["purchase_id"] = purchase_ids[0]
            
            return result
            
        except Exception as e:
            print(f"Buy chest error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/chests/open")
    async def open_chest(request: Request):
        try:
            # Получаем данные из запроса
            body = await request.json()
            wallet = body.get("wallet")
            id_purchase = body.get("id_purchase")
            
            if not wallet or not id_purchase:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, id_purchase"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(
                                status_code=403,
                                content={"success": False, "error": "Access denied"}
                            )
                    except Exception as e:
                        return JSONResponse(
                            status_code=401,
                            content={"success": False, "error": "Invalid session"}
                        )
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "Authentication required"}
                    )
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(
                            status_code=403,
                            content={"success": False, "error": "Access denied"}
                        )
                except HTTPException as e:
                    return JSONResponse(
                        status_code=e.status_code,
                        content={"success": False, "error": e.detail}
                    )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем, что покупка существует и принадлежит пользователю
            cursor.execute("""
                SELECT cp.id_purchase, cp.id_user, cp.id_chest, cp.is_opened, cp.opened_at,
                       u.wallet, c.prob_common, c.prob_rare, c.prob_epic, c.prob_legendary, c.chance_loss
                FROM Chest_purchases cp
                JOIN Users u ON cp.id_user = u.id_user
                JOIN Chests c ON cp.id_chest = c.id_chest
                WHERE cp.id_purchase = %s AND u.wallet = %s
                FOR UPDATE OF cp
            """, (id_purchase, wallet))
            
            purchase_data = cursor.fetchone()
            if not purchase_data:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Purchase not found or access denied"}
                )
            
            # Проверяем, что пак еще не открыт
            if purchase_data['is_opened']:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Pack already opened"}
                )
            
            # Определяем редкость карты на основе вероятностей
            rarity = determine_card_rarity(
                prob_common=purchase_data['prob_common'],
                prob_rare=purchase_data['prob_rare'],
                prob_epic=purchase_data['prob_epic'],
                prob_legendary=purchase_data['prob_legendary'],
                chance_loss=purchase_data['chance_loss']
            )
            
            # Если выпал loss, просто отмечаем пак как открытый
            if rarity == 'loss':
                cursor.execute("""
                    UPDATE Chest_purchases
                    SET is_opened = TRUE, opened_at = NOW()
                    WHERE id_purchase = %s AND is_opened = FALSE
                    RETURNING id_purchase
                """, (id_purchase,))
                updated = cursor.fetchone()
                if not updated:
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Pack already opened"}
                    )
                
                # Записываем в Chest_openings
                cursor.execute("""
                    INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
                    SELECT %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM Chest_openings WHERE id_purchase = %s
                    )
                """, (id_purchase, purchase_data['id_user'], purchase_data['id_chest'], id_purchase))
                
                conn.commit()
                cursor.close()
                conn.close()
                
                return {
                    "success": True,
                    "lost": True,
                    "rarity": None,
                    "message": "Nothing dropped"
                }
            
            # Получаем случайную карту с нужной редкостью
            card = get_random_card_by_rarity(rarity, cursor)
            if not card:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"No cards found with rarity: {rarity}"}
                )
            
            # Отмечаем пак как открытый
            cursor.execute("""
                UPDATE Chest_purchases
                SET is_opened = TRUE, opened_at = NOW()
                WHERE id_purchase = %s AND is_opened = FALSE
                RETURNING id_purchase
            """, (id_purchase,))
            updated = cursor.fetchone()
            if not updated:
                conn.rollback()
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Pack already opened"}
                )
            
            # Записываем в Chest_openings и получаем id_opening
            cursor.execute("""
                INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
                SELECT %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM Chest_openings WHERE id_purchase = %s
                )
                RETURNING id_opening
            """, (id_purchase, purchase_data['id_user'], purchase_data['id_chest'], id_purchase))
            opening_data = cursor.fetchone()
            if opening_data and opening_data.get('id_opening') is not None:
                id_opening = opening_data['id_opening']
            else:
                cursor.execute(
                    "SELECT id_opening FROM Chest_openings WHERE id_purchase = %s",
                    (id_purchase,)
                )
                existing_opening = cursor.fetchone()
                id_opening = existing_opening['id_opening'] if existing_opening else None
            
            # Добавляем карту пользователю (или увеличиваем quantity если уже есть)
            # Связываем карту с открытием пака через id_opening
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT (id_user, id_card) 
                DO UPDATE SET quantity = Card_User.quantity + 1,
                              id_opening = COALESCE(Card_User.id_opening, EXCLUDED.id_opening)
            """, (purchase_data['id_user'], card['id_card'], id_opening))
            
            # Проверяем супер джекпот: собрал ли пользователь все карты?
            super_jackpot_result = claim_super_jackpot(cursor, conn, purchase_data['id_user'])
            
            conn.commit()
            
            response_data = {
                "success": True,
                "lost": False,
                "rarity": rarity,
                "card_id": card['id_card'],
                "card_name": card.get('name', ''),
                "image_url": card.get('image_url', ''),
                "start_bounty": card['start_bounty']
            }
            
            # Если выиграл супер джекпот, добавляем информацию в ответ
            if super_jackpot_result.get("won"):
                response_data["super_jackpot"] = {
                    "won": True,
                    "prize": float(super_jackpot_result["prize"])
                }
            
            cursor.close()
            conn.close()
            
            return response_data
            
        except Exception as e:
            print(f"Open chest error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.get("/api/daily-checkin/status/{wallet}")
    async def get_daily_checkin_status(wallet: str, request: Request):
        """Получить статус ежедневного чекина"""
        await _ensure_authorized_user(request, wallet)
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Получаем код для сегодня
            today_code = get_today_daily_code(cursor)
            # Коммитим, если код был сгенерирован
            conn.commit()
            
            # Получаем статус чекина
            status = get_user_checkin_status(cursor, user['id_user'])
            
            # Получаем активный boost
            boost = get_user_active_boost(cursor, user['id_user'])
            
            return {
                "success": True,
                "today_code": today_code,
                "checked_in_today": status["checked_in_today"],
                "consecutive_days": status["consecutive_days"],
                "can_claim_reward": status["can_claim_reward"],
                "boost": boost
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Get daily checkin status error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Failed to load checkin status"}
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @app.get("/api/referral/rewards/{wallet}")
    async def get_referral_rewards(wallet: str, request: Request, limit: int = 50):
        await _ensure_authorized_user(request, wallet)
        conn = None
        cursor = None
        try:
            limit = max(1, min(int(limit), 200))

            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM Referral_rewards WHERE id_referrer = %s",
                (user["id_user"],)
            )
            total_row = cursor.fetchone()
            total_earned = float(total_row["total"]) if total_row and total_row.get("total") is not None else 0.0

            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM Referral_rewards WHERE id_referrer = %s AND claimed_at IS NULL",
                (user["id_user"],)
            )
            available_row = cursor.fetchone()
            available_to_claim = float(available_row["total"]) if available_row and available_row.get("total") is not None else 0.0

            cursor.execute("""
                SELECT rr.id_reward, rr.id_purchase, rr.amount, rr.created_at,
                       u.wallet AS referred_wallet
                FROM Referral_rewards rr
                JOIN Users u ON u.id_user = rr.id_referred
                WHERE rr.id_referrer = %s
                ORDER BY rr.created_at DESC, rr.id_reward DESC
                LIMIT %s
            """, (user["id_user"], limit))
            rewards = cursor.fetchall() or []

            normalized = []
            for r in rewards:
                d = dict(r)
                if d.get("created_at") is not None:
                    try:
                        d["created_at"] = d["created_at"].isoformat()
                    except Exception:
                        pass
                if d.get("amount") is not None:
                    d["amount"] = float(d["amount"])
                normalized.append(d)

            return {
                "success": True,
                "totalEarned": total_earned,
                "availableToClaim": available_to_claim,
                "count": len(normalized),
                "rewards": normalized
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Referral rewards error: {e}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Failed to load referral rewards"}
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    @app.post("/api/referral/claim/{wallet}")
    async def claim_referral_rewards(wallet: str, request: Request):
        await _ensure_authorized_user(request, wallet)
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM Referral_rewards WHERE id_referrer = %s AND claimed_at IS NULL",
                (user["id_user"],)
            )
            row = cursor.fetchone()
            claimed_amount = float(row["total"]) if row and row.get("total") is not None else 0.0

            if claimed_amount > 0:
                cursor.execute(
                    "UPDATE Referral_rewards SET claimed_at = now() WHERE id_referrer = %s AND claimed_at IS NULL",
                    (user["id_user"],)
                )
                conn.commit()

            return {
                "success": True,
                "claimed": claimed_amount
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Referral claim error: {e}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Failed to claim referral rewards"}
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    @app.post("/api/daily-checkin/checkin/{wallet}")
    async def daily_checkin(wallet: str, request: Request):
        """Выполнить ежедневный чекин"""
        await _ensure_authorized_user(request, wallet)
        conn = None
        cursor = None
        try:
            body = await request.json()
            daily_code = body.get("daily_code")
            
            if not daily_code:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "daily_code is required"}
                )
            
            # Базовая валидация на уровне API (дополнительная проверка перед вызовом process_daily_checkin)
            if not isinstance(daily_code, str):
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "daily_code must be a string"}
                )
            
            # Нормализуем код (убираем пробелы, приводим к верхнему регистру)
            daily_code_normalized = daily_code.strip().upper()
            
            # Проверка длины
            if len(daily_code_normalized) != 8:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Daily code must be exactly 8 characters long"}
                )
            
            # Проверка формата (только A-Z и 0-9, без спецсимволов)
            import re
            if not re.match(r'^[A-Z0-9]{8}$', daily_code_normalized):
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Daily code must contain only uppercase letters (A-Z) and digits (0-9), no special characters allowed"}
                )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Обрабатываем чекин
            result = process_daily_checkin(cursor, conn, user['id_user'], daily_code)
            
            if not result.get("success"):
                error_msg = result.get("error", "Check-in failed")
                # Улучшаем сообщения об ошибках для пользователя
                if "Invalid daily code" in error_msg:
                    error_msg = "Invalid code. Please check the code from Twitter and try again."
                elif "Already checked in" in error_msg:
                    error_msg = "You have already checked in today!"
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": error_msg
                    }
                )
            
            return {
                "success": True,
                "consecutive_days": result["consecutive_days"],
                "reward_issued": result["reward_issued"],
                "rewards": result.get("rewards", [])
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Daily checkin error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    @app.get("/api/referral/summary/{wallet}")
    async def get_referral_summary(wallet: str, request: Request):
        await _ensure_authorized_user(request, wallet)
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT id_user, ref_code FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            
            cursor.execute(
                "SELECT COUNT(*) FROM Referral_system WHERE id_referrer = %s",
                (user['id_user'],)
            )
            ref_row = cursor.fetchone()
            referral_count = int(ref_row['count']) if ref_row and ref_row['count'] else 0
            
            return {
                "success": True,
                "referrals": referral_count,
                "refCode": user['ref_code']
            }
        except HTTPException:
            raise
        except Exception as e:
            print(f"Referral summary error: {e}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "Failed to load referral summary"}
            )
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    @app.post("/api/battle/start")
    async def start_battle(request: Request):
        """Начать поиск противника для батла"""
        try:
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        x_wallet = auth_data["wallet"]
                    except Exception as e:
                        return JSONResponse(
                            status_code=401,
                            content={"success": False, "error": "Invalid session"}
                        )
                else:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "Authentication required"}
                    )
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                except HTTPException as e:
                    return JSONResponse(
                        status_code=e.status_code,
                        content={"success": False, "error": e.detail}
                    )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем id_user
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (x_wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "User not found"}
                )
            
            user_id = user['id_user']
            
            # Проверяем, есть ли у пользователя карты
            cursor.execute("""
                SELECT COUNT(*) as card_count
                FROM Card_User
                WHERE id_user = %s AND quantity > 0
            """, (user_id,))
            card_count = cursor.fetchone()
            
            if not card_count or card_count['card_count'] == 0:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "You don't have any cards. You need at least one card to participate in battles."
                    }
                )
            
            # Проверяем, есть ли активный батл (исключаем отмененные и завершенные)
            cursor.execute("""
                SELECT id_battle, status FROM Battles 
                WHERE id_user = %s
                AND status IN ('searching', 'card_selection', 'fighting')
                ORDER BY started_at DESC
                LIMIT 1
            """, (user_id,))
            active_battle = cursor.fetchone()
            
            if active_battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "error": "You already have an active battle",
                        "battle_id": active_battle['id_battle'],
                        "status": active_battle['status']
                    }
                )
            
            # Создаем новый батл
            cursor.execute("""
                INSERT INTO Battles (id_user, status)
                VALUES (%s, 'searching')
                RETURNING id_battle, started_at
            """, (user_id,))
            battle = cursor.fetchone()
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Генерируем случайное время поиска (30-60 секунд)
            import random
            search_duration = random.randint(30, 60)
            
            return {
                "success": True,
                "battle_id": battle['id_battle'],
                "status": "searching",
                "search_duration": search_duration,
                "started_at": str(battle['started_at'])
            }
            
        except Exception as e:
            print(f"Start battle error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/battle/cancel")
    async def cancel_battle(request: Request):
        """Отменить активный батл"""
        try:
            body = await request.json()
            wallet = body.get("wallet")
            battle_id = body.get("battle_id")
            
            if not wallet or not battle_id:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, battle_id"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                    except Exception as e:
                        return JSONResponse(status_code=401, content={"success": False, "error": "Invalid session"})
                else:
                    return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                except HTTPException as e:
                    return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем батл
            cursor.execute("""
                SELECT b.*, u.id_user
                FROM Battles b
                JOIN Users u ON b.id_user = u.id_user
                WHERE b.id_battle = %s AND u.wallet = %s
            """, (battle_id, wallet))
            battle = cursor.fetchone()
            
            if not battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Battle not found"}
                )
            
            # Можно отменить только если батл еще не завершен
            if battle['status'] == 'completed':
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Cannot cancel completed battle"}
                )
            
            # Отменяем батл
            cursor.execute("""
                UPDATE Battles
                SET status = 'cancelled'
                WHERE id_battle = %s
            """, (battle_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "battle_id": battle_id,
                "status": "cancelled"
            }
            
        except Exception as e:
            print(f"Cancel battle error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/battle/finish-search")
    async def finish_battle_search(request: Request):
        """Завершить поиск противника и перейти к выбору карт"""
        try:
            body = await request.json()
            wallet = body.get("wallet")
            battle_id = body.get("battle_id")
            
            if not wallet or not battle_id:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, battle_id"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                    except Exception as e:
                        return JSONResponse(status_code=401, content={"success": False, "error": "Invalid session"})
                else:
                    return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                except HTTPException as e:
                    return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем батл
            cursor.execute("""
                SELECT b.*, u.id_user
                FROM Battles b
                JOIN Users u ON b.id_user = u.id_user
                WHERE b.id_battle = %s AND u.wallet = %s
            """, (battle_id, wallet))
            battle = cursor.fetchone()
            
            if not battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Battle not found"}
                )
            
            if battle['status'] != 'searching':
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": f"Battle is in {battle['status']} status, not searching"}
                )
            
            # Переводим в статус выбора карт
            cursor.execute("""
                UPDATE Battles
                SET status = 'card_selection'
                WHERE id_battle = %s
            """, (battle_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "battle_id": battle_id,
                "status": "card_selection"
            }
            
        except Exception as e:
            print(f"Finish battle search error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/battle/select-cards")
    async def select_battle_cards(request: Request):
        """Выбрать карты для батла"""
        try:
            body = await request.json()
            wallet = body.get("wallet")
            battle_id = body.get("battle_id")
            cards = body.get("cards", [])  # [{id_card, quantity}, ...]
            
            if not wallet or not battle_id or not cards:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, battle_id, cards"}
                )
            
            if len(cards) == 0 or len(cards) > 5:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "You must select 1-5 cards"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                    except Exception as e:
                        return JSONResponse(status_code=401, content={"success": False, "error": "Invalid session"})
                else:
                    return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                except HTTPException as e:
                    return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем батл
            cursor.execute("""
                SELECT b.*, u.id_user
                FROM Battles b
                JOIN Users u ON b.id_user = u.id_user
                WHERE b.id_battle = %s AND u.wallet = %s
            """, (battle_id, wallet))
            battle = cursor.fetchone()
            
            if not battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Battle not found"}
                )
            
            if battle['status'] != 'card_selection':
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": f"Battle is in {battle['status']} status, cannot select cards"}
                )
            
            # Проверяем, что у пользователя есть все выбранные карты
            user_id = battle['id_user']
            total_tickets = 0
            
            for card_data in cards:
                id_card = card_data.get('id_card')
                quantity = card_data.get('quantity', 1)
                
                if not id_card or quantity <= 0:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Invalid card data"}
                    )
                
                # Проверяем наличие карты
                cursor.execute("""
                    SELECT cu.quantity as user_quantity, c.start_bounty
                    FROM Card_User cu
                    JOIN Cards c ON cu.id_card = c.id_card
                    WHERE cu.id_user = %s AND cu.id_card = %s
                """, (user_id, id_card))
                card_info = cursor.fetchone()
                
                if not card_info or card_info['user_quantity'] < quantity:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": f"Not enough cards. Card {id_card}: have {card_info['user_quantity'] if card_info else 0}, need {quantity}"}
                    )
                
                total_tickets += card_info['start_bounty'] * quantity
            
            # Сохраняем выбранные карты
            import json
            cursor.execute("""
                UPDATE Battles
                SET user_cards = %s, user_tickets = %s, status = 'fighting'
                WHERE id_battle = %s
            """, (json.dumps(cards), total_tickets, battle_id))
            
            # Генерируем карты для противника (1-5 карт, сумма билетов 100-400)
            import random
            opponent_tickets_target = random.randint(100, 400)
            opponent_tickets = 0
            
            # Получаем случайные карты из БД с полной информацией
            cursor.execute("""
                SELECT id_card, start_bounty, rarity, name, image_url, image_key
                FROM Cards
                WHERE image_url IS NOT NULL AND image_url != ''
                ORDER BY RANDOM()
                LIMIT 50
            """)
            available_cards = cursor.fetchall()
            
            # Выбираем карты для противника, чтобы сумма была близка к цели
            selected_opponent_cards = {}
            attempts = 0
            num_opponent_cards = random.randint(1, 5)  # Случайное количество карт
            
            while opponent_tickets < opponent_tickets_target and attempts < 100 and len(selected_opponent_cards) < num_opponent_cards:
                card = random.choice(available_cards)
                card_id = card['id_card']
                card_tickets = card['start_bounty']
                
                if opponent_tickets + card_tickets <= opponent_tickets_target + 100:  # Допуск
                    if card_id not in selected_opponent_cards:
                        selected_opponent_cards[card_id] = {
                            'id_card': card_id,
                            'quantity': 1,
                            'start_bounty': card_tickets,
                            'name': card.get('name'),
                            'image_url': card.get('image_url'),
                            'rarity': card.get('rarity')
                        }
                        opponent_tickets += card_tickets
                attempts += 1
            
            # Если не набрали достаточно, добавляем еще карты до минимума 100
            if opponent_tickets < 100:
                for card in available_cards:
                    if card['id_card'] not in selected_opponent_cards and len(selected_opponent_cards) < 5:
                        selected_opponent_cards[card['id_card']] = {
                            'id_card': card['id_card'],
                            'quantity': 1,
                            'start_bounty': card['start_bounty'],
                            'name': card.get('name'),
                            'image_url': card.get('image_url'),
                            'rarity': card.get('rarity')
                        }
                        opponent_tickets += card['start_bounty']
                        if opponent_tickets >= 100:
                            break
                
                # Если все еще меньше 100, добавляем карты без ограничения по количеству
                if opponent_tickets < 100:
                    for card in available_cards:
                        if card['id_card'] not in selected_opponent_cards:
                            selected_opponent_cards[card['id_card']] = {
                                'id_card': card['id_card'],
                                'quantity': 1,
                                'start_bounty': card['start_bounty'],
                                'name': card.get('name'),
                                'image_url': card.get('image_url'),
                                'rarity': card.get('rarity')
                            }
                            opponent_tickets += card['start_bounty']
                            if opponent_tickets >= 100:
                                break
                
                # Если все еще меньше 100 (мало карт в БД), устанавливаем минимум 100
                if opponent_tickets < 100:
                    opponent_tickets = 100
            
            opponent_cards_list = list(selected_opponent_cards.values())
            
            # Обновляем батл с картами противника (внутреннее название bot_cards остается для БД)
            cursor.execute("""
                UPDATE Battles
                SET bot_cards = %s, bot_tickets = %s
                WHERE id_battle = %s
            """, (json.dumps(opponent_cards_list), opponent_tickets, battle_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "battle_id": battle_id,
                "user_cards": cards,
                "user_tickets": total_tickets,
                "opponent_tickets": opponent_tickets,
                "status": "fighting"
            }
            
        except Exception as e:
            print(f"Select battle cards error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/battle/fight")
    async def fight_battle(request: Request):
        """Провести бой и определить победителя"""
        try:
            body = await request.json()
            wallet = body.get("wallet")
            battle_id = body.get("battle_id")
            
            if not wallet or not battle_id:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing required fields: wallet, battle_id"}
                )
            
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        if auth_data["wallet"] != wallet:
                            return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                    except Exception as e:
                        return JSONResponse(status_code=401, content={"success": False, "error": "Invalid session"})
                else:
                    return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    if auth_data["wallet"] != wallet:
                        return JSONResponse(status_code=403, content={"success": False, "error": "Access denied"})
                except HTTPException as e:
                    return JSONResponse(status_code=e.status_code, content={"success": False, "error": e.detail})
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем батл
            cursor.execute("""
                SELECT b.*, u.id_user
                FROM Battles b
                JOIN Users u ON b.id_user = u.id_user
                WHERE b.id_battle = %s AND u.wallet = %s
            """, (battle_id, wallet))
            battle = cursor.fetchone()
            
            if not battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Battle not found"}
                )
            
            if battle['status'] != 'fighting':
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": f"Battle is in {battle['status']} status, cannot fight"}
                )
            
            user_id = battle['id_user']
            user_tickets = battle['user_tickets']
            opponent_tickets = battle['bot_tickets']  # Внутреннее название, в ответе будет opponent_tickets
            
            # Определяем победителя
            # Билеты не влияют на победу - это только для отображения
            import random
            import json
            from datetime import datetime
            
            # Случайное определение победителя
            # Противник выигрывает в 70% случаев
            random_value = random.random()
            opponent_wins = random_value < 0.70
            winner = 'bot' if opponent_wins else 'user'  # В БД остается bot для внутренней логики
            
            # Обновляем статус батла
            cursor.execute("""
                UPDATE Battles
                SET status = 'completed', winner = %s, completed_at = %s
                WHERE id_battle = %s
            """, (winner, datetime.now(), battle_id))
            
            # Передаем карты
            user_cards = json.loads(battle['user_cards']) if isinstance(battle['user_cards'], str) else battle['user_cards']
            opponent_cards = json.loads(battle['bot_cards']) if isinstance(battle['bot_cards'], str) else battle['bot_cards']
            
            # Получаем полную информацию о картах пользователя
            user_cards_full = []
            for card_data in user_cards:
                cursor.execute("""
                    SELECT id_card, name, image_url, start_bounty, rarity
                    FROM Cards
                    WHERE id_card = %s
                """, (card_data['id_card'],))
                card_info = cursor.fetchone()
                if card_info:
                    user_cards_full.append({
                        'id_card': card_info['id_card'],
                        'name': card_info.get('name'),
                        'image_url': card_info.get('image_url'),
                        'start_bounty': card_info['start_bounty'],
                        'rarity': card_info.get('rarity'),
                        'quantity': card_data.get('quantity', 1)
                    })
            
            # Получаем полную информацию о картах противника
            opponent_cards_full = []
            for card_data in opponent_cards:
                cursor.execute("""
                    SELECT id_card, name, image_url, start_bounty, rarity
                    FROM Cards
                    WHERE id_card = %s
                """, (card_data['id_card'],))
                card_info = cursor.fetchone()
                if card_info:
                    opponent_cards_full.append({
                        'id_card': card_info['id_card'],
                        'name': card_info.get('name'),
                        'image_url': card_info.get('image_url'),
                        'start_bounty': card_info['start_bounty'],
                        'rarity': card_info.get('rarity'),
                        'quantity': card_data.get('quantity', 1)
                    })
            
            if opponent_wins:
                # Противник выиграл - пользователь теряет свои карты
                for card_data in user_cards:
                    id_card = card_data['id_card']
                    quantity = card_data['quantity']
                    cursor.execute("""
                        UPDATE Card_User
                        SET quantity = quantity - %s
                        WHERE id_user = %s AND id_card = %s
                    """, (quantity, user_id, id_card))
                    cursor.execute("""
                        DELETE FROM Card_User
                        WHERE id_user = %s AND id_card = %s AND quantity <= 0
                    """, (user_id, id_card))
            else:
                # Пользователь выиграл - получает карты противника
                for card_data in opponent_cards:
                    id_card = card_data['id_card']
                    quantity = card_data.get('quantity', 1)
                    cursor.execute("""
                        INSERT INTO Card_User (id_user, id_card, quantity)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + %s
                    """, (user_id, id_card, quantity, quantity))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Преобразуем winner для ответа (bot -> opponent)
            winner_response = 'opponent' if winner == 'bot' else winner
            
            return {
                "success": True,
                "battle_id": battle_id,
                "winner": winner_response,
                "user_tickets": user_tickets,
                "opponent_tickets": opponent_tickets,
                "user_cards": user_cards_full,
                "opponent_cards": opponent_cards_full,
                "cards_won": opponent_cards_full if not opponent_wins else [],
                "cards_lost": user_cards_full if opponent_wins else []
            }
            
        except Exception as e:
            print(f"Fight battle error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.get("/api/battle/status/{battle_id}")
    async def get_battle_status(battle_id: int, request: Request):
        """Получить статус батла"""
        try:
            # Проверяем авторизацию
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")
            
            wallet = None
            if not x_wallet or not x_signature or not x_message:
                auth_token = request.cookies.get("auth_token")
                if auth_token:
                    try:
                        from core.sessions import verify_session_cookie
                        auth_data = await verify_session_cookie(request)
                        wallet = auth_data["wallet"]
                    except Exception:
                        pass
            else:
                try:
                    auth_data = await verify_auth(x_wallet=x_wallet, x_signature=x_signature, x_message=x_message)
                    wallet = auth_data["wallet"]
                except HTTPException:
                    pass
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("""
                SELECT b.*, u.wallet
                FROM Battles b
                JOIN Users u ON b.id_user = u.id_user
                WHERE b.id_battle = %s
            """, (battle_id,))
            battle = cursor.fetchone()
            
            if not battle:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Battle not found"}
                )
            
            # Проверяем, что пользователь имеет доступ к этому батлу
            if wallet and battle['wallet'] != wallet:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=403,
                    content={"success": False, "error": "Access denied"}
                )
            
            import json
            user_cards = json.loads(battle['user_cards']) if isinstance(battle['user_cards'], str) else battle['user_cards']
            opponent_cards = json.loads(battle['bot_cards']) if isinstance(battle['bot_cards'], str) else battle['bot_cards']
            
            cursor.close()
            conn.close()
            
            # Преобразуем winner для ответа (bot -> opponent)
            winner_response = 'opponent' if battle.get('winner') == 'bot' else battle.get('winner')
            
            return {
                "success": True,
                "battle_id": battle_id,
                "status": battle['status'],
                "user_tickets": battle['user_tickets'],
                "opponent_tickets": battle['bot_tickets'],
                "winner": winner_response,
                "user_cards": user_cards,
                "opponent_cards": opponent_cards if battle['status'] == 'completed' else [],
                "started_at": str(battle['started_at']),
                "completed_at": str(battle['completed_at']) if battle['completed_at'] else None
            }
            
        except Exception as e:
            print(f"Get battle status error: {e}")
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    # ==================== PREDICTIONS API ====================
    
    @app.get("/api/predictions/markets")
    async def get_predictions_markets(
        request: Request,
        period: str = "24h",
        limit: int = 20,
        force_refresh: bool = False
    ):
        """Получить список активных пари (исключает пари, на которые пользователь уже сделал ставку)"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Пытаемся получить информацию о пользователе из request state (установлено auth_middleware)
            user_id = None
            if request and hasattr(request.state, 'user'):
                user_id = request.state.user.get('user_id')
            
            # Если нет user_id в state, пытаемся получить из заголовков или cookie
            if not user_id:
                x_wallet = request.headers.get("X-Wallet") if request else None
                if x_wallet:
                    cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (x_wallet,))
                    user = cursor.fetchone()
                    if user:
                        user_id = user['id_user']
                else:
                    # Проверяем cookie
                    if request:
                        auth_token = request.cookies.get("auth_token")
                        if auth_token:
                            try:
                                from core.sessions import verify_session_cookie
                                user_data = await verify_session_cookie(request)
                                user_id = user_data.get('user_id')
                            except:
                                pass  # Пользователь не авторизован, показываем все пари
            
            # Если force_refresh, можно запустить синхронизацию (но это долго, лучше не делать)
            # Просто получаем из БД
            
            # Определяем сортировку по периоду
            order_by = "volume_24h DESC"
            if period == "7d":
                order_by = "volume_7d DESC"
            elif period == "30d":
                order_by = "volume_30d DESC"
            
            criteria_prob_diff_max = float(os.getenv("PREDICTIONS_PROB_DIFF_MAX", "30"))
            criteria_days_min = float(os.getenv("PREDICTIONS_DAYS_MIN", "14"))
            criteria_days_max = float(os.getenv("PREDICTIONS_DAYS_MAX", "21"))

            # Если пользователь авторизован, исключаем пари, на которые он уже сделал ставку
            if user_id:
                cursor.execute(f"""
                    SELECT 
                        p.id_prediction,
                        p.polymarket_id,
                        p.title,
                        p.description,
                        p.category,
                        p.outcome_a,
                        p.outcome_b,
                        p.outcome_a_probability,
                        p.outcome_b_probability,
                        p.resolution_date,
                        p.status,
                        p.volume_24h,
                        p.volume_7d,
                        p.volume_30d,
                        p.created_at,
                        p.updated_at
                    FROM public.predictions p
                    LEFT JOIN public.user_bets ub ON p.id_prediction = ub.id_prediction AND ub.id_user = %s
                    WHERE p.status = 'active'
                      AND ub.id_bet IS NULL
                      AND p.resolution_date IS NOT NULL
                      AND p.resolution_date > (now() + (%s * interval '1 day'))
                      AND p.resolution_date < (now() + (%s * interval '1 day'))
                      AND ABS(COALESCE(p.outcome_a_probability, 50) - COALESCE(p.outcome_b_probability, 50)) <= %s
                    ORDER BY {order_by}
                    LIMIT %s
                """, (user_id, criteria_days_min, criteria_days_max, criteria_prob_diff_max, limit))
            else:
                # Если пользователь не авторизован, показываем все активные пари
                cursor.execute(f"""
                    SELECT 
                        id_prediction,
                        polymarket_id,
                        title,
                        description,
                        category,
                        outcome_a,
                        outcome_b,
                        outcome_a_probability,
                        outcome_b_probability,
                        resolution_date,
                        status,
                        volume_24h,
                        volume_7d,
                        volume_30d,
                        created_at,
                        updated_at
                    FROM public.predictions
                    WHERE status = 'active'
                      AND resolution_date IS NOT NULL
                      AND resolution_date > (now() + (%s * interval '1 day'))
                      AND resolution_date < (now() + (%s * interval '1 day'))
                      AND ABS(COALESCE(outcome_a_probability, 50) - COALESCE(outcome_b_probability, 50)) <= %s
                    ORDER BY {order_by}
                    LIMIT %s
                """, (criteria_days_min, criteria_days_max, criteria_prob_diff_max, limit))
            
            markets = cursor.fetchall()
            cursor.close()
            conn.close()
            
            # Преобразуем в список словарей
            markets_list = []
            for market in markets:
                ends_at = str(market['resolution_date']) if market['resolution_date'] else None
                markets_list.append({
                    "id_prediction": market['id_prediction'],
                    "polymarket_id": market['polymarket_id'],
                    "title": market['title'],
                    "description": market.get('description', ''),
                    "category": market.get('category', 'general'),
                    "outcome_a": market['outcome_a'],
                    "outcome_b": market['outcome_b'],
                    "outcome_a_probability": float(market['outcome_a_probability']) if market['outcome_a_probability'] else 50.0,
                    "outcome_b_probability": float(market['outcome_b_probability']) if market['outcome_b_probability'] else 50.0,
                    "resolution_date": ends_at,
                    "ends_at": ends_at,
                    "status": market['status'],
                    "volume_24h": float(market['volume_24h']) if market['volume_24h'] else 0.0,
                    "volume_7d": float(market['volume_7d']) if market['volume_7d'] else 0.0,
                    "volume_30d": float(market['volume_30d']) if market['volume_30d'] else 0.0
                })
            
            return {
                "success": True,
                "markets": markets_list,
                "count": len(markets_list)
            }
            
        except Exception as e:
            print(f"Get predictions markets error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/predictions/bet/{wallet}")
    async def create_prediction_bet(wallet: str, request: Request):
        """Создать ставку на пари"""
        try:
            # Проверяем авторизацию
            await _ensure_authorized_user(request, wallet)
            
            # Получаем данные из тела запроса
            body = await request.json()
            prediction_id = body.get("prediction_id")
            chosen_outcome = body.get("chosen_outcome")
            
            if not prediction_id or not chosen_outcome:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Missing prediction_id or chosen_outcome"}
                )
            
            if chosen_outcome not in ['A', 'B']:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "chosen_outcome must be 'A' or 'B'"}
                )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем пользователя
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "User not found"}
                )
            user_id = user['id_user']
            
            criteria_prob_diff_max = float(os.getenv("PREDICTIONS_PROB_DIFF_MAX", "30"))
            criteria_days_min = float(os.getenv("PREDICTIONS_DAYS_MIN", "14"))
            criteria_days_max = float(os.getenv("PREDICTIONS_DAYS_MAX", "21"))

            # Проверяем, что пари существует и активно
            cursor.execute("""
                SELECT id_prediction, status, outcome_a_probability, outcome_b_probability, resolution_date
                FROM public.predictions 
                WHERE id_prediction = %s
            """, (prediction_id,))
            prediction = cursor.fetchone()
            
            if not prediction:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Prediction not found"}
                )
            
            if prediction['status'] != 'active':
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Prediction is not active"}
                )

            from datetime import datetime, timezone
            ends_at = prediction.get('resolution_date')
            if not ends_at:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Prediction does not meet criteria"}
                )

            if isinstance(ends_at, str):
                try:
                    ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
                except Exception:
                    cursor.close()
                    conn.close()
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "error": "Prediction does not meet criteria"}
                    )

            now = datetime.now(timezone.utc)
            if ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=timezone.utc)
            days_until = (ends_at - now).total_seconds() / (24 * 3600)
            prob_a = float(prediction.get('outcome_a_probability') or 50.0)
            prob_b = float(prediction.get('outcome_b_probability') or 50.0)
            if abs(prob_a - prob_b) > criteria_prob_diff_max or days_until < criteria_days_min or days_until > criteria_days_max:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Prediction does not meet criteria"}
                )
            
            # Проверяем, нет ли уже ставки от этого пользователя
            cursor.execute("""
                SELECT id_bet FROM public.user_bets 
                WHERE id_user = %s AND id_prediction = %s
            """, (user_id, prediction_id))
            existing_bet = cursor.fetchone()
            
            if existing_bet:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "Bet already exists for this prediction"}
                )
            
            # Создаем ставку
            cursor.execute("""
                INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
                VALUES (%s, %s, %s, %s)
                RETURNING id_bet
            """, (user_id, prediction_id, chosen_outcome, 'pending'))
            
            bet = cursor.fetchone()
            bet_id = bet['id_bet']
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "bet_id": bet_id,
                "message": "Bet placed successfully"
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Create prediction bet error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.get("/api/predictions/user/{wallet}")
    async def get_user_predictions_bets(wallet: str, request: Request):
        """Получить ставки пользователя"""
        try:
            # Проверяем авторизацию
            await _ensure_authorized_user(request, wallet)
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Получаем пользователя
            cursor.execute("SELECT id_user FROM Users WHERE wallet = %s", (wallet,))
            user = cursor.fetchone()
            if not user:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "User not found"}
                )
            user_id = user['id_user']
            
            # Получаем ставки пользователя
            cursor.execute("""
                SELECT 
                    ub.id_bet,
                    ub.id_prediction,
                    ub.chosen_outcome,
                    ub.status,
                    ub.reward_issued,
                    ub.reward_claimed,
                    ub.reward_claimed_at,
                    ub.reward_type,
                    ub.reward_data,
                    ub.created_at,
                    ub.resolved_at,
                    p.title,
                    p.outcome_a,
                    p.outcome_b,
                    p.outcome_a_probability,
                    p.outcome_b_probability,
                    p.resolution_date,
                    p.status as prediction_status,
                    p.winner_outcome
                FROM public.user_bets ub
                JOIN public.predictions p ON ub.id_prediction = p.id_prediction
                WHERE ub.id_user = %s
                ORDER BY ub.created_at DESC
            """, (user_id,))
            
            bets = cursor.fetchall()
            cursor.close()
            conn.close()
            
            bets_list = []
            for bet in bets:
                ends_at = str(bet['resolution_date']) if bet.get('resolution_date') else None
                bets_list.append({
                    "bet_id": bet['id_bet'],
                    "prediction_id": bet['id_prediction'],
                    "title": bet['title'],
                    "chosen_outcome": bet['chosen_outcome'],
                    "outcome_a": bet['outcome_a'],
                    "outcome_b": bet['outcome_b'],
                    "outcome_a_probability": float(bet['outcome_a_probability']) if bet['outcome_a_probability'] else 50.0,
                    "outcome_b_probability": float(bet['outcome_b_probability']) if bet['outcome_b_probability'] else 50.0,
                    "status": bet['status'],
                    "reward_issued": bool(bet.get('reward_issued')),
                    "reward_claimed": bool(bet.get('reward_claimed')),
                    "reward_claimed_at": str(bet['reward_claimed_at']) if bet.get('reward_claimed_at') else None,
                    "reward_type": bet.get('reward_type'),
                    "reward_data": bet.get('reward_data'),
                    "prediction_status": bet['prediction_status'],
                    "winner_outcome": bet['winner_outcome'],
                    "ends_at": ends_at,
                    "created_at": str(bet['created_at']),
                    "resolved_at": str(bet['resolved_at']) if bet['resolved_at'] else None
                })
            
            return {
                "success": True,
                "bets": bets_list,
                "count": len(bets_list)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Get user predictions bets error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.post("/api/predictions/resolve/{prediction_id}")
    async def resolve_prediction(prediction_id: int, request: Request):
        """Разрешить пари (установить победителя)"""
        try:
            # Проверяем авторизацию (только админ может разрешать)
            x_wallet = request.headers.get("X-Wallet")
            x_signature = request.headers.get("X-Signature")
            x_message = request.headers.get("X-Message")

            auth_wallet = None
            if x_wallet and x_signature and x_message:
                auth_data = await verify_auth(
                    x_wallet=x_wallet,
                    x_signature=x_signature,
                    x_message=x_message
                )
                auth_wallet = auth_data.get("wallet")
            else:
                auth_token = request.cookies.get("auth_token")
                if not auth_token:
                    return JSONResponse(
                        status_code=401,
                        content={"success": False, "error": "Unauthorized"}
                    )
                from core.sessions import verify_session_cookie
                auth_data = await verify_session_cookie(request)
                auth_wallet = auth_data.get("wallet")

            admins_env = os.getenv("PREDICTIONS_RESOLVE_ADMINS", "").strip()
            if admins_env:
                allowed_admins = {w.strip() for w in admins_env.split(',') if w.strip()}
                if auth_wallet not in allowed_admins:
                    return JSONResponse(
                        status_code=403,
                        content={"success": False, "error": "Forbidden"}
                    )
            
            # Получаем данные из тела запроса
            body = await request.json()
            winner_outcome = body.get("winner_outcome")  # 'A', 'B', или 'cancelled'
            
            if winner_outcome not in ['A', 'B', 'cancelled']:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "error": "winner_outcome must be 'A', 'B', or 'cancelled'"}
                )
            
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Проверяем, что пари существует
            cursor.execute("""
                SELECT id_prediction, status FROM public.predictions 
                WHERE id_prediction = %s
            """, (prediction_id,))
            prediction = cursor.fetchone()
            
            if not prediction:
                cursor.close()
                conn.close()
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": "Prediction not found"}
                )
            
            # Обновляем пари
            cursor.execute("""
                UPDATE public.predictions 
                SET status = 'resolved',
                    winner_outcome = %s,
                    updated_at = now()
                WHERE id_prediction = %s
            """, (winner_outcome, prediction_id))
            
            # Инициализируем список наград (для случая cancelled будет пустым)
            rewards_summary = []
            
            # Обновляем статусы ставок
            if winner_outcome == 'cancelled':
                cursor.execute("""
                    UPDATE public.user_bets 
                    SET status = 'cancelled',
                        resolved_at = now()
                    WHERE id_prediction = %s AND status = 'pending'
                """, (prediction_id,))
            else:
                # Получаем список выигравших пользователей
                cursor.execute("""
                    SELECT id_user FROM public.user_bets 
                    WHERE id_prediction = %s 
                    AND chosen_outcome = %s 
                    AND status = 'pending'
                """, (prediction_id, winner_outcome))
                winning_users = cursor.fetchall()
                
                # Выдаем награды выигравшим пользователям
                for user_row in winning_users:
                    user_id = user_row['id_user']
                    try:
                        rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, conn, user_id)
                        if rewards_issued:
                            rewards_summary.append({
                                "user_id": user_id,
                                "rewards": rewards_issued
                            })
                            cursor.execute("""
                                UPDATE public.user_bets
                                SET reward_type = %s,
                                    reward_data = %s
                                WHERE id_prediction = %s
                                AND id_user = %s
                                AND chosen_outcome = %s
                                AND status = 'pending'
                            """, (
                                reward_type,
                                json.dumps(reward_data) if reward_data else None,
                                prediction_id,
                                user_id,
                                winner_outcome
                            ))
                    except Exception as e:
                        print(f"Error issuing reward to user {user_id}: {e}")
                        # Продолжаем обработку других пользователей
                
                # Помечаем выигравшие ставки
                cursor.execute("""
                    UPDATE public.user_bets 
                    SET status = 'won',
                        resolved_at = now(),
                        reward_issued = TRUE
                    WHERE id_prediction = %s 
                    AND chosen_outcome = %s 
                    AND status = 'pending'
                """, (prediction_id, winner_outcome))
                
                # Помечаем проигравшие ставки
                cursor.execute("""
                    UPDATE public.user_bets 
                    SET status = 'lost',
                        resolved_at = now()
                    WHERE id_prediction = %s 
                    AND chosen_outcome != %s 
                    AND status = 'pending'
                """, (prediction_id, winner_outcome))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "message": "Prediction resolved successfully",
                "rewards_issued": len(rewards_summary) > 0,
                "rewards_count": len(rewards_summary)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Resolve prediction error: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Internal error: {str(e)}"}
            )
    
    @app.get("/health")
    async def health_check():
        try:
            conn = get_db_connection()
            conn.close()
            return {"status": "ok", "database": "connected"}
        except Exception as e:
            return {"status": "ok", "database": "disconnected", "error": str(e)}

