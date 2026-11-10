"""DWS workspace 目录布局与权限。"""

import os
from pathlib import Path

from app.dws.workspace import DwsWorkspace


def test_dws_workspace_creates_private_user_dirs(tmp_path):
    ws = DwsWorkspace(tmp_path)
    base = ws.ensure("u-1")

    assert base == tmp_path.resolve() / "u-1" / "dws"
    assert (base / "config").is_dir()
    assert (base / "keychain").is_dir()
    assert (base / "files").is_dir()
    if os.name != "nt":  # Windows 不执行 POSIX 权限位
        for name in ("config", "keychain", "files"):
            assert (base / name).stat().st_mode & 0o777 == 0o700


def test_dws_workspace_rejects_path_like_user_id(tmp_path):
    ws = DwsWorkspace(tmp_path)
    for bad in ("", "..", "../x", "a/b", "a\\b"):
        try:
            ws.user_dir(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝非法 user_id: {bad!r}")


def test_dws_workspace_env_uses_user_dirs(tmp_path):
    ws = DwsWorkspace(tmp_path)
    env = ws.env("u-2")
    assert Path(env["DWS_CONFIG_DIR"]).parts[-3:] == ("u-2", "dws", "config")
    assert Path(env["DWS_KEYCHAIN_DIR"]).parts[-3:] == ("u-2", "dws", "keychain")
