/** 能力类型显示名（type 字段 = kind） */
export const TYPE_LABELS = {
  agent: '专家',
  tool: '编排函数',
  skill: '技能',
  mcp: '连接器',
  workflow: '能力编排',
  plugin: '能力包',
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
    label: '专家',
    short: '专家',
    description: '专家（Agent，可含 TEAM.md）与能力编排平行；禁止互相转换节点模型。',
    kinds: ['agent', 'workflow']
  },
  install: {
    key: 'install',
    label: '能力包',
    short: '能力包',
    description: '一次分发技能 + 连接器（可选专家）。能力保留，逛店默认不展示。',
    kinds: ['plugin']
  }
}

/** 推荐默认：专家 + 依赖（技能 / 连接器） */
export const DEFAULT_BROWSE_KINDS = ['agent', 'skill', 'mcp']

/** 「更多」：进阶编排类；能力包 / rule / command / hook 保留能力但不逛店展示 */
export const MORE_BROWSE_KINDS = ['workflow', 'tool']
export const HIDDEN_BROWSE_KINDS = ['plugin', 'rule', 'command', 'hook']
export const KIND_SHELF = Object.fromEntries(
  Object.values(SHELVES).flatMap((s) => s.kinds.map((k) => [k, s.key]))
)

export const KIND_HINTS = {
  skill: {
    shelf: 'brick',
    what: '技能：一套方法与步骤。专家按它来回答和办事，自己不会直接执行。',
    where: '添加到专家后，提问时即按此技能处理。',
    whoRuns: '专家（零号员工）'
  },
  mcp: {
    shelf: 'brick',
    what: '连接器：把专家接到你的业务系统（数据库、工单、日历等），连上后就能查询和操作。',
    where: '配置后由专家调用；也支持桌面或平台统一接入。',
    whoRuns: '专家通过连接器调用外部系统'
  },
  tool: {
    shelf: 'brick',
    what: '编排函数：自动化流程里的一步，由「能力编排」调用。',
    where: '在能力编排中作为节点使用；不单独安装到专家。',
    whoRuns: '能力编排（云端执行）'
  },
  rule: {
    shelf: 'brick',
    what: '规则：给开发者或 AI 的持久约定（可控制何时生效）。',
    where: '由作者/开发者按需加载到工程或宿主。',
    whoRuns: '开发者工具 / 宿主'
  },
  command: {
    shelf: 'brick',
    what: '命令：可复用的一段提示或操作，用“/名称”唤起。',
    where: '在支持命令的对话或工具里按需使用。',
    whoRuns: '开发者工具 / 宿主'
  },
  hook: {
    shelf: 'brick',
    what: '钩子：在特定时机自动触发的脚本（观察、拦截或跟进）。',
    where: '由宿主在对应事件触发。',
    whoRuns: '开发者工具 / 宿主'
  },
  agent: {
    shelf: 'recipe',
    what: '专家：有明确职责的数字员工，能按流程完成一类任务。',
    where: '加入后即可在零号员工里使用并提问。',
    whoRuns: '零号员工 / 其他 Agent 调用'
  },
  workflow: {
    shelf: 'recipe',
    what: '能力编排：把多个能力按顺序串成一条自动化流程。',
    where: '加入后由平台云端执行，可查看每次运行的节点日志。',
    whoRuns: '市场编排引擎'
  },
  plugin: {
    shelf: 'install',
    what: '能力包：把技能、连接器等打包，一次分发、一次生效。',
    where: '加入后其中的能力按权限生效。',
    whoRuns: '零号员工执行包内能力'
  }
}

/** 技术细节（面向作者/开发者；消费者面默认不展示） */
export const KIND_TECH = {
  skill: 'skill.json + SKILL.md（frontmatter: name/description，可选 license/compatibility/metadata/allowed-tools）',
  mcp: 'server.json（标准，MCP Registry）或 mcp.json + connection.json + tools.json + security.json',
  tool: 'tool.json + schema.json + implementation/tool.py（云端沙箱执行）',
  rule: 'rule.json + RULE.mdc（alwaysApply / globs）',
  command: 'command.json + COMMAND.md',
  hook: 'hook.json + hooks.json（可选 scripts/）',
  agent: 'agent.json + PROMPT.md（可含 TEAM.md、skills/、mcp/）',
  workflow: 'workflow.json（nodes/edges 引用已上架能力；云端执行）',
  plugin: 'plugin.json 或 .cursor-plugin/plugin.json + skills/ rules/ commands/ hooks/ mcp.json'
}

/** 可本地 cap install 的 kind（与 taxonomy.local_install_kinds 对齐） */
export const LOCAL_INSTALL_KINDS = ['agent', 'skill', 'mcp', 'plugin', 'rule', 'command', 'hook']

/** remote（云端订阅即用）时的用法文案：不出现本地安装/命令/接口等说法 */
export const REMOTE_WHERE_HINTS = {
  skill: '加入后即可在专家的回答中生效，无需本地安装。',
  mcp: '加入后由平台统一连接调用，无需本地安装。',
  tool: '加入后由能力编排调用，无需本地安装。',
  rule: '加入后在平台或宿主中按需生效。',
  command: '加入后在支持的对话中按需使用。',
  hook: '加入后由宿主在对应事件触发。',
  agent: '加入后可在零号员工中使用；详情页可先问一句试用。',
  workflow: '加入后由平台执行，可查看运行结果。',
  plugin: '加入后其中能力按权限生效，无需本地安装。'
}

/** 按能力类型与分发方式取用法提示；remote 时 where 换成云端说明 */
export function kindHintFor(cap) {
  const hint = cap ? KIND_HINTS[cap.type] : null
  if (!hint) return null
  if (cap.distribution === 'remote' && REMOTE_WHERE_HINTS[cap.type]) {
    return { ...hint, where: REMOTE_WHERE_HINTS[cap.type] }
  }
  return hint
}

/** 按 kind 生成真实消费命令；workflow/tool 不假装 cap install；remote 云端能力订阅即用，无安装命令 */
export function installCommandFor(cap) {
  if (!cap?.name) return ''
  if (cap.distribution === 'remote') return ''
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

/** 该能力当前是否提供本地安装：kind 支持本地安装，且分发方式不是 remote（云端订阅即用） */
export function canLocalInstallCapability(cap) {
  if (!cap) return false
  if (cap.distribution === 'remote') return false
  return isLocalInstallKind(cap.type)
}

/** 双编排用词：避免都叫「工作流」 */
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
  tool: [
    '把它加进一条自动化流程并运行，然后告诉我结果',
    '说明这一步具体会做什么'
  ],
  rule: [
    '这条规则会在什么情况下生效？',
    '它约束的是哪些内容？'
  ],
  command: [
    '在支持命令的对话里输入 /<名称> 看看效果',
    '这个命令解决什么场景？'
  ],
  hook: [
    '这个钩子在什么时机触发？',
    '触发后会做什么？'
  ],
  agent: [
    '用三句话说清你能帮我做什么',
    '假设我是新同事，请按你的职责处理一件典型任务'
  ],
  workflow: [
    '运行这个自动化流程，并告诉我结果',
    '说明这条流程包含哪些步骤'
  ],
  plugin: [
    '用三句话说清这个能力包装完后能做什么',
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

/** 消费方式（控制面如何被用）；安装与启用由各运行端本地各记，host-sync 仅作兼容不列 */
export const CONSUME_WAYS = [
  { id: 'sync', label: '目录同步', api: 'GET /api/capabilities/sync', who: '引擎 / CI' },
  { id: 'download', label: '下载制品', api: 'GET /api/capabilities/{name}/download', who: 'cap / 人工' },
  { id: 'install', label: '本地组装', api: 'cap install', who: 'CLI / 兼容路径' },
  { id: 'local', label: '本地运行', api: 'cap run --mode local', who: '零号员工（市场不执行）' },
  { id: 'trial', label: '云端试用', api: 'POST /api/runtime/*', who: '详情页 / 我的能力（问答验证）' },
  { id: 'a2a', label: 'A2A 互调', api: 'Agent Card + tasks/send', who: 'Agent 之间' },
  { id: 'mcp_bridge', label: '市场 MCP 桥', api: 'marketplace_* tools', who: 'IDE / Agent 客户端' },
  { id: 'gateway', label: 'MCP HTTP 网关', api: '/market/api/mcp-gateway/{name}', who: 'Dify 等' },
  { id: 'join', label: '加入我的能力', api: 'POST /api/my/capabilities', who: '人（调用授权前提）' }
]

/** 消费矩阵：remote 云端能力去掉「本地组装 / 本地运行」两行（订阅即用，无需安装） */
export function consumeWaysFor(cap) {
  if (cap?.distribution !== 'remote') return CONSUME_WAYS
  return CONSUME_WAYS.filter((w) => w.id !== 'install' && w.id !== 'local')
}

/** 审核清单（提交/审核侧提示） */
export const REVIEW_CHECKLIST = [
  '包结构与 schema 合法（按 kind 必需文件）',
  '无硬编码密钥（使用 ${VAR} / ${VAR:default} 占位）',
  'MCP command/url 可接受或已由管理员确认',
  'Tool 包通过 AST 安全审计（禁危险导入/调用）',
  '依赖的积木存在且版本可解析',
  '能力包子组件命名不冲突；component 默认不单独上架浏览',
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
  agent: ['开发专家类', '运维管理类', '业务分析类', '客服支持类', '通用专家类'],
  tool: ['文件操作', '数据查询', 'API调用', '代码分析', '文档处理', '消息通知', '系统管理', '安全审计'],
  skill: ['开发流程', '测试', '文档', '数据分析', '通用效率', '沟通协作'],
  mcp: ['数据库连接', 'DevOps工具', '项目管理', '消息通知', '数据分析', '内部系统'],
  rule: ['编码规范', '安全合规', '文档风格', '测试约定', '通用约束'],
  command: ['开发流程', '发布部署', '代码审查', '文档', '通用效率'],
  hook: ['安全拦截', '格式化', '审计', '会话初始化', '工具门禁'],
  workflow: ['自动化', '数据分析', '开发流程', '通用效率'],
  plugin: ['工单场景', '业务分析', '开发专家', '运维管理', '通用场景']
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
  '「加入」只完成授权；安装与启用由零号员工 / 桌面各自本地记录，加入后即可在对应端安装使用（distribution=remote 的云端能力加入即用）。也可以直接写你要办的事。'

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
    hint: '连接需要密钥占位。员工请通过已配置的专家使用；作者/管理员可试用（密钥取自市场服务器环境变量）。'
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

/** 发布意图：主叙事「专家 + 依赖」；能力包保留能力但不作为默认发布入口 */
export const PUBLISH_INTENTS = [
  {
    key: 'recipe',
    label: '发专家',
    blurb: '推荐：人设 + 依赖（技能 / 连接器）。网页在线编辑即可；依赖可先上架再引用，或包内嵌。',
    defaultType: 'agent'
  },
  {
    key: 'brick',
    label: '发组件',
    blurb: '技能 / 连接器（专家的依赖），或编排函数。可网页在线编辑；给专家引用复用。',
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

/** 静态/接口资源路径补部署子路径(如 icon_url 形如 /api/... 而站点在 /market/) */
export function assetUrl(path) {
  if (!path) return ''
  if (/^https?:\/\//i.test(path) || path.startsWith('data:')) return path
  const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
  return path.startsWith('/') ? base + path : path
}
