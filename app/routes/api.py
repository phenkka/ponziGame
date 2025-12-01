from fastapi import HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
import hashlib
import json
import os
import requests

from core.models import AuthRequest
from core.utils import get_db_connection, generate_ref_code, verify_solana_signature, verify_solana_transaction, HELIUS_RPC_URL, determine_card_rarity, get_random_card_by_rarity, get_or_create_active_round, add_to_jackpot, draw_jackpot, add_to_super_jackpot, claim_super_jackpot, get_or_create_active_super_jackpot_round
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
            cursor.execute("SELECT id_purchase FROM Chest_purchases WHERE tx_signature = %s", (tx_signature,))
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
                
                cursor.execute("""
                    INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
                    VALUES (%s, %s, %s)
                    RETURNING id_purchase
                """, (user['id_user'], id_chest, unique_tx_sig))
                
                purchase = cursor.fetchone()
                if purchase:
                    purchase_ids.append(purchase['id_purchase'])
            
            # Добавляем 10% от общей суммы в джекпот
            jackpot_contribution = total_price * 0.1
            add_to_jackpot(cursor, conn, jackpot_contribution)
            
            # Добавляем 5% от общей суммы в супер джекпот
            super_jackpot_contribution = total_price * 0.05
            add_to_super_jackpot(cursor, conn, super_jackpot_contribution)
            
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
                    WHERE id_purchase = %s
                """, (id_purchase,))
                
                # Записываем в Chest_openings
                cursor.execute("""
                    INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
                    VALUES (%s, %s, %s)
                """, (id_purchase, purchase_data['id_user'], purchase_data['id_chest']))
                
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
            
            # Добавляем карту пользователю (или увеличиваем quantity если уже есть)
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (id_user, id_card) 
                DO UPDATE SET quantity = Card_User.quantity + 1
            """, (purchase_data['id_user'], card['id_card']))
            
            # Отмечаем пак как открытый
            cursor.execute("""
                UPDATE Chest_purchases
                SET is_opened = TRUE, opened_at = NOW()
                WHERE id_purchase = %s
            """, (id_purchase,))
            
            # Записываем в Chest_openings
            cursor.execute("""
                INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
                VALUES (%s, %s, %s)
            """, (id_purchase, purchase_data['id_user'], purchase_data['id_chest']))
            
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
    
    @app.get("/health")
    async def health_check():
        try:
            conn = get_db_connection()
            conn.close()
            return {"status": "ok", "database": "connected"}
        except Exception as e:
            return {"status": "ok", "database": "disconnected", "error": str(e)}

