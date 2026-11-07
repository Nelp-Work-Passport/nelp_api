"""Live test of the bucket: python -m app.storage_check

Uploads a small file, reads it through a temporary link, confirms it cannot be
read without one, then deletes it.
"""

import sys
import urllib.error
import urllib.request
import uuid

from app.adapters import storage
from app.config import get_settings
from app.errors import AppError


def _status(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def main() -> int:
    settings = get_settings()
    if not storage.is_configured():
        print("Not configured: set S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY in .env")
        return 1

    store = storage.get_storage()
    key = f"healthcheck/{uuid.uuid4()}.txt"
    body = b"nelp storage check"
    try:
        store.put(key, body, "text/plain")
    except AppError as exc:
        print(f"Upload: FAILED ({exc.__cause__})")
        return 1
    print("Upload: ok")
    failed = False
    try:
        status, content = _status(store.presigned_url(key, 60))
        linked = status == 200 and content == body
        print(f"Read through temporary link: {'ok' if linked else f'FAILED ({status})'}")

        public_url = f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"
        status, _ = _status(public_url)
        private = status in (401, 403)
        print(f"Blocked without a link: {'ok' if private else f'FAILED ({status})'}")
        failed = not (linked and private)
    finally:
        store.delete(key)
        print("Delete: ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
