# AI 能力公共市场平台

基于 [README](README)（设计文档）实现的公司内部 AI 能力公共市场：

- **四类能力**：Agent、工具、技能、MCP（另支持 workflow 编排与 **plugin 组合包**）
- **页面结构**：能力市场（浏览+加入）、我的能力（创建/加入/调用调试）、管理后台（审核/下架/统计/用户）、个人中心；工作流作为能力类型，不单开页面
- **统一门户**：能力浏览 / 搜索 / 详情 / 评分评论 / 订阅通知
- **发布-审核-上架流程**：草稿 → 提交审核 → 通过/驳回/打回 → 正式版 → 弃用 → 归档；
  同一逻辑能力仅保留一个正式版，新版本发布时旧正式版自动转为「已弃用」并通知作者
- **版本管理**：语义化版本 `MAJOR.MINOR.PATCH`，同一能力多版本并存
- **权限模型**：Admin / Publisher / User 三角色，遵循 README 4.4 权限矩阵；
  能力调用/执行（runtime、A2A 委派、工作流执行）授权给：配置角色（默认 admin）、能力作者、
  已加入「我的能力」的调用方；可通过 `RUNTIME_ACCESS_ROLES` 扩展角色；任何登录用户都可发布/编辑/订阅能力
- **调用权限（access_policy）**：每个能力可设置 `open`（所有登录用户可加入并调用）、
  `admin_only`（仅管理员/作者）、`restricted`（仅白名单用户名），作者或管理员在详情页设置，运行时按此拦截
- **执行引擎**：Agent 实例化、工具调用、技能激活、MCP 安装与动态发现，并记录用量
- **A2A 互调协议**：符合 A2A v1.0（JSON-RPC 2.0）——Agent Card 发现、tasks/send、tasks/get、tasks/cancel
- **工具真实执行沙箱**：上传的实现包在隔离子进程中真实执行（AST 安全审计 + 超时/输出限制）
- **统计报表**：管理员全局统计，发布者自有能力统计

## 项目结构

```
market/
├── backend/                 # FastAPI 后端（独立项目）
│   ├── app/
│   │   ├── main.py          # 应用入口（/docs 可查看 API 文档）
│   │   ├── models.py        # 数据模型（能力/工件/审核/评分/订阅/用量/通知）
│   │   ├── routers/         # auth / portal / publish / admin / runtime
│   │   ├── services/        # 能力生命周期、包校验、执行引擎、统计
│   │   ├── a2a/             # A2A 协议（Agent Card + JSON-RPC 任务）
│   │   ├── sandbox/         # 工具真实执行沙箱（审计 + 子进程隔离）
│   │   ├── marketplace_mcp/ # MCP 桥接：让任意 Agent 原生调用市场能力
│   │   ├── auth.py          # JWT 认证
│   │   ├── permissions.py   # 角色权限矩阵
│   │   └── storage.py       # 能力包存储（本地 / MinIO）
│   ├── tests/               # pytest 测试（11 个核心流程用例）
│   └── requirements.txt
├── frontend/                # Vue 3 前端（独立项目，Vite）
│   ├── src/views/           # 能力市场/详情/我的/个人中心/管理后台
│   ├── src/components/
│   └── vite.config.js       # 开发代理 /api → localhost:8000
└── start-dev.bat / .ps1     # 一键启动
```

## 快速开始

### 后端

```bash
cd backend
pip install -r requirements.txt
python run.py
```

默认使用 SQLite + 本地文件存储，开箱即用，无需安装任何中间件。可通过 `.env`（参考 `.env.example`）切换 PostgreSQL / MinIO。

### 前端

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 http://localhost:5173，API 文档见 http://localhost:8000/docs。

也可以直接运行 `start-dev.bat`（Windows）或 `start-dev.ps1` 一键启动前后端。

## 快速用市场能力（三种方式）

### 方式一：MCP 桥接（推荐，最顺滑）

`backend/marketplace_mcp/server.py` 把市场能力暴露为 5 个标准 MCP 工具，任何支持 MCP 的 Agent（Claude Code、Codex、Cursor、自研 Agent）都能原生调用：

| 工具 | 作用 |
| --- | --- |
| `marketplace_search` | 搜索/浏览能力 |
| `marketplace_use_tool` | 真实调用工具（沙箱执行） |
| `marketplace_run_agent` | 实例化 Agent 并委派任务 |
| `marketplace_activate_skill` | 激活技能（返回执行指引） |
| `marketplace_discover_mcp` | 动态发现 MCP 能力 |

配置（调用类接口需要管理员授权令牌，示例使用 `admin`）：

```bash
# 1. 登录拿 token（runtime 调用仅管理员可用）
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 2. 给 Claude Code 添加市场
claude mcp add marketplace \
  --env MARKETPLACE_URL=http://127.0.0.1:8000 \
  --env MARKETPLACE_TOKEN=<上一步返回的 access_token> \
  -- python E:/ai/market/backend/marketplace_mcp/server.py

# Codex / Cursor 等：在 MCP 配置文件中添加同一条命令即可
```

之后直接对你的 Agent 说："搜索市场里的工具，帮我调用文件哈希计算" 或 "把生成上月销售报表的任务委派给数字中台分析师"。

### 方式二：纯 HTTP 直调（自研 Agent / 脚本）

```python
from urllib.parse import quote
import httpx

API = "http://127.0.0.1:8000"
HEADERS = {"Authorization": "Bearer <你的 token>"}

# 调用工具（沙箱真实执行）
r = httpx.post(f"{API}/api/runtime/tools/{quote('文件哈希计算')}/invoke",
               json={"params": {"path": "/tmp/a.txt"}}, headers=HEADERS, timeout=60)

# 委派 Agent
r = httpx.post(f"{API}/api/runtime/agents/{quote('数字中台分析师')}/instances",
               params={"task": "生成上月销售报表"}, headers=HEADERS, timeout=60)

# 激活技能
r = httpx.post(f"{API}/api/runtime/skills/{quote('TDD 开发工作流')}/activate",
               json={"context": "写测试"}, headers=HEADERS, timeout=30)
```

完整示例见 `backend/examples/quick_use.py`（`python examples/quick_use.py`）。

### 方式三：A2A 委派（Agent 之间互调）

你的 Agent 若支持 A2A 协议，直接向市场 Agent 发 JSON-RPC 任务（详见上文 A2A 章节）。三种方式都会记录用量并计入统计。

## 演示账号（首次启动自动种子）

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 市场管理员 | admin | admin123 |
| 能力发布者 | publisher | publisher123 |
| 普通用户 | user | user123456 |

## 核心 API

| 模块 | 说明 |
| --- | --- |
| `POST /api/auth/login` `POST /api/auth/register` | 登录 / 注册 |
| `GET /api/capabilities` | 浏览与搜索（类型/分类/状态/排序，分页返回 `{items,total,page,page_size}`） |
| `GET /api/capabilities/{id}` | 能力详情与版本列表 |
| `POST /api/publish/capabilities` | 登录用户创建草稿（审核由管理员把关） |
| `POST /api/publish/capabilities/{id}/submit` | 提交审核 |
| `POST /api/admin/capabilities/{id}/review` | 管理员审核（通过/驳回/打回） |
| `POST /api/runtime/agents/{name}/instances` | 实例化 Agent |
| `POST /api/runtime/tools/{name}/invoke` | 调用工具 |
| `POST /api/runtime/skills/{name}/activate` | 激活技能 |
| `POST /api/runtime/mcp/{name}/install` | 安装 MCP |
| `GET /api/runtime/mcp/discover` | MCP 动态发现 |
| `GET /api/admin/stats` | 平台统计 |
| `GET /.well-known/agent-card.json` | A2A 标准发现：市场元卡片 |
| `GET /api/a2a/agents` | 发现平台内所有可互调 Agent |
| `POST /api/a2a/agents/{id}/a2a` | A2A JSON-RPC：tasks/send / tasks/get / tasks/cancel |
| `GET /api/a2a/tasks/{task_id}` | REST 方式查询 A2A 任务状态 |

## 测试

```bash
cd backend
python -m pytest tests -q
```

覆盖：认证、发布-审核-上架全流程、权限矩阵（登录用户可创建草稿、审核仅管理员）、搜索过滤、执行引擎用量记录、评分订阅、版本冲突、私有可见性。

## A2A（Agent-to-Agent）互调

平台把每个已发布的 **Agent 类型能力**暴露为标准 A2A Agent，其他 Agent 可通过统一协议发现并委派任务：

1. **发现**：读取 `/.well-known/agent-card.json`（市场元卡片，技能 = 所有已发布 Agent）或 `GET /api/a2a/agents/{id}/card` 获取单个 Agent Card。
2. **委派**：向 `POST /api/a2a/agents/{id}/a2a` 发送 JSON-RPC `tasks/send`，同步返回任务结果：

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "tasks/send",
  "params": {
    "id": "task-001",
    "message": { "role": "user", "parts": [{ "type": "text", "text": "生成上月销售报表" }] }
  }
}
```

3. **跟踪/取消**：`tasks/get` 查询状态，`tasks/cancel` 取消（终态任务保持终态）。每次互调计入能力用量（action=`a2a_task`）。

错误码遵循规范：`-32601` 方法不存在、`-32001` 任务不存在、`-32003` Agent 不存在或未启用 A2A。详情页的 A2A 面板可直接发送测试任务。

## 工具真实执行沙箱

工具能力上传 zip 实现包（含 `implementation/tool.py`）后，`POST /api/runtime/tools/{name}/invoke` 将**真实执行**而不是模拟返回：

- **契约**：`tool.py` 提供 `run(params: dict) -> dict`（或 `main`），参数经 stdin 传入、结果经 stdout 返回。
- **安全审计**：执行前对源码做 AST 检查——严格白名单导入（禁网络/进程/系统模块），禁止 `eval/exec`、`os.system` 等危险调用。
- **隔离执行**：在临时目录中以 `python -I` 独立子进程运行，带超时（默认 15s）与输出大小限制。
- **zip 炸弹防护**：解压前检查解压总量与单文件大小上限，防压缩炸弹。
- **进程树清理**：超时后整树终止子进程（Windows 下 `taskkill /T`），避免残留。
- **失败处理**：审计不过、超时、抛异常都会返回 `execution: "real", ok: false, error: ...`，并记录失败用量。

未上传实现包时降级为模拟执行（`execution: "simulated"`）。生产环境如需更强隔离，可在此基础上换成容器/WASM 沙箱。

## 调用授权

- 能力详情页仅展示介绍信息（参数 Schema / 版本 / 评分 / 订阅），不提供直接调用；
- 「能力市场」可把能力加入「我的能力」；「我的能力」页支持对自己创建或已加入的能力直接调用 / 调试，无需管理员；
- 管理员在「管理后台 → 调试 / 试用」可调试平台内全部能力；
- 外部调用（`/api/runtime/*`、A2A `tasks/send`、工作流执行）必须携带授权令牌，调用方需为：
  配置角色（`RUNTIME_ACCESS_ROLES`，默认 `admin`）、能力作者，或已加入「我的能力」的用户。

## 其他工程化优化

- **SQL 下推 + 分页**：浏览/搜索/分类全部在数据库层过滤、排序、分页，并加了 `(type, status, visibility)` 复合索引，能力量级大也不会退化。
- **工具参数展示**：上传工具包时自动解析 `schema.json` 并保存，详情页直接展示参数名/类型/必填/说明，降低调用出错率。
- **登录限流**：同一用户名 + IP 15 分钟内失败 5 次即锁定，防暴力破解。
- **流式校验和**：上传时边写边算 SHA-256，大包不再二次全量读取。
- **原子用量计数**：`usage_count` 改为数据库原子自增，避免并发读改写。

## 技术说明

- 存储层按 README 设计为 PostgreSQL(元数据) + MinIO(能力包)，代码通过 `DATABASE_URL` / `ARTIFACT_STORAGE` 配置切换；默认 SQLite/本地文件，零外部依赖开箱即用。
- 能力包上传时按 README 3.x 各市场的发布包结构校验必需文件（如 tool 包必须含 `tool.json + schema.json + implementation/tool.py`）。
- 前端所有列表/详情/操作均对接真实 API；中文能力名在运行时调用中自动做 URL 编码。

## 能力层（Capability Layer）

market 是公司 AI 能力的**独立能力层**：本身维护大量的 tool / mcp / skill / workflow 基础能力，
也可定义各种 agent（人设 + 依赖绑定），并通过「Agent + 工具 + 技能」封装成完整功能对外提供服务。
能力层不依赖 agent 仓库代码，可独立部署；agent 仓库只是能力层的消费者之一。

### 四种消费模式

1. **下载到本地组装成本地 Agent**：消费者把 Agent 及其工具/技能/MCP 依赖下载到本地，
   组装进本地 agent 配置目录（`config/agents/<name>/`），用本地引擎直接运行。
2. **直接在云端运行**：`POST /api/runtime/*` 在能力层云端执行（工具走沙箱真实执行、记录用量）。
3. **A2A 协议互调**：本地 Agent 与云端 Agent 通过 A2A JSON-RPC 双向委派任务。
4. **服务封装**：Agent + 工具 + 技能 封装为完整服务，可下载为自包含 Agent 包，
   也可经云端 runtime / A2A 端点以服务方式被调用。

### 消费者 CLI（`market/consumer/`）

`cap` 是独立于市场后端的能力层消费者（仅依赖 httpx）：

```bash
cd market/consumer && pip install -r requirements.txt

python -m cap sync                                  # 同步能力目录
python -m cap pull 文件哈希计算 -o tool.zip          # 下载单个能力包
python -m cap install 数字中台                        # 组装成本地 Agent
python -m cap run 数字中台 --task "生成销售报表" --mode local   # 本地引擎
python -m cap run 数字中台 --task "生成销售报表" --mode cloud   # 云端 runtime
python -m cap run 数字中台 --task "生成销售报表" --mode a2a     # A2A 委派云端 Agent
python -m cap serve --port 8765                      # 本地 A2A 服务（云端可反向调用）
```

详见 [consumer/README.md](consumer/README.md)。

### 能力层接口

- `GET /api/capabilities/sync` — 返回各能力的最新发布版本（含 `download_url` / `has_artifact`），供 Agent 等消费者拉取目录。
- `GET /api/capabilities/{name}/download?version=` — 按名称（可选版本）下载能力包 zip，响应头带 `X-Capability-*`（名称/类型/版本/SHA-256），中文名称按 RFC 5987 百分号编码。

把 agent 现有资产提取到能力层的脚本（在 `backend/` 目录执行）：

```bash
python scripts/seed_agent_capabilities.py --agent-root ../../agent --dry-run   # 预览打包计划
python scripts/seed_agent_capabilities.py --agent-root ../../agent             # 发布到能力层
python scripts/seed_agent_capabilities.py --agent-root ../../agent --types tool skill
```

脚本会把 agent 现有资产按市场包规范打包（tool 含 `schema.json`、agent 含 `PROMPT.md`/`TEAM.md`/`skills/`/`agents/`、mcp 含 `connection.json` 等），以 `published` 状态入库；MCP 包内的密钥自动脱敏为 `${VAR}` 占位符，避免凭据进入能力层。

### A2A 双向互调

- 云端 Agent Card：`/.well-known/agent-card.json`（市场元卡片）与 `/api/a2a/agents`（全部 Agent Card）。
  Card 的 `url` 即 JSON-RPC 任务端点：`POST /api/a2a/agents/{id}/a2a`（`tasks/send` / `tasks/get` / `tasks/cancel`）。
- 本地 Agent 通过 `cap serve` 暴露同样的标准端点；两边可互相发现、互相委派。
- 每次互调计入能力用量（`action=a2a_task`），执行模式（`llm` / `simulated` / `local`）写入任务 `metadata.mode`。

### Agent 编辑与版本

Agent 在同一个编辑页完成提示词与绑定能力编辑，保存即生成新版本草稿：

- `GET/PUT /api/agents/{name}/edit`：读取/保存编辑态（`PROMPT.md` + 工具/技能/MCP 依赖）；无草稿时保存自动创建 patch+1 新版本，有草稿则原地更新，提示词与依赖写回能力包（`dependencies.json`）。
- 提交审核 / 发布走既有 publish 流程；发布后 `POST /api/runtime/agents/{name}/instances` 按该版本包内依赖动态组装运行时（工具含 schema、技能、MCP 最新或锁定版本）。
- 前端：Agent 详情页「编辑 Agent」进入编辑页；编辑页可「保存为新版本草稿」「提交审核」「导出快照包」。
- 旧绑定 API（`/api/agents/{name}/bindings`）保留用于运行时绑定覆盖（请求体 `binding` / `bindings`），UI 已并入 Agent 编辑页，不再有独立 tab。

### Agent 真实执行

- 配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（OpenAI 兼容网关，如 `https://ai.rosiwit.com/v1`）后，Agent 任务为**真实执行**：加载人设 PROMPT + 该版本绑定能力 → LLM 工具调用循环，工具走市场沙箱真实执行；未配置时降级为模拟（响应 `mode=simulated`）。
- 绑定的**技能**会注册为 `skill` 工具：LLM 先激活技能拿到 SKILL.md 执行指引，再按指引执行；
  绑定的 **MCP** 会在云端容器内启动其服务端（从能力包提取实现 + 解析 `${VAR}` 环境变量），
  工具以 `mcp_<服务>_<工具>` 函数形式注册，可被 LLM 直接调用；执行响应含 `runtime.mcp_tools`。
- 入口：`POST /api/runtime/agents/{name}/tasks`（直接发任务）、A2A `tasks/send`、工作流 agent 节点、MCP 桥 `marketplace_run_agent`。
- 执行响应包含 `mode`（llm / simulated）、`tool_calls` 统计与运行时组装清单；A2A 任务在 `metadata.mode` 标注执行模式。

### 工作流编排

- 第 5 类能力 `workflow`：包内 `workflow.json` 定义 `nodes`（引用 tool/agent/skill/mcp + 入参模板）与 `edges`（依赖边），支持 DAG 拓扑与环检测。
- 执行：`POST /api/runtime/workflows/{name}/executions`（入参 `input`），节点间用 `${input.x}` / `${nodeId.key}` 传值；`GET /api/runtime/workflows/executions/{id}` 查询、`POST .../cancel` 取消。
- 工作流作为能力类型展示与调用（能力市场 / 我的能力 / 管理后台调试）；新建能力弹窗中选择
  workflow 类型并填写 `workflow.json`；MCP 桥新增 `marketplace_run_workflow` 工具，供任意 Agent 原生调用。

### MCP HTTP 中转网关

参考 Dify 的接入方式，平台提供统一的 MCP HTTP 中转网关（`/api/mcp-gateway`）：

- **入站（外部客户端 → 平台）**：管理后台「MCP 网关」注册服务（stdio / HTTP / SSE），平台自动暴露两个端点：
  - Streamable HTTP：`GET|POST /api/mcp-gateway/{name}/stream`
  - SSE：`GET /api/mcp-gateway/{name}/sse` + `POST /api/mcp-gateway/{name}/messages`
  - 外部客户端（Dify、Claude Desktop、其他 Agent）带令牌（`X-Gateway-Token` 或 `Authorization: Bearer`）即可像连接普通 MCP server 一样发现并调用工具；stdio 服务自动转成 HTTP（mcp-proxy 模式），不再限制语言/运行时。
- **出站（平台 → 上游）**：MCP 能力包的 `connection.json` 支持 `transport: http|sse|gateway`：
  ```json
  { "transport": "http", "url": "https://mcp.example.com/mcp",
    "headers": { "Authorization": "Bearer ${MCP_TOKEN}" } }
  { "transport": "gateway", "server": "demo-mcp" }
  ```
  Agent 运行时与工作流 MCP 节点通过网关连接任意语言的远程 MCP server（占位符 `${ENV}` / `${ENV:default}` 从环境变量解析）。
- 管理接口：`/api/admin/mcp-gateway/servers`（仅管理员，CRUD + 连接测试）；管理后台 →「MCP 网关」可视化操作。
- 演示服务：`backend/scripts/demo_mcp_server.py`（FastMCP stdio），可在管理后台注册 `python /app/backend/scripts/demo_mcp_server.py` 立即联调。

## Plugin（一键上架）

创建类型为 `plugin` 的草稿后，上传单个 zip：

```text
plugin.json                 # 或 .cursor-plugin/plugin.json
agents/<name>/agent.json
agents/<name>/PROMPT.md
skills/<name>/SKILL.md
mcp.json                    # { "mcpServers": { ... } }
tools/<name>/...            # 可选
```

平台自动拆出子能力；**浏览市场默认不列出** `plugin-component`（`include_components=true` 可列出）；详情页从插件进入子组件。  
「加入我的能力」一键带上全部组件，**移除插件时级联移除组件**；发布 / 驳回 / 撤回 / 弃用 / 删除会对子能力做对称级联。  
重上传去掉某组件时：草稿子能力删除，已发布子能力弃用并断开。

## 能力对齐（内嵌 vs 独立）

- **Agent 内嵌** skill/mcp：上传时写入 `input_schema.embedded_*`，并按名称匹配市场已发布能力，固化 `capability_id` / `market_version`（版本不一致时标 `version_mismatch`）。**不会**自动拆成独立市场上架行。
- **Skill / MCP 详情**：展示 `used_by`（引用它的 Agent / Plugin）。
- **Plugin 子能力**：物化为独立 Capability，带 `parent_plugin_id`；校验规则与独立 skill/mcp/tool 包对齐。
- **新版本**：复制 `input_schema`（含 plugin components 草案）；能力包仍需重新上传。

## Docker 部署

镜像为多阶段构建（前端 `vite build` + 后端 FastAPI 单镜像），基础镜像使用公司内网仓库 `public-docker-virtual.xzrobot.com`（Docker Hub 在办公网络不可达时仍可构建）：

```bash
# 服务器上（项目目录 ~/market）
cp .env.example .env          # 至少配置 JWT_SECRET
docker compose build market
docker compose up -d
```

运行约定（与 rag / ai-gateway 一致）：

- 端口 `8093`（`8000` 被 rag 占用），数据卷挂载 `./data:/app/backend/data`，`marketplace.db` 与能力包工件持久化在宿主机。
- 环境变量：`JWT_SECRET`（必填）、`SEED_ADMIN_PASSWORD`、`DATABASE_URL`、`ARTIFACT_STORAGE` / `ARTIFACT_DIR`，默认 SQLite + 本地工件存储开箱即用。
- 容器 `restart: unless-stopped`，升级时 `docker compose build market && docker compose up -d` 即可。
- 注意 `passlib` 与 `bcrypt>=4.1` 不兼容，`requirements.txt` 已锁定 `bcrypt==4.0.1`。
