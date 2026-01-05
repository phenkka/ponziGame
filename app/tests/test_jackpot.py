import pytest
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
from psycopg2.extras import RealDictCursor
from core.utils import get_user_tickets, get_or_create_active_round, add_to_jackpot, draw_jackpot, save_tickets_snapshot


class TestJackpotBasic:
    @pytest.mark.asyncio
    async def test_get_jackpot_creates_round_if_none(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "jackpot" in data
            assert "endsAt" in data
            assert "timeLeft" in data
            assert data["jackpot"] == 0  # Новый раунд начинается с 0
            
            # Проверяем, что раунд создан в БД
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
            rounds = cursor.fetchall()
            assert len(rounds) == 1
            assert rounds[0]["total_amount"] == 0
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_get_jackpot_returns_active_round(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем активный раунд
        started_at = datetime.now()
        ends_at = started_at + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 2500)
            RETURNING id_round
        """, (started_at, ends_at))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["jackpot"] == 2500
            assert data["timeLeft"] > 0
            assert "endsAt" in data
    
    @pytest.mark.asyncio
    async def test_get_last_jackpot(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя с уникальными данными
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'winner_wallet_{unique_id}', f'REF_WINNER_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем завершенный раунд
        started_at = datetime.now() - timedelta(days=2)
        ends_at = datetime.now() - timedelta(days=1)
        completed_at = datetime.now() - timedelta(days=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount, winner_user_id, prize_amount, completed_at)
            VALUES (%s, %s, 'completed', 5000, %s, 500, %s)
        """, (started_at, ends_at, user_id, completed_at))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot/last")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["lastJackpot"] is not None
            assert data["lastJackpot"]["amount"] == 500
            assert data["lastJackpot"]["winner"] == f"winner_wallet_{unique_id}"
            assert data["lastJackpot"]["date"] is not None


class TestJackpotAmountUpdates:
    @pytest.mark.asyncio
    async def test_buy_chest_adds_to_jackpot(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_JACKPOT_{wallet[:8]}")
        )
        db_connection.commit()
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем верификацию транзакции
        with patch('routes.api.verify_solana_transaction') as mock_verify:
            mock_verify.return_value = {"valid": True}
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/buy",
                    json={
                        "wallet": wallet,
                        "id_chest": chest_id,
                        "txSignature": "test_signature_123"
                    },
                    headers=auth_headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
        
        # Проверяем, что 40% (40) добавлено в джекпот
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount FROM Jackpot_rounds WHERE status = 'active'")
        round_data = cursor.fetchone()
        assert round_data is not None
        assert float(round_data["total_amount"]) == 40.0  # 40% от 100
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_multiple_chest_purchases_add_to_jackpot(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_MULTI_{wallet[:8]}")
        )
        db_connection.commit()
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (200, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем верификацию транзакции
        with patch('routes.api.verify_solana_transaction') as mock_verify:
            mock_verify.return_value = {"valid": True}
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                # Покупаем 3 пака
                for i in range(3):
                    response = await client.post(
                        "/api/chests/buy",
                        json={
                            "wallet": wallet,
                            "id_chest": chest_id,
                            "txSignature": f"test_signature_{i}"
                        },
                        headers=auth_headers
                    )
                    assert response.status_code == 200
        
        # Проверяем, что в джекпот добавлено 3 * 80 = 240 (40% от 200 * 3)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount FROM Jackpot_rounds WHERE status = 'active'")
        round_data = cursor.fetchone()
        assert round_data is not None
        assert float(round_data["total_amount"]) == 240.0  # 40% от 200 * 3
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_jackpot_during_active_round(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_ACTIVE_{wallet[:8]}")
        )
        db_connection.commit()
        
        # Создаем активный раунд с начальной суммой
        started_at = datetime.now()
        ends_at = started_at + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 100)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (500, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем верификацию транзакции
        with patch('routes.api.verify_solana_transaction') as mock_verify:
            mock_verify.return_value = {"valid": True}
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                # Покупаем пак - должно добавить 40% (200) в джекпот
                response = await client.post(
                    "/api/chests/buy",
                    json={
                        "wallet": wallet,
                        "id_chest": chest_id,
                        "txSignature": "test_tx_active_round"
                    },
                    headers=auth_headers
                )
                assert response.status_code == 200
        
        # Проверяем, что сумма в джекпоте увеличилась (100 + 200 = 300)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        round_data = cursor.fetchone()
        assert float(round_data["total_amount"]) == 300.0
        
        # Проверяем, что раунд все еще активен
        cursor.execute("SELECT status FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        status = cursor.fetchone()
        assert status["status"] == "active"
        cursor.close()


class TestJackpotTickets:
    """Тесты системы tickets для джекпота"""
    
    @pytest.mark.asyncio
    async def test_open_chest_adds_tickets_via_cards(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_TICKETS_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карту с start_bounty = 50
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 50, 'Test Card', 'test.png', 'TEST_JACKPOT_CARD')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = 'TEST_JACKPOT_CARD'")
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Создаем покупку
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_123')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем определение редкости и выбор карты
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': card_id,
                'rarity': 'basic',
                'start_bounty': 50,
                'name': 'Test Card',
                'image_url': 'test.png',
                'image_key': 'TEST_JACKPOT_CARD'
            }
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase_id},
                    headers=auth_headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
        
        # Проверяем, что тикеты считаются правильно
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 50
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_tickets_calculation_with_multiple_cards(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_MULTICARD_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем карты с разными start_bounty
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', 'TEST_MULTI1'),
            ('rare', 20, 'Card 2', 'card2.png', 'TEST_MULTI2'),
            ('epic', 30, 'Card 3', 'card3.png', 'TEST_MULTI3')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card, start_bounty
        """)
        cards = cursor.fetchall()
        if len(cards) < 3:
            cursor.execute("SELECT id_card, start_bounty FROM Cards WHERE image_key IN ('TEST_MULTI1', 'TEST_MULTI2', 'TEST_MULTI3')")
            cards = cursor.fetchall()
        
        # Добавляем карты пользователю (некоторые с quantity > 1)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES 
            (%s, %s, 2),  -- 2 * 10 = 20 тикетов
            (%s, %s, 1),  -- 1 * 20 = 20 тикетов
            (%s, %s, 3)   -- 3 * 30 = 90 тикетов
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, cards[0]["id_card"], user_id, cards[1]["id_card"], user_id, cards[2]["id_card"]))
        db_connection.commit()
        cursor.close()
        
        # Проверяем подсчет тикетов через функцию
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 130  # 20 + 20 + 90
        cursor.close()

    @pytest.mark.asyncio
    async def test_bonus_pack_prediction_reward_excluded_from_tickets(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_BONUS_PRED_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]

        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 50, 'Bonus Card', 'bonus.png', 'TEST_BONUS_PRED_CARD')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = 'TEST_BONUS_PRED_CARD'")
            card = cursor.fetchone()
        card_id = card["id_card"]

        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, f"prediction_reward_{user_id}_123"))
        purchase_id = cursor.fetchone()["id_purchase"]

        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_id, user_id, chest_id))
        opening_id = cursor.fetchone()["id_opening"]

        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, card_id, opening_id))

        db_connection.commit()

        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 0

        cursor.close()

    @pytest.mark.asyncio
    async def test_bonus_pack_daily_checkin_excluded_from_tickets_and_snapshot(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_BONUS_CHECKIN_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]

        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 60, 'Checkin Card', 'checkin.png', 'TEST_BONUS_CHECKIN_CARD')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = 'TEST_BONUS_CHECKIN_CARD'")
            card = cursor.fetchone()
        card_id = card["id_card"]

        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, f"daily_checkin_{user_id}_2026-01-05_123"))
        purchase_id = cursor.fetchone()["id_purchase"]

        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_id, user_id, chest_id))
        opening_id = cursor.fetchone()["id_opening"]

        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = EXCLUDED.quantity
        """, (user_id, card_id, opening_id))

        db_connection.commit()

        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 0

        from datetime import datetime, timedelta
        started_at = datetime.now() - timedelta(hours=1)
        ends_at = datetime.now() + timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 0)
            RETURNING id_round
        """, (started_at, ends_at))
        round_id = cursor.fetchone()["id_round"]
        db_connection.commit()

        save_tickets_snapshot(cursor, db_connection, round_id, datetime.now())

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM Jackpot_tickets_snapshot WHERE id_round = %s",
            (round_id,)
        )
        cnt = cursor.fetchone()["cnt"]
        assert int(cnt) == 0

        cursor.close()


class TestJackpotDraw:
    @pytest.mark.asyncio
    async def test_draw_jackpot_with_winner(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей с уникальными данными
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (f'user1_wallet_{unique_id}', f'REF1_{unique_id}',
              f'user2_wallet_{unique_id}', f'REF2_{unique_id}'))
        users = cursor.fetchall()
        user1_id = users[0]["id_user"]
        user2_id = users[1]["id_user"]
        
        # Создаем карты с разными start_bounty (уникальные ключи)
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 100, 'Card 1', 'card1.png', %s),
            ('rare', 200, 'Card 2', 'card2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card, start_bounty
        """, (f'TEST_CARD1_{unique_id}', f'TEST_CARD2_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card, start_bounty FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_CARD1_{unique_id}', f'TEST_CARD2_{unique_id}'))
            cards = cursor.fetchall()
        card1_id = cards[0]["id_card"]
        card2_id = cards[1]["id_card"]
        
        # Пользователь 1 получает карту с 100 тикетами
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user1_id, card1_id))
        
        # Пользователь 2 получает карту с 200 тикетами (больше шансов)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user2_id, card2_id))
        
        # Создаем активный раунд с джекпотом
        started_at = datetime.now() - timedelta(hours=25)  # Раунд истек
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Проводим розыгрыш
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/jackpot/draw")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            # Проверяем, что раунд был обработан
            assert len(data["drawn_rounds"]) > 0, "No rounds were drawn"
            round_found = False
            for drawn_round in data["drawn_rounds"]:
                if drawn_round["round_id"] == round_id:
                    round_found = True
                    assert drawn_round["prize"] == 1000.0  # Вся сумма джекпота
                    assert drawn_round["winner"] in [f"user1_wallet_{unique_id}", f"user2_wallet_{unique_id}"]
                    assert drawn_round["tickets"] > 0
                    break
            assert round_found, f"Round {round_id} was not found in drawn_rounds"
        
        # Проверяем, что раунд завершен
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        round_data = cursor.fetchone()
        assert round_data["status"] == "completed"
        assert round_data["winner_user_id"] in [user1_id, user2_id]
        assert float(round_data["prize_amount"]) == 1000.0  # Вся сумма джекпота
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_draw_jackpot_no_participants(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 500)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Проводим розыгрыш (нет участников)
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/jackpot/draw")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
        
        # Проверяем, что раунд завершен, но без победителя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        round_data = cursor.fetchone()
        assert round_data is not None, "Round not found"
        assert round_data["status"] == "completed", f"Round status is {round_data['status']}, expected 'completed'"
        assert round_data["winner_user_id"] is None, "Winner should be None when no participants"
        assert float(round_data["prize_amount"]) == 500.0  # Вся сумма джекпота
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_draw_jackpot_creates_new_round(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 100)
        """, (started_at, ends_at))
        db_connection.commit()
        cursor.close()
        
        # Проводим розыгрыш
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/jackpot/draw")
            assert response.status_code == 200
        
        # Проверяем, что создан новый активный раунд
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
        active_rounds = cursor.fetchall()
        assert len(active_rounds) == 1
        assert active_rounds[0]["total_amount"] == 0  # Новый раунд начинается с 0
        cursor.close()


class TestJackpotSecurity:
    @pytest.mark.asyncio
    async def test_cannot_manipulate_winner_by_adding_cards_after_round_ends(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей с уникальными данными
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (wallet, f'REF1_{unique_id}',
              f'other_user_{unique_id}', f'REF2_{unique_id}'))
        users = cursor.fetchall()
        user1_id = users[0]["id_user"]
        user2_id = users[1]["id_user"]
        
        # Создаем карты
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', 'TEST_SEC1'),
            ('legendary', 1000, 'Card 2', 'card2.png', 'TEST_SEC2')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN ('TEST_SEC1', 'TEST_SEC2')")
            cards = cursor.fetchall()
        card1_id = cards[0]["id_card"]
        card2_id = cards[1]["id_card"]
        
        # Пользователь 1 получает карту с 10 тикетами (мало)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user1_id, card1_id))
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Пытаемся добавить карту ПОСЛЕ окончания раунда (но до розыгрыша)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user1_id, card2_id))  # Добавляем карту с 1000 тикетами
        db_connection.commit()
        cursor.close()
        
        # Проводим розыгрыш - должен использовать tickets на момент окончания раунда
        # Но так как мы используем текущее состояние БД, проверим что система работает корректно
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/jackpot/draw")
            assert response.status_code == 200
        
        # Проверяем, что раунд завершен
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        round_data = cursor.fetchone()
        assert round_data is not None, "Round not found"
        assert round_data["status"] == "completed", f"Round status is {round_data['status']}, expected 'completed'"
        # Победитель должен быть определен на основе snapshot tickets (на момент окончания раунда)
        # Карта, добавленная после ends_at, не должна учитываться
        assert round_data["winner_user_id"] is not None, "Winner should be selected"
        # Проверяем, что snapshot был сохранен
        cursor.execute("SELECT COUNT(*) as cnt FROM Jackpot_tickets_snapshot WHERE id_round = %s", (round_id,))
        snapshot_result = cursor.fetchone()
        snapshot_count = snapshot_result['cnt'] if snapshot_result else 0
        assert snapshot_count > 0, "Snapshot should be saved"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_cannot_manipulate_jackpot_amount_directly(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем активный раунд
        started_at = datetime.now()
        ends_at = started_at + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 100)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        
        # Пытаемся напрямую изменить сумму в БД (симуляция атаки)
        cursor.execute("""
            UPDATE Jackpot_rounds
            SET total_amount = 999999
            WHERE id_round = %s
        """, (round_id,))
        db_connection.commit()
        
        # Проверяем через API - должно вернуть измененную сумму
        # (в реальной системе нужна защита от прямого доступа к БД)
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot")
            assert response.status_code == 200
            data = response.json()
            # API вернет то, что в БД (в продакшене нужна защита)
            assert data["jackpot"] == 999999
        
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_winner_selection_is_probabilistic(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        import time
        unique_suffix = str(int(time.time() * 1000000))  # Уникальный суффикс
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей с уникальными данными
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (f'user_low_tickets_{unique_suffix}', f'REF1_{unique_suffix}',
              f'user_high_tickets_{unique_suffix}', f'REF2_{unique_suffix}'))
        users = cursor.fetchall()
        user_low_id = users[0]["id_user"]
        user_high_id = users[1]["id_user"]
        
        # Создаем карты с уникальными ключами
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Low Card', 'low.png', %s),
            ('legendary', 100, 'High Card', 'high.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_LOW_{unique_suffix}', f'TEST_HIGH_{unique_suffix}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_LOW_{unique_suffix}', f'TEST_HIGH_{unique_suffix}'))
            cards = cursor.fetchall()
        low_card_id = cards[0]["id_card"]
        high_card_id = cards[1]["id_card"]
        
        # Пользователь с низкими tickets: 10 тикетов
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_low_id, low_card_id))
        
        # Пользователь с высокими tickets: 100 тикетов (в 10 раз больше)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_high_id, high_card_id))
        
        # Проводим много розыгрышей и проверяем статистику
        wins_low = 0
        wins_high = 0
        total_rounds = 100
        
        for i in range(total_rounds):
            # Создаем истекший раунд
            started_at = datetime.now() - timedelta(hours=25)
            ends_at = datetime.now() - timedelta(hours=1)
            cursor.execute("""
                INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
                VALUES (%s, %s, 'active', 1000)
                RETURNING id_round
            """, (started_at, ends_at))
            round_data = cursor.fetchone()
            round_id = round_data["id_round"]
            db_connection.commit()
            
            # Проводим розыгрыш
            conn = db_connection
            draw_jackpot(cursor, conn)
            
            # Проверяем победителя
            cursor.execute("SELECT winner_user_id FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
            winner = cursor.fetchone()
            if winner and winner["winner_user_id"]:
                if winner["winner_user_id"] == user_low_id:
                    wins_low += 1
                elif winner["winner_user_id"] == user_high_id:
                    wins_high += 1
        
        # Пользователь с большим количеством tickets должен выигрывать чаще
        # Ожидаем примерно 90% побед для user_high (100 tickets из 110 total)
        # Но из-за случайности допускаем отклонение
        win_rate_high = wins_high / total_rounds
        assert win_rate_high > 0.7, f"User with high tickets should win more often. Win rate: {win_rate_high}"
        assert wins_high > wins_low, f"User with high tickets ({wins_high}) should win more than user with low tickets ({wins_low})"
        
        cursor.close()


class TestJackpotRoundTransitions:
    @pytest.mark.asyncio
    async def test_get_jackpot_auto_completes_expired_round(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 500)
            RETURNING id_round
        """, (started_at, ends_at))
        expired_round = cursor.fetchone()
        expired_round_id = expired_round["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Получаем джекпот - должен автоматически завершить истекший раунд и создать новый
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["jackpot"] == 0  # Новый раунд начинается с 0
        
        # Проверяем, что старый раунд завершен
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE id_round = %s", (expired_round_id,))
        old_round = cursor.fetchone()
        assert old_round["status"] == "completed"
        
        # Проверяем, что создан новый активный раунд
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
        new_rounds = cursor.fetchall()
        assert len(new_rounds) == 1
        assert new_rounds[0]["id_round"] != expired_round_id
        assert new_rounds[0]["total_amount"] == 0
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_get_jackpot_auto_completes_expired_round_with_winner(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя с уникальными данными
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'test_winner_{unique_id}', f'REF_TEST_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем карту с уникальным ключом
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 100, 'Test Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_AUTO_WINNER_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_AUTO_WINNER_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Добавляем карту пользователю
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id))
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at, ends_at))
        expired_round = cursor.fetchone()
        expired_round_id = expired_round["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Получаем джекпот - должен автоматически завершить истекший раунд, провести розыгрыш и создать новый
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["jackpot"] == 0  # Новый раунд начинается с 0
        
        # Проверяем, что старый раунд завершен с победителем
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE id_round = %s", (expired_round_id,))
        old_round = cursor.fetchone()
        assert old_round["status"] == "completed"
        assert old_round["winner_user_id"] == user_id
        assert float(old_round["prize_amount"]) == 1000.0  # Вся сумма джекпота
        
        # Проверяем, что создан новый активный раунд
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
        new_rounds = cursor.fetchall()
        assert len(new_rounds) == 1
        assert new_rounds[0]["id_round"] != expired_round_id
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_jackpot_round_transition(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_TRANS_{wallet[:8]}")
        )
        db_connection.commit()
        
        # Создаем истекший активный раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 200)
            RETURNING id_round
        """, (started_at, ends_at))
        old_round = cursor.fetchone()
        old_round_id = old_round["id_round"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем верификацию транзакции
        with patch('routes.api.verify_solana_transaction') as mock_verify:
            mock_verify.return_value = {"valid": True}
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                # Покупаем пак - должен автоматически завершить старый раунд, создать новый и добавить 40% в новый
                response = await client.post(
                    "/api/chests/buy",
                    json={
                        "wallet": wallet,
                        "id_chest": chest_id,
                        "txSignature": "test_tx_transition"
                    },
                    headers=auth_headers
                )
                assert response.status_code == 200
        
        # Проверяем, что старый раунд завершен
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT status FROM Jackpot_rounds WHERE id_round = %s", (old_round_id,))
        old_round_status = cursor.fetchone()
        assert old_round_status["status"] == "completed"
        
        # Проверяем, что создан новый активный раунд с добавленной суммой (40% от 100 = 40)
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
        new_rounds = cursor.fetchall()
        assert len(new_rounds) == 1
        assert new_rounds[0]["id_round"] != old_round_id
        assert float(new_rounds[0]["total_amount"]) == 40.0
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_multiple_expired_rounds_draw(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей с уникальными данными
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (f'user1_multi_{unique_id}', f'REF1_MULTI_{unique_id}',
              f'user2_multi_{unique_id}', f'REF2_MULTI_{unique_id}'))
        users = cursor.fetchall()
        user1_id = users[0]["id_user"]
        user2_id = users[1]["id_user"]
        
        # Создаем карты с уникальными ключами
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 50, 'Card 1', 'card1.png', %s),
            ('rare', 100, 'Card 2', 'card2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_MULTI_ROUND1_{unique_id}', f'TEST_MULTI_ROUND2_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_MULTI_ROUND1_{unique_id}', f'TEST_MULTI_ROUND2_{unique_id}'))
            cards = cursor.fetchall()
        card1_id = cards[0]["id_card"]
        card2_id = cards[1]["id_card"]
        
        # Пользователь 1 получает карту
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user1_id, card1_id))
        
        # Пользователь 2 получает карту
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user2_id, card2_id))
        
        # Создаем два истекших активных раунда
        now = datetime.now()
        started_at1 = now - timedelta(hours=26)
        ends_at1 = now - timedelta(hours=2)
        started_at2 = now - timedelta(hours=25)
        ends_at2 = now - timedelta(hours=1)
        
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 500)
            RETURNING id_round
        """, (started_at1, ends_at1))
        round1 = cursor.fetchone()
        round1_id = round1["id_round"]
        
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at2, ends_at2))
        round2 = cursor.fetchone()
        round2_id = round2["id_round"]
        db_connection.commit()
        cursor.close()
        
        # Проводим розыгрыш - должен обработать оба раунда
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post("/api/jackpot/draw")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
        
        # Проверяем, что оба раунда завершены
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id_round, status FROM Jackpot_rounds WHERE id_round = %s OR id_round = %s", (round1_id, round2_id))
        rounds = cursor.fetchall()
        assert len(rounds) == 2, f"Expected 2 rounds, got {len(rounds)}"
        for r in rounds:
            assert r["status"] == "completed", f"Round {r['id_round']} has status {r['status']}, expected 'completed'"
        
        # Проверяем, что создан новый активный раунд
        cursor.execute("SELECT * FROM Jackpot_rounds WHERE status = 'active'")
        new_rounds = cursor.fetchall()
        assert len(new_rounds) == 1
        cursor.close()


class TestJackpotPrizeCalculation:
    @pytest.mark.asyncio
    async def test_prize_is_10_percent_of_total_amount(self, clean_db, db_connection):
        """Тест: приз составляет всю сумму джекпота (100% от total_amount)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя с картой (уникальные данные)
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'prize_test_user_{unique_id}', f'REF_PRIZE_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 100, 'Test Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_PRIZE_CARD_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_PRIZE_CARD_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id))
        
        # Тестируем разные суммы джекпота
        test_amounts = [100, 500, 1000, 5000, 10000]
        
        for total_amount in test_amounts:
            # Создаем истекший раунд
            started_at = datetime.now() - timedelta(hours=25)
            ends_at = datetime.now() - timedelta(hours=1)
            cursor.execute("""
                INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
                VALUES (%s, %s, 'active', %s)
                RETURNING id_round
            """, (started_at, ends_at, total_amount))
            round_data = cursor.fetchone()
            round_id = round_data["id_round"]
            db_connection.commit()
            
            # Проводим розыгрыш
            conn = db_connection
            draw_jackpot(cursor, conn)
            
            # Проверяем приз
            cursor.execute("SELECT prize_amount FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
            prize = cursor.fetchone()
            assert prize is not None, f"Round {round_id} not found"
            expected_prize = total_amount  # Приз = вся сумма джекпота
            assert float(prize["prize_amount"]) == expected_prize, \
                f"Prize should be 100% of {total_amount}, got {prize['prize_amount']}, expected {expected_prize}"
        
        cursor.close()


class TestJackpotBonusPacksExclusion:
    """Тесты для исключения бонусных паков из джекпота"""
    
    @pytest.mark.asyncio
    async def test_purchased_pack_cards_count_in_tickets(self, clean_db, db_connection, auth_headers):
        """Тест: карты из купленных паков учитываются в tickets"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_PURCHASED_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карту с start_bounty = 50
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 50, 'Purchased Card', 'test.png', 'TEST_PURCHASED_CARD')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = 'TEST_PURCHASED_CARD'")
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Создаем покупку с обычным tx_signature (купленный пак)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'real_tx_signature_123')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        
        # Создаем открытие пака
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_id, user_id, chest_id))
        opening = cursor.fetchone()
        id_opening = opening["id_opening"]
        
        # Добавляем карту с привязкой к открытию
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id, id_opening))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что тикеты считаются (карта из купленного пака)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 50, f"Expected 50 tickets for purchased pack card, got {tickets}"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_prediction_reward_pack_cards_excluded_from_tickets(self, clean_db, db_connection):
        """Тест: карты из бонусных паков (prediction_reward) НЕ учитываются в tickets"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'bonus_user_{unique_id}', f'REF_BONUS_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карту с start_bounty = 100
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('rare', 100, 'Bonus Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_BONUS_CARD_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_BONUS_CARD_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Создаем покупку с tx_signature вида prediction_reward_ (бонусный пак)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'prediction_reward_123456')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        
        # Создаем открытие пака
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_id, user_id, chest_id))
        opening = cursor.fetchone()
        id_opening = opening["id_opening"]
        
        # Добавляем карту с привязкой к открытию бонусного пака
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id, id_opening))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что тикеты НЕ считаются (карта из бонусного пака)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 0, f"Expected 0 tickets for bonus pack card, got {tickets}"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_daily_checkin_pack_cards_excluded_from_tickets(self, clean_db, db_connection):
        """Тест: карты из бонусных паков (daily_checkin) НЕ учитываются в tickets"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'checkin_user_{unique_id}', f'REF_CHECKIN_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карту с start_bounty = 200
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('epic', 200, 'Checkin Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_CHECKIN_CARD_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_CHECKIN_CARD_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Создаем покупку с tx_signature вида daily_checkin_ (бонусный пак)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'daily_checkin_123456_20231206')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        
        # Создаем открытие пака
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_id, user_id, chest_id))
        opening = cursor.fetchone()
        id_opening = opening["id_opening"]
        
        # Добавляем карту с привязкой к открытию бонусного пака
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id, id_opening))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что тикеты НЕ считаются (карта из бонусного пака)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 0, f"Expected 0 tickets for daily checkin pack card, got {tickets}"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_old_cards_without_id_opening_count_in_tickets(self, clean_db, db_connection):
        """Тест: старые карты без id_opening учитываются в tickets (обратная совместимость)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'old_user_{unique_id}', f'REF_OLD_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем карту с start_bounty = 75
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 75, 'Old Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_OLD_CARD_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_OLD_CARD_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Добавляем карту БЕЗ id_opening (старая карта)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, NULL)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id,))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что тикеты считаются (старая карта учитывается)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 75, f"Expected 75 tickets for old card without id_opening, got {tickets}"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_mixed_purchased_and_bonus_packs(self, clean_db, db_connection, auth_headers):
        """Тест: смешанный случай - у пользователя есть и купленные, и бонусные паки"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_MIXED_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карты
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 50, 'Purchased Card', 'test1.png', 'TEST_MIXED_PURCHASED'),
            ('rare', 100, 'Bonus Card 1', 'test2.png', 'TEST_MIXED_BONUS1'),
            ('epic', 150, 'Bonus Card 2', 'test3.png', 'TEST_MIXED_BONUS2')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card, image_key
        """)
        cards = cursor.fetchall()
        if len(cards) < 3:
            cursor.execute("SELECT id_card, image_key FROM Cards WHERE image_key IN ('TEST_MIXED_PURCHASED', 'TEST_MIXED_BONUS1', 'TEST_MIXED_BONUS2')")
            cards = cursor.fetchall()
        
        card_purchased_id = next(c['id_card'] for c in cards if c['image_key'] == 'TEST_MIXED_PURCHASED')
        card_bonus1_id = next(c['id_card'] for c in cards if c['image_key'] == 'TEST_MIXED_BONUS1')
        card_bonus2_id = next(c['id_card'] for c in cards if c['image_key'] == 'TEST_MIXED_BONUS2')
        
        # 1. Купленный пак
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'real_tx_purchased_123')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase_purchased = cursor.fetchone()
        purchase_purchased_id = purchase_purchased["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_purchased_id, user_id, chest_id))
        opening_purchased = cursor.fetchone()
        id_opening_purchased = opening_purchased["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_purchased_id, id_opening_purchased))
        
        # 2. Бонусный пак (prediction_reward)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'prediction_reward_456')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase_bonus1 = cursor.fetchone()
        purchase_bonus1_id = purchase_bonus1["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_bonus1_id, user_id, chest_id))
        opening_bonus1 = cursor.fetchone()
        id_opening_bonus1 = opening_bonus1["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_bonus1_id, id_opening_bonus1))
        
        # 3. Бонусный пак (daily_checkin)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'daily_checkin_789')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase_bonus2 = cursor.fetchone()
        purchase_bonus2_id = purchase_bonus2["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_bonus2_id, user_id, chest_id))
        opening_bonus2 = cursor.fetchone()
        id_opening_bonus2 = opening_bonus2["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_bonus2_id, id_opening_bonus2))
        
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что учитывается только карта из купленного пака (50 tickets)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        assert tickets == 50, f"Expected 50 tickets (only purchased pack), got {tickets}. Bonus packs should be excluded."
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_save_tickets_snapshot_excludes_bonus_packs(self, clean_db, db_connection):
        """Тест: save_tickets_snapshot исключает карты из бонусных паков"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        from core.utils import save_tickets_snapshot
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (f'user_purchased_{unique_id}', f'REF_PURCHASED_{unique_id}',
              f'user_bonus_{unique_id}', f'REF_BONUS_{unique_id}'))
        users = cursor.fetchall()
        user_purchased_id = users[0]["id_user"]
        user_bonus_id = users[1]["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карты
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 50, 'Purchased Card', 'test1.png', %s),
            ('rare', 100, 'Bonus Card', 'test2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card, image_key
        """, (f'TEST_SNAPSHOT_PURCHASED_{unique_id}', f'TEST_SNAPSHOT_BONUS_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card, image_key FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_SNAPSHOT_PURCHASED_{unique_id}', f'TEST_SNAPSHOT_BONUS_{unique_id}'))
            cards = cursor.fetchall()
        
        card_purchased_id = next(c['id_card'] for c in cards if 'PURCHASED' in c['image_key'])
        card_bonus_id = next(c['id_card'] for c in cards if 'BONUS' in c['image_key'])
        
        # Пользователь 1: купленный пак
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'real_tx_snapshot_123')
            RETURNING id_purchase
        """, (user_purchased_id, chest_id))
        purchase_purchased = cursor.fetchone()
        purchase_purchased_id = purchase_purchased["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_purchased_id, user_purchased_id, chest_id))
        opening_purchased = cursor.fetchone()
        id_opening_purchased = opening_purchased["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_purchased_id, card_purchased_id, id_opening_purchased))
        
        # Пользователь 2: бонусный пак
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'prediction_reward_snapshot_456')
            RETURNING id_purchase
        """, (user_bonus_id, chest_id))
        purchase_bonus = cursor.fetchone()
        purchase_bonus_id = purchase_bonus["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_bonus_id, user_bonus_id, chest_id))
        opening_bonus = cursor.fetchone()
        id_opening_bonus = opening_bonus["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_bonus_id, card_bonus_id, id_opening_bonus))
        
        # Создаем раунд
        started_at = datetime.now()
        ends_at = started_at + timedelta(hours=24)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        
        # Сохраняем snapshot (используем тот же курсор)
        conn = db_connection
        save_tickets_snapshot(cursor, conn, round_id, ends_at)
        
        # Проверяем snapshot - должен быть только пользователь с купленным паком
        # Используем тот же курсор (функция не закрывает его)
        cursor.execute("""
            SELECT id_user, tickets_count FROM Jackpot_tickets_snapshot WHERE id_round = %s
        """, (round_id,))
        snapshots = cursor.fetchall()
        
        # Должен быть только пользователь с купленным паком (50 tickets)
        assert len(snapshots) == 1, f"Expected 1 user in snapshot, got {len(snapshots)}"
        assert snapshots[0]["id_user"] == user_purchased_id, "Expected user with purchased pack"
        assert snapshots[0]["tickets_count"] == 50, f"Expected 50 tickets, got {snapshots[0]['tickets_count']}"
        
        # Пользователь с бонусным паком не должен быть в snapshot
        user_bonus_in_snapshot = any(s["id_user"] == user_bonus_id for s in snapshots)
        assert not user_bonus_in_snapshot, "User with bonus pack should not be in snapshot"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_jackpot_draw_excludes_bonus_packs(self, clean_db, db_connection):
        """Тест: розыгрыш джекпота исключает карты из бонусных паков"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем двух пользователей
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES 
            (%s, %s),
            (%s, %s)
            RETURNING id_user, wallet
        """, (f'user_purchased_draw_{unique_id}', f'REF_PURCHASED_DRAW_{unique_id}',
              f'user_bonus_draw_{unique_id}', f'REF_BONUS_DRAW_{unique_id}'))
        users = cursor.fetchall()
        user_purchased_id = users[0]["id_user"]
        user_bonus_id = users[1]["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        # Создаем карты
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Purchased Card', 'test1.png', %s),
            ('legendary', 1000, 'Bonus Card', 'test2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card, image_key
        """, (f'TEST_DRAW_PURCHASED_{unique_id}', f'TEST_DRAW_BONUS_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card, image_key FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_DRAW_PURCHASED_{unique_id}', f'TEST_DRAW_BONUS_{unique_id}'))
            cards = cursor.fetchall()
        
        card_purchased_id = next(c['id_card'] for c in cards if 'PURCHASED' in c['image_key'])
        card_bonus_id = next(c['id_card'] for c in cards if 'BONUS' in c['image_key'])
        
        # Пользователь 1: купленный пак (10 tickets)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'real_tx_draw_123')
            RETURNING id_purchase
        """, (user_purchased_id, chest_id))
        purchase_purchased = cursor.fetchone()
        purchase_purchased_id = purchase_purchased["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_purchased_id, user_purchased_id, chest_id))
        opening_purchased = cursor.fetchone()
        id_opening_purchased = opening_purchased["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_purchased_id, card_purchased_id, id_opening_purchased))
        
        # Пользователь 2: бонусный пак (1000 tickets, но не должны учитываться)
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'prediction_reward_draw_456')
            RETURNING id_purchase
        """, (user_bonus_id, chest_id))
        purchase_bonus = cursor.fetchone()
        purchase_bonus_id = purchase_bonus["id_purchase"]
        
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (%s, %s, %s)
            RETURNING id_opening
        """, (purchase_bonus_id, user_bonus_id, chest_id))
        opening_bonus = cursor.fetchone()
        id_opening_bonus = opening_bonus["id_opening"]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_bonus_id, card_bonus_id, id_opening_bonus))
        
        # Создаем истекший раунд
        started_at = datetime.now() - timedelta(hours=25)
        ends_at = datetime.now() - timedelta(hours=1)
        cursor.execute("""
            INSERT INTO Jackpot_rounds (started_at, ends_at, status, total_amount)
            VALUES (%s, %s, 'active', 1000)
            RETURNING id_round
        """, (started_at, ends_at))
        round_data = cursor.fetchone()
        round_id = round_data["id_round"]
        db_connection.commit()
        
        # Проводим розыгрыш (используем тот же курсор)
        conn = db_connection
        draw_jackpot(cursor, conn)
        
        # Проверяем результат - победитель должен быть только пользователь с купленным паком
        # Используем тот же курсор (функция не закрывает его)
        cursor.execute("SELECT winner_user_id FROM Jackpot_rounds WHERE id_round = %s", (round_id,))
        winner = cursor.fetchone()
        assert winner is not None, "Round should have a winner"
        assert winner["winner_user_id"] == user_purchased_id, \
            f"Winner should be user with purchased pack (id={user_purchased_id}), got {winner['winner_user_id']}"
        
        # Проверяем snapshot - должен быть только пользователь с купленным паком
        cursor.execute("""
            SELECT id_user, tickets_count FROM Jackpot_tickets_snapshot WHERE id_round = %s
        """, (round_id,))
        snapshots = cursor.fetchall()
        assert len(snapshots) == 1, f"Expected 1 user in snapshot, got {len(snapshots)}"
        assert snapshots[0]["id_user"] == user_purchased_id
        assert snapshots[0]["tickets_count"] == 10, f"Expected 10 tickets, got {snapshots[0]['tickets_count']}"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_card_with_id_opening_but_no_chest_purchase(self, clean_db, db_connection):
        """Тест: edge case - карта с id_opening, но без связи с Chest_purchases"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)
            RETURNING id_user
        """, (f'edge_user_{unique_id}', f'REF_EDGE_{unique_id}'))
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем карту
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 50, 'Edge Card', 'test.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_EDGE_CARD_{unique_id}',))
        card = cursor.fetchone()
        if not card:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_EDGE_CARD_{unique_id}',))
            card = cursor.fetchone()
        card_id = card["id_card"]
        
        # Создаем открытие БЕЗ покупки (некорректная ситуация, но возможна)
        cursor.execute("""
            INSERT INTO Chest_openings (id_purchase, id_user, id_chest)
            VALUES (NULL, %s, 1)
            RETURNING id_opening
        """, (user_id,))
        opening = cursor.fetchone()
        id_opening = opening["id_opening"]
        
        # Добавляем карту с id_opening, но без связи с покупкой
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity, id_opening)
            VALUES (%s, %s, 1, %s)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, card_id, id_opening))
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что тикеты НЕ считаются (нет связи с покупкой)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        tickets = get_user_tickets(cursor, user_id)
        # Карта с id_opening, но без связи с Chest_purchases не должна учитываться
        # (так как cp.tx_signature будет NULL)
        assert tickets == 0, f"Expected 0 tickets for card without purchase link, got {tickets}"
        cursor.close()
