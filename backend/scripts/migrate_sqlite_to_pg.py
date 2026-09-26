"""一次性迁移：把 SQLite(data/marketplace.db) 全量拷贝到当前 DATABASE_URL 指向的库。

用法（在 market 容器内，DATABASE_URL 已指向目标库）：
    python scripts/migrate_sqlite_to_pg.py [--sqlite data/marketplace.db] [--dry-run]

- 目标库须已建表(init_db/create_all)；先清空目标表(按依赖逆序)再按依赖序插入，可重跑。
- 按列类型做必要转换：DateTime/Boolean/JSON。
- 制品(artifacts)在文件系统(./data/artifacts)，不涉及。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, insert  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.types import Boolean, DateTime, JSON  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.models import *  # noqa: F401,F403,E402  # 确保所有模型注册进 metadata


def _convert(col, value):
    if value is None:
        return None
    if isinstance(col.type, Boolean):
        return bool(value)
    if isinstance(col.type, DateTime) and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    if isinstance(col.type, JSON) and isinstance(value, str):
        return json.loads(value)
    return value


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="data/marketplace.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.sqlite)
    if not src.exists():
        raise SystemExit(f"sqlite 文件不存在: {src}")

    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    src_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    tables = [t for t in Base.metadata.sorted_tables if t.name in src_tables]
    skipped = src_tables - {t.name for t in tables} - {"sqlite_sequence"}
    print(f"迁移表 {len(tables)} 个；sqlite 独有(跳过): {sorted(skipped) or '无'}")

    async with engine.begin() as pg:
        # 清空目标表（依赖逆序），保证可重跑
        for t in reversed(tables):
            await pg.execute(delete(t))
        total = 0
        for t in tables:
            cols = {c.name: c for c in t.columns}
            rows = [dict(r) for r in con.execute(f'SELECT * FROM "{t.name}"')]
            payload = []
            for r in rows:
                item = {}
                for name, col in cols.items():
                    if name in r:
                        try:
                            item[name] = _convert(col, r[name])
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            raise SystemExit(f"{t.name}.{name} 转换失败 {r[name]!r}: {exc}") from exc
                payload.append(item)
            note = ""
            if payload and not args.dry_run:
                try:
                    async with pg.begin_nested():
                        await pg.execute(insert(t), payload)
                except IntegrityError:
                    # sqlite 不强制外键, 历史数据可能存在孤儿行: 逐行兜底, 跳过冲突行
                    ok = bad = 0
                    for item in payload:
                        try:
                            async with pg.begin_nested():
                                await pg.execute(insert(t), [item])
                            ok += 1
                        except IntegrityError:
                            bad += 1
                    note = f"（批量冲突→逐行 ok={ok} 跳过孤儿={bad}）"
            total += len(payload)
            print(f"  {t.name:<24} {len(payload):>6} 行 {note}")
        print(f"{'[dry-run] ' if args.dry_run else ''}合计 {total} 行")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
