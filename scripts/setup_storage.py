"""Create and verify the private Supabase Storage bucket used for uploads.

Usage:
    python scripts/setup_storage.py
    python scripts/setup_storage.py --verify-only

Uses SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from the server environment.
Never prints either value. The bucket is always private; clients never receive
public or signed URLs from the API.
"""
import argparse
import asyncio
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402

ALLOWED_MIME_TYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY or "",
        "Content-Type": "application/json",
    }


def _bucket_missing(response: httpx.Response) -> bool:
    """Supabase Storage reports a missing bucket as 400 with a nested 404 body."""
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    body = response.text.lower()
    return "bucket not found" in body or "nosuchbucket" in body


async def _run(verify_only: bool) -> int:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the server environment.")
        return 2

    base = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/bucket"
    bucket = settings.SUPABASE_STORAGE_BUCKET
    desired = {
        "id": bucket,
        "name": bucket,
        "public": False,
        "file_size_limit": settings.MAX_UPLOAD_SIZE_BYTES,
        "allowed_mime_types": ALLOWED_MIME_TYPES,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)) as client:
        existing = await client.get(f"{base}/{bucket}", headers=_headers())
        if _bucket_missing(existing):
            if verify_only:
                print(f"Bucket '{bucket}' does not exist.")
                return 1
            created = await client.post(base, headers=_headers(), json=desired)
            if created.status_code not in (200, 201):
                print(f"ERROR: bucket creation failed (status {created.status_code}): {created.text}")
                return 1
            print(f"Created private bucket '{bucket}' (max {settings.MAX_UPLOAD_SIZE_BYTES} bytes).")
        elif existing.status_code != 200:
            print(f"ERROR: could not inspect bucket '{bucket}' (status {existing.status_code}): {existing.text}")
            return 1
        else:
            info = existing.json()
            if info.get("public") is True:
                if verify_only:
                    print(f"ERROR: bucket '{bucket}' is PUBLIC.")
                    return 1
                updated = await client.put(f"{base}/{bucket}", headers=_headers(), json=desired)
                if updated.status_code not in (200, 204):
                    print(f"ERROR: could not make bucket private (status {updated.status_code}): {updated.text}")
                    return 1
                print(f"Bucket '{bucket}' existed but was public; switched it to private.")
            else:
                print(f"Bucket '{bucket}' already exists and is private.")

        # Re-read to report observed state rather than assumed state.
        final = await client.get(f"{base}/{bucket}", headers=_headers())
        if final.status_code != 200:
            print(f"ERROR: verification failed (status {final.status_code}).")
            return 1
        info = final.json()
        if info.get("public") is True:
            print(f"ERROR: bucket '{bucket}' is still public.")
            return 1
        print(
            f"Verified: bucket '{bucket}' is private (file size limit={info.get('file_size_limit')})."
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create/verify the private DocChat storage bucket")
    parser.add_argument("--verify-only", action="store_true", help="do not create or modify the bucket")
    args = parser.parse_args()
    return asyncio.run(_run(args.verify_only))


if __name__ == "__main__":
    raise SystemExit(main())
