-- Миграция 09: Отметка о том, что награда за выигранное пари была забрана
--
-- Описание: добавляет поля reward_claimed и reward_claimed_at в таблицу user_bets.

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS reward_claimed boolean NOT NULL DEFAULT false;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS reward_claimed_at timestamp NULL;
