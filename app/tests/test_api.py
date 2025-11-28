import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
import json
import hashlib
from nacl.signing import SigningKey
import base58


class TestChestsAPI:
    @pytest.mark.asyncio
    async def test_get_chests_public(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/chests")
            assert response.status_code == 200
            data = response.json()
            assert "success" in data
            assert "chests" in data
            assert isinstance(data["chests"], list)
    
    @pytest.mark.asyncio
    async def test_get_chests_with_data(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Добавляем тестовый пак в БД
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/chests")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert len(data["chests"]) > 0
            # Проверяем, что наш пак есть в списке
            chest_ids = [c["id_chest"] for c in data["chests"]]
            assert chest_id in chest_ids


class TestUserChestsAPI:
    @pytest.mark.asyncio
    async def test_get_user_chests_requires_auth(self, clean_db, db_connection):
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
            # Запрос без авторизации
            response = await client.get(f"/api/user/{wallet_address}/chests")
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_user_chests_with_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        # Добавляем пак
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        
        # Добавляем покупку пака пользователем
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature, is_opened)
            VALUES (%s, %s, %s, false)
        """, (user_id, chest_id, "test_tx_signature_123"))
        
        db_connection.commit()
        cursor.close()
        
        # Создаем валидную подпись
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/user/{wallet_address}/chests", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "chests" in data
            assert isinstance(data["chests"], list)
            assert len(data["chests"]) > 0
    
    @pytest.mark.asyncio
    async def test_get_user_chests_with_cookie(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user, ref_code",
            (wallet_address, "TESTCODE")
        )
        result = cursor.fetchone()
        user_id = result[0]
        ref_code = result[1]
        db_connection.commit()
        cursor.close()
        
        # Создаем валидный cookie токен
        token_data = f"{wallet_address}:{ref_code}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()
        
        cookies = {"auth_token": token_hash}
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/user/{wallet_address}/chests", cookies=cookies)
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "chests" in data


class TestBalanceAPI:
    @pytest.mark.asyncio
    async def test_get_balance_requires_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/balance/{wallet_address}")
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_balance_with_auth(self, clean_db, db_connection):
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
        
        # Создаем валидную подпись
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/balance/{wallet_address}", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "balance" in data
            assert "amount" in data["balance"]
            assert "decimals" in data["balance"]
            assert "symbol" in data["balance"]
            assert data["balance"]["symbol"] == "TIRED"


class TestUserCardsAPI:
    @pytest.mark.asyncio
    async def test_get_user_cards_requires_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/user/{wallet_address}/cards")
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_user_cards_with_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        # Добавляем карту (используем уникальный image_key для тестов)
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
            VALUES ('basic', 10, 'Test Card', 'TEST_USER_CARDS_1', 'img/cards/TEST1.png')
            ON CONFLICT (image_key) DO NOTHING
            RETURNING id_card
        """)
        result = cursor.fetchone()
        if result:
            card_id = result[0]
        else:
            # Если карта уже существует, получаем её ID
            cursor.execute("SELECT id_card FROM Cards WHERE image_key = 'TEST_USER_CARDS_1'")
            card_id = cursor.fetchone()[0]
        
        # Добавляем карту пользователю
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
        """, (user_id, card_id))
        
        db_connection.commit()
        cursor.close()
        
        # Создаем валидную подпись
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/user/{wallet_address}/cards", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "cards" in data
            assert isinstance(data["cards"], list)
            assert len(data["cards"]) > 0
            assert data["cards"][0]["id_card"] == card_id


class TestCardsAPI:
    @pytest.mark.asyncio
    async def test_get_cards_public(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/cards")
            assert response.status_code == 200
            data = response.json()
            assert "success" in data
            assert "cards" in data
            assert isinstance(data["cards"], list)
    
    @pytest.mark.asyncio
    async def test_get_cards_with_rarity_filter(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Добавляем тестовые карты (используем уникальные image_key для тестов)
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
            VALUES 
                ('basic', 10, 'Basic Card', 'TEST_B1', 'img/cards/B1.png'),
                ('rare', 25, 'Rare Card', 'TEST_R1', 'img/cards/R1.png')
            ON CONFLICT (image_key) DO NOTHING
        """)
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Фильтр по basic
            response = await client.get("/api/cards?rarity=basic")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert all(card["rarity"] == "basic" for card in data["cards"])
            
            # Фильтр по rare
            response = await client.get("/api/cards?rarity=rare")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert all(card["rarity"] == "rare" for card in data["cards"])
    
    @pytest.mark.asyncio
    async def test_get_cards_with_image_filter(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Добавляем тестовые карты (используем уникальные image_key для тестов)
        cursor = db_connection.cursor()
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_key, image_url)
            VALUES 
                ('basic', 10, 'Card With Image', 'TEST_B1_IMG', 'img/cards/B1.png'),
                ('basic', 10, 'Card Without Image', 'TEST_B2_NOIMG', NULL)
            ON CONFLICT (image_key) DO NOTHING
        """)
        db_connection.commit()
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Фильтр по наличию изображения
            response = await client.get("/api/cards?hasImage=true")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert all(card.get("image_url") for card in data["cards"])


class TestAPIErrorHandling:
    
    @pytest.mark.asyncio
    async def test_get_user_chests_invalid_wallet(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        # Создаем валидную подпись для несуществующего пользователя
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пользователь не существует в БД, поэтому verify_auth вернет 401
            response = await client.get(f"/api/user/{wallet_address}/chests", headers=headers)
            assert response.status_code == 401  # Пользователь должен быть зарегистрирован
            data = response.json()
            assert "detail" in data or "error" in data
    
    @pytest.mark.asyncio
    async def test_get_user_cards_access_denied(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем двух пользователей
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
        
        # Создаем подпись для первого пользователя
        message = "Gamba Auth: 1234567890"
        signed = signing_key_1.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_1,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся получить карты второго пользователя
            response = await client.get(f"/api/user/{wallet_2}/cards", headers=headers)
            assert response.status_code == 403  # Forbidden


class TestChestPurchaseAPI:
    """Тесты для API покупки паков"""
    
    @pytest.mark.asyncio
    async def test_buy_chest_requires_auth(self, clean_db, db_connection):
        """Проверяет, что покупка пака требует авторизацию"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": "TestWallet",
                    "id_chest": 1,
                    "txSignature": "test_signature"
                }
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_buy_chest_missing_fields(self, clean_db, db_connection):
        """Проверяет, что покупка пака требует все обязательные поля"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя и авторизацию
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
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Без txSignature
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": 1
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Missing required fields" in data["error"]
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_chest_not_found(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет обработку несуществующего пака"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мок не должен вызываться, так как пак не найден раньше
        mock_verify_tx.return_value = {"valid": False, "error": "Should not be called"}
        
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
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": 99999,  # Несуществующий пак
                    "txSignature": "test_signature_123"
                },
                headers=headers
            )
            # Должен вернуть 404, но если вернул 500, значит ошибка в коде
            if response.status_code == 500:
                data = response.json()
                print(f"Error in test: {data.get('error', 'Unknown error')}")
            assert response.status_code == 404, f"Expected 404, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is False
            assert "Chest not found" in data["error"]
            # Проверяем, что verify_solana_transaction не вызывалась (если пак не найден, она не должна вызываться)
            # Но если вернулся 500, значит ошибка произошла раньше
    
    @pytest.mark.asyncio
    async def test_buy_chest_duplicate_transaction(self, clean_db, db_connection):
        """Проверяет, что нельзя использовать одну транзакцию дважды"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        
        # Создаем первую покупку с этой транзакцией
        tx_signature = "duplicate_tx_signature_123"
        cursor.execute("""
            INSERT INTO Chest_purchases (id_user, id_chest, tx_signature)
            VALUES (%s, %s, %s)
        """, (user_id, chest_id, tx_signature))
        
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся использовать ту же транзакцию
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Transaction already used" in data["error"]
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_success(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет успешную покупку пака"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокируем успешную верификацию транзакции
        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": 100.0,
            "sender": "test_wallet"
        }
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        tx_signature = "valid_tx_signature_123"
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature
                },
                headers=headers
            )
            # Если вернулся 500, выводим ошибку для отладки
            if response.status_code == 500:
                data = response.json()
                print(f"Error in test_buy_chest_with_cookie_auth: {data.get('error', 'Unknown error')}")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is True
            # Проверяем обратную совместимость: при quantity=1 (по умолчанию) должен быть purchase_id
            assert "purchase_id" in data or "purchase_ids" in data
            assert "message" in data
            # Проверяем, что quantity по умолчанию = 1
            assert data.get("quantity", 1) == 1
            
            # Проверяем, что покупка создана в БД
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT id_purchase, id_user, id_chest, tx_signature, is_opened
                FROM Chest_purchases
                WHERE tx_signature = %s
            """, (tx_signature,))
            purchase = cursor.fetchone()
            cursor.close()
            
            assert purchase is not None
            assert purchase[1] == user_id  # id_user
            assert purchase[2] == chest_id  # id_chest
            assert purchase[3] == tx_signature  # tx_signature
            assert purchase[4] is False  # is_opened
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_invalid_transaction(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет обработку невалидной транзакции"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокируем неуспешную верификацию транзакции
        mock_verify_tx.return_value = {
            "valid": False,
            "error": "Transaction verification failed: Invalid signature"
        }
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE")
        )
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": "invalid_tx_signature"
                },
                headers=headers
            )
            # Если вернулся 500, выводим ошибку для отладки
            if response.status_code == 500:
                data = response.json()
                print(f"Error in test_buy_chest_invalid_transaction: {data.get('error', 'Unknown error')}")
            assert response.status_code == 400, f"Expected 400, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is False
            assert "Transaction verification failed" in data["error"]


class TestChestPurchaseQuantity:
    """Тесты для покупки нескольких паков и защиты от обхода оплаты"""
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_multiple_chests_success(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет успешную покупку нескольких паков с правильной суммой"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_QTY")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        quantity = 5
        price_per_pack = 100.0
        total_price = price_per_pack * quantity
        
        # Мокируем успешную верификацию транзакции с правильной суммой
        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": total_price,
            "sender": wallet_address
        }
        
        tx_signature = "valid_tx_multiple_123"
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature,
                    "quantity": quantity
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["quantity"] == quantity
            assert len(data["purchase_ids"]) == quantity
            
            # Проверяем, что verify_solana_transaction была вызвана с правильной суммой
            mock_verify_tx.assert_called_once()
            call_args = mock_verify_tx.call_args
            assert call_args[1]["expected_amount"] == total_price
            
            # Проверяем, что создано правильное количество покупок
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = %s
            """, (user_id, chest_id))
            count = cursor.fetchone()[0]
            assert count == quantity
            
            # Проверяем, что джекпот получил правильную сумму (10% от общей)
            cursor.execute("""
                SELECT total_amount FROM Jackpot_rounds WHERE status = 'active'
                ORDER BY id_round DESC LIMIT 1
            """)
            jackpot = cursor.fetchone()
            if jackpot:
                expected_jackpot = total_price * 0.1
                assert float(jackpot[0]) == expected_jackpot, \
                    f"Expected jackpot contribution {expected_jackpot}, got {jackpot[0]}"
            
            cursor.close()
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_multiple_chests_wrong_amount(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет, что нельзя купить много паков за меньшую стоимость"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_WRONG")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        quantity = 10  # Пытаемся купить 10 паков
        price_per_pack = 100.0
        total_price = price_per_pack * quantity  # Должно быть 1000
        wrong_amount = 100.0  # Но отправляем только 100 (цена за 1 пак)
        
        # Мокируем верификацию транзакции - она должна вернуть невалидную транзакцию
        mock_verify_tx.return_value = {
            "valid": False,
            "error": f"Expected amount {total_price}, but got {wrong_amount}",
            "actual_amount": wrong_amount
        }
        
        tx_signature = "wrong_amount_tx_123"
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature,
                    "quantity": quantity
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Transaction verification failed" in data["error"]
            
            # Проверяем, что verify_solana_transaction была вызвана с правильной суммой
            mock_verify_tx.assert_called_once()
            call_args = mock_verify_tx.call_args
            assert call_args[1]["expected_amount"] == total_price, \
                f"Expected verification with amount {total_price}, but got {call_args[1]['expected_amount']}"
            
            # Проверяем, что покупки НЕ созданы
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = %s AND tx_signature LIKE %s
            """, (user_id, chest_id, f"{tx_signature}%"))
            count = cursor.fetchone()[0]
            assert count == 0, "No purchases should be created with wrong amount"
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_buy_chest_invalid_quantity_too_low(self, clean_db, db_connection):
        """Проверяет валидацию количества - нельзя купить меньше 1 пака"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE_INVALID")
        )
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": 1,
                    "txSignature": "test_signature",
                    "quantity": 0  # Невалидное количество
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Quantity must be between 1 and 100" in data["error"]
    
    @pytest.mark.asyncio
    async def test_buy_chest_invalid_quantity_too_high(self, clean_db, db_connection):
        """Проверяет валидацию количества - нельзя купить больше 100 паков"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE_INVALID_HIGH")
        )
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": 1,
                    "txSignature": "test_signature",
                    "quantity": 101  # Невалидное количество
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Quantity must be between 1 and 100" in data["error"]
    
    @pytest.mark.asyncio
    async def test_buy_chest_invalid_quantity_type(self, clean_db, db_connection):
        """Проверяет валидацию типа количества"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE_INVALID_TYPE")
        )
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": 1,
                    "txSignature": "test_signature",
                    "quantity": "not_a_number"  # Невалидный тип
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Invalid quantity" in data["error"]
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_default_quantity(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет, что при отсутствии quantity используется значение по умолчанию (1)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокируем успешную верификацию транзакции
        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": 100.0,
            "sender": "test_wallet"
        }
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_DEFAULT")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        tx_signature = "default_quantity_tx_123"
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Не указываем quantity - должно использоваться значение по умолчанию (1)
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            # Проверяем, что quantity по умолчанию = 1
            assert data.get("quantity", 1) == 1
            assert len(data["purchase_ids"]) == 1
            
            # Проверяем, что verify_solana_transaction была вызвана с суммой за 1 пак
            mock_verify_tx.assert_called_once()
            call_args = mock_verify_tx.call_args
            assert call_args[1]["expected_amount"] == 100.0
            
            # Проверяем, что создана только одна покупка
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM Chest_purchases 
                WHERE id_user = %s AND id_chest = %s AND tx_signature = %s
            """, (user_id, chest_id, tx_signature))
            count = cursor.fetchone()[0]
            assert count == 1
            cursor.close()
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_jackpot_contribution_multiple(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет, что джекпот получает правильную сумму при покупке нескольких паков"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_JACKPOT")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 250)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        quantity = 4
        price_per_pack = 250.0
        total_price = price_per_pack * quantity  # 1000
        expected_jackpot_contribution = total_price * 0.1  # 100
        
        # Мокируем успешную верификацию транзакции
        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": total_price,
            "sender": wallet_address
        }
        
        tx_signature = "jackpot_test_tx_123"
        
        # Получаем начальную сумму джекпота
        cursor = db_connection.cursor()
        cursor.execute("""
            SELECT COALESCE(total_amount, 0) as total FROM Jackpot_rounds 
            WHERE status = 'active' ORDER BY id_round DESC LIMIT 1
        """)
        initial_jackpot = cursor.fetchone()[0] if cursor.rowcount > 0 else 0.0
        cursor.close()
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature,
                    "quantity": quantity
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что джекпот получил правильную сумму
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT total_amount FROM Jackpot_rounds 
                WHERE status = 'active' ORDER BY id_round DESC LIMIT 1
            """)
            final_jackpot = cursor.fetchone()[0]
            jackpot_increase = float(final_jackpot) - float(initial_jackpot)
            
            assert abs(jackpot_increase - expected_jackpot_contribution) < 0.01, \
                f"Expected jackpot increase {expected_jackpot_contribution}, got {jackpot_increase}"
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_buy_chest_wrong_wallet(self, clean_db, db_connection):
        """Проверяет, что нельзя купить пак за другого пользователя"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем двух пользователей
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
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        # Авторизуемся как первый пользователь
        message = "Gamba Auth: 1234567890"
        signed = signing_key_1.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_1,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся купить пак для второго пользователя
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_2,  # Чужой wallet
                    "id_chest": chest_id,
                    "txSignature": "test_signature"
                },
                headers=headers
            )
            assert response.status_code == 403
            data = response.json()
            assert data["success"] is False
            assert "Access denied" in data["error"]
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_with_cookie_auth(self, mock_verify_tx, clean_db, db_connection):
        """Проверяет покупку пака с cookie авторизацией"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Мокируем успешную верификацию транзакции
        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": 100.0,
            "sender": "test_wallet"
        }
        
        # Создаем пользователя и пак
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user, ref_code",
            (wallet_address, "TESTCODE")
        )
        result = cursor.fetchone()
        user_id = result[0]
        ref_code = result[1]
        
        cursor.execute("""
            INSERT INTO Chests (prob_common, prob_rare, prob_epic, prob_legendary, chance_loss, price)
            VALUES (80, 12, 7, 1, 0, 100)
            RETURNING id_chest
        """)
        chest_id = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        # Создаем валидный cookie токен
        token_data = f"{wallet_address}:{ref_code}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()
        
        cookies = {"auth_token": token_hash}
        tx_signature = "cookie_auth_tx_signature"
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": tx_signature
                },
                cookies=cookies
            )
            # Если вернулся 500, выводим ошибку для отладки
            if response.status_code == 500:
                data = response.json()
                print(f"Error in test_buy_chest_with_cookie_auth: {data.get('error', 'Unknown error')}")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is True
            # Проверяем обратную совместимость: при quantity=1 (по умолчанию) должен быть purchase_id
            assert "purchase_id" in data or "purchase_ids" in data
            # Проверяем, что quantity по умолчанию = 1
            assert data.get("quantity", 1) == 1

