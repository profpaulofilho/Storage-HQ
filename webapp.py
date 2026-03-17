from __future__ import annotations

import os
import re
import sqlite3
from functools import wraps
from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

from drive_service import (
    drive_enabled,
    get_authorization_url,
    save_credentials_from_response,
    upload_bytes_to_drive,
)
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    psycopg = None
    dict_row = None

APP_TITLE = 'Storage HQs - Biblioteca de Attilan'
DEFAULT_ADMIN_USERNAME = 'STAN_ADM'
DEFAULT_ADMIN_PASSWORD = 'Excelsior'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
LEGACY_SHA256_LEN = 64
PLACEHOLDER_IMAGE = 'images/placeholder-cover.svg'

SQLITE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    cover_image TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    edition_number TEXT NOT NULL,
    is_special_edition INTEGER DEFAULT 0,
    publication_date TEXT,
    publisher TEXT,
    launch_value REAL,
    currency_type TEXT,
    current_value REAL,
    cover_image TEXT,
    synopsis TEXT,
    collector_comments TEXT,
    trivia TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (collection_id) REFERENCES collections (id) ON DELETE CASCADE,
    UNIQUE (collection_id, edition_number)
);
'''

POSTGRES_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collections (
    id BIGSERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    cover_image TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comics (
    id BIGSERIAL PRIMARY KEY,
    collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    edition_number TEXT NOT NULL,
    is_special_edition INTEGER DEFAULT 0,
    publication_date TEXT,
    publisher TEXT,
    launch_value NUMERIC(12,2),
    currency_type TEXT,
    current_value NUMERIC(12,2),
    cover_image TEXT,
    synopsis TEXT,
    collector_comments TEXT,
    trivia TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (collection_id, edition_number)
);
'''


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me-in-production')
    data_dir = Path(os.getenv('DATA_DIR', '/var/data'))
    data_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir = data_dir / 'uploads'
    uploads_dir.mkdir(parents=True, exist_ok=True)

    database_url = os.getenv('DATABASE_URL', '').strip()
    use_postgres = bool(database_url)

    app.config.update(
        DATA_DIR=data_dir,
        UPLOAD_FOLDER=uploads_dir,
        DATABASE=str(data_dir / 'storage_hqs.db'),
        DATABASE_URL=database_url,
        DB_BACKEND='postgres' if use_postgres else 'sqlite',
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        APP_TITLE=APP_TITLE,
        PLACEHOLDER_IMAGE=PLACEHOLDER_IMAGE,
    )

    register_filters(app)
    register_hooks(app)
    register_routes(app)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_database()

    return app


def register_filters(app: Flask):
    @app.template_filter('currency')
    def currency_filter(value, symbol='R$'):
        if value in (None, ''):
            return '—'
        try:
            return f'{symbol} {float(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        except (ValueError, TypeError):
            return value


# -------------------- database helpers --------------------

def using_postgres() -> bool:
    return current_app.config.get('DB_BACKEND') == 'postgres'


def translate_query(query: str) -> str:
    if using_postgres():
        return query
    return query.replace('%s', '?')


def get_db():
    if 'db' in g:
        return g.db

    if using_postgres():
        if psycopg is None:
            raise RuntimeError('psycopg não instalado. Adicione psycopg[binary] ao requirements.txt.')
        g.db = psycopg.connect(current_app.config['DATABASE_URL'], row_factory=dict_row)
    else:
        g.db = sqlite3.connect(current_app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def db_execute(query: str, params=()):
    db = get_db()
    return db.execute(translate_query(query), params)


def close_db(_exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def commit_db():
    get_db().commit()


def init_database():
    if using_postgres():
        if psycopg is None:
            raise RuntimeError('psycopg não instalado. Adicione psycopg[binary] ao requirements.txt.')
        with psycopg.connect(current_app.config['DATABASE_URL'], row_factory=dict_row) as db:
            with db.cursor() as cur:
                cur.execute(POSTGRES_SCHEMA)
            ensure_default_admin(db)
            db.commit()
    else:
        db = sqlite3.connect(current_app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            db.executescript(SQLITE_SCHEMA)
            ensure_default_admin(db)
            db.commit()
        finally:
            db.close()


def ensure_default_admin(db):
    admin_username = os.getenv('DEFAULT_ADMIN_USERNAME', DEFAULT_ADMIN_USERNAME)
    admin_password = os.getenv('DEFAULT_ADMIN_PASSWORD', DEFAULT_ADMIN_PASSWORD)
    existing = db.execute(translate_query('SELECT 1 FROM users WHERE username = %s'), (admin_username,)).fetchone()
    if not existing:
        db.execute(
            translate_query('INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, 1)'),
            (admin_username, generate_password_hash(admin_password)),
        )


def fetch_one(query, params=()):
    row = db_execute(query, params).fetchone()
    return dict(row) if row else None


def fetch_all(query, params=()):
    rows = db_execute(query, params).fetchall()
    return [dict(row) for row in rows]


def integrity_error(exc: Exception) -> bool:
    candidates = [sqlite3.IntegrityError]
    if psycopg is not None:
        candidates.append(psycopg.IntegrityError)
    return isinstance(exc, tuple(candidates))


# -------------------- media helpers --------------------

def normalize_image_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith('uploads/'):
        return value
    parsed = urlparse(value)
    if parsed.scheme in ('http', 'https'):
        if 'drive.google.com' in parsed.netloc:
            match = re.search(r'/file/d/([^/]+)', parsed.path)
            if match:
                return f'https://drive.google.com/uc?id={match.group(1)}'
            query_id = parse_qs(parsed.query).get('id')
            if query_id:
                return f'https://drive.google.com/uc?id={query_id[0]}'
        return value
    return value


def media_src(value: str | None) -> str:
    normalized = normalize_image_url(value)
    if not normalized:
        return url_for('static', filename=current_app.config['PLACEHOLDER_IMAGE'])
    if normalized.startswith('http://') or normalized.startswith('https://'):
        return normalized
    if normalized.startswith('uploads/'):
        return url_for('uploaded_file', filename=normalized)
    return url_for('static', filename=normalized)


def allowed_file(filename: str):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload_or_url(file_storage, image_url: str | None, subdir='misc'):
    image_url = normalize_image_url(image_url)
    if image_url:
        return image_url
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError('Tipo de arquivo não suportado. Envie PNG, JPG, GIF, WEBP ou BMP.')

    filename = secure_filename(file_storage.filename)
    extension = filename.rsplit('.', 1)[1].lower()
    unique_name = f'{uuid4().hex}.{extension}'

    if drive_enabled():
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
        if not folder_id:
            raise ValueError('GOOGLE_DRIVE_FOLDER_ID não configurado no ambiente.')
        try:
            uploaded = upload_bytes_to_drive(file_storage, unique_name, folder_id)
            return f"https://drive.google.com/uc?id={uploaded['id']}"
        except Exception as exc:
            raise ValueError(f'Erro ao enviar imagem para o Google Drive: {exc}')

    target_dir = current_app.config['UPLOAD_FOLDER'] / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    absolute_path = target_dir / unique_name
    file_storage.save(absolute_path)
    return f'uploads/{subdir}/{unique_name}'


def delete_file(relative_path: str | None):
    relative_path = normalize_image_url(relative_path)
    if not relative_path or relative_path.startswith('http'):
        return
    absolute = current_app.config['DATA_DIR'] / relative_path
    if absolute.exists() and absolute.is_file():
        absolute.unlink(missing_ok=True)


# -------------------- app hooks and routes --------------------

def register_hooks(app: Flask):
    @app.before_request
    def load_logged_in_user():
        user_id = session.get('user_id')
        g.user = None
        if user_id:
            g.user = get_user_by_id(user_id)

    @app.context_processor
    def inject_globals():
        stats = None
        try:
            stats = get_stats()
        except Exception:
            stats = None
        return {
            'app_title': app.config['APP_TITLE'],
            'current_user': g.get('user'),
            'stats': stats,
            'media_src': media_src,
        }


def register_routes(app: Flask):
    @app.get('/')
    def index():
        return render_template('index.html', collections=get_collections(), stats=get_stats())

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = authenticate_user(username, password)
            if user:
                session.clear()
                session['user_id'] = user['id']
                flash('Login realizado com sucesso.', 'success')
                return redirect(url_for('dashboard'))
            flash('Nome de usuário ou senha inválidos.', 'danger')
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        flash('Sessão encerrada.', 'info')
        return redirect(url_for('index'))

    @app.get('/dashboard')
    @login_required
    def dashboard():
        return render_template(
            'dashboard.html',
            collections=get_collections(with_counts=True),
            stats=get_stats(),
            recent_comics=get_recent_comics(limit=8),
        )

    @app.route('/admin/users/new', methods=['GET', 'POST'])
    @admin_required
    def admin_user_new():
        if request.method == 'POST':
            result = add_admin_user(
                request.form.get('username', '').strip(),
                request.form.get('password', ''),
            )
            flash(result['message'], 'success' if result['success'] else 'danger')
            if result['success']:
                return redirect(url_for('dashboard'))
        return render_template('admin_user_form.html')

    @app.get('/collections/<int:collection_id>')
    def collection_detail(collection_id: int):
        collection = get_collection(collection_id)
        if not collection:
            abort(404)
        return render_template('collection_detail.html', collection=collection, comics=get_comics_by_collection(collection_id))

    @app.route('/collections/new', methods=['GET', 'POST'])
    @admin_required
    def collection_new():
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            cover = None
            try:
                cover = save_upload_or_url(
                    request.files.get('cover_image'),
                    request.form.get('cover_image_url', ''),
                    subdir='collections',
                )
            except ValueError as exc:
                flash(str(exc), 'danger')
                return render_template('collection_form.html', collection=None)
            if not name:
                flash('Informe o nome da coleção.', 'danger')
            else:
                try:
                    collection_id = add_collection(name, cover)
                    flash('Coleção criada com sucesso.', 'success')
                    return redirect(url_for('collection_detail', collection_id=collection_id))
                except Exception as exc:
                    if integrity_error(exc):
                        if cover:
                            delete_file(cover)
                        flash('Já existe uma coleção com esse nome.', 'danger')
                    else:
                        raise
        return render_template('collection_form.html', collection=None)

    @app.route('/collections/<int:collection_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def collection_edit(collection_id: int):
        collection = get_collection(collection_id)
        if not collection:
            abort(404)
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            try:
                new_cover = save_upload_or_url(
                    request.files.get('cover_image'),
                    request.form.get('cover_image_url', ''),
                    subdir='collections',
                )
            except ValueError as exc:
                flash(str(exc), 'danger')
                return render_template('collection_form.html', collection=collection)
            if not name:
                flash('Informe o nome da coleção.', 'danger')
            else:
                cover_path = new_cover or collection['cover_image']
                try:
                    update_collection(collection_id, name, cover_path)
                    if new_cover and collection['cover_image'] and collection['cover_image'] != new_cover:
                        delete_file(collection['cover_image'])
                    flash('Coleção atualizada.', 'success')
                    return redirect(url_for('collection_detail', collection_id=collection_id))
                except Exception as exc:
                    if integrity_error(exc):
                        if new_cover:
                            delete_file(new_cover)
                        flash('Já existe uma coleção com esse nome.', 'danger')
                    else:
                        raise
        return render_template('collection_form.html', collection=collection)

    @app.post('/collections/<int:collection_id>/delete')
    @admin_required
    def collection_delete(collection_id: int):
        collection = get_collection(collection_id)
        if not collection:
            abort(404)
        for comic in get_comics_by_collection(collection_id):
            delete_file(comic.get('cover_image'))
        delete_file(collection.get('cover_image'))
        delete_collection(collection_id)
        flash('Coleção removida.', 'info')
        return redirect(url_for('dashboard'))

    @app.get('/comics/<int:comic_id>')
    def comic_detail(comic_id: int):
        comic = get_comic(comic_id)
        if not comic:
            abort(404)
        return render_template('comic_detail.html', comic=comic, collection=get_collection(comic['collection_id']))

    @app.route('/collections/<int:collection_id>/comics/new', methods=['GET', 'POST'])
    @admin_required
    def comic_new(collection_id: int):
        collection = get_collection(collection_id)
        if not collection:
            abort(404)
        if request.method == 'POST':
            try:
                data = parse_comic_form(request)
                data['cover_image'] = save_upload_or_url(
                    request.files.get('cover_image'),
                    request.form.get('cover_image_url', ''),
                    subdir='comics',
                )
            except ValueError as exc:
                flash(str(exc), 'danger')
                return render_template('comic_form.html', comic=None, collection=collection)
            if not data['name'] or not data['edition_number']:
                flash('Nome e número da edição são obrigatórios.', 'danger')
            else:
                try:
                    comic_id = add_comic(collection_id=collection_id, **data)
                    flash('HQ cadastrada com sucesso.', 'success')
                    return redirect(url_for('comic_detail', comic_id=comic_id))
                except Exception as exc:
                    if integrity_error(exc):
                        delete_file(data.get('cover_image'))
                        flash('Já existe uma HQ com esse número de edição nesta coleção.', 'danger')
                    else:
                        raise
        return render_template('comic_form.html', comic=None, collection=collection)

    @app.route('/comics/<int:comic_id>/edit', methods=['GET', 'POST'])
    @admin_required
    def comic_edit(comic_id: int):
        comic = get_comic(comic_id)
        if not comic:
            abort(404)
        collection = get_collection(comic['collection_id'])
        if request.method == 'POST':
            try:
                data = parse_comic_form(request)
                new_cover = save_upload_or_url(
                    request.files.get('cover_image'),
                    request.form.get('cover_image_url', ''),
                    subdir='comics',
                )
            except ValueError as exc:
                flash(str(exc), 'danger')
                return render_template('comic_form.html', comic=comic, collection=collection)
            data['cover_image'] = new_cover or comic['cover_image']
            if not data['name'] or not data['edition_number']:
                flash('Nome e número da edição são obrigatórios.', 'danger')
            else:
                try:
                    update_comic(comic_id, **data)
                    if new_cover and comic['cover_image'] and comic['cover_image'] != new_cover:
                        delete_file(comic['cover_image'])
                    flash('HQ atualizada.', 'success')
                    return redirect(url_for('comic_detail', comic_id=comic_id))
                except Exception as exc:
                    if integrity_error(exc):
                        delete_file(new_cover)
                        flash('Já existe uma HQ com esse número de edição nesta coleção.', 'danger')
                    else:
                        raise
        return render_template('comic_form.html', comic=comic, collection=collection)

    @app.post('/comics/<int:comic_id>/delete')
    @admin_required
    def comic_delete(comic_id: int):
        comic = get_comic(comic_id)
        if not comic:
            abort(404)
        collection_id = comic['collection_id']
        delete_file(comic.get('cover_image'))
        delete_comic(comic_id)
        flash('HQ removida.', 'info')
        return redirect(url_for('collection_detail', collection_id=collection_id))

    @app.get('/uploads/<path:filename>')
    def uploaded_file(filename):
        return send_from_directory(current_app.config['DATA_DIR'], filename)

    @app.get('/google-drive/connect')
    @admin_required
    def google_drive_connect():
        auth_url, state, code_verifier = get_authorization_url()
        session['google_oauth_state'] = state
        session['google_oauth_code_verifier'] = code_verifier
        return redirect(auth_url)

    @app.get('/oauth2callback')
    @admin_required
    def oauth2callback():
        state = session.get('google_oauth_state')
        code_verifier = session.get('google_oauth_code_verifier')
        if not state or not code_verifier:
            flash('Estado OAuth não encontrado. Tente conectar novamente.', 'danger')
            return redirect(url_for('dashboard'))
        try:
            save_credentials_from_response(state, code_verifier, request.url)
            session.pop('google_oauth_state', None)
            session.pop('google_oauth_code_verifier', None)
            flash('Google Drive conectado com sucesso.', 'success')
        except Exception as exc:
            flash(f'Erro ao conectar Google Drive: {exc}', 'danger')
        return redirect(url_for('dashboard'))

    @app.get('/healthz')
    def healthz():
        return {'status': 'ok', 'db_backend': current_app.config['DB_BACKEND']}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash('Faça login para continuar.', 'warning')
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash('Faça login para continuar.', 'warning')
            return redirect(url_for('login'))
        if not g.user['is_admin']:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# -------------------- business logic --------------------
def authenticate_user(username: str, password: str):
    user = fetch_one('SELECT * FROM users WHERE username = %s', (username,))
    if not user:
        return None
    stored = user['password_hash']
    ok = False
    if stored.startswith('pbkdf2:') or stored.startswith('scrypt:'):
        ok = check_password_hash(stored, password)
    elif len(stored) == LEGACY_SHA256_LEN:
        import hashlib
        ok = hashlib.sha256(password.encode()).hexdigest() == stored
        if ok:
            db_execute('UPDATE users SET password_hash = %s WHERE id = %s', (generate_password_hash(password), user['id']))
            commit_db()
    if not ok:
        return None
    return user


def get_user_by_id(user_id: int | None):
    if not user_id:
        return None
    return fetch_one('SELECT * FROM users WHERE id = %s', (user_id,))


def get_admin_count():
    row = fetch_one('SELECT COUNT(*) AS total FROM users WHERE is_admin = 1')
    return int(row['total']) if row else 0


def add_admin_user(username: str, password: str):
    if not username or not password:
        return {'success': False, 'message': 'Preencha usuário e senha.'}
    if len(password) < 8:
        return {'success': False, 'message': 'A senha deve ter pelo menos 8 caracteres.'}
    if get_admin_count() >= 2:
        return {'success': False, 'message': 'Limite de 2 usuários administradores atingido.'}
    try:
        db_execute(
            'INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, 1)',
            (username, generate_password_hash(password)),
        )
        commit_db()
        return {'success': True, 'message': 'Usuário administrador criado com sucesso.'}
    except Exception as exc:
        if integrity_error(exc):
            return {'success': False, 'message': 'Nome de usuário já existe.'}
        raise


def get_collections(with_counts=False):
    if with_counts:
        return fetch_all(
            '''
            SELECT c.*, COUNT(cm.id) AS comic_count
            FROM collections c
            LEFT JOIN comics cm ON cm.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.name
            '''
        )
    return fetch_all('SELECT * FROM collections ORDER BY name')


def get_collection(collection_id: int):
    return fetch_one('SELECT * FROM collections WHERE id = %s', (collection_id,))


def add_collection(name: str, cover_image: str | None):
    cur = db_execute('INSERT INTO collections (name, cover_image) VALUES (%s, %s)', (name, cover_image))
    collection_id = cur.lastrowid if hasattr(cur, 'lastrowid') else None
    if collection_id is None:
        row = fetch_one('SELECT id FROM collections WHERE name = %s', (name,))
        collection_id = row['id'] if row else None
    commit_db()
    return collection_id


def update_collection(collection_id: int, name: str, cover_image: str | None):
    db_execute(
        'UPDATE collections SET name = %s, cover_image = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s',
        (name, cover_image, collection_id),
    )
    commit_db()


def delete_collection(collection_id: int):
    db_execute('DELETE FROM collections WHERE id = %s', (collection_id,))
    commit_db()


def get_comics_by_collection(collection_id: int):
    return fetch_all(
        '''
        SELECT * FROM comics
        WHERE collection_id = %s
        ORDER BY edition_number
        ''',
        (collection_id,),
    )


def get_comic(comic_id: int):
    return fetch_one('SELECT * FROM comics WHERE id = %s', (comic_id,))


def add_comic(**kwargs):
    cur = db_execute(
        '''
        INSERT INTO comics (
            collection_id, name, edition_number, is_special_edition,
            publication_date, publisher, launch_value, currency_type,
            current_value, cover_image, synopsis, collector_comments, trivia
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''',
        (
            kwargs['collection_id'], kwargs['name'], kwargs['edition_number'], kwargs['is_special_edition'],
            kwargs['publication_date'], kwargs['publisher'], kwargs['launch_value'], kwargs['currency_type'],
            kwargs['current_value'], kwargs['cover_image'], kwargs['synopsis'], kwargs['collector_comments'], kwargs['trivia'],
        ),
    )
    comic_id = cur.lastrowid if hasattr(cur, 'lastrowid') else None
    if comic_id is None:
        row = fetch_one(
            'SELECT id FROM comics WHERE collection_id = %s AND edition_number = %s',
            (kwargs['collection_id'], kwargs['edition_number']),
        )
        comic_id = row['id'] if row else None
    commit_db()
    return comic_id


def update_comic(comic_id: int, **kwargs):
    db_execute(
        '''
        UPDATE comics SET
            name = %s, edition_number = %s, is_special_edition = %s, publication_date = %s,
            publisher = %s, launch_value = %s, currency_type = %s, current_value = %s,
            cover_image = %s, synopsis = %s, collector_comments = %s, trivia = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        ''',
        (
            kwargs['name'], kwargs['edition_number'], kwargs['is_special_edition'], kwargs['publication_date'],
            kwargs['publisher'], kwargs['launch_value'], kwargs['currency_type'], kwargs['current_value'],
            kwargs['cover_image'], kwargs['synopsis'], kwargs['collector_comments'], kwargs['trivia'], comic_id,
        ),
    )
    commit_db()


def delete_comic(comic_id: int):
    db_execute('DELETE FROM comics WHERE id = %s', (comic_id,))
    commit_db()


def get_recent_comics(limit=8):
    return fetch_all(
        '''
        SELECT cm.*, c.name AS collection_name
        FROM comics cm
        JOIN collections c ON c.id = cm.collection_id
        ORDER BY cm.updated_at DESC, cm.created_at DESC
        LIMIT %s
        ''',
        (limit,),
    )


def get_stats():
    collections = fetch_one('SELECT COUNT(*) AS total FROM collections')
    comics = fetch_one('SELECT COUNT(*) AS total FROM comics')
    admins = fetch_one('SELECT COUNT(*) AS total FROM users WHERE is_admin = 1')
    total_value = fetch_one('SELECT COALESCE(SUM(current_value), 0) AS total FROM comics')
    return {
        'collections': int(collections['total']) if collections else 0,
        'comics': int(comics['total']) if comics else 0,
        'admins': int(admins['total']) if admins else 0,
        'total_value': float(total_value['total'] or 0) if total_value else 0.0,
    }


def parse_comic_form(req):
    def text(name):
        value = req.form.get(name, '').strip()
        return value or None

    def money(name):
        value = req.form.get(name, '').strip()
        if not value:
            return None
        normalized = value.replace('.', '').replace(',', '.') if ',' in value else value
        try:
            return float(normalized)
        except ValueError:
            raise ValueError(f'Valor inválido no campo {name}.')

    return {
        'name': req.form.get('name', '').strip(),
        'edition_number': req.form.get('edition_number', '').strip(),
        'is_special_edition': 1 if req.form.get('is_special_edition') == 'on' else 0,
        'publication_date': text('publication_date'),
        'publisher': text('publisher'),
        'launch_value': money('launch_value'),
        'currency_type': text('currency_type') or 'R$',
        'current_value': money('current_value'),
        'synopsis': text('synopsis'),
        'collector_comments': text('collector_comments'),
        'trivia': text('trivia'),
    }


if __name__ == '__main__':
    app = create_app()
    port = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=True)
