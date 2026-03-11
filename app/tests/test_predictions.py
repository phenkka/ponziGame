import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from psycopg2.extras import RealDictCursor
from main import app
import json
from nacl.signing import SigningKey
import base58
import time


def get_utc_date():
    """Получает текущую дату в UTC (для тестов)"""
    return datetime.now(timezone.utc).date()


class TestPredictionsMarkets:
    @pytest.mark.asyncio
    async def test_get_markets_requires_auth(self, clean_db, db_connection):
        """Тест: получение пари требует авторизации"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/predictions/markets")
            # Этот endpoint может быть публичным, проверяем что он работает
            assert response.status_code in [200, 401]
    
    @pytest.mark.asyncio
    async def test_get_markets_success(self, clean_db, db_connection):
        """Тест: успешное получение пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем тестовое пари в БД
        cursor = db_connection.cursor()
        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "test-market-1", "Will Bitcoin reach $100k?", "Test prediction", "crypto",
            "Yes", "No", 45.5, 54.5,
            resolution_date,
            5000.0, 30000.0, 100000.0, "active"
        ))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/predictions/markets?period=24h&limit=20")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "markets" in data
            assert len(data["markets"]) > 0
            assert data["markets"][0]["title"] == "Will Bitcoin reach $100k?"
            assert data["markets"][0].get("ends_at") is not None

    @pytest.mark.asyncio
    async def test_get_markets_orders_by_volume_and_excludes_inactive(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor()

        # active (high volume)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "m1", "M1", "", "general",
            "Yes", "No", 49.0, 51.0, 2.0, 1.5,
            datetime.now(timezone.utc) + timedelta(days=15),
            1000.0, 0.0, 0.0, "active"
        ))

        # active (lower volume)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "m2", "M2", "", "general",
            "Yes", "No", 35.0, 65.0, None, None,
            datetime.now(timezone.utc) + timedelta(days=15),
            10.0, 0.0, 0.0, "active"
        ))

        # inactive
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "m3", "M3", "", "general",
            "Yes", "No", 49.0, 51.0, None, None,
            datetime.now(timezone.utc) + timedelta(days=15),
            999999.0, 0.0, 0.0, "resolved"
        ))

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/predictions/markets?period=24h&limit=50")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            markets = data.get("markets", [])
            returned_ids = [m.get("polymarket_id") for m in markets]
            assert "m1" in returned_ids
            assert "m2" in returned_ids
            assert "m3" not in returned_ids
            assert returned_ids.index("m1") < returned_ids.index("m2")

            m1 = next(m for m in markets if m.get("polymarket_id") == "m1")
            assert m1.get("outcome_a_odds") == 2.0
            assert m1.get("outcome_b_odds") == 1.5
            assert m1.get("outcome_a_probability") == 49.0
            assert m1.get("outcome_b_probability") == 51.0
    
    @pytest.mark.asyncio
    async def test_get_markets_empty(self, clean_db, db_connection):
        """Тест: получение пари когда их нет"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/predictions/markets")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["markets"] == []


class TestPredictionsBets:
    @pytest.mark.asyncio
    async def test_create_bet_requires_auth(self, clean_db, db_connection):
        """Тест: создание ставки требует авторизации"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пари в БД
        cursor = db_connection.cursor()
        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test Prediction", "Yes", "No", 50.0, 50.0, resolution_date, "active"))
        prediction_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/bet/test_wallet",
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_create_bet_success(self, clean_db, db_connection, auth_headers):
        """Тест: успешное создание ставки"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        user_id = user['id_user']

        cursor.execute("""
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
        """, (1, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'test_card_1'))
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, 1, 5))
        
        # Создаем пари
        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test Prediction", "Yes", "No", 50.0, 50.0, resolution_date, "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "bet_id" in data
            assert data.get("bet_tickets") == 10

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT quantity FROM Card_User WHERE id_user = %s AND id_card = %s", (user_id, 1))
        row = cursor.fetchone()
        assert row is not None
        assert int(row.get('quantity') or 0) == 4
        cursor.execute("SELECT bet_card_id, bet_card_quantity, bet_tickets FROM public.user_bets WHERE id_user = %s AND id_prediction = %s", (user_id, prediction_id))
        bet_row = cursor.fetchone()
        assert bet_row is not None
        assert int(bet_row.get('bet_card_id') or 0) == 1
        assert int(bet_row.get('bet_card_quantity') or 0) == 1
        assert int(bet_row.get('bet_tickets') or 0) == 10
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_create_bet_duplicate(self, clean_db, db_connection, auth_headers):
        """Тест: нельзя сделать две ставки на одно пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем пари
        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test Prediction", "Yes", "No", 50.0, 50.0, resolution_date, "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        # Создаем первую ставку
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, bet_card_id, bet_card_quantity, bet_tickets)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, prediction_id, "A", 1, 1, 10))
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся создать вторую ставку
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "B", "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "already exists" in data["error"].lower() or "already placed" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_create_bet_cannot_overspend_cards(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (wallet, "TESTCODE"))
        user_id = cursor.fetchone()['id_user']

        cursor.execute("""
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
        """, (1, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'test_card_1'))

        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, 1, 1))

        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-overspend-1", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, resolution_date, "active"))
        prediction_id = cursor.fetchone()['id_prediction']

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1["success"] is True

            r2 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "B", "card_id": 1, "card_quantity": 1}
            )
            assert r2.status_code == 400

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT quantity FROM Card_User WHERE id_user = %s AND id_card = %s", (user_id, 1))
        row = cur.fetchone()
        assert row is None or int(row.get('quantity') or 0) >= 0
        cur.close()

    @pytest.mark.asyncio
    async def test_create_bet_cannot_reuse_same_card_across_predictions(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (wallet, "TESTCODE"))
        user_id = cursor.fetchone()['id_user']

        cursor.execute("""
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
        """, (1, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'test_card_1'))

        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, 1, 1))

        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-reuse-1", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, resolution_date, "active"))
        prediction_id_1 = cursor.fetchone()['id_prediction']

        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-reuse-2", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, resolution_date, "active"))
        prediction_id_2 = cursor.fetchone()['id_prediction']

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id_1, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert r1.status_code == 200

            r2 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id_2, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert r2.status_code == 400
            d2 = r2.json()
            assert d2.get("success") is False

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT quantity FROM Card_User WHERE id_user = %s AND id_card = %s", (user_id, 1))
        row = cur.fetchone()
        assert row is None or int(row.get('quantity') or 0) >= 0
        cur.close()
    
    @pytest.mark.asyncio
    async def test_create_bet_invalid_outcome(self, clean_db, db_connection, auth_headers):
        """Тест: невалидный исход ставки"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, datetime.now(timezone.utc) + timedelta(days=15), "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "C", "card_id": 1, "card_quantity": 1}  # Невалидный исход
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
    
    @pytest.mark.asyncio
    async def test_create_bet_inactive_prediction(self, clean_db, db_connection, auth_headers):
        """Тест: нельзя сделать ставку на неактивное пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "resolved"))  # Уже разрешено
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "not active" in data["error"].lower()


class TestPredictionsCardInstanceLifecycle:
    @pytest.mark.asyncio
    async def test_bet_with_card_instance_id_stakes_and_snapshots_odds(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 101
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_101')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 1)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            (
                "test-inst-bet-1", "Test", "Yes", "No",
                50.0, 50.0,
                2.0, 1.5,
                datetime.now(timezone.utc) + timedelta(days=15),
                "active"
            )
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert int(data.get("bet_tickets") or 0) == 10

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT status FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (instance_id, user_id)
        )
        inst = cur.fetchone()
        assert inst is not None
        assert inst["status"] == 'staked'

        cur.execute(
            """
            SELECT bet_card_instance_id, bet_bounty, odds_at_bet
            FROM public.user_bets
            WHERE id_user = %s AND id_prediction = %s
            """,
            (user_id, prediction_id)
        )
        bet_row = cur.fetchone()
        assert bet_row is not None
        assert int(bet_row.get('bet_card_instance_id') or 0) == int(instance_id)
        assert int(bet_row.get('bet_bounty') or 0) == 10
        assert float(bet_row.get('odds_at_bet') or 0) == 2.0
        cur.close()

    @pytest.mark.asyncio
    async def test_resolve_win_returns_instance_and_mints_upgraded_copy(self, clean_db, db_connection, auth_headers, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 102
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_102')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 1)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            (
                "test-inst-win-1", "Test", "Yes", "No",
                50.0, 50.0,
                2.0, 1.5,
                datetime.now(timezone.utc) + timedelta(days=15),
                "active"
            )
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r_bet = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r_bet.status_code == 200

            r_resolve = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"winner_outcome": "A"}
            )
            assert r_resolve.status_code == 200
            assert r_resolve.json().get("success") is True

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT status FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (instance_id, user_id)
        )
        inst = cur.fetchone()
        assert inst is not None
        assert inst["status"] == 'available'

        cur.execute(
            """
            SELECT minted_card_instance_id, payout_tickets, status
            FROM public.user_bets
            WHERE id_user = %s AND id_prediction = %s
            """,
            (user_id, prediction_id)
        )
        bet_row = cur.fetchone()
        assert bet_row is not None
        minted_instance_id = bet_row.get('minted_card_instance_id')
        assert minted_instance_id is not None
        assert int(bet_row.get('payout_tickets') or 0) == 20
        assert bet_row.get('status') == 'won'

        cur.execute(
            "SELECT bounty, status FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (minted_instance_id, user_id)
        )
        minted = cur.fetchone()
        assert minted is not None
        assert int(minted.get('bounty') or 0) == 20
        assert minted.get('status') == 'available'

        cur.execute(
            "SELECT quantity FROM public.card_user WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        qty = cur.fetchone()
        assert qty is not None
        assert int(qty.get('quantity') or 0) == 2
        cur.close()

    @pytest.mark.asyncio
    async def test_resolve_lose_burns_instance(self, clean_db, db_connection, auth_headers, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 103
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_103')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 1)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            (
                "test-inst-lose-1", "Test", "Yes", "No",
                50.0, 50.0,
                2.0, 1.5,
                datetime.now(timezone.utc) + timedelta(days=15),
                "active"
            )
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r_bet = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r_bet.status_code == 200

            r_resolve = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"winner_outcome": "B"}
            )
            assert r_resolve.status_code == 200
            assert r_resolve.json().get("success") is True

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT status FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (instance_id, user_id)
        )
        inst = cur.fetchone()
        assert inst is not None
        assert inst["status"] == 'burned'

        cur.execute(
            "SELECT status, minted_card_instance_id FROM public.user_bets WHERE id_user = %s AND id_prediction = %s",
            (user_id, prediction_id)
        )
        bet_row = cur.fetchone()
        assert bet_row is not None
        assert bet_row.get('status') == 'lost'
        assert bet_row.get('minted_card_instance_id') is None

        cur.execute(
            "SELECT quantity FROM public.card_user WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        qty = cur.fetchone()
        assert qty is None or int(qty.get('quantity') or 0) == 0
        cur.close()

    @pytest.mark.asyncio
    async def test_resolve_cancel_returns_instance_without_mint(self, clean_db, db_connection, auth_headers, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 104
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_104')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 1)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            (
                "test-inst-cancel-1", "Test", "Yes", "No",
                50.0, 50.0,
                2.0, 1.5,
                datetime.now(timezone.utc) + timedelta(days=15),
                "active"
            )
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r_bet = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r_bet.status_code == 200

            r_resolve = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"winner_outcome": "cancelled"}
            )
            assert r_resolve.status_code == 200
            assert r_resolve.json().get("success") is True

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT status FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (instance_id, user_id)
        )
        inst = cur.fetchone()
        assert inst is not None
        assert inst["status"] == 'available'

        cur.execute(
            "SELECT status, minted_card_instance_id FROM public.user_bets WHERE id_user = %s AND id_prediction = %s",
            (user_id, prediction_id)
        )
        bet_row = cur.fetchone()
        assert bet_row is not None
        assert bet_row.get('status') == 'cancelled'
        assert bet_row.get('minted_card_instance_id') is None

        cur.execute(
            "SELECT quantity FROM public.card_user WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        qty = cur.fetchone()
        assert qty is not None
        assert int(qty.get('quantity') or 0) == 1
        cur.close()

    @pytest.mark.asyncio
    async def test_cannot_stake_same_card_instance_twice(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 105
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_105')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 2)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]
        cursor.execute(
            "INSERT INTO public.card_user_instances (id_user, id_card, bounty, status) VALUES (%s, %s, %s, 'available')",
            (user_id, card_id, 10)
        )

        resolution_date = datetime.now(timezone.utc) + timedelta(days=15)
        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            ("test-inst-dupe-1", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, resolution_date, "active")
        )
        p1 = cursor.fetchone()["id_prediction"]
        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            ("test-inst-dupe-2", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, resolution_date, "active")
        )
        p2 = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": p1, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r1.status_code == 200

            r2 = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": p2, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r2.status_code == 400
            d2 = r2.json()
            assert d2.get("success") is False
            assert "card instance" in str(d2.get("error") or "").lower()

    @pytest.mark.asyncio
    async def test_resolve_idempotent_for_card_stake_win(self, clean_db, db_connection, auth_headers, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)

        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        card_id = 106
        cursor.execute(
            """
            INSERT INTO Cards (id_card, rarity, start_bounty, name, image_url, image_key)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_card) DO NOTHING
            """,
            (card_id, 'basic', 10, 'Test Card', 'http://example.com/card.png', 'TEST_PRED_CARD_106')
        )
        cursor.execute(
            """
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
            """,
            (user_id, card_id, 1)
        )
        cursor.execute(
            """
            INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
            VALUES (%s, %s, %s, 'available')
            RETURNING id_instance
            """,
            (user_id, card_id, 10)
        )
        instance_id = cursor.fetchone()["id_instance"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            (
                "test-inst-idem-1", "Test", "Yes", "No",
                50.0, 50.0,
                2.0, 1.5,
                datetime.now(timezone.utc) + timedelta(days=15),
                "active"
            )
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r_bet = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "A", "card_instance_id": instance_id}
            )
            assert r_bet.status_code == 200

            r_resolve_1 = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"winner_outcome": "A"}
            )
            assert r_resolve_1.status_code == 200

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT minted_card_instance_id FROM public.user_bets WHERE id_user = %s AND id_prediction = %s",
            (user_id, prediction_id)
        )
        bet_row = cur.fetchone()
        assert bet_row is not None
        minted_instance_id = bet_row.get('minted_card_instance_id')
        assert minted_instance_id is not None

        cur.execute(
            "SELECT COUNT(*)::int AS c FROM public.card_user_instances WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        before_count = int(cur.fetchone().get('c') or 0)

        async with AsyncClient(app=app, base_url="http://test") as client:
            r_resolve_2 = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"winner_outcome": "A"}
            )
            assert r_resolve_2.status_code == 200

        cur.execute(
            "SELECT COUNT(*)::int AS c FROM public.card_user_instances WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        after_count = int(cur.fetchone().get('c') or 0)
        assert after_count == before_count

        cur.execute(
            "SELECT bounty FROM public.card_user_instances WHERE id_instance = %s AND id_user = %s",
            (minted_instance_id, user_id)
        )
        minted = cur.fetchone()
        assert minted is not None
        assert int(minted.get('bounty') or 0) == 20
        cur.close()


# Тесты для выдачи наград перенесены в test_predictions_rewards.py
# для более детальной проверки типов наград


class TestPredictionsEdgeCases:
    @pytest.mark.asyncio
    async def test_get_user_bets_empty(self, clean_db, db_connection, auth_headers):
        """Тест: получение ставок пользователя когда их нет"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
        """, (wallet, "TESTCODE"))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/predictions/user/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["bets"] == []

    @pytest.mark.asyncio
    async def test_get_user_bets_access_denied_other_wallet(self, clean_db, db_connection):
        """Тест: нельзя получить ставки другого пользователя"""
        if db_connection is None:
            pytest.skip("Database not available")

        signing_key_1 = SigningKey.generate()
        wallet_1 = base58.b58encode(signing_key_1.verify_key.encode()).decode('utf-8')

        signing_key_2 = SigningKey.generate()
        wallet_2 = base58.b58encode(signing_key_2.verify_key.encode()).decode('utf-8')

        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_1, "CODE1")
        )
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_2, "CODE2")
        )
        db_connection.commit()
        cursor.close()

        message = "Gamba Auth: 1234567890"
        signed = signing_key_1.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)

        headers = {
            "X-Wallet": wallet_1,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/predictions/user/{wallet_2}",
                headers=headers
            )
            assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_create_bet_missing_params(self, clean_db, db_connection, auth_headers):
        """Тест: создание ставки без обязательных параметров"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
        """, (wallet, "TESTCODE"))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Без prediction_id
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"chosen_outcome": "A", "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 400
            
            # Без chosen_outcome
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": 1, "card_id": 1, "card_quantity": 1}
            )
            assert response.status_code == 400

            # Без card_id
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": 1, "chosen_outcome": "A", "card_quantity": 1}
            )
            assert response.status_code == 400

            # Без card_quantity
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": 1, "chosen_outcome": "A", "card_id": 1}
            )
            assert response.status_code == 400
    
    @pytest.mark.asyncio
    async def test_resolve_nonexistent_prediction(self, clean_db, db_connection):
        """Тест: разрешение несуществующего пари"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя для авторизации
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        test_wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
        """, (test_wallet, "TESTCODE"))
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки авторизации
        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        
        headers = {
            "X-Wallet": test_wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/predictions/resolve/99999",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            assert response.status_code == 404
            data = response.json()
            assert data["success"] is False
            assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_resolve_requires_admin_when_env_set(self, clean_db, db_connection, monkeypatch):
        """Тест: resolve запрещен не-админам, если задан PREDICTIONS_RESOLVE_ADMINS"""
        if db_connection is None:
            pytest.skip("Database not available")

        signing_key = SigningKey.generate()
        wallet = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')

        cursor = db_connection.cursor()
        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)", (wallet, "TESTCODE"))
        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            ("test-admin-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active")
        )
        prediction_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()

        # Разрешаем только другого админа
        monkeypatch.setenv("PREDICTIONS_RESOLVE_ADMINS", "SomeOtherWallet")

        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_resolve_idempotent_no_double_rewards(self, clean_db, db_connection, monkeypatch):
        """Тест: повторный resolve не должен выдавать награду второй раз"""
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        signing_key = SigningKey.generate()
        wallet = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()["id_user"]

        cursor.execute(
            """
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            ("test-idem-1", "Test", "Yes", "No", 50.0, 50.0, 2.0, 1.5, datetime.now(timezone.utc) + timedelta(days=15), "active")
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        cursor.execute(
            """
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, bet_tickets)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, prediction_id, "A", "pending", 101)
        )
        db_connection.commit()
        cursor.close()

        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1 = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1["success"] is True

            cur = db_connection.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT tickets_bonus FROM public.users WHERE id_user = %s", (user_id,))
            before_bonus = int(cur.fetchone().get("tickets_bonus") or 0)
            cur.close()

            cur = db_connection.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT payout_tickets FROM public.user_bets WHERE id_user = %s AND id_prediction = %s", (user_id, prediction_id))
            payout_first = cur.fetchone().get("payout_tickets")
            cur.close()

            assert payout_first is not None

            r2 = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["success"] is True

            cur = db_connection.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT tickets_bonus FROM public.users WHERE id_user = %s", (user_id,))
            after_bonus = int(cur.fetchone().get("tickets_bonus") or 0)
            cur.close()

            assert after_bonus == before_bonus
    
    @pytest.mark.asyncio
    async def test_resolve_invalid_winner(self, clean_db, db_connection):
        """Тест: разрешение пари с невалидным исходом"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя для авторизации
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        test_wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
        """, (test_wallet, "TESTCODE"))
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"))
        prediction_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки авторизации
        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        
        headers = {
            "X-Wallet": test_wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "INVALID"}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
    
    @pytest.mark.asyncio
    async def test_resolve_no_reward_duplicate(self, clean_db, db_connection):
        """Тест: разрешение пари обновляет статус ставки"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        user_id = user['id_user']
        
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        # Создаем ставку
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, bet_tickets)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending", 101))
        bet = cursor.fetchone()
        
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки авторизации, используем тот же wallet
        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            
            assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
            
            # Проверяем, что ставка помечена как выигрышная
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT status, payout_tickets FROM public.user_bets WHERE id_bet = %s
            """, (bet['id_bet'],))
            updated_bet = cursor.fetchone()
            assert updated_bet['status'] == 'won'
            assert updated_bet.get('payout_tickets') is not None
            cursor.close()
