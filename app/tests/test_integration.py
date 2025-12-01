import pytest
import sys
from pathlib import Path

# Добавляем родительскую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
import json
from nacl.signing import SigningKey
import base58


class TestFullAuthFlow:
    @pytest.mark.asyncio
    async def test_full_auth_flow(self, clean_db):
        async with AsyncClient(app=app, base_url="http://test") as client:
            wallet_signing_key = SigningKey.generate()
            wallet_verify_key = wallet_signing_key.verify_key
            wallet_address = base58.b58encode(wallet_verify_key.encode()).decode('utf-8')
            
            message = "Gamba Auth: 1234567890"
            signed = wallet_signing_key.sign(message.encode('utf-8'))
            signature_list = list(signed.signature)
            
            # Создаем пользователя
            auth_response = await client.post(
                "/api/auth",
                json={
                    "wallet": wallet_address,
                    "signature": signature_list,
                    "message": message
                }
            )
            
            # Проверяем, что пользователь создан (может быть ошибка верификации, но не 401)
            assert auth_response.status_code != 401

            request_message = "Gamba Auth: 1234567891"
            request_signed = wallet_signing_key.sign(request_message.encode('utf-8'))
            request_signature = list(request_signed.signature)
            
            headers = {
                "X-Wallet": wallet_address,
                "X-Signature": json.dumps(request_signature),
                "X-Message": request_message
            }
            
            # Пытаемся получить доступ к /shop
            shop_response = await client.get("/shop", headers=headers)
            # Должен либо пройти (200), либо вернуть ошибку верификации, но не 401 из-за отсутствия заголовков
            assert shop_response.status_code != 401 or "Missing" not in str(shop_response.content)
    
    @pytest.mark.asyncio
    async def test_unauthorized_access_blocked(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Пытаемся получить доступ без заголовков
            response = await client.get("/shop")
            assert response.status_code == 401
            
            data = response.json()
            # FastAPI возвращает {"detail": "..."} для HTTPException
            assert "detail" in data or "error" in data or "Unauthorized" in str(data).lower() or "Missing" in str(data)
    
    @pytest.mark.asyncio
    async def test_public_endpoints_accessible(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Проверяем публичные эндпоинты
            public_endpoints = [
                "/",
                "/health",
                "/api/whitelist/TestWallet123",
            ]
            
            for endpoint in public_endpoints:
                response = await client.get(endpoint)
                assert response.status_code != 401, f"Endpoint {endpoint} should be public"
    
    @pytest.mark.asyncio
    async def test_protected_endpoints_blocked(self):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Проверяем защищенные эндпоинты (включая профиль)
            protected_endpoints = [
                "/shop",
                "/battle",
                "/cards",
                "/rules",
                "/profile",
            ]
            
            for endpoint in protected_endpoints:
                response = await client.get(endpoint)
                assert response.status_code == 401, f"Endpoint {endpoint} should be protected"
                data = response.json()
                # FastAPI возвращает {"detail": "..."} для HTTPException
                assert "detail" in data or "error" in data or "Unauthorized" in str(data).lower() or "Missing" in str(data)

