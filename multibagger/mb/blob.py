"""Raw file storage: Cloud Storage in GCP, a local folder otherwise."""
from __future__ import annotations

from .config import settings


class LocalBlob:
    def __init__(self, root=None):
        self.root = (root or settings.data_dir) / "blob"

    def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def get(self, path: str) -> bytes | None:
        p = self.root / path
        return p.read_bytes() if p.exists() else None

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())

    def move(self, src: str, dst: str) -> None:
        d = self.root / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        (self.root / src).replace(d)


class GcsBlob:
    def __init__(self):
        from google.cloud import storage

        self.bucket = storage.Client(project=settings.project or None).bucket(settings.bucket)

    def put(self, path, data, content_type="application/octet-stream"):
        self.bucket.blob(path).upload_from_string(data, content_type=content_type)
        return f"gs://{settings.bucket}/{path}"

    def get(self, path):
        b = self.bucket.blob(path)
        return b.download_as_bytes() if b.exists() else None

    def exists(self, path):
        return self.bucket.blob(path).exists()

    def list(self, prefix):
        return sorted(b.name for b in self.bucket.list_blobs(prefix=prefix) if not b.name.endswith("/"))

    def move(self, src, dst):
        self.bucket.copy_blob(self.bucket.blob(src), self.bucket, dst)
        self.bucket.blob(src).delete()


_blob = None


def get_blob():
    global _blob
    if _blob is None:
        _blob = GcsBlob() if settings.backend == "gcp" else LocalBlob()
    return _blob
