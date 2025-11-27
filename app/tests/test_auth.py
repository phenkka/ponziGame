import pytest
import sys
from pathlib import Path

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app


class TestPublicEndpoints:
    @pytest.mark.asyncio
    async def test_root_endpoint(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert "text/html" in response.headers.get("content-type", "")
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
    
    @pytest.mark.asyncio
    async def test_api_whitelist_public(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/api/whitelist/TestWallet123")
            assert response.status_code == 200
            data = response.json()
            assert "success" in data
    
    @pytest.mark.asyncio
    async def test_api_auth_public(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/auth",
                json={
                    "wallet": "TestWallet123",
                    "signature": [1, 2, 3],
                    "message": "Test message"
                }
            )
            # Может вернуть ошибку, но не 401 Unauthorized
            assert response.status_code != 401


class TestProtectedEndpoints:
    
    @pytest.mark.asyncio
    async def test_shop_page_requires_auth(self, missing_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/shop", headers=missing_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_battle_page_requires_auth(self, missing_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/battle", headers=missing_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_cards_page_requires_auth(self, missing_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/cards", headers=missing_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_profile_page_requires_auth(self, missing_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/profile", headers=missing_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_rules_page_requires_auth(self, missing_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/rules", headers=missing_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_shop_page_with_invalid_signature(self, invalid_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/shop", headers=invalid_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
            assert "signature" in str(data["detail"]).lower() or "invalid" in str(data["detail"]).lower() or "authentication" in str(data["detail"]).lower()
    
    @pytest.mark.asyncio
    async def test_battle_page_with_invalid_signature(self, invalid_auth_headers):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/battle", headers=invalid_auth_headers)
            assert response.status_code == 401
            data = response.json()
            assert "detail" in data
    
    @pytest.mark.asyncio
    async def test_pages_with_valid_signature(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        import base58
        from nacl.signing import SigningKey
        import json
        import hashlib
        
        # Создаем реальный Solana адрес и пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_bytes = verify_key.encode()
        wallet_address = base58.b58encode(wallet_bytes).decode('utf-8')
        
        # Создаем пользователя в БД
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING ref_code",
            (wallet_address, "TESTCODE")
        )
        ref_code = cursor.fetchone()[0]
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
        
        # Проверяем все защищенные страницы
        pages = ["/shop", "/battle", "/cards", "/profile", "/rules"]
        async with AsyncClient(app=app, base_url="http://test") as client:
            for page in pages:
                response = await client.get(page, headers=headers)
                assert response.status_code == 200, f"Page {page} should be accessible with valid signature"
                assert "text/html" in response.headers.get("content-type", "")
    
    @pytest.mark.asyncio
    async def test_pages_with_valid_cookie(self, clean_db, db_connection, test_user):
        if db_connection is None:
            pytest.skip("Database not available")
        
        import hashlib
        
        # Создаем валидный cookie токен
        token_data = f"{test_user['wallet']}:{test_user['ref_code']}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()
        
        cookies = {"auth_token": token_hash}
        
        # Проверяем все защищенные страницы
        pages = ["/shop", "/battle", "/cards", "/profile", "/rules"]
        async with AsyncClient(app=app, base_url="http://test") as client:
            for page in pages:
                response = await client.get(page, cookies=cookies)
                assert response.status_code == 200, f"Page {page} should be accessible with valid cookie"
                assert "text/html" in response.headers.get("content-type", "")
    
    @pytest.mark.asyncio
    async def test_api_user_requires_auth(self, missing_auth_headers, test_user):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/user/{test_user['wallet']}",
                headers=missing_auth_headers
            )
            assert response.status_code == 401
            data = response.json()
            # FastAPI возвращает {"detail": "..."} для HTTPException
            assert "detail" in data or "error" in data or "Unauthorized" in str(data).lower() or "Missing" in str(data)
    
    @pytest.mark.asyncio
    async def test_api_user_with_invalid_signature(self, invalid_auth_headers):
        # Используем wallet из invalid_auth_headers
        invalid_wallet = invalid_auth_headers.get("X-Wallet", "InvalidWallet")
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/user/{invalid_wallet}",
                headers=invalid_auth_headers
            )
            assert response.status_code == 401
            data = response.json()
            # FastAPI возвращает {"detail": "..."} для HTTPException или {"error": "..."} для JSONResponse
            assert "detail" in data or "error" in data or "Unauthorized" in str(data).lower() or "Invalid" in str(data).lower()
    
    @pytest.mark.asyncio
    async def test_api_user_with_missing_headers(self, test_user):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Отправляем запрос без заголовков авторизации
            response = await client.get(f"/api/user/{test_user['wallet']}")
            assert response.status_code == 401
            data = response.json()
            # FastAPI возвращает {"detail": "..."} для HTTPException или {"error": "..."} для JSONResponse
            assert "detail" in data or "error" in data or "Unauthorized" in str(data).lower() or "Missing" in str(data).lower()


class TestAuthFlow:
    @pytest.mark.asyncio
    async def test_auth_creates_user(self, clean_db, db_connection):
        # Пропускаем тест, если БД недоступна
        if db_connection is None:
            pytest.skip("Database not available")
        
        import base58
        from nacl.signing import SigningKey
        
        # Создаем валидный Solana адрес и подпись
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_bytes = verify_key.encode()
        wallet_address = base58.b58encode(wallet_bytes).decode('utf-8')
        
        # Создаем валидную подпись
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/auth",
                json={
                    "wallet": wallet_address,
                    "signature": signature_list,
                    "message": message
                }
            )
            # Проверяем, что запрос успешен
            assert response.status_code == 200
            data = response.json()
            assert "success" in data
            # Если успешно, должен вернуть refCode
            if data.get("success"):
                assert "refCode" in data
            else:
                # Если ошибка БД, пропускаем тест
                if "could not translate host name" in str(data.get("error", "")):
                    pytest.skip("Database connection failed")
                # Иначе это ошибка подписи или другая ошибка
                pytest.fail(f"Auth failed: {data.get('error', 'Unknown error')}")
    
    @pytest.mark.asyncio
    async def test_auth_returns_existing_user(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        import base58
        from nacl.signing import SigningKey
        
        # Создаем валидный Solana адрес и пользователя
        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_bytes = verify_key.encode()
        wallet_address = base58.b58encode(wallet_bytes).decode('utf-8')
        
        # Создаем пользователя в БД
        cursor = db_connection.cursor()
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING ref_code",
            (wallet_address, "EXISTINGCODE")
        )
        existing_ref_code = cursor.fetchone()[0]
        db_connection.commit()
        cursor.close()
        
        # Создаем валидную подпись
        message = "Gamba Auth: 1234567890"
        signed = signing_key.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.post(
                "/api/auth",
                json={
                    "wallet": wallet_address,
                    "signature": signature_list,
                    "message": message
                }
            )
            # Проверяем, что запрос успешен
            assert response.status_code == 200
            data = response.json()
            assert "success" in data
            # Должен вернуть существующий refCode
            if data.get("success"):
                assert "refCode" in data
                assert data["refCode"] == existing_ref_code
            else:
                pytest.fail(f"Auth failed: {data.get('error', 'Unknown error')}")


class TestAuthorizationHeaders:
    @pytest.mark.asyncio
    async def test_missing_wallet_header(self, test_user):
        async with AsyncClient(app=app, base_url="http://test") as client:
            headers = {
                "X-Signature": '"[1,2,3]"',
                "X-Message": "Test message"
            }
            response = await client.get(
                f"/api/user/{test_user['wallet']}",
                headers=headers
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_missing_signature_header(self, test_user):
        async with AsyncClient(app=app, base_url="http://test") as client:
            headers = {
                "X-Wallet": test_user['wallet'],
                "X-Message": "Test message"
            }
            response = await client.get(
                f"/api/user/{test_user['wallet']}",
                headers=headers
            )
            assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_missing_message_header(self, test_user):
        async with AsyncClient(app=app, base_url="http://test") as client:
            headers = {
                "X-Wallet": test_user['wallet'],
                "X-Signature": '"[1,2,3]"'
            }
            response = await client.get(
                f"/api/user/{test_user['wallet']}",
                headers=headers
            )
            assert response.status_code == 401


class TestUserAccess:
    @pytest.mark.asyncio
    async def test_user_cannot_access_other_user_data(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")
        
        import base58
        from nacl.signing import SigningKey
        import json
        
        # Создаем двух пользователей с реальными Solana адресами
        signing_key_1 = SigningKey.generate()
        verify_key_1 = signing_key_1.verify_key
        wallet_1 = base58.b58encode(verify_key_1.encode()).decode('utf-8')
        
        signing_key_2 = SigningKey.generate()
        verify_key_2 = signing_key_2.verify_key
        wallet_2 = base58.b58encode(verify_key_2.encode()).decode('utf-8')
        
        # Создаем пользователей в БД
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
        
        # Создаем валидную подпись для первого пользователя
        message = "Gamba Auth: 1234567890"
        signed = signing_key_1.sign(message.encode('utf-8'))
        signature_list = list(signed.signature)
        
        headers = {
            "X-Wallet": wallet_1,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message
        }
        
        # Пытаемся получить данные второго пользователя
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                f"/api/user/{wallet_2}",
                headers=headers
            )
            # Должен вернуть 403 Forbidden или 401
            assert response.status_code in [401, 403]
            if response.status_code == 403:
                data = response.json()
                assert "error" in data or "denied" in str(data).lower() or "access" in str(data).lower()

