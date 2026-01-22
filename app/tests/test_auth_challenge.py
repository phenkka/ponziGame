import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from httpx import AsyncClient
from main import app
import base58
from nacl.signing import SigningKey


class TestAuthChallenge:
    @pytest.mark.asyncio
    async def test_auth_challenge_is_one_time(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")

        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')

        async with AsyncClient(app=app, base_url="http://test") as client:
            ch = await client.post("/api/auth/challenge", json={"wallet": wallet_address})
            assert ch.status_code == 200
            ch_data = ch.json()
            assert ch_data.get("success") is True
            message = ch_data.get("message")
            assert isinstance(message, str) and message

            signature_list = list(signing_key.sign(message.encode('utf-8')).signature)

            r1 = await client.post(
                "/api/auth",
                json={"wallet": wallet_address, "signature": signature_list, "message": message}
            )
            assert r1.status_code == 200
            d1 = r1.json()
            assert d1.get("success") is True

            r2 = await client.post(
                "/api/auth",
                json={"wallet": wallet_address, "signature": signature_list, "message": message}
            )
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2.get("success") is False
            assert "already used" in str(d2.get("error", "")).lower() or "expired" in str(d2.get("error", "")).lower()

    @pytest.mark.asyncio
    async def test_auth_requires_challenge_when_enabled(self, clean_db, db_connection, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        monkeypatch.setenv("AUTH_CHALLENGE_REQUIRED", "1")

        import time

        signing_key = SigningKey.generate()
        verify_key = signing_key.verify_key
        wallet_address = base58.b58encode(verify_key.encode()).decode('utf-8')

        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)

        async with AsyncClient(app=app, base_url="http://test") as client:
            r = await client.post(
                "/api/auth",
                json={"wallet": wallet_address, "signature": signature_list, "message": message}
            )
            assert r.status_code == 200
            data = r.json()
            assert data.get("success") is False
            assert "challenge" in str(data.get("error", "")).lower()
