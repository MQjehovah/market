<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { authState } from '../stores/auth'
import {
  TYPE_LABELS,
  TYPE_CATEGORIES,
  VISIBILITY_LABELS,
  STATUS_LABELS,
  PACKAGE_HINTS,
  ORCH_LABELS,
  consumeWaysFor,
  kindHintFor,
  REVIEW_CHECKLIST,
  OWNER_PROGRESS_STEPS,
  JOIN_VS_INSTALL_HINT,
  INSTALL_POLICY_LABELS,
  DISTRIBUTION_LABELS,
  RISK_DEFAULT_LABELS,
  DISTRIBUTION_BADGE,
  RISK_DEFAULT_BADGE,
  ROLE_LABELS,
  shelfLabel,
  formatDate,
  stars,
  formatSize,
  needsZipUpload,
  EXAMPLE_PROMPTS,
  installCommandFor,
  canLocalInstallCapability,
  ownerProgressIndex,
  editRouteFor,
  canOnlineEdit,
  mcpTrialMode
} from '../utils/format'
import StatusBadge from '../components/StatusBadge.vue'
import PackagePreview from '../components/PackagePreview.vue'
import DebugCapabilityModal from '../components/DebugCapabilityModal.vue'
import ConfirmActionModal from '../components/ConfirmActionModal.vue'
import AskTrialPanel from '../components/AskTrialPanel.vue'

const __API_BASE__ = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/api'

const props = defineProps({ id: { type: String, required: true } })
const router = useRouter()
const route = useRoute()

const cap = ref(null)
const versions = ref([])
const ratings = ref([])
const error = ref('')
const notice = ref('')
const reviewComment = ref('')
const rating = ref({ score: 5, comment: '' })
const newVersion = ref('')
const versionChangelog = ref('')
const versionSuggestions = ref({ current: '', major: '', minor: '', patch: '' })
const uploading = ref(false)
const saving = ref(false)
const myIds = ref(new Set())
const myNotice = ref('')
const confirmRemove = ref(false)
const subscribedNames = ref(new Set())
const subBusy = ref(false)
const copyNotice = ref('')
const iconInput = ref(null)
const iconBusy = ref(false)
const iconNotice = ref('')
const iconError = ref('')
const iconFailed = ref(false)
const accessPolicy = ref('open')
const installPolicy = ref('optional')
const allowedUsers = ref('')
const allowedDepartments = ref('')
const allowedRoles = ref([])
const accessSaved = ref('')
const ACCESS_ROLE_KEYS = ['admin', 'publisher', 'user']

function normList(values) {
  if (!Array.isArray(values)) return []
  return values.map((v) => String(v ?? '').trim()).filter(Boolean)
}

function splitList(text) {
  return String(text || '')
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

const openSection = ref({ overview: true, usage: true, developer: false, governance: false })
const reviewChecks = ref([])
const allReviewChecked = computed(() => reviewChecks.value.length > 0 && reviewChecks.value.every(Boolean))
const needsPackage = computed(() => cap.value && needsZipUpload(cap.value.type))
const showPackageUpload = computed(
  () =>
    needsPackage.value &&
    isOwner.value &&
    ['draft', 'returned', 'rejected', 'reviewing'].includes(cap.value?.status)
)

function resetReviewChecks() {
  reviewChecks.value = REVIEW_CHECKLIST.map(() => false)
}

function toggleSection(key) {
  openSection.value[key] = !openSection.value[key]
}

const editForm = reactive({
  name: '',
  type: 'tool',
  description: '',
  category: '',
  tags: '',
  visibility: 'internal',
  distribution: 'both',
  risk_default: 'read',
  data_domain: ''
})

const isOwner = computed(() => cap.value && authState.user && cap.value.author_id === authState.user.id)
const isAdmin = computed(() => authState.user?.role === 'admin')
const isPublisher = computed(() => ['admin', 'publisher'].includes(authState.user?.role))

const canEdit = computed(() => isOwner.value && ['draft', 'returned', 'rejected'].includes(cap.value?.status))
const hasPackage = computed(() => Boolean((cap.value?.artifacts || []).length))
const canSubmit = computed(() => {
  if (!isOwner.value || !['draft', 'returned', 'rejected'].includes(cap.value?.status)) return false
  if (needsZipUpload(cap.value?.type) && !hasPackage.value) return false
  return true
})
const needsPackageFirst = computed(
  () =>
    isOwner.value &&
    ['draft', 'returned', 'rejected'].includes(cap.value?.status) &&
    needsZipUpload(cap.value?.type) &&
    !hasPackage.value
)
const canDelete = computed(() => isOwner.value && ['draft', 'returned', 'rejected', 'reviewing'].includes(cap.value?.status))
const canWithdraw = computed(() => isOwner.value && cap.value?.status === 'reviewing')
const canReview = computed(() => isAdmin.value && cap.value?.status === 'reviewing')
const isAgent = computed(() => cap.value?.type === 'agent')
const isPlugin = computed(() => cap.value?.type === 'plugin')
const isWorkflow = computed(() => cap.value?.type === 'workflow')
const isMcp = computed(() => cap.value?.type === 'mcp')
const isPublished = computed(() => ['published', 'deprecated'].includes(cap.value?.status))
/** 展示名: 中文 display_name 优先; name 为标准机器名/内部标识 */
const displayName = computed(() => (cap.value?.display_name || '').trim() || (cap.value?.name || ''))
/** 外部导入来源(官方 MCP Registry)与运行时依赖 */
const provenance = computed(() => cap.value?.provenance || {})
const requiresBinary = computed(() => (cap.value?.requires?.binary || '').trim())
const requiresAuth = computed(() => (cap.value?.requires?.auth || '').trim())
const kindHint = computed(() => kindHintFor(cap.value))
const shelfName = computed(() => (cap.value ? shelfLabel(cap.value.type) : ''))
const showTemplateDownload = computed(
  () => showPackageUpload.value && ['skill', 'mcp', 'tool', 'agent', 'plugin', 'rule', 'command', 'hook'].includes(cap.value?.type)
)
const progressIndex = computed(() =>
  ownerProgressIndex(cap.value, { joined: myIds.value.has(props.id) })
)
const onlineEditPath = computed(() => (cap.value ? editRouteFor(cap.value) : null))
const preferOnlineEdit = computed(
  () => Boolean(onlineEditPath.value && (isOwner.value || isAdmin.value) && canOnlineEdit(cap.value?.type))
)
const pluginComponents = computed(() => {
  const schema = cap.value?.input_schema || {}
  if (schema.kind !== 'plugin') return []
  return schema.components || []
})
const embeddedSkills = computed(() => {
  const schema = cap.value?.input_schema || {}
  return Array.isArray(schema.embedded_skills) ? schema.embedded_skills : []
})
const embeddedMcp = computed(() => {
  const schema = cap.value?.input_schema || {}
  return Array.isArray(schema.embedded_mcp) ? schema.embedded_mcp : []
})
const usedBy = computed(() => Array.isArray(cap.value?.used_by) ? cap.value.used_by : [])
const usedByAgents = computed(() =>
  usedBy.value.filter((u) => u.type === 'agent' && (u.capability_id || u.name))
)
const debugCap = ref(null)
const askTrialRef = ref(null)
const showAskTrial = computed(() => {
  if (!cap.value || !isPublished.value) return false
  if (isAgent.value || isPlugin.value) return true
  if (['skill', 'mcp'].includes(cap.value.type) && usedByAgents.value.length) return true
  return false
})
const askAgentName = computed(() => {
  if (!cap.value) return ''
  if (isAgent.value || isPlugin.value) return cap.value.name
  return usedByAgents.value[0]?.name || ''
})
const askAgentVersion = computed(() => {
  if (isAgent.value || isPlugin.value) return cap.value?.version || ''
  return usedByAgents.value[0]?.version || ''
})
const askSubjectLabel = computed(() =>
  ['skill', 'mcp'].includes(cap.value?.type) ? cap.value.name : ''
)
const askSuggestions = computed(() =>
  exampleList.value.filter((s) => !/cap install|POST \/api/i.test(s)).slice(0, 4)
)
const askCanRun = computed(() => {
  if (!authState.token) return false
  if (isOwner.value || isAdmin.value) return true
  if (isAgent.value || isPlugin.value) return canRuntime.value
  // 技能经专家试用：专家也需可访问，或已加入该技能/专家
  return joined.value || Boolean(usedByAgents.value[0]?.capability_id)
})
const askBlockedHint = computed(() => {
  if (!authState.token) return '登录后才能试用。'
  if (askCanRun.value) return ''
  return '先「加入」授权，再问一句。'
})
const parentPluginId = computed(() => cap.value?.parent_plugin_id || null)
const isSkillOrMcp = computed(() => ['skill', 'mcp'].includes(cap.value?.type))
const hasSiblings = computed(() => (versions.value || []).some((v) => v.id !== cap.value?.id))
const latestArtifact = computed(() => {
  const arts = cap.value?.artifacts || []
  return arts.length ? arts[arts.length - 1] : null
})
const packageSizeLabel = computed(() =>
  latestArtifact.value ? formatSize(latestArtifact.value.size_bytes) : ''
)

const installCommand = computed(() => (cap.value ? installCommandFor(cap.value) : ''))
const canLocalInstall = computed(() => (cap.value ? canLocalInstallCapability(cap.value) : false))
const isRemoteOnly = computed(() => cap.value?.distribution === 'remote')
const consumeWays = computed(() => consumeWaysFor(cap.value))
const departmentSuggestions = computed(() => {
  const set = new Set()
  const own = String(authState.user?.department || '').trim()
  if (own) set.add(own)
  for (const d of normList(cap.value?.allowed_departments)) set.add(d)
  return [...set]
})
const accessRestrictions = computed(() => {
  const c = cap.value
  if (!c) return []
  const policy = c.access_policy || 'open'
  const depts = normList(c.allowed_departments)
  const roles = normList(c.allowed_roles)
  const users = normList(c.allowed_users)
  const hasLists = depts.length || roles.length || users.length
  if (!isOwner.value && !isAdmin.value) {
    // 非作者/管理员只看策略结论，不暴露名单明细
    if (policy === 'admin_only') return ['仅管理员']
    if (policy === 'restricted' || hasLists) return ['访问受限（需授权）']
    return []
  }
  const parts = []
  if (policy === 'admin_only') parts.push('仅管理员')
  if (policy === 'restricted' && !hasLists) parts.push('白名单为空（仅管理员/作者可用）')
  if (depts.length) parts.push(`部门：${depts.join('、')}`)
  if (roles.length) parts.push(`角色：${roles.map((r) => ROLE_LABELS[r] || r).join('、')}`)
  if (users.length) parts.push(`用户：${users.join('、')}`)
  return parts
})
/** 订阅资格：与后端 services/access.py 的 AND 语义保持一致（admin/作者恒过） */
const subscribeGate = computed(() => {
  const c = cap.value
  if (!c) return { ok: true, reason: '' }
  if (!authState.token) return { ok: false, reason: '请先登录' }
  const user = authState.user || {}
  if (user.role === 'admin' || c.author_id === user.id) return { ok: true, reason: '' }
  if ((c.access_policy || 'open') === 'admin_only') {
    return { ok: false, reason: '该能力仅限管理员' }
  }
  const depts = normList(c.allowed_departments)
  const roles = normList(c.allowed_roles)
  const users = normList(c.allowed_users)
  if (!(depts.length || roles.length || users.length)) {
    return (c.access_policy || 'open') === 'open'
      ? { ok: true, reason: '' }
      : { ok: false, reason: '访问受限（需授权）' }
  }
  // 访客只见策略结论，不回显具体名单明细
  if (depts.length && !depts.includes(String(user.department || '').trim())) {
    return { ok: false, reason: '访问受限（需授权）' }
  }
  if (roles.length && !roles.includes(user.role)) {
    return { ok: false, reason: '访问受限（需授权）' }
  }
  if (users.length && !users.includes(user.username)) {
    return { ok: false, reason: '访问受限（需授权）' }
  }
  return { ok: true, reason: '' }
})
const canSubscribe = computed(() => subscribeGate.value.ok)
const subscribeBlockedReason = computed(() => subscribeGate.value.reason)
const exampleList = computed(() => {
  if (!cap.value) return []
  const schema = cap.value.input_schema || {}
  const custom = schema.examples || schema.example_prompts
  if (Array.isArray(custom) && custom.length) return custom.map(String)
  const raw = EXAMPLE_PROMPTS[cap.value.type] || []
  const items = raw.map((s) =>
    s.replaceAll('<name>', cap.value.name).replaceAll('<plugin>', cap.value.name)
  )
  // remote 云端能力不展示安装类示例
  if (isRemoteOnly.value) return items.filter((s) => !/cap install|--mode local/i.test(s))
  return items
})
const validationReport = computed(() => {
  const raw = cap.value?.validation_report || (cap.value?.input_schema || {})._validation_report
  if (!raw || typeof raw !== 'object') return null
  return raw
})

const contentTab = ref('intro')
const mcpConnection = ref(null)
const mcpTools = ref([])
const mcpMetaLoading = ref(false)
const mcpConfigNotice = ref('')

function normalizeMcpTools(raw) {
  const rows = Array.isArray(raw?.tools) ? raw.tools : Array.isArray(raw) ? raw : []
  return rows
    .filter((t) => t && t.name)
    .map((t) => ({
      name: String(t.name),
      description: String(t.description || '')
    }))
}

/** 对外展示：只给 ${VAR} 占位，不回传明文密钥 */
function publicEnvHint(key, value) {
  const text = value == null ? '' : String(value).trim()
  if (/^\$\{[^}]+\}$/.test(text)) return text
  const safe = String(key || 'VAR').replace(/[^A-Za-z0-9_]/g, '_').replace(/^_+|_+$/g, '') || 'VAR'
  return `\${${safe}}`
}

function publicEnvMap(env) {
  if (!env || typeof env !== 'object') return {}
  const out = {}
  for (const [k, v] of Object.entries(env)) out[k] = publicEnvHint(k, v)
  return out
}

const mcpSchema = computed(() => {
  const s = cap.value?.input_schema
  return s && s.kind === 'mcp' ? s : null
})

const mcpTransport = computed(() => {
  return (
    mcpConnection.value?.transport ||
    mcpConnection.value?.type ||
    mcpSchema.value?.transport ||
    'stdio'
  )
})

const dashboardConsume = computed(() => cap.value?.consumers?.dashboard || null)

const mcpToolRows = computed(() => {
  if (mcpTools.value.length) return mcpTools.value
  const fromSchema = normalizeMcpTools(mcpSchema.value?.tools)
  if (fromSchema.length) return fromSchema
  return normalizeMcpTools(dashboardConsume.value?.tools)
})

const mcpEnvRows = computed(() => {
  const env =
    (mcpConnection.value?.env && typeof mcpConnection.value.env === 'object'
      ? mcpConnection.value.env
      : null) ||
    (mcpSchema.value?.env && typeof mcpSchema.value.env === 'object' ? mcpSchema.value.env : null) ||
    {}
  const keys = Object.keys(env).length
    ? Object.keys(env)
    : mcpSchema.value?.required_env || []
  return keys.map((k) => ({
    key: k,
    hint: publicEnvHint(k, env[k]),
    required: true
  }))
})

/** 业务凭据（环境变量）：详情页直接配置，按能力级加密托管，平台轨启动时注入 */
const vaultSecrets = ref([])
const envDraft = ref({})
const envSaving = ref(false)
const envClearing = ref('')
const envNotice = ref('')
const envError = ref('')

function isSecretKey(key) {
  const k = String(key || '').toLowerCase()
  return ['password', 'secret', 'token', 'key', 'passwd', 'credential'].some((s) => k.includes(s))
}

const envRowsWithState = computed(() => {
  const capId = cap.value?.id || ''
  const hits = {}
  for (const s of vaultSecrets.value) {
    if (s.scope && s.scope !== capId) continue
    if (!hits[s.key_name]) hits[s.key_name] = {}
    if (s.scope === capId) hits[s.key_name].cap = s
    else hits[s.key_name].global = s
  }
  return mcpEnvRows.value.map((r) => ({
    ...r,
    secret: isSecretKey(r.key),
    capRow: hits[r.key]?.cap || null,
    globalRow: hits[r.key]?.global || null
  }))
})

async function loadVaultSecrets() {
  if (!authState.token) {
    vaultSecrets.value = []
    return
  }
  try {
    vaultSecrets.value = (await api.get('/my/secrets')) || []
  } catch {
    vaultSecrets.value = []
  }
}

async function refreshVault() {
  await loadVaultSecrets()
  envDraft.value = {}
}

async function saveCapEnv() {
  const capId = cap.value?.id
  if (!capId) return
  const payload = {}
  for (const [k, v] of Object.entries(envDraft.value)) {
    if (String(v || '').trim()) payload[k] = String(v).trim()
  }
  if (!Object.keys(payload).length) {
    envError.value = '请先填写至少一项环境变量值'
    return
  }
  envSaving.value = true
  envError.value = ''
  envNotice.value = ''
  try {
    await api.put('/my/secrets/bulk', { secrets: payload, scope: capId })
    envNotice.value = `已加密保存并启用注入（${Object.keys(payload).length} 项）`
    await refreshVault()
  } catch (e) {
    envError.value = e.message || '保存失败'
  } finally {
    envSaving.value = false
  }
}

async function clearCapEnv(row) {
  if (!row?.capRow) return
  envClearing.value = row.key
  envError.value = ''
  envNotice.value = ''
  try {
    await api.delete(`/my/secrets/${row.capRow.id}`)
    envNotice.value = `已清除 ${row.key} 的能力级覆盖`
    await refreshVault()
  } catch (e) {
    envError.value = e.message || '清除失败'
  } finally {
    envClearing.value = ''
  }
}

/** 平台密钥（能力级、全用户共用）：市场网关/在线试用/agent 平台轨注入，仅作者或管理员可维护 */
const platformSecrets = ref({ items: [], declared_env: [] })
const platformLoaded = ref(false)
const platformDraft = ref({})
const platformSaving = ref(false)
const platformClearing = ref('')
const platformNotice = ref('')
const platformError = ref('')

const platformSecretRows = computed(() => {
  const configured = new Map((platformSecrets.value.items || []).map((i) => [i.key_name, i]))
  const keys = [...(platformSecrets.value.declared_env || [])]
  for (const key of configured.keys()) keys.push(key)
  return [...new Set(keys.map((k) => String(k).trim()).filter(Boolean))].map((key) => ({
    key,
    secret: isSecretKey(key),
    row: configured.get(key) || null
  }))
})

async function loadPlatformSecrets() {
  platformLoaded.value = false
  const c = cap.value
  if (!c || !(isOwner.value || isAdmin.value) || c.type !== 'mcp') {
    platformSecrets.value = { items: [], declared_env: [] }
    platformLoaded.value = true
    return
  }
  try {
    platformSecrets.value =
      (await api.get(`/capabilities/${c.id}/platform-secrets`)) ||
      { items: [], declared_env: [] }
  } catch {
    platformSecrets.value = { items: [], declared_env: [] }
  } finally {
    platformLoaded.value = true
  }
}

async function savePlatformSecrets() {
  const capId = cap.value?.id
  if (!capId) return
  const payload = {}
  for (const [k, v] of Object.entries(platformDraft.value)) {
    if (String(v || '').trim()) payload[k] = String(v).trim()
  }
  if (!Object.keys(payload).length) {
    platformError.value = '请先填写至少一项平台密钥值'
    return
  }
  platformSaving.value = true
  platformError.value = ''
  platformNotice.value = ''
  try {
    const res = await api.put(`/capabilities/${capId}/platform-secrets`, { secrets: payload })
    platformSecrets.value = res || { items: [], declared_env: [] }
    platformDraft.value = {}
    platformNotice.value = `平台密钥已保存（${Object.keys(payload).length} 项），全用户生效；更新后需重建连接`
  } catch (e) {
    platformError.value = e.message || '保存失败'
  } finally {
    platformSaving.value = false
  }
}

async function clearPlatformSecret(row) {
  const capId = cap.value?.id
  if (!row?.row || !capId) return
  if (!confirm(`确认清除平台密钥「${row.key}」？清除后平台轨将无法注入该变量。`)) return
  platformClearing.value = row.key
  platformError.value = ''
  platformNotice.value = ''
  try {
    await api.delete(
      `/capabilities/${capId}/platform-secrets/${encodeURIComponent(row.key)}`
    )
    platformNotice.value = `已清除平台密钥 ${row.key}`
    await loadPlatformSecrets()
  } catch (e) {
    platformError.value = e.message || '清除失败'
  } finally {
    platformClearing.value = ''
  }
}

watch(
  () => [authState.token, authState.user?.id, authState.user?.role],
  () => {
    loadPlatformSecrets()
  }
)

watch(
  () => [cap.value?.id, authState.token, mcpEnvRows.value.map((r) => r.key).join(',')],
  () => {
    refreshVault()
  }
)

const mcpTrial = computed(() =>
  mcpTrialMode({
    transport: mcpTransport.value,
    envKeys: mcpEnvRows.value.map((r) => r.key)
  })
)
const joined = computed(() => Boolean(cap.value && myIds.value.has(cap.value.id)))
const subscribed = computed(() => Boolean(cap.value && subscribedNames.value.has(cap.value.name)))
const canRuntime = computed(() => isOwner.value || isAdmin.value || joined.value)
const canTrialMcp = computed(() => {
  if (!isMcp.value || !isPublished.value || !authState.token) return false
  if (isOwner.value || isAdmin.value) return true
  return joined.value && mcpTrial.value.canOneClick
})
const canTrialCurrent = computed(() => {
  if (!isPublished.value || !authState.token) return false
  if (isMcp.value) return canTrialMcp.value
  if (isAgent.value) return canRuntime.value
  return false
})
const nextStep = computed(() => {
  if (!cap.value || !isPublished.value) return null
  if (!authState.token) return { kind: 'login', label: '登录后加入' }
  const trialLike = () => {
    if (usedByAgents.value.length) {
      return { kind: 'trial-agent', label: `试用专家 · ${usedByAgents.value[0].name}` }
    }
    if (canTrialCurrent.value) return { kind: 'trial', label: isAgent.value ? '问一句试用' : '试用连接器' }
    return { kind: 'mine', label: '去我的能力' }
  }
  if (!joined.value) {
    return { kind: 'join', label: isPlugin.value ? '加入 · 能力包' : '加入' }
  }
  return trialLike()
})
/** 已加入后的统一口径：安装与启用由各运行端本地各记（按 distribution 细化） */
const joinedHint = computed(() => {
  if (cap.value?.distribution === 'remote') return '已加入，云端能力加入即用（无需安装）。'
  if (cap.value?.distribution === 'local') {
    return '已加入 · 可在零号员工或桌面安装使用（本地安装）。'
  }
  return '已加入 · 可在零号员工或桌面安装使用（本地安装 / 云端托管）。'
})
const mcpClientConfigJson = computed(() => {
  if (!cap.value || !isMcp.value) return ''
  const name = cap.value.name
  const conn = mcpConnection.value || {}
  const schema = mcpSchema.value || {}
  const transport = mcpTransport.value
  const entry = {
    name,
    isActive: true
  }
  if (transport === 'stdio') {
    entry.type = 'stdio'
    entry.command = conn.command || schema.command || 'python'
    entry.args = Array.isArray(conn.args)
      ? conn.args
      : Array.isArray(schema.args)
        ? schema.args
        : []
    const env = publicEnvMap(conn.env || schema.env || {})
    if (Object.keys(env).length) entry.env = env
  } else if (transport === 'gateway') {
    entry.type = 'sse'
    entry.baseUrl = `${window.location.origin}${__API_BASE__}/mcp-gateway/relay/${name}/sse`
  } else if (transport === 'sse') {
    entry.type = 'sse'
    entry.baseUrl = conn.url || schema.url || ''
  } else {
    entry.type = 'streamableHttp'
    entry.baseUrl = conn.url || schema.url || ''
  }
  return JSON.stringify({ mcpServers: { [name]: entry } }, null, 2)
})

const contentTabs = computed(() => {
  if (!cap.value) return []
  const tabs = [{ key: 'intro', label: '介绍' }]
  if (isMcp.value && mcpToolRows.value.length) {
    tabs.push({ key: 'tools', label: `工具（${mcpToolRows.value.length}）` })
  }
  if ((cap.value.artifacts || []).length) {
    tabs.push({ key: 'files', label: '文件预览' })
  }
  if (isPlugin.value) tabs.push({ key: 'components', label: '组件' })
  if (isAgent.value && (embeddedSkills.value.length || embeddedMcp.value.length)) {
    tabs.push({ key: 'bundle', label: '内含能力' })
  }
  tabs.push({ key: 'versions', label: '版本' })
  if (canEdit.value || canReview.value || isOwner.value || isAdmin.value) {
    tabs.push({ key: 'manage', label: '管理' })
  }
  return tabs
})
const typeInitial = computed(() => {
  const t = TYPE_LABELS[cap.value?.type] || cap.value?.type || '?'
  return String(t).slice(0, 1)
})
const readmeHtml = computed(() => {
  const md = (cap.value?.readme_md || '').trim()
  if (!md) return ''
  try {
    return marked.parse(md, { gfm: true, breaks: true })
  } catch {
    return ''
  }
})

watch(cap, () => {
  iconFailed.value = false
  const keys = contentTabs.value.map((t) => t.key)
  if (!keys.includes(contentTab.value)) contentTab.value = 'intro'
})

// 切换能力时立即重置头像加载失败态（cap 尚未加载完成也会先生效）
watch(
  () => props.id,
  () => {
    iconFailed.value = false
  }
)

async function copyInstallCommand() {
  const cmd = installCommand.value
  if (!cmd) return
  try {
    await navigator.clipboard.writeText(cmd)
    copyNotice.value = canLocalInstall.value ? '已复制安装命令' : '已复制消费接口'
    setTimeout(() => { copyNotice.value = '' }, 2000)
  } catch {
    copyNotice.value = cmd
  }
}

function formatStat(n) {
  const v = Number(n) || 0
  if (v >= 10000) return `${(v / 1000).toFixed(v >= 100000 ? 0 : 1)}k`
  return String(v)
}
const publishedLatestId = computed(() => {
  const pubs = (versions.value || []).filter((v) => v.status === 'published')
  if (!pubs.length) return null
  const best = pubs.reduce((a, b) => {
    const pa = String(a.version).split('.').map(Number)
    const pb = String(b.version).split('.').map(Number)
    for (let i = 0; i < 3; i++) {
      if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0) ? a : b
    }
    return a
  })
  return best.id
})
const canCreateVersion = computed(
  () => isOwner.value && ['published', 'deprecated', 'draft', 'returned', 'rejected'].includes(cap.value?.status)
)
const canChangeIdentity = computed(() => canEdit.value && !hasSiblings.value)
const editCategories = computed(() => TYPE_CATEGORIES[editForm.type] || [])

watch(cap, (c) => {
  if (!c) return
  editForm.name = c.name
  editForm.type = c.type
  editForm.description = c.description || ''
  editForm.category = c.category || ''
  editForm.tags = (c.tags || []).join(', ')
  editForm.visibility = c.visibility || 'internal'
  editForm.distribution = c.distribution || 'both'
  editForm.risk_default = c.risk_default || 'read'
  editForm.data_domain = c.data_domain || ''
})

async function loadMcpPackageMeta() {
  mcpConnection.value = null
  mcpTools.value = []
  if (!cap.value || cap.value.type !== 'mcp' || !(cap.value.artifacts || []).length) return
  mcpMetaLoading.value = true
  try {
    const [connRes, toolsRes] = await Promise.all([
      api.get(`/capabilities/${cap.value.id}/package/file?path=${encodeURIComponent('connection.json')}`).catch(() => null),
      api.get(`/capabilities/${cap.value.id}/package/file?path=${encodeURIComponent('tools.json')}`).catch(() => null)
    ])
    if (connRes?.content) {
      try {
        mcpConnection.value = JSON.parse(connRes.content)
      } catch {
        mcpConnection.value = null
      }
    }
    if (toolsRes?.content) {
      try {
        mcpTools.value = normalizeMcpTools(JSON.parse(toolsRes.content))
      } catch {
        mcpTools.value = []
      }
    }
  } finally {
    mcpMetaLoading.value = false
  }
}

function stubFromCap(c) {
  return {
    name: c.name,
    type: c.type,
    version: c.latest_version || c.version || ''
  }
}

function openTrial() {
  if (!cap.value) return
  if (showAskTrial.value && askAgentName.value) {
    contentTab.value = 'intro'
    nextTick(() => askTrialRef.value?.focus?.())
    return
  }
  debugCap.value = stubFromCap(cap.value)
}

function openTrialAgent(u) {
  if (showAskTrial.value && (u?.name || askAgentName.value)) {
    contentTab.value = 'intro'
    nextTick(() => askTrialRef.value?.focus?.())
    return
  }
  debugCap.value = {
    name: u.name,
    type: u.type || 'agent',
    version: u.version || ''
  }
}

async function copyMcpClientConfig() {
  const text = mcpClientConfigJson.value
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    mcpConfigNotice.value = '已复制 MCP 客户端配置'
    setTimeout(() => { mcpConfigNotice.value = '' }, 2000)
  } catch {
    mcpConfigNotice.value = text
  }
}

async function load() {
  try {
    cap.value = await api.get(`/capabilities/${props.id}`)
    accessPolicy.value = cap.value.access_policy || 'open'
    installPolicy.value = cap.value.install_policy || 'optional'
    allowedUsers.value = (cap.value.allowed_users || []).join(', ')
    allowedDepartments.value = normList(cap.value.allowed_departments).join(', ')
    allowedRoles.value = normList(cap.value.allowed_roles)
    versions.value = await api.get(`/capabilities/${props.id}/versions`)
    ratings.value = await api.get(`/capabilities/${props.id}/ratings`)
    await loadMcpPackageMeta()
    await loadPlatformSecrets()
  } catch (e) {
    error.value = e.message
  }
}

async function loadMy() {
  if (!authState.token) return
  try {
    const items = await api.get('/my/capabilities?scope=added')
    myIds.value = new Set(items.map((c) => c.id))
  } catch {
    myIds.value = new Set()
  }
  try {
    const subs = await api.get('/my/subscriptions')
    subscribedNames.value = new Set(subs?.names || [])
  } catch {
    subscribedNames.value = new Set()
  }
}

async function toggleMy() {
  myNotice.value = ''
  if (myIds.value.has(props.id)) {
    if ((cap.value?.install_policy || 'optional') === 'required') {
      myNotice.value = '必装能力不可移除'
      return
    }
    confirmRemove.value = true
    return
  }
  if (!subscribeGate.value.ok) {
    myNotice.value = subscribeGate.value.reason
    return
  }
  try {
    const r = await api.post('/my/capabilities', { capability_id: props.id })
    myIds.value = new Set([...myIds.value, props.id])
    // 专家/能力包会随依赖一并加入；刷新「我的」id 集合
    try {
      const mine = await api.get('/my/capabilities?scope=added')
      myIds.value = new Set((mine || []).filter((c) => c.added).map((c) => c.id))
    } catch {
      /* ignore refresh errors */
    }
    const extra = r?.message && r.message.includes('未加入') ? `；${r.message}` : ''
    myNotice.value = joinedHint.value + extra
  } catch (e) {
    myNotice.value = e.message
  }
}

async function removeMine() {
  confirmRemove.value = false
  myNotice.value = ''
  try {
    const r = await api.delete(`/my/capabilities/${props.id}`)
    const next = new Set(myIds.value)
    next.delete(props.id)
    myIds.value = next
    myNotice.value = r.message
  } catch (e) {
    myNotice.value = e.message
  }
}

function downloadTemplate() {
  if (!cap.value) return
  const name = encodeURIComponent(cap.value.name || 'example')
  window.open(`${__API_BASE__}/meta/package-templates/${cap.value.type}?name=${name}`, '_blank')
}

/** 头像：canvas 居中裁剪为 1:1（512×512，JPEG 白底）后上传 */
function cropIconToSquare(file, size = 512) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      try {
        const side = Math.min(img.width, img.height)
        const sx = (img.width - side) / 2
        const sy = (img.height - side) / 2
        const canvas = document.createElement('canvas')
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        ctx.fillStyle = '#fff'
        ctx.fillRect(0, 0, size, size)
        ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size)
        canvas.toBlob(
          (blob) => {
            URL.revokeObjectURL(url)
            if (blob) resolve(blob)
            else reject(new Error('图片处理失败'))
          },
          'image/jpeg',
          0.9
        )
      } catch (e) {
        URL.revokeObjectURL(url)
        reject(e)
      }
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('图片读取失败'))
    }
    img.src = url
  })
}

async function onIconPick(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  iconNotice.value = ''
  iconError.value = ''
  if (!String(file.type).startsWith('image/')) {
    iconError.value = '请选择 PNG/JPG/WebP 图片'
    return
  }
  iconBusy.value = true
  try {
    const blob = await cropIconToSquare(file, 512)
    const upload = new File([blob], 'icon.jpg', { type: 'image/jpeg' })
    await api.putUpload(`/capabilities/${props.id}/icon`, upload)
    iconFailed.value = false
    await load()
    iconNotice.value = '能力头像已更新'
  } catch (e) {
    iconError.value = e.message || '头像上传失败'
  } finally {
    iconBusy.value = false
  }
}

async function removeIcon() {
  if (!cap.value?.icon_url) return
  if (!confirm('确认删除能力头像？删除后回退为类型默认图标。')) return
  iconBusy.value = true
  iconNotice.value = ''
  iconError.value = ''
  try {
    await api.delete(`/capabilities/${props.id}/icon`)
    iconFailed.value = false
    await load()
    iconNotice.value = '能力头像已删除'
  } catch (e) {
    iconError.value = e.message
  } finally {
    iconBusy.value = false
  }
}

function focusPackage() {
  contentTab.value = 'manage'
  router.replace({ query: { ...route.query, focus: 'package' } })
  setTimeout(() => {
    document.getElementById('package-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 50)
}

async function saveAccess() {
  accessSaved.value = ''
  try {
    await api.post(`/capabilities/${props.id}/access`, {
      access_policy: accessPolicy.value,
      allowed_users: splitList(allowedUsers.value),
      // 未传=保持、传空数组=清空；本页始终显式提交两个名单
      allowed_departments: splitList(allowedDepartments.value),
      allowed_roles: [...allowedRoles.value]
    })
    await api.post(`/capabilities/${props.id}/install-policy`, {
      install_policy: installPolicy.value
    })
    accessSaved.value = '调用权限与安装策略已更新'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function doAction(path, payload = {}) {
  notice.value = ''
  error.value = ''
  try {
    cap.value = await api.post(path, payload)
    await load()
  } catch (e) {
    error.value = e.message
  }
}

function submitReview(action) {
  doAction(`/admin/capabilities/${props.id}/review`, { action, comment: reviewComment.value })
}

async function uploadArtifact(event) {
  const file = event.target.files[0]
  if (!file) return
  uploading.value = true
  try {
    cap.value = await api.upload(`/publish/capabilities/${props.id}/artifact`, file)
    notice.value = '能力包上传并校验成功'
  } catch (e) {
    error.value = e.message
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

async function loadVersionSuggestions() {
  if (!authState.token || !isOwner.value) return
  try {
    versionSuggestions.value = await api.get(`/publish/capabilities/${props.id}/next-version`)
    const patch = versionSuggestions.value.patch
    if (!patch) return
    const taken = new Set((versions.value || []).map((v) => v.version))
    // 空值，或当前填的号已存在时，自动换成可用建议
    if (!newVersion.value || taken.has(newVersion.value)) {
      newVersion.value = patch
    }
  } catch {
    /* 非所有者或不需要建议时忽略 */
  }
}

function applySuggestedVersion(kind) {
  const v = versionSuggestions.value?.[kind]
  if (v) newVersion.value = v
}

async function createVersion() {
  if (!newVersion.value) return
  error.value = ''
  try {
    const v = await api.post(`/publish/capabilities/${props.id}/versions`, {
      new_version: newVersion.value,
      change_type: 'patch',
      changelog: versionChangelog.value.trim()
    })
    newVersion.value = ''
    versionChangelog.value = ''
    if (!v?.id) {
      throw new Error('已创建版本但未返回新草稿 id')
    }
    // router-view 按 path 重建后，用 query 保留成功提示
    await router.push({
      path: `/capabilities/${v.id}`,
      query: { focus: 'package', created: v.version }
    })
  } catch (e) {
    if (e && (e.name === 'NavigationDuplicated' || String(e.message || '').includes('Avoided redundant'))) {
      return
    }
    error.value = e.message || String(e)
    await loadVersionSuggestions()
    if (versionSuggestions.value.patch) {
      newVersion.value = versionSuggestions.value.patch
    }
  }
}

async function downloadVersion(v) {
  try {
    const headers = {}
    if (authState.token) headers.Authorization = `Bearer ${authState.token}`
    const res = await fetch(
      `${__API_BASE__}/capabilities/${encodeURIComponent(v.name || cap.value.name)}/download?version=${encodeURIComponent(v.version)}`,
      { headers }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(typeof body.detail === 'string' ? body.detail : '下载失败')
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${v.name || cap.value.name}-${v.version}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message
  }
}

function copyText(url) {
  navigator.clipboard?.writeText(url).then(() => (notice.value = '已复制到剪贴板'))
}

async function downloadArtifact() {
  if (!cap.value) return
  try {
    const headers = {}
    if (authState.token) headers.Authorization = `Bearer ${authState.token}`
    const res = await fetch(
      `${__API_BASE__}/capabilities/${encodeURIComponent(cap.value.name)}/download?version=${encodeURIComponent(cap.value.version)}`,
      { headers }
    )
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(typeof body.detail === 'string' ? body.detail : '下载失败')
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${cap.value.name}-${cap.value.version}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message
  }
}

function cardUrl(id) {
  return `${location.origin}${__API_BASE__}/a2a/agents/${id}/card`
}

function rpcUrl(id) {
  return `${location.origin}${__API_BASE__}/a2a/agents/${id}/a2a`
}

async function submitRating() {
  error.value = ''
  try {
    await api.post(`/capabilities/${props.id}/ratings`, rating.value)
    rating.value = { score: 5, comment: '' }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function toggleSubscribe() {
  if (!cap.value || subBusy.value) return
  error.value = ''
  notice.value = ''
  subBusy.value = true
  const name = cap.value.name
  try {
    if (subscribed.value) {
      await api.delete(`/subscriptions?capability_name=${encodeURIComponent(name)}`)
      const next = new Set(subscribedNames.value)
      next.delete(name)
      subscribedNames.value = next
      notice.value = '已取消订阅更新'
    } else {
      await api.post('/subscriptions', { capability_name: name })
      subscribedNames.value = new Set([...subscribedNames.value, name])
      notice.value = '订阅成功，新版本发布时将收到通知'
    }
  } catch (e) {
    error.value = e.message
  } finally {
    subBusy.value = false
  }
}

function onEditTypeChange() {
  if (!(TYPE_CATEGORIES[editForm.type] || []).includes(editForm.category)) {
    editForm.category = ''
  }
}

async function saveMeta() {
  error.value = ''
  notice.value = ''
  if (!editForm.name.trim()) {
    error.value = '请填写能力名称'
    return
  }
  if (editForm.type !== cap.value.type && (cap.value.artifacts || []).length) {
    if (!confirm('更改类型将清空已上传的能力包，需按新类型重新上传。确定继续？')) return
  }
  saving.value = true
  try {
    cap.value = await api.put(`/publish/capabilities/${props.id}`, {
      name: editForm.name.trim(),
      type: editForm.type,
      description: editForm.description,
      category: editForm.category,
      tags: editForm.tags.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      visibility: editForm.visibility,
      distribution: editForm.distribution,
      risk_default: editForm.risk_default,
      data_domain: editForm.data_domain.trim()
    })
    notice.value = '草稿已保存'
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function withdrawReview() {
  error.value = ''
  notice.value = ''
  try {
    cap.value = await api.post(`/publish/capabilities/${props.id}/withdraw`)
    notice.value = '已撤回审核，可继续修改类型或删除'
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function removeCap() {
  if (!confirm(`确认删除「${cap.value.name} v${cap.value.version}」？删除后可使用该名称重新创建。`)) return
  error.value = ''
  try {
    await api.delete(`/publish/capabilities/${props.id}`)
    router.push('/my')
  } catch (e) {
    error.value = e.message
  }
}

function focusPackagePanel() {
  contentTab.value = 'manage'
  setTimeout(() => document.getElementById('package-panel')?.scrollIntoView({ behavior: 'smooth' }), 300)
}

async function bootstrapDetail({ keepNotice = false } = {}) {
  if (!keepNotice) notice.value = ''
  error.value = ''
  resetReviewChecks()
  await load()
  await loadVersionSuggestions()
  loadMy()
  if (route.query.created) {
    notice.value = `新版本 v${route.query.created} 草稿已创建`
  }
  if (route.query.focus === 'package') {
    focusPackagePanel()
  }
}

watch(
  () => props.id,
  async (id, prev) => {
    if (!id || id === prev) return
    await bootstrapDetail({ keepNotice: Boolean(notice.value) })
  }
)

onMounted(() => {
  bootstrapDetail()
})
</script>

<template>
  <div v-if="error && !cap" class="empty">{{ error }}</div>
  <div v-else-if="cap" class="detail">
    <div v-if="error" class="alert alert-error">{{ error }}</div>
    <div v-if="notice" class="alert alert-success">{{ notice }}</div>

    <nav class="detail-crumb muted">
      <router-link to="/">发现</router-link>
      <span>/</span>
      <router-link v-if="cap.type === 'skill'" :to="{ path: '/', query: { type: 'skill' } }">技能</router-link>
      <router-link v-else-if="cap.type === 'agent'" :to="{ path: '/', query: { type: 'agent' } }">专家</router-link>
      <router-link v-else-if="cap.type === 'plugin'" :to="{ path: '/', query: { shelf: 'install' } }">能力包</router-link>
      <router-link v-else-if="shelfName" :to="{ path: '/', query: { type: cap.type } }">{{ TYPE_LABELS[cap.type] || shelfName }}</router-link>
      <span v-if="cap.type || shelfName">/</span>
      <span>{{ displayName }}</span>
    </nav>

    <div v-if="isOwner" class="owner-progress panel mb-16">
      <div
        v-for="(step, i) in OWNER_PROGRESS_STEPS"
        :key="step.key"
        class="owner-progress-step"
        :class="{ done: i < progressIndex, active: i === progressIndex }"
      >
        <span class="n">{{ i + 1 }}</span>
        <span class="l">{{ step.label }}</span>
      </div>
    </div>

    <section class="detail-hero panel">
      <img
        v-if="cap.icon_url && !iconFailed"
        class="detail-hero-icon icon-img"
        :src="cap.icon_url"
        :alt="cap.name"
        @error="iconFailed = true"
      />
      <div v-else class="detail-hero-icon" aria-hidden="true">{{ typeInitial }}</div>
      <div class="detail-hero-main">
        <div class="detail-hero-badges">
          <StatusBadge :status="cap.status" />
          <span v-if="cap.latest" class="badge badge-primary">最新版</span>
          <span class="badge">{{ TYPE_LABELS[cap.type] }}</span>
          <span v-if="shelfName" class="badge badge-primary">{{ shelfName }}</span>
          <span class="badge" :class="DISTRIBUTION_BADGE[cap.distribution]">{{ DISTRIBUTION_LABELS[cap.distribution] || cap.distribution }}</span>
          <span class="badge" :class="RISK_DEFAULT_BADGE[cap.risk_default]">{{ RISK_DEFAULT_LABELS[cap.risk_default] || cap.risk_default }}</span>
          <span v-if="isMcp" class="badge">{{ mcpTransport }}</span>
          <span
            v-if="isMcp && isPublished"
            class="badge"
            :class="mcpTrial.canOneClick ? 'badge-success' : 'badge-warning'"
          >{{ mcpTrial.label }}</span>
          <span v-if="isMcp && mcpToolRows.length" class="badge badge-primary">{{ mcpToolRows.length }} 工具</span>
          <span v-if="provenance.origin === 'mcp-registry'" class="badge badge-primary" :title="provenance.registry_name || '外部导入'">外部导入</span>
          <span v-if="cap.verified" class="badge badge-success" title="管理员认证">认证</span>
          <span v-if="requiresBinary" class="badge" :title="`依赖本机 CLI：${requiresBinary}`">需 {{ requiresBinary }}</span>
        </div>
        <h1 class="detail-title">
          {{ displayName }}
          <span class="detail-ver">v{{ cap.version }}</span>
        </h1>
        <p v-if="cap.slug" class="detail-tagline muted" style="margin-top: -6px">标准名：{{ cap.slug }}</p>
        <p class="detail-tagline">{{ cap.description || '暂无简介' }}</p>
        <div class="detail-byline muted">
          <span>作者 {{ cap.author_name || '-' }}</span>
          <span>·</span>
          <span>更新于 {{ formatDate(cap.updated_at) }}</span>
        </div>
        <div class="detail-kpis">
          <div class="kpi"><strong>{{ formatStat(cap.usage_count) }}</strong><span>使用</span></div>
          <div class="kpi"><strong>{{ stars(cap.avg_rating) }}</strong><span>{{ formatStat(cap.rating_count) }} 评价</span></div>
          <div class="kpi"><strong>{{ versions.length }}</strong><span>版本</span></div>
          <div class="kpi">
            <strong class="kpi-trust">{{ cap.status === 'published' ? '已审核' : (STATUS_LABELS[cap.status] || cap.status) }}</strong>
            <span>安全状态</span>
          </div>
        </div>
        <div v-if="!canEdit" class="detail-tags">
          <span v-if="cap.category" class="badge">{{ cap.category }}</span>
          <span
            v-for="t in (cap.tags || []).filter((x) => x && x !== 'plugin-component')"
            :key="t"
            class="badge"
          >{{ t }}</span>
        </div>
      </div>
    </section>

    <div class="detail-layout">
      <div class="detail-main">
        <div class="detail-tabs">
          <button
            v-for="t in contentTabs"
            :key="t.key"
            type="button"
            class="detail-tab"
            :class="{ active: contentTab === t.key }"
            @click="contentTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <div v-show="contentTab === 'intro'">
          <section class="panel">
            <h2 class="detail-section-title">介绍</h2>
            <div v-if="readmeHtml" class="guide-block">
              <h3 class="guide-title">README</h3>
              <div class="readme-body" v-html="readmeHtml"></div>
            </div>
            <div v-else-if="cap.description" class="guide-block">
              <h3 class="guide-title">简介</h3>
              <div class="guide-body prose">{{ cap.description }}</div>
            </div>
            <div v-if="cap.changelog" class="guide-block">
              <h3 class="guide-title">本版说明</h3>
              <div class="guide-body prose">{{ cap.changelog }}</div>
            </div>

            <div v-if="isMcp && dashboardConsume" class="guide-block">
              <h3 class="guide-title">桌面工作台</h3>
              <p class="muted" style="font-size: 13px">
                本机无法直接拉起时，可连市场能力网关（Authorization: Bearer 员工 SSO）。
              </p>
              <table class="table">
                <tbody>
                  <tr>
                    <td>模式</td>
                    <td><code>{{ dashboardConsume.mode }}</code></td>
                  </tr>
                  <tr>
                    <td>SSE</td>
                    <td><code>{{ dashboardConsume.sse_url }}</code></td>
                  </tr>
                  <tr>
                    <td>Streamable HTTP</td>
                    <td><code>{{ dashboardConsume.stream_url }}</code></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="isMcp" class="guide-block">
              <h3 class="guide-title">配置参数</h3>
              <table class="table">
                <thead><tr><th>参数</th><th>必填</th><th>说明 / 默认</th></tr></thead>
                <tbody>
                  <tr>
                    <td><code>transport</code></td>
                    <td><span class="badge badge-danger">必填</span></td>
                    <td class="muted">{{ mcpTransport }}（stdio / sse / http / gateway）</td>
                  </tr>
                  <tr v-if="mcpTransport === 'stdio'">
                    <td><code>command</code></td>
                    <td><span class="badge badge-danger">必填</span></td>
                    <td class="muted">{{ mcpConnection?.command || mcpSchema?.command || 'python' }}</td>
                  </tr>
                  <tr v-if="mcpTransport === 'stdio' && (mcpConnection?.args || mcpSchema?.args || []).length">
                    <td><code>args</code></td>
                    <td><span class="muted">可选</span></td>
                    <td class="muted"><code>{{ (mcpConnection?.args || mcpSchema?.args || []).join(' ') }}</code></td>
                  </tr>
                  <tr v-if="['sse', 'http', 'streamable_http'].includes(mcpTransport)">
                    <td><code>url</code></td>
                    <td><span class="badge badge-danger">必填</span></td>
                    <td class="muted">{{ mcpConnection?.url || mcpSchema?.url || '—' }}</td>
                  </tr>
                  <tr v-if="mcpTransport === 'gateway'">
                    <td><code>server</code></td>
                    <td><span class="badge badge-danger">必填</span></td>
                    <td class="muted">网关服务名 {{ mcpConnection?.server || mcpSchema?.server || '—' }}</td>
                  </tr>
                  <tr v-for="row in envRowsWithState" :key="row.key">
                    <td><code>{{ row.key }}</code></td>
                    <td>
                      <span v-if="row.secret" class="badge badge-danger">凭据</span>
                      <span v-else class="muted">可选</span>
                    </td>
                    <td class="muted">
                      在下方「本机凭据（本地安装用）」填写，占位 <code>{{ row.hint }}</code>（不回显明文）
                    </td>
                  </tr>
                </tbody>
              </table>

              <div v-if="isMcp && (isOwner || isAdmin)" class="env-fill-box">
                <h4 class="env-fill-title">平台密钥（云端注入 · 全用户共用）</h4>
                <p class="muted" style="font-size: 13px; margin: 0 0 10px">
                  用于市场网关 / 在线试用 / 零号员工平台轨；由作者或管理员维护，配置后全用户生效，更新后需重建连接。
                </p>
                <div v-if="!platformLoaded" class="muted" style="font-size: 13px">加载平台密钥…</div>
                <div v-else-if="!platformSecretRows.length" class="muted" style="font-size: 13px">
                  该能力未声明环境变量，无需配置平台密钥。
                </div>
                <template v-else>
                  <div class="env-rows">
                    <div v-for="row in platformSecretRows" :key="'ps-' + row.key" class="env-row">
                      <div class="env-row-head">
                        <code>{{ row.key }}</code>
                        <span v-if="row.row" class="badge badge-success">已配置</span>
                        <span v-else class="badge badge-danger">未配置</span>
                        <span class="muted" style="font-size: 12px">{{ row.secret ? '凭据' : '可选' }}</span>
                      </div>
                      <div class="env-row-input">
                        <input
                          v-model="platformDraft[row.key]"
                          class="input"
                          :type="row.secret ? 'password' : 'text'"
                          :placeholder="row.row ? '留空保持不变' : '输入平台密钥值'"
                          autocomplete="off"
                        />
                        <button
                          v-if="row.row"
                          class="btn btn-sm"
                          type="button"
                          :disabled="platformClearing === row.key"
                          @click="clearPlatformSecret(row)"
                        >{{ platformClearing === row.key ? '清除中…' : '清除' }}</button>
                      </div>
                    </div>
                  </div>
                  <div class="flex" style="gap: 8px; margin-top: 12px; flex-wrap: wrap">
                    <button class="btn btn-primary btn-sm" type="button" :disabled="platformSaving" @click="savePlatformSecrets">
                      {{ platformSaving ? '保存中…' : '保存平台密钥' }}
                    </button>
                  </div>
                </template>
                <p v-if="platformNotice" class="alert alert-success mt-12" style="font-size: 13px">{{ platformNotice }}</p>
                <p v-if="platformError" class="alert alert-error mt-12" style="font-size: 13px">{{ platformError }}</p>
              </div>

              <div v-if="mcpEnvRows.length" class="env-fill-box">
                <h4 class="env-fill-title">本机凭据（本地安装用）</h4>
                <p class="muted" style="font-size: 13px; margin: 0 0 10px">
                  <template v-if="isOwner || isAdmin">
                    仅供 dashboard 本地安装 / 本机运行时使用；云端平台轨已改用上方平台密钥。
                  </template>
                  <template v-else>
                    仅供 dashboard 本地安装 / 本机运行时使用；云端平台轨已改用能力级平台密钥（由作者或管理员维护）。
                  </template>
                </p>
                <router-link
                  v-if="!authState.token"
                  class="btn btn-primary btn-sm"
                  :to="{ path: '/login', query: { redirect: route.fullPath } }"
                >登录后配置</router-link>
                <template v-else>
                  <div class="env-rows">
                    <div v-for="row in envRowsWithState" :key="row.key" class="env-row">
                      <div class="env-row-head">
                        <code>{{ row.key }}</code>
                        <span v-if="row.capRow" class="badge badge-success">能力级已配</span>
                        <span v-else-if="row.globalRow" class="badge badge-warning">全局值生效</span>
                        <span v-else class="badge badge-danger">未配置</span>
                        <span class="muted" style="font-size: 12px">{{ row.secret ? '凭据' : '可选' }}</span>
                      </div>
                      <div class="env-row-input">
                        <input
                          v-model="envDraft[row.key]"
                          class="input"
                          :type="row.secret ? 'password' : 'text'"
                          :placeholder="row.capRow || row.globalRow ? '留空保持不变' : row.hint"
                          autocomplete="off"
                        />
                        <button
                          v-if="row.capRow"
                          class="btn btn-sm"
                          type="button"
                          :disabled="envClearing === row.key"
                          @click="clearCapEnv(row)"
                        >{{ envClearing === row.key ? '清除中…' : '清除覆盖' }}</button>
                      </div>
                    </div>
                  </div>
                  <div class="flex" style="gap: 8px; margin-top: 12px; flex-wrap: wrap">
                    <button class="btn btn-primary btn-sm" type="button" :disabled="envSaving" @click="saveCapEnv">
                      {{ envSaving ? '保存中…' : '保存并注入' }}
                    </button>
                    <router-link class="btn btn-sm" to="/my/secrets">全局共享密钥</router-link>
                  </div>
                  <p v-if="envNotice" class="alert alert-success mt-12" style="font-size: 13px">{{ envNotice }}</p>
                  <p v-if="envError" class="alert alert-error mt-12" style="font-size: 13px">{{ envError }}</p>
                </template>
              </div>
            </div>

            <AskTrialPanel
              v-if="showAskTrial && askAgentName"
              ref="askTrialRef"
              :agent-name="askAgentName"
              :agent-version="askAgentVersion"
              :subject-label="askSubjectLabel"
              :suggestions="askSuggestions"
              :can-run="askCanRun"
              :blocked-hint="askBlockedHint"
            />

            <div v-if="isMcp && isPublished" class="guide-block trial-block">
              <h3 class="guide-title">{{ usedByAgents.length ? '高级 · 单独调工具' : '试用连接器' }}</h3>
              <p class="guide-lead">{{ mcpTrial.hint }}</p>
              <p v-if="usedByAgents.length" class="muted" style="font-size: 13px; margin: 0 0 10px">
                日常请用上方「问一句」。这里只验证连接器能否连上并调用工具。
              </p>
              <div class="trial-actions">
                <button
                  v-if="canTrialMcp"
                  class="btn"
                  :class="usedByAgents.length ? '' : 'btn-primary'"
                  type="button"
                  @click="debugCap = stubFromCap(cap)"
                >{{ usedByAgents.length ? '连接并调工具' : '试用连接器' }}</button>
                <router-link
                  v-else-if="!authState.token"
                  :to="{ path: '/login', query: { redirect: route.fullPath } }"
                  class="btn btn-primary"
                >登录后试用</router-link>
                <span v-else-if="!joined" class="muted" style="font-size: 13px">先「加入」授权，再试用。</span>
              </div>
            </div>

            <details v-if="isMcp && mcpClientConfigJson" class="guide-block mcp-advanced">
              <summary class="mcp-advanced-summary">高级 · MCP 客户端 JSON</summary>
              <p class="muted" style="font-size: 12px; margin: 8px 0 10px">
                给调试或兼容客户端粘贴 <code>mcpServers</code>。员工日常请加入后随专家安装，不要把这段当主路径。
              </p>
              <div class="flex" style="gap: 8px; margin-bottom: 8px">
                <button class="btn btn-sm" type="button" @click="copyMcpClientConfig">复制 JSON</button>
              </div>
              <pre class="mcp-config-pre">{{ mcpClientConfigJson }}</pre>
              <p v-if="mcpConfigNotice" class="muted" style="font-size: 12px; margin: 8px 0 0">{{ mcpConfigNotice }}</p>
            </details>

            <div v-if="exampleList.length && !showAskTrial" class="guide-block">
              <h3 class="guide-title">示例用法</h3>
              <ul class="guide-list">
                <li v-for="(s, i) in exampleList" :key="'ex-'+i">
                  <span v-if="['skill', 'mcp', 'agent', 'plugin'].includes(cap.type)">{{ s }}</span>
                  <code v-else>{{ s }}</code>
                </li>
              </ul>
            </div>
            <div v-if="validationReport" class="guide-block">
              <h3 class="guide-title">校验报告</h3>
              <div class="guide-body">
                <div class="muted" style="font-size: 12px; margin-bottom: 8px">上传时结构校验结果（警告不阻断；错误需修复后重传）</div>
                <div v-if="(validationReport.errors || []).length" style="color: var(--danger); margin-bottom: 8px">
                  <div v-for="(e, i) in validationReport.errors" :key="'ve-'+i">{{ e }}</div>
                </div>
                <ul v-if="(validationReport.warnings || []).length" class="guide-list">
                  <li v-for="(w, i) in validationReport.warnings" :key="'vw-'+i">{{ w }}</li>
                </ul>
                <div v-else-if="!(validationReport.errors || []).length" class="muted" style="font-size: 13px">结构校验通过，无警告。</div>
              </div>
            </div>
            <div v-if="(cap.artifacts || []).length" class="guide-block">
              <div class="flex-between flex-wrap" style="align-items: center">
                <div>
                  <h3 class="guide-title" style="margin: 0">能力包</h3>
                  <div class="muted" style="font-size: 12px; margin-top: 4px">可预览包内 README、SKILL.md、配置与源码</div>
                </div>
                <button class="btn btn-sm btn-primary" type="button" @click="contentTab = 'files'">预览文件</button>
              </div>
            </div>
            <div v-if="kindHint" class="guide-block">
              <h3 class="guide-title">核心用法</h3>
              <div class="guide-body">
                <p class="guide-lead">{{ TYPE_LABELS[cap.type] }}（{{ shelfName }}）：{{ kindHint.what }}</p>
                <ul class="guide-list">
                  <li>
                    <strong>{{ ['skill', 'mcp', 'agent', 'plugin'].includes(cap.type) ? '怎么用' : '装到哪' }}</strong>
                    ：{{ kindHint.where }}
                  </li>
                  <li>
                    <strong>{{ ['skill', 'mcp', 'agent', 'plugin'].includes(cap.type) ? '谁来答' : '谁执行' }}</strong>
                    ：{{ kindHint.whoRuns }}
                  </li>
                  <li v-if="PACKAGE_HINTS[cap.type] && (isOwner || isAdmin)"><strong>包规范</strong>：{{ PACKAGE_HINTS[cap.type] }}</li>
                </ul>
                <div v-if="isAgent && (isOwner || isAdmin)" class="guide-note">
                  <strong>{{ ORCH_LABELS.team.name }}</strong>（TEAM.md）：节点是角色，在零号员工 TeamOrchestrator 执行。与「能力编排」平行，禁止互转。
                </div>
                <div v-if="isWorkflow && (isOwner || isAdmin)" class="guide-note">
                  <strong>{{ ORCH_LABELS.capability.name }}</strong>：节点是已上架能力，只在云端执行；可将带 TEAM.md 的 Agent 作为 agent 节点调用。
                </div>
              </div>
            </div>
            <div class="guide-block">
              <h3 class="guide-title">怎么用 · 消费矩阵</h3>
              <div class="guide-body">
                <p class="guide-lead muted">市场是控制面目录。日常在零号员工问答里用；试用只验证连接，不是生产主路径。</p>
                <table class="table">
                  <thead><tr><th>方式</th><th v-if="isOwner || isAdmin">接口</th><th>适用</th></tr></thead>
                  <tbody>
                    <tr v-for="w in consumeWays" :key="w.id">
                      <td>{{ w.label }}</td>
                      <td v-if="isOwner || isAdmin"><code style="font-size: 11px">{{ w.api }}</code></td>
                      <td class="muted" style="font-size: 12px">{{ w.who }}</td>
                    </tr>
                  </tbody>
                </table>
                <p v-if="isRemoteOnly" class="guide-lead muted" style="margin: 10px 0 0">
                  云端能力加入即用：通过云端接口 / 平台轨调用或试用，无需本地安装。
                </p>
              </div>
            </div>
          </section>

          <div v-if="parentPluginId" class="panel">
            <h3>来自能力包</h3>
            <div class="muted" style="font-size: 13px">本能力由能力包拆分生成。</div>
            <div class="mt-16"><router-link :to="`/capabilities/${parentPluginId}`">查看父能力包</router-link></div>
          </div>

          <div v-if="usedBy.length" class="panel">
            <h3>{{ usedByAgents.length && usedByAgents.length === usedBy.length ? '被以下专家使用' : '被以下能力使用' }}</h3>
            <div class="muted" style="font-size: 13px">
              {{ isMcp ? '挂到这些专家后，对话里才会动手。' : '来自专家内嵌声明或能力包组件引用。' }}
              <router-link :to="`/?${cap.type}=${encodeURIComponent(cap.name)}&shelf=all`">在目录中筛选</router-link>
            </div>
            <table class="table mt-16">
              <thead><tr><th>类型</th><th>名称</th><th>版本</th><th></th></tr></thead>
              <tbody>
                <tr v-for="u in usedBy" :key="u.capability_id || u.name">
                  <td>{{ TYPE_LABELS[u.type] || u.type }}</td>
                  <td>{{ u.name }}</td>
                  <td>v{{ u.version }}</td>
                  <td class="used-by-actions">
                    <router-link v-if="u.capability_id" :to="`/capabilities/${u.capability_id}`">查看</router-link>
                    <button
                      v-if="u.type === 'agent' && authState.token && isPublished"
                      class="btn btn-sm"
                      type="button"
                      @click="openTrialAgent(u)"
                    >试用专家</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="cap.type === 'tool' && Object.keys(cap.input_schema || {}).length && !(cap.input_schema || {}).kind" class="panel">
            <h3>参数定义（schema.json）</h3>
            <table class="table">
              <thead><tr><th>参数</th><th>类型</th><th>必填</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="(prop, name) in (cap.input_schema.properties || {})" :key="name">
                  <td><code>{{ name }}</code></td>
                  <td>{{ prop.type || 'any' }}</td>
                  <td>
                    <span v-if="(cap.input_schema.required || []).includes(name)" class="badge badge-danger">必填</span>
                    <span v-else class="muted">可选</span>
                  </td>
                  <td class="muted">{{ prop.description || prop.title || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="isAgent && cap.status === 'published'" class="a2a-panel panel">
            <div class="flex-between flex-wrap">
              <h3>A2A 互调信息</h3>
              <span class="badge badge-primary">protocolVersion 1.0</span>
            </div>
            <div class="muted" style="font-size: 13px">可作为标准 A2A Agent 被发现与委派。</div>
            <div class="mt-16">
              <div class="flex"><span class="muted" style="width: 120px">Agent Card</span>
                <code class="url-code">GET {{ __API_BASE__ }}/a2a/agents/{{ cap.id }}/card</code>
                <button class="btn btn-sm" type="button" @click="copyText(cardUrl(cap.id))">复制</button>
              </div>
              <div class="flex mt-8"><span class="muted" style="width: 120px">JSON-RPC</span>
                <code class="url-code">POST {{ __API_BASE__ }}/a2a/agents/{{ cap.id }}/a2a</code>
                <button class="btn btn-sm" type="button" @click="copyText(rpcUrl(cap.id))">复制</button>
              </div>
            </div>
          </div>
        </div>

        <div v-show="contentTab === 'tools'">
          <section class="panel">
            <h2 class="detail-section-title">工具清单</h2>
            <p class="muted" style="font-size: 13px; margin: 0 0 12px">
              来自能力包 <code>tools.json</code>（声明式清单；真实可用工具以连接后发现为准）。
            </p>
            <table v-if="mcpToolRows.length" class="table">
              <thead><tr><th style="width: 28%">工具名称</th><th>描述</th></tr></thead>
              <tbody>
                <tr v-for="t in mcpToolRows" :key="'full-'+t.name">
                  <td><code>{{ t.name }}</code></td>
                  <td class="muted">{{ t.description || '—' }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="muted">暂无工具声明</div>
          </section>
        </div>

        <div v-if="contentTab === 'files'">
          <section class="panel">
            <h2 class="detail-section-title">文件预览</h2>
            <p class="muted" style="font-size: 13px; margin: 0 0 12px">浏览能力包内文件；Markdown 渲染预览，其它文本以源码显示。</p>
            <PackagePreview v-if="(cap.artifacts || []).length" :capability-id="cap.id" />
            <div v-else class="muted">尚未上传能力包</div>
          </section>
        </div>

        <div v-show="contentTab === 'components'">
          <div v-if="isPlugin" class="panel">
            <h3>能力包组件</h3>
            <div class="muted" style="font-size: 13px">上传能力包 zip 后自动拆出；一键加入会同时加入下列组件。</div>
            <div v-if="pluginComponents.length === 0" class="muted mt-12">尚未上传能力包，或包内无组件</div>
            <table v-else class="table mt-12">
              <thead><tr><th>角色</th><th>类型</th><th>名称</th><th>版本</th><th></th></tr></thead>
              <tbody>
                <tr v-for="c in pluginComponents" :key="c.capability_id || c.name">
                  <td><span class="badge">{{ c.role || '-' }}</span></td>
                  <td>{{ TYPE_LABELS[c.type] || c.type }}</td>
                  <td>{{ c.name }}</td>
                  <td>v{{ c.version }}</td>
                  <td><router-link v-if="c.capability_id" :to="`/capabilities/${c.capability_id}`">查看</router-link></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-show="contentTab === 'bundle'">
          <div v-if="isAgent && (embeddedSkills.length || embeddedMcp.length)" class="panel">
            <h3>包含的 Skills / 连接器</h3>
            <div class="muted" style="font-size: 13px">
              从包内提取；若市场已收录可跳转。加入本专家时，已上架的依赖会一并加入「我的能力」。
            </div>
            <div v-if="embeddedSkills.length" class="mt-16">
              <h4 style="margin: 0 0 8px">Skills（{{ embeddedSkills.length }}）</h4>
              <table class="table">
                <thead><tr><th>名称</th><th>描述</th><th>版本</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="s in embeddedSkills" :key="s.name">
                    <td>{{ s.display_name || s.name }}</td>
                    <td class="muted">{{ s.description || '-' }}</td>
                    <td>
                      {{ s.version ? `v${s.version}` : '-' }}
                      <span v-if="s.version_mismatch" class="badge badge-warning">版本差异</span>
                    </td>
                    <td>
                      <router-link v-if="s.capability_id" :to="`/capabilities/${s.capability_id}`">市场已收录</router-link>
                      <span v-else class="muted">市场未收录</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="embeddedMcp.length" class="mt-16">
              <h4 style="margin: 0 0 8px">连接器（{{ embeddedMcp.length }}）</h4>
              <table class="table">
                <thead><tr><th>名称</th><th>描述</th><th>命令 / 包</th><th></th></tr></thead>
                <tbody>
                  <tr v-for="m in embeddedMcp" :key="m.name">
                    <td>{{ m.name }}</td>
                    <td class="muted">{{ m.description || '-' }}</td>
                    <td class="muted">
                      {{ m.command || m.package || '-' }}
                      <span v-if="m.version_mismatch" class="badge badge-warning">版本差异</span>
                    </td>
                    <td>
                      <router-link v-if="m.capability_id" :to="`/capabilities/${m.capability_id}`">市场已收录</router-link>
                      <span v-else class="muted">市场未收录</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div v-show="contentTab === 'versions'">
          <section class="panel">
            <h2 class="detail-section-title">版本历史</h2>
            <table class="table">
              <thead><tr><th>版本</th><th>状态</th><th>说明</th><th>更新时间</th><th></th></tr></thead>
              <tbody>
                <tr v-for="v in versions" :key="v.id" :class="{ 'row-current': v.id === cap.id }">
                  <td>
                    v{{ v.version }}
                    <span v-if="v.id === cap.id" class="badge" style="margin-left: 6px">当前</span>
                    <span v-if="v.id === publishedLatestId" class="badge badge-primary" style="margin-left: 6px">正式最新</span>
                  </td>
                  <td><StatusBadge :status="v.status" /></td>
                  <td class="muted" style="max-width: 220px; font-size: 12px">{{ v.changelog || '—' }}</td>
                  <td class="muted">{{ formatDate(v.updated_at) }}</td>
                  <td class="flex" style="gap: 8px; flex-wrap: wrap">
                    <router-link v-if="v.id !== cap.id" :to="`/capabilities/${v.id}`">查看</router-link>
                    <button
                      v-if="['published', 'deprecated'].includes(v.status) && (v.artifacts || []).length"
                      class="btn btn-sm"
                      type="button"
                      @click="downloadVersion(v)"
                    >下载</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-if="isOwner" class="mt-24">
              <h3>版本管理</h3>
              <div class="muted" style="font-size: 12px; margin-bottom: 8px">
                <template v-if="['published', 'deprecated'].includes(cap.status)">
                  已发布内容请先创建新版本草稿；可用在线编辑完善，或上传能力包后提交审核。
                </template>
                <template v-else-if="preferOnlineEdit">
                  草稿可用在线编辑完善（保存会生成能力包），或手动上传 zip 后提交审核。
                </template>
                <template v-else>草稿需上传能力包后再提交审核。</template>
              </div>
              <div class="flex" style="gap: 8px; flex-wrap: wrap; align-items: center">
                <button type="button" class="btn btn-sm" @click="applySuggestedVersion('major')">major {{ versionSuggestions.major || '' }}</button>
                <button type="button" class="btn btn-sm" @click="applySuggestedVersion('minor')">minor {{ versionSuggestions.minor || '' }}</button>
                <button type="button" class="btn btn-sm" @click="applySuggestedVersion('patch')">patch {{ versionSuggestions.patch || '' }}</button>
                <input v-model="newVersion" class="input" style="max-width: 180px" placeholder="如 1.1.0" />
                <button class="btn btn-primary" type="button" @click="createVersion">创建新版本</button>
              </div>
              <textarea v-model="versionChangelog" class="textarea mt-8" rows="2" placeholder="本版本变更说明（可选）"></textarea>
            </div>
            <div v-if="canReview" class="mt-24">
              <h3>审核</h3>
              <div class="muted" style="font-size: 12px; line-height: 1.5; margin-bottom: 8px">
                审核清单：
                <ul style="margin: 6px 0 0; padding-left: 18px; list-style: none">
                  <li v-for="(item, i) in REVIEW_CHECKLIST" :key="i" style="margin: 4px 0">
                    <label style="display: flex; gap: 8px; align-items: flex-start; cursor: pointer">
                      <input v-model="reviewChecks[i]" type="checkbox" />
                      <span>{{ item }}</span>
                    </label>
                  </li>
                </ul>
                <div v-if="!allReviewChecked" class="muted" style="font-size: 12px; margin-top: 6px">请勾选全部审核项后再点「通过并发布」</div>
              </div>
              <textarea v-model="reviewComment" class="textarea" placeholder="审核意见（可选）"></textarea>
              <div class="flex mt-8">
                <button class="btn btn-success" type="button" :disabled="!allReviewChecked" @click="submitReview('approve')">通过并发布</button>
                <button class="btn btn-danger" type="button" @click="submitReview('reject')">驳回</button>
                <button class="btn" type="button" @click="submitReview('return')">打回修改</button>
              </div>
            </div>
          </section>
        </div>

        <div v-show="contentTab === 'manage'">
          <div v-if="isOwner || isAdmin" class="panel">
            <h3>能力头像</h3>
            <p class="muted" style="font-size: 13px; margin: 6px 0 0">
              建议使用 1:1 图片；选择后自动居中裁剪为 512×512 上传（PNG/JPG/WebP，≤256KB）。无头像时展示类型默认图标。
            </p>
            <div class="flex mt-16" style="align-items: center; gap: 16px; flex-wrap: wrap">
              <img
                v-if="cap.icon_url && !iconFailed"
                class="icon-preview icon-img"
                :src="cap.icon_url"
                :alt="cap.name"
                @error="iconFailed = true"
              />
              <div v-else class="icon-preview icon-preview-fallback">{{ typeInitial }}</div>
              <div class="flex" style="gap: 8px; flex-wrap: wrap">
                <label class="btn btn-sm" :class="{ disabled: iconBusy }" style="cursor: pointer">
                  {{ iconBusy ? '处理中…' : (cap.icon_url ? '更换头像' : '选择图片') }}
                  <input
                    ref="iconInput"
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/*"
                    style="display: none"
                    :disabled="iconBusy"
                    @change="onIconPick"
                  />
                </label>
                <button v-if="cap.icon_url" class="btn btn-sm" type="button" :disabled="iconBusy" @click="removeIcon">
                  删除头像
                </button>
              </div>
            </div>
            <p v-if="iconNotice" class="alert alert-success mt-12" style="font-size: 13px">{{ iconNotice }}</p>
            <p v-if="iconError" class="alert alert-error mt-12" style="font-size: 13px">{{ iconError }}</p>
          </div>

          <div v-if="canEdit" class="panel">
            <h3>编辑草稿</h3>
            <div class="muted" style="font-size: 13px">选错类型或名称时可直接改。已有其他版本时类型/名称锁定。</div>
            <div class="grid mt-16" style="grid-template-columns: 1fr 1fr">
              <div class="field">
                <label>名称</label>
                <input v-model="editForm.name" class="input" :disabled="!canChangeIdentity" />
              </div>
              <div class="field">
                <label>类型</label>
                <select v-model="editForm.type" class="select" :disabled="!canChangeIdentity" @change="onEditTypeChange">
                  <option v-for="(label, key) in TYPE_LABELS" :key="key" :value="key">{{ label }}</option>
                </select>
              </div>
            </div>
            <div class="field mt-12">
              <label>描述</label>
              <textarea v-model="editForm.description" class="textarea" rows="3"></textarea>
            </div>
            <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
              <div class="field">
                <label>分类</label>
                <select v-model="editForm.category" class="select">
                  <option value="">不选</option>
                  <option v-for="c in editCategories" :key="c" :value="c">{{ c }}</option>
                </select>
              </div>
              <div class="field">
                <label>可见性</label>
                <select v-model="editForm.visibility" class="select">
                  <option value="internal">内部（全员可见）</option>
                  <option value="public">公开</option>
                  <option value="team">团队</option>
                  <option value="private">私有（仅自己）</option>
                </select>
              </div>
            </div>
            <div class="grid mt-12" style="grid-template-columns: 1fr 1fr">
              <div class="field">
                <label>分发方式</label>
                <select v-model="editForm.distribution" class="select">
                  <option v-for="(label, key) in DISTRIBUTION_LABELS" :key="key" :value="key">
                    {{ label }}（{{ key }}）
                  </option>
                </select>
              </div>
              <div class="field">
                <label>默认风险</label>
                <select v-model="editForm.risk_default" class="select">
                  <option v-for="(label, key) in RISK_DEFAULT_LABELS" :key="key" :value="key">
                    {{ label }}（{{ key }}）
                  </option>
                </select>
              </div>
            </div>
            <div class="field mt-12">
              <label>数据域（≤64 字）</label>
              <input
                v-model="editForm.data_domain"
                class="input"
                maxlength="64"
                placeholder="设备/客户/财务/知识…"
              />
            </div>
            <div class="field mt-12">
              <label>标签（逗号分隔）</label>
              <input v-model="editForm.tags" class="input" />
            </div>
            <div class="flex mt-16">
              <button class="btn btn-primary" type="button" :disabled="saving" @click="saveMeta">{{ saving ? '保存中…' : '保存修改' }}</button>
              <span v-if="!canChangeIdentity" class="muted" style="font-size: 12px; align-self: center">已有其他版本，类型和名称不可改</span>
            </div>
          </div>

          <div v-if="canSubmit || canWithdraw || canDelete || preferOnlineEdit || isAdmin || isPublisher" class="panel">
            <h3>操作</h3>
            <div class="flex flex-wrap" style="gap: 8px">
              <router-link
                v-if="preferOnlineEdit"
                :to="onlineEditPath"
                class="btn"
                :class="canEdit ? 'btn-primary' : ''"
              >{{ canEdit ? '在线编辑' : (cap.type === 'workflow' ? '查看编排' : '在线编辑') }}</router-link>
              <button
                v-if="canSubmit"
                class="btn btn-success"
                type="button"
                @click="doAction(`/publish/capabilities/${props.id}/submit`)"
              >提交审核</button>
              <button
                v-else-if="needsPackageFirst"
                class="btn"
                :class="preferOnlineEdit ? '' : 'btn-primary'"
                type="button"
                @click="focusPackage"
              >{{ preferOnlineEdit ? '上传 zip' : '去上传能力包' }}</button>
              <button v-if="canWithdraw" class="btn" type="button" @click="withdrawReview">撤回审核</button>
              <button v-if="canDelete" class="btn btn-danger" type="button" @click="removeCap">删除</button>
              <button v-if="isAdmin && cap.status === 'published'" class="btn btn-danger" type="button" @click="doAction(`/admin/capabilities/${props.id}/deprecate`)">下架（弃用）</button>
              <button v-if="isAdmin && ['published', 'deprecated', 'rejected', 'returned'].includes(cap.status)" class="btn btn-danger" type="button" @click="doAction(`/admin/capabilities/${props.id}/archive`)">归档</button>
            </div>
          </div>

          <div v-if="(isOwner || isAdmin) && ['published', 'deprecated'].includes(cap.status)" class="panel">
            <div class="flex-between flex-wrap">
              <h3>调用权限与安装策略</h3>
              <span v-if="accessSaved" class="muted" style="font-size: 12px">{{ accessSaved }}</span>
            </div>
            <div class="muted" style="font-size: 13px">运行配置，不占版本。安装策略影响「我的能力」加入/移除。</div>
            <div class="flex mt-16" style="gap: 10px; flex-wrap: wrap">
              <select v-model="accessPolicy" class="select" style="max-width: 300px">
                <option value="open">开放：所有登录用户可加入并调用</option>
                <option value="admin_only">仅管理员：普通账号不可调用</option>
                <option value="restricted">白名单：仅指定用户可调用</option>
              </select>
              <select v-model="installPolicy" class="select" style="max-width: 220px">
                <option value="optional">{{ INSTALL_POLICY_LABELS.optional }}</option>
                <option value="default_on">{{ INSTALL_POLICY_LABELS.default_on }}</option>
                <option value="required">{{ INSTALL_POLICY_LABELS.required }}</option>
              </select>
              <button class="btn btn-primary" type="button" @click="saveAccess">保存</button>
            </div>
            <div class="access-lists mt-12">
              <div class="field">
                <label>用户白名单（逗号分隔，留空不限）</label>
                <input
                  v-model="allowedUsers"
                  class="input"
                  placeholder="zhangsan, lisi（留空不限）"
                />
              </div>
              <div class="field">
                <label>部门白名单（逗号分隔，留空不限）</label>
                <input
                  v-model="allowedDepartments"
                  class="input"
                  list="access-dept-options"
                  placeholder="软件部, 信息部（留空不限）"
                />
                <datalist id="access-dept-options">
                  <option v-for="d in departmentSuggestions" :key="d" :value="d" />
                </datalist>
              </div>
              <div class="field">
                <label>角色白名单（勾选，留空不限）</label>
                <div class="role-options">
                  <label v-for="key in ACCESS_ROLE_KEYS" :key="key" class="role-option">
                    <input v-model="allowedRoles" type="checkbox" :value="key" />
                    <span>{{ ROLE_LABELS[key] }}</span>
                  </label>
                </div>
              </div>
              <p class="muted" style="font-size: 12px; margin: 0">
                多门同时配置时需全部命中（AND）；留空的门不限制。非空名单在「开放」策略下同样生效。
              </p>
            </div>
          </div>

          <div id="package-panel" class="panel">
            <h3>{{ cap.name }} 内容</h3>
            <div v-if="isWorkflow" class="muted" style="font-size: 13px; margin-bottom: 10px">Workflow 内容在画布中维护，无需上传 zip。</div>
            <div v-else-if="preferOnlineEdit" class="muted" style="font-size: 13px; margin-bottom: 10px">
              推荐在线编辑；保存会生成/更新能力包。也可手动上传 zip。
            </div>
            <div v-if="(cap.artifacts || []).length === 0" class="muted">尚未上传能力包</div>
            <template v-else>
              <ul class="package-tree">
                <li v-for="a in (cap.artifacts || [])" :key="a.id" class="package-row">
                  <span class="package-name">{{ a.filename }}</span>
                  <span class="package-size muted">{{ formatSize(a.size_bytes) }}</span>
                </li>
              </ul>
              <button class="btn btn-sm mt-12" type="button" @click="contentTab = 'files'">预览包内文件</button>
            </template>
            <div v-if="latestArtifact" class="package-checksum muted">
              checksum {{ (latestArtifact.checksum || '').slice(0, 16) }}…
            </div>
            <div v-if="showPackageUpload" class="mt-16">
              <div class="flex" style="gap: 8px; flex-wrap: wrap">
                <label class="btn btn-primary">
                  {{ uploading ? '上传中…' : '上传能力包 (zip)' }}
                  <input type="file" accept=".zip" style="display: none" @change="uploadArtifact" />
                </label>
                <button
                  v-if="showTemplateDownload"
                  class="btn"
                  type="button"
                  @click="downloadTemplate"
                >下载空模板 zip</button>
              </div>
              <div class="muted mt-8" style="font-size: 12px">
                包内需包含 {{ TYPE_LABELS[cap.type] || cap.type }} 规范文件（{{ PACKAGE_HINTS[cap.type] }}）
              </div>
              <div v-if="cap.type === 'mcp'" class="alert mt-8" style="font-size: 12px">
                市场连接器需 <code>mcp.json</code>、<code>connection.json</code>、<code>tools.json</code>、<code>security.json</code>。
                不能直接上传 agent 仓的 <code>mcp-server.json</code>。
              </div>
            </div>
          </div>
        </div>
      </div>

      <aside class="detail-aside">
        <div class="panel aside-card sticky-card">
          <h3>下一步</h3>
          <template v-if="isPublished">
            <div class="aside-cta">
              <button
                v-if="nextStep?.kind === 'join'"
                class="btn btn-block btn-lg btn-primary"
                type="button"
                :disabled="!canSubscribe"
                :title="canSubscribe ? '' : subscribeBlockedReason"
                @click="toggleMy"
              >{{ nextStep.label }}</button>
              <router-link
                v-else-if="nextStep?.kind === 'login'"
                :to="{ path: '/login', query: { redirect: route.fullPath } }"
                class="btn btn-block btn-lg btn-primary"
              >{{ nextStep.label }}</router-link>
              <button
                v-else-if="nextStep?.kind === 'trial-agent'"
                class="btn btn-block btn-lg btn-primary"
                type="button"
                @click="openTrialAgent(usedByAgents[0])"
              >{{ nextStep.label }}</button>
              <button
                v-else-if="nextStep?.kind === 'trial'"
                class="btn btn-block btn-lg btn-primary"
                type="button"
                @click="openTrial"
              >{{ nextStep.label }}</button>
              <router-link
                v-else-if="nextStep?.kind === 'mine'"
                to="/my"
                class="btn btn-block btn-lg btn-primary"
              >{{ nextStep.label }}</router-link>
              <p v-if="nextStep?.kind === 'join' && !canSubscribe" class="aside-hint aside-deny">
                {{ subscribeBlockedReason }}
              </p>
              <p v-if="copyNotice" class="muted" style="font-size: 12px; margin: 0">{{ copyNotice }}</p>
              <span v-if="myNotice" class="muted" style="font-size: 12px">{{ myNotice }}</span>
            </div>
            <div class="aside-more">
              <button
                v-if="joined && (cap.install_policy || 'optional') !== 'required'"
                class="aside-link"
                type="button"
                @click="toggleMy"
              >移出</button>
              <button
                v-if="canLocalInstall && installCommand"
                class="aside-link"
                type="button"
                @click="copyInstallCommand"
              >高级 · 复制 cap install（本机运行）</button>
              <button v-if="!isRemoteOnly" class="aside-link" type="button" @click="downloadArtifact">
                下载 zip{{ packageSizeLabel ? ` · ${packageSizeLabel}` : '' }}
              </button>
              <button
                v-if="authState.token"
                class="aside-link"
                type="button"
                :disabled="subBusy"
                @click="toggleSubscribe"
              >{{ subscribed ? '取消订阅更新' : '订阅更新' }}</button>
              <router-link v-if="authState.token && nextStep?.kind !== 'mine'" to="/my" class="aside-link">我的能力</router-link>
            </div>
            <p class="aside-hint muted">
              <template v-if="isRemoteOnly">
                云端能力加入即用：无需安装，加入后即可在云端调用或试用。{{ isMcp ? mcpTrial.hint : '' }}
              </template>
              <template v-else-if="!joined">先加入，完成授权。{{ JOIN_VS_INSTALL_HINT }}</template>
              <template v-else>{{ joinedHint }}{{ isMcp ? ` ${mcpTrial.hint}` : '' }}</template>
            </p>
          </template>
          <template v-else>
            <p class="aside-hint muted" style="margin-top: 0">
              <template v-if="preferOnlineEdit && needsPackageFirst">
                先在线编辑完善内容（保存会生成能力包），再提交审核；也可手动上传 zip。
              </template>
              <template v-else-if="needsPackageFirst">先上传能力包，再提交审核。可先下载空模板。</template>
              <template v-else-if="cap.status === 'reviewing'">已提交，等待管理员审核。</template>
              <template v-else>完善内容后提交审核；上架后才能加入与本地安装。</template>
            </p>
            <div class="aside-cta mt-16">
              <router-link
                v-if="preferOnlineEdit && canEdit"
                :to="onlineEditPath"
                class="btn btn-block btn-primary btn-lg"
              >在线编辑</router-link>
              <button
                v-if="needsPackageFirst"
                class="btn btn-block"
                :class="preferOnlineEdit ? '' : 'btn-primary btn-lg'"
                type="button"
                @click="focusPackage"
              >{{ preferOnlineEdit ? '上传 zip' : '去上传能力包' }}</button>
              <button
                v-else-if="canSubmit"
                class="btn btn-block btn-success btn-lg"
                type="button"
                @click="doAction(`/publish/capabilities/${props.id}/submit`)"
              >提交审核</button>
            </div>
          </template>
          <dl class="aside-meta">
            <div><dt>类型</dt><dd>{{ TYPE_LABELS[cap.type] }}</dd></div>
            <div><dt>分类</dt><dd>{{ shelfName || '—' }}</dd></div>
            <div><dt>版本</dt><dd>v{{ cap.version }}</dd></div>
            <div><dt>可见性</dt><dd>{{ VISIBILITY_LABELS[cap.visibility] }}</dd></div>
            <div><dt>分发方式</dt><dd>{{ DISTRIBUTION_LABELS[cap.distribution] || cap.distribution || '—' }}</dd></div>
            <div v-if="accessRestrictions.length"><dt>访问限制</dt><dd>{{ accessRestrictions.join('；') }}</dd></div>
            <div><dt>默认风险</dt><dd>{{ RISK_DEFAULT_LABELS[cap.risk_default] || cap.risk_default || '—' }}</dd></div>
            <div><dt>数据域</dt><dd>{{ cap.data_domain || '—' }}</dd></div>
            <div v-if="cap.category"><dt>分类</dt><dd>{{ cap.category }}</dd></div>
            <div><dt>作者</dt><dd>{{ cap.author_name || '-' }}</dd></div>
            <div v-if="cap.slug"><dt>标准名</dt><dd class="mono">{{ cap.slug }}</dd></div>
            <div v-if="provenance.origin"><dt>来源</dt><dd>{{ provenance.registry_name || provenance.origin }}<span v-if="provenance.license" class="muted"> · {{ provenance.license }}</span></dd></div>
            <div v-if="requiresBinary"><dt>依赖 CLI</dt><dd class="mono">{{ requiresBinary }}<span v-if="cap.requires.min_version" class="muted"> ≥ {{ cap.requires.min_version }}</span></dd></div>
            <div v-if="requiresAuth"><dt>依赖登录</dt><dd class="mono">{{ requiresAuth }}</dd></div>
            <div v-if="(cap.required_scopes || []).length"><dt>调用 scope</dt><dd class="mono">{{ (cap.required_scopes || []).join(', ') }}</dd></div>
            <div v-if="isMcp"><dt>传输</dt><dd>{{ mcpTransport }}</dd></div>
            <div v-if="isMcp && isPublished"><dt>试用</dt><dd>{{ mcpTrial.label }}</dd></div>
            <div v-if="isMcp && mcpToolRows.length"><dt>工具</dt><dd>{{ mcpToolRows.length }} 个</dd></div>
            <div v-if="isMcp && mcpEnvRows.length"><dt>环境变量</dt><dd>{{ mcpEnvRows.length }} 项</dd></div>
            <div><dt>更新</dt><dd>{{ formatDate(cap.updated_at) }}</dd></div>
          </dl>
        </div>

        <div class="panel aside-card">
          <h3>评分与评论</h3>
          <div class="rating-form">
            <select v-model="rating.score" class="select" style="max-width: 90px">
              <option v-for="s in [5, 4, 3, 2, 1]" :key="s" :value="s">{{ s }} ★</option>
            </select>
            <input v-model="rating.comment" class="input" placeholder="写下你的评价" @keyup.enter="submitRating" />
            <button class="btn btn-primary btn-sm" type="button" @click="submitRating">提交</button>
          </div>
          <div v-if="ratings.length === 0" class="muted">暂无评价</div>
          <div v-for="r in ratings" :key="r.id" class="rating-item">
            <div class="flex-between">
              <strong>{{ r.username }}</strong>
              <span style="color: var(--warning)">{{ stars(r.score) }}</span>
            </div>
            <div class="muted">{{ r.comment || '（无评论）' }}</div>
          </div>
        </div>
      </aside>
    </div>
    <ConfirmActionModal
      :show="confirmRemove"
      title="确定移除？"
      :body="`移除后，「${cap.name}」将不再出现在自定义列表中，不影响目录上架状态。`"
      ok-text="移除"
      @ok="removeMine"
      @cancel="confirmRemove = false"
    />
    <DebugCapabilityModal
      :show="!!debugCap"
      :cap="debugCap"
      :title="debugCap?.type === 'agent' ? '试用专家' : (debugCap?.type === 'mcp' ? '试用连接器' : '云端试用')"
      @close="debugCap = null"
    />
  </div>
</template>

<style scoped>
.detail-crumb {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  font-size: 13px; margin-bottom: 14px;
}
.detail-crumb a { color: var(--muted); }
.detail-crumb a:hover { color: var(--primary); }
.detail-hero {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 20px;
  margin-bottom: 20px;
  padding: 24px;
  align-items: start;
  box-shadow: var(--shadow);
}
.detail-hero-icon {
  width: 72px; height: 72px; border-radius: 18px;
  background: linear-gradient(145deg, #2f6bff, #1f56e0);
  color: #fff; font-size: 28px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 8px 20px rgba(47, 107, 255, 0.25);
}
.icon-img { object-fit: cover; }
.detail-hero-icon.icon-img { background: var(--panel-2); }
.icon-preview {
  width: 72px; height: 72px; border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  object-fit: cover; background: var(--panel-2);
}
.icon-preview-fallback {
  background: linear-gradient(145deg, #2f6bff, #1f56e0);
  color: #fff; font-size: 26px; font-weight: 700;
}
.detail-hero-main { min-width: 0; }
.detail-hero-badges { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.detail-title {
  margin: 0; font-size: clamp(26px, 3vw, 36px); line-height: 1.2;
  letter-spacing: -0.02em; font-weight: 700;
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px;
}
.detail-ver {
  font-size: 16px; font-weight: 600; color: var(--muted);
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 999px; padding: 2px 10px;
}
.detail-tagline {
  margin: 12px 0 0; color: var(--muted); font-size: 15px;
  line-height: 1.65; max-width: 48rem;
}
.detail-byline { margin-top: 10px; font-size: 13px; display: flex; flex-wrap: wrap; gap: 6px; }
.detail-kpis {
  display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px;
}
.kpi {
  min-width: 96px; padding: 10px 12px; border-radius: 12px;
  background: var(--panel-2); border: 1px solid var(--border);
}
.kpi strong { display: block; font-size: 18px; letter-spacing: -0.02em; }
.kpi span { color: var(--muted); font-size: 12px; }
.kpi-trust { color: var(--success); font-size: 16px !important; }
.detail-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.detail-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 0.85fr);
  gap: 20px;
  align-items: start;
}
.detail-main, .detail-aside { display: flex; flex-direction: column; gap: 16px; }
.detail-tabs {
  display: flex; flex-wrap: wrap; gap: 4px;
  padding: 4px; background: #fff; border: 1px solid var(--border);
  border-radius: 12px; box-shadow: var(--shadow);
}
.detail-tab {
  border: none; background: transparent; color: var(--muted);
  padding: 8px 14px; border-radius: 10px; cursor: pointer; font-size: 13px;
}
.detail-tab:hover { color: var(--text); background: var(--panel-2); }
.detail-tab.active { color: var(--primary); background: var(--primary-soft); font-weight: 600; }
.sticky-card { position: sticky; top: 16px; z-index: 1; }
.owner-progress {
  display: flex; flex-wrap: wrap; gap: 6px 4px; align-items: stretch;
  padding: 12px 14px;
}
.owner-progress-step {
  display: flex; align-items: center; gap: 6px;
  flex: 1 1 auto; min-width: 88px;
  font-size: 12px; color: var(--muted);
}
.owner-progress-step .n {
  width: 22px; height: 22px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--border); background: var(--panel-2);
  font-weight: 600; font-size: 11px;
}
.owner-progress-step.done { color: var(--text); }
.owner-progress-step.done .n { background: var(--primary-soft); border-color: var(--primary); color: var(--primary); }
.owner-progress-step.active { color: var(--primary); font-weight: 600; }
.owner-progress-step.active .n { background: var(--primary); border-color: var(--primary); color: #fff; }
.aside-cta { display: flex; flex-direction: column; gap: 8px; }
.btn-block { width: 100%; justify-content: center; }
.btn-lg { padding: 11px 20px; font-size: 15px; font-weight: 600; }
.install-box {
  display: flex; gap: 8px; align-items: center;
  padding: 10px 12px; border-radius: 10px;
  background: #0f172a; border: 1px solid #1e293b;
}
.install-cmd {
  flex: 1; min-width: 0; color: #e2e8f0; font-size: 12px;
  background: transparent; border: none; word-break: break-all;
}
.aside-hint { margin: 12px 0 0; font-size: 12px; line-height: 1.55; }
.aside-deny { color: var(--danger); }
.access-lists { display: grid; gap: 12px; max-width: 640px; }
.role-options { display: flex; flex-wrap: wrap; gap: 14px; }
.role-option {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13px; cursor: pointer;
}
.aside-more {
  display: flex; flex-wrap: wrap; gap: 8px 14px;
  margin-top: 12px;
}
.aside-link {
  border: none; background: transparent; padding: 0;
  color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: underline;
}
.aside-link:hover { color: var(--primary); }
.aside-meta {
  margin: 16px 0 0; padding-top: 14px; border-top: 1px solid var(--border);
  display: grid; gap: 8px;
}
.aside-meta > div { display: grid; grid-template-columns: 64px 1fr; gap: 8px; font-size: 13px; }
.aside-meta dt { margin: 0; color: var(--muted); }
.aside-meta dd { margin: 0; color: var(--text); }
.detail-section-title { margin: 0 0 16px; font-size: 20px; font-weight: 650; }
.guide-block + .guide-block { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.guide-title { margin: 0 0 8px; font-size: 15px; font-weight: 650; }
.guide-body { margin-top: 8px; }
.guide-lead { margin: 0 0 10px; font-size: 14px; line-height: 1.65; }
.guide-list { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.7; color: var(--muted); }
.guide-list strong { color: var(--text); }
.guide-note {
  margin-top: 12px; padding: 10px 12px; border-radius: 8px;
  background: var(--panel-2); border: 1px solid var(--border);
  font-size: 13px; line-height: 1.55;
}
.prose { white-space: pre-wrap; font-size: 14px; line-height: 1.65; color: var(--muted); }
.readme-body {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text);
  overflow-wrap: anywhere;
}
.readme-body :deep(h1),
.readme-body :deep(h2),
.readme-body :deep(h3) {
  margin: 1.1em 0 0.45em;
  line-height: 1.3;
  font-weight: 650;
}
.readme-body :deep(h1) { font-size: 1.45em; }
.readme-body :deep(h2) { font-size: 1.25em; }
.readme-body :deep(h3) { font-size: 1.1em; }
.readme-body :deep(p) { margin: 0.65em 0; color: var(--muted); }
.readme-body :deep(ul),
.readme-body :deep(ol) { margin: 0.5em 0; padding-left: 1.35em; color: var(--muted); }
.readme-body :deep(li) { margin: 0.25em 0; }
.readme-body :deep(pre) {
  margin: 0.75em 0; padding: 12px 14px; overflow: auto;
  background: #0f172a; color: #e2e8f0; border-radius: 10px; font-size: 12px;
}
.readme-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.92em;
}
.readme-body :deep(:not(pre) > code) {
  background: var(--panel-2); border: 1px solid var(--border);
  border-radius: 6px; padding: 1px 6px; color: #2451c7;
}
.readme-body :deep(a) { color: var(--primary); }
.readme-body :deep(blockquote) {
  margin: 0.75em 0; padding: 6px 12px; border-left: 3px solid var(--border-strong);
  color: var(--muted); background: var(--panel-2);
}
.readme-body :deep(table) { width: 100%; border-collapse: collapse; margin: 0.75em 0; font-size: 13px; }
.readme-body :deep(th),
.readme-body :deep(td) { border: 1px solid var(--border); padding: 8px 10px; text-align: left; }
.readme-body :deep(th) { background: var(--panel-2); color: var(--muted); font-weight: 500; }
.mcp-config-pre {
  margin: 0; padding: 12px 14px; border-radius: 10px;
  background: var(--panel-2); border: 1px solid var(--border);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; line-height: 1.55; overflow: auto; max-height: 320px;
  color: var(--text); white-space: pre;
}
.trial-block .guide-lead { margin-bottom: 10px; }
.trial-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.env-fill-box {
  margin-top: 14px; padding: 14px; border-radius: 12px;
  border: 1px solid var(--border); background: var(--panel-2, #f8fafc);
}
.env-fill-title { margin: 0 0 6px; font-size: 14px; }
.env-rows { display: flex; flex-direction: column; gap: 10px; }
.env-row { display: flex; flex-direction: column; gap: 6px; }
.env-row-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.env-row-input { display: flex; gap: 8px; align-items: center; }
.env-row-input .input { flex: 1; }
.env-fill-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px;
}
.mcp-advanced { border: 1px dashed var(--border); border-radius: 12px; padding: 12px 14px; }
.mcp-advanced-summary {
  cursor: pointer; font-size: 13px; font-weight: 600; color: var(--muted);
}
.mcp-advanced-summary:hover { color: var(--text); }
.used-by-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font-size: 13px; color: var(--muted); }
.package-tree { list-style: none; margin: 0; padding: 0; }
.package-row {
  display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
  padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px;
}
.package-row:last-child { border-bottom: none; }
.package-name { word-break: break-all; }
.package-size { flex-shrink: 0; font-variant-numeric: tabular-nums; }
.package-checksum { margin-top: 8px; font-size: 11px; }
.rating-form { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.rating-item { padding: 10px 0; border-bottom: 1px solid var(--border); }
.rating-item:last-child { border-bottom: none; }
.a2a-panel { border: 1px dashed #7a5cff; }
.url-code {
  background: var(--panel-2); padding: 4px 10px; border-radius: 6px;
  font-size: 12px; color: #2451c7; word-break: break-all;
}
.row-current td { background: rgba(79, 140, 255, 0.06); }
.mt-8 { margin-top: 8px; } .mt-12 { margin-top: 12px; } .mt-16 { margin-top: 16px; } .mt-24 { margin-top: 24px; }
h3 { margin: 0 0 12px; }
@media (max-width: 960px) {
  .detail-hero, .detail-layout { grid-template-columns: 1fr; }
  .detail-hero-icon { width: 56px; height: 56px; font-size: 22px; border-radius: 14px; }
  .sticky-card { position: static; }
}
</style>
