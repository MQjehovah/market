# 能力层消费者（cap）

`cap` 是 market **能力平台**的独立消费端：只依赖 `httpx`，不依赖市场后端代码。
市场是控制面（注册/审核/分发）；本 CLI 把资产落到零号员工 `config/`，或走云端试用 / A2A。

## 两条消费路径

| 路径 | 谁用 | 是否下载 zip | 入口 |
| --- | --- | --- | --- |
| **员工装机** | 零号员工 / Dashboard | 是（skill/mcp/agent…） | `cap install` / 宿主同步 |
| **模型线上网关** | IDE / Agent / 任意 MCP 客户端 | 否 | `marketplace_mcp`（见下方） |

MCP 若声明 `env`（如 `ERP_*` / `DB_*`）：`cap install name --type mcp` 会提示填写；`--env KEY=VAL` 可预填，`--no-interactive` 供 CI。填齐后可选择立即 `enabled: true`，否则请编辑 `mcp_servers.json` 再启用。

线上边界：tool / mcp / agent / workflow 可远程调；skill 只返回 `SKILL.md` 文本注入上下文；rule/command/hook 仍须本地装。

### 模型不装包：只连 marketplace_mcp

```bash
# 市场 JWT 或 SSO access_token 均可（Bearer）
set MARKETPLACE_URL=http://127.0.0.1:8093
set MARKETPLACE_TOKEN=<Bearer token>
# 或显式：set MARKETPLACE_SSO_TOKEN=<SSO access_token>
cd ../backend
python marketplace_mcp/server.py
```

Claude Code / Cursor 等把上述命令配成一条 MCP server 即可。工具面：

- `marketplace_search` — 搜目录  
- `marketplace_use_tool` — 云端沙箱 invoke（无本地 zip）  
- `marketplace_call_mcp` — 调商品 MCP 暴露的 tool  
- `marketplace_activate_skill` — 返回 SKILL.md 正文  
- `marketplace_fetch_agent_persona` — 返回助手 PROMPT.md（人设，不跑任务）  
- `marketplace_run_agent` / `marketplace_run_workflow` — 云端执行  

调用前须已加入「我的能力」（或作者 / `RUNTIME_ACCESS_ROLES`）。授权由市场 runtime 门禁统一校验。

桌面装人设请用 `GET /api/runtime/agents/{name}/persona`（或 MCP 上表工具），**不要**用 `/api/agents/{name}/edit`（那是作者编辑草稿接口）。

| 模式 | 说明 | 命令 |
| --- | --- | --- |
| 同步 | 拉取能力清单（Agent / 积木 / 配方 / 安装包） | `cap sync` |
| 下载 | 下载单个能力包（zip + 校验和） | `cap pull` |
| 本地组装 | 下载 Agent / skill / mcp / plugin / rule / command / hook（tool、workflow 不支持本地装） | `cap install` |
| 本地运行 | 用本地 agent 引擎执行任务 | `cap run --mode local` |
| 云端运行 | 能力层云端试用（沙箱 + 用量） | `cap run --mode cloud` |
| A2A 委派 | 委派任务给云端 Agent | `cap run --mode a2a` / `cap delegate` |
| A2A 服务 | 本地 Agent 暴露为 A2A | `cap serve` |

## 安装

```bash
pip install -r requirements.txt
```

## 配置

```bash
CAP_URL=http://127.0.0.1:8093
CAP_TOKEN=...
CAP_USER=admin
CAP_PASSWORD=<你的市场账号口令>
AGENT_ROOT=E:/ai/agent
CAP_PYTHON=python
```

## 本地组装结构（与 Agent.initialize 对齐）

```text
config/agents/<名称>/
├── PROMPT.md / TEAM.md?       # 人设；TEAM.md = 团队流水线（非市场 workflow）
├── agents/<成员>/             # 团队成员
├── skills/<技能>/SKILL.md
├── mcp_servers.json           # 凭据 ${VAR} 占位，需人工启用
├── dependencies.json
└── installed.json
```

业务能力经 MCP；市场 `tool` 沙箱桥接不作为本地主路径（运行时 `_init_tools` 只扫 `src/tools`）。

能力编排（市场 `workflow`）**不**安装进本目录，仅云端执行。

详见根目录 [README.md](../README.md) 三货架与消费矩阵。
