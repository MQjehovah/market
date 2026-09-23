# AI 能力目录（公司内部）

**定位：内部 AI 资产的系统记录与治理控制面**——注册、版本、审核、发现、分发、授权、计量。  
**不是**对话产品、不是编排 PaaS、不是 IDE。零号员工 / IDE / Dify 等是消费者。

产品叙事对齐 [飞书 aily SkillHub](https://www.feishu.cn/content/article/7646699294103292898)：  
**员工发现安装**（目录精选/热门 → 加入我的能力 → `cap install`）· **开发者发布**（上传/校验 → 提交审核）· **管理员治理**（人工审核、可见性、上下架）。  
控制面在本仓库；对话内唤起技能仍由零号员工等执行面完成。

对标思路：Backstage Catalog（实体分 kind）+ 私有制品库（SemVer）+ Agent Registry / MCP Gateway（治理与入口）+ 飞书 SkillHub（企业技能分发/治理闭环）。

## 三货架（type = kind；逛店 Tab ≠ 货架一一对应）

| 货架 key | 中文 | kind | 含义 | 装到哪 / 谁执行 |
| --- | --- | --- | --- | --- |
| **组件** `brick` | 组件 | `skill` | SOP（SKILL.md） | `config/.../skills/`；Agent 的 `skill` 工具激活 |
| **组件** | 组件 | `mcp` | 连接器 | `mcp_servers.json`；MCPManager / 网关 / IDE |
| **组件** | 组件 | `tool` | 沙箱函数，主要给能力编排节点 | 仅云端 invoke；**不是** `src/tools` |
| **组件** | 组件 | `rule` | 持久指导（RULE.mdc） | `config/rules/<name>/` |
| **组件** | 组件 | `command` | `/` 斜杠命令（COMMAND.md） | `config/commands/<name>/` |
| **组件** | 组件 | `hook` | 生命周期脚本（hooks.json） | `config/hooks/<name>/` |
| **助手** `recipe` | 助手 | `agent` | 人设 + 依赖；可选 **TEAM.md 团队流水线** | `cap install` → `config/agents/<name>/` |
| **助手** | 助手 | `workflow` | **能力编排**（已上架能力的静态 DAG） | 只云端执行，**不进** Agent 目录 |
| **安装包** `install` | 安装包 | `plugin` | 一键分发 skill/mcp/rule/command/hook（可选助手） | 拆子能力；兼容 Cursor Plugin |

逛店顶栏：**推荐 | 技能 | 安装包 | 助手 | 更多**。  
推荐默认浏览：`skill + plugin + agent`。连接器 / 规则 / 命令 / Hooks / 能力编排 / 编排函数进「更多」。`plugin-component` 默认隐藏。

元数据：`GET /api/meta/taxonomy`。空模板：`GET /api/meta/package-templates/{kind}`。

## kind 关系

这些 kind **不是同一层东西**。市场 `tool`、MCP 发现出的 tools、零号员工 `src/tools` **不是同一种**。

| kind | 是什么 | 自己干活吗 | 主消费路径 |
| --- | --- | --- | --- |
| **skill** | SOP（`SKILL.md`） | 否；装进 Agent 后由名为 `skill` 的元工具读进上下文 | `cap install` → `skills/` |
| **mcp** | 连接器（`connection.json`） | 否；连上后 **发现出的 tools** 才可调 | `cap install` → `mcp_servers.json` |
| **tool** | 云端沙箱 `tool.py` | 是（仅市场沙箱 / workflow 节点） | `POST /api/runtime/tools/{name}/invoke`；**无**本地安装 |
| **rule** | 持久指导（`RULE.mdc`） | 否；按 alwaysApply / globs 读进上下文 | `cap install --type rule` → `config/rules/` |
| **command** | 斜杠命令（`COMMAND.md`） | 否；对话里 `/` 唤起 | `cap install --type command` → `config/commands/` |
| **hook** | 生命周期脚本（`hooks.json`） | 宿主按事件执行 scripts | `cap install --type hook` → `config/hooks/` |
| **agent** | 人设（`PROMPT.md`）+ 依赖清单 | 对话在零号员工 / A2A | `cap install` → `config/agents/<name>/` |

Agent 日常主路：**skill 当说明书** + **MCP 发现出的 tools 当手**。市场 `tool` 若出现在 `dependencies.json` 里，本地只生成 HTTP 桥，真正执行仍打回市场沙箱。

Agent 拿到 skill / mcp 的三种来源：

1. **ref**：`dependencies.json` 指向已上架商品；`cap install` 时再下载装入该 Agent 目录  
2. **内嵌**：zip 自带 `skills/`、`mcp/`；不单独逛店；同名上架时详情 `used_by`  
3. **plugin 拆包**：上传后拆子草稿（`plugin-component`），默认不出现在目录列表  

`cap install` 只支持 `agent / skill / mcp / plugin / rule / command / hook`。**workflow / tool 不进本地目录。**

- **plugin**：分发袋，可带 skill / mcp / rule / command / hook / 可选 agent / 可选 tool  
- **workflow**：云端 DAG，节点 `tool|agent|skill|mcp`；与 `TEAM.md`（角色流水线）平行、禁止互转  

## 双编排（平行，禁止互转）

| | 团队流水线 TEAM.md | 能力编排 Workflow |
| --- | --- | --- |
| 节点 | 角色 `assignee` | 能力 `tool\|agent\|skill\|mcp` |
| 引擎 | 零号员工 `TeamOrchestrator` | 市场 `workflows.py` |
| 落地 | `config/agents/<name>/` | 云端 `/api/runtime/workflows/...` |

唯一组合：workflow 的 **agent 节点** 可调用带 TEAM.md 的 Agent。

## 项目结构

```
market/
├── backend/                 # FastAPI：Catalog / Governance / Consume
│   ├── app/services/taxonomy.py   # 货架与语义
│   ├── marketplace_mcp/           # 市场 MCP 桥（IDE / Agent 客户端）
│   └── ...
├── frontend/                # Vue 3：能力目录 / 我的能力 / 治理后台
└── consumer/                # cap CLI：sync / install / run
```

## 快速开始

```bash
cd backend && pip install -r requirements.txt && python run.py
cd frontend && npm install && npm run dev
```

或 `start-dev.bat` / `start-dev.ps1`。演示账号：`admin/admin123`、`publisher/publisher123`、`user/user123456`。

## 小白发布可安装场景（推荐：安装包）

1. 登录 → 侧栏「发布能力」或首页「发布能力」  
2. 选 **发安装包** → 填名称/版本 → 创建草稿  
3. 详情「管理」→ **下载空模板 zip** → 按需改内容 → 上传 zip  
4. **提交审核**（无包时按钮为「去上传能力包」，不会空提交）  
5. 管理员在治理后台通过  
6. 发现页 **加入**（仅授权；加入≠安装）→ 复制 `cap install name@version --type plugin` 装到零号员工  

进阶：技能 / 连接器 / 编排函数 / 助手创建后会进入**在线编辑**（保存即生成能力包），再提交审核；也可先上架组件再发助手并在依赖里引用（依赖须已上架）。安装包仍以上传 zip 为主。

MCP 注意：市场包必须是 `mcp.json` + `connection.json` + `tools.json` + `security.json`；不能直接上传 agent 仓里的 `mcp-server.json`。

## 消费矩阵

| 方式 | 接口 | 说明 |
| --- | --- | --- |
| 目录同步 | `GET /api/capabilities/sync` | 引擎 / CI |
| 下载制品 | `GET /api/capabilities/{name}/download` | SHA-256 校验 |
| 本地组装 | `cap install` | 对齐 `Agent.initialize` 目录 |
| 本地运行 | `cap run --mode local` | 市场不执行 |
| 云端试用 | `POST /api/runtime/*` | 调试，非门户主路径 |
| A2A | Agent Card + `tasks/send` | 协议层 |
| 市场 MCP 桥 | `marketplace_*` | 一个 MCP 进全目录 |
| MCP HTTP 网关 | `/api/mcp-gateway/{name}` | Dify 等；平台能力非商品 kind |
| 加入我的能力 | `/api/my/capabilities` | 调用授权前提之一 |

授权：`RUNTIME_ACCESS_ROLES` ∪ 作者 ∪ 已加入者 ∪ `access_policy`（open / admin_only / restricted）。

## 生命周期与审核

`draft → reviewing → published → deprecated → archived`（另有 rejected / returned）。

审核关注：包结构、`${VAR}` 密钥占位、MCP 地址、Tool AST 审计、依赖可解析、Plugin 组件命名、可见性。

## 与零号员工边界

- 市场：Asset 记录、zip、审核、网关、试用 API  
- 零号员工：`PROMPT` / `TEAM` / `skills` / `mcp_servers.json` + 宿主 `src/tools` + 通道 `src/plugins`  
- IDE / 客户端：装 Plugin（含 rules / commands / hooks）或连市场 MCP；rule / command / hook 也可单独上架  
- **不要**把宿主 BuiltinTool、钉钉/飞书通道插件登记成市场商品  

「我的能力」分两栏：**自定义**（已加入，可启用/停用）与 **我发布的**（草稿/审核）。

`cap install` 目标：

```text
config/agents/<name>/
  PROMPT.md
  TEAM.md?
  agents/<member>/
  skills/<skill>/SKILL.md
  mcp_servers.json
  dependencies.json / installed.json
config/rules/<name>/
config/commands/<name>/
config/hooks/<name>/
```

业务函数经 MCP（发现出的 tools）；市场 `tool` 仅云端沙箱 / Workflow。本地 Agent 依赖里的市场 tool 若有，也只是 HTTP 桥，不是宿主 BuiltinTool。

## 发布路径

1. **引用**：积木先独立上架，Agent/Plugin `ref` + version；详情 `used_by`。  
2. **内嵌/组件**：仅服务本包。Agent `embedded_*` 不拆行；Plugin component 带 `parent_plugin_id`，浏览隐藏。

标准源可对齐 `agent/packages/{skills,mcp,agents,plugins}`。

## 核心 API（分组）

**Catalog**：`GET /api/capabilities`（`shelf` / `include_bricks`）、详情、版本、同步、下载、`GET /api/meta/taxonomy`  
**Governance**：发布/审核、调用权限、安装策略、用户、MCP 网关 CRUD  
**Consume**：`/api/runtime/*`、A2A、`marketplace_mcp`、`/api/mcp-gateway/*`

## 测试

```bash
cd backend && python -m pytest tests -q
```

## Docker

```bash
cp .env.example .env   # 至少 JWT_SECRET
docker compose build market && docker compose up -d
```

端口默认 `8093`。详见 `README` 设计原稿与 `.env.example`。
