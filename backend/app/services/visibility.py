"""能力可见性的唯一判定来源（browse SQL 过滤与解析/权限 Python 判定共用）。

规则（与门户 `_visibility_where` 的既有语义一致）：

- ``internal`` / ``public``：所有访问者（含匿名）可见；
- ``private``：仅作者可见；
- ``team``：作者与访问者同属一个非空团队时可见；
- ``admin``：可见全部；
- 作者本人始终可见自己的任意可见性能力。

`is_capability_visible` 是 Python 侧权威谓词，`visibility_condition` 是等价的
SQL 条件构建器；两者由 `tests/test_marketplace.py` 的等价性用例锁定，避免再次漂移。
"""

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models import Capability, User


def is_capability_visible(cap: Capability, user: User | None) -> bool:
    """单条能力（Python 判定）。与 ``visibility_condition`` 等价。"""
    if cap.visibility in ("internal", "public"):
        return True
    if user is None:
        return False
    if user.role == "admin":
        return True
    if cap.author_id == user.id:
        return True
    if cap.visibility == "team":
        author = cap.author
        return bool(user.team) and author is not None and author.team == user.team
    return False


def visibility_condition(user: User | None) -> ColumnElement[bool] | None:
    """SQL 过滤条件；admin 返回 ``None`` 表示不限制。与 ``is_capability_visible`` 等价。"""
    if user is not None and user.role == "admin":
        return None
    clauses = [Capability.visibility.in_(("internal", "public"))]
    if user is not None:
        clauses.append(Capability.author_id == user.id)
        if user.team:
            clauses.append(
                and_(
                    Capability.visibility == "team",
                    Capability.author.has(User.team == user.team),
                )
            )
    return or_(*clauses)
