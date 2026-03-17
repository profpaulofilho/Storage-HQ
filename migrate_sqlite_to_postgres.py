import os
import sqlite3

import psycopg

SQLITE_PATH = os.getenv('SQLITE_PATH', 'testdata/storage_hqs.db')
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise SystemExit('Defina DATABASE_URL antes de executar a migração.')

sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row
pg_conn = psycopg.connect(DATABASE_URL)

with pg_conn:
    with pg_conn.cursor() as cur:
        users = sqlite_conn.execute('SELECT username, password_hash, is_admin, created_at FROM users').fetchall()
        for row in users:
            cur.execute(
                '''
                INSERT INTO users (username, password_hash, is_admin, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (username) DO NOTHING
                ''',
                (row['username'], row['password_hash'], row['is_admin'], row['created_at']),
            )

        collections = sqlite_conn.execute('SELECT id, name, cover_image, created_at, updated_at FROM collections ORDER BY id').fetchall()
        for row in collections:
            cur.execute(
                '''
                INSERT INTO collections (id, name, cover_image, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                ''',
                (row['id'], row['name'], row['cover_image'], row['created_at'], row['updated_at']),
            )

        comics = sqlite_conn.execute(
            '''
            SELECT id, collection_id, name, edition_number, is_special_edition, publication_date,
                   publisher, launch_value, currency_type, current_value, cover_image,
                   synopsis, collector_comments, trivia, created_at, updated_at
            FROM comics ORDER BY id
            '''
        ).fetchall()
        for row in comics:
            cur.execute(
                '''
                INSERT INTO comics (
                    id, collection_id, name, edition_number, is_special_edition, publication_date,
                    publisher, launch_value, currency_type, current_value, cover_image,
                    synopsis, collector_comments, trivia, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                ''',
                tuple(row),
            )

        cur.execute("SELECT setval(pg_get_serial_sequence('collections', 'id'), coalesce((SELECT max(id) FROM collections), 1), true)")
        cur.execute("SELECT setval(pg_get_serial_sequence('comics', 'id'), coalesce((SELECT max(id) FROM comics), 1), true)")
        cur.execute("SELECT setval(pg_get_serial_sequence('users', 'id'), coalesce((SELECT max(id) FROM users), 1), true)")

print('Migração concluída.')
