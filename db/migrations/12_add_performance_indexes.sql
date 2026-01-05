CREATE INDEX IF NOT EXISTS idx_referral_system_referrer
ON Referral_system(id_referrer);

CREATE INDEX IF NOT EXISTS idx_chest_purchases_user_created_at
ON Chest_purchases(id_user, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chest_openings_purchase
ON Chest_openings(id_purchase);

CREATE INDEX IF NOT EXISTS idx_card_trades_user_created_at
ON Card_trades(id_user, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_jackpot_rounds_status_id_round
ON Jackpot_rounds(status, id_round DESC);

CREATE INDEX IF NOT EXISTS idx_jackpot_rounds_status_ends_at
ON Jackpot_rounds(status, ends_at);

CREATE INDEX IF NOT EXISTS idx_jackpot_rounds_status_completed_at
ON Jackpot_rounds(status, completed_at DESC);

CREATE INDEX IF NOT EXISTS idx_jackpot_tickets_snapshot_round_tickets
ON Jackpot_tickets_snapshot(id_round, tickets_count);

CREATE INDEX IF NOT EXISTS idx_super_jackpot_rounds_winner
ON Super_jackpot_rounds(winner_user_id);

CREATE INDEX IF NOT EXISTS idx_predictions_status_resolution_date
ON predictions(status, resolution_date);

CREATE INDEX IF NOT EXISTS idx_user_bets_user_created_at
ON user_bets(id_user, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_bets_prediction_status
ON user_bets(id_prediction, status);
