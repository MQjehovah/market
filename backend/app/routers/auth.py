import time
import urllib.parse
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select

from app.auth import CurrentUser, DbSession, _resolve_sso_user, create_access_token, hash_password, verify_password
from app.config import get_settings
from app.core import sso_auth
from app.models import User
from app.schemas import ChangePasswordRequest, MessageOut, TokenOut, UserLogin, UserOut
from app.services.install_policy import ensure_default_on_joins

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_failures: dict[str, deque] = defaultdict(deque)
_LOCK_AFTER = 5
_LOCK_WINDOW = 15 * 60


@router.post("/login", response_model=TokenOut)
async def login(data: UserLogin, request: Request, db: DbSession):
    client = request.client.host if request.client else "unknown"
    key = f"{data.username}:{client}"
    now = time.time()
    failures = _login_failures[key]
    while failures and failures[0] < now - _LOCK_WINDOW:
        failures.popleft()
    if len(failures) >= _LOCK_AFTER:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "登录失败次数过多，请 15 分钟后再试",
        )
    user = await db.scalar(select(User).where(User.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash):
        failures.append(now)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    _login_failures.pop(key, None)
    await ensure_default_on_joins(db, user)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


@router.get("/sso/start")
async def sso_start():
    """生成一次性 state 并 302 跳转到 SSO authorize 页。"""
    if not sso_auth.is_login_configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SSO not enabled")
    state = sso_auth.new_state()
    try:
        url = sso_auth.build_authorize_url(state)
    except sso_auth.SsoAuthError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e
    return RedirectResponse(url, status_code=302)


def _sso_login_redirect(error: str = "") -> RedirectResponse:
    """SSO 出错时回到前端登录页并带 error 文案，不把 JSON 错误丢给浏览器。

    与成功路径同样把参数拼在 redirect_target 之后，前端 LoginView 读 route.query.error 展示。
    """
    target = (get_settings().sso_redirect_target or "/login").strip() or "/login"
    if error:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}error={urllib.parse.quote(error)}"
    return RedirectResponse(target, status_code=302)


@router.get("/oidc/callback")
async def oidc_callback(db: DbSession, code: str = "", state: str = ""):
    """SSO 回调:code→id_token→校验→查/建用户→签本系统 JWT→302 回前端。"""
    if not sso_auth.is_login_configured():
        return _sso_login_redirect("SSO 登录未启用")
    if not code or not state:
        return _sso_login_redirect("SSO 回调参数缺失")
    if not sso_auth.validate_state(state):
        return _sso_login_redirect("SSO 登录状态已失效，请重试")
    try:
        id_token = sso_auth.exchange_code(code)
    except sso_auth.SsoAuthError as e:
        return _sso_login_redirect(f"SSO 登录失败：{e}")
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user = await _resolve_sso_user(db, id_token, credentials_exc)
    except HTTPException as e:
        return _sso_login_redirect(str(e.detail))
    await ensure_default_on_joins(db, user)
    token = create_access_token(user)
    target = (get_settings().sso_redirect_target or "/login").strip() or "/login"
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}sso_token={urllib.parse.quote(token)}", status_code=302)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=MessageOut)
async def change_password(data: ChangePasswordRequest, db: DbSession, user: CurrentUser):
    """用户自助修改密码（需验证原密码）。"""
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "原密码不正确")
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return MessageOut(message="密码已修改，请重新登录")
