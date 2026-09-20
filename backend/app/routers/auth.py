import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import or_, select

from app.auth import CurrentUser, DbSession, create_access_token, hash_password, verify_password
from app.models import User
from app.schemas import ChangePasswordRequest, MessageOut, TokenOut, UserLogin, UserOut, UserRegister
from app.services.install_policy import ensure_default_on_joins

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_failures: dict[str, deque] = defaultdict(deque)
_LOCK_AFTER = 5
_LOCK_WINDOW = 15 * 60


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(data: UserRegister, db: DbSession):
    exists = await db.scalar(
        select(User.id).where(or_(User.username == data.username, User.email == data.email))
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名或邮箱已被使用")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        organization=data.organization,
        team=data.team,
        role="user",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await ensure_default_on_joins(db, user)
    return TokenOut(access_token=create_access_token(user), user=UserOut.model_validate(user))


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
