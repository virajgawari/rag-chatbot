"""Local filesystem storage adapter for development and tests."""
import asyncio
from pathlib import Path

from backend.core.errors import StorageUnavailableError
from backend.storage.base import BaseStorage


class LocalStorage(BaseStorage):
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _target(self, storage_path: str) -> Path:
        target = (self.base_dir / storage_path).resolve()
        if not target.is_relative_to(self.base_dir):
            raise StorageUnavailableError("Invalid storage path.")
        return target

    def _write(self, target: Path, data: bytes) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target.relative_to(self.base_dir))

    def _read(self, target: Path) -> bytes:
        if not target.exists():
            raise StorageUnavailableError("The stored file could not be found.")
        return target.read_bytes()

    def _delete(self, target: Path) -> bool:
        if target.exists():
            target.unlink()
            return True
        return False

    async def upload(self, destination_path: str, data: bytes, mime_type: str) -> str:
        target = self._target(destination_path)
        return await asyncio.to_thread(self._write, target, data)

    async def download(self, storage_path: str) -> bytes:
        target = self._target(storage_path)
        return await asyncio.to_thread(self._read, target)

    async def delete(self, storage_path: str) -> bool:
        target = self._target(storage_path)
        return await asyncio.to_thread(self._delete, target)
