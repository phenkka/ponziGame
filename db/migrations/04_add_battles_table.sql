-- Миграция 04: Добавление таблицы Battles
-- 
-- Описание: Создает таблицу для системы батлов (PVP)
-- Таблица Battles хранит историю и состояние батлов между пользователями

-- Create Battles table
create table if not exists Battles(
    id_battle bigserial primary key,
    id_user bigint not null references Users(id_user) on delete cascade,
    status text not null default 'searching' check (status in ('searching', 'card_selection', 'fighting', 'completed', 'cancelled')),
    user_cards jsonb not null default '[]',  -- [{id_card, quantity}, ...]
    bot_cards jsonb not null default '[]',    -- [{id_card, quantity}, ...]
    user_tickets int not null default 0,
    bot_tickets int not null default 0,
    winner text check (winner in ('user', 'bot')),
    started_at timestamp default now(),
    completed_at timestamp null
);

-- Create indexes for better performance
create index if not exists idx_battles_user on Battles(id_user);
create index if not exists idx_battles_status on Battles(status);
create index if not exists idx_battles_started_at on Battles(started_at);



