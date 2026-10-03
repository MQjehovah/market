"""将旧格式专家包升级为新格式（plugin.json + agents/<name>.md + mcps/<n>/connection.json）。

就地**追加**新工件（旧工件保留，可回滚）；已是新格式或无工件则跳过。
用法（容器内 /app/backend）：python -m scripts.migrate_agent_packages [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import zipfile

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, CapabilityArtifact
from app.services.agent_package import legacy_agent_to_new
from app.storage import get_storage


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


def _read_files(uri: str) -> dict[str, bytes]:
    content = get_storage().open(uri).read()
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


async def _latest_artifact(db, cap_id: str) -> CapabilityArtifact | None:
    return await db.scalar(
        select(CapabilityArtifact)
        .where(CapabilityArtifact.capability_id == cap_id)
        .order_by(CapabilityArtifact.uploaded_at.desc())
    )


async def main(dry_run: bool) -> int:
    migrated = skipped = failed = 0
    async with SessionLocal() as db:
        caps = list(
            (
                await db.scalars(
                    select(Capability)
                    .where(Capability.type == "agent")
                    .order_by(Capability.name, Capability.version)
                )
            ).all()
        )
        for cap in caps:
            art = await _latest_artifact(db, cap.id)
            if art is None:
                skipped += 1
                continue
            try:
                files = _read_files(art.uri)
            except Exception as exc:  # noqa: BLE001
                print(f"[read-fail] {cap.name} v{cap.version}: {exc}")
                failed += 1
                continue
            new_files = legacy_agent_to_new(files, cap.name)
            if new_files is None:
                skipped += 1
                continue
            if dry_run:
                print(
                    f"[dry] {cap.name} v{cap.version} [{cap.status}] "
                    f"{len(files)} -> {len(new_files)} files"
                )
                migrated += 1
                continue
            data = _zip_bytes(new_files)
            filename = f"{cap.name}-{cap.version}-newfmt.zip"
            info = get_storage().save(cap.id, filename, io.BytesIO(data))
            db.add(CapabilityArtifact(capability_id=cap.id, filename=filename, **info))
            await db.commit()
            print(f"[ok] {cap.name} v{cap.version} [{cap.status}] {len(files)} -> {len(new_files)}")
            migrated += 1
    print(f"\nmigrated={migrated} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run)))
