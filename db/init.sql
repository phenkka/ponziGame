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