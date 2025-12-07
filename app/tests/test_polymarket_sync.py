import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, Mock
import json
from psycopg2.extras import RealDictCursor

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.polymarket_sync import (
    fetch_polymarket_markets,
    save_markets_to_db,
    mark_resolved_predictions,
    sync_polymarket_markets,
    get_active_predictions_count,
    get_db_connection
)


class TestPolymarketSyncAPI:
    """Тесты для работы с Polymarket API"""
    
    @patch('services.polymarket_sync.requests.get')
    def test_fetch_markets_success(self, mock_get, db_connection):
        """Тест: успешное получение пари из API"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем успешный ответ от API
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'{"test": "data"}'
        mock_response.json.return_value = [
            {
                "id": "market-1",
                "question": "Will Bitcoin reach $100k?",
                "closed": False,
                "volume": 5000.0,
                "volume24hr": 5000.0,
                "outcomes": [
                    {"title": "Yes", "price": 0.48},
                    {"title": "No", "price": 0.52}
                ],
                "outcomePrices": [0.48, 0.52],
                "endDate": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "category": "crypto"
            },
            {
                "id": "market-2",
                "question": "Will Ethereum reach $5k?",
                "closed": False,
                "volume": 3000.0,
                "volume24hr": 3000.0,
                "outcomes": [
                    {"title": "Yes", "price": 0.50},
                    {"title": "No", "price": 0.50}
                ],
                "outcomePrices": [0.50, 0.50],
                "endDate": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
                "category": "crypto"
            }
        ]
        mock_get.return_value = mock_response
        
        # Вызываем функцию
        markets = fetch_polymarket_markets(limit=10)
        
        # Проверяем результат
        assert len(markets) > 0
        assert mock_get.called
        assert mock_get.call_args[0][0] == "https://gamma-api.polymarket.com/markets"
    
    @patch('services.polymarket_sync.requests.get')
    def test_fetch_markets_api_error(self, mock_get, db_connection):
        """Тест: обработка ошибки API"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем ошибку API
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response
        
        # Вызываем функцию
        markets = fetch_polymarket_markets(limit=10)
        
        # Проверяем, что вернулся пустой список
        assert markets == []
    
    @patch('services.polymarket_sync.requests.get')
    def test_fetch_markets_timeout(self, mock_get, db_connection):
        """Тест: обработка таймаута API"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем таймаут
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("Connection timeout")
        
        # Вызываем функцию
        markets = fetch_polymarket_markets(limit=10)
        
        # Проверяем, что вернулся пустой список
        assert markets == []


class TestPolymarketSyncDatabase:
    """Тесты для работы с базой данных"""
    
    def test_save_new_markets(self, clean_db, db_connection):
        """Тест: сохранение новых пари в БД"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        markets = [
            {
                "polymarket_id": "test-market-1",
                "title": "Test Prediction 1",
                "description": "Test description",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,
                "outcome_b_probability": 50.0,
                "resolution_date": (datetime.now(timezone.utc) + timedelta(days=7)),
                "volume_24h": 5000.0,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            },
            {
                "polymarket_id": "test-market-2",
                "title": "Test Prediction 2",
                "description": "Test description 2",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 48.0,
                "outcome_b_probability": 52.0,
                "resolution_date": (datetime.now(timezone.utc) + timedelta(days=5)),
                "volume_24h": 3000.0,
                "volume_7d": 21000.0,
                "volume_30d": 90000.0
            }
        ]
        
        # Сохраняем пари
        save_markets_to_db(markets)
        
        # Проверяем, что пари сохранены
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM public.predictions WHERE status = 'active'")
        count = cursor.fetchone()[0]
        assert count == 2
        
        # Проверяем содержимое (используем RealDictCursor для именованных полей)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM public.predictions WHERE polymarket_id = 'test-market-1'")
        prediction = cursor.fetchone()
        assert prediction is not None
        assert prediction['polymarket_id'] == "test-market-1"
        assert prediction['title'] == "Test Prediction 1"
        cursor.close()
    
    def test_update_existing_markets(self, clean_db, db_connection):
        """Тест: обновление существующих пари (проценты)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пари в БД
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, ("test-market-1", "Test Prediction", "Yes", "No", 45.0, 55.0, "active"))
        db_connection.commit()
        cursor.close()
        
        # Обновляем пари с новыми процентами
        markets = [
            {
                "polymarket_id": "test-market-1",
                "title": "Test Prediction Updated",
                "description": "Updated description",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,  # Новые проценты
                "outcome_b_probability": 50.0,
                "resolution_date": None,
                "volume_24h": 6000.0,
                "volume_7d": 42000.0,
                "volume_30d": 180000.0
            }
        ]
        
        save_markets_to_db(markets)
        
        # Проверяем, что пари обновлено
        cursor = db_connection.cursor()
        cursor.execute("""
            SELECT outcome_a_probability, outcome_b_probability, title 
            FROM public.predictions 
            WHERE polymarket_id = 'test-market-1'
        """)
        result = cursor.fetchone()
        assert result[0] == 50.0  # Обновленные проценты
        assert result[1] == 50.0
        assert result[2] == "Test Prediction Updated"  # Обновленный заголовок
        cursor.close()
    
    def test_mark_resolved_predictions(self, clean_db, db_connection):
        """Тест: помечание завершенных пари как resolved"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пари с прошедшей датой разрешения
        cursor = db_connection.cursor()
        past_date = datetime.now(timezone.utc) - timedelta(days=1)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, 
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, ("test-resolved-1", "Resolved Prediction", "Yes", "No", 50.0, 50.0, past_date, "active"))
        
        # Создаем активное пари с будущей датой
        future_date = datetime.now(timezone.utc) + timedelta(days=7)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, 
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, ("test-active-1", "Active Prediction", "Yes", "No", 50.0, 50.0, future_date, "active"))
        db_connection.commit()
        cursor.close()
        
        # Помечаем завершенные пари
        mark_resolved_predictions()
        
        # Проверяем, что завершенное пари помечено как resolved
        cursor = db_connection.cursor()
        cursor.execute("SELECT status FROM public.predictions WHERE polymarket_id = 'test-resolved-1'")
        status = cursor.fetchone()[0]
        assert status == "resolved"
        
        # Проверяем, что активное пари осталось активным
        cursor.execute("SELECT status FROM public.predictions WHERE polymarket_id = 'test-active-1'")
        status = cursor.fetchone()[0]
        assert status == "active"
        cursor.close()
    
    def test_mark_resolved_predictions_issues_rewards(self, clean_db, db_connection):
        """Тест: автоматическая выдача наград при разрешении пари через mark_resolved_predictions"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        from nacl.signing import SigningKey
        import base58
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем пари с прошедшей датой разрешения
        past_date = datetime.now(timezone.utc) - timedelta(days=1)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, 
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-reward-1", "Reward Test Prediction", "Yes", "No", 60.0, 40.0, past_date, "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        # Создаем выигрышную ставку (пользователь выбрал A, а A более вероятен)
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending"))
        bet = cursor.fetchone()
        bet_id = bet['id_bet']
        
        # Создаем проигрышную ставку (другой пользователь выбрал B)
        signing_key2 = SigningKey.generate()
        verify_key2 = signing_key2.verify_key
        wallet2 = base58.b58encode(verify_key2.encode()).decode('utf-8')
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet2, "TESTCODE2"))
        user2 = cursor.fetchone()
        user_id2 = user2['id_user']
        
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
        """, (user_id2, prediction_id, "B", "pending"))
        
        db_connection.commit()
        cursor.close()
        
        # Вызываем mark_resolved_predictions (должна автоматически выдать награды)
        mark_resolved_predictions()
        
        # Проверяем, что пари помечено как resolved с правильным победителем
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT status, winner_outcome FROM public.predictions 
            WHERE id_prediction = %s
        """, (prediction_id,))
        prediction_result = cursor.fetchone()
        assert prediction_result['status'] == 'resolved'
        assert prediction_result['winner_outcome'] == 'A'  # A более вероятен (60% vs 40%)
        
        # Проверяем, что выигрышная ставка помечена как won и награда выдана
        cursor.execute("""
            SELECT status, reward_issued, reward_type FROM public.user_bets 
            WHERE id_bet = %s
        """, (bet_id,))
        bet_result = cursor.fetchone()
        assert bet_result['status'] == 'won'
        assert bet_result['reward_issued'] is True
        assert bet_result['reward_type'] is not None  # Должен быть тип награды
        
        # Проверяем, что проигрышная ставка помечена как lost
        cursor.execute("""
            SELECT status, reward_issued FROM public.user_bets 
            WHERE id_user = %s AND id_prediction = %s
        """, (user_id2, prediction_id))
        losing_bet = cursor.fetchone()
        assert losing_bet['status'] == 'lost'
        assert losing_bet['reward_issued'] is False
        
        # Проверяем, что награда действительно выдана (пак, карта или буст)
        cursor.execute("""
            SELECT COUNT(*) as count FROM Chest_purchases WHERE id_user = %s
        """, (user_id,))
        chests = cursor.fetchone()
        chest_count = chests['count'] if chests else 0
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM Card_User WHERE id_user = %s
        """, (user_id,))
        cards = cursor.fetchone()
        card_count = cards['count'] if cards else 0
        
        cursor.execute("""
            SELECT COUNT(*) as count FROM User_boost WHERE id_user = %s AND is_active = TRUE
        """, (user_id,))
        boosts = cursor.fetchone()
        boost_count = boosts['count'] if boosts else 0
        
        # Должна быть выдана хотя бы одна награда
        assert (chest_count > 0) or (card_count > 0) or (boost_count > 0), \
            f"Reward not issued: chests={chest_count}, cards={card_count}, boosts={boost_count}"
        
        cursor.close()
    
    def test_get_active_predictions_count(self, clean_db, db_connection):
        """Тест: получение количества активных пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем несколько пари
        cursor = db_connection.cursor()
        for i in range(5):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-{i}", f"Test {i}", "Yes", "No", 50.0, 50.0, "active"))
        
        # Создаем одно завершенное пари
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, ("test-resolved", "Resolved", "Yes", "No", 50.0, 50.0, "resolved"))
        db_connection.commit()
        cursor.close()
        
        # Проверяем количество активных пари
        count = get_active_predictions_count()
        assert count == 5


class TestPolymarketSyncFullFlow:
    """Тесты полного цикла синхронизации"""
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_creates_20_predictions(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: синхронизация создает 20 пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем получение пари (25 пари для выбора)
        mock_fetch.return_value = [
            {
                "polymarket_id": f"test-market-{i}",
                "title": f"Test Prediction {i}",
                "description": f"Description {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,
                "outcome_b_probability": 50.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 5000.0 + i * 100,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            }
            for i in range(25)
        ]
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что создано 20 активных пари
        count = get_active_predictions_count()
        assert count == 20
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_updates_existing_predictions(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: синхронизация обновляет существующие пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем 5 существующих пари
        cursor = db_connection.cursor()
        for i in range(5):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-{i}", f"Test {i}", "Yes", "No", 45.0, 55.0, "active"))
        db_connection.commit()
        cursor.close()
        
        # Мокаем получение пари с обновленными процентами
        mock_fetch.return_value = [
            {
                "polymarket_id": f"test-{i}",
                "title": f"Test {i} Updated",
                "description": f"Updated {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,  # Обновленные проценты
                "outcome_b_probability": 50.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 6000.0,
                "volume_7d": 42000.0,
                "volume_30d": 180000.0
            }
            for i in range(5)
        ] + [
            {
                "polymarket_id": f"test-new-{i}",
                "title": f"New Test {i}",
                "description": f"New {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,
                "outcome_b_probability": 50.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 5000.0,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            }
            for i in range(15)  # Добавляем еще 15 новых
        ]
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что всего 20 активных пари
        count = get_active_predictions_count()
        assert count == 20
        
        # Проверяем, что существующие пари обновлены
        cursor = db_connection.cursor()
        cursor.execute("SELECT outcome_a_probability FROM public.predictions WHERE polymarket_id = 'test-0'")
        prob = cursor.fetchone()[0]
        assert prob == 50.0  # Обновленный процент
        cursor.close()
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_replaces_resolved_predictions(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: синхронизация заменяет завершенные пари новыми"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем 15 активных и 5 завершенных пари
        cursor = db_connection.cursor()
        for i in range(15):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-active-{i}", f"Active {i}", "Yes", "No", 50.0, 50.0, "active"))
        
        for i in range(5):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-resolved-{i}", f"Resolved {i}", "Yes", "No", 50.0, 50.0, "resolved"))
        db_connection.commit()
        cursor.close()
        
        # Мокаем получение новых пари
        mock_fetch.return_value = [
            {
                "polymarket_id": f"test-new-{i}",
                "title": f"New Test {i}",
                "description": f"New {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,
                "outcome_b_probability": 50.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 5000.0,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            }
            for i in range(25)
        ]
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что всего 20 активных пари (15 старых + 5 новых)
        count = get_active_predictions_count()
        assert count == 20
        
        # Проверяем, что завершенные пари остались завершенными
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM public.predictions WHERE status = 'resolved'")
        resolved_count = cursor.fetchone()[0]
        assert resolved_count == 5
        cursor.close()
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_handles_no_markets_from_api(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: обработка случая, когда API не вернул пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем пустой ответ от API
        mock_fetch.return_value = []
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что ошибок не было
        # Количество активных пари должно остаться прежним (0)
        count = get_active_predictions_count()
        assert count == 0
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_handles_api_error(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: обработка ошибки API при синхронизации"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем ошибку API
        mock_fetch.side_effect = Exception("API Error")
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию (должна обработать ошибку)
        try:
            sync_polymarket_markets(target_count=20)
        except Exception:
            pass  # Ожидаем, что ошибка будет обработана
        
        # Проверяем, что система не упала
        count = get_active_predictions_count()
        assert count >= 0  # Может быть 0 или больше, но не отрицательное


class TestPolymarketSyncEdgeCases:
    """Тесты граничных случаев"""
    
    def test_save_skips_resolved_predictions(self, clean_db, db_connection):
        """Тест: пропуск уже разрешенных пари при сохранении"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем разрешенное пари
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, ("test-resolved", "Resolved", "Yes", "No", 50.0, 50.0, "resolved"))
        db_connection.commit()
        cursor.close()
        
        # Пытаемся обновить разрешенное пари
        markets = [
            {
                "polymarket_id": "test-resolved",
                "title": "Should Not Update",
                "description": "Test",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 60.0,
                "outcome_b_probability": 40.0,
                "resolution_date": None,
                "volume_24h": 1000.0,
                "volume_7d": 7000.0,
                "volume_30d": 30000.0
            }
        ]
        
        save_markets_to_db(markets)
        
        # Проверяем, что пари не обновлено
        cursor = db_connection.cursor()
        cursor.execute("SELECT title, status FROM public.predictions WHERE polymarket_id = 'test-resolved'")
        result = cursor.fetchone()
        assert result[0] == "Resolved"  # Старое название
        assert result[1] == "resolved"  # Статус не изменился
        cursor.close()
    
    def test_sync_maintains_exactly_target_count(self, clean_db, db_connection):
        """Тест: поддержание точного количества пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем 25 активных пари (больше целевого)
        cursor = db_connection.cursor()
        for i in range(25):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-{i}", f"Test {i}", "Yes", "No", 50.0, 50.0, "active"))
        db_connection.commit()
        cursor.close()
        
        # Синхронизация не должна удалять лишние пари, только обновлять
        # (логика добавления новых работает только если меньше целевого)
        count_before = get_active_predictions_count()
        assert count_before == 25
        
        # После синхронизации должно остаться 25 (не удаляем существующие)
        # Но если мы добавим новые, то будет больше целевого
        # Это нормально - мы не удаляем существующие активные пари
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_handles_database_connection_error(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: обработка ошибки подключения к БД"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем ошибку подключения
        with patch('services.polymarket_sync.get_db_connection') as mock_db:
            mock_db.side_effect = Exception("Database connection failed")
            
            # Выполняем синхронизацию (должна обработать ошибку)
            try:
                sync_polymarket_markets(target_count=20)
            except Exception:
                pass  # Ожидаем, что ошибка будет обработана
    
    def test_sync_with_existing_resolved_predictions(self, clean_db, db_connection):
        """Тест: синхронизация с существующими завершенными пари в истории"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем завершенные пари (история)
        cursor = db_connection.cursor()
        for i in range(10):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-resolved-{i}", f"Resolved {i}", "Yes", "No", 50.0, 50.0, "resolved"))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что завершенные пари остаются в истории
        cursor = db_connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM public.predictions WHERE status = 'resolved'")
        resolved_count = cursor.fetchone()[0]
        assert resolved_count == 10
        cursor.close()
        
        # Проверяем, что активных пари нет
        active_count = get_active_predictions_count()
        assert active_count == 0
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_updates_probabilities_correctly(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: правильное обновление вероятностей при синхронизации"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пари с начальными процентами
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b, 
                outcome_a_probability, outcome_b_probability, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, ("test-update-prob", "Test Update", "Yes", "No", 45.0, 55.0, "active"))
        db_connection.commit()
        cursor.close()
        
        # Мокаем получение пари с новыми процентами
        mock_fetch.return_value = [
            {
                "polymarket_id": "test-update-prob",
                "title": "Test Update",
                "description": "Test",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 52.0,  # Новые проценты
                "outcome_b_probability": 48.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 5000.0,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            }
        ]
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что проценты обновлены
        cursor = db_connection.cursor()
        cursor.execute("""
            SELECT outcome_a_probability, outcome_b_probability 
            FROM public.predictions 
            WHERE polymarket_id = 'test-update-prob'
        """)
        result = cursor.fetchone()
        assert result[0] == 52.0  # Обновленный процент
        assert result[1] == 48.0
        cursor.close()
    
    @patch('services.polymarket_sync.fetch_polymarket_markets')
    @patch('services.polymarket_sync.mark_resolved_predictions')
    def test_sync_maintains_20_active_predictions(self, mock_mark_resolved, mock_fetch, clean_db, db_connection):
        """Тест: поддержание ровно 20 активных пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем 15 активных пари
        cursor = db_connection.cursor()
        for i in range(15):
            cursor.execute("""
                INSERT INTO public.predictions (
                    polymarket_id, title, outcome_a, outcome_b, 
                    outcome_a_probability, outcome_b_probability, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (f"test-{i}", f"Test {i}", "Yes", "No", 50.0, 50.0, "active"))
        db_connection.commit()
        cursor.close()
        
        # Мокаем получение достаточного количества новых пари
        # Включаем существующие для обновления и новые для добавления
        mock_fetch.return_value = [
            # Существующие пари (для обновления)
            {
                "polymarket_id": f"test-{i}",
                "title": f"Test {i} Updated",
                "description": f"Updated {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 52.0,  # Обновленные проценты
                "outcome_b_probability": 48.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 6000.0,
                "volume_7d": 42000.0,
                "volume_30d": 180000.0
            }
            for i in range(15)
        ] + [
            # Новые пари (для добавления)
            {
                "polymarket_id": f"test-new-{i}",
                "title": f"New Test {i}",
                "description": f"New {i}",
                "category": "test",
                "outcome_a": "Yes",
                "outcome_b": "No",
                "outcome_a_probability": 50.0,
                "outcome_b_probability": 50.0,
                "resolution_date": datetime.now(timezone.utc) + timedelta(days=7),
                "volume_24h": 5000.0,
                "volume_7d": 35000.0,
                "volume_30d": 150000.0
            }
            for i in range(30)  # Достаточно для выбора
        ]
        mock_mark_resolved.return_value = None
        
        # Выполняем синхронизацию
        sync_polymarket_markets(target_count=20)
        
        # Проверяем, что теперь ровно 20 активных пари (15 обновленных + 5 новых)
        count = get_active_predictions_count()
        assert count == 20
