import os
import io
import json
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    """Inisialisasi Google Drive API v3 memakai OAuth Refresh Token atau Service Account."""
    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    client_id = os.environ.get("GDRIVE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GDRIVE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GDRIVE_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    raise RuntimeError(
        "Kredensial Google Drive belum disetel! Isi GDRIVE_CLIENT_ID + GDRIVE_CLIENT_SECRET + GDRIVE_REFRESH_TOKEN "
        "atau GDRIVE_SERVICE_ACCOUNT_JSON di GitHub Secrets."
    )


def find_folder(service, folder_name: str) -> dict:
    """Cari folder di Google Drive berdasarkan nama."""
    safe_name = folder_name.replace("'", "\\'")
    q = (
        f"mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{safe_name}' and trashed = false"
    )
    res = (
        service.files()
        .list(
            q=q,
            spaces="drive",
            fields="files(id, name, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc",
            pageSize=5,
        )
        .execute()
    )
    folders = res.get("files", [])
    if not folders:
        raise FileNotFoundError(f"Folder '{folder_name}' tidak ditemukan di Google Drive.")
    return folders[0]


def list_folder_files(service, folder_id: str) -> list:
    """Daftar semua file non-folder di dalam folder proyek."""
    q = (
        f"'{folder_id}' in parents "
        f"and mimeType != 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    items = []
    page_token = None
    while True:
        res = (
            service.files()
            .list(
                q=q,
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, webViewLink)",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return sorted(items, key=lambda x: x["name"].lower())


def download_file(service, file_id: str, dest_path: str):
    """Unduh satu file dari Google Drive ke disk lokal."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def download_project_assets(service, folder_id: str, local_dir: str, include_media: bool = True) -> dict:
    """Unduh bahan proyek dari folder Drive ke local_dir."""
    os.makedirs(local_dir, exist_ok=True)
    files = list_folder_files(service, folder_id)
    downloaded = []
    media_ext = (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp")
    audio_ext = (".mp3", ".m4a", ".wav")
    meta_names = {"clip_labels.json", "auto_plan.json", "plan_state.json"}

    for f in files:
        name = f["name"]
        low = name.lower()
        if any(ex in low for ex in ("output_", "short_")):
            continue
        is_meta = low in meta_names or low.startswith("transkrip")
        is_audio = low.endswith(audio_ext)
        is_media = low.endswith(media_ext)

        if is_meta or is_audio or (include_media and is_media):
            dest = os.path.join(local_dir, name)
            print(f"[Drive] Mengunduh {name} ...")
            download_file(service, f["id"], dest)
            downloaded.append(name)

    return {"all_remote": files, "downloaded": downloaded}


def upload_or_update_file(service, folder_id: str, local_path: str, remote_name: str = None, mime_type: str = None) -> dict:
    """Unggah atau timpa file dengan nama yang sama di folder Drive tujuan."""
    name = remote_name or os.path.basename(local_path)
    safe_name = name.replace("'", "\\'")
    q = f"'{folder_id}' in parents and name = '{safe_name}' and trashed = false"
    existing = service.files().list(q=q, fields="files(id, name)").execute().get("files", [])

    media = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)
    if existing:
        file_id = existing[0]["id"]
        updated = (
            service.files()
            .update(fileId=file_id, media_body=media, fields="id, name, webViewLink")
            .execute()
        )
        return updated

    meta = {"name": name, "parents": [folder_id]}
    created = (
        service.files()
        .create(body=meta, media_body=media, fields="id, name, webViewLink")
        .execute()
    )
    return created
