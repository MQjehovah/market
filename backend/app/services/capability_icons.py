"""能力头像：本地文件 magic bytes 校验与原子存取（PNG / JPEG / WebP，≤256KB）。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.config import get_settings

MAX_ICON_BYTES = 256 * 1024

_MEDIA_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}


def sniff_icon_ext(data: bytes) -> str | None:
    """按 magic bytes 识别图片类型（供上传校验）。支持 PNG / JPEG / WebP / SVG。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:2048].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return ".svg"
    return None


def media_type_for(stored_name: str) -> str:
    return _MEDIA_BY_EXT.get(Path(stored_name).suffix.lower(), "application/octet-stream")


def icon_abs_path(stored_name: str) -> Path:
    """DB 存的文件名 → 绝对路径（只取 basename，防路径穿越）。"""
    return get_settings().icon_path / Path(stored_name).name


def save_icon_file(cap_id: str, ext: str, data: bytes) -> str:
    """原子写头像文件（临时文件 + os.replace），返回存储文件名。"""
    root = get_settings().icon_path
    root.mkdir(parents=True, exist_ok=True)
    name = f"{cap_id}{ext}"
    target = root / name
    fd, tmp = tempfile.mkstemp(prefix=f".{cap_id}-", suffix=".tmp", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return name


def delete_icon_file(stored_name: str) -> None:
    if not stored_name:
        return
    icon_abs_path(stored_name).unlink(missing_ok=True)
