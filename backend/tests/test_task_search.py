"""任务意图搜索：打分、关系扩展、分组 API。"""

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, User, UserCapability
from app.services.task_search import score_capability, tokenize_query


def test_tokenize_keeps_phrase_and_bigrams():
    terms = tokenize_query("处理工单")
    assert "处理工单" in terms
    assert "处理" in terms
    assert "工单" in terms
    # 2-gram
    assert "理工" in terms


def test_function_word_bigrams_do_not_match():
    from types import SimpleNamespace

    cap = SimpleNamespace(
        name="subagent",
        category="",
        tags=[],
        description="子代理工具。有的任务可以拆开，不会自动释放。",
        readme_md="通过 session_id 可以复用",
    )
    score, matched = score_capability(cap, tokenize_query("zzz不会有的词xyz"))
    assert score == 0
    assert matched == []


def test_score_prefers_name_over_description():
    from types import SimpleNamespace

    name_hit = SimpleNamespace(
        name="工单分流",
        category="",
        tags=[],
        description="其它",
        readme_md="",
    )
    desc_hit = SimpleNamespace(
        name="其它助手",
        category="",
        tags=[],
        description="用于处理工单的说明",
        readme_md="",
    )
    terms = tokenize_query("工单")
    s_name, _ = score_capability(name_hit, terms)
    s_desc, _ = score_capability(desc_hit, terms)
    assert s_name > s_desc > 0


async def _seed_task_caps():
    async with SessionLocal() as db:
        publisher = await db.scalar(select(User).where(User.username == "publisher"))
        assert publisher is not None
        skill = Capability(
            name="task-search-ticket-skill",
            description="按优先级分流处理工单",
            type="skill",
            version="1.0.0",
            status="published",
            visibility="internal",
            category="客服",
            tags=["工单", "分流"],
            author_id=publisher.id,
            organization=publisher.organization or "",
            readme_md="处理工单的标准流程",
        )
        mcp = Capability(
            name="task-search-kb-mcp",
            description="连接企业知识库检索",
            type="mcp",
            version="1.0.0",
            status="published",
            visibility="internal",
            category="知识库",
            tags=["知识库", "检索"],
            author_id=publisher.id,
            organization=publisher.organization or "",
            readme_md="查知识库相关文档",
        )
        agent = Capability(
            name="task-search-ticket-agent",
            description="客服场景问答助手",
            type="agent",
            version="1.0.0",
            status="published",
            visibility="internal",
            category="客服",
            tags=["客服"],
            author_id=publisher.id,
            organization=publisher.organization or "",
            input_schema={
                "embedded_skills": [
                    {"name": "task-search-ticket-skill", "capability_id": None}
                ],
                "embedded_mcp": [
                    {"name": "task-search-kb-mcp", "capability_id": None}
                ],
            },
        )
        lone_skill = Capability(
            name="task-search-lone-skill",
            description="独立的工单分类技能",
            type="skill",
            version="1.0.0",
            status="published",
            visibility="internal",
            category="客服",
            tags=["工单"],
            author_id=publisher.id,
            organization=publisher.organization or "",
        )
        db.add_all([skill, mcp, agent, lone_skill])
        await db.commit()
        return {
            "skill": skill.name,
            "mcp": mcp.name,
            "agent": agent.name,
            "lone": lone_skill.name,
        }


@pytest.mark.asyncio
async def test_task_search_groups_agent_and_skills(client):
    names = await _seed_task_caps()

    r = await client.get("/api/capabilities/task-search", params={"q": "处理工单"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["q"] == "处理工单"
    assert body["terms"]

    agent_names = {x["name"] for x in body["agents"]}
    skill_names = {x["name"] for x in body["skills"]}
    assert names["agent"] in agent_names
    assert names["skill"] in skill_names or names["lone"] in skill_names
    # 助手与技能同现
    assert body["agents"] and body["skills"]


@pytest.mark.asyncio
async def test_task_search_connector_terms_fill_mcps(client):
    await _seed_task_caps()

    r = await client.get("/api/capabilities/task-search", params={"q": "查知识库"})
    assert r.status_code == 200, r.text
    body = r.json()
    mcp_names = {x["name"] for x in body["mcps"]}
    assert "task-search-kb-mcp" in mcp_names
    assert body["mcps"]


@pytest.mark.asyncio
async def test_task_search_empty_q(client):
    r = await client.get("/api/capabilities/task-search", params={"q": "  "})
    assert r.status_code == 200
    body = r.json()
    assert body["agents"] == []
    assert body["skills"] == []
    assert body["mcps"] == []


@pytest.mark.asyncio
async def test_task_search_marks_joined_for_subscription(client, user_headers):
    """joined 标记: 未加入=false; 加入「我的能力」后对应命中=true(按名跨版本)。"""
    names = await _seed_task_caps()

    r = await client.get("/api/capabilities/task-search",
                         params={"q": "处理工单"}, headers=user_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    hits = [*body["agents"], *body["skills"], *body["mcps"]]
    assert hits and all(h["joined"] is False for h in hits)

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == "user"))
        cap = await db.scalar(select(Capability).where(Capability.name == names["skill"]))
        db.add(UserCapability(user_id=user.id, capability_id=cap.id))
        await db.commit()

    r = await client.get("/api/capabilities/task-search",
                         params={"q": "处理工单"}, headers=user_headers)
    body = r.json()
    by_name = {x["name"]: x for grp in ("agents", "skills", "mcps", "others", "plugins")
               for x in body[grp]}
    assert by_name[names["skill"]]["joined"] is True
    assert by_name[names["agent"]]["joined"] is False
