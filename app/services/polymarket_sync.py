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
import json
from datetime import datetime, timezone
from dateutil import parser as date_parser
import time
from typing import Optional

load_dotenv()

# Polymarket API URL
POLYMARKET_API_URL = os.getenv("POLYMARKET_API_URL", "https://gamma-api.polymarket.com")

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
        if isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str, tz=timezone.utc)

        if isinstance(date_str, str) and date_str.isdigit():
            return datetime.fromtimestamp(int(date_str), tz=timezone.utc)

        parsed_date = date_parser.parse(date_str)

        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)

        return parsed_date
    except Exception as e:
        print(f"Warning: Could not parse date '{date_str}': {e}")
        return None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _probability_to_decimal_odds(probability_percent: float) -> Optional[float]:
    try:
        if probability_percent <= 0:
            return None
        return 100.0 / float(probability_percent)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def fetch_polymarket_top_popular_markets(target_count: int = 10, max_fetch: int = 3000) -> list:
    try:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        url = f"{POLYMARKET_API_URL}/markets"
        all_markets_data = []
        max_requests = (min(max_fetch, 3000) + 499) // 500

        for request_num in range(max_requests):
            params = {
                "closed": False,
                "limit": 500,
                "offset": request_num * 500
            }

            response = requests.get(url, params=params, headers=headers, timeout=30)
            if response.status_code != 200:
                print(f"ERROR: Polymarket API returned status {response.status_code}")
                try:
                    print(f"Response: {response.text[:500]}")
                except Exception:
                    pass
                return []

            data = response.json()
            if isinstance(data, dict):
                if 'data' in data:
                    data = data['data']
                elif 'results' in data:
                    data = data['results']
                elif 'markets' in data:
                    data = data['markets']
                else:
                    for _, value in data.items():
                        if isinstance(value, list) and len(value) > 0:
                            data = value
                            break

            if not data or not isinstance(data, list):
                break

            all_markets_data.extend(data)
            if len(data) < 500:
                break

            time.sleep(0.2)

        now = datetime.now(timezone.utc)
        skipped = {
            "closed": 0,
            "not_2_outcomes": 0,
            "no_prices": 0,
            "bad_prices": 0,
            "no_date": 0,
            "past_date": 0,
        }
        mapped = []
        for market in all_markets_data:
            if market.get('closed', False) or market.get('status') == 'closed':
                skipped["closed"] += 1
                continue

            outcomes = market.get('outcomes') or market.get('outcome') or market.get('tokens') or []
            if isinstance(outcomes, str) and outcomes.strip():
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []
            if isinstance(outcomes, dict):
                outcomes = list(outcomes.values()) if outcomes else []
            if outcomes and isinstance(outcomes, list) and all(isinstance(o, str) for o in outcomes):
                outcomes = [{"title": o} for o in outcomes]
            if not outcomes or len(outcomes) < 2:
                skipped["not_2_outcomes"] += 1
                continue

            outcome_a_obj = outcomes[0] if isinstance(outcomes[0], dict) else {}
            outcome_b_obj = outcomes[1] if isinstance(outcomes[1], dict) else {}

            outcome_prices_raw = market.get('outcomePrices')
            outcome_prices = None
            if isinstance(outcome_prices_raw, str) and outcome_prices_raw.strip():
                try:
                    parsed = json.loads(outcome_prices_raw)
                    outcome_prices = parsed
                except Exception:
                    outcome_prices = None
            elif isinstance(outcome_prices_raw, (list, tuple)):
                outcome_prices = list(outcome_prices_raw)
            elif isinstance(outcome_prices_raw, dict):
                outcome_prices = list(outcome_prices_raw.values())

            price_a = None
            price_b = None
            if outcome_prices and isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                price_a = outcome_prices[0]
                price_b = outcome_prices[1]
            elif 'price' in outcome_a_obj and 'price' in outcome_b_obj:
                price_a = outcome_a_obj.get('price')
                price_b = outcome_b_obj.get('price')

            price_a_f = _safe_float(price_a, default=0.0)
            price_b_f = _safe_float(price_b, default=0.0)
            if price_a_f <= 0 or price_b_f <= 0:
                skipped["no_prices"] += 1
                continue

            if price_a_f > 1.0 and price_a_f <= 100.0:
                price_a_f = price_a_f / 100.0
            if price_b_f > 1.0 and price_b_f <= 100.0:
                price_b_f = price_b_f / 100.0

            if price_a_f <= 0 or price_b_f <= 0 or price_a_f > 1.0 or price_b_f > 1.0:
                skipped["bad_prices"] += 1
                continue

            prob_a = price_a_f * 100
            prob_b = price_b_f * 100
            total_prob = prob_a + prob_b
            if total_prob <= 0:
                continue
            prob_a = (prob_a / total_prob) * 100
            prob_b = (prob_b / total_prob) * 100

            resolution_date_raw = market.get('endDate') or market.get('end_date') or market.get('resolutionDate') or market.get('endDateUTC')
            resolution_date = parse_resolution_date(resolution_date_raw)
            if not resolution_date:
                skipped["no_date"] += 1
                continue
            if resolution_date < now:
                skipped["past_date"] += 1
                continue

            volume_24h = _safe_float(
                market.get('volume24h')
                or market.get('volume24hr')
                or market.get('volume_24h')
                or market.get('volume')
                or market.get('volumeUsd'),
                default=0.0
            )
            volume_7d = _safe_float(market.get('volume7d') or market.get('volume_7d') or volume_24h * 7, default=0.0)
            volume_30d = _safe_float(market.get('volume30d') or market.get('volume_30d') or volume_24h * 30, default=0.0)

            market_id = market.get('id') or market.get('slug') or market.get('market_id') or ''
            if not market_id:
                question = market.get('question') or market.get('title') or 'unknown'
                market_id = hashlib.md5(question.encode()).hexdigest()[:16]

            title = market.get('question') or market.get('title') or 'Unknown Market'
            outcome_a_title = outcome_a_obj.get('title') or outcome_a_obj.get('name') or 'Yes'
            outcome_b_title = outcome_b_obj.get('title') or outcome_b_obj.get('name') or 'No'

            mapped.append({
                "polymarket_id": str(market_id),
                "title": title,
                "description": market.get('description') or '',
                "category": market.get('category') or market.get('tags', ['general'])[0] if isinstance(market.get('tags'), list) else 'general',
                "outcome_a": outcome_a_title,
                "outcome_b": outcome_b_title,
                "outcome_a_probability": round(prob_a, 2),
                "outcome_b_probability": round(prob_b, 2),
                "outcome_a_odds": _probability_to_decimal_odds(prob_a),
                "outcome_b_odds": _probability_to_decimal_odds(prob_b),
                "resolution_date": resolution_date,
                "volume_24h": volume_24h,
                "volume_7d": volume_7d,
                "volume_30d": volume_30d,
            })

        mapped.sort(key=lambda m: (_safe_float(m.get('volume_24h'), 0.0)), reverse=True)
        print(
            "Top-popular fetch: "
            f"fetched={len(all_markets_data)} "
            f"mapped={len(mapped)} "
            f"skipped={skipped} "
            f"returning={min(target_count, len(mapped))}"
        )
        return mapped[:target_count]
    except Exception as e:
        print(f"ERROR fetching top popular markets: {e}")
        return []


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

        criteria_prob_diff_max = float(os.getenv("PREDICTIONS_PROB_DIFF_MAX", "30"))
        criteria_days_min = float(os.getenv("PREDICTIONS_DAYS_MIN", "14"))
        criteria_days_max = float(os.getenv("PREDICTIONS_DAYS_MAX", "21"))
        
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
            if prob_diff > criteria_prob_diff_max:
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
            
            # ФИЛЬТР: только события, которые закончатся через 2-3 недели (по умолчанию 14-21 дней)
            if resolution_date:
                now = datetime.now(timezone.utc)
                time_until_resolution = resolution_date - now
                days_until = time_until_resolution.total_seconds() / (24 * 3600)
                
                if days_until < criteria_days_min or days_until > criteria_days_max:
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
        print(f"Criteria: prob diff <= {criteria_prob_diff_max}%, 2 outcomes, any volume, resolution in {criteria_days_min}-{criteria_days_max} days")
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
        return {"saved": 0, "updated": 0, "skipped": 0, "errors": 0}
    
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        saved_count = 0
        updated_count = 0
        skipped_count = 0
        errors_count = 0
        
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
                            outcome_a_odds = %s,
                            outcome_b_odds = %s,
                            resolution_date = %s,
                            volume_24h = %s,
                            volume_7d = %s,
                            volume_30d = %s,
                            status = 'active',
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
                        market.get('outcome_a_odds'),
                        market.get('outcome_b_odds'),
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
                            outcome_a_odds, outcome_b_odds,
                            resolution_date, volume_24h, volume_7d, volume_30d, status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        market['polymarket_id'],
                        market['title'],
                        market.get('description', ''),
                        market.get('category', 'general'),
                        market['outcome_a'],
                        market['outcome_b'],
                        market['outcome_a_probability'],
                        market['outcome_b_probability'],
                        market.get('outcome_a_odds'),
                        market.get('outcome_b_odds'),
                        market.get('resolution_date'),
                        market.get('volume_24h', 0),
                        market.get('volume_7d', 0),
                        market.get('volume_30d', 0),
                        'active'
                    ))
                    saved_count += 1
                    
            except Exception as e:
                print(f"ERROR saving market {market.get('polymarket_id')}: {e}")
                errors_count += 1
                continue
        
        conn.commit()
        print(f"\nSave summary: {saved_count} new, {updated_count} updated, {skipped_count} skipped (resolved)")
        return {"saved": saved_count, "updated": updated_count, "skipped": skipped_count, "errors": errors_count}
        
    except Exception as e:
        print(f"ERROR saving markets to DB: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
        return {"saved": 0, "updated": 0, "skipped": 0, "errors": len(markets) if markets else 0}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def mark_resolved_predictions():
    """Помечает пари как resolved, если resolution_date прошла."""
    conn = None
    cursor = None
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Находим пари, которые закончились, но еще не разрешены.
        # FOR UPDATE SKIP LOCKED защищает от параллельных запусков (одно и то же пари обработает только один воркер).
        cursor.execute("""
            SELECT id_prediction, outcome_a_probability, outcome_b_probability, outcome_a_odds, outcome_b_odds
            FROM public.predictions
            WHERE status = 'active'
              AND resolution_date IS NOT NULL
              AND resolution_date < now()
            FOR UPDATE SKIP LOCKED
        """)
        
        expired_predictions = cursor.fetchall()
        resolved_count = 0
        payouts_count = 0
        
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

            # Обновляем статус пари (идемпотентно: только если еще active)
            cursor.execute("""
                UPDATE public.predictions
                SET status = 'resolved',
                    winner_outcome = %s,
                    updated_at = now()
                WHERE id_prediction = %s
                  AND status = 'active'
            """, (winner_outcome, prediction_id))

            if cursor.rowcount == 0:
                continue

            odds = None
            if winner_outcome == 'A':
                odds = prediction.get('outcome_a_odds')
            elif winner_outcome == 'B':
                odds = prediction.get('outcome_b_odds')
            odds_val = float(odds) if odds is not None else 1.0
            try:
                import math
                if not math.isfinite(odds_val) or odds_val <= 0:
                    odds_val = 1.0
            except Exception:
                odds_val = 1.0

            cursor.execute("""
                SELECT id_bet, id_user, bet_tickets
                FROM public.user_bets
                WHERE id_prediction = %s
                  AND chosen_outcome = %s
                  AND status = 'pending'
                  AND payout_tickets IS NULL
                FOR UPDATE
            """, (prediction_id, winner_outcome))
            winning_bets = cursor.fetchall()
            for b in winning_bets:
                bet_tickets = int(b.get('bet_tickets') or 0)
                payout_tickets = int(math.ceil(bet_tickets * odds_val))
                cursor.execute("""
                    UPDATE public.users
                    SET tickets_bonus = tickets_bonus + %s
                    WHERE id_user = %s
                """, (payout_tickets, b['id_user']))
                cursor.execute("""
                    UPDATE public.user_bets
                    SET payout_tickets = %s
                    WHERE id_bet = %s
                """, (payout_tickets, b['id_bet']))
                payouts_count += 1
            
            # Помечаем выигравшие ставки
            cursor.execute("""
                UPDATE public.user_bets 
                SET status = 'won',
                    resolved_at = now()
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
            print(f"Paid out tickets to {payouts_count} winning bets")
        
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


def sync_polymarket_top_popular_markets(target_count: int = 10):
    print(f"[{datetime.now()}] Top-popular sync starting (target={target_count})")
    if not wait_for_db():
        return

    mark_resolved_predictions()

    markets = fetch_polymarket_top_popular_markets(target_count=target_count)
    if not markets:
        print("Top-popular sync: no markets returned from Polymarket")
        return

    print(f"Top-popular sync: saving {len(markets)} markets")
    save_stats = save_markets_to_db(markets)

    if not save_stats or (int(save_stats.get('saved') or 0) + int(save_stats.get('updated') or 0)) <= 0:
        print("Top-popular sync: skip cancelling actives because no markets were saved/updated")
        return

    keep_ids = [m.get('polymarket_id') for m in markets if m.get('polymarket_id')]
    if not keep_ids:
        return

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            UPDATE public.predictions p
            SET status = 'cancelled',
                updated_at = now()
            WHERE p.status = 'active'
              AND p.polymarket_id <> ALL(%s)
              AND p.polymarket_id NOT LIKE 'DEV_TEST_%%'
              AND NOT EXISTS (
                SELECT 1 FROM public.user_bets ub
                WHERE ub.id_prediction = p.id_prediction
                  AND ub.status = 'pending'
              )
            """,
            (keep_ids,)
        )
        conn.commit()
    except Exception as e:
        print(f"ERROR cancelling predictions not in top list: {e}")
        if conn:
            conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Sync predictions from Polymarket API')
    parser.add_argument('--target', type=int, default=10, help='Target number of active predictions to maintain (default: 10)')
    parser.add_argument('--daemon', action='store_true', help='Run as daemon (sync every hour)')
    parser.add_argument('--interval', type=int, default=3600, help='Sync interval in seconds (default: 3600 = 1 hour)')
    parser.add_argument('--mode', type=str, default='filtered', choices=['filtered', 'top_popular'])
    
    args = parser.parse_args()

    sync_fn = sync_polymarket_markets if args.mode == 'filtered' else sync_polymarket_top_popular_markets
    
    if args.daemon:
        print(f"Running as daemon, syncing every {args.interval} seconds...")
        print(f"Target: {args.target} active predictions")
        print(f"Starting first sync immediately...")
        try:
            sync_fn(target_count=args.target)
        except Exception as e:
            print(f"ERROR in first sync: {e}")
            import traceback
            traceback.print_exc()
        
        while True:
            try:
                print(f"Waiting {args.interval} seconds until next sync...")
                time.sleep(args.interval)
                sync_fn(target_count=args.target)
            except KeyboardInterrupt:
                print("\nStopping daemon...")
                break
            except Exception as e:
                print(f"ERROR in daemon loop: {e}")
                import traceback
                traceback.print_exc()
    else:
        sync_fn(target_count=args.target)
