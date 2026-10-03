"""内置示例工作流（Dify 对齐）。可作为组装/导入模板与文档用途。

场景：IT 告警 → 检索运维手册 → 严重度分类 → 查设备台账 → 专家诊断 → 自动处置/建单 → 钉钉通知。
节点类型/变量语法对齐 Dify（连字符节点名 + {{#node.field#}}）。
"""

IT_ALERT_WORKFLOW: dict = {
    "name": "IT告警处置",
    "description": "告警接入 → 知识库检索 → 严重度分流 → 设备台账 → 专家诊断 → 建单/通知（钉钉）",
    "on_error": "continue",
    "timeout_seconds": 600,
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "params": {"fields": ["alert_id", "host", "severity", "message", "source"]},
            "position": {"x": 40, "y": 200},
        },
        {
            "id": "kb",
            "type": "knowledge-retrieval",
            "params": {"query": "{{#start.host#}} {{#start.message#}}", "top_k": 5},
            "position": {"x": 260, "y": 200},
        },
        {
            "id": "cls",
            "type": "question-classifier",
            "params": {
                "query": "{{#start.message#}}",
                "classes": [
                    {"id": "p1", "name": "P1", "description": "严重/影响生产"},
                    {"id": "p2", "name": "P2", "description": "一般故障"},
                    {"id": "p3", "name": "P3", "description": "轻微/咨询"},
                ],
            },
            "position": {"x": 480, "y": 200},
        },
        {
            "id": "ledger",
            "type": "mcp",
            "capability": "mysql_query",
            "params": {
                "op": "call",
                "tool": "execute_query",
                "args": {"query": "select * from devices where host='{{#start.host#}}'"},
            },
            "position": {"x": 700, "y": 120},
        },
        {
            "id": "diag",
            "type": "agent",
            "capability": "设备运维",
            "params": {
                "task": "诊断告警：{{#start.message#}}\n设备台账：{{#ledger.result#}}\n手册片段：{{#kb.hits#}}"
            },
            "position": {"x": 920, "y": 120},
        },
        {
            "id": "canfix",
            "type": "if-else",
            "params": {
                "conditions": [{"left": "{{#diag.text#}}", "operator": "contains", "right": "自动处置"}]
            },
            "position": {"x": 1140, "y": 120},
        },
        {
            "id": "ticket",
            "type": "llm",
            "params": {
                "system": "你是资深 IT 运维工程师，输出简洁的中文工单。",
                "prompt": "根据以下信息生成工单（标题/描述/建议/优先级）：\n{{#diag.text#}}",
            },
            "position": {"x": 920, "y": 320},
        },
        {
            "id": "notify",
            "type": "mcp",
            "capability": "dingtalk",
            "params": {
                "op": "call",
                "tool": "dingtalk_send_message",
                "args": {"text": "告警 {{#start.alert_id#}}（{{#start.host#}}）：\n{{#diag.text#}}\n工单：{{#ticket.text#}}"},
            },
            "position": {"x": 1360, "y": 220},
        },
        {
            "id": "agg",
            "type": "variable-aggregator",
            "params": {
                "variables": ["{{#diag.text#}}", "{{#ticket.text#}}", "{{#notify.result#}}"],
                "mode": "append",
            },
            "position": {"x": 1580, "y": 220},
        },
        {
            "id": "end",
            "type": "end",
            "params": {
                "outputs": {
                    "diagnosis": "{{#diag.text#}}",
                    "ticket": "{{#ticket.text#}}",
                    "notified": "{{#notify.result#}}",
                }
            },
            "position": {"x": 1800, "y": 220},
        },
    ],
    "edges": [
        {"id": "e1", "from": "start", "to": "kb"},
        {"id": "e2", "from": "kb", "to": "cls"},
        {"id": "e3", "from": "cls", "to": "ledger", "condition": "p1"},
        {"id": "e4", "from": "cls", "to": "ledger", "condition": "p2"},
        {"id": "e5", "from": "cls", "to": "ticket", "condition": "p3"},
        {"id": "e6", "from": "ledger", "to": "diag"},
        {"id": "e7", "from": "diag", "to": "canfix"},
        {"id": "e8", "from": "canfix", "to": "notify", "condition": "true"},
        {"id": "e9", "from": "canfix", "to": "ticket", "condition": "false"},
        {"id": "e10", "from": "ticket", "to": "notify"},
        {"id": "e11", "from": "notify", "to": "agg"},
        {"id": "e12", "from": "agg", "to": "end"},
    ],
}


# 带人工审批闸门：LLM 出方案 → 审批（暂停等待）→ 通过则通知、驳回则记录。
CHANGE_APPROVAL_WORKFLOW: dict = {
    "name": "变更审批",
    "description": "变更申请 → LLM 生成方案 → 人工审批（暂停）→ 通过执行通知 / 驳回记录",
    "on_error": "continue",
    "timeout_seconds": 0,
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "params": {"fields": ["change_id", "target", "action"]},
            "position": {"x": 40, "y": 180},
        },
        {
            "id": "plan",
            "type": "llm",
            "params": {
                "system": "你是变更评审助手，输出简洁的中文变更方案与风险。",
                "prompt": "目标系统：{{#start.target#}}\n操作：{{#start.action#}}\n请给出变更步骤与风险。",
            },
            "position": {"x": 260, "y": 180},
        },
        {
            "id": "approval",
            "type": "approval",
            "params": {
                "title": "审批变更执行：{{#start.change_id#}}",
                "description": "{{#plan.text#}}",
                "assignee": "ops",
                "timeout_seconds": 86400,
            },
            "position": {"x": 480, "y": 180},
        },
        {
            "id": "notify",
            "type": "mcp",
            "capability": "dingtalk",
            "params": {
                "op": "call",
                "tool": "dingtalk_send_message",
                "args": {"text": "变更 {{#start.change_id#}} 已批准执行：\n{{#plan.text#}}"},
            },
            "position": {"x": 700, "y": 100},
        },
        {
            "id": "rejected",
            "type": "template",
            "params": {"template": "变更 {{#start.change_id#}} 被驳回：{{#approval.comment#}}"},
            "position": {"x": 700, "y": 280},
        },
        {
            "id": "end",
            "type": "end",
            "params": {
                "outputs": {
                    "approved": "{{#approval.approved#}}",
                    "plan": "{{#plan.text#}}",
                    "result": "{{#notify.result#}}{{#rejected.text#}}",
                }
            },
            "position": {"x": 920, "y": 180},
        },
    ],
    "edges": [
        {"id": "a1", "from": "start", "to": "plan"},
        {"id": "a2", "from": "plan", "to": "approval"},
        {"id": "a3", "from": "approval", "to": "notify", "condition": "true"},
        {"id": "a4", "from": "approval", "to": "rejected", "condition": "false"},
        {"id": "a5", "from": "notify", "to": "end"},
        {"id": "a6", "from": "rejected", "to": "end"},
    ],
}


# 会话式（chatflow）：多轮历史 + 知识库检索 + 会话变量记录主题 → answer 节点回复。
KNOWLEDGE_QA_CHATFLOW: dict = {
    "name": "制度问答",
    "description": "会话式知识问答：多轮历史 + 知识库检索 + 会话变量（记录最近问题）",
    "mode": "chat",
    "conversation": {"history_turns": 10},
    "on_error": "fail",
    "timeout_seconds": 180,
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "params": {"fields": ["query"]},
            "position": {"x": 40, "y": 160},
        },
        {
            "id": "kb",
            "type": "knowledge-retrieval",
            "params": {"query": "{{#sys.query#}}", "top_k": 5},
            "position": {"x": 260, "y": 160},
        },
        {
            "id": "llm",
            "type": "llm",
            "params": {
                "system": (
                    "你是企业制度与 IT 支持助手，基于知识库片段用简洁中文回答；"
                    "片段不足时明确说明并给出建议。"
                ),
                "prompt": (
                    "历史对话：{{#sys.history#}}\n"
                    "知识库片段：{{#kb.hits#}}\n"
                    "用户问题：{{#sys.query#}}"
                ),
            },
            "position": {"x": 480, "y": 160},
        },
        {
            "id": "assign",
            "type": "variable-assigner",
            "params": {"assignments": {"last_question": "{{#sys.query#}}"}},
            "position": {"x": 700, "y": 160},
        },
        {
            "id": "answer",
            "type": "answer",
            "params": {"answer": "{{#llm.text#}}"},
            "position": {"x": 920, "y": 160},
        },
    ],
    "edges": [
        {"id": "c1", "from": "start", "to": "kb"},
        {"id": "c2", "from": "kb", "to": "llm"},
        {"id": "c3", "from": "llm", "to": "assign"},
        {"id": "c4", "from": "assign", "to": "answer"},
    ],
}
