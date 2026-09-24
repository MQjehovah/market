"""能力访问权限的唯一判定来源：部门/角色/用户白名单三门（AND 语义）。

与 visibility.py 分工：visibility 决定"能否看见"，本模块决定"能否订阅/调用"。
"""

from app.models import Capability, User


def _norm(values) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def capability_access_ok(cap: Capability, user: User | None, *, joined: bool = False) -> bool:
    """订阅与运行共用的权限谓词（不含 visibility/status 校验）。"""
    if user is None:
        return False
    if user.role == "admin" or cap.author_id == user.id:
        return True
    if (cap.access_policy or "open") == "admin_only":
        return False
    depts = _norm(cap.allowed_departments)
    roles = _norm(cap.allowed_roles)
    users = _norm(cap.allowed_users)
    if not (depts or roles or users):
        return (cap.access_policy or "open") == "open"
    if depts and (user.department or "").strip() not in depts:
        return False
    if roles and user.role not in roles:
        return False
    if users and user.username not in users:
        return False
    return True


def access_deny_reason(cap: Capability, user: User | None) -> str:
    """给 403/按钮提示的人类可读原因。"""
    if user is None:
        return "请先登录"
    if (cap.access_policy or "open") == "admin_only":
        return "该能力仅限管理员"
    depts = _norm(cap.allowed_departments)
    roles = _norm(cap.allowed_roles)
    users = _norm(cap.allowed_users)
    if depts and (user.department or "").strip() not in depts:
        return f"该能力仅限部门：{', '.join(depts)}"
    if roles and user.role not in roles:
        return f"该能力仅限角色：{', '.join(roles)}"
    if users and user.username not in users:
        return "该能力仅限白名单用户"
    return "没有该能力的访问权限"
