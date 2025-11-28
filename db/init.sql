create table if not exists Users(
    id_user bigserial primary key,
    wallet text not null unique,
    ref_code text not null unique,
    create_at date default current_date
);

create table if not exists Referral_system(
    id_sys bigserial primary key,
    id_referrer bigint not null references Users(id_user) on delete cascade,
    id_referred bigint not null unique references Users(id_user) on delete cascade,
    created_at date default current_date,
    constraint referral_no_self check (id_referrer <> id_referred)
);

create table if not exists Cards(
    id_card serial primary key,
    rarity text not null
        check (rarity in ('basic', 'rare', 'epic', 'legendary')),
    start_bounty int not null,
    update_time date default current_date,
    name text,
    image_url text,
    image_key text unique
);

create table if not exists Card_User(
    id_user bigint not null references Users(id_user) on delete cascade,
    id_card int not null references Cards(id_card) on delete cascade,
    quantity integer not null default 1,
    created_at date default current_date,
    primary key (id_user, id_card)
);

create table if not exists Chests(
    id_chest serial primary key,
    prob_common int not null,
    prob_rare int not null,
    prob_epic int not null,
    prob_legendary int not null,
    chance_loss int not null default 0,
    price decimal(10, 2) not null,
    update_time date default current_date,
    constraint ch_prob_check_with_loss
    check (prob_common >= 0 and prob_rare >= 0 and prob_epic >= 0 and prob_legendary >= 0 and chance_loss >= 0
           and prob_common + prob_rare + prob_epic + prob_legendary + chance_loss = 100)
);

create table if not exists Chest_purchases(
    id_purchase bigserial primary key,
    id_user bigint not null references Users(id_user),
    id_chest int not null references Chests(id_chest),
    tx_signature text not null unique,
    created_at timestamp not null default now(),
    is_opened boolean not null default false,
    opened_at timestamp null
);

create table if not exists Chest_openings(
    id_opening bigserial primary key,
    id_purchase bigint references Chest_purchases(id_purchase) on delete set null,
    id_user bigint not null references Users(id_user),
    id_chest int not null references Chests(id_chest),
    opened_at date default current_date
);

create table if not exists Jackpot_rounds(
    id_round serial primary key,
    started_at timestamp default now(),
    ends_at timestamp not null,
    winner_user_id bigint null references Users(id_user),
    prize numeric(20,8) null,
    status text default 'active' check (status in ('active', 'completed')),
    completed_at timestamp null,
    prize_amount numeric(20,8) null,
    total_amount numeric(20,8) default 0 not null,
    tickets_snapshot jsonb null  -- Снимок tickets на момент окончания раунда
);

create table if not exists Jackpot_tickets_snapshot(
    id_snapshot bigserial primary key,
    id_round int not null references Jackpot_rounds(id_round) on delete cascade,
    id_user bigint not null references Users(id_user) on delete cascade,
    tickets_count int not null,
    created_at timestamp default now(),
    unique(id_round, id_user)
);

create table if not exists Card_trades(
    id_trade bigserial primary key,
    id_user bigint not null references Users(id_user) on delete cascade,
    traded_cards jsonb not null,  -- Массив обмениваемых карт: [{"id_card": 1, "quantity": 4}, ...]
    received_card_id int not null references Cards(id_card) on delete cascade,
    rarity text not null check (rarity in ('basic', 'rare', 'epic')),
    created_at timestamp default now()
);