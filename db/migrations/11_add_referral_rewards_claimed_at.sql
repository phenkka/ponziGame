ALTER TABLE public.Referral_rewards
ADD COLUMN IF NOT EXISTS claimed_at timestamp NULL;

CREATE INDEX IF NOT EXISTS idx_referral_rewards_claimed_at
ON public.Referral_rewards(id_referrer, claimed_at);
