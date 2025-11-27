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
    image_key text
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
