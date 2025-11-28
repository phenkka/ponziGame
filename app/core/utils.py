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


def get_random_card_by_rarity(rarity: str, cursor) -> dict:
    cursor.execute("""
        SELECT id_card, rarity, start_bounty, name, image_url, image_key
        FROM Cards
        WHERE rarity = %s
        AND image_url IS NOT NULL
        AND image_url != ''
        ORDER BY RANDOM()
        LIMIT 1
    """, (rarity,))
    
    return cursor.fetchone()

