import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ['https://www.googleapis.com/auth/drive.file']

def get_drive_service():
    json_data = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not json_data:
        return None

    creds_dict = json.loads(json_data)

    credentials = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )

    service = build("drive", "v3", credentials=credentials)

    return service


def upload_file_to_drive(file_path, filename):
    service = get_drive_service()

    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }

    media = MediaFileUpload(file_path)

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    return file.get("id")
