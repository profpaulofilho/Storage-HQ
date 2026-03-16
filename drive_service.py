import json
import os
import tempfile
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive.file']


def drive_enabled():
    return os.getenv('GOOGLE_DRIVE_ENABLED', 'false').lower() == 'true'


def _token_path():
    data_dir = Path(os.getenv('DATA_DIR', '/var/data'))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / 'google_oauth_token.json'


def get_oauth_client_config():
    client_id = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')
    redirect_uri = os.getenv('GOOGLE_OAUTH_REDIRECT_URI')

    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError('Credenciais OAuth do Google Drive não configuradas.')

    return {
        "web": {
            "client_id": client_id,
            "project_id": "storage-hqs",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
        }
    }


def build_auth_flow(state=None):
    config = get_oauth_client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, state=state)
    flow.redirect_uri = os.getenv('GOOGLE_OAUTH_REDIRECT_URI')
    return flow


def get_authorization_url():
    flow = build_auth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )
    return auth_url, state


def save_credentials_from_response(state, authorization_response):
    flow = build_auth_flow(state=state)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    _token_path().write_text(creds.to_json(), encoding='utf-8')


def load_credentials():
    token_file = _token_path()
    if not token_file.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding='utf-8')

    return creds


def get_drive_service():
    creds = load_credentials()
    if not creds:
        return None
    return build('drive', 'v3', credentials=creds)


def upload_bytes_to_drive(file_storage, filename, folder_id):
    service = get_drive_service()
    if service is None:
        raise RuntimeError('Google Drive não autorizado. Conecte sua conta primeiro.')

    suffix = ''
    if '.' in filename:
        suffix = '.' + filename.rsplit('.', 1)[1].lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file_storage.save(tmp.name)
        temp_path = tmp.name

    try:
        metadata = {
            'name': filename,
            'parents': [folder_id],
        }

        media = MediaFileUpload(temp_path, resumable=True)

        created = service.files().create(
            body=metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink'
        ).execute()

        service.permissions().create(
            fileId=created['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()

        return {
            'id': created['id'],
            'webViewLink': created.get('webViewLink'),
            'webContentLink': created.get('webContentLink'),
        }
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
