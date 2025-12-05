-- Миграция 05: Добавление системы ежедневного чекина
-- 
-- Описание: Создает систему ежедневного чекина с наградами
-- - Таблица Daily_codes - ежедневные коды на год вперед
-- - Таблица Daily_checkins - история чекинов пользователей
-- - Таблица User_boost - персональные бонусы (увеличение шансов)
--
-- ВАЖНО: Система использует UTC для определения "сегодняшней" даты.
-- Все коды генерируются и проверяются относительно UTC времени.
-- Это обеспечивает единообразие для пользователей из разных часовых поясов.
--
-- После применения этой миграции необходимо сгенерировать коды:
-- python3 db/utils/generate_daily_codes.py

-- Таблица с ежедневными кодами на год вперед
create table if not exists Daily_codes(
    id_code serial primary key,
    code_date date not null unique,
    daily_code text not null unique,
    created_at timestamp default now()
);

-- Таблица для отслеживания чекинов пользователей
create table if not exists Daily_checkins(
    id_checkin bigserial primary key,
    id_user bigint not null references Users(id_user) on delete cascade,
    checkin_date date not null,
    daily_code text not null,
    consecutive_days int not null default 1,
    reward_type text,
    reward_data jsonb,
    created_at timestamp default now(),
    unique(id_user, checkin_date)
);

-- Таблица для персональных бонусов (увеличение шансов)
create table if not exists User_boost(
    id_boost bigserial primary key,
    id_user bigint not null references Users(id_user) on delete cascade,
    boost_type text not null default 'legendary_chance',
    boost_value numeric(5,2) not null default 10.0, -- процент увеличения
    expires_at timestamp not null,
    created_at timestamp default now(),
    is_active boolean default true
);

create index if not exists idx_daily_codes_date on Daily_codes(code_date);
create index if not exists idx_daily_checkins_user on Daily_checkins(id_user);
create index if not exists idx_daily_checkins_date on Daily_checkins(checkin_date);
create index if not exists idx_user_boost_user on User_boost(id_user);
create index if not exists idx_user_boost_active on User_boost(is_active, expires_at);
