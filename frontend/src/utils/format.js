/** 能力类型显示名（type 字段 = kind） */
export const TYPE_LABELS = {
  agent: '助手',
  tool: '编排函数',
  skill: '技能',
  mcp: '连接器',
  workflow: '能力编排',
  plugin: '安装包'
}

/** 三货架：API key 不变，中文对用户可读 */
export const SHELVES = {
  brick: {
    key: 'brick',
    label: '组件',
    short: '组件',
    description: '技能 / 连接器 / 编排函数。发布与高级筛选用；逛店请用「技能」或「更多」。',
    kinds: ['skill', 'mcp', 'tool']
  },
  recipe: {
    key: 'recipe',
    label: '助手',
    short: '助手',
    description: '助手（Agent，可含 TEAM.md）与能力编排平行；禁止互相转换节点模型。',
    kinds: ['agent', 'workflow']
  },
  install: {
    key: 'install',
    label: '安装包',
    short: '安装包',
    description: '一次分发技能 + 连接器（可选助手）。装到零号员工 / IDE，不是钉钉/飞书通道插件。',
    kinds: ['plugin']
  }
}

/** 推荐默认：技能 + 安装包 + 助手 */
export const DEFAULT_BROWSE_KINDS = ['skill', 'plugin', 'agent']

/** 「更多」里的高级类型 */
export const MORE_BROWSE_KINDS = ['mcp', 'workflow', 'tool']
export const KIND_SHELF = Object.fromEntries(
  Object.values(SHELVES).flatMap((s) => s.kinds.map((k) => [k, s.key]))
)

export const KIND_HINTS = {
  skill: {
    shelf: 'brick',
    what: 'SOP 说明书（SKILL.md）；自己不执行，由 Agent 的 skill 元工具读入上下文',
    where: 'cap install --type skill → config/.../skills/',
    whoRuns: '零号员工 / Cursor（不单独「干活」）'
  },
  mcp: {
    shelf: 'brick',
    what: '连接器；真正可调的是发现出的 tools（≠ 市场 tool kind，≠ 宿主 src/tools）',
    where: 'cap install --type mcp → 合并进 mcp_servers.json（常先 enabled:false）',
    whoRuns: '零号员工 MCPManager / IDE / 市场网关'
  },
  tool: {
    shelf: 'brick',
    what: '云端沙箱函数（tool.py）；主要给 Workflow 节点；无本地安装',
    where: '仅 POST /api/runtime/tools/{name}/invoke；不写 src/tools',
    whoRuns: '市场 runtime 沙箱 / Workflow 节点'
  },
  agent: {
    shelf: 'recipe',
    what: '人设 + 依赖容器；日常靠 skill 说明书 + MCP 发现的 tools',
    where: 'cap install → config/agents/<name>/',
    whoRuns: '零号员工 Agent.initialize / A2A；试用走云端 runtime'
  },
  workflow: {
    shelf: 'recipe',
    what: '已上架能力的静态 DAG（tool|agent|skill|mcp），与 TEAM.md 平行；无本地安装',
    where: '不进 Agent 目录；只云端执行',
    whoRuns: '市场 workflows 引擎 / MCP 桥 marketplace_run_workflow'
  },
  plugin: {
    shelf: 'install',
    what: '分发袋（skills + mcp + 可选 agents/tools）；上传后拆子能力',
    where: 'cap install --type plugin → config/plugins/<name>/',
    whoRuns: '安装后由 IDE 或零号员工执行子组件'
  }
}

/** 可本地 cap install 的 kind（与 taxonomy.local_install_kinds 对齐） */
export const LOCAL_INSTALL_KINDS = ['agent', 'skill', 'mcp', 'plugin']

/** 按 kind 生成真实消费命令；workflow/tool 不假装 cap install */
export function installCommandFor(cap) {
  if (!cap?.name) return ''
  const ver = cap.version ? `@${cap.version}` : ''
  const name = `${cap.name}${ver}`
  switch (cap.type) {
    case 'agent':
      return `cap install ${name}`
    case 'skill':
      return `cap install ${name} --type skill`
    case 'mcp':
      return `cap install ${name} --type mcp`
    case 'plugin':
      return `cap install ${name} --type plugin`
    case 'tool':
      return `POST /api/runtime/tools/${cap.name}/invoke`
    case 'workflow':
      return `POST /api/runtime/workflows/${cap.name}/executions`
    default:
      return ''
  }
}

export function isLocalInstallKind(kind) {
  return LOCAL_INSTALL_KINDS.includes(kind)
}

/** 双编排用词：避免都叫「工作流」 */
/** 适用场景（详情页默认文案；可由 input_schema.scenarios 覆盖） */
export const SCENARIO_HINTS = {
  skill: ['把团队 SOP 沉淀为可复用说明书', '在 Agent 对话中按需激活特定流程', '跨项目共享同一套操作规范'],
  mcp: ['连接内部系统 / 数据库 / 第三方 API', '给 Agent 或 IDE 提供可调 tools', '统一管理连接配置与密钥占位'],
  tool: ['作为能力编排（Workflow）节点执行', '云端沙箱调试函数逻辑', '被 Agent 配方间接引用'],
  agent: ['面向业务场景的助手人设与依赖锁定', '带 TEAM.md 时做多角色协作流水线', '通过 A2A / 市场 MCP 被其他 Agent 调用'],
  workflow: ['把已上架能力串成固定 DAG', '云端批处理 / 自动化流水线', '与 TEAM.md 团队流水线分工并行'],
  plugin: ['一次分发 skills + mcp（+ 可选 agents）', '给 Cursor / 零号员工做场景安装包', '组织内默认/强制安装的能力合集']
}

/** 示例用法（命令或提示级，非对话内唤起） */
export const EXAMPLE_PROMPTS = {
  skill: ['先加载该 skill，再按 SKILL.md 步骤处理当前任务', 'cap install <name> --type skill 后在 Agent 中激活'],
  mcp: ['cap install <name> --type mcp 后启用对应 mcp server', '在 Cursor MCP 配置中引用本连接器'],
  tool: ['在 Workflow 中添加 tool 节点并选择本能力', 'POST /api/runtime/tools/{name}/invoke 调试'],
  agent: ['cap install <name> 后在零号员工中打开该 Agent', '用自然语言描述任务，由该 Agent 按人设执行'],
  workflow: ['在市场运行本 Workflow 并查看节点日志', 'marketplace_run_workflow 通过 MCP 桥触发'],
  plugin: ['加入我的能力后同步到本地引擎', 'cap install <plugin> --type plugin 拆出子能力']
}


export const ORCH_LABELS = {
  team: {
    name: '团队流水线',
    carrier: 'TEAM.md + agents/<角色>/',
    nodes: '角色 assignee',
    engine: '零号员工 TeamOrchestrator'
  },
  capability: {
    name: '能力编排',
    carrier: 'workflow.json nodes/edges',
    nodes: '已上架能力 tool|agent|skill|mcp',
    engine: '市场云端 workflows'
  }
}

/** 消费方式（控制面如何被用） */
export const CONSUME_WAYS = [
  { id: 'sync', label: '目录同步', api: 'GET /api/capabilities/sync', who: '引擎 / CI' },
  { id: 'download', label: '下载制品', api: 'GET /api/capabilities/{name}/download', who: 'cap / 人工' },
  { id: 'install', label: '本地组装', api: 'cap install', who: '零号员工' },
  { id: 'local', label: '本地运行', api: 'cap run --mode local', who: '零号员工（市场不执行）' },
  { id: 'trial', label: '云端试用', api: 'POST /api/runtime/*', who: '开发者调试' },
  { id: 'a2a', label: 'A2A 互调', api: 'Agent Card + tasks/send', who: 'Agent 之间' },
  { id: 'mcp_bridge', label: '市场 MCP 桥', api: 'marketplace_* tools', who: 'Cursor 等' },
  { id: 'gateway', label: 'MCP HTTP 网关', api: '/api/mcp-gateway/{name}', who: 'Dify 等' },
  { id: 'join', label: '加入我的能力', api: 'POST /api/my/capabilities', who: '人（调用授权前提）' }
]

/** 审核清单（提交/审核侧提示） */
export const REVIEW_CHECKLIST = [
  '包结构与 schema 合法（按 kind 必需文件）',
  '无硬编码密钥（使用 ${VAR} / ${VAR:default} 占位）',
  'MCP command/url 可接受或已由管理员确认',
  'Tool 包通过 AST 安全审计（禁危险导入/调用）',
  '依赖的积木存在且版本可解析',
  'Plugin 子组件命名不冲突；component 默认不单独上架浏览',
  '可见性与许可证符合组织策略（private/team/internal/public）'
]

export const INSTALL_POLICY_LABELS = {
  optional: '可选',
  default_on: '默认加入（可退）',
  required: '强制'
}

export const STATUS_LABELS = {
  draft: '草稿',
  reviewing: '待审',
  published: '正式版',
  deprecated: '已弃用',
  archived: '已归档',
  rejected: '已驳回',
  returned: '已打回'
}

export const STATUS_BADGE = {
  draft: 'badge',
  reviewing: 'badge-warning',
  published: 'badge-success',
  deprecated: 'badge-warning',
  archived: 'badge',
  rejected: 'badge-danger',
  returned: 'badge-danger'
}

export const VISIBILITY_LABELS = {
  private: '私有',
  team: '团队',
  internal: '内部',
  public: '公开'
}

export const TYPE_CATEGORIES = {
  agent: ['开发助手类', '运维管理类', '业务分析类', '客服支持类', '通用助手类'],
  tool: ['文件操作', '数据查询', 'API调用', '代码分析', '文档处理', '消息通知', '系统管理', '安全审计'],
  skill: ['开发流程', '测试', '文档', '数据分析', '通用效率', '沟通协作'],
  mcp: ['数据库连接', 'DevOps工具', '项目管理', '消息通知', '数据分析', '内部系统'],
  workflow: ['自动化', '数据分析', '开发流程', '通用效率'],
  plugin: ['工单场景', '业务分析', '开发助手', '运维管理', '通用场景']
}

export const PACKAGE_HINTS = {
  agent: 'agent.json + PROMPT.md（可含 TEAM.md 团队流水线、skills/、mcp/；内嵌与市场同名 skill/mcp 自动关联）',
  tool: 'tool.json + schema.json + implementation/tool.py（供能力编排节点；非宿主 src/tools）',
  skill: 'skill.json + SKILL.md',
  mcp: 'mcp.json + connection.json + tools.json + security.json',
  workflow: 'workflow.json（nodes/edges 引用已上架能力；与 TEAM.md 平行，不进 Agent 目录）',
  plugin: 'plugin.json 或 .cursor-plugin/plugin.json + skills/ + mcp.json（拆包子能力；浏览默认隐藏子项）'
}

/** 角色展示 */
export const ROLE_LABELS = {
  admin: 'admin',
  publisher: 'publisher',
  user: 'user'
}

/** 发布三意图：小白默认 Path B = 发安装包优先 */
export const PUBLISH_INTENTS = [
  {
    key: 'install',
    label: '发安装包',
    blurb: '推荐：一次装齐技能+连接器（可选助手）。审核通过后 cap install 即可。',
    defaultType: 'plugin'
  },
  {
    key: 'recipe',
    label: '发助手',
    blurb: '助手（可含 TEAM.md）或能力编排。网页在线编辑即可；依赖的技能/连接器需先上架，或改发安装包内嵌。',
    defaultType: 'agent'
  },
  {
    key: 'brick',
    label: '发组件',
    blurb: '技能 / 连接器 / 编排函数。可网页在线编辑；可复用零件，给助手与安装包引用。',
    defaultType: 'skill'
  }
]

/** 所有者发布进度（详情 / 我的） */
export const OWNER_PROGRESS_STEPS = [
  { key: 'created', label: '创建' },
  { key: 'package', label: '完善内容' },
  { key: 'submit', label: '提交审核' },
  { key: 'reviewing', label: '审核中' },
  { key: 'published', label: '已上架' },
  { key: 'joined', label: '已加入' },
  { key: 'install', label: '本地安装' }
]

/**
 * 计算所有者进度：返回当前步 index（0-based）与是否可本地安装展示。
 * joined = 已在「我的能力」；install 步与 joined 同亮（提示复制命令）。
 */
export function ownerProgressIndex(cap, { joined = false } = {}) {
  if (!cap) return 0
  const hasArtifact = Boolean(
    cap.has_artifact || (cap.artifacts && cap.artifacts.length) || cap.artifact_id
  )
  const status = cap.status
  if (['published', 'deprecated'].includes(status)) {
    if (joined) return 6
    return 4
  }
  if (status === 'reviewing') return 3
  if (['draft', 'returned', 'rejected'].includes(status)) {
    if (!needsZipUpload(cap.type) || hasArtifact) return 2
    return 1
  }
  return 0
}

/** 支持网页在线编辑的类型（保存可生成/更新能力包） */
export const ONLINE_EDITABLE_KINDS = ['skill', 'mcp', 'tool', 'agent', 'workflow']

/** 非 zip 主路径的 kind（内容在编辑器/定义里） */
export const ZIP_OPTIONAL_KINDS = ['workflow']

export function needsZipUpload(kind) {
  return !ZIP_OPTIONAL_KINDS.includes(kind)
}

export function canOnlineEdit(kind) {
  return ONLINE_EDITABLE_KINDS.includes(kind)
}

/** 在线编辑路由；plugin 无编辑页返回 null */
export function editRouteFor(cap) {
  if (!cap?.name && !cap?.id) return null
  const name = encodeURIComponent(cap.name || '')
  switch (cap.type) {
    case 'workflow':
      return `/workflows/${cap.has_draft && cap.draft_id ? cap.draft_id : cap.id}/edit`
    case 'skill':
      return `/skills/${name}/edit`
    case 'mcp':
      return `/mcp/${name}/edit`
    case 'tool':
      return `/tools/${name}/edit`
    case 'agent':
      return `/agents/${name}/edit`
    default:
      return null
  }
}

/** 创建草稿后的最佳下一步：有在线编辑则进编辑页，否则去上传包 */
export function nextRouteAfterCreate(cap) {
  if (!cap?.id) return '/my'
  if (cap.type === 'workflow') {
    return { path: `/workflows/${cap.id}/edit` }
  }
  const edit = editRouteFor(cap)
  if (edit) return edit
  return { path: `/capabilities/${cap.id}`, query: { focus: 'package' } }
}

export function shelfOf(kind) {
  return KIND_SHELF[kind] || ''
}

export function shelfLabel(kind) {
  const key = shelfOf(kind)
  return key ? SHELVES[key].label : ''
}

export function roleLabel(role) {
  return ROLE_LABELS[role] || role || 'user'
}

/** 我的能力待办分类 */
export function ownedTodoBucket(cap) {
  if (cap?.has_draft) {
    if (needsZipUpload(cap.type) && !cap.draft_has_artifact) return 'missing_package'
    return 'ready_to_submit'
  }
  const status = cap?.status
  const hasArtifact = Boolean(
    cap?.has_artifact || (cap?.artifacts && cap.artifacts.length) || cap?.artifact_id
  )
  if (['draft', 'returned', 'rejected'].includes(status)) {
    if (needsZipUpload(cap.type) && !hasArtifact) return 'missing_package'
    return 'ready_to_submit'
  }
  if (status === 'reviewing') return 'in_review'
  return 'other'
}

export function formatDate(value) {
  if (!value) return '-'
  const d = new Date(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function stars(score) {
  return '★'.repeat(Math.round(score || 0)) + '☆'.repeat(5 - Math.round(score || 0))
}
