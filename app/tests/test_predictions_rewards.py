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
import time


class TestPredictionsTicketPayouts:
    @pytest.mark.asyncio
    async def test_resolve_prediction_pays_tickets(self, clean_db, db_connection, monkeypatch):
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)

        signing_key = SigningKey.generate()
        wallet = base58.b58encode(signing_key.verify_key.encode()).decode('utf-8')

        cursor.execute("INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user", (wallet, "TESTCODE"))
        user_id = cursor.fetchone()['id_user']

        cursor.execute("""
            INSERT INTO public.predictions (
                polymarket_id, title, outcome_a, outcome_b,
                outcome_a_probability, outcome_b_probability,
                outcome_a_odds, outcome_b_odds,
                resolution_date, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_prediction
        """, (
            "test-payout-1", "Test", "Yes", "No",
            50.0, 50.0,
            2.0, 1.5,
            datetime.now(timezone.utc) + timedelta(days=15), "active"
        ))
        prediction_id = cursor.fetchone()['id_prediction']

        cursor.execute("""
            INSERT INTO public.user_bets (id_user, id_prediction, chosen_outcome, status, bet_tickets)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id_bet
        """, (user_id, prediction_id, "A", "pending", 101))
        bet_id = cursor.fetchone()['id_bet']

        db_connection.commit()
        cursor.close()

        message = f"Gamba Auth: {int(time.time() * 1000)}"
        signature_list = list(signing_key.sign(message.encode('utf-8')).signature)
        headers = {
            "X-Wallet": wallet,
            "X-Signature": json.dumps(signature_list),
            "X-Message": message,
            "Content-Type": "application/json"
        }

        async with AsyncClient(app=app, base_url="http://test") as client:
            monkeypatch.delenv("PREDICTIONS_RESOLVE_ADMINS", raising=False)
            resp = await client.post(
                f"/api/predictions/resolve/{prediction_id}",
                headers=headers,
                json={"winner_outcome": "A"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True

        cur = db_connection.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT status, payout_tickets FROM public.user_bets WHERE id_bet = %s", (bet_id,))
        bet_row = cur.fetchone()
        assert bet_row is not None
        assert bet_row['status'] == 'won'
        assert int(bet_row.get('payout_tickets') or 0) == 202

        cur.execute("SELECT tickets_bonus FROM public.users WHERE id_user = %s", (user_id,))
        u = cur.fetchone()
        assert u is not None
        assert int(u.get('tickets_bonus') or 0) == 202
        cur.close()

    @pytest.mark.asyncio
    async def test_claim_endpoint_removed(self, clean_db, db_connection, auth_headers):
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) ON CONFLICT (wallet) DO NOTHING",
            (auth_headers["X-Wallet"], "TESTCODE")
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            r = await client.post("/api/predictions/claim/123", headers=auth_headers)
            assert r.status_code == 404
