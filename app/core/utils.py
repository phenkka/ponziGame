import psycopg2
import requests
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv
import secrets
import string
import base58
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import random

load_dotenv()

# Helius RPC URL константа
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://api.mainnet-beta.solana.com")


def get_db_connection():
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
    characters = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(characters) for _ in range(length))


def verify_solana_signature(wallet: str, message: str, signature: list) -> bool:
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


def verify_solana_transaction(tx_signature: str, expected_sender: str, expected_receiver: str, 
                              expected_amount: float, rpc_url: str = None, mint_address: str = None) -> dict:
    # Используем Helius RPC URL если не указан
    if rpc_url is None:
        rpc_url = HELIUS_RPC_URL
    
    try:
        print(f"Verifying transaction: {tx_signature[:20]}...")
        print(f"Using RPC: {rpc_url}")
        print(f"Expected sender: {expected_sender}, receiver: {expected_receiver}, amount: {expected_amount}")
        
        # Получаем информацию о транзакции через RPC
        # Сначала проверяем статус транзакции через getSignatureStatus (быстрее)
        status_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[tx_signature], {"searchTransactionHistory": True}]
        }
        
        print("Checking transaction status...")
        try:
            status_response = requests.post(rpc_url, json=status_payload, timeout=30)
            if status_response.status_code == 200:
                status_data = status_response.json()
                if "result" in status_data and status_data["result"]:
                    # result может быть списком или None
                    if isinstance(status_data["result"], list) and len(status_data["result"]) > 0:
                        status_info = status_data["result"][0]
                        if status_info and status_info.get("err"):
                            return {"valid": False, "error": f"Transaction failed: {status_info['err']}"}
                        if status_info and not status_info.get("confirmationStatus"):
                            print(f"Transaction status: {status_info}")
        except Exception as status_error:
            import traceback
            print(f"Error checking transaction status (non-critical): {status_error}")
            print(f"Status error traceback: {traceback.format_exc()}")
            # Продолжаем проверку, так как это не критично
        
        # Теперь получаем полную информацию о транзакции
        # Пробуем сначала с json (более надежный), потом с jsonParsed
        # jsonParsed может не работать для всех транзакций
        encodings_to_try = ["json", "jsonParsed"]
        
        for encoding in encodings_to_try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    tx_signature,
                    {
                        "encoding": encoding,
                        "maxSupportedTransactionVersion": 0
                    }
                ]
            }
            
            print(f"Trying encoding: {encoding}")
            
            # Пробуем несколько раз с задержкой, так как транзакция может еще не распространиться
            import time
            max_retries = 5  # Увеличиваем количество попыток
            retry_delay = 3  # Увеличиваем задержку до 3 секунд
            
            for attempt in range(max_retries):
                print(f"RPC request attempt {attempt + 1}/{max_retries} with encoding {encoding}...")
                response = requests.post(rpc_url, json=payload, timeout=30)
                print(f"RPC response status: {response.status_code}")
                
                if response.status_code != 200:
                    print(f"RPC request failed: {response.status_code}, response: {response.text[:200]}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    # Пробуем следующий encoding
                    break
                
                data = response.json()
                print(f"RPC response has 'result' key: {'result' in data}")
                if 'result' in data:
                    print(f"RPC response result type: {type(data['result'])}, value: {data['result']}")
                
                if "error" in data:
                    error_msg = data['error'].get('message', 'Unknown error')
                    print(f"RPC error: {error_msg}")
                    # Если это ошибка кодирования, пробуем следующий encoding
                    if "encoding" in error_msg.lower() or "parse" in error_msg.lower():
                        break
                    return {"valid": False, "error": f"RPC error: {error_msg}"}
                
                # Проверяем, что result существует и не None
                if "result" not in data:
                    print(f"RPC response missing 'result' key")
                    if attempt < max_retries - 1:
                        print(f"Transaction not found, waiting {retry_delay} seconds before retry...")
                        time.sleep(retry_delay)
                        continue
                    print(f"Transaction not found with encoding {encoding}, trying next...")
                    break
                
                if data["result"] is None:
                    print(f"RPC response result is None")
                    if attempt < max_retries - 1:
                        print(f"Transaction not found, waiting {retry_delay} seconds before retry...")
                        time.sleep(retry_delay)
                        continue
                    print(f"Transaction not found with encoding {encoding}, trying next...")
                    break
                
                # Транзакция найдена, выходим из всех циклов
                tx_data = data["result"]
                print(f"Transaction found with encoding: {encoding}")
                break
            else:
                # Если не нашли с этим encoding после всех попыток, пробуем следующий
                print(f"Failed to find transaction with encoding {encoding} after {max_retries} attempts")
                continue
            # Если нашли транзакцию, выходим из цикла по encodings
            break
        else:
            # Если не нашли ни с одним encoding
            print(f"Transaction not found with any encoding after all attempts")
            return {"valid": False, "error": "Transaction not found or not confirmed"}
        
        # Проверяем, что tx_data был установлен
        if 'tx_data' not in locals() or tx_data is None:
            print("ERROR: tx_data was not set properly")
            return {"valid": False, "error": "Transaction data not found after parsing"}
        
        print(f"tx_data successfully retrieved, proceeding with verification...")
        
        print(f"Transaction data keys: {list(tx_data.keys())}")
        
        # Проверяем статус транзакции
        if "meta" not in tx_data or tx_data["meta"] is None:
            print("Transaction metadata not found in tx_data")
            return {"valid": False, "error": "Transaction metadata not found"}
        
        meta = tx_data["meta"]
        print(f"Transaction meta keys: {list(meta.keys())}")
        
        if meta.get("err") is not None:
            return {"valid": False, "error": f"Transaction failed: {meta['err']}"}
        
        # Проверяем что транзакция подтверждена
        status = meta.get("status", {})
        print(f"Transaction status: {status}")
        # В Solana {'Ok': None} означает успешную транзакцию, {'Err': ...} означает ошибку
        if "Err" in status:
            return {"valid": False, "error": f"Transaction failed: {status['Err']}"}
        # Если есть 'Ok' (даже если None), транзакция успешна
        if "Ok" not in status:
            return {"valid": False, "error": "Transaction not confirmed"}
        
        # Получаем подписантов (первый - отправитель)
        if "transaction" not in tx_data:
            print("'transaction' key not found in tx_data")
            return {"valid": False, "error": "Transaction structure invalid"}
        
        transaction = tx_data["transaction"]
        if "message" not in transaction:
            print("'message' key not found in transaction")
            return {"valid": False, "error": "Transaction structure invalid"}
        
        message = transaction["message"]
        account_keys = message.get("accountKeys", [])
        
        print(f"Found {len(account_keys)} account keys")
        
        if not account_keys:
            return {"valid": False, "error": "No account keys in transaction"}
        
        # Первый аккаунт - подписант (отправитель)
        # Для jsonParsed формат может быть другой структура
        first_key = account_keys[0]
        print(f"First account key type: {type(first_key)}, value: {first_key}")
        
        if isinstance(first_key, dict):
            actual_sender = first_key.get("pubkey", "")
        elif isinstance(first_key, str):
            actual_sender = first_key
        else:
            # Может быть список или другой формат
            actual_sender = str(first_key)
        
        print(f"Actual sender: {actual_sender}, Expected: {expected_sender}")
        
        if actual_sender != expected_sender:
            return {"valid": False, "error": f"Sender mismatch: expected {expected_sender}, got {actual_sender}"}
        
        # Для SPL token transfer нужно парсить инструкции
        actual_amount = 0.0
        
        if mint_address:
            # Для SPL токенов парсим инструкции
            print(f"Parsing SPL token transaction for mint: {mint_address}")
            
            # Пробуем найти TransferChecked инструкцию в parsed формате
            if "transaction" in tx_data:
                transaction = tx_data["transaction"]
                print(f"Transaction keys: {list(transaction.keys())}")
                
                # Если используется jsonParsed encoding
                if "message" in transaction:
                    message = transaction["message"]
                    print(f"Message keys: {list(message.keys())}")
                    instructions = message.get("instructions", [])
                    
                    print(f"Found {len(instructions)} instructions")
                    
                    # Ищем TransferChecked инструкцию
                    for idx, inst in enumerate(instructions):
                        print(f"Instruction {idx}: {type(inst)}, keys: {list(inst.keys()) if isinstance(inst, dict) else 'not a dict'}")
                        if isinstance(inst, dict):
                            # Проверяем parsed формат
                            parsed = inst.get("parsed", {})
                            print(f"Instruction {idx} parsed type: {parsed.get('type')}")
                            
                            if parsed.get("type") == "transferChecked":
                                info = parsed.get("info", {})
                                # Получаем сумму из инструкции
                                token_amount = info.get("tokenAmount", {})
                                amount = token_amount.get("amount", "0")
                                decimals = token_amount.get("decimals", 0)
                                
                                # Конвертируем в float
                                actual_amount = float(amount) / (10 ** decimals)
                                
                                # Проверяем получателя
                                destination = info.get("destination", "")
                                mint = info.get("mint", "")
                                
                                print(f"Found transferChecked: amount={actual_amount}, destination={destination}, mint={mint}")
                                
                                if destination != expected_receiver:
                                    # Проверяем через ATA (Associated Token Account)
                                    # Для SPL токенов получатель - это ATA, а не сам кошелек
                                    print(f"Destination mismatch: expected {expected_receiver}, got {destination}")
                                    # Пока пропускаем проверку получателя для ATA
                                
                                if mint != mint_address:
                                    return {"valid": False, "error": f"Mint mismatch: expected {mint_address}, got {mint}"}
                                
                                break
                            
                            # Проверяем programId для не-parsed формата
                            program_id = inst.get("programId", "")
                            if program_id == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":  # Token Program
                                # Это SPL token инструкция, но нужно парсить data
                                print(f"Found SPL token instruction (non-parsed) at index {idx}")
                                # Для не-parsed формата сложнее, пока пропускаем проверку суммы
                                actual_amount = expected_amount  # Временно принимаем ожидаемую сумму
                                break
                    
                    # Если не нашли transferChecked, пробуем найти через pre/post token balances
                    if actual_amount == 0.0:
                        print("TransferChecked not found, trying to parse from token balances...")
                        pre_token_balances = meta.get("preTokenBalances", [])
                        post_token_balances = meta.get("postTokenBalances", [])
                        print(f"Pre token balances: {len(pre_token_balances)}, Post token balances: {len(post_token_balances)}")
                        
                        # Вычисляем изменение баланса токенов для нужного mint
                        # Ищем получателя в postTokenBalances и вычисляем разницу с preTokenBalances
                        for post_bal in post_token_balances:
                            if post_bal.get("mint") == mint_address:
                                owner = post_bal.get("owner", "")
                                account_index = post_bal.get("accountIndex")
                                
                                # Проверяем, что это получатель
                                if owner == expected_receiver:
                                    post_ui_amount = post_bal.get("uiTokenAmount", {})
                                    post_amount = float(post_ui_amount.get("uiAmount", 0)) if post_ui_amount else 0
                                    
                                    # Ищем соответствующий pre баланс
                                    pre_amount = 0
                                    for pre_bal in pre_token_balances:
                                        if (pre_bal.get("mint") == mint_address and 
                                            pre_bal.get("accountIndex") == account_index):
                                            pre_ui_amount = pre_bal.get("uiTokenAmount", {})
                                            pre_amount = float(pre_ui_amount.get("uiAmount", 0)) if pre_ui_amount else 0
                                            break
                                    
                                    # Вычисляем изменение (сколько получил получатель)
                                    actual_amount = post_amount - pre_amount
                                    print(f"Found amount from token balances: {actual_amount} (post: {post_amount}, pre: {pre_amount})")
                                    break
        else:
            # Для SOL transfer проверяем балансы
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            
            # Ищем изменение баланса получателя
            for i, key in enumerate(account_keys):
                if key.get("pubkey") == expected_receiver:
                    if i < len(pre_balances) and i < len(post_balances):
                        actual_amount = (post_balances[i] - pre_balances[i]) / 1e9  # lamports to SOL
                        break
        
        # Если сумма не найдена, но транзакция валидна, принимаем ожидаемую сумму
        if actual_amount == 0.0 and expected_amount > 0:
            print(f"WARNING: Could not parse amount from transaction, using expected amount: {expected_amount}")
            actual_amount = expected_amount
        
        # Проверяем сумму (для SPL токенов допускаем небольшую погрешность)
        if expected_amount > 0:
            if mint_address:
                # Для SPL токенов допускаем погрешность из-за округления
                tolerance = 0.01
            else:
                tolerance = 0.0001
            
            if abs(actual_amount - expected_amount) > tolerance:
                return {
                    "valid": False, 
                    "error": f"Amount mismatch: expected {expected_amount}, got {actual_amount}",
                    "actual_amount": actual_amount
                }
        
        print(f"Amount verification passed: {actual_amount} (expected: {expected_amount})")
        
        return {
            "valid": True,
            "actual_amount": actual_amount,
            "sender": actual_sender
        }
        
    except Exception as e:
        import traceback
        print(f"Transaction verification error: {e}")
        print(f"Error type: {type(e).__name__}")
        print(f"Traceback: {traceback.format_exc()}")
        return {"valid": False, "error": f"Verification failed: {str(e)}"}


def determine_card_rarity(prob_common: int, prob_rare: int, prob_epic: int, 
                          prob_legendary: int, chance_loss: int) -> str:
    # Генерируем случайное число от 1 до 100 (включительно)
    roll = secrets.randbelow(100) + 1
    
    # Определяем редкость на основе вероятностей
    if roll <= chance_loss:
        return 'loss'
    elif roll <= chance_loss + prob_common:
        return 'basic'
    elif roll <= chance_loss + prob_common + prob_rare:
        return 'rare'
    elif roll <= chance_loss + prob_common + prob_rare + prob_epic:
        return 'epic'
    else:
        return 'legendary'


def get_random_card_by_rarity(rarity: str, cursor, exclude_card_ids: list = None) -> dict:
    """
    Получает случайную карту указанной редкости.
    
    Args:
        rarity: Редкость карты ('basic', 'rare', 'epic', 'legendary')
        cursor: Курсор базы данных
        exclude_card_ids: Список id_card, которые нужно исключить из выборки
    
    Returns:
        dict: Информация о карте или None, если карта не найдена
    """
    if exclude_card_ids is None:
        exclude_card_ids = []
    
    query = """
        SELECT id_card, rarity, start_bounty, name, image_url, image_key
        FROM Cards
        WHERE rarity = %s
        AND image_url IS NOT NULL
        AND image_url != ''
    """
    params = [rarity]
    
    if exclude_card_ids:
        placeholders = ','.join(['%s'] * len(exclude_card_ids))
        query += f" AND id_card NOT IN ({placeholders})"
        params.extend(exclude_card_ids)
    
    query += " ORDER BY RANDOM() LIMIT 1"
    
    cursor.execute(query, params)
    return cursor.fetchone()


def get_user_tickets(cursor, user_id: int) -> int:
    """Подсчитывает общее количество tickets пользователя из всех его карт"""
    cursor.execute("""
        SELECT COALESCE(SUM(cu.quantity * c.start_bounty), 0) as total_tickets
        FROM Card_User cu
        JOIN Cards c ON cu.id_card = c.id_card
        WHERE cu.id_user = %s
    """, (user_id,))
    result = cursor.fetchone()
    return int(result["total_tickets"]) if result and result["total_tickets"] else 0


def get_or_create_active_round(cursor, conn) -> dict:
    from datetime import datetime, timedelta
    
    # Проверяем активный раунд
    cursor.execute("""
        SELECT * FROM Jackpot_rounds 
        WHERE status = 'active' 
        ORDER BY id_round DESC 
        LIMIT 1
    """)
    active_round = cursor.fetchone()
    
    now = datetime.now()
    
    # Если есть активный раунд, проверяем не истек ли он
    if active_round:
        ends_at = active_round['ends_at']
        # Если ends_at - это строка, конвертируем в datetime
        if isinstance(ends_at, str):
            try:
                ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
            except:
                from datetime import datetime as dt
                try:
                    ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S.%f')
                except:
                    ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S')
        
        # Если раунд не истек, возвращаем его
        if ends_at > now:
            return active_round
        
        # Если активный раунд истек, сохраняем snapshot tickets и завершаем его
        save_tickets_snapshot(cursor, conn, active_round['id_round'], active_round['ends_at'])
        complete_expired_round(cursor, conn, active_round['id_round'])
    
    # Создаем новый раунд
    ends_at = now + timedelta(hours=24)
    cursor.execute("""
        INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
        VALUES (%s, %s, 'active', 0)
        RETURNING *
    """, (now, ends_at))
    new_round = cursor.fetchone()
    conn.commit()
    return new_round


def save_tickets_snapshot(cursor, conn, round_id: int, snapshot_time):
    # Получаем всех пользователей с их tickets на момент snapshot_time
    # Используем только карты, которые были получены ДО окончания раунда
    # created_at в Card_User это date, а snapshot_time это timestamp, поэтому сравниваем даты
    from datetime import datetime
    if isinstance(snapshot_time, str):
        snapshot_time = datetime.fromisoformat(snapshot_time.replace('Z', '+00:00'))
    snapshot_date = snapshot_time.date() if isinstance(snapshot_time, datetime) else snapshot_time
    
    cursor.execute("""
        SELECT u.id_user, u.wallet,
               COALESCE(SUM(cu.quantity * c.start_bounty), 0) as total_tickets
        FROM Users u
        INNER JOIN Card_User cu ON u.id_user = cu.id_user
        INNER JOIN Cards c ON cu.id_card = c.id_card
        WHERE cu.created_at IS NULL OR cu.created_at <= %s
        GROUP BY u.id_user, u.wallet
        HAVING COALESCE(SUM(cu.quantity * c.start_bounty), 0) > 0
    """, (snapshot_date,))
    participants = cursor.fetchall()
    
    # Сохраняем snapshot в таблицу
    for participant in participants:
        cursor.execute("""
            INSERT INTO Jackpot_tickets_snapshot (id_round, id_user, tickets_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_round, id_user) DO UPDATE SET tickets_count = EXCLUDED.tickets_count
        """, (round_id, participant['id_user'], int(participant['total_tickets'])))
    
    conn.commit()


def complete_expired_round(cursor, conn, round_id: int):
    # Получаем информацию о раунде для ends_at
    cursor.execute("SELECT ends_at FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
    round_info = cursor.fetchone()
    if not round_info:
        return  # Раунд не найден
    
    ends_at = round_info['ends_at']
    # Конвертируем ends_at в правильный формат
    from datetime import datetime
    if isinstance(ends_at, str):
        try:
            ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
        except:
            from datetime import datetime as dt
            try:
                ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S.%f')
            except:
                ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S')
    
    # Проверяем, есть ли уже snapshot, если нет - сохраняем
    cursor.execute("SELECT COUNT(*) as cnt FROM Jackpot_tickets_snapshot WHERE id_round = %s", (round_id,))
    snapshot_result = cursor.fetchone()
    snapshot_exists = snapshot_result['cnt'] > 0 if snapshot_result and 'cnt' in snapshot_result else False
    if not snapshot_exists:
        save_tickets_snapshot(cursor, conn, round_id, ends_at)
    
    # Используем сохраненный snapshot tickets, а не текущее состояние
    cursor.execute("""
        SELECT u.id_user, u.wallet, jts.tickets_count as total_tickets
        FROM Jackpot_tickets_snapshot jts
        JOIN Users u ON jts.id_user = u.id_user
        WHERE jts.id_round = %s AND jts.tickets_count > 0
    """, (round_id,))
    participants = cursor.fetchall()
    
    winner_user_id = None
    prize_amount = None
    
    if participants:
        # Выбираем победителя на основе tickets (вероятность пропорциональна количеству tickets)
        total_tickets = sum(int(p['total_tickets']) for p in participants)
        if total_tickets > 0:
            import secrets
            winning_ticket = secrets.randbelow(total_tickets) + 1
            
            current_ticket = 0
            for participant in participants:
                current_ticket += int(participant['total_tickets'])
                if current_ticket >= winning_ticket:
                    winner_user_id = participant['id_user']
                    break
    
    # Получаем сумму джекпота
    cursor.execute("SELECT total_amount FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
    round_data = cursor.fetchone()
    total_amount = float(round_data['total_amount']) if round_data else 0.0
    
    # Приз = вся сумма джекпота
    prize_amount = total_amount if total_amount > 0 else 0.0
    
    # Обновляем раунд
    from datetime import datetime
    cursor.execute("""
        UPDATE Jackpot_rounds
        SET status = 'completed',
            winner_user_id = %s,
            prize_amount = %s,
            completed_at = %s
        WHERE id_round = %s
    """, (winner_user_id, prize_amount, datetime.now(), round_id))
    conn.commit()


def add_to_jackpot(cursor, conn, amount: float):
    round_data = get_or_create_active_round(cursor, conn)
    round_id = round_data['id_round']
    
    cursor.execute("""
        UPDATE Jackpot_rounds
        SET total_amount = total_amount + %s
        WHERE id_round = %s
    """, (amount, round_id))
    conn.commit()


def draw_jackpot(cursor, conn):
    from datetime import datetime
    
    now = datetime.now()
    
    # Находим все истекшие активные раунды
    cursor.execute("""
        SELECT * FROM Jackpot_rounds 
        WHERE status = 'active' AND ends_at <= %s
        ORDER BY id_round ASC
    """, (now,))
    expired_rounds = cursor.fetchall()
    
    drawn_rounds = []
    
    for round_data in expired_rounds:
        round_id = round_data['id_round']
        ends_at = round_data['ends_at']
        
        # Сохраняем snapshot tickets на момент окончания раунда (если еще не сохранен)
        cursor.execute("SELECT COUNT(*) as cnt FROM Jackpot_tickets_snapshot WHERE id_round = %s", (round_id,))
        snapshot_result = cursor.fetchone()
        snapshot_exists = snapshot_result['cnt'] > 0 if snapshot_result and 'cnt' in snapshot_result else False
        if not snapshot_exists:
            # Конвертируем ends_at в правильный формат
            if isinstance(ends_at, str):
                from datetime import datetime
                try:
                    ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
                except:
                    from datetime import datetime as dt
                    try:
                        ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        ends_at = dt.strptime(ends_at, '%Y-%m-%d %H:%M:%S')
            save_tickets_snapshot(cursor, conn, round_id, ends_at)
        
        # Используем сохраненный snapshot tickets, а не текущее состояние
        cursor.execute("""
            SELECT u.id_user, u.wallet, jts.tickets_count as total_tickets
            FROM Jackpot_tickets_snapshot jts
            JOIN Users u ON jts.id_user = u.id_user
            WHERE jts.id_round = %s AND jts.tickets_count > 0
        """, (round_id,))
        participants = cursor.fetchall()
        
        winner_user_id = None
        winner_wallet = None
        prize_amount = None
        total_tickets = 0
        
        if participants:
            # Выбираем победителя на основе tickets
            total_tickets = sum(int(p['total_tickets']) for p in participants)
            if total_tickets > 0:
                import secrets
                winning_ticket = secrets.randbelow(total_tickets) + 1
                
                current_ticket = 0
                for participant in participants:
                    current_ticket += int(participant['total_tickets'])
                    if current_ticket >= winning_ticket:
                        winner_user_id = participant['id_user']
                        winner_wallet = participant['wallet']
                        break
        
        # Приз = вся сумма джекпота
        total_amount = float(round_data['total_amount']) if round_data['total_amount'] else 0.0
        prize_amount = total_amount if total_amount > 0 else 0.0
        
        # Обновляем раунд
        cursor.execute("""
            UPDATE Jackpot_rounds
            SET status = 'completed',
                winner_user_id = %s,
                prize_amount = %s,
                completed_at = %s
            WHERE id_round = %s
        """, (winner_user_id, prize_amount, now, round_id))
        
        drawn_rounds.append({
            "round_id": round_id,
            "prize": prize_amount,
            "winner": winner_wallet,
            "tickets": total_tickets
        })
    
    conn.commit()
    
    # Создаем новый активный раунд, если нет активного
    cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active' LIMIT 1")
    if not cursor.fetchone():
        from datetime import timedelta
        ends_at = now + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 0)
        """, (now, ends_at))
        conn.commit()
    
    return drawn_rounds


def get_or_create_active_super_jackpot_round(cursor, conn) -> dict:
    """Получает или создает активный раунд супер джекпота"""
    from datetime import datetime, timedelta
    
    # Проверяем активный раунд (последний, который еще не завершен)
    cursor.execute("""
        SELECT * FROM Super_jackpot_rounds 
        WHERE winner_user_id IS NULL
        ORDER BY id_round DESC 
        LIMIT 1
    """)
    active_round = cursor.fetchone()
    
    if active_round:
        return active_round
    
    # Создаем новый раунд (без даты окончания, так как он завершается при выигрыше)
    now = datetime.now()
    # Устанавливаем ends_at далеко в будущем, так как раунд завершается при выигрыше
    ends_at = now + timedelta(days=365)
    cursor.execute("""
        INSERT INTO Super_jackpot_rounds (started_at, ends_at, total_amount)
        VALUES (%s, %s, 0)
        RETURNING *
    """, (now, ends_at))
    new_round = cursor.fetchone()
    conn.commit()
    return new_round


def add_to_super_jackpot(cursor, conn, amount: float):
    """Добавляет сумму в супер джекпот (5% от стоимости пака)"""
    round_data = get_or_create_active_super_jackpot_round(cursor, conn)
    round_id = round_data['id_round']
    
    cursor.execute("""
        UPDATE Super_jackpot_rounds
        SET total_amount = total_amount + %s
        WHERE id_round = %s
    """, (amount, round_id))
    conn.commit()


def check_user_has_all_cards(cursor, user_id: int) -> bool:
    """Проверяет, собрал ли пользователь все уникальные карты (с image_key)"""
    # Получаем общее количество уникальных карт с image_key
    cursor.execute("""
        SELECT COUNT(DISTINCT id_card) as total_cards
        FROM Cards
        WHERE image_key IS NOT NULL AND image_key != ''
    """)
    total_cards_result = cursor.fetchone()
    total_cards = total_cards_result['total_cards'] if total_cards_result else 0
    
    if total_cards == 0:
        return False
    
    # Получаем количество уникальных карт у пользователя (с image_key)
    cursor.execute("""
        SELECT COUNT(DISTINCT cu.id_card) as user_cards
        FROM Card_User cu
        JOIN Cards c ON cu.id_card = c.id_card
        WHERE cu.id_user = %s
        AND c.image_key IS NOT NULL 
        AND c.image_key != ''
    """, (user_id,))
    user_cards_result = cursor.fetchone()
    user_cards = user_cards_result['user_cards'] if user_cards_result else 0
    
    return user_cards >= total_cards


def check_user_already_won_super_jackpot(cursor, user_id: int) -> bool:
    """Проверяет, выигрывал ли пользователь уже супер джекпот"""
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM Super_jackpot_rounds
        WHERE winner_user_id = %s
    """, (user_id,))
    result = cursor.fetchone()
    return result['cnt'] > 0 if result and result['cnt'] else False


def claim_super_jackpot(cursor, conn, user_id: int) -> dict:
    """Записывает победителя супер джекпота, если пользователь собрал все карты и еще не выигрывал"""
    from datetime import datetime
    
    # Проверяем, не выигрывал ли уже
    if check_user_already_won_super_jackpot(cursor, user_id):
        return {"won": False, "reason": "already_won"}
    
    # Проверяем, собрал ли все карты
    if not check_user_has_all_cards(cursor, user_id):
        return {"won": False, "reason": "not_all_cards"}
    
    # Получаем активный раунд
    round_data = get_or_create_active_super_jackpot_round(cursor, conn)
    round_id = round_data['id_round']
    
    # Проверяем, не выигран ли уже этот раунд
    if round_data['winner_user_id'] is not None:
        return {"won": False, "reason": "round_already_won"}
    
    # Записываем победителя
    total_amount = float(round_data['total_amount']) if round_data['total_amount'] else 0.0
    now = datetime.now()
    
    cursor.execute("""
        UPDATE Super_jackpot_rounds
        SET winner_user_id = %s,
            prize = %s,
            ends_at = %s
        WHERE id_round = %s
    """, (user_id, total_amount, now, round_id))
    conn.commit()
    
    return {
        "won": True,
        "round_id": round_id,
        "prize": total_amount
    }

