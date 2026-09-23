"""种子数据：初始账号 + 各市场示例能力，便于首次体验。"""

import logging
import secrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.config import get_settings
from app.core.env_guard import WEAK_VALUES, is_production
from app.models import Capability, User

logger = logging.getLogger(__name__)


def _resolve_seed_password(value: str | None, env_name: str, role_label: str) -> str:
    """种子口令的统一解析规则。

    已显式配置且非弱值则沿用;生产缺省/弱值直接拒绝(守卫已在导入 config 时拦截,
    这里兜底);开发缺省/弱值时随机生成并只打印一次,绝不写入固定口令。
    """
    configured = (value or "").strip()
    if configured and configured not in WEAK_VALUES:
        return configured
    if is_production():
        raise RuntimeError(
            f"环境变量 {env_name} 未配置或仍为不安全的默认值,生产环境拒绝创建{role_label}"
        )
    generated = secrets.token_urlsafe(12)
    logger.warning(
        "%s 未配置或为弱值,已生成一次性%s口令:%s(仅本次打印)", env_name, role_label, generated
    )
    return generated


def resolve_seed_admin_password() -> str:
    """解析种子管理员口令(规则见 _resolve_seed_password)。"""
    return _resolve_seed_password(
        get_settings().seed_admin_password, "SEED_ADMIN_PASSWORD", "管理员"
    )


def resolve_seed_publisher_password() -> str:
    """解析种子发布者口令(规则见 _resolve_seed_password)。"""
    return _resolve_seed_password(
        get_settings().seed_publisher_password, "SEED_PUBLISHER_PASSWORD", "发布者"
    )


def resolve_seed_user_password() -> str:
    """解析种子普通用户口令(规则见 _resolve_seed_password)。"""
    return _resolve_seed_password(
        get_settings().seed_user_password, "SEED_USER_PASSWORD", "普通用户"
    )


async def seed_if_empty(db: AsyncSession) -> None:
    count = (await db.scalar(select(func.count()).select_from(User))) or 0
    if count > 0:
        return

    settings = get_settings()
    admin = User(
        username=settings.seed_admin_username,
        email=settings.seed_admin_email,
        password_hash=hash_password(resolve_seed_admin_password()),
        display_name="市场管理员",
        role="admin",
        organization="平台部",
    )
    publisher = User(
        username="publisher",
        email="publisher@example.com",
        password_hash=hash_password(resolve_seed_publisher_password()),
        display_name="能力发布者",
        role="publisher",
        organization="数字中台部",
        team="中台团队",
    )
    user = User(
        username="user",
        email="user@example.com",
        password_hash=hash_password(resolve_seed_user_password()),
        display_name="普通用户",
        role="user",
        organization="业务部",
        team="业务团队",
    )
    db.add_all([admin, publisher, user])
    await db.flush()

    demo = [
        Capability(
            name="数字中台分析师",
            description="面向业务数据分析场景的 Agent 角色：可完成销售报表生成、趋势分析、异常归因，并输出可读的分析结论。",
            type="agent",
            version="1.2.0",
            status="published",
            category="业务分析类",
            tags=["数据分析", "报表", "中台"],
            visibility="internal",
            author_id=publisher.id,
            organization=publisher.organization,
            usage_count=18,
        ),
        Capability(
            name="文件哈希计算",
            description="计算文件的 MD5 / SHA-256 哈希，用于文件完整性校验。",
            type="tool",
            version="1.0.0",
            status="published",
            category="文件操作",
            tags=["只读", "无状态", "低风险"],
            visibility="internal",
            author_id=publisher.id,
            organization=publisher.organization,
            usage_count=42,
        ),
        Capability(
            name="TDD 开发工作流",
            description="测试驱动开发技能：红灯-绿灯-重构三步循环，含需求澄清与测试用例编写指引。",
            type="skill",
            version="0.2.0",
            status="published",
            category="开发流程",
            tags=["TDD", "测试", "工程实践"],
            visibility="internal",
            author_id=publisher.id,
            organization=publisher.organization,
            usage_count=9,
        ),
        Capability(
            name="PostgreSQL 连接器",
            description="标准 PostgreSQL MCP Server 连接配置，支持 SQL 查询、表结构查看与数据导出。",
            type="mcp",
            version="2.0.0",
            status="published",
            category="数据库连接",
            tags=["数据库", "SQL"],
            visibility="internal",
            author_id=admin.id,
            organization=admin.organization,
            usage_count=27,
        ),
        Capability(
            name="代码审查助手",
            description="基于团队规范进行代码审查的 Agent：检查可读性、边界条件与潜在缺陷，输出评审意见。",
            type="agent",
            version="0.5.0",
            status="reviewing",
            category="开发助手类",
            tags=["代码审查", "质量"],
            visibility="team",
            author_id=publisher.id,
            organization=publisher.organization,
        ),
        Capability(
            name="会议纪要模板",
            description="自动整理会议录音/文本为结构化纪要：结论、行动项、负责人与截止时间。",
            type="skill",
            version="0.1.0",
            status="draft",
            category="通用助手类",
            tags=["会议", "效率"],
            visibility="private",
            author_id=publisher.id,
            organization=publisher.organization,
        ),
    ]
    db.add_all(demo)
    await db.commit()
