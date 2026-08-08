"""能力包存储抽象：本地文件系统（默认）或 MinIO。"""

import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException, status

from app.config import get_settings


class ArtifactStorage:
    def save(self, capability_id: str, filename: str, content: BinaryIO) -> dict:
        raise NotImplementedError

    def open(self, uri: str) -> BinaryIO:
        raise NotImplementedError

    def delete(self, uri: str) -> None:
        raise NotImplementedError


class LocalStorage(ArtifactStorage):
    def __init__(self) -> None:
        self.root: Path = get_settings().artifact_path
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, uri: str) -> Path:
        return self.root / uri

    def save(self, capability_id: str, filename: str, content: BinaryIO) -> dict:
        target_dir = self.root / capability_id
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name
        target = target_dir / safe_name
        with target.open("wb") as out:
            shutil.copyfileobj(content, out)
        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        uri = f"{capability_id}/{safe_name}"
        return {
            "uri": uri,
            "storage_type": "local",
            "checksum": checksum,
            "size_bytes": target.stat().st_size,
        }

    def open(self, uri: str) -> BinaryIO:
        path = self._path(uri)
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "能力包不存在")
        return path.open("rb")

    def delete(self, uri: str) -> None:
        path = self._path(uri)
        if path.is_file():
            path.unlink()


class MinioStorage(ArtifactStorage):
    def __init__(self) -> None:
        from minio import Minio

        s = get_settings()
        self.client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        self.bucket = s.minio_bucket
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def save(self, capability_id: str, filename: str, content: BinaryIO) -> dict:
        import io

        data = content.read()
        object_name = f"{capability_id}/{Path(filename).name}"
        self.client.put_object(
            self.bucket, object_name, io.BytesIO(data), length=len(data)
        )
        return {
            "uri": object_name,
            "storage_type": "minio",
            "checksum": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }

    def open(self, uri: str) -> BinaryIO:
        import io

        resp = self.client.get_object(self.bucket, uri)
        return io.BytesIO(resp.read())

    def delete(self, uri: str) -> None:
        self.client.remove_object(self.bucket, uri)


def get_storage() -> ArtifactStorage:
    if get_settings().artifact_storage == "minio":
        return MinioStorage()
    return LocalStorage()
