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


class TestPredictionsRewardDistribution:
    @pytest.mark.asyncio
    async def test_resolve_prediction_issues_rewards(self, clean_db, db_connection, monkeypatch):
        """Тест: при разрешении пари выигравшим пользователям выдаются награды"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE"))
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем пари
        cursor.execute("""
            INSERT INTO public.predictions (polymarket_id, title, outcome_a, outcome_b, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test Prediction", "Yes", "No", "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        # Создаем выигрышную ставку
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending"))
        bet = cursor.fetchone()
        
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки авторизации
        signed = signing_key.sign(b"test message")
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": "test message",
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)
            # Разрешаем пари с исходом A (пользователь выиграл)
            with patch('core.utils.random.random', return_value=0.50):
                response = await client.post(
                    f"/api/predictions/resolve/{prediction_id}",
                    headers=headers,
                    json={"winner_outcome": "A"}
                )
            
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["rewards_issued"] is True
            assert data["rewards_count"] == 1
            
            # Проверяем, что ставка помечена как выигрышная и награда выдана
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT status, reward_issued, reward_type, reward_data FROM public.user_bets WHERE id_bet = %s
            """, (bet['id_bet'],))
            updated_bet = cursor.fetchone()
            assert updated_bet['status'] == 'won'
            assert updated_bet['reward_issued'] is True
            assert updated_bet['reward_type'] is not None
            assert updated_bet['reward_data'] is not None
            
            # Проверяем, что награда действительно выдана (пак или карта)
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
            
            # Должна быть выдана хотя бы одна награда (пак, карта или буст)
            assert (chest_count > 0) or (card_count > 0) or (boost_count > 0), \
                f"Reward not issued: chests={chest_count}, cards={card_count}, boosts={boost_count}"
            
            cursor.close()

            # Проверяем, что эндпоинт пользователя возвращает reward_type/reward_data
            response_bets = await client.get(
                f"/api/predictions/user/{wallet}",
                headers=headers
            )
            assert response_bets.status_code == 200
            bets_data = response_bets.json()
            assert bets_data["success"] is True
            assert len(bets_data.get("bets", [])) > 0
            returned_bet = next((b for b in bets_data["bets"] if b.get("prediction_id") == prediction_id), None)
            assert returned_bet is not None
            assert returned_bet.get("status") == "won"
            assert returned_bet.get("reward_issued") is True
            assert returned_bet.get("reward_type") is not None
            assert returned_bet.get("reward_data") is not None

    @pytest.mark.asyncio
    async def test_claim_reward_idempotent(self, clean_db, db_connection, monkeypatch):
        """Тест: claim награды идемпотентный (повторно забрать нельзя)."""
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        signing_key = SigningKey.generate()
        wallet = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')

        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
        """, (wallet, "TESTCODE_CLAIM"))
        user_id = cursor.fetchone()['id_user']

        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, (
            "test-claim-1", "Test", "Yes", "No",
            50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"
        ))
        prediction_id = cursor.fetchone()['id_prediction']

        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, reward_issued)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "won", True))
        bet_id = cursor.fetchone()['id_bet']

        db_connection.commit()
        cursor.close()

        signed = signing_key.sign(b"test message")
        signature_list = list(signed.signature)
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": "test message",
            "Content-Type": "application/json"
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            # Первый claim
            r1 = await client.post(f"/api/predictions/claim/{bet_id}", headers=headers)
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1["success"] is True
            assert d1["already_claimed"] is False

            # Второй claim
            r2 = await client.post(f"/api/predictions/claim/{bet_id}", headers=headers)
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["success"] is True
            assert d2["already_claimed"] is True

        # Проверяем в БД, что флаг выставлен и время заполнено
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT reward_claimed, reward_claimed_at
            FROM public.user_bets
            WHERE id_bet = %s
        """, (bet_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row['reward_claimed'] is True
        assert row['reward_claimed_at'] is not None
        cursor.close()

    @pytest.mark.asyncio
    async def test_claim_reward_forbidden_for_other_user(self, clean_db, db_connection):
        """Тест: нельзя claim награду по чужой ставке."""
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        sk1 = SigningKey.generate()
        w1 = base58.b58encode(sk1.verify_key.encode()).decode('utf-8')
        sk2 = SigningKey.generate()
        w2 = base58.b58encode(sk2.verify_key.encode()).decode('utf-8')

        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (w1, "C1"))
        u1 = cursor.fetchone()['id_user']
        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (w2, "C2"))
        u2 = cursor.fetchone()['id_user']

        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, (
            "test-claim-2", "Test", "Yes", "No",
            50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"
        ))
        prediction_id = cursor.fetchone()['id_prediction']

        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, reward_issued)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (u1, prediction_id, "A", "won", True))
        bet_id = cursor.fetchone()['id_bet']

        db_connection.commit()
        cursor.close()

        signed = sk2.sign(b"test message")
        headers = {
            "X-Wallet": w2,
            "X-Signature": json.dumps(list(signed.signature)),
            "X-Message": "test message",
            "Content-Type": "application/json"
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            r = await client.post(f"/api/predictions/claim/{bet_id}", headers=headers)
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_claim_reward_not_available_for_loser_or_not_issued(self, clean_db, db_connection, auth_headers):
        """Тест: claim возможен только для won + reward_issued."""
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (wallet, "CLAIMNA"))
        user_id = cursor.fetchone()['id_user']

        # Создаем два разных пари, чтобы не нарушать unique(id_user, id_prediction)
        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, (
            "test-claim-3-lost", "Test", "Yes", "No",
            50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"
        ))
        prediction_lost_id = cursor.fetchone()['id_prediction']

        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, (
            "test-claim-3-not-issued", "Test", "Yes", "No",
            50.0, 50.0, datetime.now(timezone.utc) + timedelta(days=15), "active"
        ))
        prediction_not_issued_id = cursor.fetchone()['id_prediction']

        # Lost
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, reward_issued)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_lost_id, "A", "lost", True))
        lost_bet_id = cursor.fetchone()['id_bet']

        # Won but reward_issued = false
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, reward_issued)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_not_issued_id, "A", "won", False))
        not_issued_bet_id = cursor.fetchone()['id_bet']

        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1 = await client.post(f"/api/predictions/claim/{lost_bet_id}", headers=auth_headers)
            assert r1.status_code == 400

            r2 = await client.post(f"/api/predictions/claim/{not_issued_bet_id}", headers=auth_headers)
            assert r2.status_code == 400
    
    @pytest.mark.asyncio
    async def test_reward_broken_packs(self, clean_db, db_connection):
        """Тест: проверка выдачи 3 Broken паков (40% вероятность)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокаем random.random() чтобы получить 40% вероятность (roll < 40)
        with patch('core.utils.random.random', return_value=0.35):  # 35% попадает в диапазон < 40%
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
            
            cursor.execute("""
                INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
            """, (wallet, "TESTCODE"))
            user = cursor.fetchone()
            user_id = user['id_user']
            
            from core.utils import issue_prediction_reward
            rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, db_connection, user_id)
            
            assert reward_type == "broken_packs"
            assert reward_data["quantity"] == 3
            assert reward_data["id_chest"] == 5
            assert len(rewards_issued) == 1
            assert rewards_issued[0]["type"] == "broken_packs"
            assert rewards_issued[0]["quantity"] == 3
            
            # Проверяем, что в БД добавлены 3 пака
            cursor.execute("""
                SELECT COUNT(*) as count FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = 5
            """, (user_id,))
            result = cursor.fetchone()
            assert result['count'] == 3
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_reward_common_pack(self, clean_db, db_connection):
        """Тест: проверка выдачи 1 Common пака (25% вероятность)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        with patch('core.utils.random.random', return_value=0.50):  # 50% попадает в диапазон 40-65%
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
            
            cursor.execute("""
                INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
            """, (wallet, "TESTCODE"))
            user = cursor.fetchone()
            user_id = user['id_user']
            
            from core.utils import issue_prediction_reward
            rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, db_connection, user_id)
            
            assert reward_type == "common_pack"
            assert reward_data["quantity"] == 1
            assert reward_data["id_chest"] == 1
            assert len(rewards_issued) == 1
            assert rewards_issued[0]["type"] == "common_pack"
            
            cursor.execute("""
                SELECT COUNT(*) as count FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = 1
            """, (user_id,))
            result = cursor.fetchone()
            assert result['count'] == 1
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_reward_legendary_pack(self, clean_db, db_connection):
        """Тест: проверка выдачи 1 Legendary пака (10% вероятность)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        with patch('core.utils.random.random', return_value=0.70):  # 70% попадает в диапазон 65-75%
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
            
            cursor.execute("""
                INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
            """, (wallet, "TESTCODE"))
            user = cursor.fetchone()
            user_id = user['id_user']
            
            from core.utils import issue_prediction_reward
            rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, db_connection, user_id)
            
            assert reward_type == "legendary_pack"
            assert reward_data["quantity"] == 1
            assert reward_data["id_chest"] == 4
            assert len(rewards_issued) == 1
            assert rewards_issued[0]["type"] == "legendary_pack"
            
            cursor.execute("""
                SELECT COUNT(*) as count FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = 4
            """, (user_id,))
            result = cursor.fetchone()
            assert result['count'] == 1
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_reward_boost(self, clean_db, db_connection):
        """Тест: проверка выдачи персонального увеличения шансов (5% вероятность)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        with patch('core.utils.random.random', return_value=0.98):  # 98% попадает в диапазон > 95%
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
            
            cursor.execute("""
                INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user
            """, (wallet, "TESTCODE"))
            user = cursor.fetchone()
            user_id = user['id_user']
            
            from core.utils import issue_prediction_reward
            rewards_issued, reward_type, reward_data = issue_prediction_reward(cursor, db_connection, user_id)
            
            assert reward_type == "boost"
            assert reward_data["boost_type"] == "legendary_chance"
            assert reward_data["boost_value"] == 10.0
            assert "expires_at" in reward_data
            
            assert len(rewards_issued) == 1
            assert rewards_issued[0]["type"] == "boost"
            assert rewards_issued[0]["boost_type"] == "legendary_chance"
            assert rewards_issued[0]["boost_value"] == 10.0
            
            # Проверяем, что boost добавлен в БД
            cursor.execute("""
                SELECT boost_type, boost_value, expires_at FROM User_boost 
                WHERE id_user = %s AND is_active = TRUE
            """, (user_id,))
            boost_result = cursor.fetchone()
            assert boost_result is not None
            assert boost_result['boost_type'] == "legendary_chance"
            assert float(boost_result['boost_value']) == 10.0
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_no_reward_for_losers(self, clean_db, db_connection, monkeypatch):
        """Тест: проигравшие пользователи не получают награды"""
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
            INSERT INTO public.predictions (polymarket_id, title, outcome_a, outcome_b, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-3", "Test", "Yes", "No", "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        # Создаем проигрышную ставку (пользователь выбрал B, но выиграл A)
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
        """, (user_id, prediction_id, "B", "pending"))
        
        db_connection.commit()
        cursor.close()
        
        signed = signing_key.sign(b"test message")
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": "test message",
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)
            # Разрешаем пари с исходом A (пользователь проиграл, выбрал B)
            response = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            # Награды не выдаются проигравшим
            assert data["rewards_count"] == 0
            
            # Проверяем, что ставка помечена как проигрышная
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT status, reward_issued FROM public.user_bets WHERE id_user = %s
            """, (user_id,))
            bet_result = cursor.fetchone()
            assert bet_result['status'] == 'lost'
            assert bet_result['reward_issued'] is False
            
            # Проверяем, что награды не выданы
            cursor.execute("""
                SELECT COUNT(*) as count FROM Chest_purchases WHERE id_user = %s
            """, (user_id,))
            chests = cursor.fetchone()
            assert chests['count'] == 0
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_resolve_prediction_cancelled(self, clean_db, db_connection, monkeypatch):
        """Тест: при отмене пари все ставки помечаются как cancelled и награды не выдаются"""
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
            INSERT INTO public.predictions (polymarket_id, title, outcome_a, outcome_b, status)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, ("test-1", "Test", "Yes", "No", "active"))
        prediction = cursor.fetchone()
        prediction_id = prediction['id_prediction']
        
        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status)
            VALUES (%s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending"))
        bet = cursor.fetchone()
        
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки авторизации, используем тот же wallet
        signed = signing_key.sign(b"test message")
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": "test message",
            "Content-Type": "application/json"
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)
            response = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "cancelled"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            # При отмене награды не выдаются
            assert data["rewards_count"] == 0
            
            # Проверяем, что ставка помечена как отмененная
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT status, reward_issued FROM public.user_bets WHERE id_bet = %s
            """, (bet['id_bet'],))
            updated_bet = cursor.fetchone()
            assert updated_bet['status'] == 'cancelled'
            assert updated_bet['reward_issued'] is False
            
            # Проверяем, что награды не выданы
            cursor.execute("""
                SELECT COUNT(*) as count FROM Chest_purchases WHERE id_user = %s
            """, (user_id,))
            chests = cursor.fetchone()
            assert chests['count'] == 0
            
            cursor.close()
