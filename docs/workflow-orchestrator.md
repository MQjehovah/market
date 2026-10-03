# 工作流编排器（Dify 对齐）

market 的工作流能力是一个 Dify 式编排器：**DAG 并行调度 + 条件分支 + 迭代/循环 + 变量传递**，
节点可直接**复用市场能力**（tool / agent / skill / mcp）。编排结果落为市场 `workflow` 能力，
可提交审核、上架、被 A2A/运行时调用。

## 节点类型

| 分类 | 节点 | 说明 |
|---|---|---|
| 基础 | `start` / `end` / `answer` | 入口 / 汇总输出 / 直接回复 |
| LLM | `llm` / `agent` / `parameter-extractor` / `knowledge-retrieval` | 大模型 / A2A 专家 / 结构化抽取 / 知识库检索 |
| 逻辑 | `if-else` / `question-classifier` / `iteration` / `loop` / `variable-aggregator` / `variable-assigner` / `approval` | 条件 / 分类分流 / 迭代 / 循环 / 聚合 / 赋值 / 人工审批 |
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

## 触发方式（trigger）

`workflow.json` 顶层 `trigger` 声明工作流如何被自动唤起（**触发仅对已发布版本生效**）。

- `{"type":"webhook","token":"..."}`：`POST /api/runtime/workflows/{name}/trigger`，头 `X-Workflow-Token`，body `{"input":{...}}`。
- `{"type":"schedule","cron":"30 8 * * *","enabled":true,"input":{}}`：market 后台每 60s 扫描执行（多 worker 需 `WORKERS=1`）。
- `{"type":"gitlab","token":"<GitLab Secret Token>"}`：直接接收 GitLab Webhook（Push / Merge Request），校验 `X-Gitlab-Token` 并映射入参：
  - MR：`project` / `mr_iid` / `title` / `source_branch` / `target_branch` / `action` / `web_url`；`diff` 经 `gitlab` 能力平台密钥调 `/merge_requests/{iid}/changes` 拉取。
  - Push：`project` / `ref` / `commit` / `commit_message` / `commits`；`diff` 经 `/repository/compare` 拉取。
  - 需给市场 `gitlab` 能力配置平台密钥 `GITLAB_URL` + `GITLAB_TOKEN`（未配置则 `diff` 为空）。

### 让 GitLab 回调驱动「代码评审助手」

1. 编辑工作流「代码评审助手」，把 `trigger` 设为 `{"type":"gitlab","token":"<自定密钥>"}`；**提交审核并发布**（触发仅对已发布版本生效）。
2. GitLab 项目 **Settings → Webhooks**：
   - URL：`http://<market-host>:8093/api/runtime/workflows/<工作流名>/trigger`（中文名需 URL 编码，或改用英文名工作流）。
   - Secret token：与上面 `token` 一致。
   - 勾选 **Push events** 与 **Merge request events**。
3. 市场后台给 `gitlab` 能力配置平台密钥 `GITLAB_URL`（如 `https://gitlab.company.com`）与 `GITLAB_TOKEN`（`api` 只读令牌）。
4. 触发后到「运行记录 / 审批中心」查看：命中“阻断”会停在『人工审批』节点，需在 `/approvals` 通过/驳回后续跑。

> 试运行时停在 `waiting` 是到达『人工审批』节点等待决定，**并非失败**；去 `/approvals` 审批后会续跑。

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

## 人工审批（human-in-the-loop）

`approval` 节点在执行到此处时**暂停工作流**（`state="waiting"`），记录待审批项（含节点输出入
`runtime.pending`），等人工决定后再**断点续跑**（已完成节点不重跑）。

- 声明：节点 `params` 可含 `title` / `description` / `assignee` / `timeout_seconds`。
- 输出/分支：`{ approved, comment, approver, decision_at }`；下游按 `true`（通过）/`false`（驳回）分支。
- 接口：
  - 待审批列表：`GET /api/runtime/workflows/approvals`（管理员全部；否则本人发起或被指派）。
  - 决定并发续跑：`POST /api/runtime/workflows/executions/{exec_id}/approve`，body `{ "node_id": "...", "approved": true, "comment": "..." }`。
- 可见性：执行拥有者、`assignee` 命中（用户名/角色/部门）或管理员可审批。
- 前端：`/approvals` 审批中心；编辑器节点面板「逻辑 → 人工审批」（通过/驳回双出口）。
- 内置模板 `变更审批`（`CHANGE_APPROVAL_WORKFLOW`）。
- 说明：进程内暂停（非独立 worker 持久恢复）；多 worker 下审批列表以 DB 为准，续跑由处理请求的进程执行。

## 作为应用运行（能力详情页「使用」）

已发布的工作流可直接在**能力详情页 → 使用**里运行（面向内部员工，`access_policy=open` 时任意登录用户可运行）：

- 形态由 `workflow.json` 顶层 `presentation.mode` 决定：`auto`（默认，推断）/ `form` / `chat` / `automation`。
  - `chat`：对话界面（要求引擎 `mode=chat`），复用会话存储/历史。
  - `form`：由 `start.fields` 渲染表单 → 运行 → 展示 `end` 输出。
  - `automation`：展示触发信息 + 手动运行 + 最近运行记录。
- `start.fields` 支持**富字段**（向后兼容纯字符串）：`{"key","label","type":"text|number|date|select|textarea","required","default","options"}`。
- 接口：
  - `GET /api/runtime/workflows/{name}/run-meta`（形态/输入字段/输出/触发）
  - `POST /api/runtime/workflows/{name}/run`（按字段校验必填/补默认后运行）
  - `GET /api/runtime/workflows/{name}/executions?limit=`（近期执行，本人；admin 全量）
- 准入：`require_workflow_run`（admin / 作者 / 角色具备 `capability.invoke` / 统一访问谓词通过；`admin_only`、`restricted` 仍拦截）。

## 环境变量

- `MARKET_RAG_URL` / `MARKET_RAG_TOKEN`：`knowledge-retrieval` 节点调 RAG `/api/search`（未配置则返回空并提示）。
- LLM 节点复用 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。
