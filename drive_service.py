import json
import os
import tempfile

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive']


def drive_enabled():
    return os.getenv('GOOGLE_DRIVE_ENABLED', 'false').lower() == 'true'


def get_drive_service():
    json_data = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not json_data:
        return None

    creds_dict = json.loads(json_data)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=SCOPES
    )
    return build('drive', 'v3', credentials=credentials)


def upload_bytes_to_drive(file_storage, filename, folder_id):
    service = get_drive_service()
    if service is None:
        raise RuntimeError('Google Drive não configurado.')

    suffix = ''
    if '.' in filename:
        suffix = '.' + filename.rsplit('.', 1)[1].lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file_storage.save(tmp.name)
        temp_path = tmp.name

    try:
        metadata = {
            'name': filename,
            'parents': [folder_id]
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
