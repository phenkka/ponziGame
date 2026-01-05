import pytest
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
import json
import base58
from nacl.signing import SigningKey
from collections import Counter
from psycopg2.extras import RealDictCursor


class TestChestOpeningAPI:
    @pytest.mark.asyncio
    async def test_open_chest_requires_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": "test_wallet", "id_purchase": 1}
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_open_chest_not_found(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        # Создаем пользователя для авторизации
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO UPDATE SET ref_code = EXCLUDED.ref_code",
            (wallet, f"TESTCODE_NOTFOUND_{wallet[:8]}")
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": 99999},
                headers=auth_headers
            )
            assert response.status_code == 404
            data = response.json()
            assert data["success"] is False
            assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    @patch('routes.api.get_random_card_by_rarity')
    @patch('routes.api.determine_card_rarity')
    async def test_open_chest_double_open_does_not_duplicate_rewards(self, mock_determine_rarity, mock_get_card, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_DOUBLEOPEN_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]

        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (100, 0, 0, 0, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute(
            "INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url) VALUES (%s, %s, %s, %s, 'img/cards/test.png') RETURNING id_card",
            ("basic", 10, "Test Basic Card", f"TEST_DOUBLEOPEN_{wallet[:8]}")
        )
        card_id = cursor.fetchone()["id_card"]

        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, f"test_tx_signature_doubleopen_{wallet[:8]}"))
        purchase_id = cursor.fetchone()["id_purchase"]
        db_connection.commit()
        cursor.close()

        mock_determine_rarity.return_value = "basic"
        mock_get_card.return_value = {"id_card": card_id, "start_bounty": 10, "name": "Test Basic Card", "image_url": "img/cards/test.png"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp1 = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert resp1.status_code == 200

            resp2 = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert resp2.status_code == 400
            data2 = resp2.json()
            assert data2["success"] is False
            assert "already opened" in data2["error"].lower()

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(*) AS cnt FROM Chest_openings WHERE id_purchase = %s", (purchase_id,))
        openings_cnt = cursor.fetchone()["cnt"]
        assert openings_cnt == 1

        cursor.execute(
            "SELECT quantity FROM Card_User WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        cu = cursor.fetchone()
        assert cu is not None
        assert cu["quantity"] == 1
        cursor.close()

    @pytest.mark.asyncio
    @patch('routes.api.get_random_card_by_rarity')
    @patch('routes.api.determine_card_rarity')
    async def test_open_chest_concurrent_requests_only_one_succeeds(self, mock_determine_rarity, mock_get_card, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        wallet = auth_headers["X-Wallet"]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_CONCURRENT_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]

        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (100, 0, 0, 0, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute(
            "INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url) VALUES (%s, %s, %s, %s, 'img/cards/test.png') RETURNING id_card",
            ("basic", 10, "Test Basic Card", f"TEST_CONCURRENT_{wallet[:8]}")
        )
        card_id = cursor.fetchone()["id_card"]

        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, f"test_tx_signature_concurrent_{wallet[:8]}"))
        purchase_id = cursor.fetchone()["id_purchase"]
        db_connection.commit()
        cursor.close()

        mock_determine_rarity.return_value = "basic"
        mock_get_card.return_value = {"id_card": card_id, "start_bounty": 10, "name": "Test Basic Card", "image_url": "img/cards/test.png"}

        async with AsyncClient(app=app, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase_id},
                    headers=auth_headers
                ),
                client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase_id},
                    headers=auth_headers
                )
            )

            codes = sorted([r1.status_code, r2.status_code])
            assert codes == [200, 400]

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT COUNT(*) AS cnt FROM Chest_openings WHERE id_purchase = %s", (purchase_id,))
        openings_cnt = cursor.fetchone()["cnt"]
        assert openings_cnt == 1

        cursor.execute(
            "SELECT quantity FROM Card_User WHERE id_user = %s AND id_card = %s",
            (user_id, card_id)
        )
        cu = cursor.fetchone()
        assert cu is not None
        assert cu["quantity"] == 1
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_open_chest_already_opened(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "TESTCODE_ALREADY_OPENED")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем покупку и отмечаем как открытую
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature, is_opened, opened_at)
            VALUES (%s, %s, %s, TRUE, NOW())
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_1"))
        purchase_id = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "already opened" in data["error"].lower()
    
    @pytest.mark.asyncio
    async def test_open_chest_wrong_user(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем первого пользователя (владельца пака)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_WRONG_USER_1_{wallet[:8]}")
        )
        owner_id = cursor.fetchone()["id_user"]
        
        # Создаем второго пользователя (который пытается открыть)
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        other_wallet = base58.b58encode(verify_key.encode()).decode('utf-8')
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (other_wallet, f"TESTCODE_WRONG_USER_2_{other_wallet[:8]}")
        )
        other_user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем покупку для первого пользователя
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (owner_id, chest_id, "test_tx_signature_2"))
        purchase_id = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        # Второй пользователь пытается открыть пак первого
        # Создаем заголовки для второго пользователя
        signing_key_other = SigningKey.generate()
        verify_key_other = signing_key_other.verify_key
        wallet_bytes_other = verify_key_other.encode()
        other_wallet_actual = base58.b58encode(wallet_bytes_other).decode('utf-8')
        
        # Обновляем пользователя с правильным wallet
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "UPDATE Users SET wallet = %s WHERE id_user = %s",
            (other_wallet_actual, other_user_id)
        )
        db_connection.commit()
        cursor.close()
        
        # Создаем заголовки для второго пользователя
        TEST_MESSAGE = "Gamba Auth: 1234567890"
        signed_other = signing_key_other.sign(TEST_MESSAGE.encode('utf-8'))
        signature_list_other = list(signed_other.signature)
        other_headers = {
            "X-Wallet": other_wallet_actual,
            "X-Signature": json.dumps(signature_list_other),
            "X-Message": TEST_MESSAGE
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": other_wallet_actual, "id_purchase": purchase_id},
                headers=other_headers
            )
            assert response.status_code == 404
            data = response.json()
            assert data["success"] is False
            assert "not found" in data["error"].lower() or "access denied" in data["error"].lower()
    
    @pytest.mark.asyncio
    async def test_open_chest_success_with_card(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_SUCCESS_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак (100% basic для теста)
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (100, 0, 0, 0, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем покупку
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_3"))
        purchase_id = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["lost"] is False
            assert data["rarity"] == "basic"
            assert "card_id" in data
            received_card_id = data["card_id"]
        
        # Проверяем, что карта добавлена пользователю (может быть любая карта с редкостью basic)
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT quantity FROM Card_User
            WHERE id_user = %s AND id_card = %s
        """, (user_id, received_card_id))
        result = cursor.fetchone()
        assert result is not None
        assert result["quantity"] == 1
        
        # Проверяем, что полученная карта имеет редкость basic
        cursor.execute("""
            SELECT rarity FROM Cards WHERE id_card = %s
        """, (received_card_id,))
        card_data = cursor.fetchone()
        assert card_data is not None
        assert card_data["rarity"] == "basic"
        
        # Проверяем, что пак отмечен как открытый
        cursor.execute("""
            SELECT is_opened, opened_at FROM Chest_purchases
            WHERE id_purchase = %s
        """, (purchase_id,))
        purchase = cursor.fetchone()
        assert purchase["is_opened"] is True
        assert purchase["opened_at"] is not None
        
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_open_chest_with_loss(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_LOSS_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак с 100% шансом потери
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (0, 0, 0, 0, 100, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем покупку
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_4"))
        purchase_id = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["lost"] is True
            assert data["rarity"] is None
        
        # Проверяем, что пак отмечен как открытый
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT is_opened FROM Chest_purchases
            WHERE id_purchase = %s
        """, (purchase_id,))
        purchase = cursor.fetchone()
        assert purchase["is_opened"] is True
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_open_chest_card_quantity_increase(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_QUANTITY_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак (100% basic)
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (100, 0, 0, 0, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Открываем первый пак и получаем карту
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_5_1"))
        purchase_id_1 = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        # Открываем первый пак
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id_1},
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["rarity"] == "basic"
            first_card_id = data["card_id"]
        
        # Проверяем, что карта добавлена с quantity = 1
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT quantity FROM Card_User
            WHERE id_user = %s AND id_card = %s
        """, (user_id, first_card_id))
        result = cursor.fetchone()
        assert result is not None
        assert result["quantity"] == 1
        
        # Открываем второй пак
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_5_2"))
        purchase_id_2 = cursor.fetchone()["id_purchase"]
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id_2},
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["rarity"] == "basic"
            second_card_id = data["card_id"]
        
        # Проверяем quantity для второй карты
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT quantity FROM Card_User
            WHERE id_user = %s AND id_card = %s
        """, (user_id, second_card_id))
        result = cursor.fetchone()
        assert result is not None
        
        if second_card_id == first_card_id:
            # Если выпала та же карта, quantity должен быть 2
            assert result["quantity"] == 2, f"Expected quantity 2 for duplicate card {second_card_id}, got {result['quantity']}"
        else:
            # Если выпала другая карта, quantity должен быть 1
            assert result["quantity"] == 1, f"Expected quantity 1 for new card {second_card_id}, got {result['quantity']}"
        
        # Проверяем, что первая карта все еще имеет quantity = 1 (если это не та же карта)
        if second_card_id != first_card_id:
            cursor.execute("""
                SELECT quantity FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, first_card_id))
            result = cursor.fetchone()
            assert result["quantity"] == 1
        
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_open_chest_rarity_distribution(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_DIST_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак с равными вероятностями (25% каждая)
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (25, 25, 25, 25, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем карты всех редкостей
        rarities = ['basic', 'rare', 'epic', 'legendary']
        for rarity in rarities:
            card_name = f'Test {rarity} Card'
            cursor.execute("""
                INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
                VALUES (%s, 10, %s, %s, 'img/cards/test.png')
                ON CONFLICT (image_key) DO NOTHING
            """, (rarity, card_name, f'TEST_DIST_{rarity.upper()}_1'))
        
        db_connection.commit()
        cursor.close()
        
        # Открываем много паков и собираем статистику
        rarities_found = []
        for i in range(100):
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
                VALUES (%s, %s, %s)
                RETURNING id_purchase
            """, (user_id, chest_id, f"test_tx_signature_dist_{i}"))
            purchase_id = cursor.fetchone()["id_purchase"]
            db_connection.commit()
            cursor.close()
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase_id},
                    headers=auth_headers
                )
                if response.status_code == 200:
                    data = response.json()
                    if not data.get("lost"):
                        rarities_found.append(data["rarity"])
        
        # Проверяем, что все редкости встречаются (с некоторой погрешностью)
        rarity_counts = Counter(rarities_found)
        print(f"Rarity distribution: {rarity_counts}")
        
        # Каждая редкость должна встречаться хотя бы раз (с вероятностью 25% из 100 попыток)
        for rarity in rarities:
            assert rarity in rarity_counts, f"Rarity {rarity} not found in distribution"
    
    @pytest.mark.asyncio
    async def test_open_chest_security_no_manipulation(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_SECURITY_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак (только legendary)
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (0, 0, 0, 100, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем несколько legendary карт
        legendary_cards = []
        for i in range(5):
            cursor.execute("""
                INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
                VALUES ('legendary', 100, 'Legendary Card %s', %s, 'img/cards/legendary%s.png')
                ON CONFLICT (image_key) DO NOTHING
                RETURNING id_card
            """, (i, f'TEST_SECURITY_LEG_{i}', i))
            result = cursor.fetchone()
            if result is None:
                cursor.execute("SELECT id_card FROM Cards WHERE image_key = %s", (f'TEST_SECURITY_LEG_{i}',))
                result = cursor.fetchone()
            legendary_cards.append(result["id_card"])
        
        # Открываем несколько паков и проверяем, что карты выбираются случайно
        received_cards = []
        for i in range(20):
            cursor.execute("""
                INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
                VALUES (%s, %s, %s)
                RETURNING id_purchase
            """, (user_id, chest_id, f"test_tx_security_{i}"))
            purchase_id = cursor.fetchone()["id_purchase"]
            db_connection.commit()
            
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/chests/open",
                    json={"wallet": wallet, "id_purchase": purchase_id},
                    headers=auth_headers
                )
                if response.status_code == 200:
                    data = response.json()
                    if not data.get("lost"):
                        received_cards.append(data["card_id"])
            
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Проверяем, что получены разные карты (не все одинаковые)
        unique_cards = set(received_cards)
        assert len(unique_cards) > 1, "All cards are the same - randomness might be compromised"
        
        cursor.close()
    
    @pytest.mark.asyncio
    async def test_open_chest_creates_opening_record(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")
        
        wallet = auth_headers["X-Wallet"]
        
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        
        # Создаем пользователя
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, f"TESTCODE_OPENING_{wallet[:8]}")
        )
        user_id = cursor.fetchone()["id_user"]
        
        # Создаем пак
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (100, 0, 0, 0, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()["id_chest"]
        
        # Создаем карту
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
            VALUES ('basic', 10, 'Test Card', 'TEST_OPENING_RECORD_1', 'img/cards/test1.png')
            ON CONFLICT (image_key) DO NOTHING
        """)
        
        # Создаем покупку
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
            RETURNING id_purchase
        """, (user_id, chest_id, "test_tx_signature_6"))
        purchase_id = cursor.fetchone()["id_purchase"]
        
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/open",
                json={"wallet": wallet, "id_purchase": purchase_id},
                headers=auth_headers
            )
            assert response.status_code == 200
        
        # Проверяем, что запись в Chest_openings создана
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT * FROM Chest_openings
            WHERE id_purchase = %s
        """, (purchase_id,))
        opening = cursor.fetchone()
        assert opening is not None
        assert opening["id_user"] == user_id
        assert opening["id_chest"] == chest_id
        cursor.close()

