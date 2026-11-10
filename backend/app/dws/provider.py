"""DWS 对能力认证 provider 接口的适配。

通用路由只认 config_ui.provider，不认识 DWS；本文件是唯一把二者接起来的地方。
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.routers import dws as dws_api


class DwsAuthProvider:
    name = "dws"

    def page_url(self, cap) -> str:
        base = get_settings().public_base_url.rstrip("/")
        return f"{base}/capabilities/{cap.id}?tab=credentials"

    def _with_page(self, data: dict[str, Any], cap) -> dict[str, Any]:
        page = self.page_url(cap)
        data = dict(data or {})
        data["capability_id"] = cap.id
        data["page_url"] = page
        # login_url 是第三方授权链接时保留；没有时才回退到能力配置页
        if not data.get("login_url"):
            data["login_url"] = page
        return data

    async def account(self, user, db, cap, refresh: bool = False):
        return self._with_page(await dws_api.get_account(user=user, db=db, refresh=refresh), cap)

    async def workspace(self, user, db, cap):
        return self._with_page(await dws_api.get_workspace(user=user, db=db), cap)

    async def start(self, user, db, cap):
        return self._with_page(await dws_api.start_login(user=user, db=db), cap)

    async def poll(self, user, db, cap, session_id: str):
        return self._with_page(await dws_api.poll_login(session_id=session_id, user=user, db=db), cap)

    async def logout(self, user, db, cap, profile: str = ""):
        return self._with_page(await dws_api.logout(user=user, db=db, profile=profile), cap)

    async def check(self, user, db, cap):
        return self._with_page(await dws_api.check(user=user, db=db), cap)

    async def schema(self, user, db, cap, path: str, query: str):
        return await dws_api.schema(user=user, db=db, path=path, query=query)

    async def health(self, user, db, cap):
        return await dws_api.health(user=user, db=db)

    async def calls(self, user, db, cap, limit: int):
        return await dws_api.calls(user=user, db=db, limit=limit)

    async def upload(self, user, db, cap, file):
        return await dws_api.upload_file(user=user, db=db, file=file)

    async def download(self, user, db, cap, file_id: str):
        return await dws_api.download_file(file_id=file_id, user=user, db=db)
