/** 能力类型显示名（type 字段 = kind） */
export const TYPE_LABELS = {
  agent: '助手',
  tool: '编排函数',
  skill: '技能',
  mcp: '连接器',
  workflow: '能力编排',
  plugin: '安装包',
  rule: '规则',
  command: '命令',
  hook: 'Hooks'
}

/** 三货架：API key 不变，中文对用户可读 */
export const SHELVES = {
  brick: {
    key: 'brick',
    label: '组件',
    short: '组件',
    description: '技能 / 连接器 / 编排函数。发布与高级筛选用；逛店请用「技能」「连接器」或「更多」。',
    kinds: ['skill', 'mcp', 'tool', 'rule', 'command', 'hook']
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
    description: '一次分发技能 + 连接器（可选助手）。能力保留，逛店默认不展示。',
    kinds: ['plugin']
  }
}

/** 推荐默认：助手 + 依赖（技能 / 连接器） */
export const DEFAULT_BROWSE_KINDS = ['agent', 'skill', 'mcp']

/** 「更多」：进阶编排类；安装包 / rule / command / hook 保留能力但不逛店展示 */
export const MORE_BROWSE_KINDS = ['workflow', 'tool']
export const HIDDEN_BROWSE_KINDS = ['plugin', 'rule', 'command', 'hook']
export const KIND_SHELF = Object.fromEntries(
  Object.values(SHELVES).flatMap((s) => s.kinds.map((k) => [k, s.key]))
)

export const KIND_HINTS = {
  skill: {
    shelf: 'brick',
    what: '问答说明书（SKILL.md）。自己不执行，助手按需读入后按步骤回答。',
    where: '随助手装到零号员工；在对话里提问即可触发',
    whoRuns: '问答助手（零号员工）'
  },
  mcp: {
    shelf: 'brick',
    what: '连接器：给助手接外部系统。真正可调的是连上后发现的工具，单独下载不是一项服务。',
    where: '随助手装到零号员工；桌面可连 /api/mcp-gateway/{name}/sse（SSO Bearer）',
    whoRuns: '问答助手通过连接器调用外部系统'
  },
  tool: {
    shelf: 'brick',
    what: '云端沙箱函数（tool.py）；主要给 Workflow 节点；无本地安装',
    where: '仅 POST /api/runtime/tools/{name}/invoke；不写 src/tools',
    whoRuns: '市场 runtime 沙箱 / Workflow 节点'
  },
  rule: {
    shelf: 'brick',
    what: '持久指导（RULE.mdc）；alwaysApply / globs 控制何时生效',
    where: 'cap install --type rule → config/rules/<name>/',
    whoRuns: 'IDE / Agent 读入上下文'
  },
  command: {
    shelf: 'brick',
    what: '可复用提示（COMMAND.md）；对话里用 / 唤起',
    where: 'cap install --type command → config/commands/<name>/',
    whoRuns: 'IDE / Agent 斜杠命令'
  },
  hook: {
    shelf: 'brick',
    what: '生命周期脚本（hooks.json）；观察、拦截或跟进 Agent 事件',
    where: 'cap install --type hook → config/hooks/<name>/',
    whoRuns: 'IDE / 宿主 hook 运行时'
  },
  agent: {
    shelf: 'recipe',
    what: '人设 + 依赖容器；日常靠 skill 说明书 + MCP 发现的 tools',
    where: 'cap install 后在零号员工打开该助手并提问',
    whoRuns: '零号员工问答 / A2A；详情页可云端试用'
  },
  workflow: {
    shelf: 'recipe',
    what: '已上架能力的静态 DAG（tool|agent|skill|mcp），与 TEAM.md 平行；无本地安装',
    where: '不进 Agent 目录；只云端执行',
    whoRuns: '市场 workflows 引擎 / MCP 桥 marketplace_run_workflow'
  },
  plugin: {
    shelf: 'install',
    what: '分发袋（skills + mcp + rules/commands/hooks + 可选 agents/tools）；上传后拆子能力',
    where: 'cap install --type plugin 后拆到零号员工，对话里按子能力生效',
    whoRuns: '问答助手（零号员工）执行子组件'
  }
}

/** 可本地 cap install 的 kind（与 taxonomy.local_install_kinds 对齐） */
export const LOCAL_INSTALL_KINDS = ['agent', 'skill', 'mcp', 'plugin', 'rule', 'command', 'hook']

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
    case 'rule':
      return `cap install ${name} --type rule`
    case 'command':
      return `cap install ${name} --type command`
    case 'hook':
      return `cap install ${name} --type hook`
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
  skill: ['把团队 SOP 沉淀为可复用说明书', '在问答助手对话中按需激活特定流程', '跨项目共享同一套操作规范'],
  mcp: ['给问答助手接内部系统 / 工单 / 知识库', '连上后助手才能查、改真实数据', '密钥用占位，启用后才在对话里动手'],
  tool: ['作为能力编排（Workflow）节点执行', '云端沙箱调试函数逻辑', '被 Agent 配方间接引用'],
  rule: ['把团队编码规范沉淀为可复用规则', '按 globs 只对特定文件生效', 'alwaysApply 作为全局约束'],
  command: ['把高频操作做成 / 斜杠命令', '跨项目复用同一套操作提示', '装进助手或安装包后一键唤起'],
  hook: ['在 Agent 生命周期跑校验或审计脚本', '拦截危险 shell / MCP 调用', '会话开始时注入环境'],
  agent: ['面向业务场景的助手人设与依赖锁定', '带 TEAM.md 时做多角色协作流水线', '通过 A2A / 市场 MCP 被其他 Agent 调用'],
  workflow: ['把已上架能力串成固定 DAG', '云端批处理 / 自动化流水线', '与 TEAM.md 团队流水线分工并行'],
  plugin: ['一次分发技能 + 连接器（+ 可选助手）', '给零号员工做场景安装包，装完即可问答', '把相关能力打成可一键安装的合集']
}

/** 示例用法（命令或提示级，非对话内唤起） */
export const EXAMPLE_PROMPTS = {
  skill: [
    '按这套流程处理：有人提交了一张工单，请你一步步说明你会怎么做',
    '用三句话说清这个技能适合解决什么问题'
  ],
  mcp: [
    '帮我查一条示例数据并说明用了哪个工具',
    '先列出会用到的工具，再试着调用一个只读接口'
  ],
  tool: ['在 Workflow 中添加 tool 节点并选择本能力', 'POST /api/runtime/tools/{name}/invoke 调试'],
  rule: ['cap install <name> --type rule 后由 Agent 按 alwaysApply/globs 加载', '作为安装包组件随 plugin 分发'],
  command: ['cap install <name> --type command 后在对话中输入 /<name>', '把固定操作步骤写成 COMMAND.md'],
  hook: ['cap install <name> --type hook 后由宿主按事件触发 scripts', '在 hooks.json 声明 matcher 与 fail 策略'],
  agent: [
    '用三句话说清你能帮我做什么',
    '假设我是新同事，请按你的职责处理一件典型任务'
  ],
  workflow: ['在市场运行本 Workflow 并查看节点日志', 'marketplace_run_workflow 通过 MCP 桥触发'],
  plugin: [
    '用三句话说清这个安装包装完后能做什么',
    '按场景演示一次你最擅长的任务'
  ]
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
  { id: 'host_sync', label: '宿主同步', api: 'GET /api/my/host-sync', who: '零号员工 / 桌面（已加入且启用）' },
  { id: 'download', label: '下载制品', api: 'GET /api/capabilities/{name}/download', who: 'cap / 人工' },
  { id: 'install', label: '本地组装', api: 'cap install', who: 'CLI / 兼容路径' },
  { id: 'local', label: '本地运行', api: 'cap run --mode local', who: '零号员工（市场不执行）' },
  { id: 'trial', label: '云端试用', api: 'POST /api/runtime/*', who: '详情页 / 我的能力（问答验证）' },
  { id: 'a2a', label: 'A2A 互调', api: 'Agent Card + tasks/send', who: 'Agent 之间' },
  { id: 'mcp_bridge', label: '市场 MCP 桥', api: 'marketplace_* tools', who: 'IDE / Agent 客户端' },
  { id: 'gateway', label: 'MCP HTTP 网关', api: '/market/api/mcp-gateway/relay/{name}', who: 'Dify 等' },
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
  'Rule/Command/Hook 包结构合法（RULE.mdc / COMMAND.md / hooks.json）',
  '可见性与许可证符合组织策略（private/team/internal/public）'
]

export const STATUS_LABELS = {
  draft: '草稿',
  reviewing: '审核中',
  published: '已上架',
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
  rule: ['编码规范', '安全合规', '文档风格', '测试约定', '通用约束'],
  command: ['开发流程', '发布部署', '代码审查', '文档', '通用效率'],
  hook: ['安全拦截', '格式化', '审计', '会话初始化', '工具门禁'],
  workflow: ['自动化', '数据分析', '开发流程', '通用效率'],
  plugin: ['工单场景', '业务分析', '开发助手', '运维管理', '通用场景']
}

export const PACKAGE_HINTS = {
  agent: 'agent.json + PROMPT.md（可含 TEAM.md 团队流水线、skills/、mcp/；内嵌与市场同名 skill/mcp 自动关联）',
  tool: 'tool.json + schema.json + implementation/tool.py（供能力编排节点；非宿主 src/tools）',
  skill: 'skill.json + SKILL.md',
  mcp: 'mcp.json + connection.json + tools.json + security.json（可含 implementation/*.py）',
  rule: 'rule.json + RULE.mdc（frontmatter：alwaysApply / globs）',
  command: 'command.json + COMMAND.md',
  hook: 'hook.json + hooks.json（可选 scripts/）',
  workflow: 'workflow.json（nodes/edges 引用已上架能力；与 TEAM.md 平行，不进 Agent 目录）',
  plugin: 'plugin.json 或 .cursor-plugin/plugin.json + skills/ rules/ commands/ hooks/ mcp.json（拆包子能力；浏览默认隐藏子项）'
}

/** 角色展示 */
export const ROLE_LABELS = {
  admin: '管理员',
  publisher: '发布者',
  user: '普通用户'
}

/** 安装策略（加入「我的能力」行为） */
export const INSTALL_POLICY_LABELS = {
  optional: '可选加入',
  default_on: '默认加入',
  required: '必装'
}

/** 分发方式（distribution） */
export const DISTRIBUTION_LABELS = {
  local: '本地',
  remote: '远程',
  both: '全部'
}

/** 默认风险（risk_default） */
export const RISK_DEFAULT_LABELS = {
  read: '只读',
  write: '写入',
  destructive: '破坏性'
}

export const DISTRIBUTION_BADGE = {
  local: '',
  remote: 'badge-primary',
  both: 'badge-success'
}

export const RISK_DEFAULT_BADGE = {
  read: '',
  write: 'badge-warning',
  destructive: 'badge-danger'
}

/** 加入≠安装：统一文案，避免 Browse / My / Detail 各写一套 */
export const JOIN_VS_INSTALL_HINT =
  '「加入」只完成授权。在「我的能力」里保持启用后，零号员工 / 桌面会按 host-sync 拉取；复制 cap install 仅作兼容。也可以直接写你要办的事。'

/** 连接器线上试用方式：演示免密 / 平台网关 / 需自备凭证 */
export function mcpTrialMode({ transport = 'stdio', envKeys = [] } = {}) {
  const needsCreds = (envKeys || []).length > 0
  if (transport === 'gateway') {
    return {
      id: 'gateway',
      label: '网关（平台密钥）',
      canOneClick: true,
      hint: '走市场 MCP 网关，试用由平台侧连接，无需你提供密钥。'
    }
  }
  if (!needsCreds) {
    return {
      id: 'demo',
      label: '演示（免密）',
      canOneClick: true,
      hint: '无需密钥即可在本页连接并发现工具。'
    }
  }
  return {
    id: 'credentials',
    label: '需自备凭证',
    canOneClick: false,
    hint: '连接需要密钥占位。员工请通过已配置的助手使用；作者/管理员可试用（密钥取自市场服务器环境变量）。'
  }
}

export const TYPE_LETTER = {
  agent: 'A',
  tool: 'T',
  skill: 'S',
  mcp: 'M',
  workflow: 'W',
  plugin: 'P',
  rule: 'R',
  command: 'C',
  hook: 'H'
}

export const TYPE_COLORS = {
  agent: '#2f6bff',
  tool: '#12b76a',
  skill: '#f5a524',
  mcp: '#7c3aed',
  workflow: '#0ea5e9',
  plugin: '#e5484d',
  rule: '#0f766e',
  command: '#c2410c',
  hook: '#4f46e5'
}

/** 发布意图：主叙事「助手 + 依赖」；安装包保留能力但不作为默认发布入口 */
export const PUBLISH_INTENTS = [
  {
    key: 'recipe',
    label: '发助手',
    blurb: '推荐：人设 + 依赖（技能 / 连接器）。网页在线编辑即可；依赖可先上架再引用，或包内嵌。',
    defaultType: 'agent'
  },
  {
    key: 'brick',
    label: '发组件',
    blurb: '技能 / 连接器（助手的依赖），或编排函数。可网页在线编辑；给助手引用复用。',
    defaultType: 'skill'
  }
]

/** 发组件时可见的 kind（rule/command/hook 保留但不展示） */
export const VISIBLE_CREATE_KINDS = {
  recipe: ['agent', 'workflow'],
  brick: ['skill', 'mcp', 'tool'],
  install: ['plugin']
}

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
export const ONLINE_EDITABLE_KINDS = ['skill', 'mcp', 'tool', 'agent', 'workflow', 'rule', 'command', 'hook']

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
    case 'rule':
      return `/rules/${name}/edit`
    case 'command':
      return `/commands/${name}/edit`
    case 'hook':
      return `/hooks/${name}/edit`
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
