"""Supabase Storage adapter using the Storage REST API.

The bucket must be private. Files are only ever read/written by this server
using the service-role key; no public or signed URLs are returned to clients.
"""
from urllib.parse import quote

import httpx

from backend.core.errors import StorageUnavailableError
from backend.storage.base import BaseStorage

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)


class SupabaseStorage(BaseStorage):
    def __init__(self, supabase_url: str, service_role_key: str, bucket: str = "documents"):
        self.supabase_url = supabase_url.rstrip("/")
        self.service_role_key = service_role_key
        self.bucket = bucket
        self.storage_base = f"{self.supabase_url}/storage/v1/object"

    def _headers(self, mime_type: str = "application/octet-stream") -> dict:
        return {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
            "Content-Type": mime_type,
        }

    async def upload(self, destination_path: str, data: bytes, mime_type: str) -> str:
        encoded_path = quote(destination_path, safe="/")
        url = f"{self.storage_base}/{self.bucket}/{encoded_path}"
        headers = {**self._headers(mime_type), "x-upsert": "true"}
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(url, headers=headers, content=data)
        except httpx.HTTPError as exc:
            raise StorageUnavailableError("Could not reach file storage.") from exc
        if response.status_code not in (200, 201):
            raise StorageUnavailableError(
                f"File storage rejected the upload (status {response.status_code})."
            )
        return f"{self.bucket}/{destination_path}"

    async def download(self, storage_path: str) -> bytes:
        clean_path = storage_path.removeprefix(f"{self.bucket}/")
        url = f"{self.storage_base}/{self.bucket}/{quote(clean_path, safe='/')}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise StorageUnavailableError("Could not reach file storage.") from exc
        if response.status_code != 200:
            raise StorageUnavailableError("The stored file could not be downloaded.")
        return response.content

    async def delete(self, storage_path: str) -> bool:
        clean_path = storage_path.removeprefix(f"{self.bucket}/")
        url = f"{self.storage_base}/{self.bucket}/{quote(clean_path, safe='/')}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.delete(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise StorageUnavailableError("Could not reach file storage.") from exc
        return response.status_code in (200, 204)
