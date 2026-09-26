"""JWT 认证与密码哈希（HS256 老轨 + SSO/OIDC 新轨双轨鉴权）。"""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.sso_auth import SsoAuthError, verify_sso_token
from app.database import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user: User) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_local_token(token: str) -> dict | None:
    """解本地 HS256 token；签名或格式无效返回 None（由调用方决定是否走 SSO/服务令牌轨）。"""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """双轨鉴权：先认自家 HS256 token，失败再试 SSO/OIDC 签发的 RS256 token。

    轨1（HS256）保持原逻辑：sub=user UUID，不存在/已禁用 -> 401，零漂移。
    轨2（SSO）sub=工号：用户不存在自动建号（role=user，最小角色）；
    命中但已禁用 -> 403。两轨返回同类型 User 对象。
    """
    credentials_exc = _credentials_exception()
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        # 轨1失败：先试服务令牌，再试 SSO
        svc_user = await _resolve_service_token_user(db, token)
        if svc_user is not None:
            return svc_user
        return await _resolve_sso_user(db, token, credentials_exc)
    user_id = payload.get("sub")
    if not user_id:
        raise credentials_exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_exc
    return user


async def resolve_user_by_bearer(db: AsyncSession, token: str) -> tuple[User, str] | None:
    """非依赖注入场景（MCP 网关等纯 ASGI）的 Bearer 解析，仅用于识别身份来源。

    顺序与 get_current_user 一致：本地 HS256 JWT → 服务令牌 → SSO。
    成功返回 (User, "jwt" | "service_token" | "sso")；token 无效、用户不存在/
    已禁用返回 None，由调用方决定回退匿名还是 401。
    """
    payload = decode_local_token(token)
    if payload is not None:
        user_id = payload.get("sub")
        if not user_id:
            return None
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user, "jwt"
    svc_user = await _resolve_service_token_user(db, token)
    if svc_user is not None:
        return svc_user, "service_token"
    try:
        user = await _resolve_sso_user(db, token, _credentials_exception())
    except HTTPException:
        return None
    return user, "sso"


async def _resolve_service_token_user(db: AsyncSession, token: str) -> User | None:
    from app.services.service_tokens import resolve_service_token

    return await resolve_service_token(db, token)


async def _resolve_sso_user(
    db: AsyncSession,
    token: str,
    credentials_exc: HTTPException,
    audience: str | None = None,
) -> User:
    """SSO 轨：校验 RS256 token 后按 username=工号 查/建 User。

    校验失败 -> 401（与轨1同文案）；命中已禁用用户 -> 403。
    audience 用于登录回调轨校验本系统自己的 client_id（资源轨缺省用 sso_audience）。
    """
    try:
        claims = verify_sso_token(token, audience=audience)
    except SsoAuthError:
        raise credentials_exc
    username = str(claims["sub"])
    raw_email = (claims.get("email") or "").strip() or None
    claim_email = raw_email.lower() if raw_email else None

    # 身份识别顺序: 工号 -> 邮箱 -> 新建。
    # 以「工号(username=sub)」为主键：与 agent 侧一致(agent 恒以工号为 name/act-as)，
    # 避免一人多账号时 SSO 被邮箱优先映射到旧账号导致 web/桌面 订阅集不一致；
    # 工号未命中再退回邮箱(兼容历史自建/邮箱注册账号)，最后才新建。
    user = None
    user = await db.scalar(select(User).where(User.username == username))
    if user is None and claim_email:
        user = await db.scalar(select(User).where(func.lower(User.email) == claim_email))
    if user is None:
        user = _new_sso_user(username, claims)
        # 新开户标记：仅在账户创建时开通一次默认能力（登录不再重复开通，避免「移出后被加回」）
        setattr(user, "_provision_defaults", True)
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            # 并发建号竞态:username 唯一约束被其他请求抢建,回滚后回查兜底
            await db.rollback()
            user = await db.scalar(select(User).where(User.username == username))
            if user is None and claim_email:
                # 也可能是邮箱撞了并发/已存在账号, 再按邮箱回查一次
                user = await db.scalar(select(User).where(func.lower(User.email) == claim_email))
            if user is None:
                # 回查仍无说明冲突非 username(病态场景:疑似 email 撞已存在账号),
                # 抛友好 409 而非裸 500(IntegrityError 不属于 HTTPException)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="用户创建冲突,请联系管理员",
                )
        else:
            await db.refresh(user)

    # 命中已有账号时以 SSO 为权威源回写邮箱与姓名。
    # 邮箱在前面已查过同值账号, 因此这里改写不会撞唯一约束。
    changed = False
    if raw_email and raw_email != (user.email or ""):
        user.email = raw_email
        changed = True
    claim_name = (claims.get("name") or "").strip()
    if claim_name and user.display_name != claim_name:
        user.display_name = claim_name
        changed = True
    # 部门以 SSO 为权威源；claim 为空时保留管理员手工填写的值
    claim_dept = (claims.get("dept") or "").strip()
    if claim_dept and (user.department or "") != claim_dept:
        user.department = claim_dept
        changed = True
    # 角色: 以 SSO roles 为权威源, 但仅"升权"(避免误将手工管理员降级, 防锁死)。
    # 代授权(subject)需要真实角色, 否则管理员在 market 侧会退化为 user 而无运行时准入。
    claim_role = _sso_role(claims)
    if _ROLE_RANK.get(claim_role, 0) > _ROLE_RANK.get(user.role, 0):
        user.role = claim_role
        changed = True
    if changed:
        await db.commit()
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用",
        )
    return user


# market 角色优先级(仅升权): user < publisher < admin
_ROLE_RANK = {"user": 0, "publisher": 1, "admin": 2}


def _sso_role(claims: dict) -> str:
    """把 SSO ``roles`` claim 映射为 market 角色(user/publisher/admin)。

    支持 list[str] 或逗号分隔字符串; 取最高优先级角色; 无匹配回退 user。
    """
    raw = claims.get("roles")
    if isinstance(raw, str):
        roles = [r.strip().lower() for r in raw.split(",") if r.strip()]
    elif isinstance(raw, list):
        roles = [str(r).strip().lower() for r in raw if str(r).strip()]
    else:
        roles = []
    if "admin" in roles:
        return "admin"
    if "publisher" in roles:
        return "publisher"
    return "user"


def _new_sso_user(username: str, claims: dict) -> User:
    """按 SSO claims 建本地用户：role 由 SSO roles 映射(user/publisher/admin)。

    email 为 NOT NULL UNIQUE，claims 缺省时用派生自唯一 username 的占位邮箱，
    避免空串撞唯一索引；password_hash 置随机不可登录占位值（SSO 用户走免密）。
    """
    return User(
        username=username,
        email=claims.get("email") or f"{username}@sso.local",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        display_name=claims.get("name") or claims.get("display_name") or username,
        role=_sso_role(claims),
        department=(claims.get("dept") or "").strip(),
        is_active=True,
    )


async def get_current_user_optional(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    if not token:
        return None
    try:
        return await get_current_user(token, db)
    except HTTPException:
        return None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
