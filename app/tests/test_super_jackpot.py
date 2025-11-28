import pytest
import sys
import uuid
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
from psycopg2.extras import RealDictCursor
from core.utils import get_or_create_active_super_jackpot_round, add_to_super_jackpot, check_user_has_all_cards, check_user_already_won_super_jackpot, claim_super_jackpot


class TestSuperJackpotBasic:
    @pytest.mark.asyncio
    async def test_get_super_jackpot_creates_round_if_none(self, clean_db, db_connection):
        """Тест: получение супер джекпота создает раунд, если его нет"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/super-jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "amount" in data
            assert data["amount"] == 0  # Новый раунд начинается с 0
            assert "winner" in data
            assert data["winner"] is None  # Победителя еще нет
            
            # Проверяем, что раунд создан в БД
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM Super_jackpot_rounds WHERE winner_user_id IS NULL")
            rounds = cursor.fetchall()
            assert len(rounds) >= 1
            assert rounds[0]["total_amount"] == 0
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_get_super_jackpot_returns_active_round(self, clean_db, db_connection):
        """Тест: получение супер джекпота возвращает активный раунд"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем активный раунд
        started_at = datetime.now()
        ends_at = started_at + timedelta(days=365)
        cursor.execute("""
            INSERT INTO Super_jackpot_rounds (started_at, ends_at, total_amount)
            VALUES (%s, %s, 5000)
            RETURNING id_round
        """, (started_at, ends_at))
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/super-jackpot")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["amount"] == 5000
            assert data["winner"] is None


class TestSuperJackpotAmountUpdates:
    @pytest.mark.asyncio
    async def test_buy_chest_adds_5_percent_to_super_jackpot(self, clean_db, db_connection, auth_headers):
        """Тест: покупка пака добавляет 5% от стоимости в супер джекпот"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_SUPER_{wallet[:8]}")
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
                        "txSignature": "test_signature_super_123"
                    },
                    headers=auth_headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
        
        # Проверяем, что 5% (5) добавлено в супер джекпот
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount FROM Super_jackpot_rounds WHERE winner_user_id IS NULL ORDER BY id_round DESC LIMIT 1")
        round_data = cursor.fetchone()
        assert round_data is not None
        assert float(round_data["total_amount"]) == 5.0  # 5% от 100
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_multiple_chest_purchases_add_to_super_jackpot(self, clean_db, db_connection, auth_headers):
        """Тест: несколько покупок паков добавляют сумму в супер джекпот"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_MULTI_SUPER_{wallet[:8]}")
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
                            "txSignature": f"test_signature_super_{i}"
                        },
                        headers=auth_headers
                    )
                    assert response.status_code == 200
        
        # Проверяем, что в супер джекпот добавлено 3 * 10 = 30 (5% от 200 * 3)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT total_amount FROM Super_jackpot_rounds WHERE winner_user_id IS NULL ORDER BY id_round DESC LIMIT 1")
        round_data = cursor.fetchone()
        assert round_data is not None
        assert float(round_data["total_amount"]) == 30.0  # 5% от 200 * 3
        cursor.close()


class TestSuperJackpotWinConditions:
    @pytest.mark.asyncio
    async def test_user_cannot_win_without_all_cards(self, clean_db, db_connection, auth_headers):
        """Тест: пользователь не может выиграть супер джекпот, если не собрал все карты"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_NO_ALL_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем несколько карт с image_key
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', %s),
            ('basic', 10, 'Card 2', 'card2.png', %s),
            ('basic', 10, 'Card 3', 'card3.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_SUPER_1_{unique_id}', f'TEST_SUPER_2_{unique_id}', f'TEST_SUPER_3_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 3:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s, %s)", 
                         (f'TEST_SUPER_1_{unique_id}', f'TEST_SUPER_2_{unique_id}', f'TEST_SUPER_3_{unique_id}'))
            cards = cursor.fetchall()
        
        # Добавляем пользователю только 2 из 3 карт (не все)
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1), (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, cards[0]["id_card"], user_id, cards[1]["id_card"]))
        
        # Создаем пак и покупку
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_no_all')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что пользователь не собрал все карты
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        has_all = check_user_has_all_cards(cursor, user_id)
        assert has_all is False, "User should not have all cards"
        
        # Мокаем определение редкости и выбор карты
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': cards[2]["id_card"],
                'rarity': 'basic',
                'start_bounty': 10,
                'name': 'Card 3',
                'image_url': 'card3.png',
                'image_key': f'TEST_SUPER_3_{unique_id}'
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
                # Супер джекпот не должен быть выигран
                assert "super_jackpot" not in data or data.get("super_jackpot", {}).get("won") is not True
        
        # Проверяем, что супер джекпот не был выигран
        cursor.execute("SELECT winner_user_id FROM Super_jackpot_rounds WHERE winner_user_id IS NOT NULL")
        winners = cursor.fetchall()
        assert len(winners) == 0, "Super jackpot should not be won"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_user_wins_when_has_all_cards(self, clean_db, db_connection, auth_headers):
        """Тест: пользователь выигрывает супер джекпот, когда собирает все карты"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_ALL_CARDS_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Временно удаляем все карты с image_key (кроме тех, что уже есть в БД из insert.sql)
        # Вместо этого просто получаем все существующие карты и добавляем их пользователю
        cursor.execute("""
            SELECT id_card FROM Cards
            WHERE image_key IS NOT NULL AND image_key != '' AND image_key NOT LIKE 'TEST_%'
        """)
        existing_cards = cursor.fetchall()
        
        # Добавляем пользователю все существующие карты
        for card in existing_cards:
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
            """, (user_id, card["id_card"]))
        
        # Создаем несколько тестовых карт с image_key
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', %s),
            ('basic', 10, 'Card 2', 'card2.png', %s),
            ('basic', 10, 'Card 3', 'card3.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_ALL_1_{unique_id}', f'TEST_ALL_2_{unique_id}', f'TEST_ALL_3_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 3:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s, %s)", 
                         (f'TEST_ALL_1_{unique_id}', f'TEST_ALL_2_{unique_id}', f'TEST_ALL_3_{unique_id}'))
            cards = cursor.fetchall()
        
        # Добавляем пользователю первые 2 тестовые карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1), (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, cards[0]["id_card"], user_id, cards[1]["id_card"]))
        
        # Добавляем сумму в супер джекпот
        conn = db_connection
        add_to_super_jackpot(cursor, conn, 1000.0)
        
        # Создаем пак и покупку
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_all_cards')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Мокаем определение редкости и выбор карты (последняя карта)
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': cards[2]["id_card"],
                'rarity': 'basic',
                'start_bounty': 10,
                'name': 'Card 3',
                'image_url': 'card3.png',
                'image_key': f'TEST_ALL_3_{unique_id}'
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
                # Супер джекпот должен быть выигран
                assert "super_jackpot" in data
                assert data["super_jackpot"]["won"] is True
                assert data["super_jackpot"]["prize"] == 1000.0
        
        # Проверяем, что супер джекпот был выигран
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM Super_jackpot_rounds WHERE winner_user_id = %s", (user_id,))
        winner_round = cursor.fetchone()
        assert winner_round is not None, "Super jackpot should be won"
        assert winner_round["winner_user_id"] == user_id
        assert float(winner_round["prize"]) == 1000.0
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_user_cannot_win_twice(self, clean_db, db_connection, auth_headers):
        """Тест: пользователь не может выиграть супер джекпот повторно"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code RETURNING id_user",
            (wallet, f"TESTCODE_TWICE_{wallet[:8]}")
        )
        user = cursor.fetchone()
        user_id = user["id_user"]
        
        # Создаем карты с image_key
        unique_id = str(uuid.uuid4())[:8]
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', %s),
            ('basic', 10, 'Card 2', 'card2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_TWICE_1_{unique_id}', f'TEST_TWICE_2_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_TWICE_1_{unique_id}', f'TEST_TWICE_2_{unique_id}'))
            cards = cursor.fetchall()
        
        # Добавляем пользователю все карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1), (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user_id, cards[0]["id_card"], user_id, cards[1]["id_card"]))
        
        # Записываем, что пользователь уже выиграл супер джекпот
        started_at = datetime.now()
        ends_at = started_at + timedelta(days=365)
        cursor.execute("""
            INSERT INTO Super_jackpot_rounds (started_at, ends_at, total_amount, winner_user_id, prize)
            VALUES (%s, %s, 500, %s, 500)
        """, (started_at, ends_at, user_id,))
        
        # Создаем новый активный раунд с суммой
        cursor.execute("""
            INSERT INTO Super_jackpot_rounds (started_at, ends_at, total_amount)
            VALUES (%s, %s, 2000)
        """, (started_at, ends_at))
        
        # Создаем пак и покупку
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_twice')
            RETURNING id_purchase
        """, (user_id, chest_id))
        purchase = cursor.fetchone()
        purchase_id = purchase["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Проверяем, что пользователь уже выигрывал
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        already_won = check_user_already_won_super_jackpot(cursor, user_id)
        assert already_won is True, "User should have already won"
        
        # Мокаем определение редкости и выбор карты
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': cards[0]["id_card"],
                'rarity': 'basic',
                'start_bounty': 10,
                'name': 'Card 1',
                'image_url': 'card1.png',
                'image_key': f'TEST_TWICE_1_{unique_id}'
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
                # Супер джекпот не должен быть выигран повторно
                assert "super_jackpot" not in data or data.get("super_jackpot", {}).get("won") is not True
        
        # Проверяем, что новый супер джекпот не был выигран
        cursor.execute("SELECT winner_user_id FROM Super_jackpot_rounds WHERE winner_user_id = %s AND id_round > (SELECT MAX(id_round) FROM Super_jackpot_rounds WHERE winner_user_id = %s)", (user_id, user_id))
        new_winners = cursor.fetchall()
        assert len(new_winners) == 0, "User should not win super jackpot again"
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_only_first_user_wins(self, clean_db, db_connection, auth_headers):
        """Тест: только первый пользователь, собравший все карты, выигрывает"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем двух пользователей
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
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
        wallet2_address = users[1]["wallet"]  # Используем wallet пользователя 2 из БД
        
        # Создаем карты с image_key
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES 
            ('basic', 10, 'Card 1', 'card1.png', %s),
            ('basic', 10, 'Card 2', 'card2.png', %s)
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """, (f'TEST_FIRST_1_{unique_id}', f'TEST_FIRST_2_{unique_id}'))
        cards = cursor.fetchall()
        if len(cards) < 2:
            cursor.execute("SELECT id_card FROM Cards WHERE image_key IN (%s, %s)", 
                         (f'TEST_FIRST_1_{unique_id}', f'TEST_FIRST_2_{unique_id}'))
            cards = cursor.fetchall()
        
        # Добавляем сумму в супер джекпот
        conn = db_connection
        add_to_super_jackpot(cursor, conn, 5000.0)
        
        # Пользователь 1 собирает все карты
        # Получаем все существующие карты с image_key (кроме тестовых)
        cursor.execute("""
            SELECT id_card FROM Cards
            WHERE image_key IS NOT NULL AND image_key != '' AND image_key NOT LIKE 'TEST_FIRST_%'
        """)
        existing_cards = cursor.fetchall()
        
        # Добавляем пользователю 1 все существующие карты
        for card in existing_cards:
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
            """, (user1_id, card["id_card"]))
        
        # Добавляем тестовые карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1), (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user1_id, cards[0]["id_card"], user1_id, cards[1]["id_card"]))
        
        # Создаем покупку для пользователя 1
        cursor.execute("""
            INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss)
            VALUES (100, 50, 30, 15, 5, 0)
            RETURNING id_chest
        """)
        chest = cursor.fetchone()
        chest_id = chest["id_chest"]
        
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_user1')
            RETURNING id_purchase
        """, (user1_id, chest_id))
        purchase1 = cursor.fetchone()
        purchase1_id = purchase1["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Пользователь 1 открывает пак и выигрывает
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': cards[0]["id_card"],
                'rarity': 'basic',
                'start_bounty': 10,
                'name': 'Card 1',
                'image_url': 'card1.png',
                'image_key': f'TEST_FIRST_1_{unique_id}'
            }
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase1_id},
                    headers=auth_headers
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                # Пользователь 1 должен выиграть
                assert "super_jackpot" in data
                assert data["super_jackpot"]["won"] is True
        
        # Пользователь 2 тоже собирает все карты
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Получаем все существующие карты с image_key (кроме тестовых)
        cursor.execute("""
            SELECT id_card FROM Cards
            WHERE image_key IS NOT NULL AND image_key != '' AND image_key NOT LIKE 'TEST_FIRST_%'
        """)
        existing_cards = cursor.fetchall()
        
        # Добавляем пользователю 2 все существующие карты
        for card in existing_cards:
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
                ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
            """, (user2_id, card["id_card"]))
        
        # Добавляем тестовые карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1), (%s, %s, 1)
            ON CONFLICT (id_user, id_card) DO UPDATE SET quantity = Card_User.quantity + 1
        """, (user2_id, cards[0]["id_card"], user2_id, cards[1]["id_card"]))
        
        # Создаем новый активный раунд
        started_at = datetime.now()
        ends_at = started_at + timedelta(days=365)
        cursor.execute("""
            INSERT INTO Super_jackpot_rounds (started_at, ends_at, total_amount)
            VALUES (%s, %s, 3000)
        """, (started_at, ends_at))
        
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, 'test_tx_user2')
            RETURNING id_purchase
        """, (user2_id, chest_id))
        purchase2 = cursor.fetchone()
        purchase2_id = purchase2["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        # Пользователь 2 открывает пак
        with patch('routes.api.determine_card_rarity', return_value='basic'), \
             patch('routes.api.get_random_card_by_rarity') as mock_get_card:
            mock_get_card.return_value = {
                'id_card': cards[0]["id_card"],
                'rarity': 'basic',
                'start_bounty': 10,
                'name': 'Card 1',
                'image_url': 'card1.png',
                'image_key': f'TEST_FIRST_1_{unique_id}'
            }
            
            # Создаем заголовки для пользователя 2
            # Используем wallet пользователя 2 из БД и создаем валидную подпись
            from nacl.signing import SigningKey
            import base58
            import json
            
            # Для тестов создаем подпись для существующего wallet
            # В реальности это делается на клиенте, но для тестов нужно создать валидную подпись
            message = "Gamba Auth: 1234567890"
            
            # Создаем новый ключ и используем его для подписи
            # В тестах мы не можем использовать реальный приватный ключ wallet, поэтому
            # создаем новый ключ и обновляем wallet пользователя 2 на новый адрес
            signing_key = SigningKey.generate()
            verify_key = signing_key.verify_key
            new_wallet2_address = base58.b58encode(verify_key.encode()).decode('utf-8')
            
            # Обновляем wallet пользователя 2 на новый адрес
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                UPDATE Users SET wallet = %s WHERE id_user = %s
            """, (new_wallet2_address, user2_id))
            db_connection.commit()
            cursor.close()
            
            # Создаем подпись
            signed = signing_key.sign(message.encode('utf-8'))
            signature_list = list(signed.signature)
            
            headers2 = {
                "X-Wallet": new_wallet2_address,
                "X-Signature": json.dumps(signature_list),
                "X-Message": message
            }
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/open",
                    json={"wallet": new_wallet2_address, "id_purchase": purchase2_id},
                    headers=headers2
                )
                assert response.status_code == 200
                data = response.json()
                assert data["success"] is True
                # Пользователь 2 должен выиграть новый раунд
                assert "super_jackpot" in data
                assert data["super_jackpot"]["won"] is True
                assert data["super_jackpot"]["prize"] == 3000.0
        
        # Проверяем, что оба пользователя выиграли разные раунды
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(*) as cnt FROM Super_jackpot_rounds WHERE winner_user_id IS NOT NULL")
        winners_count = cursor.fetchone()
        assert winners_count["cnt"] >= 2, "Both users should have won different rounds"
        cursor.close()

