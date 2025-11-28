from fastapi import HTTPException, Depends, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor
import hashlib
import json

from core.models import AuthRequest
from core.utils import get_db_connection, generate_ref_code, verify_solana_signature, verify_solana_transaction, HELIUS_RPC_URL, determine_card_rarity, get_random_card_by_rarity, get_or_create_active_round, add_to_jackpot, draw_jackpot
from core.auth import verify_auth
from pydantic import BaseModel


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
        
        # Заглушка - возвращаем нулевой баланс
        return {
            "success": True,
            "balance": {
                "amount": 0,
                "decimals": 9,
                "symbol": "TIRED"
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
            "mint": mint  # Адрес токена TIRED
        }
    
    @app.get("/api/super-jackpot")
    async def get_super_jackpot():
        return {
            "success": True,
            "amount": 0
        }
    
    @app.post("/api/chests/buy")
    async def buy_chest(request: Request):
        """Покупка пака с проверкой транзакции"""
        try:
            # Получаем данные из запроса
            body = await request.json()
            wallet = body.get("wallet")
            id_chest = body.get("id_chest")
            tx_signature = body.get("txSignature")
            
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
            tx_verification = verify_solana_transaction(
                tx_signature=tx_signature,
                expected_sender=wallet,
                expected_receiver=merchant,
                expected_amount=price,
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
            
            # Создаем запись о покупке
            cursor.execute("""
                INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
                VALUES (%s, %s, %s)
                RETURNING id_purchase
            """, (user['id_user'], id_chest, tx_signature))
            
            purchase = cursor.fetchone()
            
            # Добавляем 10% от цены пака в джекпот
            jackpot_contribution = float(chest['price']) * 0.1
            add_to_jackpot(cursor, conn, jackpot_contribution)
            
            conn.commit()
            
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "purchase_id": purchase['id_purchase'],
                "message": "Pack purchased successfully"
            }
            
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
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "success": True,
                "lost": False,
                "rarity": rarity,
                "card_id": card['id_card'],
                "card_name": card.get('name', ''),
                "image_url": card.get('image_url', ''),
                "start_bounty": card['start_bounty']
            }
            
        except Exception as e:
            print(f"Open chest error: {e}")
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

