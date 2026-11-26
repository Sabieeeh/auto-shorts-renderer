import os
import io
import ssl
import json
import time
import random
import socket

import httplib2
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google.auth.exceptions import TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]

MAX_RETRIES = 5
BACKOFF_BASE = 2.0
BACKOFF_MAX = 30.0
INNER_RETRIES = 3
UPLOAD_CHUNK = 8 * 1024 * 1024
DOWNLOAD_CHUNK = 10 * 1024 * 1024

_FATAL_OS = (FileNotFoundError, PermissionError, IsADirectoryError, NotADirectoryError)


def _build_service():
    """Bangun objek service Google Drive API v3 baru."""
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
        _call_with_retry("refresh token OAuth", None, lambda _s: creds.refresh(Request()))
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    raise RuntimeError(
        "Kredensial Google Drive belum disetel! Isi GDRIVE_CLIENT_ID + GDRIVE_CLIENT_SECRET + GDRIVE_REFRESH_TOKEN "
        "atau GDRIVE_SERVICE_ACCOUNT_JSON di GitHub Secrets."
    )


class ResilientDrive:
    """Pembungkus service Drive yang dapat membangun ulang koneksi saat error putus."""

    def __init__(self):
        self._svc = _build_service()

    def rebuild(self):
        self._svc = _build_service()

    def __getattr__(self, name):
        if name == "_svc" or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._svc, name)


def get_drive_service():
    """Inisialisasi Google Drive API v3 memakai OAuth Refresh Token atau Service Account."""
    return ResilientDrive()


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        if status in (408, 429, 500, 502, 503, 504):
            return True
        if status == 403 and "ratelimit" in str(exc).lower():
            return True
        return False
    if isinstance(exc, _FATAL_OS):
        return False
    return isinstance(
        exc,
        (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            ssl.SSLError,
            socket.timeout,
            TimeoutError,
            OSError,
            httplib2.HttpLib2Error,
            TransportError,
        ),
    )


def _rebuild(service):
    try:
        if hasattr(service, "rebuild"):
            service.rebuild()
            return service
        return _build_service()
    except Exception as e:
        print(f"[Drive] Gagal membangun ulang service: {type(e).__name__}: {str(e)[:160]}")
        return service


def _call_with_retry(label: str, service, fn, retries: int = MAX_RETRIES):
    last = None
    for attempt in range(1, retries + 1):
        try:
            return fn(service)
        except Exception as e:
            if not _is_retryable(e):
                raise
            last = e
            print(f"[Drive] {label} gagal (percobaan {attempt}/{retries}): {type(e).__name__}: {str(e)[:160]}")
            if attempt == retries:
                break
            wait = min(BACKOFF_BASE ** attempt, BACKOFF_MAX) + random.uniform(0.0, 1.0)
            print(f"[Drive] Menunggu {wait:.1f} dtk lalu mencoba lagi dengan koneksi baru...")
            time.sleep(wait)
            if service is not None:
                service = _rebuild(service)
    raise RuntimeError(f"[Drive] {label} gagal setelah {retries} percobaan: {type(last).__name__}: {last}") from last


def find_folder(service, folder_name: str) -> dict:
    """Cari folder di Google Drive berdasarkan nama."""
    safe_name = folder_name.replace("'", "\\'")
    q = (
        f"mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{safe_name}' and trashed = false"
    )

    def _op(svc):
        return (
            svc.files()
            .list(
                q=q,
                spaces="drive",
                fields="files(id, name, modifiedTime, webViewLink)",
                orderBy="modifiedTime desc",
                pageSize=5,
            )
            .execute(num_retries=INNER_RETRIES)
        )

    res = _call_with_retry(f"cari folder '{folder_name}'", service, _op)
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
        def _op(svc, _tok=page_token):
            return (
                svc.files()
                .list(
                    q=q,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, webViewLink)",
                    pageSize=100,
                    pageToken=_tok,
                )
                .execute(num_retries=INNER_RETRIES)
            )

        res = _call_with_retry("daftar file folder", service, _op)
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return sorted(items, key=lambda x: x["name"].lower())


def download_file(service, file_id: str, dest_path: str):
    """Unduh satu file dari Google Drive ke disk lokal."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    def _op(svc):
        request = svc.files().get_media(fileId=file_id)
        with io.FileIO(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=DOWNLOAD_CHUNK)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=INNER_RETRIES)

    _call_with_retry(f"unduh {os.path.basename(dest_path)}", service, _op)


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
        if any(ex in low for ex in ("output_", "short_", "long_")):
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
    size_mb = os.path.getsize(local_path) / (1024 * 1024)

    def _op(svc):
        existing = (
            svc.files()
            .list(q=q, fields="files(id, name)")
            .execute(num_retries=INNER_RETRIES)
            .get("files", [])
        )
        media = MediaFileUpload(local_path, mimetype=mime_type, chunksize=UPLOAD_CHUNK, resumable=True)
        try:
            if existing:
                request = svc.files().update(
                    fileId=existing[0]["id"], media_body=media, fields="id, name, webViewLink"
                )
            else:
                request = svc.files().create(
                    body={"name": name, "parents": [folder_id]},
                    media_body=media,
                    fields="id, name, webViewLink",
                )
            response, last_pct = None, -1
            while response is None:
                status, response = request.next_chunk(num_retries=INNER_RETRIES)
                if status:
                    pct = int(status.progress() * 100)
                    if pct >= last_pct + 20:
                        last_pct = pct
                        print(f"[Drive] Upload {name}: {pct}%")
            return response
        finally:
            fd = getattr(media, "_fd", None)
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass

    result = _call_with_retry(f"upload {name} ({size_mb:.1f} MB)", service, _op)
    print(f"[Drive] Upload selesai: {name}")
    return result
