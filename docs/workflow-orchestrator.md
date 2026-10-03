# 工作流编排器（Dify 对齐）

market 的工作流能力是一个 Dify 式编排器：**DAG 并行调度 + 条件分支 + 迭代/循环 + 变量传递**，
节点可直接**复用市场能力**（tool / agent / skill / mcp）。编排结果落为市场 `workflow` 能力，
可提交审核、上架、被 A2A/运行时调用。

## 节点类型

| 分类 | 节点 | 说明 |
|---|---|---|
| 基础 | `start` / `end` / `answer` | 入口 / 汇总输出 / 直接回复 |
| LLM | `llm` / `agent` / `parameter-extractor` / `knowledge-retrieval` | 大模型 / A2A 专家 / 结构化抽取 / 知识库检索 |
| 逻辑 | `if-else` / `question-classifier` / `iteration` / `loop` / `variable-aggregator` / `variable-assigner` | 条件 / 分类分流 / 迭代 / 循环 / 聚合 / 赋值 |
| 数据 | `code` / `http-request` / `template-transform` / `doc-extractor` / `list-operator` | 沙箱代码 / HTTP / 模板 / 文档抽取 / 列表操作 |
| 市场能力 | `tool` / `skill` / `mcp` | 市场工具 / 技能 / 连接器（安装或调用） |

> 节点名同时接受 Dify 连字符写法（如 `question-classifier`）与内部下划线写法。

## 变量

- `${input.字段}` 引用执行入参；`${节点id.字段}` 引用前序节点输出；`${sys.query}`/`${sys.now}` 系统变量。
- 兼容 Dify 语法 **`{{#节点id.字段#}}`**（内部自动转换）。
- 整串恰为一个引用时保留原始类型（数组/对象）；否则按字符串内插。

## 分支

- `if-else`：输出 `{result}`，向下游按 `true`/`false` 分支（边的 `condition`）。
- `question-classifier`：LLM 分类，按类别 id 分支（边的 `condition` = 类别 id）。
- 不可达/失败节点的出边自动失效，下游相应跳过；`on_error=continue` 时独立分支继续。

## 市场能力复用

- `tool`：调用市场工具（沙箱执行）。
- `agent`：A2A 委派市场专家。
- `skill`：激活市场技能。
- `mcp`：`op=install` 下发连接配置；**`op=call` 直接调用市场 MCP 工具**，例如：
  ```json
  { "op": "call", "tool": "dingtalk_send_message", "args": { "text": "..." } }
  ```
  连接经 `MCPBridge`（复用云端 MCP 桥接）建立，密钥走平台托管。

## 示例：IT 告警处置

内置模板 `IT告警处置`（`GET /api/workflows/templates`；编辑器「从模板新建」一键载入）：

```
start → knowledge-retrieval → question-classifier
  ├─(p1/p2) mcp(mysql_query 查台账) → agent(设备运维) → if-else
  │        ├─(true)  mcp(dingtalk 通知/建单)
  │        └─(false) llm(生成工单) → mcp(dingtalk 通知)
  └─(p3)  llm(生成工单) → mcp(dingtalk 通知)
  → variable-aggregator → end
```

## 运行

- 试运行（作者/管理员，草稿即可）：`POST /api/workflows/{id}/test`，body `{ "input": {...} }`。
- 正式执行：`POST /api/runtime/workflows/{name}/executions`。
- 执行记录：`GET /api/runtime/workflows/executions/{exec_id}`；取消：`.../cancel`。

## 会话模式（chatflow）

同一工作流可作会话式应用：`answer` 节点作为回复，`conversation` 变量跨轮持久化。

- 声明：`workflow.json` 顶层 `"mode": "chat"` 与 `"conversation": { "history_turns": 10 }`。
- 会话变量：`variable-assigner` 写入 `ctx["conversation"]`，执行结束时持久化；引用 `${conversation.变量}`。
- 多轮历史：`${sys.history}`（最近若干轮 user/assistant），供 `llm` 节点拼接上下文。
- 接口：
  - 发送：`POST /api/runtime/workflows/{name}/chat`，body `{ "query": "...", "conversation_id": "可选" }`，返回 `{ conversation_id, answer, outputs, variables }`。
  - 会话列表：`GET /api/runtime/workflows/{name}/conversations`。
  - 消息回溯：`GET /api/runtime/workflows/conversations/{id}/messages`。
  - 删除：`DELETE /api/runtime/workflows/conversations/{id}`。
- 前端：发布版工作流详情页「对话调试」→ `/workflows/{id}/chat`。
- 内置模板 `制度问答`（`KNOWLEDGE_QA_CHATFLOW`）。

## 环境变量

- `MARKET_RAG_URL` / `MARKET_RAG_TOKEN`：`knowledge-retrieval` 节点调 RAG `/api/search`（未配置则返回空并提示）。
- LLM 节点复用 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。
