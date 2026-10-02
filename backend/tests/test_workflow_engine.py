"""工作流引擎（Dify 对齐）单测：变量/新节点/校验。"""
import pytest

from app.services.workflows import _canon_type, _execute_node, render_value, validate_definition


def test_render_value_basic_and_dify_syntax():
    ctx = {"input": {"a": 1}, "n1": {"output": [1, 2, 3]}}
    assert render_value("${input.a}", ctx) == 1
    assert render_value("{{#input.a#}}", ctx) == 1
    assert render_value("x=${input.a}", ctx) == "x=1"
    # 整串恰为一个引用 → 保留原始类型
    assert render_value("{{#n1.output#}}", ctx) == [1, 2, 3]
    assert render_value("${input.missing}", ctx) is None


def test_validate_accepts_new_types_and_dify_aliases():
    wf = {
        "nodes": [
            {"id": "s", "type": "start"},
            {"id": "c", "type": "question-classifier"},
            {"id": "k", "type": "knowledge-retrieval"},
            {"id": "e", "type": "end"},
            {"id": "x", "type": "code"},
        ],
        "edges": [{"from": "s", "to": "c"}, {"from": "c", "to": "k"}, {"from": "k", "to": "e"}],
    }
    order = validate_definition(wf)
    assert set(order) == {"s", "c", "k", "e", "x"}
    assert _canon_type("question-classifier") == "question_classifier"
    assert _canon_type("if-else") == "if_else"


def test_validate_rejects_unknown_type_and_market_without_capability():
    with pytest.raises(Exception):
        validate_definition({"nodes": [{"id": "x", "type": "nope"}]})
    with pytest.raises(Exception):
        validate_definition({"nodes": [{"id": "x", "type": "tool"}]})


@pytest.mark.asyncio
async def test_pure_nodes_template_ifelse_aggregator_list_answer():
    ctx = {"input": {"query": "hello"}, "n": {"items": [3, 1, 2]}}
    out = await _execute_node(None, None, {"id": "t", "type": "template", "params": {"template": "hi ${input.query}"}}, ctx)
    assert out["output"]["text"] == "hi hello"

    out = await _execute_node(
        None, None,
        {"id": "if", "type": "if-else", "params": {"conditions": [{"left": "${input.query}", "operator": "eq", "right": "hello"}]}},
        ctx,
    )
    assert out["output"]["result"] is True
    assert out["branch"] == "true"

    out = await _execute_node(
        None, None,
        {"id": "agg", "type": "variable-aggregator", "params": {"variables": ["${a.x}", "${b.y}"], "mode": "first"}},
        {"a": {}, "b": {"y": "B"}},
    )
    assert out["output"]["result"] == "B"

    out = await _execute_node(None, None, {"id": "l", "type": "list-operator", "params": {"list": "${n.items}", "operation": "sort"}}, ctx)
    assert out["output"]["result"] == [1, 2, 3]

    out = await _execute_node(None, None, {"id": "a", "type": "answer", "params": {"answer": "done ${input.query}"}}, ctx)
    assert out["output"]["answer"] == "done hello"


@pytest.mark.asyncio
async def test_variable_assigner_and_doc_extractor():
    ctx = {"input": {}}
    out = await _execute_node(None, None, {"id": "v", "type": "variable-assigner", "params": {"assignments": {"step": "1"}}}, ctx)
    assert out["output"]["assigned"] == {"step": "1"}
    assert ctx["conversation"]["step"] == "1"

    out = await _execute_node(None, None, {"id": "d", "type": "doc-extractor", "params": {"text": '{"a": 1}'}}, {})
    assert out["output"]["json"] == {"a": 1}


@pytest.mark.asyncio
async def test_classifier_and_extractor_with_stubbed_llm(monkeypatch):
    import app.services.workflows as wf

    async def fake_chat(params, ctx):
        prompt = params.get("prompt", "")
        if "类别" in prompt:
            return "```json\n{\"id\": \"p1\"}\n```"
        return "{\"foo\": \"bar\"}"

    monkeypatch.setattr(wf, "_llm_chat", fake_chat)

    out = await wf._execute_node(
        None, None,
        {"id": "c", "type": "question-classifier", "params": {"query": "x", "classes": [{"id": "p1", "name": "P1"}, {"id": "p2", "name": "P2"}]}},
        {},
    )
    assert out["branch"] == "p1"
    assert out["output"]["class_name"] == "P1"

    out = await wf._execute_node(
        None, None,
        {"id": "pe", "type": "parameter-extractor", "params": {"query": "x", "parameters": [{"name": "foo"}]}},
        {},
    )
    assert out["output"]["params"] == {"foo": "bar"}


def test_sample_it_alert_workflow_is_valid():
    from app.services.workflow_samples import IT_ALERT_WORKFLOW

    order = validate_definition(IT_ALERT_WORKFLOW)
    assert set(order) == {
        "start", "kb", "cls", "ledger", "diag", "canfix", "ticket", "notify", "agg", "end",
    }


@pytest.mark.asyncio
async def test_iteration_node_runs_body():
    ctx = {"input": {"list": [1, 2, 3]}}
    node = {
        "id": "it",
        "type": "iteration",
        "params": {"items": "${input.list}"},
        "body": [{"id": "b", "type": "template", "params": {"template": "item=${item}"}}],
    }
    out = await _execute_node(None, None, node, ctx)
    assert out["output"]["count"] == 3
    assert out["output"]["items"][1]["text"] == "item=2"
