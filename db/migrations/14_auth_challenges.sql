CREATE TABLE IF NOT EXISTS Auth_challenges (
    nonce text PRIMARY KEY,
    wallet text NOT NULL,
    message text NOT NULL,
    created_at timestamp NOT NULL DEFAULT now(),
    expires_at timestamp NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_auth_challenges_wallet ON Auth_challenges(wallet);
CREATE INDEX IF NOT EXISTS idx_auth_challenges_expires_at ON Auth_challenges(expires_at);
