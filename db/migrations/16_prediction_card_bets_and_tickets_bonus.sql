ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS tickets_bonus integer NOT NULL DEFAULT 0;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS bet_card_id int NULL REFERENCES public.cards(id_card) ON DELETE SET NULL;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS bet_card_quantity integer NOT NULL DEFAULT 0;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS bet_tickets integer NOT NULL DEFAULT 0;

ALTER TABLE public.user_bets
ADD COLUMN IF NOT EXISTS payout_tickets integer NULL;
