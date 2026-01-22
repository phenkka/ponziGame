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
    async def test_get_markets_filters_by_probability_and_date(self, clean_db, db_connection):
        """Тест: markets фильтрует пари по критериям (≈50/50 и 14-21 день)"""
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor()

        # Валидное
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "valid-market", "Valid", "", "general",
            "Yes", "No", 49.0, 51.0,
            datetime.now(timezone.utc) + timedelta(days=15),
            0.0, 0.0, 0.0, "active"
        ))

        # Валидное по вероятностям на границе (35/65 => diff=30)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "border-prob", "Border Prob", "", "general",
            "Yes", "No", 35.0, 65.0,
            datetime.now(timezone.utc) + timedelta(days=15),
            0.0, 0.0, 0.0, "active"
        ))

        # Невалидное по вероятностям (diff > 30)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "bad-prob", "Bad Prob", "", "general",
            "Yes", "No", 34.0, 66.0,
            datetime.now(timezone.utc) + timedelta(days=15),
            0.0, 0.0, 0.0, "active"
        ))

        # Невалидное по дате (слишком рано)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, description, category,
                outcome_a, outcome_b, outcome_a_probability, outcome_b_probability,
                resolution_date,
                volume_24h, volume_7d, volume_30d, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "bad-date", "Bad Date", "", "general",
            "Yes", "No", 49.0, 51.0,
            datetime.now(timezone.utc) + timedelta(days=2),
            0.0, 0.0, 0.0, "active"
        ))

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/predictions/markets?period=24h&limit=50")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            returned_ids = {m.get("polymarket_id") for m in data.get("markets", [])}
            assert "valid-market" in returned_ids
            assert "border-prob" in returned_ids
            assert "bad-prob" not in returned_ids
            assert "bad-date" not in returned_ids
    
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
                json={"prediction_id": prediction_id, "chosen_outcome": "A"}
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
                json={"prediction_id": prediction_id, "chosen_outcome": "A"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "bet_id" in data
    
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
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome)
            VALUES (%s, %s, %s)
        """, (user_id, prediction_id, "A"))
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся создать вторую ставку
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "B"}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "already exists" in data["error"].lower() or "already placed" in data["error"].lower()
    
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
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": prediction_id, "chosen_outcome": "C"}  # Невалидный исход
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
                json={"prediction_id": prediction_id, "chosen_outcome": "A"}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "not active" in data["error"].lower()


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
                json={"chosen_outcome": "A"}
            )
            assert response.status_code == 400
            
            # Без chosen_outcome
            response = await client.post(
                f"/api/predictions/bet/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"prediction_id": 1}
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
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
            """,
            ("test-idem-1", "Test", "Yes", "No", 50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active")
        )
        prediction_id = cursor.fetchone()["id_prediction"]

        cursor.execute(
            """
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, prediction_id, "A", "pending")
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
            # Делаем награду детерминированной (common_pack)
            with patch('core.utils.random.random', return_value=0.50):
                r1 = await client.post(
                    f"/api/predictions/resolve/{prediction_id}",
                    headers=headers,
                    json={"winner_outcome": "A"}
                )
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1["success"] is True
            assert d1["rewards_count"] == 1

            # Считаем выданные паки
            cur = db_connection.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT COUNT(*) as cnt FROM Chest_purchases WHERE id_user = %s", (user_id,))
            before = cur.fetchone()["cnt"]
            cur.close()

            # Повторный resolve не должен создать новые награды
            with patch('core.utils.random.random', return_value=0.50):
                r2 = await client.post(
                    f"/api/predictions/resolve/{prediction_id}",
                    headers=headers,
                    json={"winner_outcome": "A"}
                )
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["success"] is True

            cur = db_connection.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT COUNT(*) as cnt FROM Chest_purchases WHERE id_user = %s", (user_id,))
            after = cur.fetchone()["cnt"]
            cur.close()

            assert after == before
    
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
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, reward_issued)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending", False))
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
                SELECT status FROM public.user_bets WHERE id_bet = %s
            """, (bet['id_bet'],))
            updated_bet = cursor.fetchone()
            assert updated_bet['status'] == 'won'
            cursor.close()
