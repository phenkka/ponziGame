-- Миграция 07: Исключение бонусных паков из джекпота
-- 
-- Описание: Добавляет связь между картами и открытиями паков,
-- чтобы различать карты из купленных и бонусных паков.
-- Карты из бонусных паков не должны учитываться в tickets для джекпота.
--
-- ВАЖНО: Бонусные паки имеют tx_signature вида:
-- - 'prediction_reward_...' - награды за выигрыш в предсказаниях
-- - 'daily_checkin_...' - награды за ежедневный чекин

-- Добавляем поле id_opening в Card_User для связи с открытием пака
ALTER TABLE Card_User 
ADD COLUMN IF NOT EXISTS id_opening bigint REFERENCES Chest_openings(id_opening) ON DELETE SET NULL;

-- Создаем индекс для производительности
CREATE INDEX IF NOT EXISTS idx_card_user_id_opening ON Card_User(id_opening);

-- Комментарий к полю
COMMENT ON COLUMN Card_User.id_opening IS 'Связь с открытием пака. NULL для старых карт или карт без связи.';

