"""DWS 文件上传/下载：文件始终落在用户自己的 dws workspace。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dws.workspace import DwsWorkspace
from app.models import DwsFile, User

_SAFE_NAME = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")


class DwsFileError(ValueError):
    pass


def safe_name(filename: str) -> str:
    name = Path(str(filename or "").replace("\\", "/")).name.strip()
    name = _SAFE_NAME.sub("_", name).strip("._")
    if not name:
        name = "file"
    return name[:180]


def _user_dir(workspace: DwsWorkspace, user_id: str) -> Path:
    return workspace.ensure(user_id)


def _relative(workspace: DwsWorkspace, user_id: str, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_user_dir(workspace, user_id).resolve()))
    except ValueError as exc:
        raise DwsFileError("文件路径越界") from exc


def resolve_file(workspace: DwsWorkspace, user_id: str, item: DwsFile) -> Path:
    base = _user_dir(workspace, user_id).resolve()
    path = (base / item.path).resolve()
    if base not in path.parents and path != base:
        raise DwsFileError("文件路径越界")
    return path


async def save_upload(
    db: AsyncSession,
    user: User,
    filename: str,
    content: bytes,
    *,
    workspace: DwsWorkspace | None = None,
) -> DwsFile:
    settings = get_settings()
    ws = workspace or DwsWorkspace()
    limit = max(1, settings.dws_max_file_mb) * 1024 * 1024
    if len(content) > limit:
        raise DwsFileError(f"文件超过 {settings.dws_max_file_mb} MB 限制")
    name = safe_name(filename)
    file_id = uuid.uuid4().hex
    target = _user_dir(ws, user.id) / "files" / "uploads" / file_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    row = DwsFile(
        id=file_id,
        user_id=user.id,
        name=name,
        path=_relative(ws, user.id, target),
        size=len(content),
        direction="in",
    )
    db.add(row)
    await db.flush()
    return row


async def get_file(db: AsyncSession, user: User, file_id: str) -> DwsFile:
    row = await db.get(DwsFile, file_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    return row


async def save_output(
    db: AsyncSession,
    user: User,
    path: Path,
    *,
    workspace: DwsWorkspace | None = None,
) -> DwsFile:
    ws = workspace or DwsWorkspace()
    name = safe_name(path.name)
    row = DwsFile(
        id=uuid.uuid4().hex,
        user_id=user.id,
        name=name,
        path=_relative(ws, user.id, path),
        size=path.stat().st_size if path.exists() else 0,
        direction="out",
    )
    db.add(row)
    await db.flush()
    return row
