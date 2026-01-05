import pytest
from httpx import AsyncClient
from main import app
from psycopg2.extras import RealDictCursor


class TestReferralRewardsAPI:
    @pytest.mark.asyncio
    async def test_get_referral_rewards_returns_total_and_entries(self, clean_db, db_connection, auth_headers_for_wallet):
        if db_connection is None:
            pytest.skip("Database not available")

        headers, wallet = auth_headers_for_wallet("REFERRER_WALLET_TEST")

        # Создаем referrer и делаем валидные auth headers именно для его wallet
        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "REFERRER_CODE")
        )
        referrer = cursor.fetchone()
        referrer_id = referrer["id_user"]

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            ("REFERRED_WALLET_TEST", "REFERRED_CODE")
        )
        referred = cursor.fetchone()
        referred_id = referred["id_user"]

        cursor.execute(
            "INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss) VALUES (100, 50, 30, 15, 5, 0) RETURNING id_chest"
        )
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute(
            "INSERT INTO Chest_purchases (id_user, id_chest, tx_signature) VALUES (%s, %s, %s) RETURNING id_purchase",
            (referred_id, chest_id, "tx_sig_ref_1")
        )
        purchase_id = cursor.fetchone()["id_purchase"]

        cursor.execute(
            "INSERT INTO Referral_system (id_referrer, id_referred) VALUES (%s, %s)",
            (referrer_id, referred_id)
        )

        cursor.execute(
            "INSERT INTO Referral_rewards (id_referrer, id_referred, id_purchase, amount) VALUES (%s, %s, %s, %s)",
            (referrer_id, referred_id, purchase_id, 10)
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(f"/api/referral/rewards/{wallet}", headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["totalEarned"] == 10.0
            assert data["availableToClaim"] == 10.0
            assert data["count"] == 1
            assert isinstance(data["rewards"], list)
            assert data["rewards"][0]["id_purchase"] == purchase_id
            assert data["rewards"][0]["referred_wallet"] == "REFERRED_WALLET_TEST"
            assert data["rewards"][0]["amount"] == 10.0

    @pytest.mark.asyncio
    async def test_claim_referral_rewards_marks_as_claimed(self, clean_db, db_connection, auth_headers_for_wallet):
        if db_connection is None:
            pytest.skip("Database not available")

        headers, wallet = auth_headers_for_wallet("REFERRER_WALLET_TEST")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            (wallet, "REFERRER_CODE")
        )
        referrer_id = cursor.fetchone()["id_user"]

        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s) RETURNING id_user",
            ("REFERRED_WALLET_TEST", "REFERRED_CODE")
        )
        referred_id = cursor.fetchone()["id_user"]

        cursor.execute(
            "INSERT INTO Chests (price, prob_common, prob_rare, prob_epic, prob_legendary, chance_loss) VALUES (100, 50, 30, 15, 5, 0) RETURNING id_chest"
        )
        chest_id = cursor.fetchone()["id_chest"]

        cursor.execute(
            "INSERT INTO Chest_purchases (id_user, id_chest, tx_signature) VALUES (%s, %s, %s) RETURNING id_purchase",
            (referred_id, chest_id, "tx_sig_ref_1")
        )
        purchase_id_1 = cursor.fetchone()["id_purchase"]

        cursor.execute(
            "INSERT INTO Chest_purchases (id_user, id_chest, tx_signature) VALUES (%s, %s, %s) RETURNING id_purchase",
            (referred_id, chest_id, "tx_sig_ref_2")
        )
        purchase_id_2 = cursor.fetchone()["id_purchase"]

        cursor.execute(
            "INSERT INTO Referral_system (id_referrer, id_referred) VALUES (%s, %s)",
            (referrer_id, referred_id)
        )

        cursor.execute(
            "INSERT INTO Referral_rewards (id_referrer, id_referred, id_purchase, amount) VALUES (%s, %s, %s, %s)",
            (referrer_id, referred_id, purchase_id_1, 10)
        )
        cursor.execute(
            "INSERT INTO Referral_rewards (id_referrer, id_referred, id_purchase, amount) VALUES (%s, %s, %s, %s)",
            (referrer_id, referred_id, purchase_id_2, 5)
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            claim_resp = await client.post(f"/api/referral/claim/{wallet}", headers=headers)
            assert claim_resp.status_code == 200
            claim_data = claim_resp.json()
            assert claim_data["success"] is True
            assert claim_data["claimed"] == 15.0

            rewards_resp = await client.get(f"/api/referral/rewards/{wallet}", headers=headers)
            assert rewards_resp.status_code == 200
            rewards_data = rewards_resp.json()
            assert rewards_data["success"] is True
            assert rewards_data["totalEarned"] == 15.0
            assert rewards_data["availableToClaim"] == 0.0

            claim_again_resp = await client.post(f"/api/referral/claim/{wallet}", headers=headers)
            assert claim_again_resp.status_code == 200
            claim_again_data = claim_again_resp.json()
            assert claim_again_data["success"] is True
            assert claim_again_data["claimed"] == 0.0

    @pytest.mark.asyncio
    async def test_get_referral_rewards_requires_auth(self, clean_db, db_connection):
        if db_connection is None:
            pytest.skip("Database not available")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            ("REFERRER_WALLET_TEST", "REFERRER_CODE")
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get("/api/referral/rewards/REFERRER_WALLET_TEST")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_referral_rewards_denies_other_wallet(self, clean_db, db_connection, auth_headers_for_wallet):
        if db_connection is None:
            pytest.skip("Database not available")

        ref_headers, ref_wallet = auth_headers_for_wallet("REFERRER_WALLET_TEST")
        other_headers, other_wallet = auth_headers_for_wallet("OTHER_WALLET_TEST")

        cursor = db_connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (ref_wallet, "REFERRER_CODE")
        )
        cursor.execute(
            "INSERT INTO Users (wallet, ref_code) VALUES (%s, %s)",
            (other_wallet, "OTHER_CODE")
        )
        db_connection.commit()
        cursor.close()

        async with AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(f"/api/referral/rewards/{ref_wallet}", headers=other_headers)
            assert resp.status_code == 403
