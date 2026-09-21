# 能力层消费者（cap）

`cap` 是 market **能力平台**的独立消费端：只依赖 `httpx`，不依赖市场后端代码。
市场是控制面（注册/审核/分发）；本 CLI 把资产落到零号员工 `config/`，或走云端试用 / A2A。

| 模式 | 说明 | 命令 |
| --- | --- | --- |
| 同步 | 拉取能力清单（Agent / 积木 / 配方 / 安装包） | `cap sync` |
| 下载 | 下载单个能力包（zip + 校验和） | `cap pull` |
| 本地组装 | 下载 Agent / skill / mcp / plugin（tool、workflow 不支持本地装） | `cap install` |
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
