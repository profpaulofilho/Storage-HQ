create table if not exists users (
    id bigserial primary key,
    username text unique not null,
    password_hash text not null,
    is_admin integer default 0,
    created_at timestamp default current_timestamp
);

create table if not exists collections (
    id bigserial primary key,
    name text unique not null,
    cover_image text,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp
);

create table if not exists comics (
    id bigserial primary key,
    collection_id bigint not null references collections(id) on delete cascade,
    name text not null,
    edition_number text not null,
    is_special_edition integer default 0,
    publication_date text,
    publisher text,
    launch_value numeric(12,2),
    currency_type text,
    current_value numeric(12,2),
    cover_image text,
    synopsis text,
    collector_comments text,
    trivia text,
    created_at timestamp default current_timestamp,
    updated_at timestamp default current_timestamp,
    unique (collection_id, edition_number)
);
