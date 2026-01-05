-- Миграция 10: учет реферальных начислений
--
-- Храним начисления рефереру за покупки приглашенного пользователя.

CREATE TABLE IF NOT EXISTS Referral_rewards (
    id_reward bigserial PRIMARY KEY,
    id_referrer bigint NOT NULL REFERENCES Users(id_user) ON DELETE CASCADE,
    id_referred bigint NOT NULL REFERENCES Users(id_user) ON DELETE CASCADE,
    id_purchase bigint NOT NULL REFERENCES Chest_purchases(id_purchase) ON DELETE CASCADE,
    amount numeric(20,8) NOT NULL,
    created_at timestamp NOT NULL DEFAULT now(),
    UNIQUE(id_purchase)
);

CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON Referral_rewards(id_referrer);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_referred ON Referral_rewards(id_referred);
