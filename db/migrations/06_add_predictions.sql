-- Миграция 06: Добавление системы предсказаний (Predictions)
-- 
-- Описание: Создает систему для ставок на события из Polymarket
-- - Таблица Predictions - хранит пари из Polymarket API
-- - Таблица User_bets - хранит ставки пользователей на пари
--
-- ВАЖНО: Система использует UTC для всех временных меток.

-- Таблица для хранения пари из Polymarket
create table if not exists predictions(
    id_prediction bigserial primary key,
    polymarket_id text not null unique, -- ID из Polymarket API
    title text not null, -- Название пари
    description text, -- Описание
    category text, -- Категория (politics, sports, crypto, etc.)
    outcome_a text not null, -- Вариант A (например, "Yes")
    outcome_b text not null, -- Вариант B (например, "No")
    outcome_a_probability numeric(5,2), -- Вероятность исхода A (0-100)
    outcome_b_probability numeric(5,2), -- Вероятность исхода B (0-100)
    resolution_date timestamp, -- Дата разрешения пари
    status text not null default 'active' check (status in ('active', 'resolved', 'cancelled')),
    winner_outcome text check (winner_outcome in ('A', 'B', 'cancelled')), -- Победивший исход
    volume_24h numeric(20,2), -- Объем за 24 часа
    volume_7d numeric(20,2), -- Объем за 7 дней
    volume_30d numeric(20,2), -- Объем за 30 дней
    created_at timestamp default now(),
    updated_at timestamp default now()
);

-- Таблица для ставок пользователей
create table if not exists user_bets(
    id_bet bigserial primary key,
    id_user bigint not null references Users(id_user) on delete cascade,
    id_prediction bigint not null references predictions(id_prediction) on delete cascade,
    chosen_outcome text not null check (chosen_outcome in ('A', 'B')), -- Выбранный исход
    bet_amount numeric(20,2) not null default 0, -- Сумма ставки (может быть 0 для бесплатных ставок)
    status text not null default 'pending' check (status in ('pending', 'won', 'lost', 'cancelled')),
    reward_issued boolean default false, -- Была ли выдана награда
    reward_type text, -- Тип награды (broken_packs, common_pack, etc.)
    reward_data jsonb, -- Данные награды
    created_at timestamp default now(),
    resolved_at timestamp, -- Когда ставка была разрешена
    unique(id_user, id_prediction) -- Один пользователь может сделать только одну ставку на пари
);

-- Индексы для производительности
create index if not exists idx_predictions_status on predictions(status);
create index if not exists idx_predictions_polymarket_id on predictions(polymarket_id);
create index if not exists idx_predictions_resolution_date on predictions(resolution_date);
create index if not exists idx_predictions_category on predictions(category);
create index if not exists idx_user_bets_user on user_bets(id_user);
create index if not exists idx_user_bets_prediction on user_bets(id_prediction);
create index if not exists idx_user_bets_status on user_bets(status);
create index if not exists idx_user_bets_reward_issued on user_bets(reward_issued);
