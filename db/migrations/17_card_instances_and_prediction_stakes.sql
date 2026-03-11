-- Migration 17: Card instances for prediction staking
--
-- Adds per-copy card instances with individual bounty. Used by Predictions bets:
-- - user stakes exactly one card instance
-- - on win: instance is returned + upgraded copy minted (bounty * odds)
-- - on loss: instance is burned
-- - on cancelled: instance is returned

CREATE TABLE IF NOT EXISTS public.card_user_instances (
    id_instance bigserial PRIMARY KEY,
    id_user bigint NOT NULL REFERENCES public.users(id_user) ON DELETE CASCADE,
    id_card int NOT NULL REFERENCES public.cards(id_card) ON DELETE CASCADE,
    bounty integer NOT NULL,
    status text NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'staked', 'burned')),
    created_at timestamp NOT NULL DEFAULT now(),
    updated_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_card_user_instances_user ON public.card_user_instances(id_user);
CREATE INDEX IF NOT EXISTS idx_card_user_instances_card ON public.card_user_instances(id_card);
CREATE INDEX IF NOT EXISTS idx_card_user_instances_status ON public.card_user_instances(status);

-- Store staked instance and odds snapshot
ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS bet_card_instance_id bigint NULL REFERENCES public.card_user_instances(id_instance) ON DELETE SET NULL;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS bet_bounty integer NULL;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS odds_at_bet numeric(20,8) NULL;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS minted_card_instance_id bigint NULL REFERENCES public.card_user_instances(id_instance) ON DELETE SET NULL;

-- Backfill instances from existing Card_User quantities (idempotent).
-- For each (id_user, id_card), create (quantity - existing_instances_count) new instances.
WITH inst_cnt AS (
    SELECT id_user, id_card, COUNT(*)::int AS cnt
    FROM public.card_user_instances
    GROUP BY id_user, id_card
)
INSERT INTO public.card_user_instances (id_user, id_card, bounty, status)
SELECT cu.id_user, cu.id_card, c.start_bounty, 'available'
FROM public.card_user cu
JOIN public.cards c ON c.id_card = cu.id_card
LEFT JOIN inst_cnt ic ON ic.id_user = cu.id_user AND ic.id_card = cu.id_card
CROSS JOIN LATERAL generate_series(1, GREATEST((cu.quantity - COALESCE(ic.cnt, 0)), 0)) AS gs(n)
WHERE cu.quantity > COALESCE(ic.cnt, 0)
  AND cu.quantity > 0;
