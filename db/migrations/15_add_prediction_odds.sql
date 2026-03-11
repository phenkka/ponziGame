alter table if exists predictions
    add column if not exists outcome_a_odds numeric(20,8),
    add column if not exists outcome_b_odds numeric(20,8);

create index if not exists idx_predictions_odds_a on predictions(outcome_a_odds);
create index if not exists idx_predictions_odds_b on predictions(outcome_b_odds);
