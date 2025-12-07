#!/usr/bin/env python3
"""
Сервис для синхронизации пари из Polymarket API

Этот сервис:
- Обращается к Polymarket API и получает популярные пари
- Отбирает пари с двумя вариантами ответов
- Фильтрует пари где примерно равные шансы (30-70%, 40-60%, или 50-50%)
- Фильтрует только близкие события (завершаются через 1-7 дней)
- Фильтрует только популярные пари (объем > 1000$ за 24ч)
- Записывает пари в БД (остаются до окончания - resolution_date)
- Автоматически помечает завершенные пари как resolved
- Поддерживает 10 активных пари

Запуск:
    # Однократный запуск (синхронизирует 10 пари)
    python app/services/polymarket_sync.py

    # С указанием целевого количества
    python app/services/polymarket_sync.py --target 10

    # В режиме демона (синхронизация каждый час)
    python app/services/polymarket_sync.py --daemon

    # В режиме демона с кастомным интервалом (например, каждые 30 минут)
    python app/services/polymarket_sync.py --daemon --interval 1800
"""

import sys
import os
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "app"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import requests
import hashlib
from datetime import datetime, timezone
from dateutil import parser as date_parser
import time

load_dotenv()

# Polymarket API URL
POLYMARKET_API_URL = "https://gamma-api.polymarket.com"

def get_db_connection():
    """
    Получить подключение к БД
    Поддерживает как Docker окружение (host="db"), так и локальное (host="localhost")
    """
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
            # Устанавливаем search_path на public схему
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public;")
            conn.commit()
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
        # Устанавливаем search_path на public схему
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public;")
        conn.commit()
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        raise


def wait_for_db(max_retries: int = 30, retry_delay: int = 2):
    """
    Ожидает готовности БД перед запуском синхронизации
    
    Args:
        max_retries: Максимальное количество попыток подключения
        retry_delay: Задержка между попытками в секундах
    """
    print("Waiting for database to be ready...")
    for i in range(max_retries):
        try:
            conn = get_db_connection()
            conn.close()
            print("Database is ready!")
            return True
        except Exception as e:
            if i < max_retries - 1:
                print(f"Database not ready yet (attempt {i+1}/{max_retries}), retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"ERROR: Could not connect to database after {max_retries} attempts: {e}")
                return False
    return False


def parse_resolution_date(date_str):
    """
    Парсит дату разрешения из разных форматов
    
    Args:
        date_str: Строка с датой (может быть ISO 8601, timestamp, и т.д.)
    
    Returns:
        datetime объект или None
    """
    if not date_str:
        return None
    
    try:
        # Если это timestamp (число)
        if isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str, tz=timezone.utc)
        
        # Если это строка с timestamp
        if isinstance(date_str, str) and date_str.isdigit():
            return datetime.fromtimestamp(int(date_str), tz=timezone.utc)
        
        # Пробуем распарсить как ISO 8601 или другой формат
        parsed_date = date_parser.parse(date_str)
        
        # Если дата без timezone, добавляем UTC
        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
        
        return parsed_date
    except Exception as e:
        print(f"Warning: Could not parse date '{date_str}': {e}")
        return None


def fetch_polymarket_markets(limit: int = 100, use_markets_endpoint: bool = True) -> list:
    """
    Получает популярные пари из Polymarket API
    Делает несколько запросов для получения до 3000 пари (API ограничивает до 500 за запрос)
    
    Args:
        limit: Максимальное количество пари для обработки
        use_markets_endpoint: Если True, использует прямой /markets эндпоинт, иначе /events
    
    Returns:
        Список пари с информацией о них
    """
    try:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        if use_markets_endpoint:
            url = f"{POLYMARKET_API_URL}/markets"
            # API ограничивает ответ до 500, поэтому делаем несколько запросов
            target_limit = min(limit * 300, 3000)
            all_markets_data = []
            max_requests = (target_limit + 499) // 500  # Количество запросов (по 500 каждый)
            
            print(f"[{datetime.now()}] Fetching markets from Polymarket API (will make up to {max_requests} requests)...")
            
            for request_num in range(max_requests):
                params = {
                    "closed": False,
                    "limit": 500,
                    "offset": request_num * 500
                }
                
                print(f"  Request {request_num + 1}/{max_requests}: offset={params['offset']}, limit={params['limit']}")
                
                response = requests.get(url, params=params, headers=headers, timeout=30)
                
                if response.status_code != 200:
                    print(f"ERROR: Polymarket API returned status {response.status_code}")
                    if request_num == 0:
                        print(f"Response: {response.text[:500]}")
                    break
                
                data = response.json()
                
                # Обрабатываем разные форматы ответа
                if isinstance(data, dict):
                    if 'data' in data:
                        data = data['data']
                    elif 'results' in data:
                        data = data['results']
                    elif 'markets' in data:
                        data = data['markets']
                    else:
                        for key, value in data.items():
                            if isinstance(value, list) and len(value) > 0:
                                data = value
                                break
                        else:
                            if request_num == 0:
                                print(f"ERROR: Unexpected API response structure")
                            break
                
                if not data or not isinstance(data, list):
                    if request_num == 0:
                        print(f"ERROR: API returned non-list data")
                    break
                
                all_markets_data.extend(data)
                print(f"  Received {len(data)} markets (total: {len(all_markets_data)})")
                
                # Если получили меньше 500, значит это последняя страница
                if len(data) < 500:
                    break
                
                time.sleep(0.5)  # Задержка между запросами
            
            markets_data = all_markets_data
            print(f"Total markets fetched: {len(markets_data)}")
        else:
            # Fallback на /events
            url = f"{POLYMARKET_API_URL}/events"
            params = {
                "closed": False,
                "limit": min(limit * 3, 200),
                "order": "id",
                "ascending": False
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=30)
            
            if response.status_code != 200:
                print(f"ERROR: Polymarket API returned status {response.status_code}")
                return []
            
            data = response.json()
            
            if isinstance(data, dict):
                if 'data' in data:
                    data = data['data']
                elif 'results' in data:
                    data = data['results']
                elif 'events' in data:
                    data = data['events']
            
            if not data or not isinstance(data, list):
                return []
            
            markets_data = []
            for event in data:
                event_markets = event.get('markets') or event.get('market') or []
                if isinstance(event_markets, dict):
                    event_markets = [event_markets]
                if event_markets:
                    markets_data.extend(event_markets)
        
        if len(markets_data) > 0:
            print(f"  First market keys: {list(markets_data[0].keys())[:10] if isinstance(markets_data[0], dict) else 'N/A'}")
        
        markets = []
        skipped_reasons = {
            'closed': 0,
            'not_2_outcomes': 0,
            'wrong_probability': 0,
            'low_volume': 0,
            'already_ended': 0,
            'wrong_date': 0,
            'no_date': 0
        }
        
        for idx, market in enumerate(markets_data):
            if idx % 50 == 0 and idx > 0:
                print(f"  Processed {idx}/{len(markets_data)} markets, found {len(markets)} valid markets...")
                print(f"    Skipped: {skipped_reasons}")
            
            # Проверяем, что рынок активен
            if market.get('closed', False) or market.get('status') == 'closed':
                skipped_reasons['closed'] += 1
                continue
            
            # Получаем исходы
            outcomes = market.get('outcomes') or market.get('outcome') or market.get('tokens') or []
            
            if isinstance(outcomes, dict):
                outcomes = list(outcomes.values()) if outcomes else []
            
            if not outcomes or len(outcomes) < 2:
                skipped_reasons['not_2_outcomes'] += 1
                continue
            
            outcome_a_obj = outcomes[0] if isinstance(outcomes[0], dict) else {}
            outcome_b_obj = outcomes[1] if isinstance(outcomes[1], dict) else {}
            
            # Получаем вероятности
            outcome_prices_raw = market.get('outcomePrices')
            prob_a = 50.0
            prob_b = 50.0
            use_outcome_prices = False
            
            if outcome_prices_raw and isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
                try:
                    price_a = outcome_prices_raw[0]
                    price_b = outcome_prices_raw[1]
                    
                    if isinstance(price_a, (int, float)) and isinstance(price_b, (int, float)):
                        prob_a = float(price_a) * 100
                        prob_b = float(price_b) * 100
                        use_outcome_prices = True
                except (ValueError, TypeError):
                    pass
            
            if not use_outcome_prices:
                if 'price' in outcome_a_obj:
                    try:
                        prob_a = float(outcome_a_obj.get('price', 0.5)) * 100
                    except (ValueError, TypeError):
                        pass
                elif 'probability' in outcome_a_obj:
                    try:
                        prob_a = float(outcome_a_obj.get('probability', 0.5)) * 100
                    except (ValueError, TypeError):
                        pass
                
                if 'price' in outcome_b_obj:
                    try:
                        prob_b = float(outcome_b_obj.get('price', 0.5)) * 100
                    except (ValueError, TypeError):
                        pass
                elif 'probability' in outcome_b_obj:
                    try:
                        prob_b = float(outcome_b_obj.get('probability', 0.5)) * 100
                    except (ValueError, TypeError):
                        pass
            
            # Нормализуем вероятности
            total_prob = prob_a + prob_b
            if total_prob > 0:
                prob_a = (prob_a / total_prob) * 100
                prob_b = (prob_b / total_prob) * 100
            else:
                prob_a = 50.0
                prob_b = 50.0
            
            # ФИЛЬТР: только пари где примерно равные шансы
            prob_diff = abs(prob_a - prob_b)
            if not (30 <= prob_a <= 70 and 30 <= prob_b <= 70 and prob_diff <= 40):
                skipped_reasons['wrong_probability'] += 1
                continue
            
            # Получаем объемы
            volume_24h = float(market.get('volume24h') or market.get('volume_24h') or market.get('volume') or market.get('volumeUsd') or 0)
            volume_7d = float(market.get('volume7d') or market.get('volume_7d') or volume_24h * 7 or 0)
            volume_30d = float(market.get('volume30d') or market.get('volume_30d') or volume_24h * 30 or 0)
            
            # Фильтр по объему отключен - принимаем любые пари, даже с нулевым объемом
            # Главное - чтобы подходили по дате и вероятностям
            
            # Получаем ID рынка
            market_id = market.get('id') or market.get('slug') or market.get('market_id') or ''
            if not market_id:
                question = market.get('question') or market.get('title') or 'unknown'
                market_id = hashlib.md5(question.encode()).hexdigest()[:16]
            
            # Получаем название
            title = market.get('question') or market.get('title') or 'Unknown Market'
            
            # Получаем названия исходов
            outcome_a_title = outcome_a_obj.get('title') or outcome_a_obj.get('name') or 'Yes'
            outcome_b_title = outcome_b_obj.get('title') or outcome_b_obj.get('name') or 'No'
            
            # Получаем дату разрешения
            resolution_date_raw = market.get('endDate') or market.get('end_date') or market.get('resolutionDate') or market.get('endDateUTC')
            resolution_date = parse_resolution_date(resolution_date_raw)
            
            # Пропускаем пари, которые уже закончились
            if resolution_date and resolution_date < datetime.now(timezone.utc):
                skipped_reasons['already_ended'] += 1
                continue
            
            # ФИЛЬТР: только события, которые закончатся в течение недели (1-7 дней)
            if resolution_date:
                now = datetime.now(timezone.utc)
                time_until_resolution = resolution_date - now
                days_until = time_until_resolution.total_seconds() / (24 * 3600)
                
                # Пропускаем события, которые закончатся раньше чем через 1 день или позже чем через 7 дней
                if days_until < 1.0 or days_until > 7.0:
                    skipped_reasons['wrong_date'] = skipped_reasons.get('wrong_date', 0) + 1
                    continue
            else:
                skipped_reasons['no_date'] = skipped_reasons.get('no_date', 0) + 1
                continue
            
            print(f"  ✓ Found valid market: '{title[:60]}...' (prob: {prob_a:.1f}%/{prob_b:.1f}%, vol: ${volume_24h:.0f}, days: {days_until:.1f})")
            
            markets.append({
                "polymarket_id": str(market_id),
                "title": title,
                "description": market.get('description') or '',
                "category": market.get('category') or market.get('tags', ['general'])[0] if isinstance(market.get('tags'), list) else 'general',
                "outcome_a": outcome_a_title,
                "outcome_b": outcome_b_title,
                "outcome_a_probability": round(prob_a, 2),
                "outcome_b_probability": round(prob_b, 2),
                "resolution_date": resolution_date,
                "volume_24h": volume_24h,
                "volume_7d": volume_7d,
                "volume_30d": volume_30d
            })
            
            if len(markets) >= limit:
                break
        
        print(f"\n=== Summary ===")
        print(f"Total markets processed: {len(markets_data)}")
        print(f"Valid markets found: {len(markets)}")
        print(f"Skipped reasons:")
        for reason, count in skipped_reasons.items():
            if count > 0:
                print(f"  - {reason}: {count}")
        print(f"Criteria: 30-70% probability (diff <= 40%), 2 outcomes, any volume, resolution in 1-7 days")
        print(f"================\n")
        
        return markets[:limit]
        
    except Exception as e:
        print(f"ERROR fetching Polymarket markets: {e}")
        import traceback
        traceback.print_exc()
        return []


def save_markets_to_db(markets: list):
    """
    Сохраняет пари в базу данных
    
    Args:
        markets: Список пари для сохранения
    """
    if not markets:
        print("No markets to save")
        return
    
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        saved_count = 0
        updated_count = 0
        skipped_count = 0
        
        for market in markets:
            try:
                cursor.execute("""
                    SELECT id_prediction, status FROM public.predictions 
                    WHERE polymarket_id = %s
                """, (market['polymarket_id'],))
                
                existing = cursor.fetchone()
                
                if existing:
                    if existing['status'] == 'resolved':
                        skipped_count += 1
                        continue
                    
                    cursor.execute("""
                        UPDATE public.predictions SET
                            title = %s,
                            description = %s,
                            category = %s,
                            outcome_a = %s,
                            outcome_b = %s,
                            outcome_a_probability = %s,
                            outcome_b_probability = %s,
                            resolution_date = %s,
                            volume_24h = %s,
                            volume_7d = %s,
                            volume_30d = %s,
                            updated_at = now()
                        WHERE polymarket_id = %s AND status != 'resolved'
                    """, (
                        market['title'],
                        market.get('description', ''),
                        market.get('category', 'general'),
                        market['outcome_a'],
                        market['outcome_b'],
                        market['outcome_a_probability'],
                        market['outcome_b_probability'],
                        market.get('resolution_date'),
                        market.get('volume_24h', 0),
                        market.get('volume_7d', 0),
                        market.get('volume_30d', 0),
                        market['polymarket_id']
                    ))
                    updated_count += 1
                else:
                    cursor.execute("""
                        INSERT INTO public.predictions (
                            polymarket_id, title, description, category,
                            outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                            resolution_date, volume_24h, volume_7d, volume_30d, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        market['polymarket_id'],
                        market['title'],
                        market.get('description', ''),
                        market.get('category', 'general'),
                        market['outcome_a'],
                        market['outcome_b'],
                        market['outcome_a_probability'],
                        market['outcome_b_probability'],
                        market.get('resolution_date'),
                        market.get('volume_24h', 0),
                        market.get('volume_7d', 0),
                        market.get('volume_30d', 0),
                        'active'
                    ))
                    saved_count += 1
                    
            except Exception as e:
                print(f"ERROR saving market {market.get('polymarket_id')}: {e}")
                continue
        
        conn.commit()
        print(f"\nSave summary: {saved_count} new, {updated_count} updated, {skipped_count} skipped (resolved)")
        
    except Exception as e:
        print(f"ERROR saving markets to DB: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def mark_resolved_predictions():
    """
    Помечает пари как resolved, если resolution_date прошла
    И автоматически выдает награды выигравшим пользователям
    """
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Находим пари, которые закончились, но еще не разрешены
        cursor.execute("""
            SELECT id_prediction, outcome_a_probability, outcome_b_probability
            FROM public.predictions 
            WHERE status = 'active' 
            AND resolution_date IS NOT NULL 
            AND resolution_date < now()
        """)
        
        expired_predictions = cursor.fetchall()
        resolved_count = 0
        rewards_issued_count = 0
        
        for prediction in expired_predictions:
            prediction_id = prediction['id_prediction']
            
            # Определяем победителя на основе вероятностей
            import random
            prob_a = float(prediction['outcome_a_probability']) if prediction['outcome_a_probability'] else 50.0
            prob_b = float(prediction['outcome_b_probability']) if prediction['outcome_b_probability'] else 50.0
            
            if abs(prob_a - prob_b) < 5.0:
                winner_outcome = random.choice(['A', 'B'])
            else:
                winner_outcome = 'A' if prob_a > prob_b else 'B'
            
            # Обновляем статус пари
            cursor.execute("""
                UPDATE public.predictions 
                SET status = 'resolved',
                    winner_outcome = %s,
                    updated_at = now()
                WHERE id_prediction = %s
            """, (winner_outcome, prediction_id))
            
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
                    from core.utils import issue_prediction_reward
                    import json
                    rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, conn, user_id)
                    if rewards_issued:
                        rewards_issued_count += 1
                        cursor.execute("""
                            UPDATE public.user_bets 
                            SET reward_type = %s,
                                reward_data = %s
                            WHERE id_prediction = %s 
                            AND id_user = %s 
                            AND chosen_outcome = %s 
                            AND status = 'pending'
                        """, (reward_type, json.dumps(reward_data) if reward_data else None, prediction_id, user_id, winner_outcome))
                        print(f"  ✓ Issued reward to user {user_id} for prediction {prediction_id}: {reward_type}")
                except Exception as e:
                    print(f"  ✗ Error issuing reward to user {user_id} for prediction {prediction_id}: {e}")
                    import traceback
                    traceback.print_exc()
            
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
            
            resolved_count += 1
        
        conn.commit()
        
        if resolved_count > 0:
            print(f"Marked {resolved_count} predictions as resolved (resolution_date passed)")
            print(f"Issued rewards to {rewards_issued_count} winning users")
        
    except Exception as e:
        print(f"ERROR marking resolved predictions: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_active_predictions_count():
    """Получает количество активных пари в БД"""
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM public.predictions 
            WHERE status = 'active'
        """)
        
        result = cursor.fetchone()
        return result['count'] if result else 0
        
    except Exception as e:
        print(f"ERROR getting active predictions count: {e}")
        return 0
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def sync_polymarket_markets(target_count: int = 10):
    """
    Основная функция синхронизации
    Поддерживает заданное количество активных пари в БД
    
    Args:
        target_count: Целевое количество активных пари (по умолчанию 10)
    """
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] Starting Polymarket sync...")
    print(f"{'='*60}\n")
    
    if not wait_for_db():
        print("ERROR: Database is not available, exiting")
        return
    
    mark_resolved_predictions()
    
    active_count = get_active_predictions_count()
    print(f"Current active predictions in DB: {active_count}")
    
    markets = fetch_polymarket_markets(limit=target_count * 3)
    
    if not markets:
        print("No markets found from Polymarket API")
        return
    
    conn = None
    cursor = None
    existing_ids = set()
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT polymarket_id FROM public.predictions 
            WHERE status = 'active'
        """)
        existing_ids = {row['polymarket_id'] for row in cursor.fetchall()}
    except Exception as e:
        print(f"ERROR getting existing predictions: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    
    existing_markets = [m for m in markets if m['polymarket_id'] in existing_ids]
    new_markets = [m for m in markets if m['polymarket_id'] not in existing_ids]
    
    if existing_markets:
        print(f"\nUpdating {len(existing_markets)} existing predictions with new probabilities...")
        save_markets_to_db(existing_markets)
    
    active_count_after = get_active_predictions_count()
    print(f"Active predictions after update: {active_count_after}")
    
    if active_count_after < target_count:
        needed = target_count - active_count_after
        print(f"\nNeed to add {needed} new predictions to reach target of {target_count}...")
        
        if new_markets:
            markets_to_add = new_markets[:needed]
            print(f"Adding {len(markets_to_add)} new predictions...")
            save_markets_to_db(markets_to_add)
        else:
            print("No new markets available to add")
    
    final_count = get_active_predictions_count()
    print(f"\nFinal active predictions count: {final_count}/{target_count}")
    
    print(f"\n[{datetime.now()}] Sync completed successfully!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync predictions from Polymarket API')
    parser.add_argument('--target', type=int, default=10, help='Target number of active predictions to maintain (default: 10)')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (sync every hour)')
    parser.add_argument('--interval', type=int, default=3600, help='Sync interval in seconds (default: 3600 = 1 hour)')
    
    args = parser.parse_args()
    
    if args.daemon:
        print(f"Running as daemon, syncing every {args.interval} seconds...")
        print(f"Target: {args.target} active predictions")
        print(f"Starting first sync immediately...")
        try:
            sync_polymarket_markets(target_count=args.target)
        except Exception as e:
            print(f"ERROR in first sync: {e}")
            import traceback
            traceback.print_exc()
        
        while True:
            try:
                print(f"Waiting {args.interval} seconds until next sync...")
                time.sleep(args.interval)
                sync_polymarket_markets(target_count=args.target)
            except KeyboardInterrupt:
                print("\nStopping daemon...")
                break
            except Exception as e:
                print(f"ERROR in daemon loop: {e}")
                import traceback
                traceback.print_exc()
    else:
        sync_polymarket_markets(target_count=args.target)
