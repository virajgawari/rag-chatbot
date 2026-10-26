"""Abstract base class for object storage."""
from abc import ABC, abstractmethod


class BaseStorage(ABC):
    @abstractmethod
    async def upload(self, destination_path: str, data: bytes, mime_type: str) -> str:
        """Upload raw file bytes and return storage path / key."""
        pass

    @abstractmethod
    async def download(self, storage_path: str) -> bytes:
        """Download raw file bytes by storage path / key."""
        pass

    @abstractmethod
    async def delete(self, storage_path: str) -> bool:
        """Delete file by storage path / key."""
        pass
