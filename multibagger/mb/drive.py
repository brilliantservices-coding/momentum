"""Read-only access to a Google Drive folder shared with the agents' service account.

The scheduled Cowork task on the user's desktop drops announcement CSVs here. Files are never
modified; processed file ids are remembered in Firestore (`drive_seen/`).
"""
from __future__ import annotations

API = "https://www.googleapis.com/drive/v3/files"
SHEET = "application/vnd.google-apps.spreadsheet"


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


def list_files(folder_id: str, session=None) -> list[dict]:
    s = session or _session()
    out, token = [], None
    while True:
        params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": 100,
                  "fields": "nextPageToken, files(id, name, mimeType, createdTime)", "orderBy": "createdTime"}
        if token:
            params["pageToken"] = token
        r = s.get(API, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        out += data.get("files", [])
        token = data.get("nextPageToken")
        if not token:
            return out


def download(f: dict, session=None) -> tuple[bytes, str]:
    """Returns (bytes, filename); Google Sheets are exported as CSV."""
    s = session or _session()
    if f.get("mimeType") == SHEET:
        r = s.get(f"{API}/{f['id']}/export", params={"mimeType": "text/csv"}, timeout=60)
        name = f["name"] if f["name"].lower().endswith(".csv") else f["name"] + ".csv"
    else:
        r = s.get(f"{API}/{f['id']}", params={"alt": "media"}, timeout=60)
        name = f["name"]
    r.raise_for_status()
    return r.content, name
