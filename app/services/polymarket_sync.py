#!/usr/bin/env python3
"""
Сервис для синхронизации пари из Polymarket API

Этот сервис:
- Обращается к Polymarket API и получает популярные пари
- Отбирает пари с двумя вариантами ответов
- Фильтрует пари где примерно 50/50 (45-55% вероятность)
- Фильтрует только популярные пари (объем > 1000$ за 24ч)
- Записывает пари в БД (остаются до окончания - resolution_date)
- Автоматически помечает завершенные пари как resolved

Запуск:
    # Однократный запуск (синхронизирует 50 пари)
    python app/services/polymarket_sync.py

    # С указанием лимита
    python app/services/polymarket_sync.py --limit 100

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
    
    Args:
        limit: Максимальное количество пари для обработки
        use_markets_endpoint: Если True, использует прямой /markets эндпоинт, иначе /events
    
    Returns:
        Список пари с информацией о них
    """
    try:
        # Используем прямой эндпоинт /markets для получения рынков
        # Согласно документации: https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide
        if use_markets_endpoint:
            url = f"{POLYMARKET_API_URL}/markets"
            params = {
                "closed": False,  # Только активные рынки (boolean, не строка!)
                "limit": min(limit * 3, 200),  # Запрашиваем больше для фильтрации
                "order": "volume",  # Сортируем по объему (популярные первыми)
                "ascending": False  # По убыванию объема
            }
        else:
            # Fallback на /events (старый способ)
            url = f"{POLYMARKET_API_URL}/events"
            params = {
                "closed": False,  # Только активные события (boolean, не строка!)
                "limit": min(limit * 3, 200),
                "order": "id",
                "ascending": False
            }
        
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        print(f"[{datetime.now()}] Fetching markets from Polymarket API...")
        print(f"  URL: {url}")
        print(f"  Params: {params}")
        from urllib.parse import urlencode
        full_url = f"{url}?{urlencode(params)}"
        print(f"  Full request URL: {full_url}")
        
        response = requests.get(url, params=params, headers=headers, timeout=30)
        
        print(f"  Response status: {response.status_code}")
        print(f"  Response size: {len(response.content)} bytes")
        
        if response.status_code != 200:
            print(f"ERROR: Polymarket API returned status {response.status_code}")
            print(f"Response headers: {dict(response.headers)}")
            print(f"Response body (first 1000 chars): {response.text[:1000]}")
            return []
        
        data = response.json()
        print(f"  Response JSON keys: {list(data.keys()) if isinstance(data, dict) else 'N/A (list)'}")
        print(f"  Response data type: {type(data)}")
        
        # Обрабатываем разные форматы ответа API
        # Согласно документации, ответ может быть массивом или объектом с данными
        if isinstance(data, dict):
            if 'data' in data:
                data = data['data']
            elif 'results' in data:
                data = data['results']
            elif 'markets' in data:
                data = data['markets']
            elif 'events' in data:
                data = data['events']
            else:
                # Ищем массив в значениях
                for key, value in data.items():
                    if isinstance(value, list) and len(value) > 0:
                        print(f"Found list in key '{key}' with {len(value)} items")
                        data = value
                        break
                else:
                    print(f"ERROR: Unexpected API response structure. Keys: {list(data.keys())[:10]}")
                    return []
        
        if not data or not isinstance(data, list):
            print(f"ERROR: API returned non-list data: {type(data)}")
            return []
        
        # Если используем /markets эндпоинт, data уже содержит markets
        # Если используем /events, нужно извлечь markets из events
        if use_markets_endpoint:
            print(f"Processing {len(data)} markets directly from Polymarket API...")
            markets_data = data
        else:
            print(f"Processing {len(data)} events from Polymarket API...")
            # Извлекаем все markets из events
            markets_data = []
            for event in data:
                event_markets = event.get('markets') or event.get('market') or []
                if isinstance(event_markets, dict):
                    event_markets = [event_markets]
                if event_markets:
                    markets_data.extend(event_markets)
            print(f"  Extracted {len(markets_data)} markets from {len(data)} events")
        
        if len(markets_data) > 0:
            print(f"  First market keys: {list(markets_data[0].keys()) if isinstance(markets_data[0], dict) else 'N/A'}")
            if isinstance(markets_data[0], dict):
                sample_market = markets_data[0]
                print(f"  Sample market structure:")
                print(f"    - question: {sample_market.get('question', sample_market.get('title', 'N/A'))[:60]}")
                print(f"    - closed: {sample_market.get('closed', 'N/A')}")
                print(f"    - volume: {sample_market.get('volume', sample_market.get('volume24h', 'N/A'))}")
                print(f"    - has outcomes: {'outcomes' in sample_market or 'outcomePrices' in sample_market}")
        
        markets = []
        skipped_reasons = {
            'closed': 0,
            'not_2_outcomes': 0,
            'wrong_probability': 0,
            'low_volume': 0,
            'already_ended': 0
        }
        
        for idx, market in enumerate(markets_data):
            if idx % 50 == 0 and idx > 0:
                print(f"  Processed {idx}/{len(markets_data)} markets, found {len(markets)} valid markets...")
                print(f"    Skipped: {skipped_reasons}")
            
            # Обрабатываем market напрямую (не из event)
            # Проверяем, что рынок активен
            if market.get('closed', False) or market.get('status') == 'closed':
                skipped_reasons['closed'] += 1
                continue
            
            # Получаем исходы - проверяем разные возможные поля согласно API
            # Может быть outcomes (массив) или outcomePrices (массив цен)
            outcomes = market.get('outcomes') or market.get('outcome') or market.get('tokens') or []
            
            # Нормализуем outcomes в список
            if isinstance(outcomes, dict):
                outcomes = list(outcomes.values()) if outcomes else []
            
            # Проверяем, что есть хотя бы 2 исхода (для названий)
            if not outcomes or len(outcomes) < 2:
                skipped_reasons['not_2_outcomes'] += 1
                if idx < 3:  # Логируем первые несколько для отладки
                    print(f"    Market {idx}: {len(outcomes) if outcomes else 0} outcomes (need 2)")
                continue
            
            outcome_a_obj = outcomes[0] if isinstance(outcomes[0], dict) else {}
            outcome_b_obj = outcomes[1] if isinstance(outcomes[1], dict) else {}
            
            # Получаем вероятности - проверяем outcomePrices сначала (согласно документации)
            outcome_prices_raw = market.get('outcomePrices')
            prob_a = 50.0
            prob_b = 50.0
            use_outcome_prices = False
            
            # Проверяем, что outcomePrices это валидный список чисел
            if outcome_prices_raw and isinstance(outcome_prices_raw, list) and len(outcome_prices_raw) >= 2:
                try:
                    price_a = outcome_prices_raw[0]
                    price_b = outcome_prices_raw[1]
                    
                    # Проверяем, что это числа (не строки, не None, не специальные символы)
                    if isinstance(price_a, (int, float)) and isinstance(price_b, (int, float)):
                        # Это уже числа, используем их
                        prob_a = float(price_a) * 100
                        prob_b = float(price_b) * 100
                        use_outcome_prices = True
                    elif isinstance(price_a, str) and isinstance(price_b, str):
                        # Если это строки, проверяем что это не специальные символы
                        price_a_clean = price_a.strip().strip('[]').strip()
                        price_b_clean = price_b.strip().strip('[]').strip()
                        
                        # Пропускаем если это пустая строка или только символы
                        if price_a_clean and price_a_clean not in ['[', ']', '', 'None'] and price_b_clean and price_b_clean not in ['[', ']', '', 'None']:
                            try:
                                prob_a = float(price_a_clean) * 100
                                prob_b = float(price_b_clean) * 100
                                use_outcome_prices = True
                            except (ValueError, TypeError):
                                pass
                except (ValueError, TypeError, IndexError) as e:
                    # Если не удалось распарсить, используем outcomes
                    if idx < 3:
                        print(f"    Warning: Could not parse outcomePrices: {e}, using outcomes instead")
            
            # Если outcomePrices не сработал, парсим из outcomes
            if not use_outcome_prices:
                prob_a = 50.0
                prob_b = 50.0
                
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
            
            # ФИЛЬТР: только пари где примерно 50/50 (от 45% до 55% - более строгий фильтр)
            if not (45 <= prob_a <= 55 and 45 <= prob_b <= 55):
                skipped_reasons['wrong_probability'] += 1
                if idx < 3:  # Логируем первые несколько для отладки
                    print(f"    Market {idx}: probability {prob_a:.1f}% / {prob_b:.1f}% (need 45-55%)")
                continue
            
            # Получаем объемы - согласно документации поле называется 'volume'
            volume_24h = float(market.get('volume24h') or market.get('volume_24h') or market.get('volume') or market.get('volumeUsd') or 0)
            volume_7d = float(market.get('volume7d') or market.get('volume_7d') or volume_24h * 7 or 0)
            volume_30d = float(market.get('volume30d') or market.get('volume_30d') or volume_24h * 30 or 0)
            
            # Фильтр: только популярные (минимум 1000$ за 24ч)
            if volume_24h < 1000:
                skipped_reasons['low_volume'] += 1
                if idx < 3:  # Логируем первые несколько для отладки
                    print(f"    Market {idx}: volume 24h = ${volume_24h:.2f} (need > $1000)")
                continue
            
            # Получаем ID рынка
            market_id = market.get('id') or market.get('slug') or market.get('market_id') or ''
            if not market_id:
                question = market.get('question') or market.get('title') or 'unknown'
                market_id = hashlib.md5(question.encode()).hexdigest()[:16]
            
            # Получаем название
            title = market.get('question') or market.get('title') or 'Unknown Market'
            
            # Получаем названия исходов из outcomes
            outcome_a_title = outcome_a_obj.get('title') or outcome_a_obj.get('name') or 'Yes'
            outcome_b_title = outcome_b_obj.get('title') or outcome_b_obj.get('name') or 'No'
            
            # Получаем дату разрешения
            resolution_date_raw = market.get('endDate') or market.get('end_date') or market.get('resolutionDate') or market.get('endDateUTC')
            resolution_date = parse_resolution_date(resolution_date_raw)
            
            # Пропускаем пари, которые уже закончились
            if resolution_date and resolution_date < datetime.now(timezone.utc):
                skipped_reasons['already_ended'] += 1
                continue
            
            # Логируем успешно найденное пари
            print(f"  ✓ Found valid market: '{title[:60]}...' (prob: {prob_a:.1f}%/{prob_b:.1f}%, vol: ${volume_24h:.0f})")
            
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
        print(f"Criteria: 45-55% probability, 2 outcomes, volume > $1000, not ended")
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
                # Проверяем, существует ли уже такое пари
                cursor.execute("""
                    SELECT id_prediction, status FROM public.predictions 
                    WHERE polymarket_id = %s
                """, (market['polymarket_id'],))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Если пари уже разрешено, не обновляем
                    if existing['status'] == 'resolved':
                        skipped_count += 1
                        continue
                    
                    # Обновляем существующее пари (обновляем проценты и другие данные)
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
                    if updated_count <= 3:  # Логируем первые несколько обновлений
                        print(f"  ↻ Updated: '{market['title'][:50]}...' (prob: {market['outcome_a_probability']:.1f}%/{market['outcome_b_probability']:.1f}%)")
                else:
                    # Создаем новое пари
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
    """
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Помечаем пари как resolved, если resolution_date прошла
        cursor.execute("""
            UPDATE public.predictions 
            SET status = 'resolved', updated_at = now()
            WHERE status = 'active' 
            AND resolution_date IS NOT NULL 
            AND resolution_date < now()
        """)
        
        resolved_count = cursor.rowcount
        conn.commit()
        
        if resolved_count > 0:
            print(f"Marked {resolved_count} predictions as resolved (resolution_date passed)")
        
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


def sync_polymarket_markets(target_count: int = 20):
    """
    Основная функция синхронизации
    Поддерживает заданное количество активных пари в БД
    
    Args:
        target_count: Целевое количество активных пари (по умолчанию 20)
    """
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] Starting Polymarket sync...")
    print(f"{'='*60}\n")
    
    # Ожидаем готовности БД
    if not wait_for_db():
        print("ERROR: Database is not available, exiting")
        return
    
    # Сначала помечаем завершенные пари
    mark_resolved_predictions()
    
    # Проверяем количество активных пари
    active_count = get_active_predictions_count()
    print(f"Current active predictions in DB: {active_count}")
    
    # Получаем все доступные пари из Polymarket (больше чем нужно для обновления)
    markets = fetch_polymarket_markets(limit=target_count * 3)  # Запрашиваем больше для выбора
    
    if not markets:
        print("No markets found from Polymarket API")
        return
    
    # Получаем список уже существующих polymarket_id ДО обновления
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
    
    # Разделяем пари на существующие (для обновления) и новые (для добавления)
    existing_markets = [m for m in markets if m['polymarket_id'] in existing_ids]
    new_markets = [m for m in markets if m['polymarket_id'] not in existing_ids]
    
    # Сначала обновляем существующие пари (обновляем проценты)
    if existing_markets:
        print(f"\nUpdating {len(existing_markets)} existing predictions with new probabilities...")
        save_markets_to_db(existing_markets)
    
    # Проверяем, сколько активных пари осталось после обновления
    active_count_after = get_active_predictions_count()
    print(f"Active predictions after update: {active_count_after}")
    
    # Если активных пари меньше целевого количества, добавляем новые
    if active_count_after < target_count:
        needed = target_count - active_count_after
        print(f"\nNeed to add {needed} new predictions to reach target of {target_count}...")
        
        if new_markets:
            # Берем только нужное количество новых пари
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
    parser.add_argument('--target', type=int, default=20, help='Target number of active predictions to maintain (default: 20)')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (sync every hour)')
    parser.add_argument('--interval', type=int, default=3600, help='Sync interval in seconds (default: 3600 = 1 hour)')
    
    args = parser.parse_args()
    
    if args.daemon:
        print(f"Running as daemon, syncing every {args.interval} seconds...")
        print(f"Target: {args.target} active predictions")
        print(f"Starting first sync immediately...")
        # Выполняем первую синхронизацию сразу
        try:
            sync_polymarket_markets(target_count=args.target)
        except Exception as e:
            print(f"ERROR in first sync: {e}")
            import traceback
            traceback.print_exc()
        
        # Затем запускаем цикл с интервалом
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
        # Однократный запуск
        sync_polymarket_markets(target_count=args.target)
