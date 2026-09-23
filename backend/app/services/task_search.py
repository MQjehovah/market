"""按要办的事搜索：关键词打分 + 依赖/used_by 关系扩展。

不做 LLM 语义检索；结果按助手 / 技能 / 连接器 / 安装包分组。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability
from app.services.agent_metadata import find_used_by
from app.services.capabilities import parse_semver, to_capability_out

# 各组结果上限
_GROUP_LIMIT = 8
# 关系扩展相对直接命中的降权
_EXPAND_FACTOR = 0.55

_PUNCT = re.compile(r"[\s,，。.!！?？;；:：、|/\\]+")


def tokenize_query(q: str) -> list[str]:
    """整句 + 空格/标点切分 + 中文 2-gram，去重保序。"""
    text = (q or "").strip()
    if not text:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        t = t.strip().lower()
        if len(t) < 2 or t in seen:
            return
        seen.add(t)
        terms.append(t)

    add(text)
    for part in _PUNCT.split(text):
        add(part)
        # 连续汉字片段做 2-gram
        chars = [c for c in part if "\u4e00" <= c <= "\u9fff"]
        if len(chars) >= 2:
            joined = "".join(chars)
            for i in range(len(joined) - 1):
                add(joined[i : i + 2])
    return terms


def _field_haystacks(cap: Capability) -> dict[str, str]:
    tags = " ".join(str(t) for t in (cap.tags or []))
    readme = (getattr(cap, "readme_md", None) or "")[:2000]
    return {
        "name": (cap.name or "").lower(),
        "category": (cap.category or "").lower(),
        "tags": tags.lower(),
        "description": (cap.description or "").lower(),
        "readme": readme.lower(),
    }


def score_capability(cap: Capability, terms: list[str]) -> tuple[float, list[str]]:
    """返回 (分数, 命中词)。分数为 0 表示未命中。"""
    if not terms:
        return 0.0, []
    fields = _field_haystacks(cap)
    weights = {
        "name": 12.0,
        "category": 6.0,
        "tags": 6.0,
        "description": 3.0,
        "readme": 1.5,
    }
    total = 0.0
    matched: list[str] = []
    matched_set: set[str] = set()
    for term in terms:
        hit = False
        for key, hay in fields.items():
            if term in hay:
                total += weights[key]
                hit = True
        if hit and term not in matched_set:
            matched_set.add(term)
            matched.append(term)
    return total, matched


def _latest_by_key(caps: list[Capability]) -> dict[tuple[str, str], Capability]:
    latest: dict[tuple[str, str], Capability] = {}
    for cap in caps:
        if cap.status not in ("published", "deprecated"):
            continue
        if "plugin-component" in (cap.tags or []):
            continue
        key = (cap.name, cap.type)
        cur = latest.get(key)
        if cur is None or parse_semver(cap.version) > parse_semver(cur.version):
            latest[key] = cap
    return latest


def _embedded_names(cap: Capability, key: str) -> list[str]:
    schema = cap.input_schema or {}
    items = schema.get(key) or []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            out.append(str(item["name"]).strip())
    return out


def _plugin_component_names(cap: Capability, type_: str) -> list[str]:
    comps = (cap.input_schema or {}).get("components") or []
    out: list[str] = []
    for item in comps:
        if not isinstance(item, dict):
            continue
        if item.get("type") != type_:
            continue
        name = str(item.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def _group_key(cap_type: str) -> str:
    if cap_type == "agent":
        return "agents"
    if cap_type == "skill":
        return "skills"
    if cap_type == "mcp":
        return "mcps"
    if cap_type == "plugin":
        return "plugins"
    return "others"


async def task_search(
    db: AsyncSession,
    caps: list[Capability],
    q: str,
    *,
    group_limit: int = _GROUP_LIMIT,
) -> dict[str, Any]:
    """对可见能力做任务意图搜索，返回分组结果。"""
    query = (q or "").strip()
    terms = tokenize_query(query)
    latest = _latest_by_key(caps)
    by_id = {c.id: c for c in latest.values()}
    by_name_type: dict[tuple[str, str], Capability] = {
        (c.name.lower(), c.type): c for c in latest.values()
    }

    # id -> (score, matched_terms)
    scored: dict[str, tuple[float, list[str]]] = {}

    def bump(cap: Capability, score: float, matched: list[str]) -> None:
        if score <= 0 or cap.id not in by_id:
            return
        prev, prev_terms = scored.get(cap.id, (0.0, []))
        if score < prev:
            return
        seen = set(prev_terms)
        merged = list(prev_terms)
        for t in matched:
            if t not in seen:
                seen.add(t)
                merged.append(t)
        scored[cap.id] = (score, merged)

    # 1) 直接打分
    for cap in latest.values():
        score, matched = score_capability(cap, terms)
        bump(cap, score, matched)

    # 2) 关系扩展（基于当前直接命中快照）
    direct_ids = [cid for cid, (s, _) in scored.items() if s > 0]
    for cid in direct_ids:
        cap = by_id.get(cid)
        if cap is None:
            continue
        base_score, base_terms = scored[cid]
        expand_score = base_score * _EXPAND_FACTOR

        if cap.type in ("skill", "mcp"):
            used = await find_used_by(db, cap)
            for ref in used:
                if ref.get("type") != "agent":
                    continue
                other = by_id.get(str(ref.get("capability_id") or ""))
                if other is None:
                    other = by_name_type.get((str(ref.get("name") or "").lower(), "agent"))
                if other is not None:
                    bump(other, expand_score, base_terms)

        if cap.type == "agent":
            for name in _embedded_names(cap, "embedded_skills"):
                other = by_name_type.get((name.lower(), "skill"))
                if other is not None:
                    bump(other, expand_score, base_terms)
            for name in _embedded_names(cap, "embedded_mcp"):
                other = by_name_type.get((name.lower(), "mcp"))
                if other is not None:
                    bump(other, expand_score, base_terms)

        if cap.type == "plugin":
            for name in _plugin_component_names(cap, "skill"):
                other = by_name_type.get((name.lower(), "skill"))
                if other is not None:
                    bump(other, expand_score, base_terms)
            for name in _plugin_component_names(cap, "mcp"):
                other = by_name_type.get((name.lower(), "mcp"))
                if other is not None:
                    bump(other, expand_score, base_terms)
            for name in _plugin_component_names(cap, "agent"):
                other = by_name_type.get((name.lower(), "agent"))
                if other is not None:
                    bump(other, expand_score, base_terms)

    groups: dict[str, list[tuple[float, Capability, list[str]]]] = {
        "agents": [],
        "skills": [],
        "mcps": [],
        "plugins": [],
        "others": [],
    }
    for cid, (score, matched) in scored.items():
        cap = by_id[cid]
        groups[_group_key(cap.type)].append((score, cap, matched))

    def pack(rows: list[tuple[float, Capability, list[str]]]) -> list[dict[str, Any]]:
        rows.sort(key=lambda x: (-x[0], x[1].name))
        out: list[dict[str, Any]] = []
        for score, cap, matched in rows[:group_limit]:
            data = to_capability_out(
                cap, author_name=cap.author.username if cap.author else ""
            ).model_dump()
            data["match_score"] = round(score, 2)
            data["matched_terms"] = matched
            out.append(data)
        return out

    return {
        "q": query,
        "terms": terms,
        "agents": pack(groups["agents"]),
        "skills": pack(groups["skills"]),
        "mcps": pack(groups["mcps"]),
        "plugins": pack(groups["plugins"]),
        "others": pack(groups["others"]),
    }
