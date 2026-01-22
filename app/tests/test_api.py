import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from psycopg2.extras import RealDictCursor
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
    

class TestReferralAPI:
    @pytest.mark.asyncio
    async def test_referral_summary_returns_counts(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        wallet_address = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user, ref_code",
            (wallet_address, "OWNER123")
        )
        owner = cursor.fetchone()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            ("ChildWalletA", "CHILDA")
        )
        child_a = cursor.fetchone()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            ("ChildWalletB", "CHILDB")
        )
        child_b = cursor.fetchone()
        cursor.execute(
            "INSERT INTO Referral_system (id_referrer, id_referred) VALUES (%s, %s)",
            (owner["id_user"], child_a["id_user"])
        )
        cursor.execute(
            "INSERT INTO Referral_system (id_referrer, id_referred) VALUES (%s, %s)",
            (owner["id_user"], child_b["id_user"])
        )
        db_connection.commit()
        cursor.close()
        
        message = "Gamba Auth: 1234567890"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        headers = {
            "X-Wallet": wallet_address,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/api/referral/summary/{wallet_address}", headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["referrals"] == 2
            assert data["refCode"] == "OWNER123"

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
            assert data["balance"]["symbol"] == "TOKENS"


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
    async def test_buy_multiple_chests_duplicate_base_signature_is_blocked(self, mock_verify_tx, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")

        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')

        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_QTY_DUP")
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

        base_sig = "multi_replay_sig_123"
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Chest_purchases (id_user, id_chest, tx_signature) VALUES (%s, %s, %s)",
            (user_id, chest_id, f"{base_sig}_0")
        )
        db_connection.commit()
        cursor.close()

        mock_verify_tx.return_value = {
            "valid": True,
            "actual_amount": 100.0,
            "sender": wallet_address
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/chests/buy",
                json={
                    "wallet": wallet_address,
                    "id_chest": chest_id,
                    "txSignature": base_sig,
                    "quantity": 2
                },
                headers=headers
            )
            assert resp.status_code == 400
            data = resp.json()
            assert data["success"] is False
            assert "Transaction already used" in data["error"]
            mock_verify_tx.assert_not_called()

    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_chest_invalid_transaction(self, mock_verify_tx, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        mock_verify_tx.return_value = {
            "valid": False,
            "error": "Transaction verification failed: Invalid signature"
        }
        
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
            if response.status_code == 500:
                data = response.json()
                print(f"Error in test_buy_chest_invalid_transaction: {data.get('error', 'Unknown error')}")
            assert response.status_code == 400, f"Expected 400, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is False
            assert "Transaction verification failed" in data["error"]


class TestSolanaTransactionVerification:
    @patch('core.utils.requests.post')
    def test_verify_solana_transaction_spl_requires_receiver_in_token_balances(self, mock_post):
        from core.utils import verify_solana_transaction

        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"result": [None]}

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.json.return_value = {
            "result": {
                "meta": {
                    "err": None,
                    "status": {"Ok": None},
                    "preTokenBalances": [
                        {"mint": "MINT", "owner": "OTHER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 0}}
                    ],
                    "postTokenBalances": [
                        {"mint": "MINT", "owner": "OTHER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 10}}
                    ]
                },
                "transaction": {
                    "message": {
                        "accountKeys": ["SENDER"],
                        "instructions": []
                    }
                }
            }
        }

        mock_post.side_effect = [status_resp, tx_resp]

        r = verify_solana_transaction(
            tx_signature="SIG",
            expected_sender="SENDER",
            expected_receiver="RECEIVER",
            expected_amount=10.0,
            rpc_url="http://rpc",
            mint_address="MINT"
        )
        assert r["valid"] is False
        assert "Receiver mismatch" in r["error"]

    @patch('core.utils.requests.post')
    def test_verify_solana_transaction_spl_requires_amount_determined(self, mock_post):
        from core.utils import verify_solana_transaction

        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"result": [None]}

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.json.return_value = {
            "result": {
                "meta": {
                    "err": None,
                    "status": {"Ok": None},
                    "preTokenBalances": [
                        {"mint": "MINT", "owner": "RECEIVER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 0}}
                    ],
                    "postTokenBalances": [
                        {"mint": "MINT", "owner": "RECEIVER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 0}}
                    ]
                },
                "transaction": {
                    "message": {
                        "accountKeys": ["SENDER"],
                        "instructions": []
                    }
                }
            }
        }

        mock_post.side_effect = [status_resp, tx_resp]

        r = verify_solana_transaction(
            tx_signature="SIG",
            expected_sender="SENDER",
            expected_receiver="RECEIVER",
            expected_amount=10.0,
            rpc_url="http://rpc",
            mint_address="MINT"
        )
        assert r["valid"] is False
        assert "Could not determine transferred amount" in r["error"]

    @patch('core.utils.requests.post')
    def test_verify_solana_transaction_spl_success_from_token_balances(self, mock_post):
        from core.utils import verify_solana_transaction

        status_resp = MagicMock()
        status_resp.status_code = 200
        status_resp.json.return_value = {"result": [None]}

        tx_resp = MagicMock()
        tx_resp.status_code = 200
        tx_resp.json.return_value = {
            "result": {
                "meta": {
                    "err": None,
                    "status": {"Ok": None},
                    "preTokenBalances": [
                        {"mint": "MINT", "owner": "RECEIVER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 0}}
                    ],
                    "postTokenBalances": [
                        {"mint": "MINT", "owner": "RECEIVER", "accountIndex": 1, "uiTokenAmount": {"uiAmount": 10}}
                    ]
                },
                "transaction": {
                    "message": {
                        "accountKeys": ["SENDER"],
                        "instructions": []
                    }
                }
            }
        }

        mock_post.side_effect = [status_resp, tx_resp]

        r = verify_solana_transaction(
            tx_signature="SIG",
            expected_sender="SENDER",
            expected_receiver="RECEIVER",
            expected_amount=10.0,
            rpc_url="http://rpc",
            mint_address="MINT"
        )
        assert r["valid"] is True
        assert r["sender"] == "SENDER"
        assert abs(r["actual_amount"] - 10.0) < 1e-9


class TestChestPurchaseQuantity:
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_multiple_chests_success(self, mock_verify_tx, clean_db, db_connection):
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
            
            # Проверяем, что джекпот получил правильную сумму (40% от общей)
            cursor.execute("""
                SELECT total_amount FROM Jackpot_rounds WHERE status = 'active'
                ORDER BY id_round DESC LIMIT 1
            """)
            jackpot = cursor.fetchone()
            if jackpot:
                expected_jackpot = total_price * 0.4
                assert float(jackpot[0]) == expected_jackpot, \
                    f"Expected jackpot contribution {expected_jackpot}, got {jackpot[0]}"
            
            cursor.close()
    
    @pytest.mark.asyncio
    @patch('routes.api.verify_solana_transaction')
    async def test_buy_multiple_chests_wrong_amount(self, mock_verify_tx, clean_db, db_connection):
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
        expected_jackpot_contribution = total_price * 0.4  # 400
        
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
                cookies=cookies,
                headers={"Origin": "http://test"}
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


class TestCardsTradeAPI:
    """Тесты для API обмена карт"""
    
    @pytest.mark.asyncio
    async def test_trade_cards_requires_auth(self, clean_db, db_connection):
        """Проверяет, что обмен карт требует авторизацию"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": "test_wallet",
                    "cards": [{"id_card": 1, "quantity": 4}],
                    "rarity": "basic"
                }
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_trade_cards_basic_success(self, clean_db, db_connection):
        """Проверяет успешный обмен 4 обычных карт на 1 обычную"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем одну обычную карту с quantity=4
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic Card', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        # Добавляем карту пользователю с quantity=4
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 4)
        """, (user_id, card_id))
        
        # Создаем еще одну обычную карту для получения при обмене (нужна для get_random_card_by_rarity)
        # Важно: image_url должен быть не пустым
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Reward Basic Card', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_BASIC_{wallet_address[:8]}',))
        reward_card_id = cursor.fetchone()[0]
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            if response.status_code != 200:
                data = response.json()
                print(f"Trade failed: {data}")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is True
            assert "card" in data
            assert data["card"]["rarity"] == "basic"
            
            # Проверяем, что карты удалены
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT quantity FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, card_id))
            result = cursor.fetchone()
            # Карта должна быть удалена (quantity = 0 или запись удалена)
            assert result is None or result[0] == 0
            
            # Проверяем, что получена новая карта
            cursor.execute("""
                SELECT COUNT(*) FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, data["card"]["id_card"]))
            new_card_count = cursor.fetchone()[0]
            assert new_card_count > 0
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_trade_cards_rare_success(self, clean_db, db_connection):
        """Проверяет успешный обмен 3 редких карт на 1 редкую"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_RARE")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем одну редкую карту с quantity=3
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('rare', 50, 'Test Rare Card', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_RARE_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 3)
        """, (user_id, card_id))
        
        # Создаем еще одну редкую карту для получения при обмене
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('rare', 50, 'Reward Rare Card', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_RARE_{wallet_address[:8]}',))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 3}],
                    "rarity": "rare"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["card"]["rarity"] == "rare"
    
    @pytest.mark.asyncio
    async def test_trade_cards_epic_success(self, clean_db, db_connection):
        """Проверяет успешный обмен 2 эпичных карт на 1 эпичную"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_EPIC")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем одну эпичную карту с quantity=2
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('epic', 100, 'Test Epic Card', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_EPIC_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 2)
        """, (user_id, card_id))
        
        # Создаем еще одну эпичную карту для получения при обмене
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('epic', 100, 'Reward Epic Card', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_EPIC_{wallet_address[:8]}',))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 2}],
                    "rarity": "epic"
                },
                headers=headers
            )
            if response.status_code != 200:
                data = response.json()
                print(f"Trade failed: {data}")
            assert response.status_code == 200, f"Expected 200, got {response.status_code}. Response: {response.json()}"
            data = response.json()
            assert data["success"] is True
            assert data["card"]["rarity"] == "epic"
    
    @pytest.mark.asyncio
    async def test_trade_cards_multiple_cards_success(self, clean_db, db_connection):
        """Проверяет обмен нескольких разных карт одного типа"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_MULTI")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем 2 разные обычные карты, каждая с quantity=2
        card_ids = []
        for i in range(2):
            cursor.execute("""
                INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
                VALUES ('basic', 10, %s, 'https://example.com/test.png', %s)
                RETURNING id_card
            """, (f'Test Basic {i}', f'TEST_BASIC_MULTI_{i}_{wallet_address[:8]}'))
            card_id = cursor.fetchone()[0]
            card_ids.append(card_id)
            
            # Каждая карта имеет quantity=2
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 2)
            """, (user_id, card_id))
        
        # Создаем еще одну обычную карту для получения при обмене
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Reward Basic Multi', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_BASIC_MULTI_{wallet_address[:8]}',))
        
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
            # Обмениваем 2 карты первого типа и 2 карты второго типа
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [
                        {"id_card": card_ids[0], "quantity": 2},
                        {"id_card": card_ids[1], "quantity": 2}
                    ],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["card"]["rarity"] == "basic"
    
    @pytest.mark.asyncio
    async def test_trade_cards_wrong_quantity_basic(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять неправильное количество карт (basic: нужно 4)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_WRONG")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем 3 обычные карты (нужно 4)
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_WRONG_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 3)
        """, (user_id, card_id))
        
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
            # Пытаемся обменять 3 карты (нужно 4)
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 3}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Need exactly 4 cards" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_wrong_quantity_rare(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять неправильное количество редких карт (нужно 3)"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_RARE_WRONG")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('rare', 50, 'Test Rare', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_RARE_WRONG_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 2)
        """, (user_id, card_id))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 2}],
                    "rarity": "rare"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Need exactly 3 cards" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_wrong_rarity(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять карты неправильной редкости"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_RARITY")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем редкую карту
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('rare', 50, 'Test Rare', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_RARE_RARITY_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 4)
        """, (user_id, card_id))
        
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
            # Пытаемся обменять редкую карту как обычную (4 карты для basic)
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            # Проверяем, что ошибка связана с неправильной редкостью
            assert "is not basic rarity" in data["error"] or "Need exactly 4 cards" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_not_enough_cards(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять больше карт, чем есть у пользователя"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_NOT_ENOUGH")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_NOT_ENOUGH_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        # У пользователя только 2 карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 2)
        """, (user_id, card_id))
        
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
            # Пытаемся обменять 4 карты (есть только 2)
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Not enough cards" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_wrong_user(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять чужие карты"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        # Создаем двух пользователей
        signing_key_1 = SigningKey.generate()
        wallet_1 = base58.b58encode(signing_key_1.verify_key.encode()).decode('utf-8')
        
        signing_key_2 = SigningKey.generate()
        wallet_2 = base58.b58encode(signing_key_2.verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_1, "TESTCODE_USER1")
        )
        user1_id = cursor.fetchone()[0]
        
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_2, "TESTCODE_USER2")
        )
        user2_id = cursor.fetchone()[0]
        
        # Пользователь 1 создает карту
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'User1 Card', 'test.png', %s)
            RETURNING id_card
        """, (f'TEST_USER1_CARD_{wallet_1[:8]}',))
        card_id = cursor.fetchone()[0]
        
        # Пользователь 1 получает 4 карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 4)
        """, (user1_id, card_id))
        
        db_connection.commit()
        cursor.close()
        
        # Пользователь 2 пытается обменять карты пользователя 1
        message = "Gamba Auth: 1234567890"
        signed = signing_key_2.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_2,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_2,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Not enough cards" in data["error"] or "You have 0" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_legendary_not_allowed(self, clean_db, db_connection):
        """Проверяет, что нельзя обменять легендарные карты"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_LEG")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('legendary', 1000, 'Test Legendary', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_LEG_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 1)
        """, (user_id, card_id))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 1}],
                    "rarity": "legendary"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Only basic, rare, and epic are allowed" in data["error"] or "Invalid rarity" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_missing_fields(self, clean_db, db_connection):
        """Проверяет, что все обязательные поля должны быть указаны"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (wallet_address, "TESTCODE_TRADE_MISSING")
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
            # Без cards
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 400
            data = response.json()
            assert data["success"] is False
            assert "Missing required fields" in data["error"]
    
    @pytest.mark.asyncio
    async def test_trade_cards_quantity_decreases_correctly(self, clean_db, db_connection):
        """Проверяет, что количество карт уменьшается правильно при обмене"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_QTY")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем карту с quantity = 6
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_QTY_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 6)
        """, (user_id, card_id))
        
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
            # Обмениваем 4 карты
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что осталось 2 карты (6 - 4 = 2)
            # Новая карта гарантированно другая (не может быть той же, что обменивается)
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT quantity FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, card_id))
            result = cursor.fetchone()
            assert result is not None
            assert result[0] == 2, f"Expected 2 cards remaining, got {result[0]}"
            
            # Проверяем, что новая карта действительно другая
            new_card_id = data["card"]["id_card"]
            assert new_card_id != card_id, "New card should be different from traded card"
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_trade_cards_card_removed_when_quantity_zero(self, clean_db, db_connection):
        """Проверяет, что карта удаляется из Card_User когда quantity становится 0"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_REMOVE")
        )
        user_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_REMOVE_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        # У пользователя ровно 4 карты
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 4)
        """, (user_id, card_id))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что обмениваемая карта удалена или quantity = 0
            # (если новая карта имеет тот же id_card, то quantity будет 1)
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT quantity FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, card_id))
            result = cursor.fetchone()
            
            new_card_id = data["card"]["id_card"]
            if new_card_id == card_id:
                # Если новая карта та же самая, то quantity должна быть 1 (новая карта)
                assert result is not None
                assert result[0] == 1
            else:
                # Если новая карта другая, то старая запись должна быть удалена
                assert result is None or result[0] == 0
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_trade_cards_saves_to_history(self, clean_db, db_connection):
        """Проверяет, что обмен сохраняется в таблицу Card_trades"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_HISTORY")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем одну обычную карту с quantity=4
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Test Basic', 'https://example.com/test.png', %s)
            RETURNING id_card
        """, (f'TEST_BASIC_HISTORY_{wallet_address[:8]}',))
        card_id = cursor.fetchone()[0]
        
        cursor.execute("""
            INSERT INTO Card_User (id_user, id_card, quantity)
            VALUES (%s, %s, 4)
        """, (user_id, card_id))
        
        # Создаем еще одну обычную карту для получения при обмене
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Reward Basic', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_BASIC_HISTORY_{wallet_address[:8]}',))
        
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
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [{"id_card": card_id, "quantity": 4}],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что запись создана в Card_trades
            from psycopg2.extras import RealDictCursor
            cursor = db_connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT id_trade, id_user, traded_cards, received_card_id, rarity
                FROM Card_trades
                WHERE id_user = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,))
            trade_record = cursor.fetchone()
            
            assert trade_record is not None, "Trade record should be saved to Card_trades"
            assert trade_record['id_user'] == user_id
            assert trade_record['rarity'] == 'basic'
            
            # Проверяем traded_cards (JSONB)
            # PostgreSQL возвращает JSONB как dict через psycopg2, не как строку
            traded_cards = trade_record['traded_cards']
            if isinstance(traded_cards, str):
                traded_cards = json.loads(traded_cards)
            assert isinstance(traded_cards, list), f"traded_cards should be a list, got {type(traded_cards)}"
            assert len(traded_cards) == 1
            assert traded_cards[0]['id_card'] == card_id
            assert traded_cards[0]['quantity'] == 4
            
            # Проверяем received_card_id
            assert trade_record['received_card_id'] == data["card"]["id_card"]
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_trade_cards_new_card_not_in_traded(self, clean_db, db_connection):
        """Проверяет, что новая карта не может быть того же типа, что обмениваемые"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_EXCLUDE")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем 2 разные обычные карты
        card_ids = []
        for i in range(2):
            cursor.execute("""
                INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
                VALUES ('basic', 10, %s, 'https://example.com/test.png', %s)
                RETURNING id_card
            """, (f'Test Basic {i}', f'TEST_BASIC_EXCLUDE_{i}_{wallet_address[:8]}'))
            card_id = cursor.fetchone()[0]
            card_ids.append(card_id)
            
            # Каждая карта имеет quantity=2
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 2)
            """, (user_id, card_id))
        
        # Создаем еще одну обычную карту для получения при обмене (должна быть выбрана)
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Reward Basic', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_BASIC_EXCLUDE_{wallet_address[:8]}',))
        reward_card_id = cursor.fetchone()[0]
        
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
            # Обмениваем 2 карты первого типа и 2 карты второго типа
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [
                        {"id_card": card_ids[0], "quantity": 2},
                        {"id_card": card_ids[1], "quantity": 2}
                    ],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что новая карта не совпадает с обмениваемыми
            new_card_id = data["card"]["id_card"]
            assert new_card_id != card_ids[0], "New card should not be the same as first traded card"
            assert new_card_id != card_ids[1], "New card should not be the same as second traded card"
            
            # Проверяем, что новая карта существует в системе
            cursor = db_connection.cursor()
            cursor.execute("""
                SELECT id_card FROM Cards WHERE id_card = %s
            """, (new_card_id,))
            card_exists = cursor.fetchone()
            assert card_exists is not None, "New card should exist in Cards table"
            
            # Проверяем, что новая карта добавлена пользователю
            cursor.execute("""
                SELECT quantity FROM Card_User
                WHERE id_user = %s AND id_card = %s
            """, (user_id, new_card_id))
            user_card = cursor.fetchone()
            assert user_card is not None, "New card should be added to user"
            assert user_card[0] >= 1, "New card quantity should be at least 1"
            
            cursor.close()
    
    @pytest.mark.asyncio
    async def test_trade_cards_excludes_all_traded_cards(self, clean_db, db_connection):
        """Проверяет, что все обмениваемые карты исключаются из выборки новой карты"""
        if db_connection is None:
            pytest.skip("Database not available")
        
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')
        
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet_address, "TESTCODE_TRADE_EXCLUDE_ALL")
        )
        user_id = cursor.fetchone()[0]
        
        # Создаем 4 разные обычные карты
        card_ids = []
        for i in range(4):
            cursor.execute("""
                INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
                VALUES ('basic', 10, %s, 'https://example.com/test.png', %s)
                RETURNING id_card
            """, (f'Test Basic {i}', f'TEST_BASIC_EXCLUDE_ALL_{i}_{wallet_address[:8]}'))
            card_id = cursor.fetchone()[0]
            card_ids.append(card_id)
            
            cursor.execute("""
                INSERT INTO Card_User (id_user, id_card, quantity)
                VALUES (%s, %s, 1)
            """, (user_id, card_id))
        
        # Создаем еще одну обычную карту для получения при обмене
        cursor.execute("""
            INSERT INTO Cards (rarity, start_bounty, name, image_url, image_key)
            VALUES ('basic', 10, 'Reward Basic', 'https://example.com/reward.png', %s)
            RETURNING id_card
        """, (f'TEST_REWARD_BASIC_EXCLUDE_ALL_{wallet_address[:8]}',))
        reward_card_id = cursor.fetchone()[0]
        
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
            # Обмениваем все 4 карты (по 1 каждого типа)
            response = await client.post(
                "/api/cards/trade",
                json={
                    "wallet": wallet_address,
                    "cards": [
                        {"id_card": card_ids[0], "quantity": 1},
                        {"id_card": card_ids[1], "quantity": 1},
                        {"id_card": card_ids[2], "quantity": 1},
                        {"id_card": card_ids[3], "quantity": 1}
                    ],
                    "rarity": "basic"
                },
                headers=headers
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            
            # Проверяем, что новая карта не совпадает ни с одной из обмениваемых
            new_card_id = data["card"]["id_card"]
            for traded_card_id in card_ids:
                assert new_card_id != traded_card_id, f"New card (id={new_card_id}) should not be the same as traded card (id={traded_card_id})"
            
            cursor.close()


class TestConfigAPI:
    """Тесты для эндпоинта /api/config"""
    
    @pytest.mark.asyncio
    async def test_get_config_public(self):
        """Тест: /api/config доступен без авторизации"""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/config")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "rpcUrl" in data
            assert "merchant" in data
            assert "mint" in data
    
    @pytest.mark.asyncio
    async def test_get_config_returns_valid_structure(self):
        """Тест: /api/config возвращает правильную структуру данных"""
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/config")
            assert response.status_code == 200
            data = response.json()
            
            # Проверяем структуру
            assert isinstance(data["rpcUrl"], str)
            assert isinstance(data["merchant"], str)
            assert isinstance(data["mint"], str) or data["mint"] is None
            
            # Проверяем, что merchant имеет дефолтное значение, если не настроен
            if not data["merchant"] or data["merchant"] == "11111111111111111111111111111111":
                # Это нормально для тестовой среды
                pass


class TestReferralRedirect:
    """Тесты для реферального редиректа /ref/{ref_code}"""
    
    @pytest.mark.asyncio
    async def test_referral_redirect_public(self):
        """Тест: реферальный редирект доступен без авторизации"""
        async with AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
            response = await client.get("/ref/TESTCODE")
            # Редирект должен вернуть 307 или 301
            assert response.status_code in [301, 302, 307, 308]
    
    @pytest.mark.asyncio
    async def test_referral_redirect_to_home_with_query(self):
        """Тест: реферальный редирект перенаправляет на главную с query параметром"""
        async with AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
            ref_code = "TESTCODE123"
            response = await client.get(f"/ref/{ref_code}")
            
            # Проверяем статус редиректа
            assert response.status_code in [301, 302, 307, 308]
            
            # Проверяем Location header
            location = response.headers.get("location")
            assert location is not None
            assert location == f"/?ref={ref_code}"
    
    @pytest.mark.asyncio
    async def test_referral_redirect_with_special_characters(self):
        """Тест: реферальный редирект работает с различными кодами"""
        async with AsyncClient(app=app, base_url="http://test", follow_redirects=False) as client:
            test_codes = ["ABC123", "XYZ789", "LONGCODE123456"]
            
            for ref_code in test_codes:
                response = await client.get(f"/ref/{ref_code}")
                assert response.status_code in [301, 302, 307, 308]
                location = response.headers.get("location")
                assert location == f"/?ref={ref_code}"

