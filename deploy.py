#!/usr/bin/env python3
"""把 market 项目打包上传到服务器，并用 docker compose 构建/启动。

用法:
  1. pip install paramiko
  2. copy deploy.config.example.py deploy.config.py  # 填主机/账号/密码
  3. python deploy.py

也可用环境变量覆盖: MARKET_DEPLOY_HOST / USER / PASSWORD / REMOTE_DIR / PUBLIC_URL
"""

from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("缺少 paramiko，请先执行: pip install paramiko", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent

# ---------- 配置（deploy.config.py > 环境变量 > 默认值）----------
HOST = "192.168.31.34"
USER = "root"
PASSWORD = ""
REMOTE_DIR = "/root/market"
PUBLIC_URL = "http://192.168.31.34:8093/"
PORT = 22
BUILD = True

_cfg = ROOT / "deploy.config.py"
if _cfg.is_file():
    _ns: dict = {}
    exec(_cfg.read_text(encoding="utf-8"), _ns)
    HOST = _ns.get("HOST", HOST)
    USER = _ns.get("USER", USER)
    PASSWORD = _ns.get("PASSWORD", PASSWORD)
    REMOTE_DIR = _ns.get("REMOTE_DIR", REMOTE_DIR)
    PUBLIC_URL = _ns.get("PUBLIC_URL", PUBLIC_URL)
    PORT = int(_ns.get("PORT", PORT))
    BUILD = bool(_ns.get("BUILD", BUILD))

HOST = os.environ.get("MARKET_DEPLOY_HOST", HOST)
USER = os.environ.get("MARKET_DEPLOY_USER", USER)
PASSWORD = os.environ.get("MARKET_DEPLOY_PASSWORD", PASSWORD)
REMOTE_DIR = os.environ.get("MARKET_DEPLOY_REMOTE_DIR", REMOTE_DIR)
PUBLIC_URL = os.environ.get("MARKET_DEPLOY_PUBLIC_URL", PUBLIC_URL)
PORT = int(os.environ.get("MARKET_DEPLOY_PORT", str(PORT)))
if "MARKET_DEPLOY_BUILD" in os.environ:
    BUILD = os.environ["MARKET_DEPLOY_BUILD"].strip().lower() in ("1", "true", "yes")

# 上传时排除的路径前缀/名称（相对仓库根）
EXCLUDE_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "data",
    "logs",
    ".DS_Store",
    "Thumbs.db",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".db", ".log")
EXCLUDE_FILES = {
    ".env",
    "deploy.config.py",
    "deploy.tar.gz",
}


def safe_print(msg: str = "") -> None:
    text = str(msg)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(enc, "replace").decode(enc, "replace"))


def connect() -> paramiko.SSHClient:
    if not PASSWORD:
        raise SystemExit(
            "未配置 PASSWORD。请复制 deploy.config.example.py 为 deploy.config.py 并填写，"
            "或设置环境变量 MARKET_DEPLOY_PASSWORD。"
        )
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    safe_print(f"SSH connect {USER}@{HOST}:{PORT} ...")
    c.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 300) -> tuple[int, str]:
    safe_print(f"$ {cmd}")
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    text = out + (("\n" + err) if err.strip() else "")
    if text:
        safe_print(text[-6000:] if len(text) > 6000 else text)
    safe_print(f"[exit {code}]")
    return code, text


def _should_skip(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & EXCLUDE_NAMES:
        return True
    name = rel.name
    if name in EXCLUDE_FILES:
        return True
    if name.endswith(EXCLUDE_SUFFIXES):
        return True
    if name.startswith("deploy") and name.endswith((".tar", ".tar.gz", ".tgz")):
        return True
    return False


def build_archive() -> Path:
    """打本地 tar.gz，排除 node_modules / data / .git 等。"""
    fd, tmp_name = tempfile.mkstemp(prefix="market-deploy-", suffix=".tar.gz")
    os.close(fd)
    path = Path(tmp_name)
    safe_print(f"Packing {ROOT} -> {path.name} ...")
    count = 0
    with tarfile.open(path, "w:gz") as tar:
        for p in ROOT.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT)
            if _should_skip(rel):
                continue
            tar.add(p, arcname=str(rel).replace("\\", "/"))
            count += 1
    size_mb = path.stat().st_size / (1024 * 1024)
    safe_print(f"Archive ready: {count} files, {size_mb:.1f} MB")
    return path


def upload_and_extract(c: paramiko.SSHClient, archive: Path) -> None:
    remote_tar = "/tmp/market-deploy.tar.gz"
    safe_print(f"Upload -> {USER}@{HOST}:{remote_tar}")
    sftp = c.open_sftp()
    try:
        sftp.put(str(archive), remote_tar)
    finally:
        sftp.close()

    # 保留远程 data/ 与 .env，只覆盖代码与 compose 文件
    script = f"""
set -e
mkdir -p {REMOTE_DIR}
cd {REMOTE_DIR}
# 备份已有 .env（若存在）
if [ -f .env ]; then cp -a .env /tmp/market.env.bak; fi
tar -xzf {remote_tar} -C {REMOTE_DIR}
if [ -f /tmp/market.env.bak ]; then mv /tmp/market.env.bak .env; fi
if [ ! -f .env ]; then
  if [ -f backend/.env.example ]; then
    cp backend/.env.example .env
    echo "WARN: created .env from backend/.env.example — please set JWT_SECRET"
  else
    echo "JWT_SECRET=change-me-in-production" > .env
    echo "WARN: created minimal .env — please set JWT_SECRET"
  fi
fi
mkdir -p data
rm -f {remote_tar}
ls -la
"""
    code, _ = run(c, script, timeout=120)
    if code != 0:
        raise SystemExit("upload/extract failed")


def compose_up(c: paramiko.SSHClient) -> None:
    if BUILD:
        code, _ = run(
            c,
            f"cd {REMOTE_DIR} && docker compose build market",
            timeout=1200,
        )
        if code != 0:
            raise SystemExit("compose build failed")

    code, _ = run(
        c,
        f"cd {REMOTE_DIR} && docker compose up -d market",
        timeout=180,
    )
    if code != 0:
        raise SystemExit("compose up failed")


def health_check(c: paramiko.SSHClient) -> None:
    safe_print("Health check...")
    time.sleep(4)
    ok = False
    for _ in range(25):
        _, out = run(
            c,
            "curl -sS -m 3 -o /tmp/mkt.json -w '%{http_code}' "
            "http://127.0.0.1:8093/api/meta/categories || echo fail",
            timeout=30,
        )
        status = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if status.endswith("200"):
            ok = True
            break
        time.sleep(2)

    run(
        c,
        "docker ps --filter name=^market$ --format "
        "'table {{.Names}}\\t{{.Status}}\\t{{.Image}}\\t{{.Ports}}'",
    )
    run(c, "docker inspect market --format 'ImageID={{.Image}} Created={{.Created}}'")
    run(c, "head -c 400 /tmp/mkt.json; echo")
    run(
        c,
        "curl -sS -m 3 -o /dev/null -w 'home:%{http_code}\\n' http://127.0.0.1:8093/",
    )

    if not ok:
        run(c, "docker logs --tail 120 market")
        raise SystemExit("health failed")


def main() -> None:
    archive = build_archive()
    c = None
    try:
        c = connect()
        upload_and_extract(c, archive)
        compose_up(c)
        health_check(c)
        c.close()
        c = None
        safe_print(f"DONE: {PUBLIC_URL}")
    finally:
        if c is not None:
            c.close()
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
