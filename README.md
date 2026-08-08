# AI 能力公共市场平台

基于 [README](README)（设计文档）实现的公司内部 AI 能力公共市场：

- **四大市场**：Agent 市场、工具市场、技能市场、MCP 市场
- **统一门户**：能力浏览 / 搜索 / 详情 / 评分评论 / 订阅通知
- **发布-审核-上架流程**：草稿 → 提交审核 → 通过/驳回/打回 → 正式版 → 弃用 → 归档
- **版本管理**：语义化版本 `MAJOR.MINOR.PATCH`，同一能力多版本并存
- **权限模型**：Admin / Publisher / User 三角色，遵循 README 4.4 权限矩阵
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
│   │   ├── auth.py          # JWT 认证
│   │   ├── permissions.py   # 角色权限矩阵
│   │   ├── storage.py       # 能力包存储（本地 / MinIO）
│   │   └── cache.py         # 缓存（内存 / Redis）
│   ├── tests/               # pytest 测试（11 个核心流程用例）
│   └── requirements.txt
├── frontend/                # Vue 3 前端（独立项目，Vite）
│   ├── src/views/           # 浏览/详情/发布/我的/个人中心/管理后台
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

默认使用 SQLite + 本地文件存储 + 内存缓存，开箱即用，无需安装任何中间件。可通过 `.env`（参考 `.env.example`）切换 PostgreSQL / MinIO / Redis。

### 前端

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 http://localhost:5173，API 文档见 http://localhost:8000/docs。

也可以直接运行 `start-dev.bat`（Windows）或 `start-dev.ps1` 一键启动前后端。

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
| `GET /api/capabilities` | 浏览与搜索（类型/分类/状态/排序） |
| `GET /api/capabilities/{id}` | 能力详情与版本列表 |
| `POST /api/publish/capabilities` | 发布者创建草稿 |
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

覆盖：认证、发布-审核-上架全流程、权限矩阵（普通用户禁止发布/审核）、搜索过滤、执行引擎用量记录、评分订阅、版本冲突、私有可见性。

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
- **失败处理**：审计不过、超时、抛异常都会返回 `execution: "real", ok: false, error: ...`，并记录失败用量。

未上传实现包时降级为模拟执行（`execution: "simulated"`）。生产环境如需更强隔离，可在此基础上换成容器/WASM 沙箱。

## 技术说明

- 存储层按 README 设计为 PostgreSQL(元数据) + MinIO(能力包) + Redis(缓存)，代码通过 `DATABASE_URL` / `ARTIFACT_STORAGE` / `CACHE_BACKEND` 配置切换；默认 SQLite/本地文件/内存缓存便于本机开发。
- 能力包上传时按 README 3.x 各市场的发布包结构校验必需文件（如 tool 包必须含 `tool.json + schema.json + implementation/tool.py`）。
- 前端所有列表/详情/操作均对接真实 API；中文能力名在运行时调用中自动做 URL 编码。
