import pytest
import sys
from pathlib import Path
from datetime import date, timedelta, datetime, timezone, timezone
from unittest.mock import patch, MagicMock

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from psycopg2.extras import RealDictCursor
from main import app
import json
from nacl.signing import SigningKey
import base58


def get_utc_date():
    """Получает текущую дату в UTC (для тестов)"""
    return datetime.now(timezone.utc).date()


def get_utc_date():
    """Получает текущую дату в UTC (для тестов)"""
    return datetime.now(timezone.utc).date()


class TestDailyCheckinStatus:
    @pytest.mark.asyncio
    async def test_get_checkin_status_requires_auth(self, clean_db, db_connection):
        """Тест: получение статуса требует авторизации"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/daily-checkin/status/{wallet_address}")
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_checkin_status_new_user(self, clean_db, db_connection, auth_headers):
        """Тест: статус для нового пользователя (никогда не заходил)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet, "TESTCODE")
        )
        
        # Создаем код на сегодня (UTC)
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY123")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/daily-checkin/status/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["checked_in_today"] is False
            assert data["consecutive_days"] == 0
            assert data["can_claim_reward"] is False
            # Проверяем, что код существует (может быть сгенерирован автоматически или наш)
            assert "today_code" in data
            assert len(data["today_code"]) == 8  # Код должен быть 8 символов
    
    @pytest.mark.asyncio
    async def test_get_checkin_status_already_checked_in(self, clean_db, db_connection, auth_headers):
        """Тест: статус для пользователя, который уже зашел сегодня"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем код на сегодня (UTC)
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY123")
        )
        
        # Создаем чекин на сегодня (используем UTC дату)
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, today, "TODAY123", 1))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/daily-checkin/status/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["checked_in_today"] is True
            assert data["consecutive_days"] == 1


class TestDailyCheckinValidation:
    @pytest.mark.asyncio
    async def test_checkin_with_wrong_code(self, clean_db, db_connection, auth_headers):
        """Тест: чекин с неправильным кодом должен быть отклонен"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем код на сегодня
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "CORRECT1")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "WRONG12"}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Invalid code" in data["error"] or "Invalid daily code" in data["error"]
    
    @pytest.mark.asyncio
    async def test_checkin_with_yesterday_code(self, clean_db, db_connection, auth_headers):
        """Тест: код вчерашнего дня не работает сегодня"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем коды на сегодня и вчера
        today = get_utc_date()
        yesterday = today - timedelta(days=1)
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (yesterday, "YESTER1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся использовать вчерашний код сегодня
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "YESTER1"}  # Вчерашний код
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Invalid code" in data["error"] or "Invalid daily code" in data["error"]
    
    @pytest.mark.asyncio
    async def test_checkin_with_correct_code(self, clean_db, db_connection, auth_headers):
        """Тест: чекин с правильным кодом должен быть успешным"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем код на сегодня
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "CORRECT1")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "CORRECT1"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["consecutive_days"] == 1
            assert data["reward_issued"] is False  # Первый день - нет награды
    
    @pytest.mark.asyncio
    async def test_checkin_twice_same_day(self, clean_db, db_connection, auth_headers):
        """Тест: нельзя зайти дважды в один день"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем код на сегодня
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "CORRECT1")
        )
        
        # Первый чекин
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, today, "CORRECT1", 1))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся зайти второй раз
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "CORRECT1"}
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "already checked in" in data["error"].lower()


class TestDailyCheckinConsecutiveDays:
    @pytest.mark.asyncio
    async def test_consecutive_days_increment(self, clean_db, db_connection, auth_headers):
        """Тест: счетчик последовательных дней увеличивается"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем коды на сегодня и вчера
        today = get_utc_date()
        yesterday = today - timedelta(days=1)
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (yesterday, "YESTER1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        
        # Создаем чекин на вчера
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, yesterday, "YESTER1", 1))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Чекин на сегодня
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "TODAY12"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["consecutive_days"] == 2  # Было 1, стало 2
    
    @pytest.mark.asyncio
    async def test_consecutive_days_reset_after_skip(self, clean_db, db_connection, auth_headers):
        """Тест: счетчик сбрасывается после пропуска дня"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем коды
        today = get_utc_date()
        two_days_ago = today - timedelta(days=2)
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (two_days_ago, "TWOAGO1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        
        # Создаем чекин 2 дня назад (consecutive_days = 2)
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, two_days_ago, "TWOAGO1", 2))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Чекин на сегодня (пропустили вчера)
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "TODAY12"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["consecutive_days"] == 1  # Сбросилось до 1, так как пропустили день
    
    @pytest.mark.asyncio
    async def test_consecutive_days_three_days_reward(self, clean_db, db_connection, auth_headers):
        """Тест: после 3 дней подряд выдается награда"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем коды на 3 дня
        today = get_utc_date()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (two_days_ago, "TWOAGO1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (yesterday, "YESTER1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        
        # Создаем чекины на 2 дня назад и вчера
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, two_days_ago, "TWOAGO1", 1))
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, yesterday, "YESTER1", 2))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Чекин на сегодня (3-й день подряд)
            response = await client.post(
                f"/api/daily-checkin/checkin/{wallet}",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"daily_code": "TODAY12"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["consecutive_days"] == 3
            assert data["reward_issued"] is True  # Должна быть выдана награда
            assert len(data["rewards"]) > 0  # Должна быть хотя бы одна награда


class TestDailyCheckinRewards:
    @pytest.mark.asyncio
    async def test_reward_broken_packs_issued(self, clean_db, db_connection, auth_headers):
        """Тест: проверка выдачи 3 broken паков как награды"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем коды
        today = get_utc_date()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (two_days_ago, "TWOAGO1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (yesterday, "YESTER1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        
        # Создаем чекины
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, two_days_ago, "TWOAGO1", 1))
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, yesterday, "YESTER1", 2))
        
        # Убеждаемся, что есть broken пак (id_chest = 5)
        cursor.execute("SELECT id_chest FROM Chests WHERE id_chest = 5")
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO Chests (id_chest, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
                VALUES (5, 5, 2, 1, 0, 92, 300)
            """)
        
        db_connection.commit()
        cursor.close()
        
        # Мокаем random.random чтобы выпала награда broken_packs (40% вероятность)
        # random.random() вызывается дважды: для выбора типа награды и для выбора редкости карты
        # Для broken_packs нужно значение < 40 (0.3 * 100 = 30)
        with patch('random.random', side_effect=[0.3, 0.5]):  # 30% < 40% = broken_packs, второй вызов не важен
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    f"/api/daily-checkin/checkin/{wallet}",
                    headers={**auth_headers, "Content-Type": "application/json"},
                    json={"daily_code": "TODAY12"}
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["reward_issued"] is True
                
                # Проверяем, что выдан broken pack
                broken_pack_reward = [r for r in data["rewards"] if r.get("type") == "broken_packs"]
                assert len(broken_pack_reward) > 0, "Broken pack reward should be issued"
                assert broken_pack_reward[0]["quantity"] == 3
                
                # Проверяем в БД, что паки добавлены
                # Нужно подождать немного, чтобы транзакция закоммитилась
                import time
                time.sleep(0.2)
                
                cursor = db_connection.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT COUNT(*) as count FROM Chest_purchases
                    WHERE id_user = %s AND id_chest = 5
                    AND tx_signature LIKE 'daily_checkin_%%'
                """, (user_id,))
                result = cursor.fetchone()
                assert result is not None, "No result from query"
                # RealDictCursor возвращает словарь
                count = result.get('count') if isinstance(result, dict) else result[0]
                assert count is not None, f"Count is None, result: {result}"
                assert count == 3, f"Expected 3 broken packs, got {count}"
                cursor.close()
    
    @pytest.mark.asyncio
    async def test_reward_boost_issued(self, clean_db, db_connection, auth_headers):
        """Тест: проверка выдачи boost как награды"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем коды
        today = get_utc_date()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (two_days_ago, "TWOAGO1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (yesterday, "YESTER1")
        )
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        
        # Создаем чекины
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, two_days_ago, "TWOAGO1", 1))
        cursor.execute("""
            INSERT INTO Daily_checkins (id_user, checkin_date, daily_code, consecutive_days)
            VALUES (%s, %s, %s, %s)
        """, (user_id, yesterday, "YESTER1", 2))
        db_connection.commit()
        cursor.close()
        
        # Мокаем random.random чтобы выпала награда boost (5% вероятность, > 95%)
        # random.random() вызывается один раз для boost (не используется для выбора редкости карты)
        with patch('random.random', return_value=0.97):  # 97% > 95% = boost
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    f"/api/daily-checkin/checkin/{wallet}",
                    headers={**auth_headers, "Content-Type": "application/json"},
                    json={"daily_code": "TODAY12"}
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                assert data["reward_issued"] is True
                
                # Проверяем, что выдан boost
                boost_reward = [r for r in data["rewards"] if r.get("type") == "boost"]
                assert len(boost_reward) > 0, "Boost reward should be issued"
                assert boost_reward[0]["boost_type"] == "legendary_chance"
                assert boost_reward[0]["boost_value"] == 10.0
                
                # Проверяем в БД, что boost создан
                cursor = db_connection.cursor(cursor_factory=RealDictCursor)
                cursor.execute("""
                    SELECT * FROM User_boost
                    WHERE id_user = %s AND is_active = TRUE
                """, (user_id,))
                boost = cursor.fetchone()
                assert boost is not None
                assert float(boost['boost_value']) == 10.0
                cursor.close()


class TestDailyCheckinBoost:
    @pytest.mark.asyncio
    async def test_boost_shown_in_status(self, clean_db, db_connection, auth_headers):
        """Тест: активный boost отображается в статусе"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем активный boost
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO User_boost (id_user, boost_type, boost_value, expires_at, is_active)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, "legendary_chance", 10.0, expires_at, True))
        
        # Создаем код на сегодня
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/daily-checkin/status/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["boost"]["active"] is True
            assert data["boost"]["boost_type"] == "legendary_chance"
            assert data["boost"]["boost_value"] == 10.0
    
    @pytest.mark.asyncio
    async def test_expired_boost_not_shown(self, clean_db, db_connection, auth_headers):
        """Тест: истекший boost не отображается"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        user = cursor.fetchone()
        user_id = user['id_user']
        
        # Создаем истекший boost
        expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO User_boost (id_user, boost_type, boost_value, expires_at, is_active)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_id, "legendary_chance", 10.0, expires_at, True))
        
        # Создаем код на сегодня
        today = get_utc_date()
        cursor.execute(
            "INSERT INTO Daily_codes (code_date, daily_code) VALUES (%s, %s) ON CONFLICT (code_date) DO UPDATE SET daily_code = EXCLUDED.daily_code",
            (today, "TODAY12")
        )
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/daily-checkin/status/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["boost"]["active"] is False  # Истекший boost не активен


class TestDailyCheckinCodeGeneration:
    @pytest.mark.asyncio
    async def test_code_generated_if_missing(self, clean_db, db_connection, auth_headers):
        """Тест: код генерируется автоматически, если его нет для сегодня"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE")
        )
        db_connection.commit()
        cursor.close()
        
        # НЕ создаем код на сегодня - он должен сгенерироваться автоматически
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/daily-checkin/status/{wallet}",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "today_code" in data
            assert len(data["today_code"]) == 8  # Код должен быть 8 символов
            
            # Проверяем, что код сохранен в БД
            # API теперь делает commit после генерации кода
            # Нужно переоткрыть соединение, чтобы увидеть изменения из другой транзакции
            import time
            time.sleep(0.2)
            
            # Переоткрываем соединение, чтобы увидеть изменения
            from core.utils import get_db_connection
            test_conn = get_db_connection()
            cursor = test_conn.cursor()
            today = get_utc_date()
            cursor.execute(
                "SELECT daily_code FROM Daily_codes WHERE code_date = %s",
                (today,)
            )
            result = cursor.fetchone()
            cursor.close()
            test_conn.close()
            
            # Код должен быть сохранен в БД после вызова API
            assert result is not None, "Code should be saved in database after API call"
            # result может быть кортежем или словарем в зависимости от cursor_factory
            code_from_db = result[0] if isinstance(result, tuple) else result.get('daily_code') if isinstance(result, dict) else None
            assert code_from_db is not None, f"Could not extract code from result: {result}"
            assert code_from_db == data["today_code"], f"Code mismatch: DB has {code_from_db}, API returned {data['today_code']}"
            cursor.close()
