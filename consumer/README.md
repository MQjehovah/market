# 能力层消费者（cap）

`cap` 是 market 能力层的**独立消费端**：只依赖 `httpx`，不依赖市场后端代码。
它让任何本地 Agent / 脚本以四种方式使用能力层：

| 模式 | 说明 | 命令 |
| --- | --- | --- |
| 同步 | 拉取能力目录（Agent / 工具 / 技能 / MCP / 工作流） | `cap sync` |
| 下载 | 下载单个能力包（zip + 校验和） | `cap pull` |
| 本地组装 | 下载 Agent + 依赖，组装成可运行的本地 Agent | `cap install` |
| 本地运行 | 用本地 agent 引擎执行任务 | `cap run --mode local` |
| 云端运行 | 直接在能力层云端执行任务（沙箱 + 用量统计） | `cap run --mode cloud` |
| A2A 委派 | 委派任务给云端 Agent（JSON-RPC） | `cap run --mode a2a` / `cap delegate` |
| A2A 服务 | 把本地 Agent 暴露为 A2A Agent，供云端/本地反向互调 | `cap serve` |

## 安装

```bash
pip install -r requirements.txt
```

## 配置

环境变量（或命令参数）：

```bash
CAP_URL=http://127.0.0.1:8093     # 能力层地址（市场后端，端口见 docker-compose）
CAP_TOKEN=...                     # 访问令牌（能力层 /api/auth/login 获取）
CAP_USER=admin                    # 未提供 token 时登录；调用方需为管理员 / 能力作者 / 已加入者
CAP_PASSWORD=admin123
AGENT_ROOT=E:/ai/agent            # 本地 agent 仓库（run --mode local / install 默认目标）
CAP_PYTHON=python                 # 本地运行使用的解释器（需已安装 agent 依赖）
```

## 快速开始

```bash
# 1. 查看能力目录
python -m cap sync

# 2. 下载单个能力包
python -m cap pull 文件哈希计算 -o tool.zip

# 3. 把云端 Agent 及其工具/技能/MCP 依赖组装成本地 Agent
python -m cap install 数字中台
#    产物：<AGENT_ROOT>/config/agents/数字中台/
#    PROMPT.md + skills/ + mcp_servers.json + tools/（云端桥接工具）

# 4. 三种方式执行任务
python -m cap run 数字中台 --task "生成上月销售报表" --mode local   # 本地引擎
python -m cap run 数字中台 --task "生成上月销售报表" --mode cloud   # 云端 runtime
python -m cap run 数字中台 --task "生成上月销售报表" --mode a2a     # A2A 委派云端 Agent

# 5. 把本地 Agent 暴露为 A2A 服务（端口 8765）
python -m cap serve --port 8765 --config-dir E:/ai/agent/config --agent-root E:/ai/agent

# 6. 云端/其它本地 Agent 反向互调（A2A 标准）
#    GET  http://127.0.0.1:8765/.well-known/agent-card.json
#    POST http://127.0.0.1:8765/api/a2a/agents/<名称>/a2a
#    内容：{"jsonrpc":"2.0","id":"1","method":"tasks/send",
#           "params":{"id":"t1","message":{"role":"user",
#                     "parts":[{"type":"text","text":"任务内容"}]}}}
```

## 本地组装结构

`cap install` 把能力层 Agent 展开到本地 agent 配置目录：

```text
config/agents/<名称>/
├── PROMPT.md / TEAM.md        # 人设（来自能力层 Agent 包）
├── skills/<技能>/SKILL.md     # 绑定的技能（含 references/scripts/assets）
├── mcp_servers.json           # 绑定的 MCP 连接（凭据 ${VAR} 占位，需人工提供后启用）
├── tools/<名称>.py            # 工具桥接（BuiltinTool，调用云端沙箱真实执行）
├── dependencies.json          # 依赖清单
└── installed.json             # 安装清单（来源版本 / 时间 / 依赖）
```

本地引擎在 `Agent.initialize()` 后扫描 `tools/` 目录并注册桥接工具，因此
组装后的 Agent 与本地 agent 引擎完全兼容，也支持 `--agent <名称>` 交互运行。

## A2A 双向互调

- **本地 → 云端**：`cap run <agent> --mode a2a --task ...`（发现 Agent Card → `tasks/send`）。
- **云端 → 本地**：`cap serve` 暴露 `/.well-known/agent-card.json` 与
  `/api/a2a/agents/<名称>/a2a`，云端 Agent 可把本地 Agent 当作标准 A2A Agent 委派任务。
- 任务状态通过 `tasks/get` / `tasks/cancel` 查询与取消，本地任务持久化在 `~/.cap/tasks.jsonl`。

## 注意事项

- `run --mode local` 会启动本地 agent 引擎，需要该解释器已安装
  `agent/requirements.txt` 的依赖；可用 `CAP_PYTHON` 指向已装好的 Python。
- `--mode cloud` / `--mode a2a` 属于外部调用：调用方需为管理员（`RUNTIME_ACCESS_ROLES`）、
  能力作者，或已把该能力加入「我的能力」的用户，请使用相应账号的 `CAP_TOKEN`。
- MCP 连接中的密钥在能力层已脱敏为 `${VAR}`，安装后需在环境中提供并显式启用。
- 本地 A2A 服务默认只监听 `127.0.0.1`；如需被云端访问，用 `--host 0.0.0.0` 并做好网络与鉴权。
